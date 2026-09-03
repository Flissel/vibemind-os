-- 042_marketing_sales_uebergaben.sql — F3: echte Antworten auf Marketing-
-- Nachrichten werden sales-claw als Uebergabe angeboten (Spec 2026-09-03, E3/E4/E7).
--
-- Die Uebergabe entsteht beim KLASSIFIZIEREN (reply/question) — das ist bereits
-- eine menschliche/kuratorische Handlung. Sie ist idempotent je eingehender
-- Nachricht (inbound_id UNIQUE). sales_app darf offene Uebergaben lesen und
-- erledigen, aber keine Tabelle in marketing.* anfassen.
CREATE TABLE IF NOT EXISTS marketing.sales_uebergaben (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inbound_id     uuid NOT NULL UNIQUE REFERENCES marketing.inbound_messages(id) ON DELETE CASCADE,
    from_email     text NOT NULL,
    from_name      text NOT NULL DEFAULT '',
    subject        text NOT NULL DEFAULT '',
    auszug         text NOT NULL DEFAULT '',
    kampagne       text NOT NULL DEFAULT '',
    klassifikation text NOT NULL,
    status         text NOT NULL DEFAULT 'offen' CHECK (status IN ('offen', 'angenommen', 'abgelehnt')),
    lead_id        text,
    grund          text NOT NULL DEFAULT '',
    created_at     timestamptz NOT NULL DEFAULT now(),
    erledigt_am    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_sales_uebergaben_offen
    ON marketing.sales_uebergaben(created_at) WHERE status = 'offen';

CREATE OR REPLACE FUNCTION marketing.trg_sales_uebergabe() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_email    text;
    v_kampagne text := '';
BEGIN
    IF NEW.classification IS NULL OR NEW.classification NOT IN ('reply', 'question') THEN
        RETURN NEW;
    END IF;
    IF coalesce(NEW.is_bounce, false) OR coalesce(NEW.is_autoreply, false) THEN
        RETURN NEW;
    END IF;
    v_email := lower(btrim(coalesce(NEW.from_email, '')));
    IF v_email = '' OR position('@' in v_email) = 0 THEN
        RETURN NEW;
    END IF;
    IF compliance.ist_gesperrt('email:' || v_email) IS NOT NULL THEN
        RETURN NEW;                       -- Sperrliste zuerst (Spec Leitplanke 2)
    END IF;
    IF NEW.linked_send_id IS NOT NULL THEN
        SELECT c.name INTO v_kampagne
        FROM marketing.campaign_sends s
        JOIN marketing.campaigns c ON c.id = s.campaign_id
        WHERE s.id = NEW.linked_send_id;
    END IF;
    INSERT INTO marketing.sales_uebergaben
        (inbound_id, from_email, from_name, subject, auszug, kampagne, klassifikation)
    VALUES (NEW.id, v_email, coalesce(NEW.from_name, ''), coalesce(NEW.subject, ''),
            left(coalesce(NEW.body_text, ''), 600), coalesce(v_kampagne, ''), NEW.classification)
    ON CONFLICT (inbound_id) DO NOTHING;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_sales_uebergabe ON marketing.inbound_messages;
CREATE TRIGGER trg_sales_uebergabe
    AFTER INSERT OR UPDATE OF classification ON marketing.inbound_messages
    FOR EACH ROW EXECUTE FUNCTION marketing.trg_sales_uebergabe();

CREATE OR REPLACE FUNCTION marketing.uebergaben_offen(p_limit int DEFAULT 20)
RETURNS TABLE (id uuid, from_email text, from_name text, subject text, auszug text,
               kampagne text, klassifikation text, seit timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
    SELECT id, from_email, from_name, subject, auszug, kampagne, klassifikation, created_at
    FROM marketing.sales_uebergaben
    WHERE status = 'offen'
    ORDER BY created_at ASC
    LIMIT greatest(1, least(coalesce(p_limit, 20), 100));
$$;

CREATE OR REPLACE FUNCTION marketing.uebergabe_erledigen(
    p_id uuid, p_status text, p_lead_id text, p_grund text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
DECLARE v_n int;
BEGIN
    IF p_status NOT IN ('angenommen', 'abgelehnt') THEN
        RAISE EXCEPTION 'uebergabe_erledigen: status muss angenommen oder abgelehnt sein';
    END IF;
    IF p_status = 'abgelehnt' AND length(btrim(coalesce(p_grund, ''))) = 0 THEN
        RAISE EXCEPTION 'uebergabe_erledigen: abgelehnt braucht einen grund';
    END IF;
    UPDATE marketing.sales_uebergaben
       SET status = p_status, lead_id = p_lead_id, grund = coalesce(p_grund, ''), erledigt_am = now()
     WHERE id = p_id AND status = 'offen';
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n = 1;
END
$$;

REVOKE ALL ON FUNCTION marketing.uebergaben_offen(int) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.uebergabe_erledigen(uuid, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION marketing.uebergaben_offen(int) TO sales_app;
GRANT EXECUTE ON FUNCTION marketing.uebergabe_erledigen(uuid, text, text, text) TO sales_app;
