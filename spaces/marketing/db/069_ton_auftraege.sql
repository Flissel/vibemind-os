-- RUNBOOK: nach 068. Idempotent, eine Transaktion. Nach jedem Replay verify_069 laufen lassen.
-- 069: Podcast fuer den Marketing-Agenten (17.10.2026). Der Agent beauftragt eine Folge (Titel + Skript
-- aus Abschnitten je Sprecher); der Ton-Arbeiter am PC spricht sie lokal (Chatterbox Multilingual, keine
-- Modelle auf der VM, kein API-Budget) und legt eine MP3 in die erzeugten Medien. Versendet wird nichts -
-- die Datei geht nur ueber versand_beauftragen -> sales-claw mit Freigabe durch einen Menschen.
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.ton_auftraege (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mandant      text NOT NULL REFERENCES marketing.mandanten(id),
    titel        text NOT NULL CHECK (length(btrim(titel)) BETWEEN 1 AND 120),
    sprache      text NOT NULL DEFAULT 'de' CHECK (sprache ~ '^[a-z]{2}$'),
    skript       jsonb NOT NULL CHECK (jsonb_typeof(skript) = 'array'
                                       AND jsonb_array_length(skript) BETWEEN 1 AND 60),
    status       text NOT NULL DEFAULT 'offen' CHECK (status IN ('offen','in_arbeit','fertig','fehler')),
    vergeben_bis timestamptz,
    versuche     int NOT NULL DEFAULT 0,
    datei        text CHECK (datei IS NULL OR datei ~ '^pod-[0-9a-f]{8}-[a-z0-9-]{1,60}\.mp3$'),
    dauer_s      numeric CHECK (dauer_s IS NULL OR dauer_s >= 0),
    befund       text NOT NULL DEFAULT '',
    erstellt_am  timestamptz NOT NULL DEFAULT now(),
    geaendert_am timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'fertig' OR datei IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ton_auftraege_offen_idx ON marketing.ton_auftraege (erstellt_am)
    WHERE status IN ('offen', 'in_arbeit');

-- Naechster Auftrag fuer den Arbeiter: offen, oder in Arbeit mit abgelaufener Frist (Arbeiter abgestuerzt).
-- Nach drei Versuchen fehler statt Endlosschleife.
CREATE OR REPLACE FUNCTION marketing.ton_naechster(p_frist interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE a marketing.ton_auftraege;
BEGIN
  UPDATE marketing.ton_auftraege SET status = 'fehler', befund = 'Drei Versuche ohne Ergebnis',
         vergeben_bis = NULL, geaendert_am = now()
   WHERE status = 'in_arbeit' AND vergeben_bis < now() AND versuche >= 3;
  SELECT * INTO a FROM marketing.ton_auftraege
   WHERE status = 'offen' OR (status = 'in_arbeit' AND vergeben_bis < now())
   ORDER BY erstellt_am LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF a.id IS NULL THEN RETURN NULL; END IF;
  UPDATE marketing.ton_auftraege SET status = 'in_arbeit', vergeben_bis = now() + p_frist,
         versuche = versuche + 1, geaendert_am = now()
   WHERE id = a.id;
  RETURN jsonb_build_object('id', a.id, 'mandant', a.mandant, 'titel', a.titel, 'sprache', a.sprache,
                            'skript', a.skript);
END $$;

CREATE OR REPLACE FUNCTION marketing.ton_verlaengern(p_id uuid, p_frist interval) RETURNS boolean
LANGUAGE sql AS $$
  WITH x AS (UPDATE marketing.ton_auftraege SET vergeben_bis = now() + p_frist, geaendert_am = now()
              WHERE id = p_id AND status = 'in_arbeit' RETURNING 1)
  SELECT EXISTS (SELECT 1 FROM x);
$$;

-- Abschluss: fertig mit Datei oder fehler mit Befund. Nur aus in_arbeit.
CREATE OR REPLACE FUNCTION marketing.ton_abschliessen(p_id uuid, p_datei text, p_dauer numeric, p_befund text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE v text;
BEGIN
  UPDATE marketing.ton_auftraege
     SET status = CASE WHEN p_datei IS NULL THEN 'fehler' ELSE 'fertig' END,
         datei = p_datei, dauer_s = p_dauer, befund = left(coalesce(p_befund, ''), 500),
         vergeben_bis = NULL, geaendert_am = now()
   WHERE id = p_id AND status = 'in_arbeit'
  RETURNING status INTO v;
  RETURN v;
END $$;

COMMIT;
