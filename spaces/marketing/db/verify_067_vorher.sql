-- Vorlauf fuer verify_067 Abschnitt -1 (Final-Review I1), nur ueber migration_probe und VOR 067 einzuspielen.
-- Legt je Aufruf eine Vormerkung im alten Modell (061: status 'wartet') auf einem eigenen Probe-Newsletter an und
-- merkt, ob 067 (Spalte bereit_am) zu dem Zeitpunkt schon da war. Erwartung in verify_067:
--   vor dem ersten 067   -> das Einspielen beendet die alte Vormerkung (fehler, "bitte noch einmal senden")
--   zwischen zwei 067    -> das erneute Einspielen laesst die wartende Runde stehen (067 bleibt idempotent)
-- Aufruf (die Datei zweimal, 067 zweimal):
--   python -m spaces.marketing.scripts.migration_probe spaces/marketing/db/verify_067_vorher.sql \
--     spaces/marketing/db/067_parallele_runden.sql spaces/marketing/db/verify_067_vorher.sql \
--     spaces/marketing/db/067_parallele_runden.sql spaces/marketing/db/verify_067.sql
CREATE TEMP TABLE IF NOT EXISTS _p067_alt (id uuid, mit_067 boolean) ON COMMIT DROP;

DO $$ DECLARE v_i uuid; v_a uuid; BEGIN
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 067 alt')
  RETURNING id INTO v_i;
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, kontext, status)
  VALUES (v_i, 'chat', 'Vormerkung von gestern', '{}', 'wartet') RETURNING id INTO v_a;
  INSERT INTO _p067_alt VALUES (v_a, EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                                              AND table_name = 'chat_auftraege' AND column_name = 'bereit_am'));
END $$;
