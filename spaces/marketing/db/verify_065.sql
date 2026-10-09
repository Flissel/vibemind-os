-- Nachweise fuer 065, nur ueber migration_probe (eine Transaktion + ROLLBACK).
CREATE TEMP TABLE _p065 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS id LIMIT 0;

DO $$ DECLARE v uuid; i uuid; BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES
    ('probe_denken', 'Probe Denken', true), ('probe_denken_b', 'Probe Denken B', true);
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, vergeben_bis)
  VALUES ('probe_denken', 'chat', 'x', 'in_arbeit', now() + interval '5 minutes') RETURNING id INTO v;
  INSERT INTO _p065 VALUES ('marke_ok', v);
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, vergeben_bis)
  VALUES ('probe_denken_b', 'chat', 'y', 'in_arbeit', now() - interval '1 second') RETURNING id INTO v;
  INSERT INTO _p065 VALUES ('marke_abgelaufen', v);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('probe_denken', 'newsletter', 'Probe 065')
  RETURNING id INTO i;
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, vergeben_bis)
  VALUES (i, 'chat', 'z', 'in_arbeit', now() + interval '5 minutes') RETURNING id INTO v;
  INSERT INTO _p065 VALUES ('chat_ok', v);
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status)
  VALUES (i, 'chat', 'w', 'fertig') RETURNING id INTO v;
  INSERT INTO _p065 VALUES ('chat_fertig', v);
END $$;

DO $$ DECLARE ok boolean; s jsonb := '[{"zeit":"08:03:41","text":"Frage an Claude"}]'; BEGIN
  ok := marketing.pult_marke_denken((SELECT id FROM _p065 WHERE k='marke_ok'), 'Let me think', s);
  IF NOT ok THEN RAISE EXCEPTION 'marke_ok muss true sein'; END IF;
  IF (SELECT denken FROM marketing.marken_auftraege WHERE id=(SELECT id FROM _p065 WHERE k='marke_ok')) <> 'Let me think'
    THEN RAISE EXCEPTION 'denken nicht gespeichert'; END IF;
  IF (SELECT schritte FROM marketing.marken_auftraege WHERE id=(SELECT id FROM _p065 WHERE k='marke_ok')) <> s
    THEN RAISE EXCEPTION 'schritte nicht gespeichert'; END IF;
  IF marketing.pult_marke_denken((SELECT id FROM _p065 WHERE k='marke_abgelaufen'), 'x', '[]') THEN
    RAISE EXCEPTION 'abgelaufene Vergabe darf nicht schreiben'; END IF;
  IF NOT marketing.pult_chat_denken((SELECT id FROM _p065 WHERE k='chat_ok'), 'c', s) THEN
    RAISE EXCEPTION 'chat_ok muss true sein'; END IF;
  IF marketing.pult_chat_denken((SELECT id FROM _p065 WHERE k='chat_fertig'), 'c', s) THEN
    RAISE EXCEPTION 'fertiger Auftrag darf nicht schreiben'; END IF;
  IF marketing.pult_chat_denken(gen_random_uuid(), 'c', s) THEN
    RAISE EXCEPTION 'unbekannter Auftrag darf nicht schreiben'; END IF;
END $$;

-- Grenzen: jede Verletzung wirft
DO $$ DECLARE a uuid := (SELECT id FROM _p065 WHERE k='chat_ok'); geworfen boolean; BEGIN
  geworfen := false;
  BEGIN PERFORM marketing.pult_chat_denken(a, repeat('x', 20101), '[]');
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'Denken > 20100 muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_chat_denken(a, 'x',
    (SELECT jsonb_agg(jsonb_build_object('zeit','08:00:00','text','s')) FROM generate_series(1,61)));
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION '61 Schritte muessen werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_chat_denken(a, 'x', jsonb_build_array(jsonb_build_object('zeit','08:00:00','text',repeat('t',201))));
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'Schritt > 200 muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_chat_denken(a, 'x', '{"a":1}');
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'Schritte als Objekt muss werfen'; END IF;
  -- genau an der Grenze geht es
  IF NOT marketing.pult_chat_denken(a, repeat('x', 20100),
    (SELECT jsonb_agg(jsonb_build_object('zeit','08:00:00','text',repeat('t',200))) FROM generate_series(1,60)))
    THEN RAISE EXCEPTION 'Grenzwerte muessen gehen'; END IF;
END $$;

SELECT 'verify_065 ok' AS ergebnis;
