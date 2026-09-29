-- verify_050.sql — Proben fuer 050_marketing_pult.sql. Aendert nichts (ROLLBACK).
BEGIN;
DO $$ BEGIN
  IF (SELECT count(*) FROM marketing.mandanten WHERE id IN ('vibemind','fin2gether')) <> 2 THEN
    RAISE EXCEPTION 'PROBE: Mandanten fehlen'; END IF;
  IF (SELECT verteiler FROM marketing.mandanten WHERE id='vibemind') <> ARRAY['sales'] THEN
    RAISE EXCEPTION 'PROBE: VibeMind-Verteiler ist nicht genau {sales}'; END IF;
  IF (SELECT count(*) FROM marketing.inhalte WHERE herkunft_proposal IS NOT NULL)
     <> (SELECT count(*) FROM marketing.broadcast_proposals) THEN
    RAISE EXCEPTION 'PROBE: nicht jeder broadcast_proposal wurde uebernommen'; END IF;
  IF EXISTS (SELECT 1 FROM marketing.inhalte i
             WHERE NOT EXISTS (SELECT 1 FROM marketing.inhalt_fassungen f
                               WHERE f.inhalt=i.id AND f.fassung=1)) THEN
    RAISE EXCEPTION 'PROBE: Inhalt ohne Fassung 1'; END IF;
  IF (SELECT count(*) FROM marketing.layout_vorlagen
      WHERE art='layout' AND mandant='vibemind' AND inhaltsart IS NOT NULL) < 3 THEN
    RAISE EXCEPTION 'PROBE: Layouts nicht VibeMind zugeordnet'; END IF;
END $$;
-- Fassungen: aus Fassung 1 speichern ergibt die naechste freie Nummer
DO $$ DECLARE v_i uuid; v_n int; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte ORDER BY erstellt_am LIMIT 1;
  v_n := marketing.pult_fassung_speichern(v_i, '{"betreff":"x","abschnitte":[{"titel":"","text":"y"}]}', 'dunkel', 'betreiber');
  IF v_n <> (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt=v_i) THEN
    RAISE EXCEPTION 'PROBE: neue Fassung ist nicht die hoechste'; END IF;
  v_n := marketing.pult_fassung_speichern(v_i, '{"betreff":"z","abschnitte":[{"titel":"","text":"w"}]}', 'dunkel', 'betreiber');
  IF v_n <> (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt=v_i) THEN
    RAISE EXCEPTION 'PROBE: zweite Speicherung nicht lueckenlos'; END IF;
END $$;
-- Fassungen sind unveraenderlich
DO $$ BEGIN
  UPDATE marketing.inhalt_fassungen SET felder='{}' WHERE fassung=1;
  RAISE EXCEPTION 'PROBE: UPDATE auf Fassung ging durch';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  IF SQLERRM NOT LIKE '%unveraenderlich%' THEN RAISE EXCEPTION 'PROBE: falscher Grund: %', SQLERRM; END IF;
END $$;
DO $$ BEGIN
  DELETE FROM marketing.inhalt_fassungen WHERE fassung=1;
  RAISE EXCEPTION 'PROBE: DELETE auf Fassung ging durch';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
END $$;
-- Felder ohne Betreff oder ohne Abschnitte werden abgewiesen
DO $$ DECLARE v_i uuid; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte LIMIT 1;
  PERFORM marketing.pult_fassung_speichern(v_i, '{"abschnitte":[]}', 'dunkel', 'betreiber');
  RAISE EXCEPTION 'PROBE: Fassung ohne Betreff ging durch';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
END $$;
-- Entscheiden: freigeben setzt die neueste Fassung, zweimal entscheiden geht nicht
-- (Fix-Runde 1 / I3: pult_entscheiden bekam einen Pflicht-Parameter p_fassung
-- in 051 - diese zwei Aufrufe sind deshalb auf die neue 5-Parameter-Signatur
-- umgestellt; siehe task-1-report.md Abschnitt "Fix round 1".)
DO $$ DECLARE v_i uuid; v_s text; v_fassung int; BEGIN
  SELECT id INTO v_i FROM marketing.inhalte WHERE status='entwurf' LIMIT 1;
  SELECT max(fassung) INTO v_fassung FROM marketing.inhalt_fassungen WHERE inhalt=v_i;
  v_s := marketing.pult_entscheiden(v_i, v_fassung, 'freigeben', 'felix', NULL);
  IF v_s <> 'freigegeben' OR (SELECT freigegebene_fassung FROM marketing.inhalte WHERE id=v_i)
     <> (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt=v_i) THEN
    RAISE EXCEPTION 'PROBE: Freigabe setzt nicht die neueste Fassung'; END IF;
  BEGIN
    PERFORM marketing.pult_entscheiden(v_i, v_fassung, 'ablehnen', 'felix', 'x');
    RAISE EXCEPTION 'PROBE: zweites Urteil ging durch';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  END;
END $$;
-- Gestalt-Pruefung der Regler
DO $$ BEGIN
  IF marketing.pult_gestalt_fehler('{"grund":"#000000","text":"#ffffff","akzent":"#5eead4","flaeche":"#111111","text_hell":"#ffffff","text_leise":"#999999","gold":"#fbbf24","handlung_text":"#000000","schrift":"serif","rundung":8,"abstand":"mittel"}') IS NOT NULL THEN
    RAISE EXCEPTION 'PROBE: gueltige Gestalt abgewiesen'; END IF;
  IF marketing.pult_gestalt_fehler('{"grund":"rot"}') IS NULL THEN
    RAISE EXCEPTION 'PROBE: Farbe rot angenommen'; END IF;
  IF marketing.pult_gestalt_fehler('{"grund":"#000000","text":"#ffffff","akzent":"#5eead4","flaeche":"#111111","text_hell":"#ffffff","text_leise":"#999999","gold":"#fbbf24","handlung_text":"#000000","rundung":999}') IS NULL THEN
    RAISE EXCEPTION 'PROBE: Rundung 999 angenommen'; END IF;
  IF marketing.pult_gestalt_fehler('{"grund":"#000000","text":"#ffffff","akzent":"#5eead4","flaeche":"#111111","text_hell":"#ffffff","text_leise":"#999999","gold":"#fbbf24","handlung_text":"#000000","schrift":"comic"}') IS NULL THEN
    RAISE EXCEPTION 'PROBE: unbekannte Schrift angenommen'; END IF;
  IF marketing.pult_gestalt_fehler(jsonb_build_object('grund','#000000','text','#ffffff','akzent','#5eead4','flaeche','#111111','text_hell','#ffffff','text_leise','#999999','gold','#fbbf24','handlung_text','#000000','logo', 'data:image/png;base64,' || repeat('A', 210000))) IS NULL THEN
    RAISE EXCEPTION 'PROBE: Logo ueber 150 KB angenommen'; END IF;
END $$;
-- Layout speichern: neue Fassung, Gestalt uebernommen; ungueltige Gestalt abgewiesen
DO $$ DECLARE v_n int; v_g jsonb; BEGIN
  SELECT gestalt INTO v_g FROM marketing.layout_vorlagen WHERE name='dunkel';
  v_n := marketing.pult_layout_speichern('dunkel', v_g || '{"rundung":4}', 'felix');
  IF (SELECT fassung FROM marketing.layout_vorlagen WHERE name='dunkel') <> v_n
     OR (SELECT gestalt->>'rundung' FROM marketing.layout_vorlagen WHERE name='dunkel') <> '4' THEN
    RAISE EXCEPTION 'PROBE: Layout-Fassung nicht uebernommen'; END IF;
  BEGIN
    PERFORM marketing.pult_layout_speichern('dunkel', '{"grund":"rot"}', 'felix');
    RAISE EXCEPTION 'PROBE: ungueltige Layout-Gestalt gespeichert';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
  END;
END $$;
-- Standard: genau einer je Mandant und Inhaltsart
DO $$ BEGIN
  PERFORM marketing.pult_layout_als_standard('hell');
  PERFORM marketing.pult_layout_als_standard('dunkel');
  IF (SELECT count(*) FROM marketing.layout_vorlagen
      WHERE standard AND mandant='vibemind'
        AND inhaltsart=(SELECT inhaltsart FROM marketing.layout_vorlagen WHERE name='dunkel')) <> 1 THEN
    RAISE EXCEPTION 'PROBE: nicht genau ein Standard'; END IF;
END $$;
ROLLBACK;
