-- verify_095: Kampagnen. In einer Transaktion mit ROLLBACK (migration_probe).
DO $$
DECLARE k uuid; b uuid; r jsonb;
BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv, verteiler) VALUES ('probe_095', 'Probe 095', false, '{}');
  k := marketing.kampagne_anlegen('probe_095', 'Herbst', 'Termine fuer bAV', 'sales',
                                  '{"beziehung": "kunde"}', current_date, current_date + 20, 'Felix');
  IF (SELECT status FROM marketing.kampagnen WHERE id = k) <> 'in_planung' THEN
    RAISE EXCEPTION '095: neue Kampagne nicht in_planung'; END IF;
  b := marketing.kampagne_beitrag(k, current_date + 3, 'newsletter', 'Auftakt', 'Mail an Kunden', 'agent');
  IF (SELECT status || '/' || kampagne FROM marketing.kampagnen_plan WHERE id = b)
     <> 'vorschlag/' || k THEN RAISE EXCEPTION '095: Beitrag falsch angelegt'; END IF;
  BEGIN PERFORM marketing.kampagne_beitrag(k, current_date + 40, 'post', 'Zu spaet', '', 'agent');
    RAISE EXCEPTION '095: Datum ausserhalb wurde angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Datum liegt ausserhalb%' THEN RAISE; END IF;
  END;
  BEGIN PERFORM marketing.kampagne_anlegen('probe_095', 'X', '', 'alle', '{}', current_date, current_date, 'F');
    RAISE EXCEPTION '095: falsche Zielgruppe angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Zielgruppe%' THEN RAISE; END IF;
  END;
  IF marketing.kampagne_status(k, 'geplant') <> 'geplant' THEN RAISE EXCEPTION '095: Status'; END IF;
  r := marketing.pult_mandant_loeschen('probe_095', 'probe_095', 'Felix');
  IF (r->>'kampagnen')::int <> 1 OR EXISTS (SELECT 1 FROM marketing.kampagnen WHERE id = k) THEN
    RAISE EXCEPTION '095: Kampagne beim Marke-Loeschen nicht weg: %', r; END IF;
  RAISE NOTICE 'verify_095 ok';
END $$;
