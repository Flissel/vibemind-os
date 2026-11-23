-- RUNBOOK: nach 100. Idempotent, eine Transaktion. Danach verify_101 (in ROLLBACK).
-- 101: Empfang fuer Auftraege des Gehirns (von = 'brain') bekommt einen gemeinsamen Kern und duenne Huellen je
-- Empfaenger. Sales (an = 'sales') und Rowboat (an = 'rowboat') holen ihre Auftraege nur noch darueber.
-- Neu: drei Kernfunktionen (_empfang_naechster, _empfang_abschliessen, _empfang_fehler; an Niemanden vergeben),
-- vier Huellen fuer die neue Rolle rowboat_empfang (NOLOGIN, ohne Passwort) und die drei sales_empfang_*-Huellen,
-- die hier an gleicher Stelle (gleiche Namen, Argumente und Rueckgabe) auf den Kern umgestellt werden: lief die
-- Sales-Datei db/auftrag-empfang.sql schon, ersetzt CREATE OR REPLACE sie ohne DROP und ohne Rechteverlust; lief
-- sie nie, entstehen die Funktionen hier neu.
-- Verhalten (wie die Sales-Vorlage): nur von = 'brain', Leihdauer ueber 0 bis 15 Minuten, Liegengebliebene werden
-- 'fehler' (kein zweiter Versuch), Abschluss nur in Frist und Leihdauer, Ergebnis ein Objekt bis 65536 Bytes,
-- Fehlergrund hoechstens 300 Zeichen.
-- Dazu: auftrag._vergeben vergibt nie einen Auftrag des Gehirns (AND von <> 'brain'), und brain_lesen setzt eine
-- ueberfaellige offene oder laufende Zeile selbst auf 'abgelaufen' (unabhaengig vom Sales-Boten).
-- Diese Datei schaltet KEINE Auftragsart frei (keine Zeile in der Tabelle der Gehirn-Arten).
BEGIN;

-- Ein Sperrwunsch auf der echten Tabelle bricht nach 5 Sekunden ab, statt den Betrieb warten zu lassen.
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rowboat_empfang') THEN
        CREATE ROLE rowboat_empfang NOLOGIN;
    END IF;
END $$;

-- ---------------------------------------------------------------------------------------------------------
-- Kern. Niemand ruft ihn direkt auf; die Huellen geben den Empfaenger fest vor. Der Laden kommt vom Aufrufer.
-- ---------------------------------------------------------------------------------------------------------

-- Offen -> in_arbeit, genau ein Mal je Auftrag. Liegengebliebene Auftraege dieses Ladens werden 'fehler'.
CREATE OR REPLACE FUNCTION auftrag._empfang_naechster(p_an text, p_laden text, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE
    a auftrag.auftraege;
    v_grund text;
BEGIN
    IF p_an IS NULL OR p_an NOT IN ('sales', 'rowboat') THEN RAISE EXCEPTION 'Unbekannter Empfaenger'; END IF;
    IF coalesce(btrim(p_laden), '') = '' THEN RAISE EXCEPTION 'Der Laden fehlt'; END IF;
    IF p_lease IS NULL OR p_lease <= interval '0' OR p_lease > interval '15 minutes' THEN
        RAISE EXCEPTION 'Leihdauer ausserhalb 0 bis 15 Minuten'; END IF;

    v_grund := CASE p_an WHEN 'sales' THEN 'Bearbeitung bei Sales abgebrochen'
                         ELSE 'Bearbeitung bei Rowboat abgebrochen' END;

    -- Liegengebliebene Auftraege dieses Ladens abschliessen statt neu vergeben.
    UPDATE auftrag.auftraege
       SET stand = 'fehler', fehler = v_grund, vergeben_bis = NULL,
           geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object(
               'stand', 'fehler', 'zeit', now(), 'fehler', v_grund))
     WHERE an = p_an AND von = 'brain' AND laden = p_laden
       AND stand = 'in_arbeit' AND vergeben_bis < now();

    SELECT * INTO a FROM auftrag.auftraege
     WHERE an = p_an AND von = 'brain' AND laden = p_laden
       AND stand = 'offen' AND frist_bis > now()
     ORDER BY angelegt
     LIMIT 1
     FOR UPDATE SKIP LOCKED;
    IF a.id IS NULL THEN RETURN NULL; END IF;

    UPDATE auftrag.auftraege
       SET stand = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_lease,
           geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object('stand', 'in_arbeit', 'zeit', now()))
     WHERE id = a.id
    RETURNING * INTO a;

    -- Nur was der Abholer braucht; Mandant, Verlauf und Ergebnis gehen nicht mit.
    RETURN jsonb_build_object('id', a.id, 'art', a.art, 'art_version', a.art_version,
                              'eingabe', a.eingabe, 'plan_id', a.plan_id, 'frist_bis', a.frist_bis);
END $$;

-- Nur im eigenen Laden und nur, solange die Leihdauer laeuft: wer zu spaet kommt, schliesst nichts ab.
CREATE OR REPLACE FUNCTION auftrag._empfang_abschliessen(p_an text, p_id uuid, p_ergebnis jsonb, p_laden text)
    RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF p_an IS NULL OR p_an NOT IN ('sales', 'rowboat') THEN RAISE EXCEPTION 'Unbekannter Empfaenger'; END IF;
    IF coalesce(btrim(p_laden), '') = '' THEN RETURN false; END IF;
    IF jsonb_typeof(p_ergebnis) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF pg_column_size(p_ergebnis) > 65536 THEN RETURN false; END IF;
    UPDATE auftrag.auftraege
       SET stand = 'fertig', ergebnis = p_ergebnis, vergeben_bis = NULL, geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object('stand', 'fertig', 'zeit', now()))
     WHERE id = p_id AND an = p_an AND von = 'brain' AND laden = p_laden
       AND stand = 'in_arbeit' AND frist_bis > now() AND vergeben_bis >= now();
    RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION auftrag._empfang_fehler(p_an text, p_id uuid, p_fehler text, p_laden text)
    RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE v_fehler text := left(coalesce(nullif(btrim(p_fehler), ''), 'Fehler ohne Angabe'), 300);
BEGIN
    IF p_an IS NULL OR p_an NOT IN ('sales', 'rowboat') THEN RAISE EXCEPTION 'Unbekannter Empfaenger'; END IF;
    IF coalesce(btrim(p_laden), '') = '' THEN RETURN false; END IF;
    UPDATE auftrag.auftraege
       SET stand = 'fehler', fehler = v_fehler, vergeben_bis = NULL, geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object('stand', 'fehler', 'zeit', now(), 'fehler', v_fehler))
     WHERE id = p_id AND an = p_an AND von = 'brain' AND laden = p_laden AND stand = 'in_arbeit';
    RETURN FOUND;
END $$;

-- ---------------------------------------------------------------------------------------------------------
-- Huellen Sales: Namen, Argumente und Rueckgabe wie in db/auftrag-empfang.sql (ersetzt sie an Ort und Stelle).
-- ---------------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auftrag.sales_empfang_naechster(p_laden text, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._empfang_naechster('sales', p_laden, p_lease);
END $$;

CREATE OR REPLACE FUNCTION auftrag.sales_empfang_abschliessen(p_id uuid, p_ergebnis jsonb, p_laden text)
    RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._empfang_abschliessen('sales', p_id, p_ergebnis, p_laden);
END $$;

CREATE OR REPLACE FUNCTION auftrag.sales_empfang_fehler(p_id uuid, p_fehler text, p_laden text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._empfang_fehler('sales', p_id, p_fehler, p_laden);
END $$;

-- ---------------------------------------------------------------------------------------------------------
-- Huellen Rowboat (neu), gleiche Signaturen.
-- ---------------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auftrag.rowboat_empfang_naechster(p_laden text, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._empfang_naechster('rowboat', p_laden, p_lease);
END $$;

CREATE OR REPLACE FUNCTION auftrag.rowboat_empfang_abschliessen(p_id uuid, p_ergebnis jsonb, p_laden text)
    RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._empfang_abschliessen('rowboat', p_id, p_ergebnis, p_laden);
END $$;

CREATE OR REPLACE FUNCTION auftrag.rowboat_empfang_fehler(p_id uuid, p_fehler text, p_laden text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._empfang_fehler('rowboat', p_id, p_fehler, p_laden);
END $$;

-- Welche der uebergebenen Punkt-Kennungen stehen schon in einem frueheren fertigen Ergebnis der Art
-- vorschlaege_anlegen an Rowboat (ergebnis.punkte[].punkt_id)? Nur im eigenen Laden, nur lesend.
CREATE OR REPLACE FUNCTION auftrag.rowboat_empfang_bekannt(p_laden text, p_punkt_ids text[]) RETURNS text[]
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF coalesce(btrim(p_laden), '') = '' OR p_punkt_ids IS NULL THEN RETURN ARRAY[]::text[]; END IF;
    IF cardinality(p_punkt_ids) > 500 THEN RAISE EXCEPTION 'Zu viele Kennungen'; END IF;
    RETURN coalesce((
        SELECT array_agg(DISTINCT k ORDER BY k)
          FROM unnest(p_punkt_ids) AS k
         WHERE EXISTS (
               SELECT 1
                 FROM auftrag.auftraege a,
                      LATERAL jsonb_array_elements(
                          CASE WHEN jsonb_typeof(a.ergebnis -> 'punkte') = 'array'
                               THEN a.ergebnis -> 'punkte' ELSE '[]'::jsonb END) AS p
                WHERE a.an = 'rowboat' AND a.von = 'brain' AND a.art = 'vorschlaege_anlegen'
                  AND a.stand = 'fertig' AND a.laden = p_laden
                  AND p ->> 'punkt_id' = k)
    ), ARRAY[]::text[]);
END $$;

-- ---------------------------------------------------------------------------------------------------------
-- Altbestand, nur geaendert wo noetig (Signatur und Rueckgabe gleich).
-- ---------------------------------------------------------------------------------------------------------

-- Wie 098, dazu: Auftraege des Gehirns vergibt nie dieser Weg, nur der Empfang (_empfang_naechster).
CREATE OR REPLACE FUNCTION auftrag._vergeben(p_an text, p_video boolean, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE a auftrag.auftraege;
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    LOOP
        SELECT * INTO a FROM auftrag.auftraege
         WHERE an = p_an AND von <> 'brain' AND frist_bis > now() AND (art = 'video') = p_video
           AND (stand = 'offen'
                OR (stand = 'in_arbeit' AND vergeben_bis < now() AND (p_video OR NOT (ergebnis ? 'video'))))
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

-- Wie 100, dazu: eine offene oder laufende Zeile nach Fristende wird vor der Rueckgabe 'abgelaufen'
-- (Zeile des Gehirns; vorher STABLE sql, jetzt schreibend). Grants bleiben bei CREATE OR REPLACE erhalten.
CREATE OR REPLACE FUNCTION auftrag.brain_lesen(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE j jsonb;
BEGIN
    UPDATE auftrag.auftraege
       SET stand = 'abgelaufen', vergeben_bis = NULL, geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object('stand', 'abgelaufen', 'zeit', now()))
     WHERE id = p_id AND von = 'brain' AND stand IN ('offen', 'in_arbeit') AND frist_bis < now();
    SELECT to_jsonb(a) INTO j FROM auftrag.auftraege a WHERE a.id = p_id AND a.von = 'brain';
    RETURN j;
END $$;

-- ---------------------------------------------------------------------------------------------------------
-- Rechte: neue Funktionen sind fuer PUBLIC ausfuehrbar - zuerst alles entziehen, dann nur der Empfaenger.
-- Der Kern bekommt kein GRANT. Der Laden kommt vom Aufrufer; das traegt nur, solange je Rolle genau ein Laden
-- Zugang hat. Bei einem zweiten Laden muss er aus der Rolle abgeleitet werden.
-- ---------------------------------------------------------------------------------------------------------
REVOKE ALL ON FUNCTION auftrag._empfang_naechster(text, text, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag._empfang_abschliessen(text, uuid, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag._empfang_fehler(text, uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_empfang_naechster(text, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_empfang_abschliessen(uuid, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.sales_empfang_fehler(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.rowboat_empfang_naechster(text, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.rowboat_empfang_abschliessen(uuid, jsonb, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.rowboat_empfang_fehler(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.rowboat_empfang_bekannt(text, text[]) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION auftrag.sales_empfang_naechster(text, interval) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_empfang_abschliessen(uuid, jsonb, text) TO sales_app;
GRANT EXECUTE ON FUNCTION auftrag.sales_empfang_fehler(uuid, text, text) TO sales_app;

GRANT USAGE ON SCHEMA auftrag TO rowboat_empfang;
GRANT EXECUTE ON FUNCTION auftrag.rowboat_empfang_naechster(text, interval) TO rowboat_empfang;
GRANT EXECUTE ON FUNCTION auftrag.rowboat_empfang_abschliessen(uuid, jsonb, text) TO rowboat_empfang;
GRANT EXECUTE ON FUNCTION auftrag.rowboat_empfang_fehler(uuid, text, text) TO rowboat_empfang;
GRANT EXECUTE ON FUNCTION auftrag.rowboat_empfang_bekannt(text, text[]) TO rowboat_empfang;

COMMIT;
