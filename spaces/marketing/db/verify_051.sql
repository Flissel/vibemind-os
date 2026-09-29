-- verify_051.sql — Proben fuer 051_marketing_pult_korrektur.sql (Fix-Runde 1:
-- I1 art-aus-Kanal, I2 Proposal-Status uebernehmen, Bruecke fuer neue
-- broadcast_proposals, I3 pult_entscheiden mit Fassung, I4 Layout-Fassungen
-- fuer alle Schreibwege, I5 Speichern=Freigabe, Minor rundung-Cast). Aendert
-- nichts (ROLLBACK).
BEGIN;

-- I1: art aus dem Kanal, keine leeren Betreffs mehr bei Newslettern.
DO $$
DECLARE v_falsch int;
BEGIN
  SELECT count(*) INTO v_falsch FROM marketing.inhalte i
    JOIN marketing.broadcast_proposals p ON p.id = i.herkunft_proposal
   WHERE i.art <> CASE WHEN p.channel = 'email' THEN 'newsletter' ELSE 'post' END;
  IF v_falsch <> 0 THEN
    RAISE EXCEPTION 'PROBE: % Inhalte mit falscher Art (Kanal passt nicht)', v_falsch; END IF;

  IF EXISTS (
    SELECT 1 FROM marketing.inhalte i
    JOIN marketing.inhalt_fassungen f ON f.inhalt = i.id AND f.fassung = 1
   WHERE i.art = 'newsletter' AND coalesce(f.felder->>'betreff', '') = ''
  ) THEN
    RAISE EXCEPTION 'PROBE: Newsletter mit leerem Betreff'; END IF;
END $$;

-- I2: Proposal-Status wortgleich uebernommen (abgelehnt/freigegeben/entwurf),
-- inklusive Akteur, Zeitpunkt, Grund und freigegebene_fassung.
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT i.id, i.status, i.entschieden_von, i.entschieden_am, i.grund, i.freigegebene_fassung,
           p.status AS p_status, p.rejected_by, p.rejected_at, p.rejection_reason,
           p.approved_by, p.approved_at, p.sent_at, p.created_at
      FROM marketing.inhalte i JOIN marketing.broadcast_proposals p ON p.id = i.herkunft_proposal
  LOOP
    IF r.p_status = 'rejected' THEN
      IF r.status <> 'abgelehnt'
         OR r.entschieden_von <> coalesce(r.rejected_by, 'bestand')
         OR r.entschieden_am <> coalesce(r.rejected_at, r.created_at)
         OR r.grund IS DISTINCT FROM r.rejection_reason THEN
        RAISE EXCEPTION 'PROBE: abgelehnter Proposal % falsch uebernommen', r.id; END IF;
    ELSIF r.p_status IN ('approved', 'sent') THEN
      IF r.status <> 'freigegeben' OR r.freigegebene_fassung <> 1
         OR r.entschieden_von <> coalesce(r.approved_by, 'bestand')
         OR r.entschieden_am <> coalesce(r.approved_at, r.sent_at, r.created_at) THEN
        RAISE EXCEPTION 'PROBE: freigegebener Proposal % falsch uebernommen', r.id; END IF;
    ELSE
      IF r.status <> 'entwurf' THEN
        RAISE EXCEPTION 'PROBE: offener Proposal % faelschlich entschieden (%)', r.id, r.status; END IF;
    END IF;
  END LOOP;
END $$;

-- Bruecke: ein NEUER broadcast_proposal erscheint automatisch als Inhalt +
-- Fassung 1, mit derselben Abbildung wie die Korrektur oben.
DO $$
DECLARE v_p uuid; v_i record;
BEGIN
  INSERT INTO marketing.broadcast_proposals
    (channel, status, draft_subject, draft_body_text, created_by,
     rejected_by, rejected_at, rejection_reason)
  VALUES ('linkedin', 'rejected', 'Testbetreff-Bruecke', 'Testinhalt fuer die Bruecke (verify_051)',
          'verify_051', 'pruefer', now(), 'Testgrund')
  RETURNING id INTO v_p;

  SELECT i.art, i.status, i.entschieden_von, i.grund, i.mandant,
         f.fassung, f.felder->>'betreff' AS betreff, f.layout, f.urheber
    INTO v_i
    FROM marketing.inhalte i JOIN marketing.inhalt_fassungen f ON f.inhalt = i.id AND f.fassung = 1
   WHERE i.herkunft_proposal = v_p;

  IF v_i.art IS NULL THEN RAISE EXCEPTION 'PROBE: Bruecke hat keinen Inhalt angelegt'; END IF;
  IF v_i.mandant <> 'vibemind' THEN RAISE EXCEPTION 'PROBE: Bruecke-Mandant falsch: %', v_i.mandant; END IF;
  IF v_i.art <> 'post' THEN RAISE EXCEPTION 'PROBE: Bruecke-Art falsch (linkedin sollte post sein): %', v_i.art; END IF;
  IF v_i.status <> 'abgelehnt' THEN RAISE EXCEPTION 'PROBE: Bruecke-Status falsch: %', v_i.status; END IF;
  IF v_i.entschieden_von <> 'pruefer' OR v_i.grund <> 'Testgrund' THEN
    RAISE EXCEPTION 'PROBE: Bruecke uebernahm Entscheidung nicht korrekt'; END IF;
  IF v_i.betreff <> 'Testbetreff-Bruecke' THEN
    RAISE EXCEPTION 'PROBE: Bruecke-Betreff falsch: %', v_i.betreff; END IF;
  IF v_i.layout <> 'dunkel' OR v_i.urheber <> 'agent' OR v_i.fassung <> 1 THEN
    RAISE EXCEPTION 'PROBE: Bruecke-Fassung 1 nicht wie erwartet (layout=%, urheber=%, fassung=%)',
      v_i.layout, v_i.urheber, v_i.fassung; END IF;
END $$;

-- I3: freigeben/ablehnen braucht die tatsaechlich gesehene Fassung.
DO $$
DECLARE v_i uuid; v_n int; v_s text; v_neueste int;
BEGIN
  -- Ohne jede Fassung gibt es nichts freizugeben.
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'post', 'Ohne Fassung (verify_051)')
  RETURNING id INTO v_i;
  BEGIN
    PERFORM marketing.pult_entscheiden(v_i, 1, 'freigeben', 'felix', NULL);
    RAISE EXCEPTION 'PROBE: Entscheidung ohne jede Fassung ging durch';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM NOT LIKE '%Ohne Fassung%' THEN
      RAISE EXCEPTION 'PROBE: falscher Grund ohne Fassung: %', SQLERRM; END IF;
  END;

  -- Eine veraltete Fassungsnummer wird zurueckgewiesen.
  SELECT id INTO v_i FROM marketing.inhalte WHERE status = 'entwurf' AND herkunft_proposal IS NOT NULL LIMIT 1;
  SELECT max(fassung) INTO v_neueste FROM marketing.inhalt_fassungen WHERE inhalt = v_i;
  v_n := marketing.pult_fassung_speichern(v_i, '{"betreff":"neu","abschnitte":[{"titel":"","text":"y"}]}', 'dunkel', 'betreiber');
  IF v_n <> v_neueste + 1 THEN RAISE EXCEPTION 'PROBE: neue Fassung nicht wie erwartet'; END IF;
  BEGIN
    PERFORM marketing.pult_entscheiden(v_i, v_neueste, 'freigeben', 'felix', NULL);
    RAISE EXCEPTION 'PROBE: veraltete Fassung ging durch';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    IF SQLERRM NOT LIKE '%Inzwischen gibt es Fassung%' THEN
      RAISE EXCEPTION 'PROBE: falscher Grund bei veralteter Fassung: %', SQLERRM; END IF;
  END;
  v_s := marketing.pult_entscheiden(v_i, v_n, 'freigeben', 'felix', NULL);
  IF v_s <> 'freigegeben' OR (SELECT freigegebene_fassung FROM marketing.inhalte WHERE id = v_i) <> v_n THEN
    RAISE EXCEPTION 'PROBE: Freigabe mit der richtigen Fassung schlug fehl'; END IF;
END $$;

-- pult_fassung_speichern: betreff nur fuer newsletter Pflicht; jede Art
-- braucht mindestens einen Abschnitt mit nicht-leerem Text.
DO $$
DECLARE v_post uuid; v_news uuid;
BEGIN
  SELECT id INTO v_post FROM marketing.inhalte WHERE art = 'post' AND status = 'entwurf' LIMIT 1;
  IF v_post IS NULL THEN RAISE EXCEPTION 'PROBE: kein art=post Inhalt fuer den Test vorhanden'; END IF;
  -- Post ohne Betreff ist erlaubt, solange ein Abschnitt Text hat.
  PERFORM marketing.pult_fassung_speichern(v_post, '{"abschnitte":[{"titel":"","text":"Inhalt ohne Betreff"}]}', 'dunkel', 'betreiber');

  SELECT id INTO v_news FROM marketing.inhalte WHERE art = 'newsletter' AND status = 'entwurf' LIMIT 1;
  IF v_news IS NOT NULL THEN
    BEGIN
      PERFORM marketing.pult_fassung_speichern(v_news, '{"abschnitte":[{"titel":"","text":"y"}]}', 'dunkel', 'betreiber');
      RAISE EXCEPTION 'PROBE: Newsletter ohne Betreff ging durch';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
  END IF;

  BEGIN
    PERFORM marketing.pult_fassung_speichern(v_post, '{"betreff":"x","abschnitte":[{"titel":"leer","text":""}]}', 'dunkel', 'betreiber');
    RAISE EXCEPTION 'PROBE: Abschnitt ohne Text ging durch';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  END;
END $$;

-- I4: layout_vorschlagen zieht Mandant/Inhaltsart/Fassung automatisch, fuer
-- JEDEN Schreibweg (hier ueber layout_vorschlagen, nicht ueber pult_*).
DO $$
DECLARE v_res jsonb;
BEGIN
  v_res := marketing.layout_vorschlagen('testfarbe-051', 'Testfarbe fuer verify_051',
    '{"grund":"#000000","flaeche":"#111111","akzent":"#5eead4","gold":"#fbbf24",
      "text":"#ffffff","text_hell":"#ffffff","text_leise":"#999999","handlung_text":"#000000"}'::jsonb,
    'pruefer');
  IF NOT coalesce((v_res->>'ok')::boolean, false) THEN
    RAISE EXCEPTION 'PROBE: layout_vorschlagen scheiterte: %', v_res->>'grund'; END IF;
  IF (SELECT mandant FROM marketing.layout_vorlagen WHERE name = 'testfarbe-051') <> 'vibemind'
     OR (SELECT inhaltsart FROM marketing.layout_vorlagen WHERE name = 'testfarbe-051') <> 'newsletter'
     OR (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'testfarbe-051') <> 1 THEN
    RAISE EXCEPTION 'PROBE: neuer Layout-Vorschlag ohne Mandant vibemind / Inhaltsart newsletter / Fassung 1'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.layout_fassungen WHERE layout = 'testfarbe-051' AND fassung = 1) THEN
    RAISE EXCEPTION 'PROBE: keine layout_fassungen-Zeile fuer Fassung 1'; END IF;

  v_res := marketing.layout_vorschlagen('testfarbe-051', 'Testfarbe fuer verify_051',
    '{"grund":"#000000","flaeche":"#111111","akzent":"#5eead4","gold":"#fbbf24",
      "text":"#eeeeee","text_hell":"#ffffff","text_leise":"#999999","handlung_text":"#000000"}'::jsonb,
    'pruefer');
  IF NOT coalesce((v_res->>'ok')::boolean, false) THEN
    RAISE EXCEPTION 'PROBE: zweiter layout_vorschlagen scheiterte: %', v_res->>'grund'; END IF;
  IF (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'testfarbe-051') <> 2 THEN
    RAISE EXCEPTION 'PROBE: erneuter Vorschlag mit anderer Gestalt erzeugte keine Fassung 2'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.layout_fassungen WHERE layout = 'testfarbe-051' AND fassung = 2) THEN
    RAISE EXCEPTION 'PROBE: keine layout_fassungen-Zeile fuer Fassung 2'; END IF;
END $$;

-- I5: pult_layout_speichern durch den Betreiber ist die Freigabe, und legt
-- genau eine neue layout_fassungen-Zeile an (keine doppelten Zeilen mehr).
DO $$
DECLARE v_g jsonb; v_n int; v_vorher int; v_nachher int;
BEGIN
  SELECT gestalt INTO v_g FROM marketing.layout_vorlagen WHERE name = 'warm-sand';
  SELECT count(*) INTO v_vorher FROM marketing.layout_fassungen WHERE layout = 'warm-sand';
  v_n := marketing.pult_layout_speichern('warm-sand', v_g || '{"rundung":6}', 'felix');
  SELECT count(*) INTO v_nachher FROM marketing.layout_fassungen WHERE layout = 'warm-sand';
  IF v_nachher <> v_vorher + 1 THEN
    RAISE EXCEPTION 'PROBE: pult_layout_speichern erzeugte nicht genau eine neue Fassungs-Zeile (vorher % nachher %)',
      v_vorher, v_nachher; END IF;
  IF (SELECT status FROM marketing.layout_vorlagen WHERE name = 'warm-sand') <> 'freigegeben'
     OR (SELECT entschieden_von FROM marketing.layout_vorlagen WHERE name = 'warm-sand') <> 'felix'
     OR (SELECT entschieden_am FROM marketing.layout_vorlagen WHERE name = 'warm-sand') IS NULL
     OR (SELECT muster_datei FROM marketing.layout_vorlagen WHERE name = 'warm-sand') <> '' THEN
    RAISE EXCEPTION 'PROBE: Speichern durch den Betreiber ist keine Freigabe geworden'; END IF;
  IF (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'warm-sand') <> v_n THEN
    RAISE EXCEPTION 'PROBE: zurueckgegebene Fassung stimmt nicht mit der Zeile ueberein'; END IF;
END $$;

-- layout_fassungen[fassung].gestalt == layout_vorlagen.gestalt, fuer jede
-- art='layout'-Zeile (auch nach den obigen Mutationen).
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM marketing.layout_vorlagen lv
     WHERE lv.art = 'layout'
       AND NOT EXISTS (
         SELECT 1 FROM marketing.layout_fassungen lf
          WHERE lf.layout = lv.name AND lf.fassung = lv.fassung AND lf.gestalt = lv.gestalt)
  ) THEN
    RAISE EXCEPTION 'PROBE: layout_fassungen und layout_vorlagen.gestalt laufen fuer mindestens eine Zeile auseinander';
  END IF;
END $$;

-- Minor: rundung als String wirft keine rohe Cast-Exception mehr, sondern
-- liefert einen deutschen Grund.
DO $$
DECLARE v_f text;
BEGIN
  v_f := marketing.pult_gestalt_fehler(jsonb_build_object(
    'grund', '#000000', 'text', '#ffffff', 'akzent', '#5eead4', 'flaeche', '#111111',
    'text_hell', '#ffffff', 'text_leise', '#999999', 'gold', '#fbbf24', 'handlung_text', '#000000',
    'rundung', 'abc'));
  IF v_f IS NULL THEN RAISE EXCEPTION 'PROBE: rundung "abc" angenommen'; END IF;
  IF v_f !~ 'rundung' THEN RAISE EXCEPTION 'PROBE: falsche Fehlermeldung fuer rundung "abc": %', v_f; END IF;
END $$;

-- Fassungen bleiben unveraenderlich: der Trigger ist nach 051 wieder aktiv.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
     WHERE c.relname = 'inhalt_fassungen' AND t.tgname = 'trg_inhalt_fassung_unveraenderlich'
       AND t.tgenabled = 'O'
  ) THEN
    RAISE EXCEPTION 'PROBE: Unveraenderlichkeits-Trigger ist nicht wieder aktiv (tgenabled <> O)';
  END IF;
END $$;
DO $$ BEGIN
  UPDATE marketing.inhalt_fassungen SET felder = '{}'
   WHERE fassung = 1 AND inhalt = (SELECT id FROM marketing.inhalte LIMIT 1);
  RAISE EXCEPTION 'PROBE: UPDATE auf Fassung ging nach 051 durch';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM NOT LIKE '%unveraenderlich%' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;

ROLLBACK;
