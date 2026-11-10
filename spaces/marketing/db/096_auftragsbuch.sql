-- RUNBOOK: nach 095. Idempotent, eine Transaktion. Danach verify_096 (in ROLLBACK).
-- 096: Auftragsbuch (M8 Stufe A). Eine gemeinsame Tabelle auftrag.auftraege, ueber die Sales, Marketing,
-- Rowboat und das Brain einander Auftraege geben (erste Kette: Reel). Ein Auftrag ist eine Bitte mit
-- Frist (120 min), Lease-Vergabe (FOR UPDATE SKIP LOCKED), hoechstens zwei Versuchen, Tagesgrenze
-- (5 je von+laden, Kalendertag Europe/Berlin) und Tiefe <= 2. Alles laeuft ueber SECURITY-DEFINER-
-- Funktionen; sales_app darf nur die sales_*-Huellen (von fest 'sales') und ablaufen; die allgemeinen Funktionen
-- gehoeren der Marketing-API.
-- Die Marketing-API verbindet als supabase_admin und braucht keinen Grant.
BEGIN;

CREATE SCHEMA IF NOT EXISTS auftrag;

CREATE TABLE IF NOT EXISTS auftrag.auftraege (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    von             text NOT NULL CONSTRAINT auftraege_von_chk CHECK (von IN ('sales', 'marketing', 'rowboat', 'brain')),
    an              text NOT NULL CONSTRAINT auftraege_an_chk CHECK (an IN ('sales', 'marketing', 'rowboat', 'brain')),
    laden           text NOT NULL,
    mandant         text NOT NULL,
    art             text NOT NULL CONSTRAINT auftraege_art_chk CHECK (art IN ('reel')),
    eingabe         jsonb NOT NULL DEFAULT '{}'::jsonb,
    eltern_id       uuid REFERENCES auftrag.auftraege(id),
    wurzel_id       uuid NOT NULL,
    tiefe           int NOT NULL DEFAULT 0 CONSTRAINT auftraege_tiefe_chk CHECK (tiefe BETWEEN 0 AND 2),
    stand           text NOT NULL DEFAULT 'offen'
                    CONSTRAINT auftraege_stand_chk CHECK (stand IN ('offen', 'in_arbeit', 'erzeugt', 'wartet_freigabe', 'fertig',
                                     'abgebrochen', 'fehler', 'abgelaufen')),
    vergeben_bis    timestamptz,
    versuche        int NOT NULL DEFAULT 0,
    frist_bis       timestamptz NOT NULL,
    ergebnis        jsonb NOT NULL DEFAULT '{}'::jsonb,
    fehler          text,
    gemeldet_stand  text,
    erinnert_am     timestamptz,
    verlauf         jsonb[] NOT NULL DEFAULT '{}',
    angelegt        timestamptz NOT NULL DEFAULT now(),
    geaendert       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auftraege_an_stand_idx ON auftrag.auftraege (an, stand, angelegt);
CREATE INDEX IF NOT EXISTS auftraege_von_laden_idx ON auftrag.auftraege (von, laden, stand);

-- Neuen Auftrag anlegen. Wirft deutsch bei Tagesgrenze, Tiefe, unbekanntem an/art/Eltern.
CREATE OR REPLACE FUNCTION auftrag.anlegen(p_von text, p_an text, p_laden text, p_mandant text,
    p_art text, p_eingabe jsonb, p_eltern uuid DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE
    v_id uuid := gen_random_uuid();
    v_tiefe int := 0;
    v_wurzel uuid := v_id;
    v_tag date := (now() AT TIME ZONE 'Europe/Berlin')::date;
    v_n int;
    v_eltern auftrag.auftraege;
BEGIN
    IF p_von IS NULL OR p_von NOT IN ('sales', 'marketing', 'rowboat', 'brain') THEN
        RAISE EXCEPTION 'Unbekannter Absender des Auftrags'; END IF;
    IF p_an IS NULL OR p_an NOT IN ('sales', 'marketing', 'rowboat', 'brain') THEN
        RAISE EXCEPTION 'Unbekannter Empfaenger des Auftrags'; END IF;
    IF p_art IS NULL OR p_art NOT IN ('reel') THEN
        RAISE EXCEPTION 'Unbekannte Art des Auftrags'; END IF;
    IF coalesce(btrim(p_laden), '') = '' OR coalesce(btrim(p_mandant), '') = '' THEN
        RAISE EXCEPTION 'Auftrag braucht Laden und Mandant'; END IF;

    -- Zaehlungen je von (und je von+laden) gegen gleichzeitiges Anlegen absichern
    PERFORM pg_advisory_xact_lock(hashtext('auftrag:' || p_von));
    SELECT count(*) INTO v_n FROM auftrag.auftraege
     WHERE von = p_von AND laden = p_laden
       AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = v_tag;
    IF v_n >= 5 THEN
        RAISE EXCEPTION 'Tagesgrenze erreicht: hoechstens 5 Auftraege je Absender und Laden pro Tag'; END IF;
    SELECT count(*) INTO v_n FROM auftrag.auftraege
     WHERE von = p_von AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = v_tag;
    IF v_n >= 20 THEN
        RAISE EXCEPTION 'Tagesgrenze erreicht: hoechstens 20 Auftraege je Absender pro Tag'; END IF;

    IF p_eltern IS NOT NULL THEN
        SELECT * INTO v_eltern FROM auftrag.auftraege WHERE id = p_eltern;
        IF v_eltern.id IS NULL THEN RAISE EXCEPTION 'Unbekannter Eltern-Auftrag'; END IF;
        IF v_eltern.von <> p_von OR v_eltern.laden <> p_laden THEN
            RAISE EXCEPTION 'Der Eltern-Auftrag gehoert einem anderen Absender oder Laden'; END IF;
        v_tiefe := v_eltern.tiefe + 1;
        v_wurzel := v_eltern.wurzel_id;
        IF v_tiefe > 2 THEN
            RAISE EXCEPTION 'Tiefe ueberschritten: Folgeauftraege gehen hoechstens zwei Ebenen tief'; END IF;
    END IF;

    INSERT INTO auftrag.auftraege (id, von, an, laden, mandant, art, eingabe, eltern_id, wurzel_id, tiefe,
                                   frist_bis, verlauf)
    VALUES (v_id, p_von, p_an, p_laden, p_mandant, p_art, coalesce(p_eingabe, '{}'::jsonb), p_eltern,
            v_wurzel, v_tiefe, now() + interval '120 minutes',
            ARRAY[jsonb_build_object('stand', 'offen', 'zeit', now())]);
    RETURN v_id;
END $$;

-- Jsonb-Form, die naechster/laufende zurueckgeben.
CREATE OR REPLACE FUNCTION auftrag._uebergabe(a auftrag.auftraege) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = auftrag, pg_temp AS $$
    SELECT jsonb_build_object('id', a.id, 'art', a.art, 'mandant', a.mandant, 'eingabe', a.eingabe,
                              'ergebnis', a.ergebnis, 'versuch', a.versuche);
$$;

-- Aeltesten uebernehmbaren Auftrag atomar vergeben. Uebernehmbar: offen, oder in_arbeit mit
-- abgelaufenem Lease UND ohne ergebnis.video (der wartet auf den Render, siehe laufende). Nur vor
-- frist_bis. Ein dritter Versuch wird nicht vergeben, sondern auf fehler gesetzt.
CREATE OR REPLACE FUNCTION auftrag.naechster(p_an text, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE a auftrag.auftraege;
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    LOOP
        SELECT * INTO a FROM auftrag.auftraege
         WHERE an = p_an AND frist_bis > now()
           AND (stand = 'offen'
                OR (stand = 'in_arbeit' AND vergeben_bis < now() AND NOT (ergebnis ? 'video')))
         ORDER BY angelegt
         LIMIT 1
         FOR UPDATE SKIP LOCKED;
        IF a.id IS NULL THEN RETURN NULL; END IF;

        IF a.versuche >= 2 THEN
            UPDATE auftrag.auftraege
               SET stand = 'fehler', fehler = 'Zwei Versuche ohne Ergebnis', vergeben_bis = NULL,
                   geaendert = now(),
                   verlauf = array_append(verlauf, jsonb_build_object(
                       'stand', 'fehler', 'zeit', now(), 'fehler', 'Zwei Versuche ohne Ergebnis'))
             WHERE id = a.id;
            a := NULL;
            CONTINUE;
        END IF;

        UPDATE auftrag.auftraege
           SET stand = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_lease,
               geaendert = now(),
               verlauf = CASE WHEN stand = 'in_arbeit' THEN verlauf
                              ELSE array_append(verlauf, jsonb_build_object('stand', 'in_arbeit', 'zeit', now())) END
         WHERE id = a.id
        RETURNING * INTO a;
        RETURN auftrag._uebergabe(a);
    END LOOP;
END $$;

-- In Arbeit befindliche Auftraege, die auf den Render warten (ergebnis.video gesetzt). Aendert nichts.
CREATE OR REPLACE FUNCTION auftrag.laufende(p_an text) RETURNS SETOF jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
    SELECT auftrag._uebergabe(a) FROM auftrag.auftraege a
     WHERE a.an = p_an AND a.stand = 'in_arbeit' AND a.ergebnis ? 'video'
     ORDER BY a.angelegt;
$$;

CREATE OR REPLACE FUNCTION auftrag.verlaengern(p_id uuid, p_lease interval) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    UPDATE auftrag.auftraege SET vergeben_bis = now() + p_lease, geaendert = now()
     WHERE id = p_id AND stand = 'in_arbeit';
    RETURN FOUND;
END $$;

-- Eigenes Renderbudget: Marketing legt das Reel an, die Frist laeuft ab jetzt mindestens p_minuten weiter
-- (die Renderzeit soll nicht gegen die Anfangsfrist zaehlen). Nur Marketing-Auftraege in Arbeit; nie verkuerzen.
CREATE OR REPLACE FUNCTION auftrag.renderfrist(p_id uuid, p_minuten int) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF p_minuten IS NULL OR p_minuten NOT BETWEEN 1 AND 1440 THEN RETURN false; END IF;
    UPDATE auftrag.auftraege
       SET frist_bis = greatest(frist_bis, now() + make_interval(mins => p_minuten)),
           geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object(
               'stand', 'in_arbeit', 'zeit', now(), 'renderfrist_min', p_minuten))
     WHERE id = p_id AND an = 'marketing' AND stand = 'in_arbeit';
    RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION auftrag.ergebnis_mischen(p_id uuid, p_teil jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF jsonb_typeof(p_teil) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    UPDATE auftrag.auftraege SET ergebnis = ergebnis || p_teil, geaendert = now()
     WHERE id = p_id AND stand IN ('in_arbeit', 'erzeugt', 'wartet_freigabe');
    RETURN FOUND;
END $$;

-- Stand wechseln, nur wenn er noch p_von_stand ist und der Uebergang erlaubt ist.
CREATE OR REPLACE FUNCTION auftrag.stand_setzen(p_id uuid, p_von_stand text, p_nach_stand text,
    p_fehler text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE v_fehler text := left(p_fehler, 300);
BEGIN
    IF (p_von_stand IS NULL OR p_nach_stand IS NULL) OR NOT ((p_von_stand, p_nach_stand) IN (
            ('offen', 'erzeugt'), ('offen', 'fehler'),
            ('in_arbeit', 'erzeugt'), ('in_arbeit', 'fehler'), ('in_arbeit', 'offen'),
            ('erzeugt', 'wartet_freigabe'), ('erzeugt', 'fehler'),
            ('wartet_freigabe', 'fertig'), ('wartet_freigabe', 'abgebrochen'),
            ('wartet_freigabe', 'fehler'))) THEN
        RETURN false;
    END IF;
    UPDATE auftrag.auftraege
       SET stand = p_nach_stand,
           fehler = coalesce(v_fehler, fehler),
           vergeben_bis = NULL,
           geaendert = now(),
           verlauf = array_append(verlauf, jsonb_strip_nulls(jsonb_build_object(
               'stand', p_nach_stand, 'zeit', now(), 'fehler', v_fehler)))
     WHERE id = p_id AND stand = p_von_stand;
    RETURN FOUND;
END $$;

-- Alles Offene/In Arbeit nach Fristende auf abgelaufen setzen; liefert die Anzahl.
CREATE OR REPLACE FUNCTION auftrag.ablaufen() RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE v_n int;
BEGIN
    UPDATE auftrag.auftraege
       SET stand = 'abgelaufen', vergeben_bis = NULL, geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object('stand', 'abgelaufen', 'zeit', now()))
     WHERE stand IN ('offen', 'in_arbeit') AND frist_bis < now();
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END $$;

-- Ganze Zeilen der eigenen Auftraege (von + laden); p_staende NULL = alle Staende.
CREATE OR REPLACE FUNCTION auftrag.eigene(p_von text, p_laden text, p_staende text[]) RETURNS SETOF jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
    SELECT to_jsonb(a) FROM auftrag.auftraege a
     WHERE a.von = p_von AND a.laden = p_laden
       AND (p_staende IS NULL OR a.stand = ANY (p_staende))
     ORDER BY a.angelegt;
$$;

CREATE OR REPLACE FUNCTION auftrag.gemeldet(p_id uuid, p_stand text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    -- Vergleichen-und-Setzen: true nur, wenn DIESER Aufruf den Stand geaendert hat (genau einmal melden).
    UPDATE auftrag.auftraege SET gemeldet_stand = p_stand, geaendert = now()
     WHERE id = p_id AND gemeldet_stand IS DISTINCT FROM p_stand;
    RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION auftrag.erinnert(p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    UPDATE auftrag.auftraege SET erinnert_am = now() WHERE id = p_id AND erinnert_am IS NULL;
    RETURN FOUND;
END $$;

-- Sales-Huelle: sales_app darf NUR diese Funktionen (und ablaufen). von ist fest 'sales', und jede
-- Funktion fasst nur Auftraege mit von = 'sales' an - kein Absender-Faelschen, kein Fremdlesen.
CREATE OR REPLACE FUNCTION auftrag.sales_anlegen(p_an text, p_laden text, p_mandant text, p_art text,
    p_eingabe jsonb, p_eltern uuid DEFAULT NULL) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    -- Sales darf Auftraege nur an Marketing geben (Design 5.2); das allgemeine anlegen kennt alle vier.
    IF p_an IS DISTINCT FROM 'marketing' THEN
        RAISE EXCEPTION 'Sales darf Auftraege nur an Marketing geben'; END IF;
    RETURN auftrag.anlegen('sales', p_an, p_laden, p_mandant, p_art, p_eingabe, p_eltern);
END $$;

CREATE OR REPLACE FUNCTION auftrag.sales_eigene(p_laden text, p_staende text[]) RETURNS SETOF jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
    SELECT auftrag.eigene('sales', p_laden, p_staende);
$$;

CREATE OR REPLACE FUNCTION auftrag.sales_ergebnis_mischen(p_id uuid, p_teil jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    -- Eigentuemer zuerst pruefen (von aendert sich nie, darum ist keine Sperre noetig)
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = p_id AND von = 'sales') THEN RETURN false; END IF;
    RETURN auftrag.ergebnis_mischen(p_id, p_teil);
END $$;

CREATE OR REPLACE FUNCTION auftrag.sales_stand_setzen(p_id uuid, p_von_stand text, p_nach_stand text,
    p_fehler text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = p_id AND von = 'sales') THEN RETURN false; END IF;
    RETURN auftrag.stand_setzen(p_id, p_von_stand, p_nach_stand, p_fehler);
END $$;

CREATE OR REPLACE FUNCTION auftrag.sales_gemeldet(p_id uuid, p_stand text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = p_id AND von = 'sales') THEN RETURN false; END IF;
    RETURN auftrag.gemeldet(p_id, p_stand);
END $$;

CREATE OR REPLACE FUNCTION auftrag.sales_erinnert(p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = p_id AND von = 'sales') THEN RETURN false; END IF;
    RETURN auftrag.erinnert(p_id);
END $$;

-- Rechte: neue Funktionen sind fuer PUBLIC ausfuehrbar - zuerst alles entziehen.
REVOKE ALL ON FUNCTION auftrag.anlegen(text, text, text, text, text, jsonb, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag._uebergabe(auftrag.auftraege) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.naechster(text, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.laufende(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.verlaengern(uuid, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.renderfrist(uuid, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.ergebnis_mischen(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.stand_setzen(uuid, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.ablaufen() FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.eigene(text, text, text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.gemeldet(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.erinnert(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_anlegen(text, text, text, text, jsonb, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_eigene(text, text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_ergebnis_mischen(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_stand_setzen(uuid, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_gemeldet(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_erinnert(uuid) FROM PUBLIC;

GRANT USAGE ON SCHEMA auftrag TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_anlegen(text, text, text, text, jsonb, uuid) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_eigene(text, text[]) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_ergebnis_mischen(uuid, jsonb) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_stand_setzen(uuid, text, text, text) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_gemeldet(uuid, text) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_erinnert(uuid) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.ablaufen() TO sales_app;

COMMIT;
