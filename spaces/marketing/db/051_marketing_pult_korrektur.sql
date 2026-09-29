-- 051_marketing_pult_korrektur.sql — Fix-Runde 1 auf 050 (task-1-fix1-review,
-- 2026-09-29). 050 ist bereits produktiv angewendet; diese Datei AENDERT NICHT,
-- was 050 bereits geschrieben hat, sondern korrigiert falsche Werte und
-- schliesst Luecken. Additiv, idempotent, in einer Transaktion.
--
-- Behobene Befunde:
--   I1 — art ignorierte den Kanal (26 von 30 uebernommenen Proposals waren
--        linkedin/telegram/discord, aber alle als 'newsletter' getypt; 10
--        Fassungen hatten betreff=''). Fix: art aus channel (email->
--        newsletter, sonst->post); pult_fassung_speichern verlangt betreff
--        nur noch fuer art='newsletter', dafuer jetzt bei JEDER Art
--        mindestens einen Abschnitt mit nicht-leerem Text.
--   I2 — der Proposal-Status ging beim Uebernehmen verloren (18 rejected, 1
--        sent, 1 approved standen als offene Entwuerfe da). Fix: Korrektur
--        auf abgelehnt/freigegeben/entwurf, nur fuer Zeilen, die noch auf
--        'entwurf' stehen (eine im Pult getroffene Entscheidung wird nie
--        ueberschrieben).
--   Bruecke — bis Stufe 4 eine eigene UI hat, muss jeder NEUE broadcast_
--        proposal automatisch als Inhalt + Fassung 1 erscheinen. AFTER-
--        INSERT-Trigger auf broadcast_proposals, dieselbe Abbildung wie I1/I2
--        in EINER Funktion (marketing._inhalt_abbildung_aus_proposal), damit
--        Korrektur und Trigger nicht zwei Kopien derselben Regeln pflegen.
--   I3 — Freigeben nahm die zum KLICK-Zeitpunkt neueste Fassung, nicht die,
--        die der Mensch tatsaechlich gesehen hat. Fix: pult_entscheiden
--        bekommt einen Pflicht-Parameter p_fassung und weist eine veraltete
--        Fassung zurueck.
--   I4 — die drei 044-Layout-Schreibwege (layout_vorschlagen, layout_
--        entscheiden, Handarbeit) liefen an der Fassungs-/Mandant-Zuordnung
--        aus 050 vorbei. Fix: ein Trigger-Paar auf layout_vorlagen (nur fuer
--        art='layout'), das bei jeder Gestalt-Aenderung automatisch die
--        naechste Fassung zieht und die passende layout_fassungen-Zeile
--        anlegt — unabhaengig vom Schreibweg.
--   I5 — pult_layout_speichern aenderte eine freigegebene Vorlage still im
--        Vorbeigehen. Fix: ein Speichern durch den Betreiber IST die
--        Freigabe (status='freigegeben', entschieden_von/-am gesetzt).
--   Minor — die rundung-Pruefung in pult_gestalt_fehler konnte bei einem
--        String wie "abc" eine echte Cast-Exception werfen statt einen
--        deutschen Satz zurueckzugeben (::numeric wird in einer einzelnen
--        OR-Bedingung nicht zuverlaessig kurzgeschlossen). Fix: CASE auf
--        jsonb_typeof VOR jedem Cast.
BEGIN;

-- ---------------------------------------------------------------------------
-- Bruecke: EINE Abbildungsfunktion fuer "broadcast_proposal -> Inhalt", von
-- der Korrektur-UPDATE unten UND vom neuen AFTER-INSERT-Trigger benutzt.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION marketing._inhalt_abbildung_aus_proposal(p marketing.broadcast_proposals)
RETURNS TABLE(art text, status text, titel text, freigegebene_fassung int,
              entschieden_von text, entschieden_am timestamptz, grund text)
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  art   := CASE WHEN p.channel = 'email' THEN 'newsletter' ELSE 'post' END;
  titel := coalesce(nullif(btrim(p.draft_subject), ''), left(p.draft_body_text, 60));
  freigegebene_fassung := NULL;
  entschieden_von := NULL;
  entschieden_am  := NULL;
  grund := NULL;
  IF p.status = 'rejected' THEN
    status := 'abgelehnt';
    entschieden_von := coalesce(p.rejected_by, 'bestand');
    entschieden_am  := coalesce(p.rejected_at, p.created_at);
    grund := p.rejection_reason;
  ELSIF p.status IN ('approved', 'sent') THEN
    status := 'freigegeben';
    freigegebene_fassung := 1;
    entschieden_von := coalesce(p.approved_by, 'bestand');
    entschieden_am  := coalesce(p.approved_at, p.sent_at, p.created_at);
  ELSE
    status := 'entwurf';
  END IF;
  RETURN NEXT;
END $$;

-- I1 + I2: bestehende, aus 050 uebernommene Zeilen korrigieren. art wird
-- immer aus dem Kanal neu gesetzt (art ist kein Entscheidungsfeld). Status
-- (und was daran haengt) nur, solange der Inhalt noch 'entwurf' ist - eine
-- im Pult getroffene Entscheidung bleibt unangetastet. Die WHERE-Klausel
-- sorgt dafuer, dass ein zweiter Lauf keine Zeile mehr anfasst (Idempotenz).
UPDATE marketing.inhalte i
   SET art = m.art,
       status = CASE WHEN i.status = 'entwurf' THEN m.status ELSE i.status END,
       freigegebene_fassung = CASE WHEN i.status = 'entwurf' THEN m.freigegebene_fassung ELSE i.freigegebene_fassung END,
       entschieden_von = CASE WHEN i.status = 'entwurf' THEN m.entschieden_von ELSE i.entschieden_von END,
       entschieden_am  = CASE WHEN i.status = 'entwurf' THEN m.entschieden_am  ELSE i.entschieden_am  END,
       grund = CASE WHEN i.status = 'entwurf' THEN m.grund ELSE i.grund END
  FROM marketing.broadcast_proposals p,
       LATERAL marketing._inhalt_abbildung_aus_proposal(p) m
 WHERE p.id = i.herkunft_proposal
   AND (i.art IS DISTINCT FROM m.art
        OR (i.status = 'entwurf' AND m.status <> 'entwurf'));

-- I1: Fassung 1 eines jetzt noch als 'newsletter' geltenden, uebernommenen
-- Inhalts bekommt einen Betreff, falls er leer war (betrifft nach obiger
-- Korrektur praktisch keine Zeile mehr, da alle mit leerem Betreff nicht-
-- email-Kanaele waren und jetzt 'post' sind - die Regel bleibt trotzdem
-- stehen, falls kuenftig doch einmal eine email-Zeile ohne Betreff auftaucht).
-- Fassungen sind per Trigger unveraenderlich; hier gezielt und nur fuer diese
-- Korrektur abschalten, danach sofort wieder einschalten (auch bei einem
-- zweiten Lauf, der nichts mehr aendert).
ALTER TABLE marketing.inhalt_fassungen DISABLE TRIGGER trg_inhalt_fassung_unveraenderlich;

UPDATE marketing.inhalt_fassungen f
   SET felder = jsonb_set(f.felder, '{betreff}', to_jsonb(i.titel))
  FROM marketing.inhalte i
 WHERE f.inhalt = i.id AND f.fassung = 1
   AND i.herkunft_proposal IS NOT NULL
   AND i.art = 'newsletter'
   AND coalesce(f.felder->>'betreff', '') = '';

ALTER TABLE marketing.inhalt_fassungen ENABLE TRIGGER trg_inhalt_fassung_unveraenderlich;

-- Bruecke: AFTER-INSERT-Trigger auf broadcast_proposals, dieselbe Abbildung.
CREATE OR REPLACE FUNCTION marketing._broadcast_proposal_zu_inhalt() RETURNS trigger AS $$
DECLARE m record; v_id uuid; v_layout_fassung int;
BEGIN
  SELECT * INTO m FROM marketing._inhalt_abbildung_aus_proposal(NEW);
  INSERT INTO marketing.inhalte (mandant, art, titel, status, freigegebene_fassung,
         entschieden_von, entschieden_am, grund, herkunft_proposal, erstellt_am)
  VALUES ('vibemind', m.art, m.titel, m.status, m.freigegebene_fassung,
          m.entschieden_von, m.entschieden_am, m.grund, NEW.id, NEW.created_at)
  ON CONFLICT (herkunft_proposal) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NOT NULL THEN
    SELECT fassung INTO v_layout_fassung FROM marketing.layout_vorlagen WHERE name = 'dunkel';
    INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, layout_fassung, urheber, erstellt_am)
    VALUES (v_id, 1,
            jsonb_build_object(
              'betreff', m.titel, 'vorschautext', '',
              'abschnitte', jsonb_build_array(jsonb_build_object('titel', '', 'text', NEW.draft_body_text)),
              'knopf_text', '', 'knopf_link', ''),
            'dunkel', v_layout_fassung, 'agent', NEW.created_at)
    ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_broadcast_proposal_zu_inhalt ON marketing.broadcast_proposals;
CREATE TRIGGER trg_broadcast_proposal_zu_inhalt
  AFTER INSERT ON marketing.broadcast_proposals
  FOR EACH ROW EXECUTE FUNCTION marketing._broadcast_proposal_zu_inhalt();

-- ---------------------------------------------------------------------------
-- I3: freigeben/ablehnen braucht die Fassung, die tatsaechlich angesehen
-- wurde - eine inzwischen neuere Fassung (vom Agenten nachgereicht) wird
-- zurueckgewiesen statt stillschweigend freigegeben.
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS marketing.pult_entscheiden(uuid, text, text, text);

CREATE OR REPLACE FUNCTION marketing.pult_entscheiden(
    p_inhalt uuid, p_fassung int, p_urteil text, p_von text, p_grund text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE v_status text; v_neueste int;
BEGIN
  IF p_urteil NOT IN ('freigeben','ablehnen') THEN
    RAISE EXCEPTION 'Urteil muss freigeben oder ablehnen sein'; END IF;
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Namen kein Urteil'; END IF;
  SELECT status INTO v_status FROM marketing.inhalte WHERE id = p_inhalt FOR UPDATE;
  IF v_status IS DISTINCT FROM 'entwurf' THEN
    RAISE EXCEPTION 'Schon entschieden (%)', coalesce(v_status, 'unbekannt'); END IF;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = p_inhalt;
  IF v_neueste IS NULL THEN
    RAISE EXCEPTION 'Ohne Fassung gibt es nichts freizugeben'; END IF;
  IF p_fassung <> v_neueste THEN
    RAISE EXCEPTION 'Inzwischen gibt es Fassung % - bitte erst ansehen', v_neueste; END IF;
  UPDATE marketing.inhalte
     SET status = CASE p_urteil WHEN 'freigeben' THEN 'freigegeben' ELSE 'abgelehnt' END,
         freigegebene_fassung = CASE p_urteil WHEN 'freigeben' THEN p_fassung END,
         entschieden_von = p_von, entschieden_am = now(), grund = p_grund
   WHERE id = p_inhalt
  RETURNING status INTO v_status;
  RETURN v_status;
END $$;

-- ---------------------------------------------------------------------------
-- I1 (Funktion): betreff nur noch fuer art='newsletter' Pflicht; JEDE Art
-- braucht mindestens einen Abschnitt mit nicht-leerem Text (vorher liess ein
-- Abschnitt mit text='' durch, solange das Array nicht leer war).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION marketing.pult_fassung_speichern(
    p_inhalt uuid, p_felder jsonb, p_layout text, p_urheber text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_n int; v_art text;
BEGIN
  IF jsonb_typeof(p_felder) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'Felder muessen ein JSON-Objekt sein'; END IF;
  SELECT art INTO v_art FROM marketing.inhalte WHERE id = p_inhalt;
  IF v_art IS NULL THEN
    RAISE EXCEPTION 'Inhalt % gibt es nicht', p_inhalt; END IF;
  IF v_art = 'newsletter' AND length(btrim(coalesce(p_felder->>'betreff', ''))) = 0 THEN
    RAISE EXCEPTION 'Ohne Betreff gibt es keine Fassung'; END IF;
  IF jsonb_typeof(p_felder->'abschnitte') IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_felder->'abschnitte') = 0 THEN
    RAISE EXCEPTION 'Mindestens ein Abschnitt ist noetig'; END IF;
  IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_felder->'abschnitte') a
                  WHERE length(btrim(coalesce(a->>'text', ''))) > 0) THEN
    RAISE EXCEPTION 'Mindestens ein Abschnitt braucht Text'; END IF;
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

-- ---------------------------------------------------------------------------
-- I4: die drei 044-Schreibwege auf layout_vorlagen (layout_vorschlagen,
-- layout_entscheiden, Handarbeit) liefen an 050s Fassungs-/Mandant-Logik
-- vorbei. Statt jeden Schreibweg einzeln zu aendern: ein Trigger-Paar, das
-- fuer JEDE Gestalt-Aenderung einer art='layout'-Zeile automatisch greift -
-- unabhaengig davon, welche Funktion geschrieben hat.
--
-- mandant bekommt einen einfachen Spalten-Default (gilt fuer jede neue
-- Zeile, auch art='formular' - das ist harmlos, weil 045/046 mandant nirgends
-- lesen oder pruefen). inhaltsart NICHT per Spalten-Default, weil ein reiner
-- Default fuer JEDE neue Zeile gelten wuerde, auch fuer Formular-Zeilen aus
-- vorlagenauftrag_vorlegen (045/046) - die duerfen inhaltsart nicht bekommen.
-- Deshalb ein BEFORE-Trigger, der inhaltsart nur setzt, wenn art='layout'.
-- ---------------------------------------------------------------------------
ALTER TABLE marketing.layout_vorlagen ALTER COLUMN mandant SET DEFAULT 'vibemind';

CREATE OR REPLACE FUNCTION marketing._layout_vorlagen_vor_schreiben() RETURNS trigger AS $$
BEGIN
  IF NEW.inhaltsart IS NULL THEN
    NEW.inhaltsart := 'newsletter';
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.fassung := coalesce((SELECT max(fassung) FROM marketing.layout_fassungen WHERE layout = NEW.name), 0) + 1;
  ELSIF NEW.gestalt IS DISTINCT FROM OLD.gestalt THEN
    NEW.fassung := coalesce((SELECT max(fassung) FROM marketing.layout_fassungen WHERE layout = NEW.name), OLD.fassung) + 1;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_layout_vorlagen_vor_schreiben ON marketing.layout_vorlagen;
CREATE TRIGGER trg_layout_vorlagen_vor_schreiben
  BEFORE INSERT OR UPDATE ON marketing.layout_vorlagen
  FOR EACH ROW WHEN (NEW.art = 'layout')
  EXECUTE FUNCTION marketing._layout_vorlagen_vor_schreiben();

-- Zweiter Trigger, AFTER: legt die layout_fassungen-Zeile mit der von obigem
-- BEFORE-Trigger bereits gesetzten Fassungsnummer an. Muss AFTER sein, weil
-- layout_fassungen.layout auf layout_vorlagen.name verweist - beim INSERT
-- steht die Zeile in layout_vorlagen vor dem BEFORE-Trigger noch nicht.
-- erstellt_von: beim INSERT vorgeschlagen_von (Muster: layout_vorschlagen);
-- beim UPDATE die transaktionslokale Einstellung marketing.layout_von, falls
-- gesetzt (Muster: pult_layout_speichern setzt sie), sonst 'unbekannt'
-- (Muster: layout_entscheiden/Handarbeit setzen sie nicht).
CREATE OR REPLACE FUNCTION marketing._layout_vorlagen_fassung_aufzeichnen() RETURNS trigger AS $$
DECLARE v_von text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_von := coalesce(NEW.vorgeschlagen_von, 'unbekannt');
    INSERT INTO marketing.layout_fassungen (layout, fassung, gestalt, erstellt_von)
    VALUES (NEW.name, NEW.fassung, NEW.gestalt, v_von)
    ON CONFLICT DO NOTHING;
  ELSIF NEW.gestalt IS DISTINCT FROM OLD.gestalt THEN
    v_von := coalesce(nullif(current_setting('marketing.layout_von', true), ''), 'unbekannt');
    INSERT INTO marketing.layout_fassungen (layout, fassung, gestalt, erstellt_von)
    VALUES (NEW.name, NEW.fassung, NEW.gestalt, v_von)
    ON CONFLICT DO NOTHING;
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_layout_vorlagen_fassung_aufzeichnen ON marketing.layout_vorlagen;
CREATE TRIGGER trg_layout_vorlagen_fassung_aufzeichnen
  AFTER INSERT OR UPDATE ON marketing.layout_vorlagen
  FOR EACH ROW WHEN (NEW.art = 'layout')
  EXECUTE FUNCTION marketing._layout_vorlagen_fassung_aufzeichnen();

-- ---------------------------------------------------------------------------
-- I5 + I4 (Funktion): ein Speichern durch den Betreiber IST die Freigabe.
-- Keine manuelle layout_fassungen-Insert mehr - das erledigen jetzt die zwei
-- Trigger oben fuer JEDEN Schreibweg gleich (keine doppelten Zeilen mehr).
--
-- Abweichung vom Wortlaut der Weisung: "muster_datei=NULL" ist mit der
-- Spalte selbst nicht moeglich - marketing.layout_vorlagen.muster_datei ist
-- NOT NULL DEFAULT '' (044, live per \d bestaetigt). Deshalb hier ''
-- statt NULL; das erfuellt denselben Zweck (kein Bezug mehr auf ein
-- Muster-Foto, weil kein Foto-Weg zu dieser Freigabe fuehrte).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION marketing.pult_layout_speichern(
    p_name text, p_gestalt jsonb, p_von text) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_f text; v_n int;
BEGIN
  v_f := marketing.pult_gestalt_fehler(p_gestalt);
  IF v_f IS NOT NULL THEN RAISE EXCEPTION 'Layout ungueltig: %', v_f; END IF;
  PERFORM 1 FROM marketing.layout_vorlagen WHERE name = p_name AND art = 'layout' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Layout % gibt es nicht', p_name; END IF;
  PERFORM set_config('marketing.layout_von', p_von, true);
  UPDATE marketing.layout_vorlagen
     SET gestalt = p_gestalt, status = 'freigegeben',
         entschieden_von = p_von, entschieden_am = now(), muster_datei = ''
   WHERE name = p_name
  RETURNING fassung INTO v_n;
  PERFORM set_config('marketing.layout_von', '', true);
  RETURN v_n;
END $$;

-- ---------------------------------------------------------------------------
-- Minor: rundung-Pruefung ueber CASE auf jsonb_typeof, damit ein String wie
-- "abc" den ::numeric-Cast nie erreicht (eine einzelne OR-Bedingung
-- garantiert in Postgres keine Auswertungsreihenfolge - vorher konnte das
-- eine rohe Cast-Exception statt eines deutschen Satzes werfen).
-- ---------------------------------------------------------------------------
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
  IF p ? 'rundung' THEN
    CASE jsonb_typeof(p->'rundung')
      WHEN 'number' THEN
        IF (p->>'rundung')::numeric < 0 OR (p->>'rundung')::numeric > 24 THEN
          RETURN 'rundung muss eine Zahl von 0 bis 24 sein';
        END IF;
      ELSE
        RETURN 'rundung muss eine Zahl von 0 bis 24 sein';
    END CASE;
  END IF;
  IF p ? 'kopf_text' AND length(p->>'kopf_text') > 120 THEN
    RETURN 'kopf_text hoechstens 120 Zeichen'; END IF;
  IF p ? 'fuss_text' AND length(p->>'fuss_text') > 300 THEN
    RETURN 'fuss_text hoechstens 300 Zeichen'; END IF;
  IF p ? 'logo' AND (p->>'logo' !~ '^data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$'
                     OR length(p->>'logo') > 204800) THEN
    RETURN 'logo muss ein PNG/JPEG unter 150 KB sein'; END IF;
  RETURN NULL;
END $$;

COMMIT;
