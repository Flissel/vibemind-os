-- 013_compliance_sperrliste.sql — die GEMEINSAME Verbotsliste (F1, 03.09.2026)
--
-- Marketing und sales-claw haengen an derselben Postgres. Bisher fuehrte jede
-- Seite Einwilligung getrennt: Marketing kennt Unsubscribes und Bounces
-- (marketing.emails), sales-claw kennt Widerrufe und Loeschantraege
-- (leads.enrichment). Keiner sah die des anderen — der Gefahrenfall ist die
-- Erstansprache an jemanden, der auf der anderen Seite laengst „nein" gesagt hat.
--
-- Eine Tabelle, beide schreiben, beide lesen. Kennung normalisiert:
--   'email:<kleingeschrieben>'   |   'tel:+<ziffern>' (E.164 ohne Formatierung)
-- Die Zeile SELBST ist das Protokoll (quelle, grund, seit); Aufheben ist ein
-- Zeitstempel, nie ein DELETE.
CREATE SCHEMA IF NOT EXISTS compliance;

CREATE TABLE IF NOT EXISTS compliance.sperrliste (
    kennung          text PRIMARY KEY,
    quelle           text NOT NULL,             -- marketing:unsubscribe | marketing:bounce | sales:widerruf | sales:loeschantrag | betreiber
    grund            text NOT NULL DEFAULT '',
    seit             timestamptz NOT NULL DEFAULT now(),
    aufgehoben_am    timestamptz,
    aufgehoben_grund text,
    CONSTRAINT kennung_form CHECK (kennung ~ '^(email:[^A-Z\s]+|tel:\+[0-9]{6,})$')
);
CREATE INDEX IF NOT EXISTS idx_sperrliste_aktiv
    ON compliance.sperrliste(kennung) WHERE aufgehoben_am IS NULL;

-- Sperren ist idempotent; eine aufgehobene Sperre wird durch erneutes Sperren
-- wieder aktiv (neue Quelle, neuer Grund, neues seit).
CREATE OR REPLACE FUNCTION compliance.sperren(p_kennung text, p_quelle text, p_grund text)
RETURNS void LANGUAGE sql AS $$
    INSERT INTO compliance.sperrliste (kennung, quelle, grund)
    VALUES (p_kennung, p_quelle, coalesce(p_grund, ''))
    ON CONFLICT (kennung) DO UPDATE
        SET quelle = EXCLUDED.quelle, grund = EXCLUDED.grund, seit = now(),
            aufgehoben_am = NULL, aufgehoben_grund = NULL
        WHERE compliance.sperrliste.aufgehoben_am IS NOT NULL;
$$;

-- Liefert 'quelle: grund' fuer eine aktive Sperre, sonst NULL.
CREATE OR REPLACE FUNCTION compliance.ist_gesperrt(p_kennung text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT quelle || ': ' || grund FROM compliance.sperrliste
    WHERE kennung = p_kennung AND aufgehoben_am IS NULL;
$$;

-- Marketing schreibt automatisch: Unsubscribe oder wiederholter Bounce.
CREATE OR REPLACE FUNCTION compliance.trg_emails_sperre() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.unsubscribed_at IS NOT NULL THEN
        PERFORM compliance.sperren('email:' || lower(trim(NEW.email)), 'marketing:unsubscribe',
                                   'abgemeldet ' || to_char(NEW.unsubscribed_at, 'YYYY-MM-DD'));
    ELSIF coalesce(NEW.bounce_count, 0) >= 2 THEN
        PERFORM compliance.sperren('email:' || lower(trim(NEW.email)), 'marketing:bounce',
                                   NEW.bounce_count || ' Bounces');
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_emails_sperre ON marketing.emails;
CREATE TRIGGER trg_emails_sperre
    AFTER INSERT OR UPDATE OF unsubscribed_at, bounce_count ON marketing.emails
    FOR EACH ROW EXECUTE FUNCTION compliance.trg_emails_sperre();

-- Bestand nachziehen (idempotent).
INSERT INTO compliance.sperrliste (kennung, quelle, grund, seit)
SELECT 'email:' || lower(trim(email)), 'marketing:unsubscribe',
       'abgemeldet ' || to_char(unsubscribed_at, 'YYYY-MM-DD'), unsubscribed_at
FROM marketing.emails WHERE unsubscribed_at IS NOT NULL
ON CONFLICT (kennung) DO NOTHING;
INSERT INTO compliance.sperrliste (kennung, quelle, grund)
SELECT 'email:' || lower(trim(email)), 'marketing:bounce', bounce_count || ' Bounces'
FROM marketing.emails WHERE coalesce(bounce_count, 0) >= 2
ON CONFLICT (kennung) DO NOTHING;

-- sales-claw (Rolle sales_app) darf lesen und sperren, nie loeschen.
GRANT USAGE ON SCHEMA compliance TO sales_app;
GRANT SELECT, INSERT, UPDATE ON compliance.sperrliste TO sales_app;
GRANT EXECUTE ON FUNCTION compliance.sperren(text, text, text) TO sales_app;
GRANT EXECUTE ON FUNCTION compliance.ist_gesperrt(text) TO sales_app;
