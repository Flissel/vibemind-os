-- RUNBOOK: nach 097. Idempotent, eine Transaktion. Danach verify_098 (in ROLLBACK).
-- 098: Auftragsbuch nimmt art 'video' (Video-Werkbank, Stufe 1) mit dem neuen, nie ablaufenden Stand
-- 'werkbank' (der Auftrag liegt zwischen den Gates beim Betreiber). Fuer 'reel' aendert sich nichts.
-- Neu: art/stand-CHECKs, anlegen (Video + Tagesgrenze 2 je von+laden), naechster (ohne Video, ueber _vergeben),
-- video_naechster (Video, ueber _vergeben), laufende (nie Video), ergebnis_mischen (auch in werkbank), stand_setzen
-- (Uebergaenge von/nach werkbank nur fuer Video), sales_stand_setzen (fuer Video nur die vier Uebergaenge
-- der Freigabe). Alles andere (ablaufen, verlaengern, renderfrist, die uebrigen sales_*-Huellen, Rechte) bleibt
-- wie in 096.
BEGIN;

ALTER TABLE auftrag.auftraege DROP CONSTRAINT IF EXISTS auftraege_art_chk;
ALTER TABLE auftrag.auftraege ADD CONSTRAINT auftraege_art_chk CHECK (art IN ('reel', 'video'));
ALTER TABLE auftrag.auftraege DROP CONSTRAINT IF EXISTS auftraege_stand_chk;
ALTER TABLE auftrag.auftraege ADD CONSTRAINT auftraege_stand_chk CHECK (stand IN (
    'offen', 'in_arbeit', 'erzeugt', 'wartet_freigabe', 'fertig', 'abgebrochen', 'fehler', 'abgelaufen', 'werkbank'));

-- Wie 096, dazu: art video zulassen und hoechstens 2 Video-Auftraege je von+laden und Kalendertag.
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
    IF p_art IS NULL OR p_art NOT IN ('reel', 'video') THEN
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
    IF p_art = 'video' THEN
        SELECT count(*) INTO v_n FROM auftrag.auftraege
         WHERE von = p_von AND laden = p_laden AND art = 'video'
           AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = v_tag;
        IF v_n >= 2 THEN
            RAISE EXCEPTION 'Tagesgrenze erreicht: hoechstens 2 Video-Auftraege je Laden pro Tag'; END IF;
    END IF;

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

-- Gemeinsamer Vergabekern fuer naechster (p_video = false: alles ausser Video) und video_naechster
-- (p_video = true: nur Video). Aeltesten uebernehmbaren Auftrag atomar vergeben. Uebernehmbar: offen,
-- oder in_arbeit mit abgelaufenem Lease (bei Nicht-Video zusaetzlich ohne ergebnis.video, der wartet auf den
-- Render). Nur vor frist_bis. Ein dritter Versuch wird nicht vergeben, sondern auf fehler gesetzt.
CREATE OR REPLACE FUNCTION auftrag._vergeben(p_an text, p_video boolean, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE a auftrag.auftraege;
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    LOOP
        SELECT * INTO a FROM auftrag.auftraege
         WHERE an = p_an AND frist_bis > now() AND (art = 'video') = p_video
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

-- Wie 096, dazu: Video-Auftraege vergibt nur video_naechster.
CREATE OR REPLACE FUNCTION auftrag.naechster(p_an text, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._vergeben(p_an, false, p_lease);
END $$;

-- Aeltesten uebernehmbaren Video-Auftrag fuer Marketing vergeben; Lease und Versuche wie naechster.
-- werkbank wird nie vergeben.
CREATE OR REPLACE FUNCTION auftrag.video_naechster(p_lease interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    RETURN auftrag._vergeben('marketing', true, p_lease);
END $$;

-- Wie 096, dazu: nie ein Video-Auftrag (der Reel-Render-Poller soll ihn nicht sehen).
CREATE OR REPLACE FUNCTION auftrag.laufende(p_an text) RETURNS SETOF jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
    SELECT auftrag._uebergabe(a) FROM auftrag.auftraege a
     WHERE a.an = p_an AND a.stand = 'in_arbeit' AND a.ergebnis ? 'video' AND a.art <> 'video'
     ORDER BY a.angelegt;
$$;

-- Wie 096, dazu: auch im Stand werkbank.
CREATE OR REPLACE FUNCTION auftrag.ergebnis_mischen(p_id uuid, p_teil jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    IF jsonb_typeof(p_teil) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    UPDATE auftrag.auftraege SET ergebnis = ergebnis || p_teil, geaendert = now()
     WHERE id = p_id AND stand IN ('in_arbeit', 'erzeugt', 'wartet_freigabe', 'werkbank');
    RETURN FOUND;
END $$;

-- Wie 096, dazu: in_arbeit->werkbank, werkbank->erzeugt|fehler|abgebrochen, erzeugt->werkbank,
-- wartet_freigabe->werkbank, wartet_freigabe->erzeugt (der Freigabe-Eintrag fehlt: Sales legt ihn neu an).
-- Jeder Uebergang von oder nach werkbank und wartet_freigabe->erzeugt gilt nur fuer art video.
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
            ('wartet_freigabe', 'fehler'),
            ('in_arbeit', 'werkbank'), ('werkbank', 'erzeugt'), ('werkbank', 'fehler'),
            ('werkbank', 'abgebrochen'), ('erzeugt', 'werkbank'), ('wartet_freigabe', 'werkbank'),
            ('wartet_freigabe', 'erzeugt'))) THEN
        RETURN false;
    END IF;
    UPDATE auftrag.auftraege
       SET stand = p_nach_stand,
           fehler = coalesce(v_fehler, fehler),
           vergeben_bis = NULL,
           geaendert = now(),
           verlauf = array_append(verlauf, jsonb_strip_nulls(jsonb_build_object(
               'stand', p_nach_stand, 'zeit', now(), 'fehler', v_fehler)))
     WHERE id = p_id AND stand = p_von_stand
       AND (art = 'video' OR ('werkbank' NOT IN (p_von_stand, p_nach_stand)
                              AND (p_von_stand, p_nach_stand) <> ('wartet_freigabe', 'erzeugt')));
    RETURN FOUND;
END $$;

-- Wie 096 (nur eigene Zeilen, von = sales), dazu (R7, enger): fuer art video nur die Uebergaenge, die Sales
-- braucht - erzeugt->wartet_freigabe (Eintrag im Freigabe-Eingang), wartet_freigabe->fertig|abgebrochen
-- (Entscheidung im Eingang), wartet_freigabe->erzeugt (Eintrag fehlt, neu anlegen). Alles andere false;
-- die Werkbank-Uebergaenge gehoeren Marketing. Fuer reel unveraendert.
CREATE OR REPLACE FUNCTION auftrag.sales_stand_setzen(p_id uuid, p_von_stand text, p_nach_stand text,
    p_fehler text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE v_art text;
BEGIN
    SELECT art INTO v_art FROM auftrag.auftraege WHERE id = p_id AND von = 'sales';
    IF v_art IS NULL THEN RETURN false; END IF;
    IF v_art = 'video' AND (p_von_stand IS NULL OR p_nach_stand IS NULL OR NOT ((p_von_stand, p_nach_stand) IN (
            ('erzeugt', 'wartet_freigabe'), ('wartet_freigabe', 'fertig'), ('wartet_freigabe', 'abgebrochen'),
            ('wartet_freigabe', 'erzeugt')))) THEN
        RETURN false;
    END IF;
    RETURN auftrag.stand_setzen(p_id, p_von_stand, p_nach_stand, p_fehler);
END $$;

REVOKE ALL ON FUNCTION auftrag._vergeben(text, boolean, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.video_naechster(interval) FROM PUBLIC;
-- Kein Grant an sales_app (auch nicht fuer _vergeben): Video-Auftraege vergibt nur die Marketing-API (supabase_admin).
-- Die Rechte der ersetzten Funktionen bleiben beim CREATE OR REPLACE erhalten.

COMMIT;
