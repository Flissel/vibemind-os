-- RUNBOOK: 091 ersetzt pult_marke_naechster(interval, text[]) und pult_marke_uebernehmen(uuid, text, text), beide
-- zuletzt aus 086. Ein Replay von 064, 066, 078 oder 086 ueberschreibt sie wieder mit der alten Fassung (naechster
-- ohne pflichtteil bei aktiven Firmen, uebernehmen ohne Pflichtangaben) - danach IMMER 091 erneut einspielen und
-- verify_086 und verify_091 zusammen ueber migration_probe laufen lassen.
-- 091: Pflichtangaben fuer bestehende Marken im Marken-Chat (sales-claw, Brief 2026-10-24 pflichtangaben-chat).
-- Idempotent, eine Transaktion.
--   1) pult_marke_naechster woertlich aus 086; einzige Aenderung: die Rueckgabe traegt pflichtteil fuer JEDE Firma
--      (nicht nur bei einrichtung = 'laeuft'), damit der Arbeiter fehlende Pflichtangaben ansprechen kann.
--      einrichtung_stand bleibt wie in 086 nur bei 'laeuft'.
--   2) pult_marke_uebernehmen woertlich aus 086; zusaetzlich: steht in vorschlag->'pflichtteil' ein Objekt, wird es
--      in derselben Transaktion in mandanten.pflichtteil gemischt (||). Erlaubt sind nur impressum,
--      abmelde_hinweis, vermittlerangaben, je Text, hoechstens 2000/500/2000 Zeichen; ein nicht leerer
--      Abmeldehinweis braucht {abmeldelink} (dieselben Regeln wie pult_mandant_pflichtteil_setzen, 086).
--      Ungueltig -> RAISE mit deutschem Grund, nichts uebernommen (auch der Vorschlag bleibt offen). JSON-null oder
--      fehlend = keine Pflichtangaben. Gilt fuer aktive Firmen und laufende Einrichtungen gleich.
--      Die Pruefung, dass Impressum und Vermittlerangaben belegt sind, macht der Arbeiter vor dem Ablegen
--      (claw/einrichtung.pflichtteil_pruefen); der direkte Weg nimmt einen Vorschlag mit pflichtteil ausserhalb
--      der Einrichtung nie an (api/marke.arbeiter_vorschlag) - der Betreiber bestaetigt mit "Übernehmen".
-- Sperrreihenfolge wie 064: mandanten -> marken_auftraege -> marken_vorschlaege.
-- Rechte: wie 060/064/066/078/086 - kein SECURITY DEFINER, kein SET search_path, keine eigenen GRANTs
-- (Default Privileges aus 003 gelten).
BEGIN;

-- 1) woertlich aus 086; neu nur: pflichtteil immer in der Rueckgabe
CREATE OR REPLACE FUNCTION marketing.pult_marke_naechster(p_frist interval, p_arten text[])
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.marken_auftraege; v_firma text; v_verlauf jsonb; v_vorschlag jsonb;
  v_einrichtung text; v_pflichtteil jsonb; v_stand jsonb;
BEGIN
  IF p_arten IS NOT NULL AND (cardinality(p_arten) = 0 OR EXISTS (
       SELECT 1 FROM unnest(p_arten) x WHERE x IS NULL OR x NOT IN ('chat','uebernehmen','bearbeitung','wissen'))) THEN
    RAISE EXCEPTION 'Unbekannte Auftragsart'; END IF;
  PERFORM marketing._marke_aufraeumen(NULL);
  -- p_arten NULL = Marken-Faden (alles ausser wissen), sonst genau die genannten Arten (Wissens-Faden:
  -- ARRAY['wissen']). Nie zwei Wissens-Laeufe derselben Firma zugleich.
  SELECT m.* INTO a FROM marketing.marken_auftraege m
   WHERE m.status = 'offen'
     AND (CASE WHEN p_arten IS NULL THEN m.art <> 'wissen' ELSE m.art = ANY (p_arten) END)
     AND NOT (m.art = 'wissen' AND EXISTS (SELECT 1 FROM marketing.marken_auftraege w
                                            WHERE w.mandant = m.mandant AND w.art = 'wissen'
                                              AND w.status = 'in_arbeit'))
   ORDER BY (m.art = 'wissen'), m.erstellt_am LIMIT 1 FOR UPDATE OF m SKIP LOCKED;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT name, einrichtung, pflichtteil, einrichtung_stand INTO v_firma, v_einrichtung, v_pflichtteil, v_stand
    FROM marketing.mandanten WHERE id = a.mandant;
  -- Verlauf: die letzten 10 fertigen Chat- und Bearbeitungs-Runden der Firma, aelteste zuerst
  SELECT coalesce(jsonb_agg(jsonb_build_object('nachricht', v.nachricht, 'antwort', v.antwort)
                            ORDER BY v.erstellt_am), '[]'::jsonb)
    INTO v_verlauf
    FROM (SELECT nachricht, antwort, erstellt_am FROM marketing.marken_auftraege
           WHERE mandant = a.mandant AND art IN ('chat','bearbeitung') AND status = 'fertig'
           ORDER BY erstellt_am DESC LIMIT 10) v;
  IF a.art = 'uebernehmen' THEN
    SELECT jsonb_build_object('id', id, 'vorschlag', vorschlag) INTO v_vorschlag
      FROM marketing.marken_vorschlaege WHERE id = a.vorschlag;
  ELSIF a.art IN ('chat','bearbeitung') THEN
    SELECT jsonb_build_object('id', id, 'vorschlag', vorschlag) INTO v_vorschlag
      FROM marketing.marken_vorschlaege WHERE mandant = a.mandant AND status = 'offen';
  END IF;
  UPDATE marketing.marken_auftraege
     SET status = 'in_arbeit', versuche = versuche + 1, vergeben_bis = now() + p_frist, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('id', a.id, 'art', a.art, 'mandant', a.mandant, 'firma', v_firma,
           'nachricht', a.nachricht, 'kontext', a.kontext, 'verlauf', v_verlauf,
           'vorschlag', v_vorschlag, 'einrichtung', v_einrichtung, 'pflichtteil', v_pflichtteil)
         || CASE WHEN v_einrichtung = 'laeuft'
                 THEN jsonb_build_object('einrichtung_stand', v_stand)
                 ELSE '{}'::jsonb END;
END $$;

-- 2) woertlich aus 086; neu nur v_pflicht, ihre Pruefung und das Mischen in mandanten.pflichtteil
CREATE OR REPLACE FUNCTION marketing.pult_marke_uebernehmen(p_vorschlag uuid, p_von text, p_mandant text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_mandant text; v_status text; v_id uuid; v_pflicht jsonb;
BEGIN
  IF length(btrim(coalesce(p_von, ''))) = 0 THEN RAISE EXCEPTION 'Ohne Namen kein Übernehmen'; END IF;
  SELECT mandant INTO v_mandant FROM marketing.marken_vorschlaege WHERE id = p_vorschlag;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannter Vorschlag'; END IF;
  IF v_mandant IS DISTINCT FROM p_mandant THEN
    RAISE EXCEPTION 'Der Vorschlag gehört zu einer anderen Firma – bitte neu laden'; END IF;
  PERFORM 1 FROM marketing.mandanten WHERE id = v_mandant AND (aktiv OR einrichtung = 'laeuft') FOR NO KEY UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Unbekannte oder inaktive Firma'; END IF;
  SELECT status, vorschlag->'pflichtteil' INTO v_status, v_pflicht
    FROM marketing.marken_vorschlaege WHERE id = p_vorschlag FOR UPDATE;
  IF v_status <> 'offen' THEN
    RAISE EXCEPTION 'Inzwischen gibt es ein neueres Profil – bitte neu laden'; END IF;
  PERFORM marketing._marke_aufraeumen(v_mandant);
  IF EXISTS (SELECT 1 FROM marketing.marken_auftraege
              WHERE mandant = v_mandant AND status IN ('offen','in_arbeit') AND art <> 'wissen') THEN
    RAISE EXCEPTION 'Der Assistent arbeitet gerade'; END IF;
  -- 091: Pflichtangaben des Vorschlags (Regeln wie pult_mandant_pflichtteil_setzen, 086); JSON-null = keine
  IF v_pflicht IS NOT NULL AND jsonb_typeof(v_pflicht) <> 'null' THEN
    IF jsonb_typeof(v_pflicht) <> 'object' THEN RAISE EXCEPTION 'Pflichtangaben muessen ein Objekt sein'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_object_keys(v_pflicht) k
                WHERE k NOT IN ('impressum','abmelde_hinweis','vermittlerangaben')) THEN
      RAISE EXCEPTION 'Pflichtangaben kennen nur impressum, abmelde_hinweis, vermittlerangaben'; END IF;
    IF EXISTS (SELECT 1 FROM jsonb_each(v_pflicht) e WHERE jsonb_typeof(e.value) <> 'string') THEN
      RAISE EXCEPTION 'Pflichtangaben muessen Text sein'; END IF;
    IF char_length(v_pflicht->>'impressum') > 2000 THEN
      RAISE EXCEPTION 'Das Impressum ist zu lang (hoechstens 2000 Zeichen)'; END IF;
    IF char_length(v_pflicht->>'abmelde_hinweis') > 500 THEN
      RAISE EXCEPTION 'Der Abmeldehinweis ist zu lang (hoechstens 500 Zeichen)'; END IF;
    IF char_length(v_pflicht->>'vermittlerangaben') > 2000 THEN
      RAISE EXCEPTION 'Die Vermittlerangaben sind zu lang (hoechstens 2000 Zeichen)'; END IF;
    IF length(btrim(coalesce(v_pflicht->>'abmelde_hinweis', ''))) > 0
       AND position('{abmeldelink}' IN v_pflicht->>'abmelde_hinweis') = 0 THEN
      RAISE EXCEPTION 'Der Abmeldehinweis braucht {abmeldelink}'; END IF;
    UPDATE marketing.mandanten SET pflichtteil = coalesce(pflichtteil, '{}'::jsonb) || v_pflicht
     WHERE id = v_mandant;
  END IF;
  UPDATE marketing.marken_vorschlaege
     SET status = 'angenommen', entschieden_von = btrim(p_von), entschieden_am = now()
   WHERE id = p_vorschlag;
  INSERT INTO marketing.marken_auftraege (mandant, art, vorschlag)
  VALUES (v_mandant, 'uebernehmen', p_vorschlag)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

COMMIT;
