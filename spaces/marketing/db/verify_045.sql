-- verify_045.sql — Formular-Vorlagen und Vorlagen-Auftraege.
-- Jede Verletzung bricht ab (psql -v ON_ERROR_STOP=1). Alles, was Zeilen
-- anlegt, laeuft in einer Transaktion und wird am Ende zurueckgerollt.
BEGIN;

-- Rechte: Sales ruft nur die vier Funktionen, liest keine Tabelle direkt.
DO $$
BEGIN
    IF NOT has_function_privilege('sales_app',
         'marketing.vorlagenauftrag_urteil(uuid,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'sales_app darf nicht urteilen';
    END IF;
    IF has_table_privilege('sales_app', 'marketing.vorlagenauftraege', 'SELECT') THEN
        RAISE EXCEPTION 'sales_app liest vorlagenauftraege direkt - verboten';
    END IF;
    IF has_function_privilege('sales_app',
         'marketing.vorlagenauftrag_vorlegen(uuid,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'sales_app darf vorlegen - das ist Marketings Seite';
    END IF;
END $$;

-- Eine gueltige Gestalt fuer die Proben.
CREATE TEMP TABLE probe_gestalt AS SELECT '{
  "seite": {"breite_mm": 148, "hoehe_mm": 105},
  "texte": [{"text": "TERMINKARTE", "platz": {"x": 8, "y": 6, "breite": 80, "hoehe": 8}, "groesse": 14}],
  "felder": [
    {"name": "kunde", "beschriftung": "Kunde", "art": "text", "quelle": "kunde.name",
     "platz": {"x": 8, "y": 20, "breite": 130, "hoehe": 12}},
    {"name": "datum", "beschriftung": "Datum", "art": "datum", "quelle": "termin.datum",
     "platz": {"x": 8, "y": 36, "breite": 60, "hoehe": 12}}]}'::jsonb AS g;

-- Ladentrennung: der Laden kommt aus der Anmeldung.
SET SESSION AUTHORIZATION sales_app;
DO $$
DECLARE r jsonb;
BEGIN
    r := marketing.vorlagenauftrag_anlegen('terminkarte', NULL, NULL,
         'Felder: Kunde, Datum', '');
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'anlegen: %', r; END IF;
    PERFORM set_config('probe.id', r->>'id', true);
    r := marketing.vorlagenauftrag_anlegen('terminkarte', NULL, NULL, 'x', '');
    IF (r->>'ok')::boolean THEN RAISE EXCEPTION 'zweiter offener Auftrag angenommen'; END IF;
    r := marketing.vorlagenauftrag_anlegen('terminkarte', '\x00'::bytea, 'image/png', 'x', '');
    IF (r->>'ok')::boolean THEN RAISE EXCEPTION 'Bild UND Beschreibung angenommen'; END IF;
END $$;
RESET SESSION AUTHORIZATION;

DO $$
DECLARE l text;
BEGIN
    SELECT laden INTO l FROM marketing.vorlagenauftraege
     WHERE id = current_setting('probe.id')::uuid;
    IF l <> 'sales' THEN RAISE EXCEPTION 'falscher Laden: %', l; END IF;
END $$;

-- Marketing uebernimmt und legt vor.
--
-- Ruling 3: die ungueltige Gestalt (Feld ausserhalb der Seite) wird ZUERST
-- probiert, WAEHREND der Auftrag noch 'in_arbeit' ist. Probierte man sie
-- erst NACH einem erfolgreichen vorlegen, stuende der Auftrag schon auf
-- 'vorgelegt', und die Ablehnung kaeme aus dem falschen Grund ("nicht in
-- Arbeit" statt der echten Gestalt-Pruefung) - die Probe waere aus dem
-- falschen Grund gruen.
DO $$
DECLARE r jsonb; n int;
BEGIN
    SELECT count(*) INTO n FROM marketing.vorlagenauftrag_uebernehmen();
    IF n <> 1 THEN RAISE EXCEPTION 'uebernehmen lieferte % Zeilen', n; END IF;

    r := marketing.vorlagenauftrag_vorlegen(current_setting('probe.id')::uuid,
         '{"seite": {"breite_mm": 148, "hoehe_mm": 105}, "felder": [{"name": "x",
           "beschriftung": "X", "art": "text", "quelle": "kunde.name",
           "platz": {"x": 140, "y": 20, "breite": 30, "hoehe": 12}}]}'::jsonb);
    IF (r->>'ok')::boolean THEN RAISE EXCEPTION 'Feld ausserhalb der Seite angenommen'; END IF;
    IF coalesce(r->>'grund', '') NOT LIKE '%nicht vollstaendig auf der Seite%' THEN
        RAISE EXCEPTION 'falscher Ablehnungsgrund (erwartet "nicht vollstaendig auf der '
                        'Seite"): %', r;
    END IF;

    r := marketing.vorlagenauftrag_vorlegen(current_setting('probe.id')::uuid,
                                            (SELECT g FROM probe_gestalt));
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'vorlegen: %', r; END IF;
END $$;

-- Ein fremder Laden darf weder lesen noch urteilen. Rolle anlegen, wenn noch
-- keine existiert (Probe), sonst die echte benutzen.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app_probe') THEN
        CREATE ROLE sales_app_probe LOGIN;
    END IF;
    GRANT USAGE ON SCHEMA marketing TO sales_app_probe;
    GRANT EXECUTE ON FUNCTION marketing.vorlagenauftrag_urteil(uuid,text,text),
          marketing.vorlagenauftraege_des_ladens() TO sales_app_probe;
END $$;
SET SESSION AUTHORIZATION sales_app_probe;
DO $$
DECLARE r jsonb; n int;
BEGIN
    SELECT count(*) INTO n FROM marketing.vorlagenauftraege_des_ladens();
    IF n <> 0 THEN RAISE EXCEPTION 'fremder Laden sieht % Auftraege', n; END IF;
    r := marketing.vorlagenauftrag_urteil(current_setting('probe.id')::uuid, 'ja', '');
    IF (r->>'ok')::boolean THEN RAISE EXCEPTION 'fremder Laden durfte freigeben'; END IF;
END $$;
RESET SESSION AUTHORIZATION;

-- Der eigene Laden: nein ohne Anmerkung wird abgewiesen, nein mit geht.
SET SESSION AUTHORIZATION sales_app;
DO $$
DECLARE r jsonb;
BEGIN
    r := marketing.vorlagenauftrag_urteil(current_setting('probe.id')::uuid, 'nein', '');
    IF (r->>'ok')::boolean THEN RAISE EXCEPTION 'nein ohne Anmerkung angenommen'; END IF;
    r := marketing.vorlagenauftrag_urteil(current_setting('probe.id')::uuid, 'nein',
                                          'Datum groesser');
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'nein: %', r; END IF;
END $$;
RESET SESSION AUTHORIZATION;

-- Zweite Runde, dann ja: freigegebene Gestalt steht, und sie ist unveraenderlich.
DO $$
DECLARE r jsonb; a record;
BEGIN
    PERFORM * FROM marketing.vorlagenauftrag_uebernehmen();
    SELECT runde, jsonb_array_length(rueckmeldungen) AS n INTO a
      FROM marketing.vorlagenauftraege WHERE id = current_setting('probe.id')::uuid;
    IF a.runde <> 2 OR a.n <> 1 THEN RAISE EXCEPTION 'Runde/Rueckmeldung falsch: %', a; END IF;
    r := marketing.vorlagenauftrag_vorlegen(current_setting('probe.id')::uuid,
                                            (SELECT g FROM probe_gestalt));
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'vorlegen 2: %', r; END IF;
END $$;
SET SESSION AUTHORIZATION sales_app;
SELECT marketing.vorlagenauftrag_urteil(current_setting('probe.id')::uuid, 'ja', '');
RESET SESSION AUTHORIZATION;
DO $$
DECLARE v record;
BEGIN
    SELECT * INTO v FROM marketing.formular_vorlage('terminkarte');
    IF v.freigegebene_gestalt IS NULL OR v.freigegebene_fassung IS NULL THEN
        RAISE EXCEPTION 'nicht freigegeben: %', v;
    END IF;
    BEGIN
        UPDATE marketing.layout_vorlagen SET freigegebene_gestalt = '{}'::jsonb
         WHERE name = 'terminkarte';
        RAISE EXCEPTION 'PROBE: freigegebene Gestalt UEBERSCHRIEBEN';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
END $$;

-- Verbotene Uebergaenge. Der eigene Waechtersatz endet auf „ANGENOMMEN" —
-- er darf nicht auf die Meldung der Datenbank passen, sonst loeste sich die
-- Probe selbst aus.
DO $$ BEGIN
    BEGIN
        UPDATE marketing.vorlagenauftraege SET status = 'neu'
         WHERE id = current_setting('probe.id')::uuid;
        RAISE EXCEPTION 'PROBE: freigegeben -> neu ANGENOMMEN';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
END $$;

-- Eine Formular-Vorlage wird NUR ueber vorlagenauftrag_urteil freigegeben,
-- nie direkt (z. B. ueber die Layout-API am Mitglied vorbei).
--
-- Ruling 4: marketing.formular_freigabe muss vor dieser Probe wieder auf ''
-- stehen. vorlagenauftrag_urteil('ja') setzt es transaktionslokal auf 'an'
-- (set_config(..., true) = SET LOCAL, gilt bis zum Transaktionsende); ohne
-- diesen Reset waere das Tor hier noch offen und die direkte Freigabe
-- unten wuerde durchgehen, obwohl sie es nicht darf. Die Migration setzt es
-- selbst schon direkt nach dem UPDATE in vorlagenauftrag_urteil zurueck -
-- dieser Reset macht die Probe zusaetzlich von dieser Selbstdisziplin
-- unabhaengig.
SELECT set_config('marketing.formular_freigabe', '', true);
DO $$ BEGIN
    INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, art,
                                           vorgeschlagen_von)
    VALUES ('probe-formular', '', (SELECT g FROM probe_gestalt), 'vorschlag', 'formular', 'probe');
    BEGIN
        UPDATE marketing.layout_vorlagen SET status = 'freigegeben',
               entschieden_von = 'betreiber', entschieden_am = now()
         WHERE name = 'probe-formular';
        RAISE EXCEPTION 'PROBE: direkte Freigabe ANGENOMMEN';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
END $$;

ROLLBACK;
\echo verify_045: alle Proben bestanden
