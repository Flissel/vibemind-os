-- RUNBOOK: nach 099. Idempotent, eine Transaktion. Danach verify_100 (in ROLLBACK).
-- 100: Auftragsbuch bekommt einen schmalen, eigenen Eingang fuer den Absender 'brain'. Rein additiv: die
-- bestehenden Funktionen (Sales, Marketing) bleiben unveraendert.
-- Neu: zwei Spalten (art_version, plan_id), die Tabelle brain_arten (welche Arten das Gehirn anlegen darf),
-- drei SECURITY-DEFINER-Funktionen (brain_anlegen, brain_lesen, brain_probe_erledigen) und die Rolle brain_app,
-- die nur diese drei ausfuehren darf. brain_anlegen schreibt selbst (ohne den allgemeinen Anlegeweg), mit
-- Tagesgrenzen: 60 Auftraege des Gehirns und 50 Proben je Kalendertag (Europe/Berlin).
-- Die Rolle ist NOLOGIN; Anmeldung und Geheimnis richtet der Betrieb getrennt ein, nicht diese Datei.
BEGIN;

-- Spalten fuer Version der Auftragsart und Plan-Kennung; beide frei (Altzeilen bleiben NULL).
ALTER TABLE auftrag.auftraege ADD COLUMN IF NOT EXISTS art_version int;
ALTER TABLE auftrag.auftraege ADD COLUMN IF NOT EXISTS plan_id text;

-- Das Gehirn darf eigene Arten fuehren; fuer alle anderen Absender bleiben es reel und video.
ALTER TABLE auftrag.auftraege DROP CONSTRAINT IF EXISTS auftraege_art_chk;
ALTER TABLE auftrag.auftraege ADD CONSTRAINT auftraege_art_chk CHECK (art IN ('reel', 'video') OR von = 'brain');

-- Welche (Empfaenger, Art, Version) das Gehirn anlegen darf.
CREATE TABLE IF NOT EXISTS auftrag.brain_arten (
    an      text NOT NULL,
    art     text NOT NULL,
    version int  NOT NULL,
    PRIMARY KEY (an, art, version)
);
INSERT INTO auftrag.brain_arten (an, art, version) VALUES ('brain', 'probe', 1) ON CONFLICT DO NOTHING;
REVOKE ALL ON TABLE auftrag.brain_arten FROM PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_app') THEN
        CREATE ROLE brain_app NOLOGIN;
    END IF;
END $$;

-- Auftrag des Gehirns anlegen. Absender fest 'brain', Tiefe 0, Frist p_frist_min Minuten.
CREATE OR REPLACE FUNCTION auftrag.brain_anlegen(p_an text, p_art text, p_version int, p_laden text,
    p_mandant text, p_eingabe jsonb, p_plan_id text, p_frist_min int) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
DECLARE
    v_id uuid := gen_random_uuid();
    v_tag date := (now() AT TIME ZONE 'Europe/Berlin')::date;
    v_n int;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM auftrag.brain_arten
                    WHERE an = p_an AND art = p_art AND version = p_version) THEN
        RAISE EXCEPTION 'Unbekannte Auftragsart'; END IF;
    IF coalesce(btrim(p_laden), '') = '' OR coalesce(btrim(p_mandant), '') = '' THEN
        RAISE EXCEPTION 'Auftrag braucht Laden und Mandant'; END IF;
    IF p_frist_min IS NULL OR p_frist_min NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'Frist ausserhalb 1 bis 120 Minuten'; END IF;
    IF p_plan_id IS NOT NULL AND p_plan_id !~ '^[A-Za-z0-9_-]{1,64}$' THEN
        RAISE EXCEPTION 'Plan-Kennung ungueltig'; END IF;

    -- Zaehlung gegen gleichzeitiges Anlegen absichern (derselbe Schluessel wie beim allgemeinen Anlegen)
    PERFORM pg_advisory_xact_lock(hashtext('auftrag:brain'));
    IF p_art = 'probe' THEN
        SELECT count(*) INTO v_n FROM auftrag.auftraege
         WHERE von = 'brain' AND art = 'probe'
           AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = v_tag;
        IF v_n >= 50 THEN
            RAISE EXCEPTION 'Tagesgrenze erreicht: hoechstens 50 Proben pro Tag'; END IF;
    ELSE
        SELECT count(*) INTO v_n FROM auftrag.auftraege
         WHERE von = 'brain' AND art <> 'probe'
           AND (angelegt AT TIME ZONE 'Europe/Berlin')::date = v_tag;
        IF v_n >= 60 THEN
            RAISE EXCEPTION 'Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag'; END IF;
    END IF;

    INSERT INTO auftrag.auftraege (id, von, an, laden, mandant, art, art_version, plan_id, eingabe,
                                   wurzel_id, tiefe, stand, frist_bis, verlauf)
    VALUES (v_id, 'brain', p_an, p_laden, p_mandant, p_art, p_version, p_plan_id,
            coalesce(p_eingabe, '{}'::jsonb), v_id, 0, 'offen',
            now() + make_interval(mins => p_frist_min),
            ARRAY[jsonb_build_object('stand', 'offen', 'zeit', now())]);
    RETURN v_id;
END $$;

-- Ganze Zeile als JSON, nur fuer Auftraege des Gehirns; sonst (auch bei unbekannter Kennung) NULL.
CREATE OR REPLACE FUNCTION auftrag.brain_lesen(p_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
    SELECT to_jsonb(a) FROM auftrag.auftraege a WHERE a.id = p_id AND a.von = 'brain';
$$;

-- Eine offene Probe des Gehirns an das Gehirn abschliessen; true nur, wenn dieser Aufruf sie erledigt hat.
CREATE OR REPLACE FUNCTION auftrag.brain_probe_erledigen(p_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = auftrag, pg_temp AS $$
BEGIN
    UPDATE auftrag.auftraege
       SET stand = 'fertig', ergebnis = '{"probe": true}'::jsonb, vergeben_bis = NULL, geaendert = now(),
           verlauf = array_append(verlauf, jsonb_build_object('stand', 'fertig', 'zeit', now()))
     WHERE id = p_id AND von = 'brain' AND an = 'brain' AND art = 'probe' AND stand = 'offen';
    RETURN FOUND;
END $$;

-- Rechte: neue Funktionen sind fuer PUBLIC ausfuehrbar - zuerst alles entziehen, dann nur brain_app.
REVOKE ALL ON FUNCTION auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.brain_lesen(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION auftrag.brain_probe_erledigen(uuid) FROM PUBLIC;

GRANT USAGE ON SCHEMA auftrag TO brain_app;
GRANT EXECUTE ON FUNCTION auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int) TO brain_app;
GRANT EXECUTE ON FUNCTION auftrag.brain_lesen(uuid) TO brain_app;
GRANT EXECUTE ON FUNCTION auftrag.brain_probe_erledigen(uuid) TO brain_app;

COMMIT;
