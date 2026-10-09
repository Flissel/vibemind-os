-- Nachweise fuer 067, nur ueber migration_probe (eine Transaktion + ROLLBACK). In der Probe ist now() konstant.
-- Eigene Probe-Newsletter. Offene, laufende und wartende Runden echter Entwuerfe in DIESER Transaktion beiseite
-- (pult_chat_naechster holt den aeltesten offenen ALLER Inhalte, eine abgelaufene Vergabe wuerde wieder offen);
-- der ROLLBACK stellt sie wieder her.
UPDATE marketing.chat_auftraege SET status = 'fehler' WHERE status IN ('offen', 'in_arbeit', 'wartet');
CREATE TEMP TABLE _p067 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS id LIMIT 0;

DO $$ DECLARE v_i uuid; v_j uuid; v_k uuid; d jsonb; BEGIN
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["held","t"]}},
         "held":{"type":"Image","data":{"style":{},"props":{"url":"medien:platzhalter-2x1.png","alt":"Team","width":600,"height":300}}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 067') RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 067', '', d, 'betreiber', false);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 067 J') RETURNING id INTO v_j;
  PERFORM marketing.pult_bloecke_speichern(v_j, 0, 'Probe 067 J', '', d, 'betreiber', false);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 067 K') RETURNING id INTO v_k;
  PERFORM marketing.pult_bloecke_speichern(v_k, 0, 'Probe 067 K', '', d, 'betreiber', false);
  INSERT INTO _p067 VALUES ('i', v_i), ('j', v_j), ('k', v_k);
END $$;

-- 0) Struktur
DO $$ BEGIN
  ASSERT to_regclass('marketing.chat_auftraege_ein_laufender') IS NULL, '0: Index ein laufender entfernt';
  ASSERT to_regclass('marketing.chat_auftraege_eine_vormerkung') IS NULL, '0: Index Vormerkung entfernt';
  ASSERT to_regprocedure('marketing.pult_chat_vormerken(uuid, text, jsonb)') IS NULL, '0: vormerken entfernt';
  ASSERT to_regprocedure('marketing.pult_chat_vormerkung_loeschen(uuid)') IS NULL, '0: vormerkung_loeschen entfernt';
  ASSERT to_regprocedure('marketing.pult_chat_vormerkung_starten(uuid)') IS NULL, '0: vormerkung_starten entfernt';
  ASSERT to_regprocedure('marketing.pult_chat_senden(uuid, text, jsonb)') IS NOT NULL, '0: senden';
  ASSERT to_regprocedure('marketing._chat_nachruecken(uuid)') IS NOT NULL, '0: nachruecken';
  ASSERT to_regprocedure('marketing._chat_laufend(uuid)') IS NOT NULL, '0: laufend';
  ASSERT to_regprocedure('marketing.pult_chat_bild_ids(uuid, jsonb)') IS NOT NULL, '0: bild_ids';
  ASSERT to_regprocedure('marketing.pult_chat_bild_hinweise(jsonb)') IS NOT NULL, '0: bild_hinweise';
  ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'marketing' AND p.proname = 'pult_chat_fertig') = 1, '0: genau eine pult_chat_fertig';
  ASSERT to_regprocedure('marketing.pult_chat_fertig(uuid, text, jsonb, jsonb, jsonb, int)') IS NOT NULL, '0: fertig mit Basis';
  ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'marketing' AND p.proname = 'pult_chat_stopp_abschliessen') = 1,
         '0: genau eine pult_chat_stopp_abschliessen';
  ASSERT to_regprocedure('marketing.pult_chat_stopp_abschliessen(uuid, jsonb, text, int)') IS NOT NULL,
         '0: stopp_abschliessen mit Basis';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'chat_auftraege' AND column_name = 'bereit_am'), '0: bereit_am';
END $$;

-- 1) Grenzen: 3 laufen, 5 warten, die 6. wartende wird abgelehnt; Handbearbeitung gesperrt
DO $$ DECLARE v_i uuid := (SELECT id FROM _p067 WHERE k = 'i'); j jsonb; v_fehler text; n int; BEGIN
  FOR n IN 1..8 LOOP
    j := marketing.pult_chat_senden(v_i, 'Runde ' || n, '{}');
    ASSERT j->>'status' = CASE WHEN n <= 3 THEN 'offen' ELSE 'wartet' END, format('1a: Runde %s: %s', n, j);
    -- eindeutige Reihenfolge (in der Probe ist now() konstant), alle juenger als 2 min
    UPDATE marketing.chat_auftraege SET erstellt_am = now() - make_interval(secs => 100 - n) WHERE id = (j->>'id')::uuid;
    INSERT INTO _p067 VALUES ('r' || n, (j->>'id')::uuid);
  END LOOP;
  ASSERT (SELECT fassung_vorher FROM marketing.chat_auftraege WHERE id = (SELECT id FROM _p067 WHERE k = 'r4')) IS NULL,
         '1b: wartende Runde hat noch keine Basis';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_senden(v_i, 'Runde 9', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Bitte warten, bis eine Runde fertig ist', format('1c: sechste wartende: %s', v_fehler);
  ASSERT marketing._chat_laufend(v_i) = 3, '1d: drei laufen';
  ASSERT (SELECT count(*) FROM marketing.chat_auftraege WHERE inhalt = v_i AND status = 'wartet') = 5, '1e: fuenf warten';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, 1, 'Probe 067', '',
          (SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 1), 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('1f: Handbearbeitung gesperrt: %s', v_fehler);
END $$;

-- 2) Vergabe aelteste zuerst; ein frei werdender Platz startet die naechste wartende Runde
DO $$ DECLARE j jsonb; r record; BEGIN
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = (SELECT id FROM _p067 WHERE k = 'r1') AND (j->>'fassung')::int = 1, format('2a: %s', j);
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = (SELECT id FROM _p067 WHERE k = 'r2'), format('2b: %s', j);
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = (SELECT id FROM _p067 WHERE k = 'r3'), format('2c: %s', j);
  ASSERT marketing.pult_chat_naechster('5 minutes') IS NULL, '2d: wartende Runden werden nicht vergeben';
  ASSERT marketing.pult_chat_zurueck((SELECT id FROM _p067 WHERE k = 'r1'), 'probe') = 'fehler', '2e: r1 gibt auf';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = (SELECT id FROM _p067 WHERE k = 'r4');
  ASSERT r.status = 'offen' AND r.bereit_am = now() AND r.fassung_vorher = 1 AND r.erstellt_am < now(),
         format('2f: r4 rueckt nach, erstellt_am bleibt: %s', row_to_json(r));
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = (SELECT id FROM _p067 WHERE k = 'r5')) = 'wartet',
         '2g: nur eine rueckt nach';
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = r.id, format('2h: die nachgerueckte wird vergeben: %s', j);
END $$;

-- 3) Rennen und Basis; Review Focus 3: Rueckgaengig-Basis = die Fassung, auf die nachgespielt wurde
DO $$ DECLARE v_i uuid := (SELECT id FROM _p067 WHERE k = 'i'); a2 uuid := (SELECT id FROM _p067 WHERE k = 'r2');
  a3 uuid := (SELECT id FROM _p067 WHERE k = 'r3'); b jsonb; j jsonb; v_fehler text; r record; BEGIN
  SELECT bloecke INTO b FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 1;
  j := marketing.pult_chat_fertig(a2, 'zwei', jsonb_set(b, '{t,data,props,text}', '"zwei"'), '[]', '{}');
  ASSERT (j->>'fassung')::int = 2, format('3a: %s', j);
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = (SELECT id FROM _p067 WHERE k = 'r5')) = 'offen',
         '3b: fertig laesst r5 nachruecken';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig(a3, 'drei', jsonb_set(b, '{held,data,props,alt}', '"Drei"'), '[]', '{}');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Inzwischen gibt es Fassung 2%', format('3c: Rennen auf alter Basis verloren: %s', v_fehler);
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = a3) = 'in_arbeit', '3d: bleibt in Arbeit';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig(a3, 'drei', b, '[]', '{}', 0); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Basis-Fassung 0 passt nicht zu diesem Auftrag', format('3e: %s', v_fehler);
  SELECT bloecke INTO b FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2;
  j := marketing.pult_chat_fertig(a3, 'drei', jsonb_set(b, '{held,data,props,alt}', '"Drei"'), '[]', '{}', 2);
  ASSERT (j->>'fassung')::int = 3, format('3f: %s', j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a3;
  ASSERT r.fassung_vorher = 2 AND r.fassung_nachher = 3, format('3g: Basis gemerkt: %s', row_to_json(r));
  ASSERT (SELECT bloecke #>> '{t,data,props,text}' FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 3)
         = 'zwei', '3h: die Arbeit von Runde 2 bleibt in Fassung 3';
END $$;

-- 4) Stopp pro Runde
DO $$ DECLARE v_i uuid := (SELECT id FROM _p067 WHERE k = 'i');
  r4 uuid := (SELECT id FROM _p067 WHERE k = 'r4'); r5 uuid := (SELECT id FROM _p067 WHERE k = 'r5');
  r6 uuid := (SELECT id FROM _p067 WHERE k = 'r6'); r7 uuid := (SELECT id FROM _p067 WHERE k = 'r7');
  r8 uuid := (SELECT id FROM _p067 WHERE k = 'r8'); b jsonb; j jsonb; v_fehler text; BEGIN
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = r6) = 'offen', '4a: r6 nach 3f nachgerueckt';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_stoppen(v_i, 'behalten'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Mehrere Runden laufen – bitte die Runde wählen', format('4b: %s', v_fehler);
  j := marketing.pult_chat_stoppen(v_i, 'behalten', r4);
  ASSERT j->>'abgeschlossen' = 'false' AND (j->>'id')::uuid = r4, format('4c: %s', j);
  ASSERT (SELECT stopp FROM marketing.chat_auftraege WHERE id = r4) = 'behalten'
     AND (SELECT stopp FROM marketing.chat_auftraege WHERE id = r5) IS NULL
     AND (SELECT status FROM marketing.chat_auftraege WHERE id = r5) = 'offen', '4d: nur diese Runde';
  j := marketing.pult_chat_stoppen(v_i, 'verwerfen', r7);
  ASSERT j->>'abgeschlossen' = 'true'
     AND (SELECT status || '|' || antwort FROM marketing.chat_auftraege WHERE id = r7)
         = 'fehler|Gestoppt, bevor der Assistent begonnen hat', '4e: wartende Runde endet sofort';
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = r8) = 'wartet', '4f: kein Platz frei, r8 wartet';
  j := marketing.pult_chat_stoppen(v_i, 'verwerfen', r5);
  ASSERT j->>'abgeschlossen' = 'true', format('4g: %s', j);
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = r8) = 'offen', '4h: Platz frei, r8 rueckt nach';
  j := marketing.pult_chat_stoppen(v_i, 'verwerfen', r7);
  ASSERT j->>'veraltet' = 'true', format('4i: erledigte Runde ist veraltet: %s', j);
  SELECT bloecke INTO b FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 3;
  j := marketing.pult_chat_stopp_abschliessen(r4, jsonb_set(b, '{t,data,props,text}', '"vier"'), '', 3);
  ASSERT j->>'status' = 'fertig' AND (j->>'fassung')::int = 4, format('4j: %s', j);
  ASSERT (SELECT fassung_vorher FROM marketing.chat_auftraege WHERE id = r4) = 3, '4k: Basis gemerkt';
END $$;

-- 5) Review Focus 4: PC aus - die Warteschlange verfaellt mit der nie abgeholten Runde
DO $$ DECLARE v_j uuid := (SELECT id FROM _p067 WHERE k = 'j'); w uuid; r record; n int; BEGIN
  FOR n IN 1..3 LOOP PERFORM marketing.pult_chat_anlegen(v_j, 'chat', 'J' || n, '{}'); END LOOP;
  w := (marketing.pult_chat_senden(v_j, 'J4', '{}')->>'id')::uuid;
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = w) = 'wartet', '5a';
  UPDATE marketing.chat_auftraege SET erstellt_am = now() - interval '3 minutes' WHERE inhalt = v_j AND status = 'offen';
  PERFORM marketing.pult_chat_aufraeumen(v_j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = w;
  ASSERT r.status = 'fehler' AND r.antwort = 'Der Assistent läuft am PC und ist gerade aus',
         format('5b: wartende Runde verfaellt mit: %s', row_to_json(r));
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = v_j AND status IN ('offen','in_arbeit','wartet')),
         '5c: nichts lebt weiter';
END $$;

-- 6) Ein Newsletter-Export laeuft allein
DO $$ DECLARE v_k uuid := (SELECT id FROM _p067 WHERE k = 'k'); a uuid; e uuid; v_fehler text; BEGIN
  a := marketing.pult_chat_anlegen(v_k, 'chat', 'K1', '{}');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_k, 'export', '', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('6a: Export neben Chat: %s', v_fehler);
  PERFORM marketing.pult_chat_zurueck(a, 'probe');
  e := marketing.pult_chat_anlegen(v_k, 'export', '', '{}');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_senden(v_k, 'K2', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('6b: Chat neben Export: %s', v_fehler);
  PERFORM marketing.pult_chat_zurueck(e, 'probe');
END $$;

-- 7) Gescheiterte Bildauftraege einer Runde als Hinweis
DO $$ DECLARE v_k uuid := (SELECT id FROM _p067 WHERE k = 'k'); a uuid; bi uuid; j jsonb; v_fehler text; BEGIN
  a := marketing.pult_chat_anlegen(v_k, 'chat', 'Bild bitte', '{}');
  UPDATE marketing.chat_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 minutes',
         fassung_vorher = 1 WHERE id = a;
  PERFORM marketing.pult_chat_fertig(a, 'ok', NULL, '[]', '{}');
  bi := marketing.pult_bild_auftrag(v_k, 'held', false, 'Kerzen', 'agent');
  UPDATE marketing.bild_auftraege SET status = 'fehler', befund = 'Zeitüberschreitung beim Laden' WHERE id = bi;
  ASSERT marketing.pult_chat_bild_ids(a, jsonb_build_array(bi::text)), '7a: gemerkt';
  j := marketing.pult_chat_bild_hinweise((SELECT ergebnis FROM marketing.chat_auftraege WHERE id = a));
  ASSERT j = jsonb_build_array('Bild für held nicht erzeugt: Zeitüberschreitung beim Laden'), format('7b: %s', j);
  ASSERT marketing.pult_chat_bild_hinweise('{}') = '[]'::jsonb, '7c: ohne Bilder leer';
  ASSERT NOT marketing.pult_chat_bild_ids(gen_random_uuid(), '[]'), '7d: unbekannte Runde';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_bild_ids(a, '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Bild-Ids muessen eine Liste mit hoechstens 10 Eintraegen sein', format('7e: %s', v_fehler);
END $$;

SELECT 'verify_067 ok' AS ergebnis;
