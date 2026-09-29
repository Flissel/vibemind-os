-- 050_marketing_pult.sql — Datenmodell des Marketing-Pults (Spec sales-claw
-- docs/superpowers/specs/2026-09-29-marketing-pult-design.md §3.3). Nur
-- Ergaenzungen; broadcast_proposals und Formular-Vorlagen bleiben unberuehrt.
-- Idempotent.
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.mandanten (
    id          text PRIMARY KEY CHECK (id ~ '^[a-z][a-z0-9_]{1,30}$'),
    name        text NOT NULL,
    aktiv       boolean NOT NULL DEFAULT false,
    verteiler   text[] NOT NULL DEFAULT '{}',
    pflichtteil jsonb NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO marketing.mandanten (id, name, aktiv, verteiler, pflichtteil) VALUES
  ('vibemind',   'VibeMind',   true,  ARRAY['sales'],
   '{"impressum": "", "abmelde_hinweis": "Du bekommst diese Mail, weil du dich eingetragen hast. Abmelden: {abmeldelink}"}'),
  ('fin2gether', 'fin2gether', false, ARRAY[]::text[],
   '{"impressum": "", "abmelde_hinweis": "", "vermittlerangaben": ""}')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS marketing.inhalte (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant              text NOT NULL REFERENCES marketing.mandanten(id),
    art                  text NOT NULL CHECK (art IN ('newsletter','post','material')),
    titel                text NOT NULL CHECK (length(btrim(titel)) > 0),
    status               text NOT NULL DEFAULT 'entwurf'
                         CHECK (status IN ('entwurf','freigegeben','abgelehnt')),
    freigegebene_fassung int,
    entschieden_von      text,
    entschieden_am       timestamptz,
    grund                text,
    herkunft_proposal    uuid UNIQUE REFERENCES marketing.broadcast_proposals(id),
    erstellt_am          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inhalte_entscheid_hat_urheber
      CHECK (status = 'entwurf' OR (entschieden_von IS NOT NULL AND entschieden_am IS NOT NULL)),
    CONSTRAINT inhalte_freigabe_hat_fassung
      CHECK (status <> 'freigegeben' OR freigegebene_fassung IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS inhalte_mandant_status_idx ON marketing.inhalte (mandant, status, art);

CREATE TABLE IF NOT EXISTS marketing.inhalt_fassungen (
    inhalt         uuid NOT NULL REFERENCES marketing.inhalte(id),
    fassung        int  NOT NULL CHECK (fassung >= 1),
    felder         jsonb NOT NULL,
    layout         text NOT NULL,
    layout_fassung int,
    urheber        text NOT NULL CHECK (urheber IN ('agent','betreiber')),
    erstellt_am    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (inhalt, fassung)
);

CREATE OR REPLACE FUNCTION marketing._fassung_unveraenderlich() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Fassungen sind unveraenderlich - speichern legt eine neue an';
END $$;
DROP TRIGGER IF EXISTS trg_inhalt_fassung_unveraenderlich ON marketing.inhalt_fassungen;
CREATE TRIGGER trg_inhalt_fassung_unveraenderlich
  BEFORE UPDATE OR DELETE ON marketing.inhalt_fassungen
  FOR EACH ROW EXECUTE FUNCTION marketing._fassung_unveraenderlich();

ALTER TABLE marketing.layout_vorlagen ADD COLUMN IF NOT EXISTS mandant text REFERENCES marketing.mandanten(id);
ALTER TABLE marketing.layout_vorlagen ADD COLUMN IF NOT EXISTS inhaltsart text
  CHECK (inhaltsart IS NULL OR inhaltsart IN ('newsletter','post','material'));
ALTER TABLE marketing.layout_vorlagen ADD COLUMN IF NOT EXISTS fassung int NOT NULL DEFAULT 1;
ALTER TABLE marketing.layout_vorlagen ADD COLUMN IF NOT EXISTS standard boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS layout_standard_je_art
  ON marketing.layout_vorlagen (mandant, inhaltsart) WHERE standard;

CREATE TABLE IF NOT EXISTS marketing.layout_fassungen (
    layout      text NOT NULL REFERENCES marketing.layout_vorlagen(name),
    fassung     int  NOT NULL CHECK (fassung >= 1),
    gestalt     jsonb NOT NULL,
    erstellt_von text NOT NULL,
    erstellt_am timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (layout, fassung)
);
DROP TRIGGER IF EXISTS trg_layout_fassung_unveraenderlich ON marketing.layout_fassungen;
CREATE TRIGGER trg_layout_fassung_unveraenderlich
  BEFORE UPDATE OR DELETE ON marketing.layout_fassungen
  FOR EACH ROW EXECUTE FUNCTION marketing._fassung_unveraenderlich();

-- Bestand: die drei Gewaender sind VibeMind-Layouts fuer Newsletter,
-- 'dunkel' ist Standard. Fassung 1 = heutige Gestalt.
UPDATE marketing.layout_vorlagen SET mandant='vibemind', inhaltsart='newsletter'
 WHERE art='layout' AND mandant IS NULL;
UPDATE marketing.layout_vorlagen SET standard=true
 WHERE name='dunkel' AND art='layout'
   AND NOT EXISTS (SELECT 1 FROM marketing.layout_vorlagen
                   WHERE standard AND mandant='vibemind' AND inhaltsart='newsletter');
INSERT INTO marketing.layout_fassungen (layout, fassung, gestalt, erstellt_von)
SELECT name, 1, gestalt, coalesce(vorgeschlagen_von, 'bestand')
  FROM marketing.layout_vorlagen WHERE art='layout'
ON CONFLICT DO NOTHING;

-- Bestand: jeder broadcast_proposal wird ein VibeMind-Newsletter mit Fassung 1.
INSERT INTO marketing.inhalte (mandant, art, titel, status, herkunft_proposal, erstellt_am)
SELECT 'vibemind', 'newsletter',
       coalesce(nullif(btrim(p.draft_subject), ''), left(p.draft_body_text, 60)),
       'entwurf', p.id, p.created_at
  FROM marketing.broadcast_proposals p
ON CONFLICT (herkunft_proposal) DO NOTHING;
INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber, erstellt_am)
SELECT i.id, 1,
       jsonb_build_object(
         'betreff', coalesce(p.draft_subject, ''),
         'vorschautext', '',
         'abschnitte', jsonb_build_array(jsonb_build_object('titel', '', 'text', p.draft_body_text)),
         'knopf_text', '', 'knopf_link', ''),
       'dunkel', 1, 'agent', p.created_at
  FROM marketing.inhalte i JOIN marketing.broadcast_proposals p ON p.id = i.herkunft_proposal
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION marketing.pult_gestalt_fehler(p jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_f text;
BEGIN
  v_f := marketing.gestalt_pruefen(p);           -- acht Farben, #rrggbb, Kontrastregeln (044)
  IF v_f IS NOT NULL THEN RETURN v_f; END IF;
  IF p ? 'schrift' AND NOT (p->>'schrift' IN ('system','serif','mono')) THEN
    RETURN 'schrift muss system, serif oder mono sein'; END IF;
  IF p ? 'abstand' AND NOT (p->>'abstand' IN ('eng','mittel','weit')) THEN
    RETURN 'abstand muss eng, mittel oder weit sein'; END IF;
  IF p ? 'rundung' AND (jsonb_typeof(p->'rundung') <> 'number'
                        OR (p->>'rundung')::numeric < 0 OR (p->>'rundung')::numeric > 24) THEN
    RETURN 'rundung muss eine Zahl von 0 bis 24 sein'; END IF;
  IF p ? 'kopf_text' AND length(p->>'kopf_text') > 120 THEN
    RETURN 'kopf_text hoechstens 120 Zeichen'; END IF;
  IF p ? 'fuss_text' AND length(p->>'fuss_text') > 300 THEN
    RETURN 'fuss_text hoechstens 300 Zeichen'; END IF;
  IF p ? 'logo' AND (p->>'logo' !~ '^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$'
                     OR length(p->>'logo') > 204800) THEN
    RETURN 'logo muss ein PNG/JPEG unter 150 KB sein'; END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_fassung_speichern(
    p_inhalt uuid, p_felder jsonb, p_layout text, p_urheber text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_n int;
BEGIN
  IF jsonb_typeof(p_felder) IS DISTINCT FROM 'object'
     OR length(btrim(coalesce(p_felder->>'betreff', ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Betreff gibt es keine Fassung'; END IF;
  IF jsonb_typeof(p_felder->'abschnitte') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_felder->'abschnitte') = 0 THEN
    RAISE EXCEPTION 'Mindestens ein Abschnitt ist noetig'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.layout_vorlagen WHERE name = p_layout AND art = 'layout') THEN
    RAISE EXCEPTION 'Layout % gibt es nicht', p_layout; END IF;
  PERFORM 1 FROM marketing.inhalte WHERE id = p_inhalt AND status = 'entwurf' FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Nur Entwuerfe lassen sich bearbeiten'; END IF;
  SELECT coalesce(max(fassung), 0) + 1 INTO v_n FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber)
  VALUES (p_inhalt, v_n, p_felder, p_layout,
          (SELECT fassung FROM marketing.layout_vorlagen WHERE name = p_layout), p_urheber);
  RETURN v_n;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_entscheiden(
    p_inhalt uuid, p_urteil text, p_von text, p_grund text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  IF p_urteil NOT IN ('freigeben','ablehnen') THEN
    RAISE EXCEPTION 'Urteil muss freigeben oder ablehnen sein'; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Namen kein Urteil'; END IF;
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status IS DISTINCT FROM 'entwurf' THEN
    RAISE EXCEPTION 'Schon entschieden (%)', coalesce(v_status, 'unbekannt'); END IF;
  UPDATE marketing.inhalte
     SET status = CASE p_urteil WHEN 'freigeben' THEN 'freigegeben' ELSE 'abgelehnt' END,
         freigegebene_fassung = CASE p_urteil WHEN 'freigeben'
           THEN (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt) END,
         entschieden_von = p_von, entschieden_am = now(), grund = p_grund
   WHERE id = p_inhalt
  RETURNING status INTO v_status;
  RETURN v_status;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_layout_speichern(
    p_name text, p_gestalt jsonb, p_von text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_n int;
BEGIN
  v_f := marketing.pult_gestalt_fehler(p_gestalt);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION 'Layout ungueltig: %', v_f; END IF;
  PERFORM 1 FROM marketing.layout_vorlagen WHERE name = p_name AND art = 'layout' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Layout % gibt es nicht', p_name; END IF;
  SELECT coalesce(max(fassung), 0) + 1 INTO v_n FROM marketing.layout_fassungen WHERE layout = p_name;
  INSERT INTO marketing.layout_fassungen (layout, fassung, gestalt, erstellt_von)
  VALUES (p_name, v_n, p_gestalt, p_von);
  UPDATE marketing.layout_vorlagen SET gestalt = p_gestalt, fassung = v_n WHERE name = p_name;
  RETURN v_n;
END $$;

CREATE OR REPLACE FUNCTION marketing.pult_layout_als_standard(p_name text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE v_m text; v_a text;
BEGIN
  SELECT mandant, inhaltsart INTO v_m, v_a FROM marketing.layout_vorlagen
   WHERE name = p_name AND art = 'layout' FOR UPDATE;
  IF v_m IS NULL THEN RAISE EXCEPTION 'Layout % gibt es nicht', p_name; END IF;
  UPDATE marketing.layout_vorlagen SET standard = false
   WHERE mandant = v_m AND inhaltsart = v_a AND standard AND name <> p_name;
  UPDATE marketing.layout_vorlagen SET standard = true WHERE name = p_name;
END $$;

COMMIT;
