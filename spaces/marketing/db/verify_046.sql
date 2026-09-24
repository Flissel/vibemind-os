-- verify_046.sql — Haertung von 045 (Fix-Runde 1: I-1, I-2, I-3, I-4, M-4,
-- M-6). Jede Probe hier muss OHNE 046 scheitern (RED) und MIT 046 bestehen
-- (GREEN). Laeuft komplett in einer Transaktion, die am Ende zurueckgerollt
-- wird - nichts hier bleibt in der Datenbank stehen.
BEGIN;

-- ---------------------------------------------------------------------------
-- I-3: Gestalten, die vorher durch NULL-durchlaessige Vergleiche rutschten.
DO $$
DECLARE v_fehler text;
BEGIN
    -- Komplett fehlendes 'felder' - vorher: jsonb_typeof(NULL) <> 'array'
    -- ist NULL, nicht TRUE, also durchgelassen.
    v_fehler := marketing._formular_gestalt_fehler(
        '{"seite":{"breite_mm":148,"hoehe_mm":105}}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: fehlendes felder ANGENOMMEN'; END IF;

    -- Feld ganz ohne platz.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"kunde.name"}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: Feld ohne platz ANGENOMMEN'; END IF;

    -- platz ohne hoehe.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":30}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: platz ohne hoehe ANGENOMMEN'; END IF;

    -- platz.hoehe explizit JSON null (Schluessel da, Wert leer).
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":30,"hoehe":null}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: platz.hoehe = null ANGENOMMEN'; END IF;

    -- Fester Text mit unvollstaendigem platz (dieselbe Luecke wie bei felder).
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"kunde.name",
                   "platz":{"x":8,"y":20,"breite":30,"hoehe":12}}],
        "texte":[{"text":"HALLO","platz":{"x":8,"y":6}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: Text ohne vollstaendigen platz ANGENOMMEN'; END IF;
END $$;

-- ---------------------------------------------------------------------------
-- I-4: quelle ist genau eine von 11 Werten, kein Muster (kunde.foo passte
-- vorher auf '^(frei|(kunde|termin|mitglied)\.[a-z_]{2,20})$').
DO $$
DECLARE v_fehler text;
BEGIN
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"kunde.foo",
                   "platz":{"x":8,"y":20,"breite":30,"hoehe":12}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: unerlaubte quelle kunde.foo ANGENOMMEN'; END IF;

    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[{"name":"x","beschriftung":"X","art":"text","quelle":"mitglied.telefon",
                   "platz":{"x":8,"y":20,"breite":30,"hoehe":12}}]}'::jsonb);
    IF v_fehler IS NULL THEN RAISE EXCEPTION 'PROBE: unerlaubte quelle mitglied.telefon ANGENOMMEN'; END IF;

    -- Gegenprobe: alle 11 erlaubten Werte muessen weiter durchgehen.
    v_fehler := marketing._formular_gestalt_fehler('{"seite":{"breite_mm":148,"hoehe_mm":105},
        "felder":[
          {"name":"a","beschriftung":"A","art":"text","quelle":"kunde.name","platz":{"x":8,"y":6,"breite":30,"hoehe":8}},
          {"name":"b","beschriftung":"B","art":"telefon","quelle":"kunde.telefon","platz":{"x":8,"y":16,"breite":30,"hoehe":8}},
          {"name":"c","beschriftung":"C","art":"text","quelle":"kunde.email","platz":{"x":8,"y":26,"breite":30,"hoehe":8}},
          {"name":"d","beschriftung":"D","art":"text","quelle":"kunde.firma","platz":{"x":8,"y":36,"breite":30,"hoehe":8}},
          {"name":"e","beschriftung":"E","art":"datum","quelle":"termin.datum","platz":{"x":8,"y":46,"breite":30,"hoehe":8}},
          {"name":"f2","beschriftung":"F","art":"uhrzeit","quelle":"termin.uhrzeit","platz":{"x":8,"y":56,"breite":30,"hoehe":8}},
          {"name":"g","beschriftung":"G","art":"text","quelle":"termin.dauer","platz":{"x":8,"y":66,"breite":30,"hoehe":8}},
          {"name":"h","beschriftung":"H","art":"text","quelle":"termin.thema","platz":{"x":8,"y":76,"breite":30,"hoehe":8}},
          {"name":"i","beschriftung":"I","art":"text","quelle":"termin.ort","platz":{"x":8,"y":86,"breite":30,"hoehe":8}},
          {"name":"j","beschriftung":"J","art":"text","quelle":"mitglied.name","platz":{"x":60,"y":6,"breite":30,"hoehe":8}},
          {"name":"k","beschriftung":"K","art":"mehrzeilig","quelle":"frei","platz":{"x":60,"y":16,"breite":30,"hoehe":8}}
        ]}'::jsonb);
    IF v_fehler IS NOT NULL THEN
        RAISE EXCEPTION 'PROBE: erlaubte Quelle faelschlich abgelehnt: %', v_fehler;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- I-1 + I-2: Schreibschutz auf Formular-Zeilen. Eine legitim per vorlegen-
-- Tor angelegte Vorschlags-Zeile als Basis.
CREATE TEMP TABLE probe046_gestalt AS SELECT '{
  "seite": {"breite_mm": 148, "hoehe_mm": 105},
  "felder": [{"name": "kunde", "beschriftung": "Kunde", "art": "text", "quelle": "kunde.name",
              "platz": {"x": 8, "y": 20, "breite": 130, "hoehe": 12}}]}'::jsonb AS g;

DO $$ BEGIN
    PERFORM set_config('marketing.formular_vorlegen', 'an', true);
    INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, art,
                                           vorgeschlagen_von, fassung)
    VALUES ('probe046-formular', '', (SELECT g FROM probe046_gestalt), 'vorschlag', 'formular', 'probe', 1);
    PERFORM set_config('marketing.formular_vorlegen', '', true);
END $$;

-- I-1: eine direkte INSERT mit status='freigegeben' umging vorher beide
-- BEFORE-UPDATE-Trigger (die feuerten nie bei INSERT). Der neue Trigger
-- feuert BEFORE INSERT OR UPDATE.
DO $$ BEGIN
    BEGIN
        INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, art,
                    vorgeschlagen_von, freigegebene_gestalt, freigegebene_fassung, fassung)
        VALUES ('probe046-insert-freigegeben', '', (SELECT g FROM probe046_gestalt),
                'freigegeben', 'formular', 'probe', (SELECT g FROM probe046_gestalt), 1, 1);
        RAISE EXCEPTION 'PROBE: INSERT mit status freigegeben ANGENOMMEN';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
        IF SQLERRM NOT LIKE '%nur ueber vorlagenauftrag_urteil%' THEN
            RAISE EXCEPTION 'PROBE: falsche Fehlermeldung bei INSERT freigegeben: %', SQLERRM;
        END IF;
    END;
END $$;

-- I-2: layout_vorschlagen (die Agent-API ohne Schluessel) darf keine
-- Formular-Zeile ueberschreiben.
DO $$ DECLARE r jsonb; BEGIN
    BEGIN
        r := marketing.layout_vorschlagen('probe046-formular', 'boese Farbtafel',
             '{"grund":"#000000","flaeche":"#111111","akzent":"#222222","gold":"#333333",
               "text":"#444444","text_hell":"#555555","text_leise":"#666666",
               "handlung_text":"#777777"}'::jsonb, 'boeser-agent');
        RAISE EXCEPTION 'PROBE: layout_vorschlagen auf Formular-Zeile ANGENOMMEN: %', r;
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
END $$;

-- I-2: layout_entscheiden('abgelehnt') darf ebenfalls keine Formular-Zeile
-- anfassen (die Sperre gilt nicht nur fuer 'freigegeben').
DO $$ DECLARE r jsonb; BEGIN
    BEGIN
        r := marketing.layout_entscheiden('probe046-formular', 'abgelehnt', 'betreiber', 'Testgrund');
        RAISE EXCEPTION 'PROBE: layout_entscheiden(abgelehnt) auf Formular-Zeile ANGENOMMEN: %', r;
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
END $$;

-- Art-Wechsel ist nie erlaubt, auch nicht formular -> layout von Hand.
DO $$ BEGIN
    BEGIN
        UPDATE marketing.layout_vorlagen SET art = 'layout' WHERE name = 'probe046-formular';
        RAISE EXCEPTION 'PROBE: Art-Wechsel formular -> layout ANGENOMMEN';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
END $$;

-- ---------------------------------------------------------------------------
-- M-4: der SELECT-dann-INSERT-Weg ist racy; der echte Riegel ist der
-- partielle Unique-Index. Zwei rohe INSERTs (nicht ueber anlegen, damit die
-- SELECT-Pruefung der Funktion gar nicht erst greifen kann) zeigen, dass der
-- Index selbst die zweite offene Zeile verhindert.
DO $$ BEGIN
    INSERT INTO marketing.vorlagenauftraege (laden, art, beschreibung, vorlage)
    VALUES ('probe046laden', 'terminkarte', 'x', 'terminkarte');
    BEGIN
        INSERT INTO marketing.vorlagenauftraege (laden, art, beschreibung, vorlage)
        VALUES ('probe046laden2', 'terminkarte', 'y', 'terminkarte');
        RAISE EXCEPTION 'PROBE: zweite offene Zeile ohne Index-Schutz ANGENOMMEN';
    EXCEPTION WHEN unique_violation THEN
        NULL; -- erwartet: der partielle Unique-Index blockt
    WHEN raise_exception THEN
        IF SQLERRM LIKE 'PROBE:%' THEN RAISE; END IF;
    END;
    -- Aufraeumen: sonst blockt diese rohe Zeile die spaeteren M-6-Proben
    -- unten, die ueber vorlagenauftrag_anlegen echte Auftraege anlegen.
    DELETE FROM marketing.vorlagenauftraege WHERE laden = 'probe046laden';
END $$;

-- Ergaenzend: wenn eine konkurrierende Zeile schon existiert (gleich, ob sie
-- ueber anlegen oder roh entstand), antwortet anlegen selbst menschenlesbar
-- - das deckt den SELECT-Pfad UND, ueber denselben Code, das Format der
-- unique_violation-Antwort ab (siehe 046: beide geben dieselbe Formulierung
-- zurueck).
DO $$ DECLARE r jsonb; BEGIN
    INSERT INTO marketing.vorlagenauftraege (laden, art, beschreibung, vorlage)
    VALUES ('probe046laden3', 'terminkarte', 'x', 'terminkarte');
    SET SESSION AUTHORIZATION sales_app;
    r := marketing.vorlagenauftrag_anlegen('terminkarte', NULL, NULL, 'kollidiert', '');
    RESET SESSION AUTHORIZATION;
    IF (r->>'ok')::boolean THEN
        RAISE EXCEPTION 'PROBE: anlegen trotz offener Fremdzeile ANGENOMMEN: %', r;
    END IF;
    IF coalesce(r->>'grund', '') NOT LIKE '%laeuft schon ein Auftrag%' THEN
        RAISE EXCEPTION 'PROBE: falsche Ablehnung bei Kollision: %', r;
    END IF;
    DELETE FROM marketing.vorlagenauftraege WHERE laden = 'probe046laden3';
END $$;

-- ---------------------------------------------------------------------------
-- M-6(a): urteil('ja') darf nicht freigeben, wenn ihr eigenes UPDATE auf
-- layout_vorlagen keine Zeile mehr trifft (Zeile fehlt/wurde umbenannt).
SET SESSION AUTHORIZATION sales_app;
DO $$ DECLARE r jsonb; BEGIN
    r := marketing.vorlagenauftrag_anlegen('terminkarte', NULL, NULL, 'M-6a Probe', '');
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'M-6a setup anlegen: %', r; END IF;
    PERFORM set_config('probe046a.id', r->>'id', true);
END $$;
RESET SESSION AUTHORIZATION;
DO $$ DECLARE r jsonb; BEGIN
    PERFORM * FROM marketing.vorlagenauftrag_uebernehmen();
    r := marketing.vorlagenauftrag_vorlegen(current_setting('probe046a.id')::uuid,
                                            (SELECT g FROM probe046_gestalt));
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'M-6a setup vorlegen: %', r; END IF;
END $$;
-- Die Vorlagen-Zeile verschwindet (z. B. von Hand geloescht).
DELETE FROM marketing.layout_vorlagen WHERE name = 'terminkarte';
SET SESSION AUTHORIZATION sales_app;
DO $$ DECLARE r jsonb; BEGIN
    r := marketing.vorlagenauftrag_urteil(current_setting('probe046a.id')::uuid, 'ja', '');
    IF (r->>'ok')::boolean THEN
        RAISE EXCEPTION 'PROBE: urteil(ja) freigegeben trotz fehlender Vorlagen-Zeile';
    END IF;
END $$;
RESET SESSION AUTHORIZATION;
-- Platz fuer die naechste Probe freimachen (M-4-Index laesst nur einen
-- offenen Auftrag pro art zu) - dieser Auftrag ist ohnehin nur Testdaten.
DELETE FROM marketing.vorlagenauftraege WHERE id = current_setting('probe046a.id')::uuid;

-- ---------------------------------------------------------------------------
-- M-6(b): vorlegen darf eine bestehende LAYOUT-Zeile mit demselben Namen
-- nicht kapern (ON CONFLICT setzte vorher nie 'art').
INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, art, vorgeschlagen_von)
VALUES ('terminkarte', '', '{"grund":"#000000","flaeche":"#111111","akzent":"#222222",
    "gold":"#333333","text":"#444444","text_hell":"#555555","text_leise":"#666666",
    "handlung_text":"#777777"}'::jsonb, 'vorschlag', 'layout', 'bestand');

SET SESSION AUTHORIZATION sales_app;
DO $$ DECLARE r jsonb; BEGIN
    r := marketing.vorlagenauftrag_anlegen('terminkarte', NULL, NULL, 'M-6b Probe', '');
    IF NOT (r->>'ok')::boolean THEN RAISE EXCEPTION 'M-6b setup anlegen: %', r; END IF;
    PERFORM set_config('probe046b.id', r->>'id', true);
END $$;
RESET SESSION AUTHORIZATION;
DO $$ DECLARE r jsonb; BEGIN
    PERFORM * FROM marketing.vorlagenauftrag_uebernehmen();
    r := marketing.vorlagenauftrag_vorlegen(current_setting('probe046b.id')::uuid,
                                            (SELECT g FROM probe046_gestalt));
    IF (r->>'ok')::boolean THEN
        RAISE EXCEPTION 'PROBE: vorlegen kaperte eine bestehende Layout-Zeile: %', r;
    END IF;
    IF (SELECT art FROM marketing.layout_vorlagen WHERE name = 'terminkarte') <> 'layout' THEN
        RAISE EXCEPTION 'PROBE: die Layout-Zeile terminkarte wurde trotzdem veraendert';
    END IF;
END $$;

ROLLBACK;
\echo verify_046: alle Proben bestanden
