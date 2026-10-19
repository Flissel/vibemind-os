-- RUNBOOK: nach 075. Idempotent, eine Transaktion. Vorher sichern (VM):
--   docker exec debian-supabase-db-1 pg_dump -U postgres -d postgres -t marketing.sync_outbox --data-only \
--     | gzip > ~/backups/sync_outbox-vor-076.sql.gz
-- Danach verify_076 ueber migration_probe laufen lassen.
-- 076: Den Abgleich Datenbank -> Markdown-Ablage (004, Worker A/B) stilllegen (Paket D3, User-Entscheid 19.10.).
--   Die Worker liefen nie im Betrieb: Zielordner nie angelegt, alle 428 Outbox-Zeilen (02.06.-03.09.) ohne
--   applied_at. Personendaten gehoeren ausserdem nicht in die unverschluesselte Rowboat-Ablage.
--   Weg: die 12 Trigger trg_emit_sync_<tabelle>, marketing.emit_sync_event(), marketing.mark_outbox_applied(),
--   marketing.sync_outbox. marketing._resolve_handle() nutzte nur emit_sync_event und faellt mit.
--   Bleibt: link_inbound_to_send (007) setzt weiter die Einstellung marketing.sync_origin; ohne Trigger liest
--   sie niemand, das ist harmlos. Webhook-Bus (020) hat eigene Tabellen. Worker C (worker_imap_sync) schreibt
--   nur inbound_messages und braucht keine Outbox.
-- Nach 076 NIE 004 erneut einspielen (legt Trigger und Outbox wieder an).

BEGIN;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'accounts', 'emails', 'email_tags', 'audiences', 'audience_members', 'tags',
        'templates', 'campaigns', 'campaign_sends', 'inbound_messages', 'strategies', 'runs'
    ] LOOP
        IF to_regclass(format('marketing.%I', t)) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS trg_emit_sync_%I ON marketing.%I', t, t);
        END IF;
    END LOOP;
END $$;

DROP FUNCTION IF EXISTS marketing.emit_sync_event();
DROP FUNCTION IF EXISTS marketing.mark_outbox_applied(uuid[]);
DROP FUNCTION IF EXISTS marketing._resolve_handle(text, jsonb);
DROP TABLE IF EXISTS marketing.sync_outbox;

INSERT INTO marketing.audit_log (actor, action, target_table, payload)
VALUES ('migration:076', 'sync.triggers.remove', 'marketing.*',
        jsonb_build_object('grund', 'Abgleich DB->Markdown lief nie (D3)', 'ersetzt', '004'));

COMMIT;
