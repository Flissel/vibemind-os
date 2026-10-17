-- RUNBOOK: nach 070. Idempotent, eine Transaktion. Nach jedem Replay verify_071 laufen lassen.
-- 071: Kampagnenkalender (17.10.2026, Betreiber: eigene Seite /marketing/plan, Agent nur Vorschlaege,
-- Erinnerung im Morgen-Bericht). Ein Eintrag ist ein PLAN - versendet wird dadurch nichts; alles geht
-- weiter durch Entwurf und Freigabe. Vom Agenten kommt immer status 'vorschlag', bis ein Mensch bestaetigt.
-- Schreiben nur ueber die Funktionen (die API liest ueber SELECT ... FROM (<sql>), darin geht kein INSERT).
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.kampagnen_plan (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant      text NOT NULL REFERENCES marketing.mandanten(id),
    datum        date NOT NULL,
    art          text NOT NULL CHECK (art IN ('newsletter','post','podcast','video','aktion','sonstiges')),
    titel        text NOT NULL CHECK (length(btrim(titel)) BETWEEN 1 AND 120),
    notiz        text NOT NULL DEFAULT '' CHECK (length(notiz) <= 1000),
    inhalt       uuid REFERENCES marketing.inhalte(id) ON DELETE SET NULL,
    status       text NOT NULL CHECK (status IN ('vorschlag','geplant','erledigt','verschoben','abgesagt')),
    angelegt_von text NOT NULL CHECK (angelegt_von IN ('agent','betreiber')),
    erstellt_am  timestamptz NOT NULL DEFAULT now(),
    geaendert_am timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kampagnen_plan_datum_idx ON marketing.kampagnen_plan (mandant, datum);

-- Anlegen: Agent -> vorschlag, Betreiber -> geplant.
CREATE OR REPLACE FUNCTION marketing.plan_anlegen(p_mandant text, p_datum date, p_art text, p_titel text,
                                                  p_notiz text, p_inhalt uuid, p_von text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v uuid;
BEGIN
  IF p_von NOT IN ('agent', 'betreiber') THEN RAISE EXCEPTION 'von muss agent oder betreiber sein'; END IF;
  IF NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = p_mandant) THEN
    RAISE EXCEPTION 'Unbekannte firma'; END IF;
  IF p_datum < current_date - 1 THEN RAISE EXCEPTION 'Datum liegt in der Vergangenheit'; END IF;
  INSERT INTO marketing.kampagnen_plan (mandant, datum, art, titel, notiz, inhalt, status, angelegt_von)
  VALUES (p_mandant, p_datum, p_art, btrim(p_titel), coalesce(p_notiz, ''), p_inhalt,
          CASE WHEN p_von = 'agent' THEN 'vorschlag' ELSE 'geplant' END, p_von)
  RETURNING id INTO v;
  RETURN v;
END $$;

-- Aendern durch den Betreiber: Status, Datum (verschieben), Titel, Notiz, Verweis auf den Entwurf.
-- NULL laesst ein Feld unveraendert. Ein neues Datum setzt verschoben, wenn kein Status mitkommt.
CREATE OR REPLACE FUNCTION marketing.plan_aendern(p_id uuid, p_status text, p_datum date, p_titel text,
                                                  p_notiz text, p_inhalt uuid) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE alt marketing.kampagnen_plan; v text;
BEGIN
  SELECT * INTO alt FROM marketing.kampagnen_plan WHERE id = p_id FOR UPDATE;
  IF alt.id IS NULL THEN RAISE EXCEPTION 'Unbekannter Planeintrag'; END IF;
  IF p_status IS NOT NULL AND p_status NOT IN ('geplant','erledigt','verschoben','abgesagt') THEN
    RAISE EXCEPTION 'status muss geplant, erledigt, verschoben oder abgesagt sein'; END IF;
  UPDATE marketing.kampagnen_plan
     SET status = coalesce(p_status, CASE WHEN p_datum IS NOT NULL AND p_datum <> alt.datum
                                          AND alt.status <> 'vorschlag' THEN 'verschoben' END, alt.status),
         datum = coalesce(p_datum, alt.datum), titel = coalesce(btrim(p_titel), alt.titel),
         notiz = coalesce(p_notiz, alt.notiz), inhalt = coalesce(p_inhalt, alt.inhalt), geaendert_am = now()
   WHERE id = p_id
  RETURNING status INTO v;
  RETURN v;
END $$;

-- Fuer den Morgen-Bericht in sales-claw (Rolle sales_app, nur Basis-Laden): was steht in den naechsten
-- p_tage Tagen an (vorschlag/geplant/verschoben), mit Hinweis, ob schon ein fertiger Entwurf dranhaengt.
CREATE OR REPLACE FUNCTION marketing.plan_anstehend(p_tage int DEFAULT 7)
RETURNS TABLE (id uuid, mandant text, datum date, art text, titel text, status text, entwurf_status text)
LANGUAGE sql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
    SELECT p.id, p.mandant, p.datum, p.art, p.titel, p.status, i.status
      FROM marketing.kampagnen_plan p
      LEFT JOIN marketing.inhalte i ON i.id = p.inhalt
     WHERE p.status IN ('vorschlag','geplant','verschoben')
       AND p.datum BETWEEN current_date AND current_date + greatest(0, least(coalesce(p_tage, 7), 31))
     ORDER BY p.datum, p.titel
     LIMIT 50;
$$;

-- Funktionen sind in Postgres standardmaessig fuer PUBLIC ausfuehrbar: Schreiben bleibt der Marketing-API
-- (Eigentuemer) vorbehalten, sales_app darf nur lesen (verify_071 prueft beides).
REVOKE ALL ON FUNCTION marketing.plan_anlegen(text, date, text, text, text, uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.plan_aendern(uuid, text, date, text, text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.plan_anstehend(int) FROM PUBLIC;
-- Dieselbe Luecke bei den Podcast-Funktionen aus 069/070 (gefunden beim Bau von 071).
REVOKE ALL ON FUNCTION marketing.ton_auftrag_anlegen(text, text, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.ton_naechster(interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.ton_verlaengern(uuid, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.ton_abschliessen(uuid, text, numeric, text) FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    GRANT USAGE ON SCHEMA marketing TO sales_app;
    GRANT EXECUTE ON FUNCTION marketing.plan_anstehend(int) TO sales_app;
  END IF;
END $$;

COMMIT;
