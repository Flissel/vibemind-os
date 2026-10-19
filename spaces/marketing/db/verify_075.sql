-- Nachweise fuer 075, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Inhalte (wie verify_063), "in Arbeit" wird direkt gesetzt. In der Probe ist now() konstant.
-- Darf mehrfach in derselben Probe laufen (z. B. nach einem Replay von 063/067/073).

DROP TABLE IF EXISTS _p075;
CREATE TEMP TABLE _p075 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS inhalt LIMIT 0;

DO $$ DECLARE v_i uuid; v_f uuid; v_a uuid; d jsonb; v_n int; BEGIN
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 075')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 075', '', d, 'betreiber', false);
  PERFORM marketing.pult_bloecke_speichern(v_i, 1, 'Probe 075', '',
    jsonb_set(d, '{t,data,props,text}', '"zwei"'), 'betreiber', false);
  INSERT INTO marketing.bild_auftraege (inhalt, grund_fassung, urheber, status) VALUES (v_i, 1, 'mensch', 'fertig');
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status) VALUES (v_i, 'chat', 'Probe', 'fertig');
  INSERT INTO marketing.rueckmeldungen (inhalt, fassung, text, von) VALUES (v_i, 1, 'Probe-Rueckmeldung', 'probe');
  PERFORM marketing.plan_anlegen('vibemind', current_date + 2, 'newsletter', 'Probe 075', '', v_i, 'betreiber');

  -- freigegeben: einreichen, freigeben
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 075 F')
  RETURNING id INTO v_f;
  PERFORM marketing.pult_bloecke_speichern(v_f, 0, 'Probe 075 F', '', d, 'betreiber', false);
  v_n := marketing.pult_einreichen(v_f, 'probe');
  PERFORM marketing.pult_entscheiden(v_f, v_n, 'freigeben', 'probe', NULL);

  -- abgelehnt (verworfen)
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 075 A')
  RETURNING id INTO v_a;
  PERFORM marketing.pult_bloecke_speichern(v_a, 0, 'Probe 075 A', '', d, 'betreiber', false);
  PERFORM marketing.pult_entscheiden(v_a, 1, 'ablehnen', 'probe', 'Testleiche');

  INSERT INTO _p075 VALUES ('i', v_i), ('f', v_f), ('a', v_a);
END $$;

-- 0) Struktur
DO $$ DECLARE r record; BEGIN
  ASSERT (SELECT count(*) FROM pg_proc WHERE proname = 'pult_inhalt_loeschen'
           AND pronamespace = 'marketing'::regnamespace) = 1, '0: genau eine pult_inhalt_loeschen';
  ASSERT to_regprocedure('marketing.pult_inhalt_loeschen(uuid, text, text)') IS NOT NULL, '0: Signatur';
  ASSERT (SELECT p.proname FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
           WHERE t.tgrelid = 'marketing.inhalt_fassungen'::regclass
             AND t.tgname = 'trg_inhalt_fassung_unveraenderlich') = '_inhalt_fassung_unveraenderlich',
         '0: inhalt_fassungen nutzt die neue Trigger-Funktion';
  ASSERT (SELECT p.proname FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
           WHERE t.tgrelid = 'marketing.layout_fassungen'::regclass
             AND t.tgname = 'trg_layout_fassung_unveraenderlich') = '_fassung_unveraenderlich',
         '0: layout_fassungen bleibt bei _fassung_unveraenderlich';
  ASSERT (SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'marketing.inhalt_fassungen'::regclass
           AND tgname = 'trg_inhalt_fassung_unveraenderlich') = 'O', '0: Trigger eingeschaltet';
  -- Jede Fremdschluessel-Verweis auf inhalte ohne CASCADE/SET NULL raeumt die Funktion selbst ab
  FOR r IN SELECT c.conrelid::regclass::text AS tab, c.confdeltype FROM pg_constraint c
            WHERE c.contype = 'f' AND c.confrelid = 'marketing.inhalte'::regclass LOOP
    ASSERT r.confdeltype IN ('c', 'n')
           OR r.tab IN ('marketing.inhalt_fassungen', 'marketing.bild_auftraege', 'marketing.chat_auftraege'),
           format('0: %s zeigt ohne CASCADE/SET NULL auf inhalte (%s)', r.tab, r.confdeltype);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT NOT has_function_privilege('sales_app', 'marketing.pult_inhalt_loeschen(uuid,text,text)', 'EXECUTE'),
           '0: sales_app darf nicht loeschen';
  END IF;
  -- Die API verbindet sich wie die Probe als SUPABASE_DB_USER (Vorgabe supabase_admin, Eigentuemerin) -
  -- diese Rolle muss die Funktion aufrufen duerfen. service_role bekommt nach REVOKE ... FROM PUBLIC kein
  -- EXECUTE (wie bei 071/074; 003 setzt keine Default Privileges fuer supabase_admin-Objekte).
  ASSERT has_function_privilege(current_user, 'marketing.pult_inhalt_loeschen(uuid,text,text)', 'EXECUTE'),
         '0: die API-Rolle (Probe-Rolle) darf loeschen';
END $$;

DO $$ DECLARE v_i uuid; v_f uuid; v_a uuid; v_fehler text; v_titel text; c uuid; b uuid; n int; BEGIN
  SELECT inhalt INTO v_i FROM _p075 WHERE k = 'i';
  SELECT inhalt INTO v_f FROM _p075 WHERE k = 'f';
  SELECT inhalt INTO v_a FROM _p075 WHERE k = 'a';
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = 2, 'Probe: zwei Fassungen';

  -- a) Fassungen bleiben ausserhalb der Funktion unveraenderlich
  v_fehler := NULL;
  BEGIN DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_i; EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('a: direktes DELETE: %s', v_fehler);
  -- falsche id in der Einstellung erlaubt nichts; UPDATE nie
  v_fehler := NULL;
  BEGIN
    PERFORM set_config('marketing.inhalt_loeschen', v_f::text, true);
    DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_i;
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('a: fremde id: %s', v_fehler);
  v_fehler := NULL;
  BEGIN
    PERFORM set_config('marketing.inhalt_loeschen', v_i::text, true);
    UPDATE marketing.inhalt_fassungen SET urheber = 'agent' WHERE inhalt = v_i;
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('a: UPDATE: %s', v_fehler);
  ASSERT coalesce(current_setting('marketing.inhalt_loeschen', true), '') = '', 'a: Einstellung zurueckgerollt';

  -- unbekannter Inhalt
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(gen_random_uuid(), 'vibemind', 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannter Inhalt', format('unbekannt: %s', v_fehler);

  -- b) fremde Firma (auch ohne Firma)
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, 'fin2gether', 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Gehört einer anderen Firma', format('b: fremd: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, NULL, 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Gehört einer anderen Firma', format('b: ohne Firma: %s', v_fehler);

  -- c) laufender Assistent sperrt, abgelaufene Vergabe nicht
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis)
  VALUES (v_i, 'chat', 'laeuft', 'in_arbeit', 1, now() + interval '5 min') RETURNING id INTO c;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, 'vibemind', 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('c: Chat: %s', v_fehler);
  UPDATE marketing.chat_auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = c;
  -- laufendes Bild sperrt
  INSERT INTO marketing.bild_auftraege (inhalt, grund_fassung, urheber, status, versuche, vergeben_bis)
  VALUES (v_i, 2, 'mensch', 'in_arbeit', 1, now() + interval '5 min') RETURNING id INTO b;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, 'vibemind', 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ein Bild wird gerade erzeugt', format('c: Bild: %s', v_fehler);
  UPDATE marketing.bild_auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = b;
  ASSERT (SELECT count(*) FROM marketing.inhalte WHERE id = v_i) = 1, 'c: abgelehnte Aufrufe loeschen nichts';
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = 2, 'c: Fassungen unberuehrt';

  -- d) eingereicht und freigegeben sperren
  -- Vorher die Probe-Auftraege abschliessen (einreichen verlangt keinen laufenden Assistenten)
  UPDATE marketing.chat_auftraege SET status = 'fehler', vergeben_bis = NULL WHERE id = c;
  PERFORM marketing.pult_einreichen(v_i, 'probe');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, 'vibemind', 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('d: eingereicht: %s', v_fehler);
  -- I1: selbst gesetzte Einstellung oeffnet eingereichte Fassungen nicht
  v_fehler := NULL;
  BEGIN
    PERFORM set_config('marketing.inhalt_loeschen', v_i::text, true);
    DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_i;
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('d: eingereicht, Einstellung gefaelscht: %s', v_fehler);
  PERFORM marketing.pult_zurueckziehen(v_i, 'probe');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_f, 'vibemind', 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Freigegebene Inhalte bleiben erhalten', format('d: freigegeben: %s', v_fehler);
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_f) = 1, 'd: freigegebene Fassung bleibt';
  -- I1: Einstellung selbst gesetzt (wie es jede Rolle mit DELETE-Recht koennte) - freigegebene Fassung bleibt hart
  v_fehler := NULL;
  BEGIN
    PERFORM set_config('marketing.inhalt_loeschen', v_f::text, true);
    DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_f;
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('d: freigegeben, Einstellung gefaelscht: %s', v_fehler);
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_f) = 1, 'd: freigegebene Fassung bleibt (I1)';
  ASSERT coalesce(current_setting('marketing.inhalt_loeschen', true), '') = '', 'd: Einstellung zurueckgerollt';

  -- e) Erfolg: Titel zurueck, alles Abhaengige weg, Plan verliert nur die Verknuepfung
  v_titel := marketing.pult_inhalt_loeschen(v_i, 'vibemind', 'probe');
  ASSERT v_titel = 'Probe 075', format('e: Titel: %s', v_titel);
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_i), 'e: inhalte';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalt_fassungen WHERE inhalt = v_i), 'e: inhalt_fassungen';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.bild_auftraege WHERE inhalt = v_i), 'e: bild_auftraege';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.chat_auftraege WHERE inhalt = v_i), 'e: chat_auftraege';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.rueckmeldungen WHERE inhalt = v_i), 'e: rueckmeldungen';
  ASSERT EXISTS (SELECT 1 FROM marketing.kampagnen_plan WHERE titel = 'Probe 075' AND inhalt IS NULL),
         'e: Plan-Eintrag bleibt ohne Verknuepfung';

  -- g) Einstellung ist danach nicht mehr die id
  ASSERT coalesce(current_setting('marketing.inhalt_loeschen', true), '') = '',
         format('g: Einstellung: %s', current_setting('marketing.inhalt_loeschen', true));
  v_fehler := NULL;
  BEGIN DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_f; EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('g: danach wieder gesperrt: %s', v_fehler);

  -- Zweiter Aufruf (Doppelklick): Unbekannter Inhalt
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, 'vibemind', 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannter Inhalt', format('Doppelklick: %s', v_fehler);

  -- f) verworfen (abgelehnt) ist loeschbar
  ASSERT marketing.pult_inhalt_loeschen(v_a, 'vibemind', 'probe') = 'Probe 075 A', 'f: abgelehnt loeschbar';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_a), 'f: weg';
END $$;

-- h) Alter Freigabeweg: Inhalt aus einem offenen broadcast_proposal sperrt, nach dem Ablehnen dort nicht mehr
DO $$ DECLARE v_kanal text; v_p uuid; v_i uuid; v_fehler text; BEGIN
  SELECT channel INTO v_kanal FROM marketing.channel_config ORDER BY (channel = 'email') DESC, channel LIMIT 1;
  IF v_kanal IS NULL THEN RAISE NOTICE 'h: kein Kanal - uebersprungen'; RETURN; END IF;
  INSERT INTO marketing.broadcast_proposals (channel, status, draft_subject, draft_body_text, created_by)
  VALUES (v_kanal, 'draft', 'Probe 075 P', 'Probe 075 Text', 'probe') RETURNING id INTO v_p;
  SELECT id INTO v_i FROM marketing.inhalte WHERE herkunft_proposal = v_p;
  ASSERT v_i IS NOT NULL, 'h: Bruecke legt Inhalt an';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_i, 'vibemind', 'probe'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt noch im alten Freigabeweg – dort erst ablehnen', format('h: offen: %s', v_fehler);
  UPDATE marketing.broadcast_proposals
     SET status = 'rejected', rejected_by = 'probe', rejected_at = now(), rejection_reason = 'Probe'
   WHERE id = v_p;
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_i) = 'abgelehnt', 'h: Bruecke spiegelt Ablehnung';
  ASSERT marketing.pult_inhalt_loeschen(v_i, 'vibemind', 'probe') IS NOT NULL, 'h: nach Ablehnung loeschbar';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_i), 'h: weg';
  ASSERT EXISTS (SELECT 1 FROM marketing.broadcast_proposals WHERE id = v_p), 'h: Proposal bleibt';
END $$;

-- i) Final-Review I1: service_role (nicht Eigentuemerin, kein Superuser) kommt nicht an der Funktion vorbei -
--    auch nicht, wenn sie den Status selbst auf entwurf stellt und die Einstellung selbst setzt.
--    Danach loescht die API-Rolle (Probe-Rolle = SUPABASE_DB_USER) ueber pult_inhalt_loeschen weiter normal.
DO $$ DECLARE d jsonb; v_fg uuid; v_e uuid; v_n int; v_fehler text; v_titel text; BEGIN
  -- Die Probe-Rolle ist die API-Rolle; sie muss Eigentuemerin oder Superuser sein, sonst scheitert Loeschen.
  ASSERT EXISTS (SELECT 1 FROM pg_class c JOIN pg_roles r ON r.rolname = current_user
                  WHERE c.oid = 'marketing.inhalt_fassungen'::regclass AND (c.relowner = r.oid OR r.rolsuper)),
         format('i: API-Rolle %s ist weder Eigentuemerin von inhalt_fassungen noch Superuser', current_user);
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  -- freigegeben mit zwei Fassungen
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 075 I')
  RETURNING id INTO v_fg;
  PERFORM marketing.pult_bloecke_speichern(v_fg, 0, 'Probe 075 I', '', d, 'betreiber', false);
  PERFORM marketing.pult_bloecke_speichern(v_fg, 1, 'Probe 075 I', '',
    jsonb_set(d, '{t,data,props,text}', '"zwei"'), 'betreiber', false);
  v_n := marketing.pult_einreichen(v_fg, 'probe');
  PERFORM marketing.pult_entscheiden(v_fg, v_n, 'freigeben', 'probe', NULL);
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_fg) = 'freigegeben', 'i: Probe freigegeben';
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_fg) = 2, 'i: zwei Fassungen';
  -- Entwurf
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 075 E')
  RETURNING id INTO v_e;
  PERFORM marketing.pult_bloecke_speichern(v_e, 0, 'Probe 075 E', '', d, 'betreiber', false);

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    RAISE NOTICE 'i: keine Rolle service_role - Restweg-Probe uebersprungen';
  ELSE
    ASSERT NOT (SELECT rolsuper FROM pg_roles WHERE rolname = 'service_role'), 'i: service_role ist kein Superuser';
    -- Rechte wie nach 003 (USAGE + ALL auf alle Tabellen) - live fehlen sie am 19.10. ganz, die Probe soll aber
    -- den Trigger pruefen, nicht den Zufall fehlender Grants. Rollt mit der Probe zurueck.
    GRANT USAGE ON SCHEMA marketing TO service_role;
    GRANT ALL ON ALL TABLES IN SCHEMA marketing TO service_role;
    -- freigegeben: Status per UPDATE auf entwurf, Einstellung selbst gesetzt, DELETE -> muss scheitern
    v_fehler := NULL;
    BEGIN
      SET LOCAL ROLE service_role;
      UPDATE marketing.inhalte SET status = 'entwurf' WHERE id = v_fg;
      ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_fg) = 'entwurf', 'i: service_role stellt Status um';
      PERFORM set_config('marketing.inhalt_loeschen', v_fg::text, true);
      DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_fg;
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    RESET ROLE;
    ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%',
           format('i: service_role, Status umgestellt + Einstellung gefaelscht: %s', v_fehler);
    ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_fg) = 2, 'i: freigegebene Fassungen bleiben';
    ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_fg) = 'freigegeben', 'i: Status zurueckgerollt';
    -- Entwurf: auch dort loescht service_role nicht an der Funktion vorbei
    v_fehler := NULL;
    BEGIN
      SET LOCAL ROLE service_role;
      PERFORM set_config('marketing.inhalt_loeschen', v_e::text, true);
      DELETE FROM marketing.inhalt_fassungen WHERE inhalt = v_e;
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    RESET ROLE;
    ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('i: service_role, Entwurf: %s', v_fehler);
    ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_e) = 1, 'i: Entwurfsfassung bleibt';
    -- Selbst mit EXECUTE (z. B. per Default Privileges aus 003) scheitert die INVOKER-Funktion fuer
    -- service_role fail-closed am Trigger - darum bleibt sie SECURITY INVOKER.
    GRANT EXECUTE ON FUNCTION marketing.pult_inhalt_loeschen(uuid, text, text) TO service_role;
    v_fehler := NULL;
    BEGIN
      SET LOCAL ROLE service_role;
      PERFORM marketing.pult_inhalt_loeschen(v_e, 'vibemind', 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    RESET ROLE;
    ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('i: service_role ueber die Funktion: %s', v_fehler);
    ASSERT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_e), 'i: Entwurf bleibt nach service_role-Versuch';
    REVOKE EXECUTE ON FUNCTION marketing.pult_inhalt_loeschen(uuid, text, text) FROM service_role;
    ASSERT current_user = session_user, 'i: Rolle zurueckgesetzt';
  END IF;
  ASSERT coalesce(current_setting('marketing.inhalt_loeschen', true), '') = '', 'i: Einstellung zurueckgerollt';

  -- Normales Loeschen ueber die Funktion als API-Rolle funktioniert weiter
  v_titel := marketing.pult_inhalt_loeschen(v_e, 'vibemind', 'probe');
  ASSERT v_titel = 'Probe 075 E', format('i: API-Rolle loescht: %s', v_titel);
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalt_fassungen WHERE inhalt = v_e), 'i: Fassungen weg';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_e), 'i: Inhalt weg';
  -- und die freigegebene bleibt auch fuer die Funktion gesperrt
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_inhalt_loeschen(v_fg, 'vibemind', 't'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Freigegebene Inhalte bleiben erhalten', format('i: freigegeben: %s', v_fehler);
END $$;

SELECT 'verify_075 ok' AS ergebnis;
