-- Nachweise fuer 061, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Legt einen eigenen Probe-Newsletter an, damit echte Auftraege anderer Inhalte nicht stoeren
-- (und verify_060 in derselben Probe seinen Inhalt schon entschieden hat).
-- In der Probe ist now() konstant: Fristen werden durch Zurueckdatieren simuliert.

CREATE TEMP TABLE _p061 ON COMMIT DROP AS SELECT NULL::uuid AS inhalt LIMIT 0;

DO $$ DECLARE v_i uuid; BEGIN
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 061')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 061', '',
    '{"root":{"type":"EmailLayout","data":{"childrenIds":[]}}}'::jsonb, 'betreiber', false);
  INSERT INTO _p061 VALUES (v_i);
END $$;

-- 0) Struktur
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'marketing' AND table_name = 'chat_auftraege'
             AND column_name IN ('zwischenstand','schritt','schritt_nr','zwischen_am','stopp','stopp_am')) = 6,
         '0: neue Spalten';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conrelid = 'marketing.chat_auftraege'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%in_arbeit%') = 1, '0: genau eine Status-Regel';
  ASSERT (SELECT pg_get_constraintdef(oid) FROM pg_constraint
           WHERE conrelid = 'marketing.chat_auftraege'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%in_arbeit%') LIKE '%wartet%', '0: Status kennt wartet';
  ASSERT to_regclass('marketing.chat_auftraege_ein_laufender') IS NOT NULL, '0: Index laufend bleibt';
  ASSERT to_regclass('marketing.chat_auftraege_eine_vormerkung') IS NOT NULL, '0: Index Vormerkung';
END $$;

DO $$ DECLARE
  v_i uuid; b jsonb; d jsonb; d2 jsonb; v_n int; v_neu int; v_betreff text; v_vt text;
  a1 uuid; a2 uuid; w uuid; w2 uuid; v_fehler text; j jsonb; r record; v_anz int;
BEGIN
  SELECT inhalt INTO v_i FROM _p061;
  SELECT fassung, bloecke, felder->>'betreff', coalesce(felder->>'vorschautext', '')
    INTO v_n, b, v_betreff, v_vt
    FROM marketing.inhalt_fassungen WHERE inhalt = v_i ORDER BY fassung DESC LIMIT 1;
  d  := jsonb_set(b, '{root,data,childrenIds}', '["z1"]')
        || '{"z1":{"type":"Text","data":{"style":{},"props":{"text":"eins"}}}}'::jsonb;
  d2 := jsonb_set(b, '{root,data,childrenIds}', '["z2"]')
        || '{"z2":{"type":"Text","data":{"style":{},"props":{"text":"zwei"}}}}'::jsonb;
  ASSERT marketing.pult_bloecke_fehler(d) IS NULL, format('Probe-Dokument ungueltig: %s', marketing.pult_bloecke_fehler(d));

  -- 1) Vormerken ohne Lauf => normaler offener Auftrag
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_vormerken(v_i, '  ', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '1a: Vormerken ohne Nachricht muss scheitern';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_vormerken(v_i, repeat('a', 2001), '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '1b: Nachricht > 2000 muss scheitern';
  j := marketing.pult_chat_vormerken(v_i, 'Erste', '{}');
  ASSERT j->>'status' = 'offen', format('1: ohne Lauf offen: %s', j);
  a1 := (j->>'id')::uuid;
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = a1) = 'offen', '1: Datensatz offen';
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = a1, '1: naechster holt ihn ab';

  -- 2) Vormerken mit Lauf => wartet; zweites Vormerken ersetzt
  j := marketing.pult_chat_vormerken(v_i, 'Zweite', '{"auswahl":"z1"}');
  ASSERT j->>'status' = 'wartet', format('2: mit Lauf wartet: %s', j);
  w := (j->>'id')::uuid;
  j := marketing.pult_chat_vormerken(v_i, 'Zweite neu', '{}');
  ASSERT j->>'status' = 'wartet' AND (j->>'id')::uuid = w, format('2: ersetzt denselben Auftrag: %s', j);
  SELECT count(*) INTO v_anz FROM marketing.chat_auftraege WHERE inhalt = v_i AND status = 'wartet';
  ASSERT v_anz = 1, format('2: genau eine Vormerkung, sind %s', v_anz);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = w;
  ASSERT r.nachricht = 'Zweite neu' AND r.kontext = '{}'::jsonb, '2: Text ersetzt';
  ASSERT marketing.pult_chat_naechster('5 minutes') IS NULL, '2: wartet wird nicht abgeholt';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_vormerkung_starten(v_i); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%Der Assistent arbeitet gerade%', format('2: Starten waehrend Lauf: %s', v_fehler);

  -- 3) Zwischenstand speichert und verlaengert
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() + interval '1 minute' WHERE id = a1;
  j := marketing.pult_chat_zwischenstand(a1, d, 'Titel setzen', 1, '5 minutes');
  ASSERT j = '{"weiter":true}'::jsonb, format('3: weiter: %s', j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a1;
  ASSERT r.zwischenstand = d AND r.schritt = 'Titel setzen' AND r.schritt_nr = 1 AND r.zwischen_am = now(), '3: gespeichert';
  ASSERT r.vergeben_bis = now() + interval '5 minutes', '3: Vergabe verlaengert';
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = v_n, '3: keine Fassung';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_zwischenstand(a1, jsonb_build_object('x', repeat('a', 262200)), 's', 2, '5 minutes');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%Zwischenstand zu gro%', format('3b: zu gross: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_zwischenstand(a1, '[]', 's', 2, '5 minutes'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '3c: kein Objekt muss scheitern';
  j := marketing.pult_chat_zwischenstand(gen_random_uuid(), d, 's', 2, '5 minutes');
  ASSERT j = '{"weiter":false,"grund":"verloren"}'::jsonb, format('3d: unbekannt verloren: %s', j);
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = a1;
  j := marketing.pult_chat_zwischenstand(a1, d2, 's', 2, '5 minutes');
  ASSERT j->>'grund' = 'verloren', format('3e: abgelaufene Vergabe verloren: %s', j);
  ASSERT (SELECT zwischenstand FROM marketing.chat_auftraege WHERE id = a1) = d, '3e: nichts gespeichert';
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() + interval '5 minutes' WHERE id = a1;

  -- 4) fertig leert den Zwischenstand und gibt die Vormerkung frei
  UPDATE marketing.chat_auftraege SET erstellt_am = now() - interval '1 minute' WHERE id = w;
  j := marketing.pult_chat_fertig(a1, 'Erledigt', d, '[]', '{}');
  ASSERT (j->>'fassung')::int = v_n + 1, format('4: fassung: %s', j);
  v_n := v_n + 1;
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a1;
  ASSERT r.status = 'fertig' AND r.zwischenstand IS NULL AND r.schritt = '', '4: Zwischenstand geleert';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = w;
  ASSERT r.status = 'offen' AND r.erstellt_am = now(), format('4: Vormerkung freigegeben: %s', r.status);

  -- 5) zurueck gibt nicht frei; wartet sperrt das Handspeichern nicht
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = w AND j->>'nachricht' = 'Zweite neu' AND (j->>'fassung')::int = v_n,
         format('5: freigegebene Vormerkung baut auf neuer Fassung: %s', j);
  PERFORM marketing.pult_chat_zwischenstand(w, d2, 'Bild', 4, '5 minutes');
  j := marketing.pult_chat_vormerken(v_i, 'Dritte', '{}');
  ASSERT j->>'status' = 'wartet', '5: Dritte wartet';
  w2 := (j->>'id')::uuid;
  ASSERT marketing.pult_chat_zurueck(w, 'Ging nicht') = 'fehler', '5: zurueck';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = w;
  ASSERT r.zwischenstand IS NULL AND r.schritt = '' AND r.antwort = 'Ging nicht', '5: zurueck leert Zwischenstand';
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = w2) = 'wartet', '5: zurueck gibt nicht frei';
  v_neu := marketing.pult_bloecke_speichern(v_i, v_n, v_betreff, v_vt, d2, 'betreiber', false);
  ASSERT v_neu = v_n + 1, '5: wartet sperrt den Betreiber nicht';
  v_n := v_neu;

  -- 6) Starten von Hand, Loeschen
  ASSERT marketing.pult_chat_vormerkung_starten(v_i) = w2, '6: starten liefert die id';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = w2;
  ASSERT r.status = 'offen' AND r.erstellt_am = now(), '6: gestartet';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_vormerkung_starten(v_i); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '6: nichts zu starten muss scheitern';
  a2 := (marketing.pult_chat_naechster('5 minutes')->>'id')::uuid;
  ASSERT a2 = w2, '6: abgeholt';
  PERFORM marketing.pult_chat_vormerken(v_i, 'Vierte', '{}');
  ASSERT marketing.pult_chat_vormerkung_loeschen(v_i), '6: loeschen true';
  ASSERT NOT marketing.pult_chat_vormerkung_loeschen(v_i), '6: zweites loeschen false';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = v_i AND status = 'wartet'), '6: weg';

  -- 7) Stopp behalten: Arbeiter erfaehrt es beim Zwischenstand; VM schliesst nach 15 s ab
  PERFORM marketing.pult_chat_zwischenstand(a2, d, 'Eins', 2, '5 minutes');
  w := (marketing.pult_chat_vormerken(v_i, 'Fuenfte', '{}')->>'id')::uuid;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_stoppen(v_i, 'weg'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '7: ungueltige Stopp-Art muss scheitern';
  j := marketing.pult_chat_stoppen(v_i, 'behalten');
  ASSERT j->>'abgeschlossen' = 'false' AND (j->>'id')::uuid = a2, format('7: Stopp laufend: %s', j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.stopp = 'behalten' AND r.stopp_am = now() AND r.status = 'in_arbeit', '7: Stopp gesetzt';
  j := marketing.pult_chat_zwischenstand(a2, d2, 'Zwei', 3, '5 minutes');
  ASSERT j = '{"weiter":false,"grund":"stopp","stopp":"behalten"}'::jsonb, format('7: weiter false: %s', j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.zwischenstand = d2 AND r.schritt_nr = 3, '7: Zwischenstand trotz Stopp gespeichert';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig(a2, 'zu spaet', d2, '[]', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%gestoppt%', format('7: fertig nach Stopp muss scheitern: %s', v_fehler);
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.pult_chat_stopp_faellig() s(id) WHERE s.id = a2), '7: noch nicht faellig';
  UPDATE marketing.chat_auftraege SET stopp_am = now() - interval '20 seconds' WHERE id = a2;
  ASSERT EXISTS (SELECT 1 FROM marketing.pult_chat_stopp_faellig() s(id) WHERE s.id = a2), '7: faellig nach 20 s';
  j := marketing.pult_chat_stopp_abschliessen(a2, d2, NULL);
  ASSERT j->>'status' = 'fertig' AND (j->>'fassung')::int = v_n + 1, format('7: abgeschlossen: %s', j);
  ASSERT (SELECT urheber FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = v_n + 1) = 'agent', '7: Urheber agent';
  ASSERT (SELECT bloecke FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = v_n + 1) = d2, '7: Bloecke uebernommen';
  v_n := v_n + 1;
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.status = 'fertig' AND r.antwort LIKE 'Gestoppt nach Schritt 3%' AND r.fassung_nachher = v_n
     AND r.zwischenstand IS NULL AND r.schritt = '' AND r.vergeben_bis IS NULL
     AND r.ergebnis->>'notiz' = 'gestoppt nach Schritt 3', format('7: Auftrag: %s / %s', r.antwort, r.ergebnis);
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = w) = 'wartet', '7: Stopp gibt Vormerkung nicht frei';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.pult_chat_stopp_faellig() s(id) WHERE s.id = a2), '7: nicht mehr faellig';
  j := marketing.pult_chat_stopp_abschliessen(a2, d, NULL);
  ASSERT j->>'status' = 'fertig' AND (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = v_n,
         format('7: zweites Abschliessen ohne neue Fassung: %s', j);

  -- 8) Stopp verwerfen: keine Fassung, auch wenn Bloecke kommen
  a2 := marketing.pult_chat_vormerkung_starten(v_i);
  PERFORM marketing.pult_chat_naechster('5 minutes');
  PERFORM marketing.pult_chat_zwischenstand(a2, d, 'Eins', 1, '5 minutes');
  j := marketing.pult_chat_stoppen(v_i, 'verwerfen');
  ASSERT j->>'abgeschlossen' = 'false', '8: Stopp laufend';
  j := marketing.pult_chat_stopp_abschliessen(a2, d, NULL);
  ASSERT j->>'status' = 'fehler' AND j->'fassung' IS NULL, format('8: verwerfen: %s', j);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = v_n, '8: keine Fassung';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.status = 'fehler' AND r.antwort = 'Gestoppt – nichts übernommen' AND r.zwischenstand IS NULL, '8: Auftrag';

  -- 9) Stopp behalten ohne gueltige Bloecke: keine Fassung, Hinweis
  a2 := (marketing.pult_chat_vormerken(v_i, 'Sechste', '{}')->>'id')::uuid;
  PERFORM marketing.pult_chat_naechster('5 minutes');
  PERFORM marketing.pult_chat_stoppen(v_i, 'behalten');
  j := marketing.pult_chat_stopp_abschliessen(a2, NULL, 'Zwischenstand ungültig');
  ASSERT j->>'status' = 'fehler', format('9: ohne Bloecke: %s', j);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = v_n, '9: keine Fassung';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.antwort = 'Gestoppt – nichts übernommen' AND r.hinweise = '["Zwischenstand ungültig"]'::jsonb, '9: Hinweis';

  -- 10) Stopp auf offen => sofort fehler; ohne Lauf => Fehler
  a2 := (marketing.pult_chat_vormerken(v_i, 'Siebte', '{}')->>'id')::uuid;
  j := marketing.pult_chat_stoppen(v_i, 'verwerfen');
  ASSERT j->>'abgeschlossen' = 'true', format('10: offen sofort abgeschlossen: %s', j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.status = 'fehler' AND r.antwort = 'Gestoppt, bevor der Assistent begonnen hat', '10: fehler';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_stoppen(v_i, 'behalten'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '10: Stopp ohne Lauf muss scheitern';

  -- 11) Requeue ueberspringt gestoppte Auftraege
  a2 := (marketing.pult_chat_vormerken(v_i, 'Achte', '{}')->>'id')::uuid;
  PERFORM marketing.pult_chat_naechster('5 minutes');
  PERFORM marketing.pult_chat_stoppen(v_i, 'behalten');
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = a2;
  PERFORM marketing.pult_chat_aufraeumen(NULL);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.status = 'in_arbeit' AND r.versuche = 1, format('11: nicht requeued: %s', r.status);

  -- 12) Entscheiden beendet auch die Vormerkung
  j := marketing.pult_chat_vormerken(v_i, 'Neunte', '{}');
  ASSERT j->>'status' = 'wartet', '12: Vormerkung neben gestopptem Lauf';
  w := (j->>'id')::uuid;
  UPDATE marketing.inhalte SET status = 'abgelehnt', entschieden_von = 'probe', entschieden_am = now(), grund = 'probe'
   WHERE id = v_i;
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = w;
  ASSERT r.status = 'fehler' AND r.antwort = 'Newsletter wurde entschieden', format('12: wartet beendet: %s', r.status);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a2;
  ASSERT r.status = 'fehler' AND r.antwort = 'Newsletter wurde entschieden', format('12: laufend beendet: %s', r.status);
END $$;
SELECT 'verify_061 ok' AS ergebnis;
