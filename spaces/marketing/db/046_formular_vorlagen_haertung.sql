-- 046_formular_vorlagen_haertung.sql — Haertung von 045 nach Fix-Runde 1
-- (task-1-fix1-findings.md, 2026-09-24). 045 selbst bleibt unveraendert (ist
-- bereits produktiv angewendet); dies hier schliesst die Luecken, die die
-- Review-Runde gefunden hat. Additiv, idempotent, in einer Transaktion.
BEGIN;

-- ---------------------------------------------------------------------------
-- I-1 + I-2 + M-6(b): EIN Schreibschutz-Trigger auf layout_vorlagen statt des
-- alten trg_formular_freigabe. Der alte Trigger a) feuerte nur bei UPDATE,
-- also nie bei INSERT (ein INSERT mit art='formular', status='freigegeben'
-- kam an ihm vorbei), und b) prüfte nur den Statuswechsel NACH freigegeben,
-- nicht Aenderungen an freigegebene_gestalt/freigegebene_fassung fuer sich
-- (die erste Freigabe hatte OLD.status <> 'freigegeben' als Bedingung, aber
-- eine SPAETERE UPDATE, die nur freigegebene_gestalt aendert, ohne den
-- Status anzufassen, ist davon unbetroffen).
--
-- Neu: JEDE Schreibaktion (INSERT oder UPDATE) auf eine Formular-Zeile (NEW
-- oder OLD hat art='formular') braucht eines der zwei transaktionslokalen
-- Tore: marketing.formular_vorlegen='an' (gesetzt von vorlagenauftrag_
-- vorlegen) oder marketing.formular_freigabe='an' (gesetzt von
-- vorlagenauftrag_urteil). Eine Aenderung an freigegebene_gestalt,
-- freigegebene_fassung oder ein Wechsel auf status=freigegeben braucht
-- zwingend das Freigabe-Tor, nicht nur irgendeins der beiden - das schliesst
-- I-1 (INSERT direkt auf freigegeben) und die Regel aus dem Design-Vorschlag.
-- Das schliesst zugleich layout_vorschlagen und layout_entscheiden (I-2) und
-- jede Handarbeit auf einer Formular-Zeile, weil keiner von denen die Tore
-- setzt. Der Art-Wechsel selbst (formular<->layout) ist unabhaengig davon nie
-- erlaubt (M-6b baut zusaetzlich eine Namenspruefung in vorlegen ein, aber
-- diese Zeile hier ist die zweite, unabhaengige Sperre auf DB-Ebene).
--
-- Namenswahl wichtig: Postgres feuert mehrere BEFORE-ROW-Trigger derselben
-- Tabelle in ALPHABETISCHER Reihenfolge ihres Namens, nicht der
-- Erzeugungsreihenfolge. trg_freigabe_unveraenderlich (045) muss VOR diesem
-- Trigger feuern, sonst faengt DIESER Trigger den Fall "freigegebene_gestalt
-- einer schon freigegebenen Zeile wird von Hand geaendert" zuerst ab und
-- meldet "wird nur ueber vorlagenauftrag_urteil freigegeben" statt
-- "unveraenderlich" (M-1 prueft genau diese Meldung). "trg_freigabe..." <
-- "trg_formular..." alphabetisch (o < r an Position 5) - ein Trigger-Name,
-- der mit "trg_formular" beginnt, faehrt FRUEHER als "trg_freigabe" und war
-- deshalb falsch. "trg_schreibschutz_..." feuert sicher danach.
CREATE OR REPLACE FUNCTION marketing._schreibschutz_formular_vorlagen() RETURNS trigger AS $$
DECLARE
    v_vorlegen boolean := coalesce(current_setting('marketing.formular_vorlegen', true), '') = 'an';
    v_freigabe boolean := coalesce(current_setting('marketing.formular_freigabe', true), '') = 'an';
    v_betrifft_formular boolean;
    v_freigabe_aenderung boolean;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.art IS DISTINCT FROM NEW.art
       AND (OLD.art = 'formular' OR NEW.art = 'formular') THEN
        RAISE EXCEPTION 'Vorlage % kann die Art nicht wechseln (% -> %)', OLD.name, OLD.art, NEW.art;
    END IF;

    v_betrifft_formular := (NEW.art = 'formular') OR (TG_OP = 'UPDATE' AND OLD.art = 'formular');
    IF NOT v_betrifft_formular THEN
        RETURN NEW;
    END IF;

    v_freigabe_aenderung :=
        (TG_OP = 'INSERT' AND (NEW.status = 'freigegeben'
            OR NEW.freigegebene_gestalt IS NOT NULL OR NEW.freigegebene_fassung IS NOT NULL))
        OR (TG_OP = 'UPDATE' AND (
            (NEW.status = 'freigegeben' AND OLD.status IS DISTINCT FROM 'freigegeben')
            OR NEW.freigegebene_gestalt IS DISTINCT FROM OLD.freigegebene_gestalt
            OR NEW.freigegebene_fassung IS DISTINCT FROM OLD.freigegebene_fassung));

    IF v_freigabe_aenderung THEN
        IF NOT v_freigabe THEN
            RAISE EXCEPTION 'Formular-Vorlage % wird nur ueber vorlagenauftrag_urteil freigegeben', NEW.name;
        END IF;
        RETURN NEW;
    END IF;

    IF NOT (v_vorlegen OR v_freigabe) THEN
        RAISE EXCEPTION 'Formular-Vorlage % wird nur ueber vorlagenauftrag_vorlegen oder vorlagenauftrag_urteil geschrieben, nicht direkt', NEW.name;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_formular_freigabe ON marketing.layout_vorlagen;
DROP FUNCTION IF EXISTS marketing._formular_freigabe_nur_per_urteil();
-- Aufraeumen einer fruehen, falsch benannten Fassung dieses Triggers (siehe
-- Namenshinweis oben) - idempotent, falls diese Datei schon einmal in dieser
-- Form lief.
DROP TRIGGER IF EXISTS trg_formular_vorlagen_schreibschutz ON marketing.layout_vorlagen;
DROP FUNCTION IF EXISTS marketing._formular_vorlagen_schreibschutz();
DROP TRIGGER IF EXISTS trg_schreibschutz_formular_vorlagen ON marketing.layout_vorlagen;
CREATE TRIGGER trg_schreibschutz_formular_vorlagen BEFORE INSERT OR UPDATE ON marketing.layout_vorlagen
    FOR EACH ROW EXECUTE FUNCTION marketing._schreibschutz_formular_vorlagen();

-- ---------------------------------------------------------------------------
-- M-4: eine echte Sperre statt SELECT-dann-INSERT. vorlagenauftrag_anlegen
-- prueft "gibt es schon einen offenen Auftrag" per SELECT und legt danach an
-- - zwischen den zwei Anweisungen kann ein zweiter, gleichzeitiger Aufruf
-- denselben Weg gehen. Der partielle Unique-Index ist die eigentliche
-- Sperre; anlegen faengt seine Verletzung unten ab und antwortet genauso
-- menschenlesbar wie der SELECT-Pfad.
CREATE UNIQUE INDEX IF NOT EXISTS idx_vorlagenauftraege_ein_offener
    ON marketing.vorlagenauftraege(art)
    WHERE status NOT IN ('freigegeben', 'gescheitert');

-- ---------------------------------------------------------------------------
-- I-3 + I-4: _formular_gestalt_fehler pruefte mit NULL-durchlaessigen
-- Vergleichen (jsonb_typeof(NULL) <> 'array' ist NULL, nicht TRUE; eine IF-
-- Bedingung, die NULL auswertet, laesst plpgsql durchlaufen wie FALSE). Das
-- liess ein komplett fehlendes 'felder', ein Feld ohne 'platz' und ein platz
-- ohne einzelne Zahlen durch. quelle war ein Muster statt einer festen
-- Liste. Diese Fassung prueft Vorhandensein explizit, bevor sie etwas
-- vergleicht, und laesst quelle nur die 11 erlaubten Werte zu.
CREATE OR REPLACE FUNCTION marketing._formular_gestalt_fehler(g jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    b numeric; h numeric; f jsonb; t jsonb; p jsonb; namen text[] := '{}';
BEGIN
    IF jsonb_typeof(g) <> 'object' THEN RETURN 'Gestalt ist kein Objekt'; END IF;
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

-- ---------------------------------------------------------------------------
-- M-4: anlegen faengt jetzt auch den unique_violation der echten Sperre ab
-- und antwortet mit derselben Formulierung wie der SELECT-Pfad (dafuer wird
-- v_offen hier noch einmal nachgeladen - beim Abfangen der Exception steht
-- es nicht mehr aus der urspruenglichen SELECT-Anweisung zur Verfuegung).
CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_anlegen(
    p_art text, p_bild bytea, p_bild_typ text, p_beschreibung text, p_anmerkung text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
DECLARE v_laden text := marketing._laden_des_aufrufers(); v_id uuid; v_offen record;
BEGIN
    IF p_art IS DISTINCT FROM 'terminkarte' THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Nur Terminkarten koennen bestellt werden.');
    END IF;
    IF (p_bild IS NULL) = (length(btrim(coalesce(p_beschreibung, ''))) = 0) THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            'Entweder ein Foto ODER eine Beschreibung der Felder - genau eins von beiden.');
    END IF;
    SELECT id, status, laden INTO v_offen FROM vorlagenauftraege
     WHERE art = p_art AND status NOT IN ('freigegeben', 'gescheitert') LIMIT 1;
    IF FOUND THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Es laeuft schon ein Auftrag fuer diese Teamvorlage (Laden %s, Stand %s).',
            v_offen.laden, v_offen.status));
    END IF;
    INSERT INTO vorlagenauftraege (laden, art, bild, bild_typ, beschreibung, anmerkung, vorlage)
    VALUES (v_laden, p_art, p_bild, CASE WHEN p_bild IS NULL THEN NULL ELSE p_bild_typ END,
            CASE WHEN p_bild IS NULL THEN btrim(p_beschreibung) ELSE '' END,
            coalesce(p_anmerkung, ''), p_art)
    RETURNING id INTO v_id;
    RETURN jsonb_build_object('ok', true, 'id', v_id);
EXCEPTION WHEN check_violation THEN
    RETURN jsonb_build_object('ok', false, 'grund',
        'Das Foto ist zu gross (hoechstens 8 MB) oder kein JPEG/PNG.');
WHEN unique_violation THEN
    SELECT id, status, laden INTO v_offen FROM vorlagenauftraege
     WHERE art = p_art AND status NOT IN ('freigegeben', 'gescheitert') LIMIT 1;
    RETURN jsonb_build_object('ok', false, 'grund', format(
        'Es laeuft schon ein Auftrag fuer diese Teamvorlage (Laden %s, Stand %s).',
        coalesce(v_offen.laden, '?'), coalesce(v_offen.status, '?')));
END $$;

-- ---------------------------------------------------------------------------
-- I-2 + M-6(a): urteil('ja') laedt die Vorlagen-Zeile jetzt selbst FOR
-- UPDATE, prueft, dass sie existiert und wirklich art='formular' ist (statt
-- ein "0 rows updated" stillschweigend als Erfolg zu werten), und prueft die
-- Gestalt noch einmal mit _formular_gestalt_fehler, statt blind zu
-- uebernehmen, was gerade in der Zeile steht.
CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_urteil(
    p_id uuid, p_urteil text, p_anmerkung text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
DECLARE
    v_laden text := marketing._laden_des_aufrufers();
    a record;
    v_vorlage record;
    v_fehler text;
BEGIN
    SELECT * INTO a FROM vorlagenauftraege WHERE id = p_id AND laden = v_laden FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Kein solcher Auftrag in diesem Laden.');
    END IF;
    IF a.status <> 'vorgelegt' THEN
        RETURN jsonb_build_object('ok', false, 'grund',
            format('Der Auftrag steht auf %s, nicht auf vorgelegt.', a.status));
    END IF;
    IF p_urteil = 'ja' THEN
        SELECT * INTO v_vorlage FROM layout_vorlagen WHERE name = a.vorlage FOR UPDATE;
        IF NOT FOUND OR v_vorlage.art <> 'formular' THEN
            RETURN jsonb_build_object('ok', false, 'grund',
                'Keine Formular-Vorlage mehr unter diesem Namen - kann nicht freigegeben werden.');
        END IF;
        v_fehler := marketing._formular_gestalt_fehler(v_vorlage.gestalt);
        IF v_fehler IS NOT NULL THEN
            RETURN jsonb_build_object('ok', false, 'grund',
                'Die vorgelegte Gestalt ist nicht mehr gueltig: ' || v_fehler);
        END IF;
        PERFORM set_config('marketing.formular_freigabe', 'an', true);
        UPDATE layout_vorlagen SET status = 'freigegeben',
               freigegebene_gestalt = v_vorlage.gestalt, freigegebene_fassung = v_vorlage.fassung,
               entschieden_von = 'laden:' || v_laden, entschieden_am = now()
         WHERE name = a.vorlage;
        -- Sofort wieder schliessen: set_config(..., true) ist SET LOCAL und
        -- gilt sonst bis zum Transaktionsende, nicht nur fuer dieses UPDATE.
        PERFORM set_config('marketing.formular_freigabe', '', true);
        UPDATE vorlagenauftraege SET status = 'freigegeben',
               rueckmeldungen = rueckmeldungen || jsonb_build_object(
                   'runde', a.runde, 'urteil', 'ja', 'anmerkung', coalesce(p_anmerkung, ''),
                   'am', now())
         WHERE id = p_id;
        RETURN jsonb_build_object('ok', true, 'status', 'freigegeben');
    ELSIF p_urteil = 'nein' THEN
        IF length(btrim(coalesce(p_anmerkung, ''))) = 0 THEN
            RETURN jsonb_build_object('ok', false, 'grund',
                'Ein Nein braucht eine Anmerkung - was soll anders werden?');
        END IF;
        UPDATE vorlagenauftraege SET status = 'nachbessern', runde = runde + 1,
               rueckmeldungen = rueckmeldungen || jsonb_build_object(
                   'runde', a.runde, 'urteil', 'nein', 'anmerkung', btrim(p_anmerkung),
                   'am', now())
         WHERE id = p_id;
        RETURN jsonb_build_object('ok', true, 'status', 'nachbessern', 'runde', a.runde + 1);
    END IF;
    RETURN jsonb_build_object('ok', false, 'grund', 'Urteil ist ja oder nein.');
END $$;

-- ---------------------------------------------------------------------------
-- M-6(b): vorlegen darf eine bestehende LAYOUT-Zeile (art='layout') mit dem
-- gleichen Namen nicht kapern - vorher setzte das ON CONFLICT DO UPDATE nie
-- 'art', eine vorbestehende Layout-Zeile haette also die Formular-Gestalt
-- bekommen und waere art='layout' geblieben (aus formular_vorlage() und
-- vorlagenauftrag_urteil() unsichtbar, aus der Layout-API weiter sichtbar).
-- Die Pruefung ist zusaetzlich zur DB-seitigen Art-Sperre im neuen Trigger:
-- die Trigger-Sperre greift erst BEIM Schreiben und wuerde hier nur einen
-- rohen Fehler statt einer menschenlesbaren Ablehnung liefern.
CREATE OR REPLACE FUNCTION marketing.vorlagenauftrag_vorlegen(p_id uuid, p_gestalt jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    a record;
    v_fehler text := marketing._formular_gestalt_fehler(p_gestalt);
    v_alt_art text;
BEGIN
    SELECT * INTO a FROM marketing.vorlagenauftraege WHERE id = p_id FOR UPDATE;
    IF NOT FOUND OR a.status <> 'in_arbeit' THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Auftrag ist nicht in Arbeit.');
    END IF;
    IF v_fehler IS NOT NULL THEN
        RETURN jsonb_build_object('ok', false, 'grund', v_fehler);
    END IF;

    SELECT art INTO v_alt_art FROM marketing.layout_vorlagen WHERE name = a.vorlage;
    IF FOUND AND v_alt_art <> 'formular' THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Es gibt bereits eine Layout-Vorlage %L (keine Formular-Vorlage) - '
            || 'Terminkarte kann so nicht vorgelegt werden.', a.vorlage));
    END IF;

    PERFORM set_config('marketing.formular_vorlegen', 'an', true);
    INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, art,
                                           vorgeschlagen_von, fassung)
    VALUES (a.vorlage, 'Teamvorlage Terminkarte', p_gestalt, 'vorschlag', 'formular',
            'marketing-arbeiter', 1)
    ON CONFLICT (name) DO UPDATE SET gestalt = EXCLUDED.gestalt, status = 'vorschlag',
        fassung = CASE WHEN marketing.layout_vorlagen.freigegebene_fassung
                            = marketing.layout_vorlagen.fassung
                       THEN marketing.layout_vorlagen.fassung + 1
                       ELSE marketing.layout_vorlagen.fassung END,
        entschieden_von = NULL, entschieden_am = NULL;
    PERFORM set_config('marketing.formular_vorlegen', '', true);

    UPDATE marketing.vorlagenauftraege SET status = 'vorgelegt', fehler = '' WHERE id = p_id;
    RETURN jsonb_build_object('ok', true);
END $$;

COMMIT;
