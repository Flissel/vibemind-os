-- verify_043.sql — Rechte, Existenz und Verhalten der Versandauftraege.
-- Jede Verletzung bricht mit Exception ab (psql -v ON_ERROR_STOP=1).
-- Alles, was hier Zeilen anlegt, raeumt am Ende wieder auf.

-- --- Rechte (Least Privilege wie 041/042) ----------------------------------
DO $$
BEGIN
    IF NOT has_function_privilege('sales_app', 'marketing.versandauftraege_offen(int)', 'EXECUTE')
       OR NOT has_function_privilege('sales_app',
              'marketing.versandauftrag_erledigen(uuid,text,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'sales_app darf die Versandauftrag-Funktionen nicht rufen';
    END IF;
    IF has_table_privilege('sales_app', 'marketing.versandauftraege', 'SELECT')
       OR has_table_privilege('sales_app', 'marketing.versandauftraege', 'INSERT')
       OR has_table_privilege('sales_app', 'marketing.versandauftraege', 'UPDATE') THEN
        RAISE EXCEPTION 'sales_app hat Tabellenrechte auf versandauftraege — verboten';
    END IF;
    -- Marketing darf anlegen; die Funktion ist NICHT SECURITY DEFINER, also
    -- muss der Rufer selbst schreiben duerfen. sales_app soll sie nicht rufen.
    IF has_function_privilege('sales_app',
           'marketing.versandauftrag_anlegen(text,text,text,text,text,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'sales_app darf versandauftrag_anlegen rufen — das ist Marketings Seite';
    END IF;
END $$;
SELECT 'verify 043 Rechte: ok' AS ergebnis;

-- --- Kennungs-Normalisierung deckt sich mit sperrliste.py -------------------
DO $$
BEGIN
    IF compliance.kennung_email('  Felix@Example.COM ') <> 'email:felix@example.com' THEN
        RAISE EXCEPTION 'kennung_email normalisiert nicht wie sperrliste.py';
    END IF;
    IF compliance.kennung_email('kein-at-zeichen') IS NOT NULL THEN
        RAISE EXCEPTION 'kennung_email nimmt etwas ohne @ an';
    END IF;
    IF compliance.kennung_email('a b@example.com') IS NOT NULL THEN
        RAISE EXCEPTION 'kennung_email nimmt Leerraum innen an';
    END IF;
    -- Nationale Null wird +49 (deutscher Betrieb, dokumentierte Annahme)
    IF compliance.kennung_tel('0176 1234567') <> 'tel:+491761234567' THEN
        RAISE EXCEPTION 'kennung_tel setzt die nationale Null nicht auf +49';
    END IF;
    IF compliance.kennung_tel('0049 176 1234567') <> 'tel:+491761234567' THEN
        RAISE EXCEPTION 'kennung_tel macht aus 00 kein +';
    END IF;
    IF compliance.kennung_tel('+49 (0)176 1234567') <> 'tel:+491761234567' THEN
        RAISE EXCEPTION 'kennung_tel entfernt die (0)-Vorwahlnull nicht';
    END IF;
    -- WhatsApp-Chat-ID liefert ihren Ziffernteil
    IF compliance.kennung_tel('491761234567@c.us') <> 'tel:+491761234567' THEN
        RAISE EXCEPTION 'kennung_tel liest die WhatsApp-Chat-ID nicht';
    END IF;
    IF compliance.kennung_tel('12345') IS NOT NULL THEN
        RAISE EXCEPTION 'kennung_tel nimmt weniger als 6 Ziffern an';
    END IF;
    -- Die erzeugte Form muss compliance.sperrliste passieren koennen
    IF compliance.kennung_tel('0176 1234567') !~ '^(email:[^A-Z\s]+|tel:\+[0-9]{6,})$' THEN
        RAISE EXCEPTION 'kennung_tel erzeugt eine Form, die sperrliste ablehnt';
    END IF;
END $$;
SELECT 'verify 043 Kennung: ok' AS ergebnis;

-- --- Verhalten beim Anlegen ------------------------------------------------
DO $$
DECLARE
    v_a     jsonb;
    v_b     jsonb;
    v_vor   bigint;
    v_nach  bigint;
    v_mail  text := 'verify043-' || substr(md5(random()::text), 1, 8) || '@example.invalid';
    v_gesp  text := 'verify043-gesperrt-' || substr(md5(random()::text), 1, 8) || '@example.invalid';
BEGIN
    -- (1) Unzulaessiger Kanal: Absage mit Grund, keine Exception, keine Zeile
    SELECT count(*) INTO v_vor FROM marketing.versandauftraege;
    v_a := marketing.versandauftrag_anlegen('telegram', v_mail, 'Hallo');
    IF (v_a->>'ok')::boolean THEN
        RAISE EXCEPTION 'telegram wurde angenommen — sales-claw kann das nicht zustellen';
    END IF;
    IF v_a->>'grund' NOT LIKE '%Telegram%' THEN
        RAISE EXCEPTION 'Die Absage nennt Telegram nicht: %', v_a->>'grund';
    END IF;

    -- (2) Leere Nachricht
    v_a := marketing.versandauftrag_anlegen('email', v_mail, '   ');
    IF (v_a->>'ok')::boolean THEN RAISE EXCEPTION 'leere Nachricht angenommen'; END IF;

    -- (3) Pfadanteil im Anhangfeld
    v_a := marketing.versandauftrag_anlegen('email', v_mail, 'Hallo', '', 'Marketing/post.pdf');
    IF (v_a->>'ok')::boolean THEN RAISE EXCEPTION 'Pfadanteil im Anhang angenommen'; END IF;

    -- (4) Unlesbarer Empfaenger
    v_a := marketing.versandauftrag_anlegen('email', 'weder-noch', 'Hallo');
    IF (v_a->>'ok')::boolean THEN RAISE EXCEPTION 'unlesbarer Empfaenger angenommen'; END IF;

    SELECT count(*) INTO v_nach FROM marketing.versandauftraege;
    IF v_nach <> v_vor THEN
        RAISE EXCEPTION 'Eine Absage hat trotzdem eine Zeile angelegt (% -> %)', v_vor, v_nach;
    END IF;

    -- (5) Gesperrter Empfaenger: kein Auftrag
    PERFORM compliance.sperren('email:' || v_gesp, 'verify043', 'Testsperre');
    v_a := marketing.versandauftrag_anlegen('email', v_gesp, 'Hallo');
    IF (v_a->>'ok')::boolean THEN
        RAISE EXCEPTION 'Auftrag fuer einen gesperrten Empfaenger entstanden';
    END IF;
    IF v_a->>'grund' NOT LIKE '%Verbotsliste%' THEN
        RAISE EXCEPTION 'Die Absage nennt die Verbotsliste nicht: %', v_a->>'grund';
    END IF;

    -- (6) Gueltiger Auftrag + Wiederholung liefert DIESELBE id
    v_a := marketing.versandauftrag_anlegen('email', v_mail, 'Hallo Welt', 'Betreff',
                                            '', 'verify043', 'verify043');
    IF NOT (v_a->>'ok')::boolean THEN
        RAISE EXCEPTION 'gueltiger Auftrag abgelehnt: %', v_a->>'grund';
    END IF;
    IF (v_a->>'wiederholung')::boolean THEN
        RAISE EXCEPTION 'der erste Auftrag gilt schon als Wiederholung';
    END IF;
    v_b := marketing.versandauftrag_anlegen('email', v_mail, 'Hallo Welt', 'Betreff',
                                            '', 'verify043', 'verify043');
    IF v_b->>'id' <> v_a->>'id' THEN
        RAISE EXCEPTION 'Wiederholung erzeugte einen zweiten Auftrag (% vs %)',
                        v_a->>'id', v_b->>'id';
    END IF;
    IF NOT (v_b->>'wiederholung')::boolean THEN
        RAISE EXCEPTION 'Wiederholung nicht als solche gemeldet';
    END IF;

    -- (7) Er steht als offen zum Abholen bereit
    IF NOT EXISTS (SELECT 1 FROM marketing.versandauftraege_offen(100)
                   WHERE id = (v_a->>'id')::uuid) THEN
        RAISE EXCEPTION 'der neue Auftrag taucht nicht unter den offenen auf';
    END IF;

    -- (8) Erledigen: abgelehnt ohne Grund ist verboten
    BEGIN
        PERFORM marketing.versandauftrag_erledigen((v_a->>'id')::uuid, 'abgelehnt', NULL, '');
        RAISE EXCEPTION 'abgelehnt ohne Grund wurde angenommen';
    EXCEPTION WHEN sqlstate 'P0001' THEN
        IF sqlerrm LIKE '%ohne Grund wurde angenommen%' THEN RAISE; END IF;
    END;

    -- (9) Erledigen: angenommen ohne draft_id ist verboten
    BEGIN
        PERFORM marketing.versandauftrag_erledigen((v_a->>'id')::uuid, 'angenommen', '', '');
        RAISE EXCEPTION 'angenommen ohne draft_id wurde angenommen';
    EXCEPTION WHEN sqlstate 'P0001' THEN
        IF sqlerrm LIKE '%ohne draft_id wurde angenommen%' THEN RAISE; END IF;
    END;

    -- (10) Erledigen wirkt genau einmal
    IF NOT marketing.versandauftrag_erledigen((v_a->>'id')::uuid, 'angenommen', 'draft-xyz', '') THEN
        RAISE EXCEPTION 'Erledigen hat nicht gegriffen';
    END IF;
    IF marketing.versandauftrag_erledigen((v_a->>'id')::uuid, 'angenommen', 'draft-xyz', '') THEN
        RAISE EXCEPTION 'Erledigen hat ein zweites Mal gegriffen';
    END IF;
    IF EXISTS (SELECT 1 FROM marketing.versandauftraege_offen(100)
               WHERE id = (v_a->>'id')::uuid) THEN
        RAISE EXCEPTION 'der erledigte Auftrag steht weiter unter den offenen';
    END IF;

    -- Aufraeumen
    DELETE FROM marketing.versandauftraege WHERE quelle = 'verify043';
    DELETE FROM compliance.sperrliste WHERE quelle = 'verify043';
    SELECT count(*) INTO v_nach FROM marketing.versandauftraege;
    IF v_nach <> v_vor THEN
        RAISE EXCEPTION 'Aufraeumen unvollstaendig (% -> %)', v_vor, v_nach;
    END IF;
END $$;
SELECT 'verify 043 Verhalten: ok' AS ergebnis;
