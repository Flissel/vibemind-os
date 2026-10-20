-- 079: Eindeutige Inhalt-Namen je Firma und Art (20.10.2026, Betreiber-Wunsch).
-- Anlass: Der Telegram-Assistent sollte "den Newsletter test" aendern, fand fuenf Entwuerfe dieses Namens und nahm
-- den neuesten. Doppelte Namen machen jede Ansprache per Name (Agent, Chat, Betreiber) mehrdeutig.
-- Idempotent, eine Transaktion.
--   1) marketing.inhalt_titel_frei(mandant, art, titel, ohne): der Titel selbst, wenn frei, sonst "titel (2)",
--      "(3)" ... - Vergleich ohne Gross/Klein und Randleerzeichen, nur innerhalb derselben Firma und Art.
--      ohne = die eigene Zeile (zaehlt nicht als Konflikt).
--   2) Bestand: in jeder Gruppe gleichen Namens behaelt der aelteste Eintrag (erstellt_am, dann id) seinen Namen,
--      die uebrigen werden in Datumsfolge durchnummeriert. Nur der Titel aendert sich.
--   3) Trigger trg_inhalte_titel_eindeutig (BEFORE INSERT OR UPDATE OF titel, mandant, art):
--      INSERT -> Titel wird bei Bedarf still durchnummeriert (deckt alle Anlege-Funktionen 050/051/053/056/058/077
--      ab, ohne sie anzufassen); UPDATE auf einen vergebenen Namen -> Fehler "Name schon vergeben" (unique_violation).
--      Eine Beratungssperre je (Firma, Art, Name) verhindert, dass zwei gleichzeitige Anlagen denselben Namen ziehen.
--   4) Eindeutiger Index inhalte_titel_eindeutig (mandant, art, lower(btrim(titel))) als harte Absicherung.
-- Rechte: wie 077 - kein SECURITY DEFINER, keine eigenen GRANTs (Default Privileges aus 003 gelten).
BEGIN;

CREATE OR REPLACE FUNCTION marketing.inhalt_titel_frei(p_mandant text, p_art text, p_titel text, p_ohne uuid DEFAULT NULL)
RETURNS text LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_basis text := btrim(p_titel);
  v_kandidat text := v_basis;
  v_n int := 1;
BEGIN
  LOOP
    IF NOT EXISTS (SELECT 1 FROM marketing.inhalte
                    WHERE mandant = p_mandant AND art = p_art AND lower(btrim(titel)) = lower(v_kandidat)
                      AND id IS DISTINCT FROM p_ohne) THEN
      RETURN v_kandidat;
    END IF;
    v_n := v_n + 1;
    v_kandidat := v_basis || ' (' || v_n || ')';
  END LOOP;
END $$;

-- 2) Bestand nummerieren (vor dem Index; der Trigger existiert hier noch nicht oder ist harmlos, weil der neue
--    Name per inhalt_titel_frei schon frei ist)
DROP TRIGGER IF EXISTS trg_inhalte_titel_eindeutig ON marketing.inhalte;
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT id, mandant, art, titel FROM (
      SELECT id, mandant, art, titel, erstellt_am,
             row_number() OVER (PARTITION BY mandant, art, lower(btrim(titel)) ORDER BY erstellt_am, id) AS n
        FROM marketing.inhalte) q
     WHERE n > 1
     ORDER BY erstellt_am, id
  LOOP
    UPDATE marketing.inhalte SET titel = marketing.inhalt_titel_frei(r.mandant, r.art, r.titel, r.id)
     WHERE id = r.id;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION marketing.inhalte_titel_eindeutig() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('marketing.inhalte.titel'),
                                hashtext(NEW.mandant || '|' || NEW.art || '|' || lower(btrim(NEW.titel))));
  IF TG_OP = 'INSERT' THEN
    NEW.titel := marketing.inhalt_titel_frei(NEW.mandant, NEW.art, NEW.titel, NEW.id);
  ELSIF marketing.inhalt_titel_frei(NEW.mandant, NEW.art, NEW.titel, NEW.id) <> btrim(NEW.titel) THEN
    RAISE EXCEPTION 'Name schon vergeben: %', btrim(NEW.titel) USING ERRCODE = 'unique_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_inhalte_titel_eindeutig
  BEFORE INSERT OR UPDATE OF titel, mandant, art ON marketing.inhalte
  FOR EACH ROW EXECUTE FUNCTION marketing.inhalte_titel_eindeutig();

CREATE UNIQUE INDEX IF NOT EXISTS inhalte_titel_eindeutig ON marketing.inhalte (mandant, art, lower(btrim(titel)));

COMMIT;
