-- 049_formular_gestalt_zahlen.sql — Final-Review Terminkarten, Befund I-1
-- (sales-claw .superpowers/sdd/2026-09-24-terminkarten/final-fix-findings.md).
--
-- 046 liess Gestalten durch, die das Setzprogramm in sales-claw
-- (sales-mcp/formular.py) nicht zeichnen kann:
--   * Zahlen als JSON-Strings ("10"): (p->>'x')::numeric wandelt den Text
--     klaglos um, in Python ist p["x"] * mm aber ein TypeError.
--   * "groesse": "12pt" bei festen Texten: float("12pt") ist ein ValueError.
--   * Felder zwischen 3 und 5,9 mm Hoehe: darin passt auch die kleinste
--     Schrift nicht, setzen() wirft PasstNicht - schon beim Musterblatt.
-- Ein einziger solcher Auftrag hielt vorlagenauftraege_pruefen fuer ALLE
-- Laeden an (ein offener Auftrag je Art).
--
-- Diese Fassung von _formular_gestalt_fehler ist die aus 046, WOERTLICH
-- uebernommen und nur erweitert:
--   1. seite.breite_mm/hoehe_mm, jedes platz.x/y/breite/hoehe (Felder UND
--      feste Texte) und texte[].groesse (falls vorhanden) muessen JSON-Zahlen
--      sein (jsonb_typeof = 'number').
--   2. Jedes Feld hat platz.hoehe >= 6 (046: >= 3).
-- Alle anderen Regeln aus 046 bleiben unveraendert. Additiv, idempotent
-- (CREATE OR REPLACE), in einer Transaktion.
BEGIN;

CREATE OR REPLACE FUNCTION marketing._formular_gestalt_fehler(g jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    b numeric; h numeric; f jsonb; t jsonb; p jsonb; namen text[] := '{}';
BEGIN
    IF jsonb_typeof(g) <> 'object' THEN RETURN 'Gestalt ist kein Objekt'; END IF;
    -- 049: erst der JSON-Typ, dann der Wert - ein String "148" ist keine Zahl.
    IF jsonb_typeof(g->'seite'->'breite_mm') IS DISTINCT FROM 'number'
       OR jsonb_typeof(g->'seite'->'hoehe_mm') IS DISTINCT FROM 'number' THEN
        RETURN 'seite.breite_mm und seite.hoehe_mm muessen JSON-Zahlen sein (ohne Anfuehrungszeichen, ohne Einheit)';
    END IF;
    b := (g->'seite'->>'breite_mm')::numeric;
    h := (g->'seite'->>'hoehe_mm')::numeric;
    IF b IS NULL OR h IS NULL OR b NOT BETWEEN 50 AND 300 OR h NOT BETWEEN 50 AND 300 THEN
        RETURN 'seite.breite_mm und seite.hoehe_mm muessen zwischen 50 und 300 liegen';
    END IF;
    IF NOT (g ? 'felder') OR jsonb_typeof(g->'felder') IS DISTINCT FROM 'array' THEN
        RETURN 'felder fehlt oder ist keine Liste';
    END IF;
    IF jsonb_array_length(g->'felder') NOT BETWEEN 1 AND 60 THEN
        RETURN 'felder muss eine Liste mit 1 bis 60 Feldern sein';
    END IF;
    FOR f IN SELECT * FROM jsonb_array_elements(g->'felder') LOOP
        IF coalesce(f->>'name', '') !~ '^[a-z][a-z0-9_]{0,39}$' THEN
            RETURN format('Feldname %s ist ungueltig', f->>'name');
        END IF;
        IF (f->>'name') = ANY(namen) THEN
            RETURN format('Feldname %s kommt doppelt vor', f->>'name');
        END IF;
        namen := namen || (f->>'name');
        IF length(btrim(coalesce(f->>'beschriftung', ''))) NOT BETWEEN 1 AND 80 THEN
            RETURN format('Feld %s braucht eine Beschriftung (1-80 Zeichen)', f->>'name');
        END IF;
        IF coalesce(f->>'art', '') NOT IN ('text', 'datum', 'uhrzeit', 'telefon', 'mehrzeilig') THEN
            RETURN format('Feld %s hat die unbekannte Art %s', f->>'name', f->>'art');
        END IF;
        IF coalesce(f->>'quelle', '') NOT IN (
            'kunde.name', 'kunde.telefon', 'kunde.email', 'kunde.firma',
            'termin.datum', 'termin.uhrzeit', 'termin.dauer', 'termin.thema', 'termin.ort',
            'mitglied.name', 'frei') THEN
            RETURN format('Feld %s hat die ungueltige Quelle %s', f->>'name', f->>'quelle');
        END IF;
        p := f->'platz';
        IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' THEN
            RETURN format('Feld %s braucht platz (x, y, breite, hoehe)', f->>'name');
        END IF;
        IF (p->>'x') IS NULL OR (p->>'y') IS NULL OR (p->>'breite') IS NULL OR (p->>'hoehe') IS NULL THEN
            RETURN format('Feld %s: platz braucht x, y, breite und hoehe, alle gesetzt', f->>'name');
        END IF;
        -- 049: JSON-Zahlen, keine Strings.
        IF jsonb_typeof(p->'x') <> 'number' OR jsonb_typeof(p->'y') <> 'number'
           OR jsonb_typeof(p->'breite') <> 'number' OR jsonb_typeof(p->'hoehe') <> 'number' THEN
            RETURN format('Feld %s: x, y, breite und hoehe muessen JSON-Zahlen sein (ohne Anfuehrungszeichen, ohne Einheit)', f->>'name');
        END IF;
        -- 049: unter 6 mm passt auch die kleinste Schrift des Setzprogramms nicht.
        IF (p->>'hoehe')::numeric < 6 THEN
            RETURN format('Feld %s ist niedriger als 6 mm (platz.hoehe >= 6)', f->>'name');
        END IF;
        IF (p->>'x')::numeric < 0 OR (p->>'y')::numeric < 0
           OR (p->>'breite')::numeric < 5 OR (p->>'hoehe')::numeric < 3
           OR (p->>'x')::numeric + (p->>'breite')::numeric > b
           OR (p->>'y')::numeric + (p->>'hoehe')::numeric > h THEN
            RETURN format('Feld %s liegt nicht vollstaendig auf der Seite', f->>'name');
        END IF;
    END LOOP;
    IF g ? 'texte' THEN
        IF jsonb_typeof(g->'texte') <> 'array' OR jsonb_array_length(g->'texte') > 30 THEN
            RETURN 'texte muss eine Liste mit hoechstens 30 Eintraegen sein';
        END IF;
        FOR t IN SELECT * FROM jsonb_array_elements(g->'texte') LOOP
            p := t->'platz';
            IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' THEN
                RETURN 'ein fester Text braucht platz (x, y, breite, hoehe)';
            END IF;
            IF (p->>'x') IS NULL OR (p->>'y') IS NULL OR (p->>'breite') IS NULL OR (p->>'hoehe') IS NULL THEN
                RETURN 'ein fester Text hat einen unvollstaendigen platz';
            END IF;
            -- 049: JSON-Zahlen, keine Strings - auch bei festen Texten.
            IF jsonb_typeof(p->'x') <> 'number' OR jsonb_typeof(p->'y') <> 'number'
               OR jsonb_typeof(p->'breite') <> 'number' OR jsonb_typeof(p->'hoehe') <> 'number' THEN
                RETURN 'ein fester Text: x, y, breite und hoehe muessen JSON-Zahlen sein (ohne Anfuehrungszeichen, ohne Einheit)';
            END IF;
            -- 049: groesse ist optional; wenn gesetzt, eine JSON-Zahl ("12pt" nicht).
            IF t ? 'groesse' AND jsonb_typeof(t->'groesse') IS DISTINCT FROM 'number' THEN
                RETURN 'ein fester Text: groesse muss eine JSON-Zahl sein (ohne Anfuehrungszeichen, ohne Einheit)';
            END IF;
            IF length(coalesce(t->>'text', '')) NOT BETWEEN 1 AND 200
               OR (p->>'x')::numeric < 0 OR (p->>'y')::numeric < 0
               OR (p->>'x')::numeric + (p->>'breite')::numeric > b
               OR (p->>'y')::numeric + (p->>'hoehe')::numeric > h THEN
                RETURN 'ein fester Text ist leer, zu lang oder liegt nicht auf der Seite';
            END IF;
        END LOOP;
    END IF;
    RETURN NULL;
EXCEPTION WHEN invalid_text_representation OR data_exception THEN
    RETURN 'Gestalt enthaelt einen Wert, der keine Zahl ist';
END $$;

COMMIT;
