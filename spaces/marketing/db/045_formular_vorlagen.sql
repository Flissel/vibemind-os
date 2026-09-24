-- 045_formular_vorlagen.sql — Terminkarten (docs/superpowers/specs/
-- 2026-09-24-terminkarten-design.md in sales-claw). Formular-Vorlagen als
-- zweite Art neben den Layouts, und der Auftragsweg Sales -> Marketing.
BEGIN;

-- 1) Vorlage: Art, Fassung, freigegebene Gestalt ------------------------------
ALTER TABLE marketing.layout_vorlagen
    ADD COLUMN IF NOT EXISTS art text NOT NULL DEFAULT 'layout',
    ADD COLUMN IF NOT EXISTS fassung integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS freigegebene_gestalt jsonb,
    ADD COLUMN IF NOT EXISTS freigegebene_fassung integer;
ALTER TABLE marketing.layout_vorlagen DROP CONSTRAINT IF EXISTS layout_vorlagen_art_check;
ALTER TABLE marketing.layout_vorlagen
    ADD CONSTRAINT layout_vorlagen_art_check CHECK (art IN ('layout', 'formular'));

-- Eine freigegebene Fassung ist unveraenderlich: freigegebene_gestalt darf
-- sich nur zusammen mit einer HOEHEREN freigegebenen_fassung aendern.
CREATE OR REPLACE FUNCTION marketing._freigabe_unveraenderlich() RETURNS trigger AS $$
BEGIN
    IF OLD.freigegebene_gestalt IS NOT NULL
       AND NEW.freigegebene_gestalt IS DISTINCT FROM OLD.freigegebene_gestalt
       AND coalesce(NEW.freigegebene_fassung, 0) <= coalesce(OLD.freigegebene_fassung, 0) THEN
        RAISE EXCEPTION 'freigegebene Fassung % von % ist unveraenderlich',
            OLD.freigegebene_fassung, OLD.name;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_freigabe_unveraenderlich ON marketing.layout_vorlagen;
CREATE TRIGGER trg_freigabe_unveraenderlich BEFORE UPDATE ON marketing.layout_vorlagen
    FOR EACH ROW EXECUTE FUNCTION marketing._freigabe_unveraenderlich();

-- Eine Formular-Vorlage gibt NUR das bestellende Mitglied frei, also nur
-- vorlagenauftrag_urteil. Die Funktion setzt dafuer transaktionslokal
-- marketing.formular_freigabe = 'an'; jeder andere Weg (die Layout-API,
-- layout_entscheiden, Handarbeit) scheitert hier.
CREATE OR REPLACE FUNCTION marketing._formular_freigabe_nur_per_urteil() RETURNS trigger AS $$
BEGIN
    IF NEW.art = 'formular' AND NEW.status = 'freigegeben'
       AND OLD.status IS DISTINCT FROM 'freigegeben'
       AND coalesce(current_setting('marketing.formular_freigabe', true), '') <> 'an' THEN
        RAISE EXCEPTION 'Formular-Vorlage % wird nur ueber vorlagenauftrag_urteil freigegeben',
            NEW.name;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_formular_freigabe ON marketing.layout_vorlagen;
CREATE TRIGGER trg_formular_freigabe BEFORE UPDATE ON marketing.layout_vorlagen
    FOR EACH ROW EXECUTE FUNCTION marketing._formular_freigabe_nur_per_urteil();

-- 2) Pruefung einer Formular-Gestalt -> NULL oder ein Satz fuer den Menschen --
CREATE OR REPLACE FUNCTION marketing._formular_gestalt_fehler(g jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    b numeric; h numeric; f jsonb; t jsonb; p jsonb; namen text[] := '{}';
BEGIN
    IF jsonb_typeof(g) <> 'object' THEN RETURN 'Gestalt ist kein Objekt'; END IF;
    b := (g->'seite'->>'breite_mm')::numeric;
    h := (g->'seite'->>'hoehe_mm')::numeric;
    IF b IS NULL OR h IS NULL OR b NOT BETWEEN 50 AND 300 OR h NOT BETWEEN 50 AND 300 THEN
        RETURN 'seite.breite_mm und seite.hoehe_mm muessen zwischen 50 und 300 liegen';
    END IF;
    IF jsonb_typeof(g->'felder') <> 'array'
       OR jsonb_array_length(g->'felder') NOT BETWEEN 1 AND 60 THEN
        RETURN 'felder muss eine Liste mit 1 bis 60 Feldern sein';
    END IF;
    FOR f IN SELECT * FROM jsonb_array_elements(g->'felder') LOOP
        IF coalesce(f->>'name', '') !~ '^[a-z][a-z0-9_]{0,39}$' THEN
            RETURN format('Feldname %s ist ungueltig', f->>'name');
        END IF;
        IF (f->>'name') = ANY(namen) THEN
            RETURN format('Feldname %s kommt doppelt vor', f->>'name');
        END IF;
        namen := namen || (f->>'name');
        IF length(btrim(coalesce(f->>'beschriftung', ''))) NOT BETWEEN 1 AND 80 THEN
            RETURN format('Feld %s braucht eine Beschriftung (1-80 Zeichen)', f->>'name');
        END IF;
        IF coalesce(f->>'art', '') NOT IN ('text', 'datum', 'uhrzeit', 'telefon', 'mehrzeilig') THEN
            RETURN format('Feld %s hat die unbekannte Art %s', f->>'name', f->>'art');
        END IF;
        IF coalesce(f->>'quelle', '') !~ '^(frei|(kunde|termin|mitglied)\.[a-z_]{2,20})$' THEN
            RETURN format('Feld %s hat die ungueltige Quelle %s', f->>'name', f->>'quelle');
        END IF;
        p := f->'platz';
        IF (p->>'x')::numeric < 0 OR (p->>'y')::numeric < 0
           OR (p->>'breite')::numeric < 5 OR (p->>'hoehe')::numeric < 3
           OR (p->>'x')::numeric + (p->>'breite')::numeric > b
           OR (p->>'y')::numeric + (p->>'hoehe')::numeric > h THEN
            RETURN format('Feld %s liegt nicht vollstaendig auf der Seite', f->>'name');
        END IF;
    END LOOP;
    IF g ? 'texte' THEN
        IF jsonb_typeof(g->'texte') <> 'array' OR jsonb_array_length(g->'texte') > 30 THEN
            RETURN 'texte muss eine Liste mit hoechstens 30 Eintraegen sein';
        END IF;
        FOR t IN SELECT * FROM jsonb_array_elements(g->'texte') LOOP
            p := t->'platz';
            IF length(coalesce(t->>'text', '')) NOT BETWEEN 1 AND 200
               OR (p->>'x')::numeric < 0 OR (p->>'y')::numeric < 0
               OR (p->>'x')::numeric + (p->>'breite')::numeric > b
               OR (p->>'y')::numeric + (p->>'hoehe')::numeric > h THEN
                RETURN 'ein fester Text ist leer, zu lang oder liegt nicht auf der Seite';
            END IF;
        END LOOP;
    END IF;
    RETURN NULL;
EXCEPTION WHEN invalid_text_representation OR data_exception THEN
    RETURN 'Gestalt enthaelt einen Wert, der keine Zahl ist';
END $$;

-- 3) Auftraege ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marketing.vorlagenauftraege (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    laden          text NOT NULL CHECK (laden ~ '^[a-z][a-z0-9_]{0,30}$'),
    art            text NOT NULL CHECK (art IN ('terminkarte')),
    bild           bytea CHECK (bild IS NULL OR octet_length(bild) <= 8388608),
    bild_typ       text CHECK (bild_typ IS NULL OR bild_typ IN ('image/jpeg', 'image/png')),
    beschreibung   text NOT NULL DEFAULT '',
    anmerkung      text NOT NULL DEFAULT '',
    vorlage        text NOT NULL DEFAULT '',
    runde          integer NOT NULL DEFAULT 1,
    fehlversuche   integer NOT NULL DEFAULT 0,
    fehler         text NOT NULL DEFAULT '',
    rueckmeldungen jsonb NOT NULL DEFAULT '[]'::jsonb,
    status         text NOT NULL DEFAULT 'neu' CHECK (status IN
                   ('neu', 'in_arbeit', 'vorgelegt', 'nachbessern', 'freigegeben', 'gescheitert')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT vorlagenauftrag_bild_oder_beschreibung CHECK (
        (bild IS NOT NULL AND bild_typ IS NOT NULL AND beschreibung = '')
        OR (bild IS NULL AND bild_typ IS NULL AND length(btrim(beschreibung)) > 0))
);

CREATE OR REPLACE FUNCTION marketing._vorlagenauftrag_uebergang() RETURNS trigger AS $$
BEGIN
    IF NEW.laden <> OLD.laden OR NEW.art <> OLD.art
       OR NEW.bild IS DISTINCT FROM OLD.bild OR NEW.beschreibung <> OLD.beschreibung THEN
        RAISE EXCEPTION 'Laden, Art, Bild und Beschreibung eines Auftrags sind fest';
    END IF;
    IF NEW.status <> OLD.status AND (OLD.status, NEW.status) NOT IN (
        ('neu', 'in_arbeit'), ('nachbessern', 'in_arbeit'),
        ('in_arbeit', 'vorgelegt'), ('in_arbeit', 'neu'),
        ('in_arbeit', 'nachbessern'), ('in_arbeit', 'gescheitert'),
        ('vorgelegt', 'freigegeben'), ('vorgelegt', 'nachbessern')) THEN
        RAISE EXCEPTION 'Uebergang % -> % ist nicht erlaubt', OLD.status, NEW.status;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_vorlagenauftrag_uebergang ON marketing.vorlagenauftraege;
CREATE TRIGGER trg_vorlagenauftrag_uebergang BEFORE UPDATE ON marketing.vorlagenauftraege
    FOR EACH ROW EXECUTE FUNCTION marketing._vorlagenauftrag_uebergang();

-- 4) Der Laden kommt aus der Anmeldung, nie aus einem Parameter ---------------
-- session_user bleibt auch in SECURITY-DEFINER-Funktionen die Rolle, mit der
-- sich der Aufrufer angemeldet hat (current_user wechselt, session_user nicht).
CREATE OR REPLACE FUNCTION marketing._laden_des_aufrufers() RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE r text := session_user;
BEGIN
    IF r = 'sales_app' THEN RETURN 'sales'; END IF;
    IF r ~ '^sales_app_[a-z][a-z0-9_]{0,30}$' THEN RETURN substr(r, 11); END IF;
    RAISE EXCEPTION 'Rolle % gehoert zu keinem Laden', r USING ERRCODE = '42501';
END $$;

-- 5) Sales-Seite -----------------------------------------------------------------
CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_anlegen(
    p_art text, p_bild bytea, p_bild_typ text, p_beschreibung text, p_anmerkung text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
DECLARE v_laden text := marketing._laden_des_aufrufers(); v_id uuid; v_offen record;
BEGIN
    IF p_art IS DISTINCT FROM 'terminkarte' THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Nur Terminkarten koennen bestellt werden.');
    END IF;
    IF (p_bild IS NULL) = (length(btrim(coalesce(p_beschreibung, ''))) = 0) THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            'Entweder ein Foto ODER eine Beschreibung der Felder - genau eins von beiden.');
    END IF;
    SELECT id, status, laden INTO v_offen FROM vorlagenauftraege
     WHERE art = p_art AND status NOT IN ('freigegeben', 'gescheitert') LIMIT 1;
    IF FOUND THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Es laeuft schon ein Auftrag fuer diese Teamvorlage (Laden %s, Stand %s).',
            v_offen.laden, v_offen.status));
    END IF;
    INSERT INTO vorlagenauftraege (laden, art, bild, bild_typ, beschreibung, anmerkung, vorlage)
    VALUES (v_laden, p_art, p_bild, CASE WHEN p_bild IS NULL THEN NULL ELSE p_bild_typ END,
            CASE WHEN p_bild IS NULL THEN btrim(p_beschreibung) ELSE '' END,
            coalesce(p_anmerkung, ''), p_art)
    RETURNING id INTO v_id;
    RETURN jsonb_build_object('ok', true, 'id', v_id);
EXCEPTION WHEN check_violation THEN
    RETURN jsonb_build_object('ok', false, 'grund',
        'Das Foto ist zu gross (hoechstens 8 MB) oder kein JPEG/PNG.');
END $$;

CREATE OR REPLACE FUNCTION marketing.vorlagenauftraege_des_ladens()
RETURNS TABLE(id uuid, art text, status text, runde int, vorlage text, fehler text,
              rueckmeldungen jsonb, aktualisiert timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
    SELECT id, art, status, runde, vorlage, fehler, rueckmeldungen, updated_at
      FROM vorlagenauftraege WHERE laden = marketing._laden_des_aufrufers()
     ORDER BY updated_at DESC LIMIT 20;
$$;

CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_urteil(
    p_id uuid, p_urteil text, p_anmerkung text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
DECLARE v_laden text := marketing._laden_des_aufrufers(); a record;
BEGIN
    SELECT * INTO a FROM vorlagenauftraege WHERE id = p_id AND laden = v_laden FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Kein solcher Auftrag in diesem Laden.');
    END IF;
    IF a.status <> 'vorgelegt' THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            format('Der Auftrag steht auf %s, nicht auf vorgelegt.', a.status));
    END IF;
    IF p_urteil = 'ja' THEN
        PERFORM set_config('marketing.formular_freigabe', 'an', true);
        UPDATE layout_vorlagen SET status = 'freigegeben',
               freigegebene_gestalt = gestalt, freigegebene_fassung = fassung,
               entschieden_von = 'laden:' || v_laden, entschieden_am = now()
         WHERE name = a.vorlage;
        -- Ruling 4: sofort wieder schliessen, sonst bliebe das Tor fuer den
        -- Rest DIESER Transaktion offen (set_config(..., true) gilt bis zum
        -- Transaktionsende, nicht nur fuer die eine UPDATE-Anweisung).
        PERFORM set_config('marketing.formular_freigabe', '', true);
        UPDATE vorlagenauftraege SET status = 'freigegeben',
               rueckmeldungen = rueckmeldungen || jsonb_build_object(
                   'runde', a.runde, 'urteil', 'ja', 'anmerkung', coalesce(p_anmerkung, ''),
                   'am', now())
         WHERE id = p_id;
        RETURN jsonb_build_object('ok', true, 'status', 'freigegeben');
    ELSIF p_urteil = 'nein' THEN
        IF length(btrim(coalesce(p_anmerkung, ''))) = 0 THEN
            RETURN jsonb_build_object('ok', false, 'grund',
                'Ein Nein braucht eine Anmerkung - was soll anders werden?');
        END IF;
        UPDATE vorlagenauftraege SET status = 'nachbessern', runde = runde + 1,
               rueckmeldungen = rueckmeldungen || jsonb_build_object(
                   'runde', a.runde, 'urteil', 'nein', 'anmerkung', btrim(p_anmerkung),
                   'am', now())
         WHERE id = p_id;
        RETURN jsonb_build_object('ok', true, 'status', 'nachbessern', 'runde', a.runde + 1);
    END IF;
    RETURN jsonb_build_object('ok', false, 'grund', 'Urteil ist ja oder nein.');
END $$;

CREATE OR REPLACE FUNCTION marketing.formular_vorlage(p_name text)
RETURNS TABLE(name text, status text, fassung int, gestalt jsonb,
              freigegebene_fassung int, freigegebene_gestalt jsonb)
LANGUAGE sql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
    SELECT name, status, fassung, gestalt, freigegebene_fassung, freigegebene_gestalt
      FROM layout_vorlagen WHERE name = p_name AND art = 'formular';
$$;

-- 6) Marketing-Seite -----------------------------------------------------------
CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_uebernehmen()
RETURNS TABLE(id uuid, art text, runde int, bild_b64 text, bild_typ text,
              beschreibung text, anmerkung text, rueckmeldungen jsonb)
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
    SELECT a.id INTO v_id FROM marketing.vorlagenauftraege a
     WHERE a.status IN ('neu', 'nachbessern') ORDER BY a.updated_at
     LIMIT 1 FOR UPDATE SKIP LOCKED;
    IF v_id IS NULL THEN RETURN; END IF;
    UPDATE marketing.vorlagenauftraege a SET status = 'in_arbeit' WHERE a.id = v_id;
    RETURN QUERY SELECT a.id, a.art, a.runde, encode(a.bild, 'base64'), a.bild_typ,
                        a.beschreibung, a.anmerkung, a.rueckmeldungen
                   FROM marketing.vorlagenauftraege a WHERE a.id = v_id;
END $$;

CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_vorlegen(p_id uuid, p_gestalt jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a record; v_fehler text := marketing._formular_gestalt_fehler(p_gestalt);
BEGIN
    SELECT * INTO a FROM marketing.vorlagenauftraege WHERE id = p_id FOR UPDATE;
    IF NOT FOUND OR a.status <> 'in_arbeit' THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Auftrag ist nicht in Arbeit.');
    END IF;
    IF v_fehler IS NOT NULL THEN
        RETURN jsonb_build_object('ok', false, 'grund', v_fehler);
    END IF;
    INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, art,
                                           vorgeschlagen_von, fassung)
    VALUES (a.vorlage, 'Teamvorlage Terminkarte', p_gestalt, 'vorschlag', 'formular',
            'marketing-arbeiter', 1)
    ON CONFLICT (name) DO UPDATE SET gestalt = EXCLUDED.gestalt, status = 'vorschlag',
        fassung = CASE WHEN marketing.layout_vorlagen.freigegebene_fassung
                            = marketing.layout_vorlagen.fassung
                       THEN marketing.layout_vorlagen.fassung + 1
                       ELSE marketing.layout_vorlagen.fassung END,
        entschieden_von = NULL, entschieden_am = NULL;
    UPDATE marketing.vorlagenauftraege SET status = 'vorgelegt', fehler = '' WHERE id = p_id;
    RETURN jsonb_build_object('ok', true);
END $$;

CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_zurueckstellen(p_id uuid, p_fehler text)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a record; v_ziel text;
BEGIN
    SELECT * INTO a FROM marketing.vorlagenauftraege WHERE id = p_id FOR UPDATE;
    IF NOT FOUND OR a.status <> 'in_arbeit' THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Auftrag ist nicht in Arbeit.');
    END IF;
    IF a.fehlversuche + 1 >= 3 THEN
        v_ziel := 'gescheitert';
    ELSIF a.runde > 1 THEN
        v_ziel := 'nachbessern';
    ELSE
        v_ziel := 'neu';
    END IF;
    UPDATE marketing.vorlagenauftraege SET status = v_ziel,
           fehlversuche = fehlversuche + 1, fehler = left(coalesce(p_fehler, ''), 500)
     WHERE id = p_id;
    RETURN jsonb_build_object('ok', true, 'status', v_ziel);
END $$;

-- 7) Rechte ---------------------------------------------------------------------
REVOKE ALL ON marketing.vorlagenauftraege FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.vorlagenauftrag_uebernehmen() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.vorlagenauftrag_vorlegen(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.vorlagenauftrag_zurueckstellen(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.vorlagenauftrag_anlegen(text, bytea, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.vorlagenauftraege_des_ladens() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.vorlagenauftrag_urteil(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.formular_vorlage(text) FROM PUBLIC;

-- Jeder Laden: sales_app und jede bestehende sales_app_<x>. Neue Laeden
-- bekommen dieselben Rechte ueber sales-claw/db/laden-anlegen.sql.
DO $$
DECLARE r text;
BEGIN
    FOR r IN SELECT rolname FROM pg_roles
              WHERE rolname = 'sales_app' OR rolname ~ '^sales_app_[a-z][a-z0-9_]{0,30}$' LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA marketing TO %I', r);
        EXECUTE format('GRANT EXECUTE ON FUNCTION
            marketing.vorlagenauftrag_anlegen(text, bytea, text, text, text),
            marketing.vorlagenauftraege_des_ladens(),
            marketing.vorlagenauftrag_urteil(uuid, text, text),
            marketing.formular_vorlage(text) TO %I', r);
    END LOOP;
END $$;

COMMIT;
