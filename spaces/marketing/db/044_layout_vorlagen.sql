-- 044_layout_vorlagen.sql — Layouts werden Daten, und eine Vorlage gilt erst
-- nach der Freigabe des Betreibers (Auftrag 12.09.2026).
--
-- WARUM NICHT marketing.templates: die Tabelle gibt es, aber sie ist etwas
-- anderes. Sie traegt Betreff, body_html, body_text und Merge-Variablen — eine
-- NACHRICHTEN-Vorlage fuer den Serienbrief. Hier geht es um die GESTALT:
-- Farben, Schriftgrade, Kopf- und Fussband. Beides in eine Tabelle zu
-- zwingen, weil beide „Vorlage" heissen, waere derselbe Fehler wie
-- `/api/proposals` gegen `/api/broadcast_proposals` (WEN gegen WAS).
-- Nebenbefund: `broadcast_proposals.draft_template_id` ist in 30 Zeilen
-- 0x gesetzt — die bestehende Tabelle wird nicht einmal benutzt.
--
-- WARUM EIN TOR: ein Layout ist das, was ein Kunde von diesem Haus SIEHT.
-- Ein Agent darf es vorschlagen; freigeben tut es ein Mensch. Dasselbe
-- Verhaeltnis wie bei Entwuerfen — nur dass eine Vorlage laenger lebt und
-- deshalb mehr Schaden anrichtet, wenn sie niemand angesehen hat.

CREATE TABLE IF NOT EXISTS marketing.layout_vorlagen (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Der Name, mit dem ein Werkzeug sie anfordert. Eindeutig, damit
    -- `pdf_erstellen(vorlage='dunkel')` nie zwei Treffer hat.
    name            text NOT NULL UNIQUE
                    CHECK (name ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
    beschreibung    text NOT NULL DEFAULT '',
    -- Die Gestalt als Daten: dieselben Schluessel, die pdf.py bisher fest
    -- verdrahtet hatte. Geprueft wird sie von marketing.gestalt_pruefen()
    -- unten — eine freigegebene Vorlage mit fehlendem Schluessel wuerde den
    -- Setzer erst beim Erzeugen zerlegen, also lange nach der Freigabe.
    gestalt         jsonb NOT NULL,
    status          text NOT NULL DEFAULT 'vorschlag'
                    CHECK (status IN ('vorschlag', 'freigegeben', 'abgelehnt')),
    vorgeschlagen_von text NOT NULL,
    -- Audit: jeder Zustandswechsel hinterlaesst einen Akteur. Wortgleiche
    -- Regel wie bei broadcast_proposals (030).
    entschieden_von text,
    entschieden_am  timestamptz,
    grund           text NOT NULL DEFAULT '',
    -- Wie das Muster aussah, das der Betreiber beim Entscheiden vor sich
    -- hatte. Ohne diesen Verweis ist „freigegeben" eine Behauptung ueber
    -- etwas, das niemand gesehen hat.
    muster_datei    text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT layout_vorlagen_entscheidung_hat_akteur CHECK (
        entschieden_am IS NULL OR entschieden_von IS NOT NULL
    ),
    CONSTRAINT layout_vorlagen_ablehnung_hat_grund CHECK (
        status <> 'abgelehnt' OR length(btrim(grund)) > 0
    )
);

CREATE INDEX IF NOT EXISTS idx_layout_vorlagen_freigegeben
    ON marketing.layout_vorlagen(name) WHERE status = 'freigegeben';

CREATE OR REPLACE FUNCTION marketing._layout_vorlagen_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_layout_vorlagen_updated_at ON marketing.layout_vorlagen;
CREATE TRIGGER trg_layout_vorlagen_updated_at
    BEFORE UPDATE ON marketing.layout_vorlagen
    FOR EACH ROW EXECUTE FUNCTION marketing._layout_vorlagen_updated_at();


-- ---------------------------------------------------------------------------
-- Pruefung der Gestalt
-- ---------------------------------------------------------------------------
-- Acht Schluessel, alle als #rrggbb. Die Liste ist nicht willkuerlich: es
-- sind genau die, die pdf.py verwendet. Fehlt einer, faellt das Setzen mit
-- einem KeyError aus — und zwar erst, wenn jemand die Vorlage benutzt.
CREATE OR REPLACE FUNCTION marketing.gestalt_pruefen(p_gestalt jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_noetig text[] := ARRAY['grund', 'flaeche', 'akzent', 'gold', 'text',
                             'text_hell', 'text_leise', 'handlung_text'];
    v_k      text;
    v_wert   text;
    v_fehlt  text[] := '{}';
BEGIN
    IF p_gestalt IS NULL OR jsonb_typeof(p_gestalt) <> 'object' THEN
        RETURN 'gestalt muss ein JSON-Objekt sein';
    END IF;
    FOREACH v_k IN ARRAY v_noetig LOOP
        IF NOT (p_gestalt ? v_k) THEN
            v_fehlt := v_fehlt || v_k;
        ELSE
            v_wert := lower(btrim(p_gestalt ->> v_k));
            IF v_wert !~ '^#[0-9a-f]{6}$' THEN
                RETURN format('%s = %L ist keine Farbe der Form #rrggbb',
                              v_k, p_gestalt ->> v_k);
            END IF;
        END IF;
    END LOOP;
    IF array_length(v_fehlt, 1) > 0 THEN
        RETURN 'es fehlen: ' || array_to_string(v_fehlt, ', ');
    END IF;
    -- Lesbarkeit ist kein Geschmack: Text auf seinem eigenen Grund ist
    -- unsichtbar, und das faellt erst dem Kunden auf.
    IF lower(p_gestalt ->> 'text') = lower(p_gestalt ->> 'grund') THEN
        RETURN 'text und grund sind dieselbe Farbe — der Text waere unsichtbar';
    END IF;
    IF lower(p_gestalt ->> 'handlung_text') = lower(p_gestalt ->> 'akzent') THEN
        RETURN 'handlung_text und akzent sind dieselbe Farbe — der Handlungs'
               || 'kasten waere leer (der Kasten ist in akzent gefuellt)';
    END IF;
    RETURN NULL;
END $$;


-- ---------------------------------------------------------------------------
-- Vorschlagen (Agent) und Entscheiden (Mensch)
-- ---------------------------------------------------------------------------
-- Getrennte Funktionen, weil es getrennte Rollen sind. Ein Agent, der beide
-- rufen koennte, haette kein Tor, sondern eine Formalie.
CREATE OR REPLACE FUNCTION marketing.layout_vorschlagen(
    p_name text, p_beschreibung text, p_gestalt jsonb, p_von text)
RETURNS jsonb
LANGUAGE plpgsql SET search_path = marketing, pg_temp AS $$
DECLARE
    v_fehler text;
    v_id     uuid;
    v_alt    record;
BEGIN
    IF coalesce(btrim(p_name), '') = '' THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'name fehlt');
    END IF;
    IF btrim(lower(p_name)) !~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$' THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            'name darf nur Kleinbuchstaben, Ziffern und Bindestriche tragen '
            '(3 bis 40 Zeichen) — er wird zum Werkzeug-Argument');
    END IF;
    v_fehler := marketing.gestalt_pruefen(p_gestalt);
    IF v_fehler IS NOT NULL THEN
        RETURN jsonb_build_object('ok', false, 'grund', v_fehler);
    END IF;

    SELECT id, status INTO v_alt FROM marketing.layout_vorlagen
     WHERE name = btrim(lower(p_name));
    IF FOUND THEN
        -- Eine FREIGEGEBENE Vorlage wird nie still ueberschrieben: der
        -- Betreiber hat genau dieses Aussehen abgenommen. Wer es aendern
        -- will, schlaegt eine neue unter neuem Namen vor.
        IF v_alt.status = 'freigegeben' THEN
            RETURN jsonb_build_object('ok', false, 'grund', format(
                'Die Vorlage %L ist bereits freigegeben und wird nicht '
                'ueberschrieben. Schlag eine neue unter einem anderen Namen '
                'vor (z. B. %L).', btrim(lower(p_name)),
                btrim(lower(p_name)) || '-v2'));
        END IF;
        UPDATE marketing.layout_vorlagen
           SET beschreibung = coalesce(p_beschreibung, ''), gestalt = p_gestalt,
               status = 'vorschlag', vorgeschlagen_von = p_von,
               entschieden_von = NULL, entschieden_am = NULL, grund = '',
               muster_datei = ''
         WHERE id = v_alt.id;
        RETURN jsonb_build_object('ok', true, 'id', v_alt.id, 'neu', false,
            'grund', 'Vorschlag ersetzt. Er wartet auf die Freigabe des Betreibers.');
    END IF;

    INSERT INTO marketing.layout_vorlagen
        (name, beschreibung, gestalt, vorgeschlagen_von)
    VALUES (btrim(lower(p_name)), coalesce(p_beschreibung, ''), p_gestalt, p_von)
    RETURNING id INTO v_id;
    RETURN jsonb_build_object('ok', true, 'id', v_id, 'neu', true,
        'grund', 'Vorschlag angelegt. Er wartet auf die Freigabe des Betreibers.');
END $$;


CREATE OR REPLACE FUNCTION marketing.layout_entscheiden(
    p_name text, p_status text, p_von text, p_grund text DEFAULT '')
RETURNS jsonb
LANGUAGE plpgsql SET search_path = marketing, pg_temp AS $$
DECLARE v_n int; v_fehler text; v_gestalt jsonb;
BEGIN
    IF p_status NOT IN ('freigegeben', 'abgelehnt') THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            'status muss freigegeben oder abgelehnt sein');
    END IF;
    IF coalesce(btrim(p_von), '') = '' THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            'Eine Entscheidung braucht einen Akteur — wer hat sie getroffen?');
    END IF;
    IF p_status = 'abgelehnt' AND coalesce(btrim(p_grund), '') = '' THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            'Eine Ablehnung braucht einen Grund, den der Vorschlagende lesen kann');
    END IF;

    SELECT gestalt INTO v_gestalt FROM marketing.layout_vorlagen
     WHERE name = btrim(lower(p_name));
    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            format('Keine Vorlage %L.', btrim(lower(p_name))));
    END IF;
    -- Noch einmal pruefen, kurz vor der Freigabe: zwischen Vorschlag und
    -- Entscheidung kann die Zeile von Hand geaendert worden sein, und eine
    -- freigegebene kaputte Vorlage faellt erst dem Kunden auf.
    IF p_status = 'freigegeben' THEN
        v_fehler := marketing.gestalt_pruefen(v_gestalt);
        IF v_fehler IS NOT NULL THEN
            RETURN jsonb_build_object('ok', false, 'grund',
                'Diese Gestalt ist nicht setzbar: ' || v_fehler);
        END IF;
    END IF;

    UPDATE marketing.layout_vorlagen
       SET status = p_status, entschieden_von = btrim(p_von),
           entschieden_am = now(), grund = coalesce(p_grund, '')
     WHERE name = btrim(lower(p_name));
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN jsonb_build_object('ok', v_n = 1, 'grund',
        CASE WHEN v_n = 1 THEN 'Entscheidung festgehalten.'
             ELSE 'Keine Zeile geaendert.' END);
END $$;


-- ---------------------------------------------------------------------------
-- Der Bestand: was heute schon gesetzt wird
-- ---------------------------------------------------------------------------
-- `dunkel` kommt FREIGEGEBEN herein, und zwar mit Beleg: ein Leser hat es am
-- 11.09.2026 in einem echten PDF beurteilt und die Schriftfarbe ausdruecklich
-- bestaetigt („Finde gut, dass die Schriftfarbe nicht ganz weiss ist").
-- Das IST eine Freigabe, nur nicht in dieser Tabelle.
--
-- `hell` kommt als VORSCHLAG herein. Es existiert im Code, aber niemand hat
-- es je gesehen. Es hier stillschweigend mitzugenehmigen waere genau die
-- Abkuerzung, die der Auftrag verbietet.
INSERT INTO marketing.layout_vorlagen
    (name, beschreibung, gestalt, status, vorgeschlagen_von,
     entschieden_von, entschieden_am, grund)
VALUES
    ('dunkel',
     'Pitch-Deck-Gewand fuer den Bildschirm: dunkler Grund, tuerkiser Akzent.',
     '{"grund":"#0f2422","flaeche":"#1d3b39","akzent":"#5eead4","gold":"#fbbf24",
       "text":"#cfe3df","text_hell":"#e9fbf6","text_leise":"#8aa3a0",
       "handlung_text":"#0f2422"}'::jsonb,
     'freigegeben', 'bestand:pdf.py',
     'betreiber', now(),
     'Bestand seit 04.09.2026, am 11.09.2026 von einem Leser an einem echten '
     'PDF beurteilt und in der Schriftfarbe ausdruecklich bestaetigt.'),
    ('hell',
     'Dasselbe Geruest fuer Druck und Weiterleitung, wo dunkle Flaechen stoeren.',
     '{"grund":"#ffffff","flaeche":"#0f2422","akzent":"#0f7a6c","gold":"#a06a00",
       "text":"#1f2937","text_hell":"#0f2422","text_leise":"#6b7280",
       "handlung_text":"#ffffff"}'::jsonb,
     'vorschlag', 'bestand:pdf.py', NULL, NULL,
     'Existiert im Code, wurde aber nie von einem Menschen angesehen.')
ON CONFLICT (name) DO NOTHING;
