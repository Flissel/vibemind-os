-- Nachweise fuer 063, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Newsletter (wie verify_061), damit echte Auftraege anderer Inhalte nicht stoeren.
-- Kein pult_chat_naechster/pult_bild_naechster: die holen den aeltesten Auftrag ALLER Inhalte;
-- "in Arbeit" wird hier deshalb direkt gesetzt. In der Probe ist now() konstant.

CREATE TEMP TABLE _p063 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS inhalt LIMIT 0;

DO $$ DECLARE v_i uuid; v_j uuid; v_k uuid; v_leer uuid; v_e uuid; d jsonb; BEGIN
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["held","t"]}},
         "held":{"type":"Image","data":{"style":{"padding":{"top":0,"bottom":0,"left":0,"right":0}},"props":{"url":"medien:platzhalter-2x1.png","alt":"Team","width":600,"height":300}}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 063')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 063', '', d, 'betreiber', false);
  PERFORM marketing.pult_bloecke_speichern(v_i, 1, 'Probe 063', '',
    jsonb_set(d, '{t,data,props,text}', '"zwei"'), 'betreiber', false);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 063 J')
  RETURNING id INTO v_j;
  PERFORM marketing.pult_bloecke_speichern(v_j, 0, 'Probe 063 J', '', d, 'betreiber', false);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 063 K')
  RETURNING id INTO v_k;
  PERFORM marketing.pult_bloecke_speichern(v_k, 0, 'Probe 063 K', '', d, 'betreiber', false);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 063 leer')
  RETURNING id INTO v_leer;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 063 E')
  RETURNING id INTO v_e;
  PERFORM marketing.pult_bloecke_speichern(v_e, 0, 'Probe 063 E', '', d, 'betreiber', false);
  INSERT INTO _p063 VALUES ('i', v_i), ('j', v_j), ('k', v_k), ('leer', v_leer), ('e', v_e);
END $$;

-- 0) Struktur
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'marketing' AND table_name = 'inhalte'
             AND column_name IN ('eingereichte_fassung','eingereicht_am','eingereicht_von')) = 3, '0: Einreich-Spalten';
  ASSERT to_regclass('marketing.rueckmeldungen') IS NOT NULL, '0: Tabelle rueckmeldungen';
  ASSERT to_regclass('marketing.rueckmeldungen_offen_idx') IS NOT NULL, '0: Index offene Rueckmeldungen';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conrelid = 'marketing.inhalte'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%abgelehnt%') = 1, '0: genau eine Status-Regel';
  ASSERT (SELECT pg_get_constraintdef(oid) FROM pg_constraint
           WHERE conrelid = 'marketing.inhalte'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%abgelehnt%') LIKE '%eingereicht%', '0: Status kennt eingereicht';
  ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'marketing' AND p.proname = 'pult_entscheiden') = 1, '0: genau eine pult_entscheiden';
  ASSERT to_regprocedure('marketing.pult_einreichen(uuid, text)') IS NOT NULL, '0: pult_einreichen';
  ASSERT to_regprocedure('marketing.pult_zurueckziehen(uuid, text)') IS NOT NULL, '0: pult_zurueckziehen';
  ASSERT to_regprocedure('marketing.pult_zurueckgeben(uuid, int, text, text)') IS NOT NULL, '0: pult_zurueckgeben';
  ASSERT to_regprocedure('marketing._pult_bloecke_speichern_053(uuid, int, text, text, jsonb, text, boolean)') IS NOT NULL,
         '0: 053-Original bleibt';
END $$;

DO $$ DECLARE
  v_i uuid; v_j uuid; v_k uuid; v_leer uuid; b jsonb; v_n int; v_fehler text; r record;
  a uuid; bi uuid; bo uuid; rm1 uuid; j jsonb; v_felder jsonb;
BEGIN
  SELECT inhalt INTO v_i FROM _p063 WHERE k = 'i';
  SELECT inhalt INTO v_j FROM _p063 WHERE k = 'j';
  SELECT inhalt INTO v_k FROM _p063 WHERE k = 'k';
  SELECT inhalt INTO v_leer FROM _p063 WHERE k = 'leer';
  SELECT fassung, bloecke INTO v_n, b FROM marketing.inhalt_fassungen
   WHERE inhalt = v_i ORDER BY fassung DESC LIMIT 1;
  ASSERT v_n = 2, format('Probe: zwei Fassungen erwartet, sind %s', v_n);
  v_felder := '{"betreff":"x","abschnitte":[{"titel":"","text":"y"}]}'::jsonb;

  -- 1) Einreichen gesperrt, solange Chat oder Bild laeuft; ohne Fassung; ohne Namen
  a := marketing.pult_chat_anlegen(v_i, 'chat', 'Bitte kuerzer', '{}');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('1a: Chat offen: %s', v_fehler);
  UPDATE marketing.chat_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 minutes'
   WHERE id = a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('1b: Chat in Arbeit: %s', v_fehler);
  ASSERT marketing.pult_chat_zurueck(a, 'probe') = 'fehler', '1b: Chat beendet';

  bi := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch');
  -- Review Focus 1 / R4: nur ein Bild-Auftrag in Arbeit sperrt
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 minutes',
         grund_fassung = v_n WHERE id = bi;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ein Bild wird gerade erzeugt', format('1d: Bild in Arbeit: %s', v_fehler);
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_i) = 'entwurf', '1d: Status bleibt entwurf';
  UPDATE marketing.bild_auftraege SET status = 'fehler', vergeben_bis = NULL WHERE id = bi;   -- Arbeiter gibt auf

  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_leer, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ohne Fassung gibt es nichts einzureichen', format('1e: ohne Fassung: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, '  '); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '1f: ohne Namen muss scheitern';
  -- Die Regel selbst: eingereicht ohne Einreich-Felder geht nicht
  v_fehler := NULL;
  BEGIN UPDATE marketing.inhalte SET status = 'eingereicht' WHERE id = v_i;
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '1g: eingereicht ohne Felder muss an der Regel scheitern';

  -- 2) Einreichen (entwurf -> eingereicht); ein wartender Bild-Auftrag sperrt nicht, er wird verworfen (R4)
  bo := marketing.pult_bild_auftrag(v_i, NULL, true, '', 'system');
  ASSERT (SELECT status FROM marketing.bild_auftraege WHERE id = bo) = 'offen', '2: Bild-Auftrag wartet';
  ASSERT marketing.pult_einreichen(v_i, 'probe') = v_n, '2: liefert die neueste Fassung';
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bo;
  ASSERT r.status = 'verworfen' AND r.befund = 'Newsletter eingereicht' AND r.geaendert_am = now(),
         format('2: wartender Bild-Auftrag verworfen: %s / %s', r.status, r.befund);
  ASSERT (SELECT status FROM marketing.bild_auftraege WHERE id = bi) = 'fehler', '2: andere Auftraege unberuehrt';
  SELECT * INTO r FROM marketing.inhalte WHERE id = v_i;
  ASSERT r.status = 'eingereicht' AND r.eingereichte_fassung = v_n AND r.eingereicht_am = now()
     AND r.eingereicht_von = 'probe' AND r.entschieden_von IS NULL, format('2: Felder: %s', row_to_json(r));
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt schon zur Freigabe', format('2: doppelt einreichen: %s', v_fehler);

  -- 3) Sperren bei eingereicht (Review Focus 5: alter Editor-Tab speichert)
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, v_n, 'Probe 063', '', b, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('3a: bloecke_speichern betreiber: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, v_n, 'Probe 063', '', b, 'agent', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('3b: bloecke_speichern agent: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_fassung_speichern(v_i, v_felder, 'dunkel', 'betreiber');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('3c: fassung_speichern: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_i, 'chat', 'Noch was', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('3d: chat_anlegen chat: %s', v_fehler);
  -- 067: Vormerken gibt es nicht mehr (die Warteschlange laeuft ueber pult_chat_anlegen, 3d deckt sie ab)
  ASSERT to_regprocedure('marketing.pult_chat_vormerken(uuid, text, jsonb)') IS NULL, '3e: Vormerken entfernt (067)';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('3f: bild_auftrag: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 55, 'ueberarbeiten');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('3g: bild_auftrag 7-arg: %s', v_fehler);
  -- Export (aendert keine Fassung) ist bei eingereicht nicht gesperrt (Brief: Sperre nur Art chat)
  a := marketing.pult_chat_anlegen(v_i, 'export', '', '{}');
  ASSERT (SELECT art FROM marketing.chat_auftraege WHERE id = a) = 'export', '3h: Export bei eingereicht';
  ASSERT (marketing.pult_chat_stoppen(v_i, 'verwerfen'))->>'abgeschlossen' = 'true', '3h: aufgeraeumt';
  -- Review Focus 1: ein am PC noch laufender Bild-Auftrag wird fertig -> keine neue Fassung, sauber verworfen
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 minutes' WHERE id = bi;
  j := marketing.pult_bild_einsetzen(bi, '{"held":"medien:nl-0123abcd-held.jpg"}', '');
  ASSERT j->'fassung' = 'null'::jsonb AND j->'eingesetzt' = '[]'::jsonb, format('3i: einsetzen: %s', j);
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bi;
  ASSERT r.status = 'verworfen' AND r.befund = 'Liegt zur Freigabe – erst zurückziehen' AND r.vergeben_bis IS NULL,
         format('3i: Auftrag: %s / %s', r.status, r.befund);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = v_n, '3i: keine neue Fassung';
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_i) = 'eingereicht', '3: Status bleibt eingereicht';

  -- 4) Verbotenes bei eingereicht: aeltere Fassung freigeben/zurueckgeben, leerer Kommentar
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_i, v_n - 1, 'freigeben', 'probe', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = format('Inzwischen gibt es Fassung %s – bitte neu laden', v_n), format('4a: aeltere freigeben: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, v_n - 1, 'probe', 'Text'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = format('Inzwischen gibt es Fassung %s – bitte neu laden', v_n), format('4b: aeltere zurueckgeben: %s', v_fehler);
  v_fehler := NULL;   -- Review Focus 3
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, v_n, 'probe', '  '); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Bitte sag kurz, was fehlt', format('4c: Leerraum: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, v_n, 'probe', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Bitte sag kurz, was fehlt', format('4d: NULL: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, v_n, 'probe', repeat('a', 2001)); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '4e: > 2000 Zeichen muss scheitern';
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_i) = 'eingereicht', '4: Status bleibt eingereicht';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.rueckmeldungen WHERE inhalt = v_i), '4: keine Rueckmeldung';

  -- 5) Zurueckgeben (eingereicht -> entwurf + Rueckmeldung)
  rm1 := marketing.pult_zurueckgeben(v_i, v_n, 'probe', '  Überschrift kürzer ');
  SELECT * INTO r FROM marketing.rueckmeldungen WHERE id = rm1;
  ASSERT r.inhalt = v_i AND r.fassung = v_n AND r.text = 'Überschrift kürzer' AND r.von = 'probe'
     AND r.am = now() AND r.erledigt_am IS NULL, format('5: Rueckmeldung: %s', row_to_json(r));
  SELECT * INTO r FROM marketing.inhalte WHERE id = v_i;
  ASSERT r.status = 'entwurf' AND r.eingereichte_fassung IS NULL AND r.eingereicht_am IS NULL AND r.eingereicht_von IS NULL,
         format('5: Inhalt: %s', row_to_json(r));
  -- Verbotenes aus entwurf
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, v_n, 'probe', 'nochmal'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('5a: zurueckgeben aus entwurf: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckziehen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('5b: zurueckziehen aus entwurf: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_i, v_n, 'freigeben', 'probe', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('5c: freigeben aus entwurf: %s', v_fehler);
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_i) = 'entwurf', '5: Status bleibt entwurf';

  -- 6) Im Entwurf wieder bearbeitbar; erneutes Einreichen erledigt offene Rueckmeldungen
  v_n := marketing.pult_bloecke_speichern(v_i, v_n, 'Probe 063', '', b, 'betreiber', false);
  ASSERT v_n = 3, '6: speichern wieder moeglich';
  ASSERT (SELECT count(*) FROM marketing.rueckmeldungen WHERE inhalt = v_i AND erledigt_am IS NULL) = 1, '6: eine offen';
  ASSERT marketing.pult_einreichen(v_i, 'probe') = v_n, '6: einreichen Fassung 3';
  ASSERT (SELECT erledigt_am FROM marketing.rueckmeldungen WHERE id = rm1) = now(), '6: Rueckmeldung erledigt';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.rueckmeldungen WHERE inhalt = v_i AND erledigt_am IS NULL), '6: keine offen';

  -- 7) Zurueckziehen (eingereicht -> entwurf, ohne Rueckmeldung)
  ASSERT marketing.pult_zurueckziehen(v_i, 'probe') = 'entwurf', '7: zurueckziehen';
  SELECT * INTO r FROM marketing.inhalte WHERE id = v_i;
  ASSERT r.status = 'entwurf' AND r.eingereichte_fassung IS NULL AND r.eingereicht_am IS NULL AND r.eingereicht_von IS NULL,
         format('7: Felder leer: %s', row_to_json(r));
  ASSERT (SELECT count(*) FROM marketing.rueckmeldungen WHERE inhalt = v_i) = 1, '7: keine neue Rueckmeldung';
  -- M3: ein zweiter Tab (Sales/Editor) trifft den zurueckgezogenen Newsletter
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_i, v_n, 'freigeben', 'chef', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('7a: freigeben nach zurueckziehen: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, v_n, 'chef', 'Preis fehlt'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('7b: zurueckgeben nach zurueckziehen: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckziehen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('7c: zurueckziehen nach zurueckziehen: %s', v_fehler);
  ASSERT (SELECT count(*) FROM marketing.rueckmeldungen WHERE inhalt = v_i) = 1, '7: weiter keine neue Rueckmeldung';

  -- 8) Review Focus 2 (DB-Seite): zweiter Tab gibt Fassung 3 frei, nachdem zurueckgezogen und neu gespeichert wurde
  v_n := marketing.pult_bloecke_speichern(v_i, v_n, 'Probe 063', '', b, 'betreiber', false);
  ASSERT v_n = 4, '8: Fassung 4';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_i, 3, 'freigeben', 'probe', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Inzwischen gibt es Fassung 4 – bitte neu laden', format('8: veraltet: %s', v_fehler);

  -- 9) Freigeben (eingereicht -> freigegeben), nur die eingereichte Fassung
  ASSERT marketing.pult_einreichen(v_i, 'probe') = 4, '9: einreichen 4';
  ASSERT marketing.pult_entscheiden(v_i, 4, 'freigeben', 'chef', NULL) = 'freigegeben', '9: freigeben';
  SELECT * INTO r FROM marketing.inhalte WHERE id = v_i;
  ASSERT r.status = 'freigegeben' AND r.freigegebene_fassung = 4 AND r.entschieden_von = 'chef'
     AND r.entschieden_am = now() AND r.eingereichte_fassung = 4, format('9: Felder: %s', row_to_json(r));
  -- Verbotenes aus freigegeben
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden (freigegeben)', format('9a: einreichen aus freigegeben: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_i, 4, 'freigeben', 'chef', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden (freigegeben)', format('9b: zweiter Tab: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_i, 4, 'ablehnen', 'chef', 'zu spaet'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden (freigegeben)', format('9c: ablehnen aus freigegeben: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckgeben(v_i, 4, 'chef', 'doch nicht'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden (freigegeben)', format('9d: zurueckgeben aus freigegeben: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_zurueckziehen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden (freigegeben)', format('9e: zurueckziehen aus freigegeben: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_i, 'chat', 'Noch was', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Nur Newsletter-Entwürfe', format('9f: chat bei freigegeben: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, 4, 'Probe 063', '', b, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Nur Entwuerfe lassen sich bearbeiten', format('9g: speichern bei freigegeben: %s', v_fehler);
  -- Export-Auftrag bei freigegeben erlaubt
  a := marketing.pult_chat_anlegen(v_i, 'export', '', '{"slug":"probe"}');
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a;
  ASSERT r.art = 'export' AND r.status = 'offen' AND r.fassung_vorher = 4, format('9h: Export: %s', row_to_json(r));
  -- Bild-Auftrag laeuft noch, als freigegeben wurde -> keine neue Fassung
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 minutes' WHERE id = bi;
  j := marketing.pult_bild_einsetzen(bi, '{"held":"medien:nl-0123abcd-held.jpg"}', '');
  ASSERT j->'fassung' = 'null'::jsonb, format('9i: einsetzen bei freigegeben: %s', j);
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bi;
  ASSERT r.status = 'verworfen' AND r.befund = 'Inhalt inzwischen entschieden', format('9i: %s / %s', r.status, r.befund);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = 4, '9i: keine neue Fassung';

  -- 10) Verwerfen aus entwurf und aus eingereicht; freigeben aus entwurf verboten
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_entscheiden(v_k, 1, 'freigeben', 'chef', NULL); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Wurde zurückgezogen – bitte neu laden', format('10a: freigeben aus entwurf: %s', v_fehler);
  ASSERT marketing.pult_entscheiden(v_j, 1, 'ablehnen', 'chef', 'passt nicht') = 'abgelehnt', '10b: verwerfen aus entwurf';
  ASSERT marketing.pult_einreichen(v_k, 'probe') = 1, '10c: K einreichen';
  -- T1: bei eingereicht liegende Auftraege (Export erlaubt; Vormerkung/Bild-Rest direkt angelegt)
  a := marketing.pult_chat_anlegen(v_k, 'export', '', '{}');
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, status)
  VALUES (v_k, 'chat', 'liegengeblieben', '{}', 'wartet') RETURNING id INTO rm1;
  INSERT INTO marketing.bild_auftraege (inhalt, platz, nur_leere, hinweis, grund_fassung, urheber)
  VALUES (v_k, NULL, true, '', 1, 'system') RETURNING id INTO bo;
  ASSERT marketing.pult_entscheiden(v_k, 1, 'ablehnen', 'chef', 'passt nicht') = 'abgelehnt', '10c: verwerfen aus eingereicht';
  SELECT * INTO r FROM marketing.inhalte WHERE id = v_k;
  ASSERT r.status = 'abgelehnt' AND r.grund = 'passt nicht' AND r.freigegebene_fassung IS NULL, '10c: Felder';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a;
  ASSERT r.status = 'fehler' AND r.antwort = 'Newsletter wurde entschieden' AND r.vergeben_bis IS NULL,
         format('10f: Export beendet: %s / %s', r.status, r.antwort);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = rm1;
  ASSERT r.status = 'fehler' AND r.antwort = 'Newsletter wurde entschieden', format('10f: Vormerkung beendet: %s', r.status);
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bo;
  ASSERT r.status = 'verworfen' AND r.befund = 'Inhalt entschieden', format('10f: Bild verworfen: %s / %s', r.status, r.befund);
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = v_k AND status IN ('offen','in_arbeit','wartet')),
         '10f: kein Chat-/Export-Auftrag lebt weiter';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_j, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden (abgelehnt)', format('10d: einreichen aus abgelehnt: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_j, 'export', '', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Nur Newsletter-Entwürfe', format('10e: Export bei abgelehnt: %s', v_fehler);

  -- 11) Die Regel der Tabelle: leerer Text geht auch direkt nicht
  v_fehler := NULL;
  BEGIN INSERT INTO marketing.rueckmeldungen (inhalt, fassung, text, von) VALUES (v_j, 1, '   ', 'probe');
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '11: leerer Text an der Tabellenregel';

  -- 12) F4: ein Bild-Auftrag mit abgelaufener Vergabe (PC aus) sperrt das Einreichen nicht, er wird
  --     mit den wartenden verworfen; M4: eine liegengebliebene Vormerkung endet mit dem Einreich-Hinweis
  SELECT inhalt INTO v_i FROM _p063 WHERE k = 'e';
  bi := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch');
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 minutes'
   WHERE id = bi;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_einreichen(v_i, 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ein Bild wird gerade erzeugt', format('12a: gueltige Vergabe sperrt: %s', v_fehler);
  UPDATE marketing.bild_auftraege SET vergeben_bis = now() - interval '1 minute' WHERE id = bi;   -- abgelaufen
  -- 067: die Vormerkung gibt es nicht mehr; eine wartende Runde rueckte beim Aufraeumen nach und sperrte das Einreichen
  -- wie jede laufende. Die Warteschlange deckt verify_067 ab, hier bleibt nur der abgelaufene Bild-Auftrag.
  ASSERT marketing.pult_einreichen(v_i, 'probe') = 1, '12b: einreichen trotz abgelaufener Vergabe';
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bi;
  ASSERT r.status = 'verworfen' AND r.befund = 'Newsletter eingereicht' AND r.vergeben_bis IS NULL,
         format('12b: abgelaufener Auftrag verworfen: %s / %s', r.status, r.befund);
END $$;
SELECT 'verify_063 ok' AS ergebnis;
