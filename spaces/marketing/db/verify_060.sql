-- Nachweise fuer 060, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Kein Ueberspringen: fehlt ein passender Entwurf, wird einer angelegt.

-- Probe-Inhalt: aeltester Newsletter-Entwurf, dessen neueste Fassung ein gueltiges Bloecke-Dokument ist.
CREATE TEMP TABLE _p060 ON COMMIT DROP AS
SELECT i.id AS inhalt
  FROM marketing.inhalte i
  JOIN marketing.inhalt_fassungen f
    ON f.inhalt = i.id AND f.fassung = (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = i.id)
 WHERE i.art = 'newsletter' AND i.status = 'entwurf' AND f.format = 'bloecke'
   AND marketing.pult_bloecke_fehler(f.bloecke) IS NULL
 ORDER BY i.erstellt_am LIMIT 1;

DO $$ DECLARE v_i uuid; BEGIN
  IF NOT EXISTS (SELECT 1 FROM _p060) THEN
    INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 060')
    RETURNING id INTO v_i;
    PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 060', '',
      '{"root":{"type":"EmailLayout","data":{"childrenIds":[]}}}'::jsonb, 'betreiber', false);
    INSERT INTO _p060 VALUES (v_i);
  END IF;
  ASSERT (SELECT count(*) FROM _p060) = 1, 'genau ein Probe-Inhalt';
END $$;

-- 0) Umbenennung, Huellen und CHECK-Regeln
DO $$ BEGIN
  ASSERT to_regprocedure('marketing._pult_bloecke_fehler_058(jsonb)') IS NOT NULL, 'Basis-Pruefer umbenannt';
  ASSERT to_regprocedure('marketing._pult_bloecke_speichern_053(uuid, int, text, text, jsonb, text, boolean)') IS NOT NULL,
         'Basis-Speichern umbenannt';
  ASSERT (SELECT provolatile FROM pg_proc WHERE oid = 'marketing.pult_bloecke_fehler(jsonb)'::regprocedure) = 'i',
         'Huelle pult_bloecke_fehler ist IMMUTABLE';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname IN ('inhalt_fassungen_bloecke_gueltig','newsletter_vorlagen_bloecke_gueltig',
                             'newsletter_vorlagen_fassungen_bloecke_gueltig')
             AND pg_get_constraintdef(oid) LIKE '%marketing.pult_bloecke_fehler(bloecke)%') = 3,
         'CHECK-Regeln nutzen die neue Huelle (nicht _pult_bloecke_fehler_058)';
END $$;

-- 1-3) Gestaltung im Pruefer
DO $$ DECLARE v_i uuid; b jsonb; d jsonb; v_g jsonb; v_f text; BEGIN
  SELECT inhalt INTO v_i FROM _p060;
  SELECT bloecke INTO b FROM marketing.inhalt_fassungen WHERE inhalt = v_i ORDER BY fassung DESC LIMIT 1;
  ASSERT b IS NOT NULL, 'neueste Fassung hat Bloecke';
  v_g := '{"version":1,"format":"quer","hintergrund":"#FFFFFF","ebenen":[]}'::jsonb;
  d := jsonb_set(b, '{root,data,childrenIds}', coalesce(b #> '{root,data,childrenIds}', '[]'::jsonb) || '"gs_probe"'::jsonb)
       || jsonb_build_object('gs_probe', jsonb_build_object('type', 'Image', 'data', jsonb_build_object('style', '{}'::jsonb,
            'props', jsonb_build_object('url', NULL, 'alt', 'x', 'width', 600, 'height', 400, 'gestaltung', v_g))));
  v_f := marketing.pult_bloecke_fehler(d);
  ASSERT v_f IS NULL, format('1: gueltige Gestaltung abgelehnt: %s', v_f);

  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,gestaltung,format}', '"a4"'));
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2: format a4 nicht abgelehnt: %s', v_f);

  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,gestaltung,version}', '2'));
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2b: version 2 nicht abgelehnt: %s', v_f);
  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,gestaltung,ebenen}', '{}'));
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2c: ebenen kein Array nicht abgelehnt: %s', v_f);
  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,gestaltung,ebenen}',
           (SELECT jsonb_agg(jsonb_build_object('id', 'e' || n)) FROM generate_series(1, 21) n)));
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2d: 21 Ebenen nicht abgelehnt: %s', v_f);
  v_f := marketing.pult_bloecke_fehler(d #- '{gs_probe,data,props,alt}');
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2e: ohne alt nicht abgelehnt: %s', v_f);
  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,alt}', to_jsonb(repeat('a', 201))));
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2f: alt > 200 nicht abgelehnt: %s', v_f);
  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,gestaltung}', '"quer"'));
  ASSERT v_f LIKE '%Gestaltung ungültig%', format('2g: Gestaltung kein Objekt nicht abgelehnt: %s', v_f);
  v_f := marketing.pult_bloecke_fehler(jsonb_set(d, '{gs_probe,data,props,gestaltung}', 'null'));
  ASSERT v_f IS NULL, format('2h: gestaltung null muss erlaubt sein: %s', v_f);

  d := jsonb_set(b, '{root,data,childrenIds}', coalesce(b #> '{root,data,childrenIds}', '[]'::jsonb) || '"gs_text"'::jsonb)
       || jsonb_build_object('gs_text', jsonb_build_object('type', 'Text', 'data', jsonb_build_object('style', '{}'::jsonb,
            'props', jsonb_build_object('text', 'x', 'gestaltung', v_g))));
  v_f := marketing.pult_bloecke_fehler(d);
  ASSERT v_f LIKE '%Gestaltung nur im Bild-Block%', format('3: Gestaltung am Text nicht abgelehnt: %s', v_f);
  ASSERT marketing.pult_bloecke_fehler('[]'::jsonb) = 'Das Dokument muss ein JSON-Objekt sein', '3b: Basisgrund kommt durch';
END $$;

-- 4-9) Chat-Auftraege und Sperre
DO $$ DECLARE
  v_i uuid; b jsonb; d jsonb; v_n int; v_neu int; v_betreff text; v_vt text;
  v_a uuid; v_a2 uuid; v_fehler text; j jsonb; r record;
BEGIN
  SELECT inhalt INTO v_i FROM _p060;
  SELECT fassung, bloecke, felder->>'betreff', coalesce(felder->>'vorschautext', '')
    INTO v_n, b, v_betreff, v_vt
    FROM marketing.inhalt_fassungen WHERE inhalt = v_i ORDER BY fassung DESC LIMIT 1;
  d := jsonb_set(b, '{root,data,childrenIds}', coalesce(b #> '{root,data,childrenIds}', '[]'::jsonb) || '"gs_probe"'::jsonb)
       || jsonb_build_object('gs_probe', jsonb_build_object('type', 'Image', 'data', jsonb_build_object('style', '{}'::jsonb,
            'props', jsonb_build_object('url', NULL, 'alt', 'x', 'width', 600, 'height', 400,
              'gestaltung', '{"version":1,"format":"quer","hintergrund":"#FFFFFF","ebenen":[]}'::jsonb))));

  -- 4) anlegen, zweiter Auftrag scheitert
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_i, 'chat', '   ', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '4a: Chat ohne Nachricht muss scheitern';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_i, 'chat', repeat('a', 2001), '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '4b: Nachricht > 2000 muss scheitern';
  v_a := marketing.pult_chat_anlegen(v_i, 'chat', 'Hallo', '{}');
  ASSERT v_a IS NOT NULL, '4: anlegen liefert uuid';
  ASSERT (SELECT fassung_vorher FROM marketing.chat_auftraege WHERE id = v_a) = v_n, '4: fassung_vorher = neueste';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_i, 'chat', 'Nochmal', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%arbeitet gerade%', format('4: zweiter Auftrag nicht abgelehnt: %s', v_fehler);

  -- 5) Sperre fuer den Betreiber, nicht fuer den Agenten
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, v_n, v_betreff, v_vt, d, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%Der Assistent arbeitet gerade%', format('5: Betreiber nicht gesperrt: %s', v_fehler);
  v_neu := marketing.pult_bloecke_speichern(v_i, v_n, v_betreff, v_vt, d, 'agent', false);
  ASSERT v_neu = v_n + 1, '5: Agent darf speichern';
  v_n := v_neu;

  -- 6) naechster
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = v_a, '6: naechster liefert den Auftrag';
  ASSERT jsonb_typeof(j->'bloecke') = 'object', '6: bloecke dabei';
  ASSERT jsonb_typeof(j->'verlauf') = 'array', '6: verlauf ist Array';
  ASSERT (j->>'fassung')::int = v_n, '6: fassung = neueste';
  ASSERT j->>'nachricht' = 'Hallo' AND j->>'art' = 'chat' AND j->>'betreff' = v_betreff, '6: Felder';
  ASSERT jsonb_typeof(j->'pflichtteil') = 'object' AND j->>'mandant' IS NOT NULL AND j->>'titel' IS NOT NULL, '6: Mandant/Titel';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = v_a;
  ASSERT r.status = 'in_arbeit' AND r.versuche = 1 AND r.vergeben_bis > now(), '6: in_arbeit vergeben';
  ASSERT marketing.pult_chat_naechster('5 minutes') IS NULL, '6: kein weiterer Auftrag';
  ASSERT marketing.pult_chat_verlaengern(v_a, '5 minutes'), '6: verlaengern';

  -- 7) fertig
  j := marketing.pult_chat_fertig(v_a, 'Erledigt', d, '[]', '{}');
  ASSERT (j->>'fassung')::int = v_n + 1, format('7: fassung = neueste+1, war %s', j);
  ASSERT (SELECT urheber FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = v_n + 1) = 'agent', '7: Urheber agent';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = v_a;
  ASSERT r.status = 'fertig' AND r.antwort = 'Erledigt' AND r.fassung_nachher = v_n + 1 AND r.vergeben_bis IS NULL, '7: fertig';
  v_n := v_n + 1;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig(v_a, 'nochmal', NULL, '[]', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%nicht (mehr) in Arbeit%', format('7b: doppeltes fertig nicht abgelehnt: %s', v_fehler);
  -- Betreiber darf wieder speichern
  v_neu := marketing.pult_bloecke_speichern(v_i, v_n, v_betreff, v_vt, d, 'betreiber', false);
  ASSERT v_neu = v_n + 1, '7c: Betreiber speichert nach fertig';
  v_n := v_neu;

  -- 8) Nicht abgeholt in 2 Minuten
  v_a2 := marketing.pult_chat_anlegen(v_i, 'chat', 'Zweite', '{}');
  UPDATE marketing.chat_auftraege SET erstellt_am = now() - interval '3 minutes' WHERE id = v_a2;
  v_neu := marketing.pult_bloecke_speichern(v_i, v_n, v_betreff, v_vt, d, 'betreiber', false);
  ASSERT v_neu = v_n + 1, '8a: nie abgeholter Auftrag (> 2 min) sperrt nicht';
  v_n := v_neu;
  PERFORM marketing.pult_chat_aufraeumen(v_i);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = v_a2;
  ASSERT r.status = 'fehler' AND r.antwort LIKE '%gerade aus%', format('8: nicht abgelaufen: %s / %s', r.status, r.antwort);

  -- 9) Nach Fehler wieder anlegbar; Verlauf traegt die letzten Nachrichten (aelteste zuerst)
  v_a := marketing.pult_chat_anlegen(v_i, 'chat', 'Dritte', '{"auswahl":"gs_probe"}');
  ASSERT v_a IS NOT NULL, '9: nach Fehler wieder anlegbar';
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = v_a, '9: naechster liefert den neuen Auftrag';
  ASSERT j->'kontext'->>'auswahl' = 'gs_probe', '9: kontext';
  ASSERT jsonb_array_length(j->'verlauf') = 2, format('9: verlauf hat 2 Eintraege: %s', j->'verlauf');
  -- in der Probe ist now() konstant: "Zweite" liegt 3 min zurueck und ist damit der aelteste Eintrag
  ASSERT j->'verlauf'->0->>'nachricht' = 'Zweite', format('9: verlauf[0] (aeltester zuerst): %s', j->'verlauf'->0);
  ASSERT j->'verlauf'->1 = '{"nachricht":"Hallo","antwort":"Erledigt"}'::jsonb, format('9: verlauf[1]: %s', j->'verlauf'->1);

  -- Lease abgelaufen: erst wieder offen, nach dem zweiten Versuch fehler
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = v_a;
  v_neu := marketing.pult_bloecke_speichern(v_i, v_n, v_betreff, v_vt, d, 'betreiber', false);
  ASSERT v_neu = v_n + 1, '10: abgelaufene Vergabe sperrt nicht';
  v_n := v_neu;
  PERFORM marketing.pult_chat_aufraeumen(NULL);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = v_a;
  ASSERT r.status = 'offen' AND r.vergeben_bis IS NULL AND r.versuche = 1, format('10: wieder offen: %s', r.status);
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = v_a AND (SELECT versuche FROM marketing.chat_auftraege WHERE id = v_a) = 2, '10: zweiter Versuch';
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = v_a;
  PERFORM marketing.pult_chat_aufraeumen(v_i);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = v_a;
  ASSERT r.status = 'fehler' AND r.antwort LIKE '%nicht fertig geworden%', format('10: nach 2 Versuchen fehler: %s', r.status);

  -- zurueck
  v_a := marketing.pult_chat_anlegen(v_i, 'export', '', '{}');
  j := marketing.pult_chat_naechster('5 minutes');
  ASSERT (j->>'id')::uuid = v_a AND j->>'art' = 'export', '11: Export-Auftrag ohne Nachricht';
  ASSERT marketing.pult_chat_zurueck(v_a, 'Ging nicht') = 'fehler', '11: zurueck liefert fehler';
  ASSERT (SELECT antwort FROM marketing.chat_auftraege WHERE id = v_a) = 'Ging nicht', '11: Antwort gesetzt';

  -- Entscheiden: laufende Auftraege werden fehler
  v_a := marketing.pult_chat_anlegen(v_i, 'chat', 'Vierte', '{}');
  UPDATE marketing.inhalte SET status = 'abgelehnt', entschieden_von = 'probe', entschieden_am = now(), grund = 'probe'
   WHERE id = v_i;
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = v_a;
  ASSERT r.status = 'fehler' AND r.antwort = 'Newsletter wurde entschieden', format('12: Entscheiden: %s', r.status);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_anlegen(v_i, 'chat', 'Fuenfte', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%Nur Newsletter-Entwürfe%', format('12: entschiedener Inhalt nicht abgelehnt: %s', v_fehler);
END $$;
SELECT 'verify_060 ok' AS ergebnis;
