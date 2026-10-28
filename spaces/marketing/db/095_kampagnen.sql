-- RUNBOOK: nach 094. Idempotent, eine Transaktion. Danach verify_095 (in ROLLBACK).
-- 095: Echte Kampagnen (Betreiber 28.10.2026, Kampagnen-Runde): eine Kampagne hat Ziel, Zielgruppe
-- (Auswahl: Kontakte aus Sales | oeffentlich ueber Kanaele | Newsletter-Verteiler), Zeitraum und
-- Status; darunter haengen die Beitraege (kampagnen_plan.kampagne). Der Agent plant KOMPLETT: neue
-- Kampagnen stehen auf 'in_planung', der Kampagnen-Arbeiter laesst den Agenten Beitraege vorschlagen
-- (plan_anlegen, status 'vorschlag') und setzt danach 'geplant'. Versendet wird dadurch nichts - jeder
-- Beitrag geht weiter durch Entwurf und Freigabe.
-- Ausserdem: pult_mandant_loeschen (094) loescht die Kampagnen der Marke mit (im replica-Modus greift
-- kein Fremdschluessel, ohne diese Zeilen blieben Waisen).
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.kampagnen (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant      text NOT NULL REFERENCES marketing.mandanten(id),
    name         text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    ziel         text NOT NULL DEFAULT '' CHECK (length(ziel) <= 2000),
    zielgruppe   text NOT NULL CHECK (zielgruppe IN ('sales', 'oeffentlich', 'verteiler')),
    filter       jsonb NOT NULL DEFAULT '{}'::jsonb,
    von          date NOT NULL,
    bis          date NOT NULL,
    status       text NOT NULL DEFAULT 'in_planung'
                 CHECK (status IN ('in_planung', 'geplant', 'laeuft', 'abgeschlossen', 'abgesagt')),
    angelegt_von text NOT NULL DEFAULT 'betreiber',
    erstellt_am  timestamptz NOT NULL DEFAULT now(),
    geaendert_am timestamptz NOT NULL DEFAULT now(),
    CHECK (bis >= von)
);
CREATE INDEX IF NOT EXISTS kampagnen_mandant_idx ON marketing.kampagnen (mandant, von);
ALTER TABLE marketing.kampagnen_plan
  ADD COLUMN IF NOT EXISTS kampagne uuid REFERENCES marketing.kampagnen(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS kampagnen_plan_kampagne_idx ON marketing.kampagnen_plan (kampagne);

CREATE OR REPLACE FUNCTION marketing.kampagne_anlegen(p_mandant text, p_name text, p_ziel text,
    p_zielgruppe text, p_filter jsonb, p_von date, p_bis date, p_wer text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  IF length(btrim(coalesce(p_wer, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen keine Kampagne'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  IF p_zielgruppe NOT IN ('sales', 'oeffentlich', 'verteiler') THEN
    RAISE EXCEPTION 'Zielgruppe muss sales, oeffentlich oder verteiler sein'; END IF;
  IF p_bis < p_von THEN RAISE EXCEPTION 'Das Ende liegt vor dem Beginn'; END IF;
  IF p_bis < current_date THEN RAISE EXCEPTION 'Der Zeitraum liegt in der Vergangenheit'; END IF;
  INSERT INTO marketing.kampagnen (mandant, name, ziel, zielgruppe, filter, von, bis, angelegt_von)
  VALUES (p_mandant, btrim(p_name), coalesce(p_ziel, ''), p_zielgruppe, coalesce(p_filter, '{}'::jsonb),
          p_von, p_bis, btrim(p_wer))
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Status: der Arbeiter setzt in_planung -> geplant; der Betreiber laeuft/abgeschlossen/abgesagt,
-- und in_planung erneut (Agent soll neu planen).
CREATE OR REPLACE FUNCTION marketing.kampagne_status(p_id uuid, p_status text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  IF p_status NOT IN ('in_planung', 'geplant', 'laeuft', 'abgeschlossen', 'abgesagt') THEN
    RAISE EXCEPTION 'Unbekannter Status'; END IF;
  UPDATE marketing.kampagnen SET status = p_status, geaendert_am = now() WHERE id = p_id
  RETURNING status INTO v;
  IF v IS NULL THEN RAISE EXCEPTION 'Unbekannte Kampagne'; END IF;
  RETURN v;
END $$;

-- Beitrag zu einer Kampagne: wie plan_anlegen (Agent -> vorschlag), dazu der Verweis. Das Datum muss
-- im Zeitraum der Kampagne liegen, die Firma ist die der Kampagne.
CREATE OR REPLACE FUNCTION marketing.kampagne_beitrag(p_kampagne uuid, p_datum date, p_art text,
    p_titel text, p_notiz text, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE k marketing.kampagnen; v uuid;
BEGIN
  SELECT * INTO k FROM marketing.kampagnen WHERE id = p_kampagne;
  IF k.id IS NULL THEN RAISE EXCEPTION 'Unbekannte Kampagne'; END IF;
  IF p_datum < k.von OR p_datum > k.bis THEN
    RAISE EXCEPTION 'Datum liegt ausserhalb der Kampagne (% bis %)', k.von, k.bis; END IF;
  v := marketing.plan_anlegen(k.mandant, p_datum, p_art, p_titel, p_notiz, NULL, p_von);
  UPDATE marketing.kampagnen_plan SET kampagne = k.id WHERE id = v;
  RETURN v;
END $$;

-- 094 erweitert: Kampagnen der Marke mit loeschen (sonst Waisen im replica-Modus).
CREATE OR REPLACE FUNCTION marketing.pult_mandant_loeschen(p_mandant text, p_bestaetigung text, p_von text)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  m marketing.mandanten;
  n jsonb := '{}'::jsonb;
  k int;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Loeschen'; END IF;
  IF p_bestaetigung IS DISTINCT FROM p_mandant THEN
    RAISE EXCEPTION 'Bestaetigung stimmt nicht - die Kennung der Marke woertlich eingeben';
  END IF;
  SELECT * INTO m FROM marketing.mandanten WHERE id = p_mandant FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte Firma'; END IF;
  IF coalesce(cardinality(m.verteiler), 0) > 0 THEN
    RAISE EXCEPTION 'Marke versendet ueber einen Laden (Verteiler %) - erst den Verteiler leeren', m.verteiler;
  END IF;
  IF EXISTS (SELECT 1 FROM marketing.bild_auftraege b JOIN marketing.inhalte i ON i.id = b.inhalt
              WHERE i.mandant = p_mandant AND b.status = 'in_arbeit')
     OR EXISTS (SELECT 1 FROM marketing.chat_auftraege c JOIN marketing.inhalte i ON i.id = c.inhalt
              WHERE i.mandant = p_mandant AND c.status = 'in_arbeit')
     OR EXISTS (SELECT 1 FROM marketing.ton_auftraege WHERE mandant = p_mandant AND status = 'in_arbeit')
     OR EXISTS (SELECT 1 FROM marketing.video_auftraege WHERE mandant = p_mandant AND status IN ('in_arbeit', 'bilder_laufen'))
     OR EXISTS (SELECT 1 FROM marketing.marken_auftraege WHERE mandant = p_mandant AND status = 'in_arbeit') THEN
    RAISE EXCEPTION 'Ein Auftrag dieser Marke laeuft gerade - danach erneut versuchen';
  END IF;

  PERFORM set_config('session_replication_role', 'replica', true);

  DELETE FROM marketing.seite_mailfassungen s USING marketing.inhalte i
   WHERE s.inhalt = i.id AND i.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('seite_mailfassungen', k);
  DELETE FROM marketing.inhalt_fassungen f USING marketing.inhalte i
   WHERE f.inhalt = i.id AND i.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('inhalt_fassungen', k);
  DELETE FROM marketing.rueckmeldungen r USING marketing.inhalte i
   WHERE r.inhalt = i.id AND i.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('rueckmeldungen', k);
  DELETE FROM marketing.bild_auftraege b USING marketing.inhalte i
   WHERE b.inhalt = i.id AND i.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('bild_auftraege', k);
  DELETE FROM marketing.chat_auftraege c USING marketing.inhalte i
   WHERE c.inhalt = i.id AND i.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('chat_auftraege', k);
  DELETE FROM marketing.kampagnen_plan WHERE mandant = p_mandant
     OR inhalt IN (SELECT id FROM marketing.inhalte WHERE mandant = p_mandant);
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('kampagnen_plan', k);
  DELETE FROM marketing.kampagnen WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('kampagnen', k);
  DELETE FROM marketing.inhalte WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('inhalte', k);

  DELETE FROM marketing.layout_fassungen f USING marketing.layout_vorlagen l
   WHERE f.layout = l.name AND l.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('layout_fassungen', k);
  DELETE FROM marketing.layout_vorlagen WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('layout_vorlagen', k);
  DELETE FROM marketing.newsletter_vorlagen_fassungen f USING marketing.newsletter_vorlagen v
   WHERE f.vorlage = v.name AND v.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('newsletter_vorlagen_fassungen', k);
  DELETE FROM marketing.newsletter_vorlagen WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('newsletter_vorlagen', k);

  DELETE FROM marketing.marken_vorschlaege WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('marken_vorschlaege', k);
  DELETE FROM marketing.marken_auftraege WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('marken_auftraege', k);
  DELETE FROM marketing.marken_spiegel WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('marken_spiegel', k);

  DELETE FROM marketing.ton_auftraege WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('ton_auftraege', k);
  DELETE FROM marketing.reel_bilder r USING marketing.video_auftraege v
   WHERE r.video_auftrag = v.id AND v.mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('reel_bilder', k);
  UPDATE marketing.video_auftraege SET vorgaenger = NULL
   WHERE vorgaenger IN (SELECT id FROM marketing.video_auftraege WHERE mandant = p_mandant)
     AND mandant IS DISTINCT FROM p_mandant;
  DELETE FROM marketing.video_auftraege WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('video_auftraege', k);
  DELETE FROM marketing.medien_mandant WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('medien_mandant', k);

  DELETE FROM marketing.mandanten WHERE id = p_mandant;
  PERFORM set_config('session_replication_role', 'origin', true);
  RETURN n || jsonb_build_object('mandant', p_mandant, 'von', p_von);
END $$;

COMMIT;
