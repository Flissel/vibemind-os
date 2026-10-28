-- 094: Marke endgueltig loeschen (Betreiber 28.10.2026, Sales-UI-Rundgang: „Ich kann noch keine Marke
-- in Marketing loeschen" - Entscheidung: endgueltig, nicht ausblenden).
-- Idempotent, eine Transaktion.
--   pult_mandant_loeschen(mandant, bestaetigung, von) -> jsonb mit der Zahl geloeschter Zeilen je Tabelle.
--   * bestaetigung muss die Marken-Kennung woertlich wiederholen (Schutz gegen Fehlklick).
--   * Abgelehnt, solange die Marke einen Verteiler hat (verteiler <> '{}'): dann versendet ein Laden
--     ueber sie (z. B. vibemind -> sales). Erst den Verteiler leeren, dann loeschen.
--   * Abgelehnt, solange ein Auftrag der Marke laeuft (Bild, Chat, Ton, Video, Marke: status 'in_arbeit', Video auch 'bilder_laufen').
--   * Geloescht werden alle Zeilen der Marke und ihre Unterzeilen: Inhalte samt Fassungen, Mail-Fassungen,
--     Rueckmeldungen, Bild- und Chat-Auftraegen; Kampagnenplan; Layouts und Newsletter-Vorlagen samt
--     Fassungen; Marken-Auftraege/-Vorschlaege/-Spiegel; Ton- und Video-Auftraege samt Reel-Bildern;
--     Medien-Zuordnung. Dateien im Medienordner bleiben liegen (koennen geteilt sein).
--   * Die Fassungs-Tabellen sind per Trigger unveraenderlich (075 erlaubt nur Entwuerfe einzeln). Fuer
--     das endgueltige Loeschen laeuft die Transaktion mit session_replication_role = replica: Trigger
--     UND Fremdschluessel-Kaskaden sind dann aus - deshalb loescht die Funktion jede Unterzeile selbst,
--     Kinder vor Eltern. Nur die Eigentuemerin/ein Superuser darf das setzen (sonst Fehler, nichts weg).
-- Rechte: wie 086 - kein SECURITY DEFINER, keine eigenen GRANTs (die Pult-API laeuft als Eigentuemerin).
BEGIN;

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

  -- Inhalte und alles darunter
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
  DELETE FROM marketing.inhalte WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('inhalte', k);

  -- Layouts und Newsletter-Vorlagen samt Fassungen
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

  -- Marken-Chat (zyklische Fremdschluessel: Auftrag <-> Vorschlag)
  DELETE FROM marketing.marken_vorschlaege WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('marken_vorschlaege', k);
  DELETE FROM marketing.marken_auftraege WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('marken_auftraege', k);
  DELETE FROM marketing.marken_spiegel WHERE mandant = p_mandant;
  GET DIAGNOSTICS k = ROW_COUNT; n := n || jsonb_build_object('marken_spiegel', k);

  -- Ton, Video (Reel-Bilder zuerst), Medien-Zuordnung
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
