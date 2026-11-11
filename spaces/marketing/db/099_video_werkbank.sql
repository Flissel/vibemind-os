-- RUNBOOK: nach 098. Idempotent, eine Transaktion. Danach verify_099 (in ROLLBACK).
-- 099: Video-Werkbank (Stufe 1). Marketings eigene Tabellen fuer lange Erklaervideos, die Szene fuer Szene
-- entstehen (video_werke = Kopf je Auftrag, video_szenen, video_vorschlaege = die Tore beim Betreiber)
-- und die Zustandsmaschine als SQL-Funktionen. Alles, was Daten aendert, ist eine Funktion mit Rueckgabewert
-- (die API-Huelle _schreiben wickelt jede Anweisung als SELECT ... FROM (<sql>) t). Jede zustandsaendernde
-- Funktion schreibt am Ende ueber _video_zaehlen den Zaehlstand in auftrag.auftraege.ergebnis->'werkbank'.
-- Die Marketing-API verbindet als supabase_admin; kein Grant an sales_app, anon, authenticated.
BEGIN;

CREATE TABLE IF NOT EXISTS marketing.video_werke (
    auftrag             uuid PRIMARY KEY REFERENCES auftrag.auftraege(id),
    mandant             text NOT NULL,
    gliederung          jsonb,
    gliederung_stand    text NOT NULL DEFAULT 'geplant'
                        CONSTRAINT video_werke_gliederung_stand_chk CHECK (gliederung_stand IN ('geplant', 'text_wartet', 'text_ok')),
    gliederung_fassung  int NOT NULL DEFAULT 0,
    gliederung_wunsch   text,
    gliederung_wuensche jsonb[] NOT NULL DEFAULT '{}',
    quellen             jsonb NOT NULL DEFAULT '[]'::jsonb,
    aufrufe             int NOT NULL DEFAULT 0,
    stimme              text,
    video_datei         text,
    video_fassung       int NOT NULL DEFAULT 0,
    video_veraltet      boolean NOT NULL DEFAULT false,
    runde               int NOT NULL DEFAULT 0,
    hinweise            jsonb NOT NULL DEFAULT '[]'::jsonb,
    angelegt            timestamptz NOT NULL DEFAULT now(),
    geaendert           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS marketing.video_szenen (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auftrag         uuid NOT NULL REFERENCES marketing.video_werke(auftrag),
    nr              int NOT NULL,
    stufe           text,
    kurz            text,
    sprechtext      text,
    bildart         text CONSTRAINT video_szenen_bildart_chk CHECK (bildart IN ('slide', 'avatar_gross', 'avatar_slide', 'clip')),
    bild            jsonb NOT NULL DEFAULT '{}'::jsonb,
    uebergang       text CONSTRAINT video_szenen_uebergang_chk CHECK (uebergang IN ('schnitt', 'blende')),
    quellen         jsonb NOT NULL DEFAULT '[]'::jsonb,
    stand           text NOT NULL DEFAULT 'geplant'
                    CONSTRAINT video_szenen_stand_chk CHECK (stand IN
                        ('geplant', 'text_wartet', 'text_ok', 'wird_erzeugt', 'clip_wartet', 'fertig')),
    rueckgabe_von   text CONSTRAINT video_szenen_rueckgabe_chk CHECK (rueckgabe_von IN ('text', 'clip')),
    fassung         int NOT NULL DEFAULT 0,
    variante        int NOT NULL DEFAULT 0,
    vergeben_bis    timestamptz,
    wunsch          text CONSTRAINT video_szenen_wunsch_chk CHECK (length(wunsch) <= 2000),
    wuensche        jsonb[] NOT NULL DEFAULT '{}',
    clip_datei      text,
    dauer_s         numeric,
    stimme          text,
    engine          text,
    fehler          text,
    fehler_nr       int NOT NULL DEFAULT 0,
    angelegt        timestamptz NOT NULL DEFAULT now(),
    geaendert       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT video_szenen_nr_uni UNIQUE (auftrag, nr)
);
CREATE INDEX IF NOT EXISTS video_szenen_auftrag_stand_idx ON marketing.video_szenen (auftrag, stand);

CREATE TABLE IF NOT EXISTS marketing.video_vorschlaege (
    kennung         text PRIMARY KEY CONSTRAINT video_vorschlaege_kennung_chk CHECK (kennung ~ '^wb-[a-z2-7]{10}$'),
    auftrag         uuid NOT NULL REFERENCES marketing.video_werke(auftrag),
    szene           uuid REFERENCES marketing.video_szenen(id),
    tor             text NOT NULL CONSTRAINT video_vorschlaege_tor_chk CHECK (tor IN ('gliederung', 'text', 'clip')),
    fassung         int NOT NULL,
    stand           text NOT NULL CONSTRAINT video_vorschlaege_stand_chk CHECK (stand IN
                        ('wartend', 'offen', 'angenommen', 'verworfen', 'zurueckziehen', 'erledigt')),
    text            text,
    abgehakt        boolean NOT NULL DEFAULT false,
    gepostet_am     timestamptz,
    entschieden_am  timestamptz,
    angelegt        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS video_vorschlaege_stand_idx ON marketing.video_vorschlaege (stand);
-- Je (Auftrag, Szene, Tor) hoechstens ein lebender Vorschlag (die Gliederung hat szene = NULL).
CREATE UNIQUE INDEX IF NOT EXISTS video_vorschlaege_lebend_uni ON marketing.video_vorschlaege
    (auftrag, coalesce(szene, '00000000-0000-0000-0000-000000000000'::uuid), tor)
    WHERE stand IN ('wartend', 'offen');

-- Neue Kennung wb-xxxxxxxxxx (Alphabet a-z, 2-7).
CREATE OR REPLACE FUNCTION marketing._video_kennung() RETURNS text
LANGUAGE sql VOLATILE AS $$
    SELECT 'wb-' || string_agg(substr('abcdefghijklmnopqrstuvwxyz234567', 1 + floor(random() * 32)::int, 1), '')
      FROM generate_series(1, 10);
$$;

-- Zaehlstand ins Auftragsergebnis schreiben (der Verkaeufer liest ihn von dort).
CREATE OR REPLACE FUNCTION marketing._video_zaehlen(p_auftrag uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_w marketing.video_werke;
    v_n int;
    v_je jsonb;
    v_wartet int;
    v_seit timestamptz;
    v_tor text;
BEGIN
    SELECT * INTO v_w FROM marketing.video_werke WHERE auftrag = p_auftrag;
    IF v_w.auftrag IS NULL THEN RETURN; END IF;
    SELECT count(*),
           jsonb_build_object(
               'geplant', count(*) FILTER (WHERE stand = 'geplant'),
               'text_wartet', count(*) FILTER (WHERE stand = 'text_wartet'),
               'text_ok', count(*) FILTER (WHERE stand = 'text_ok'),
               'wird_erzeugt', count(*) FILTER (WHERE stand = 'wird_erzeugt'),
               'clip_wartet', count(*) FILTER (WHERE stand = 'clip_wartet'),
               'fertig', count(*) FILTER (WHERE stand = 'fertig'))
      INTO v_n, v_je
      FROM marketing.video_szenen WHERE auftrag = p_auftrag;
    SELECT count(*), min(gepostet_am) INTO v_wartet, v_seit
      FROM marketing.video_vorschlaege WHERE auftrag = p_auftrag AND stand = 'offen';
    SELECT tor INTO v_tor FROM marketing.video_vorschlaege
     WHERE auftrag = p_auftrag AND stand = 'offen' ORDER BY gepostet_am, angelegt LIMIT 1;
    PERFORM auftrag.ergebnis_mischen(p_auftrag, jsonb_build_object('werkbank', jsonb_build_object(
        'szenen', v_n, 'je_stand', v_je, 'wartet', v_wartet, 'wartet_tor', v_tor, 'wartet_seit', v_seit,
        'runde', v_w.runde, 'hinweise', v_w.hinweise, 'aufrufe', v_w.aufrufe)));
END $$;

-- Arbeitsform eines Werks (fuer den Marketing-Worker): Kopf, Eingabe und alle Szenen als ganze Zeilen.
CREATE OR REPLACE FUNCTION marketing._video_werk_json(p_auftrag uuid) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'auftrag', w.auftrag, 'mandant', w.mandant, 'eingabe', a.eingabe,
        'gliederung_stand', w.gliederung_stand, 'gliederung_fassung', w.gliederung_fassung,
        'gliederung', w.gliederung, 'gliederung_wunsch', w.gliederung_wunsch,
        'quellen', w.quellen, 'stimme', w.stimme,
        'szenen', coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.nr)
                              FROM marketing.video_szenen s WHERE s.auftrag = w.auftrag), '[]'::jsonb),
        'alle_fertig', EXISTS (SELECT 1 FROM marketing.video_szenen s WHERE s.auftrag = w.auftrag)
                       AND NOT EXISTS (SELECT 1 FROM marketing.video_szenen s
                                        WHERE s.auftrag = w.auftrag AND s.stand <> 'fertig'),
        'video_fassung', w.video_fassung, 'video_veraltet', w.video_veraltet, 'aufrufe', w.aufrufe)
      FROM marketing.video_werke w JOIN auftrag.auftraege a ON a.id = w.auftrag
     WHERE w.auftrag = p_auftrag;
$$;

-- Szenen einer Gliederung in Reihenfolge: stufen[].szenen[] (Eintrag = Objekt oder nur ein Text). Aus einem
-- Eintrag werden kurz (Schluessel kurz, sonst titel), bildart (nur gueltige Werte) und quellen (nur Liste)
-- uebernommen; der Name der Stufe ist stufen[].name (sonst titel). Alles andere bleibt in der Gliederung.
CREATE OR REPLACE FUNCTION marketing._video_gliederung_szenen(p_gliederung jsonb)
RETURNS TABLE (nr int, stufe text, kurz text, bildart text, quellen jsonb)
LANGUAGE sql IMMUTABLE AS $$
    SELECT (row_number() OVER (ORDER BY st.o, sz.o))::int,
           coalesce(st.v->>'name', st.v->>'titel'),
           CASE WHEN jsonb_typeof(sz.v) = 'string' THEN sz.v #>> '{}'
                ELSE coalesce(sz.v->>'kurz', sz.v->>'titel') END,
           CASE WHEN sz.v->>'bildart' IN ('slide', 'avatar_gross', 'avatar_slide', 'clip') THEN sz.v->>'bildart' END,
           CASE WHEN jsonb_typeof(sz.v->'quellen') = 'array' THEN sz.v->'quellen' ELSE '[]'::jsonb END
      FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p_gliederung->'stufen') = 'array'
                                     THEN p_gliederung->'stufen' ELSE '[]'::jsonb END) WITH ORDINALITY AS st(v, o)
     CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(st.v->'szenen') = 'array'
                                                  THEN st.v->'szenen' ELSE '[]'::jsonb END) WITH ORDINALITY AS sz(v, o);
$$;

-- Werk zu einem uebernommenen Video-Auftrag anlegen; idempotent. NULL, wenn der Auftrag nicht passt.
CREATE OR REPLACE FUNCTION marketing.video_werk_beginnen(p_auftrag uuid) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_a auftrag.auftraege;
BEGIN
    IF EXISTS (SELECT 1 FROM marketing.video_werke WHERE auftrag = p_auftrag) THEN
        RETURN marketing._video_werk_json(p_auftrag);
    END IF;
    SELECT * INTO v_a FROM auftrag.auftraege WHERE id = p_auftrag;
    IF v_a.id IS NULL OR v_a.art <> 'video' OR v_a.an <> 'marketing' OR v_a.stand <> 'in_arbeit' THEN
        RETURN NULL;
    END IF;
    INSERT INTO marketing.video_werke (auftrag, mandant) VALUES (p_auftrag, v_a.mandant)
    ON CONFLICT (auftrag) DO NOTHING;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN marketing._video_werk_json(p_auftrag);
END $$;

-- Gliederung einstellen (nur aus geplant, Auftrag in in_arbeit oder werkbank, mindestens eine Szene in
-- stufen[].szenen[]). Gibt die Kennung des neuen Vorschlags zurueck, sonst NULL und nichts aendert sich.
CREATE OR REPLACE FUNCTION marketing.video_gliederung_setzen(p_auftrag uuid, p_gliederung jsonb, p_quellen jsonb)
RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    v_w marketing.video_werke;
    v_f int;
    v_k text;
    v_stand text;
BEGIN
    IF jsonb_typeof(p_gliederung) IS DISTINCT FROM 'object' THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM marketing._video_gliederung_szenen(p_gliederung)) THEN RETURN NULL; END IF;
    SELECT * INTO v_w FROM marketing.video_werke WHERE auftrag = p_auftrag FOR UPDATE;
    IF v_w.auftrag IS NULL OR v_w.gliederung_stand <> 'geplant' THEN RETURN NULL; END IF;
    SELECT stand INTO v_stand FROM auftrag.auftraege WHERE id = p_auftrag AND art = 'video';
    IF v_stand IS NULL OR v_stand NOT IN ('in_arbeit', 'werkbank') THEN RETURN NULL; END IF;
    v_f := v_w.gliederung_fassung + 1;
    UPDATE marketing.video_werke
       SET gliederung = p_gliederung,
           quellen = CASE WHEN jsonb_typeof(p_quellen) = 'array' THEN p_quellen ELSE '[]'::jsonb END,
           gliederung_stand = 'text_wartet',
           gliederung_fassung = v_f,
           gliederung_wuensche = CASE WHEN gliederung_wunsch IS NULL THEN gliederung_wuensche
               ELSE array_append(gliederung_wuensche, jsonb_build_object(
                        'text', gliederung_wunsch, 'zeit', now(), 'tor', 'gliederung')) END,
           gliederung_wunsch = NULL,
           geaendert = now()
     WHERE auftrag = p_auftrag;
    v_k := marketing._video_kennung();
    INSERT INTO marketing.video_vorschlaege (kennung, auftrag, szene, tor, fassung, stand)
    VALUES (v_k, p_auftrag, NULL, 'gliederung', v_f, 'wartend');
    IF v_stand = 'in_arbeit' AND NOT auftrag.stand_setzen(p_auftrag, 'in_arbeit', 'werkbank') THEN
        RAISE EXCEPTION 'Der Auftrag liess sich nicht auf werkbank setzen'; END IF;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN v_k;
END $$;

-- Wunsch des Betreibers zu einem lebenden Vorschlag (Gliederung oder Szene), auf 2000 Zeichen gekuerzt.
CREATE OR REPLACE FUNCTION marketing.video_wunsch_setzen(p_kennung text, p_wunsch text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
    v_v marketing.video_vorschlaege;
    v_w text := nullif(btrim(left(p_wunsch, 2000)), '');
BEGIN
    SELECT * INTO v_v FROM marketing.video_vorschlaege WHERE kennung = p_kennung FOR UPDATE;
    IF v_v.kennung IS NULL OR v_v.stand NOT IN ('wartend', 'offen') THEN RETURN false; END IF;
    IF v_v.szene IS NULL THEN
        UPDATE marketing.video_werke SET gliederung_wunsch = v_w, geaendert = now() WHERE auftrag = v_v.auftrag;
    ELSE
        UPDATE marketing.video_szenen SET wunsch = v_w, geaendert = now() WHERE id = v_v.szene;
    END IF;
    PERFORM marketing._video_zaehlen(v_v.auftrag);
    RETURN true;
END $$;

-- Urteil des Betreibers (passt | nochmal) ueber den Weg rowboat | seite. {"ok": false}, wenn der Vorschlag
-- nicht mehr lebt oder der Stand dahinter nicht mehr passt (veraltete Kennung); sonst {"ok": true, "stand": ...}.
CREATE OR REPLACE FUNCTION marketing.video_entscheiden(p_kennung text, p_urteil text, p_weg text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_v marketing.video_vorschlaege;
    v_w marketing.video_werke;
    v_s marketing.video_szenen;
    v_passt boolean;
    v_nach text;
    v_neu text;
BEGIN
    IF p_urteil IS NULL OR p_urteil NOT IN ('passt', 'nochmal') OR p_weg IS NULL OR p_weg NOT IN ('rowboat', 'seite') THEN
        RETURN jsonb_build_object('ok', false);
    END IF;
    v_passt := p_urteil = 'passt';
    SELECT * INTO v_v FROM marketing.video_vorschlaege WHERE kennung = p_kennung FOR UPDATE;
    IF v_v.kennung IS NULL OR v_v.stand NOT IN ('wartend', 'offen') THEN
        RETURN jsonb_build_object('ok', false);
    END IF;

    IF v_v.szene IS NULL THEN
        SELECT * INTO v_w FROM marketing.video_werke WHERE auftrag = v_v.auftrag FOR UPDATE;
        IF v_w.auftrag IS NULL OR v_w.gliederung_stand <> 'text_wartet' OR v_w.gliederung_fassung <> v_v.fassung THEN
            RETURN jsonb_build_object('ok', false);
        END IF;
        v_nach := CASE WHEN v_passt THEN 'text_ok' ELSE 'geplant' END;
        UPDATE marketing.video_werke
           SET gliederung_stand = v_nach,
               gliederung_wuensche = CASE WHEN v_passt AND gliederung_wunsch IS NOT NULL
                   THEN array_append(gliederung_wuensche, jsonb_build_object(
                            'text', gliederung_wunsch, 'zeit', now(), 'tor', 'gliederung'))
                   ELSE gliederung_wuensche END,
               gliederung_wunsch = CASE WHEN v_passt THEN NULL ELSE gliederung_wunsch END,
               geaendert = now()
         WHERE auftrag = v_v.auftrag;
        -- Szenen aus der Gliederung in Reihenfolge anlegen, nur wenn noch keine da sind
        IF v_passt AND NOT EXISTS (SELECT 1 FROM marketing.video_szenen WHERE auftrag = v_v.auftrag) THEN
            INSERT INTO marketing.video_szenen (auftrag, nr, stufe, kurz, bildart, quellen, stand)
            SELECT v_v.auftrag, g.nr, g.stufe, g.kurz, g.bildart, g.quellen, 'geplant'
              FROM marketing._video_gliederung_szenen(v_w.gliederung) g;
        END IF;
    ELSE
        SELECT * INTO v_s FROM marketing.video_szenen WHERE id = v_v.szene FOR UPDATE;
        IF v_s.id IS NULL THEN RETURN jsonb_build_object('ok', false); END IF;
        IF v_v.tor = 'text' THEN
            IF v_s.stand <> 'text_wartet' OR v_s.fassung <> v_v.fassung THEN
                RETURN jsonb_build_object('ok', false); END IF;
            v_nach := CASE WHEN v_passt THEN 'text_ok' ELSE 'geplant' END;
            UPDATE marketing.video_szenen
               SET stand = v_nach,
                   rueckgabe_von = CASE WHEN v_passt THEN NULL ELSE 'text' END,
                   wuensche = CASE WHEN v_passt AND wunsch IS NOT NULL
                       THEN array_append(wuensche, jsonb_build_object('text', wunsch, 'zeit', now(), 'tor', 'text'))
                       ELSE wuensche END,
                   wunsch = CASE WHEN v_passt THEN NULL ELSE wunsch END,
                   geaendert = now()
             WHERE id = v_s.id;
        ELSIF v_v.tor = 'clip' THEN
            IF v_s.stand <> 'clip_wartet' THEN RETURN jsonb_build_object('ok', false); END IF;
            IF v_passt THEN
                v_nach := 'fertig';
                UPDATE marketing.video_szenen
                   SET stand = 'fertig',
                       wuensche = CASE WHEN wunsch IS NOT NULL
                           THEN array_append(wuensche, jsonb_build_object('text', wunsch, 'zeit', now(), 'tor', 'clip'))
                           ELSE wuensche END,
                       wunsch = NULL, geaendert = now()
                 WHERE id = v_s.id;
            ELSIF v_s.wunsch IS NULL THEN
                -- ohne Wunsch: derselbe Text in einer neuen Variante
                v_nach := 'text_ok';
                UPDATE marketing.video_szenen
                   SET stand = 'text_ok', variante = variante + 1, fehler = NULL,
                       clip_datei = NULL, dauer_s = NULL, geaendert = now()
                 WHERE id = v_s.id;
            ELSE
                v_nach := 'geplant';
                UPDATE marketing.video_szenen
                   SET stand = 'geplant', rueckgabe_von = 'clip', fehler = NULL, geaendert = now()
                 WHERE id = v_s.id;
            END IF;
        ELSE
            RETURN jsonb_build_object('ok', false);
        END IF;
    END IF;

    v_neu := CASE WHEN p_weg = 'rowboat' THEN (CASE WHEN v_passt THEN 'angenommen' ELSE 'verworfen' END)
                  ELSE (CASE WHEN v_v.stand = 'offen' THEN 'zurueckziehen' ELSE 'erledigt' END) END;
    UPDATE marketing.video_vorschlaege SET stand = v_neu, entschieden_am = now() WHERE kennung = p_kennung;
    PERFORM marketing._video_zaehlen(v_v.auftrag);
    RETURN jsonb_build_object('ok', true, 'stand', v_nach);
END $$;

-- Szenentext einstellen (nur aus geplant, auch nach Rueckgabe vom Clip-Tor, wenn sich der Text aendert).
-- Felder: sprechtext (Pflicht), bildart, bild, uebergang, quellen; fehlende Felder behalten den alten Wert.
-- Ungueltige Werte: NULL, nichts aendert sich. Gibt die Kennung des neuen Textvorschlags zurueck.
CREATE OR REPLACE FUNCTION marketing.video_szene_text_setzen(p_szene uuid, p_text jsonb) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    v_s marketing.video_szenen;
    v_k text;
    v_f int;
    v_bildart text;
    v_uebergang text;
BEGIN
    IF jsonb_typeof(p_text) IS DISTINCT FROM 'object' OR coalesce(btrim(p_text->>'sprechtext'), '') = '' THEN
        RETURN NULL; END IF;
    SELECT * INTO v_s FROM marketing.video_szenen WHERE id = p_szene FOR UPDATE;
    IF v_s.id IS NULL OR v_s.stand <> 'geplant' THEN RETURN NULL; END IF;
    v_bildart := coalesce(p_text->>'bildart', v_s.bildart, 'slide');
    v_uebergang := coalesce(p_text->>'uebergang', v_s.uebergang, 'schnitt');
    IF v_bildart NOT IN ('slide', 'avatar_gross', 'avatar_slide', 'clip')
       OR v_uebergang NOT IN ('schnitt', 'blende') THEN RETURN NULL; END IF;
    v_f := v_s.fassung + 1;
    UPDATE marketing.video_szenen
       SET sprechtext = p_text->>'sprechtext',
           bildart = v_bildart,
           bild = CASE WHEN jsonb_typeof(p_text->'bild') = 'object' THEN p_text->'bild' ELSE bild END,
           uebergang = v_uebergang,
           quellen = CASE WHEN jsonb_typeof(p_text->'quellen') = 'array' THEN p_text->'quellen' ELSE quellen END,
           stand = 'text_wartet', fassung = v_f, rueckgabe_von = NULL, fehler = NULL,
           -- Rueckgabe vom Clip-Tor: der alte Clip gehoert zum alten Text, die Szene wird neu erzeugt
           variante = CASE WHEN v_s.rueckgabe_von = 'clip' THEN variante + 1 ELSE variante END,
           clip_datei = CASE WHEN v_s.rueckgabe_von = 'clip' THEN NULL ELSE clip_datei END,
           dauer_s = CASE WHEN v_s.rueckgabe_von = 'clip' THEN NULL ELSE dauer_s END,
           wuensche = CASE WHEN wunsch IS NULL THEN wuensche
               ELSE array_append(wuensche, jsonb_build_object(
                        'text', wunsch, 'zeit', now(), 'tor', coalesce(v_s.rueckgabe_von, 'text'))) END,
           wunsch = NULL,
           geaendert = now()
     WHERE id = p_szene;
    v_k := marketing._video_kennung();
    INSERT INTO marketing.video_vorschlaege (kennung, auftrag, szene, tor, fassung, stand)
    VALUES (v_k, v_s.auftrag, p_szene, 'text', v_f, 'wartend');
    PERFORM marketing._video_zaehlen(v_s.auftrag);
    RETURN v_k;
END $$;

-- Neues Bild nach Rueckgabe vom Clip-Tor: der Sprechtext bleibt, die Szene wird neu erzeugt (Variante +1).
CREATE OR REPLACE FUNCTION marketing.video_szene_bild_setzen(p_szene uuid, p_bildart text, p_bild jsonb) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_s marketing.video_szenen;
BEGIN
    IF p_bildart IS NULL OR p_bildart NOT IN ('slide', 'avatar_gross', 'avatar_slide', 'clip') THEN RETURN false; END IF;
    SELECT * INTO v_s FROM marketing.video_szenen WHERE id = p_szene FOR UPDATE;
    IF v_s.id IS NULL OR v_s.stand <> 'geplant' OR v_s.rueckgabe_von IS DISTINCT FROM 'clip' THEN RETURN false; END IF;
    UPDATE marketing.video_szenen
       SET bildart = p_bildart,
           bild = CASE WHEN jsonb_typeof(p_bild) = 'object' THEN p_bild ELSE '{}'::jsonb END,
           stand = 'text_ok', variante = variante + 1, rueckgabe_von = NULL, fehler = NULL,
           clip_datei = NULL, dauer_s = NULL,
           wuensche = CASE WHEN wunsch IS NULL THEN wuensche
               ELSE array_append(wuensche, jsonb_build_object('text', wunsch, 'zeit', now(), 'tor', 'clip')) END,
           wunsch = NULL,
           geaendert = now()
     WHERE id = p_szene;
    PERFORM marketing._video_zaehlen(v_s.auftrag);
    RETURN true;
END $$;

-- Naechste Szene zum Erzeugen vergeben: kleinste nr in text_ok ohne Fehler oder in wird_erzeugt mit
-- abgelaufener Leihe. Gibt die ganze Zeile zurueck, NULL wenn nichts zu tun ist.
CREATE OR REPLACE FUNCTION marketing.video_szene_nehmen(p_auftrag uuid, p_lease interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_s marketing.video_szenen;
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    SELECT * INTO v_s FROM marketing.video_szenen
     WHERE auftrag = p_auftrag
       AND ((stand = 'text_ok' AND fehler IS NULL) OR (stand = 'wird_erzeugt' AND vergeben_bis < now()))
     ORDER BY nr
     LIMIT 1
     FOR UPDATE SKIP LOCKED;
    IF v_s.id IS NULL THEN RETURN NULL; END IF;
    UPDATE marketing.video_szenen SET stand = 'wird_erzeugt', vergeben_bis = now() + p_lease, geaendert = now()
     WHERE id = v_s.id
    RETURNING * INTO v_s;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN to_jsonb(v_s);
END $$;

-- Fertigen Clip melden: nur aus wird_erzeugt und nur fuer die aktuelle Variante. Gibt die Kennung des
-- Clip-Vorschlags zurueck, sonst NULL (und aendert nichts).
CREATE OR REPLACE FUNCTION marketing.video_szene_clip_setzen(p_szene uuid, p_variante int, p_datei text,
    p_dauer_s numeric, p_stimme text, p_engine text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    v_s marketing.video_szenen;
    v_k text;
    v_f int;
BEGIN
    SELECT * INTO v_s FROM marketing.video_szenen WHERE id = p_szene FOR UPDATE;
    IF v_s.id IS NULL OR v_s.stand <> 'wird_erzeugt' OR p_variante IS DISTINCT FROM v_s.variante THEN
        RETURN NULL; END IF;
    UPDATE marketing.video_szenen
       SET stand = 'clip_wartet', vergeben_bis = NULL, clip_datei = p_datei, dauer_s = p_dauer_s,
           stimme = p_stimme, engine = p_engine, geaendert = now()
     WHERE id = p_szene;
    SELECT coalesce(max(fassung), 0) + 1 INTO v_f FROM marketing.video_vorschlaege
     WHERE auftrag = v_s.auftrag AND szene = p_szene AND tor = 'clip';
    v_k := marketing._video_kennung();
    INSERT INTO marketing.video_vorschlaege (kennung, auftrag, szene, tor, fassung, stand)
    VALUES (v_k, v_s.auftrag, p_szene, 'clip', v_f, 'wartend');
    PERFORM marketing._video_zaehlen(v_s.auftrag);
    RETURN v_k;
END $$;

-- Erzeugen ist fehlgeschlagen: Szene geht mit Fehler zurueck auf text_ok (wird erst nach video_szene_nochmal
-- wieder genommen); ein Hinweis fuer den Betreiber, je Fehler einmal.
CREATE OR REPLACE FUNCTION marketing.video_szene_fehler(p_szene uuid, p_grund text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_s marketing.video_szenen;
BEGIN
    UPDATE marketing.video_szenen
       SET stand = 'text_ok', vergeben_bis = NULL,
           fehler = left(coalesce(nullif(btrim(p_grund), ''), 'Unbekannter Fehler'), 300),
           fehler_nr = fehler_nr + 1, geaendert = now()
     WHERE id = p_szene AND stand = 'wird_erzeugt'
    RETURNING * INTO v_s;
    IF v_s.id IS NULL THEN RETURN false; END IF;
    PERFORM marketing.video_hinweis(v_s.auftrag, 's' || v_s.nr || ':' || v_s.fehler_nr,
        'Szene ' || v_s.nr || ' konnte nicht erzeugt werden und wartet auf einen neuen Versuch.');
    PERFORM marketing._video_zaehlen(v_s.auftrag);
    RETURN true;
END $$;

-- Fehler einer Szene loeschen, damit nehmen sie wieder vergibt.
CREATE OR REPLACE FUNCTION marketing.video_szene_nochmal(p_szene uuid) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_auftrag uuid;
BEGIN
    UPDATE marketing.video_szenen SET fehler = NULL, geaendert = now()
     WHERE id = p_szene AND fehler IS NOT NULL RETURNING auftrag INTO v_auftrag;
    IF v_auftrag IS NULL THEN RETURN false; END IF;
    PERFORM marketing._video_zaehlen(v_auftrag);
    RETURN true;
END $$;

-- Fertige Szene nachtraeglich wieder oeffnen (wie Clip nochmal); das Video ist dann veraltet und der
-- Auftrag geht zurueck auf werkbank. Nur solange der Auftrag in werkbank, erzeugt oder wartet_freigabe steht,
-- sonst false und nichts aendert sich.
CREATE OR REPLACE FUNCTION marketing.video_szene_oeffnen(p_szene uuid, p_wunsch text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
    v_s marketing.video_szenen;
    v_w text := nullif(btrim(left(p_wunsch, 2000)), '');
    v_stand text;
BEGIN
    SELECT * INTO v_s FROM marketing.video_szenen WHERE id = p_szene FOR UPDATE;
    IF v_s.id IS NULL OR v_s.stand <> 'fertig' THEN RETURN false; END IF;
    SELECT stand INTO v_stand FROM auftrag.auftraege WHERE id = v_s.auftrag AND art = 'video';
    IF v_stand IS NULL OR v_stand NOT IN ('werkbank', 'erzeugt', 'wartet_freigabe') THEN RETURN false; END IF;
    IF v_w IS NULL THEN
        UPDATE marketing.video_szenen
           SET stand = 'text_ok', variante = variante + 1, fehler = NULL, clip_datei = NULL, dauer_s = NULL,
               geaendert = now()
         WHERE id = p_szene;
    ELSE
        UPDATE marketing.video_szenen
           SET stand = 'geplant', rueckgabe_von = 'clip', wunsch = v_w, fehler = NULL, geaendert = now()
         WHERE id = p_szene;
    END IF;
    UPDATE marketing.video_werke SET video_veraltet = true, geaendert = now() WHERE auftrag = v_s.auftrag;
    IF v_stand <> 'werkbank' AND NOT auftrag.stand_setzen(v_s.auftrag, v_stand, 'werkbank') THEN
        RAISE EXCEPTION 'Der Auftrag liess sich nicht auf werkbank setzen'; END IF;
    PERFORM marketing._video_zaehlen(v_s.auftrag);
    RETURN true;
END $$;

-- Hinweis fuer den Betreiber an das Werk haengen; je key einmal.
CREATE OR REPLACE FUNCTION marketing.video_hinweis(p_auftrag uuid, p_key text, p_satz text) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(p_key, '') = '' OR coalesce(p_satz, '') = '' THEN RETURN false; END IF;
    UPDATE marketing.video_werke
       SET hinweise = hinweise || jsonb_build_array(jsonb_build_object('key', p_key, 'satz', p_satz)),
           geaendert = now()
     WHERE auftrag = p_auftrag
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(hinweise) h WHERE h->>'key' = p_key);
    IF NOT FOUND THEN RETURN false; END IF;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN true;
END $$;

-- Einen Modellaufruf des Werks mitzaehlen; gibt den neuen Stand zurueck (NULL ohne Werk).
CREATE OR REPLACE FUNCTION marketing.video_aufruf_zaehlen(p_auftrag uuid) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE v_n int;
BEGIN
    UPDATE marketing.video_werke SET aufrufe = aufrufe + 1, geaendert = now()
     WHERE auftrag = p_auftrag RETURNING aufrufe INTO v_n;
    IF v_n IS NULL THEN RETURN NULL; END IF;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN v_n;
END $$;

-- Arbeitsliste fuer den Worker: je Werk mit Auftrag in in_arbeit oder werkbank.
CREATE OR REPLACE FUNCTION marketing.video_arbeit() RETURNS SETOF jsonb
LANGUAGE sql STABLE AS $$
    SELECT marketing._video_werk_json(w.auftrag)
      FROM marketing.video_werke w JOIN auftrag.auftraege a ON a.id = w.auftrag
     WHERE a.stand IN ('in_arbeit', 'werkbank')
     ORDER BY a.angelegt;
$$;

-- Fertiges Video melden: nur wenn alle Szenen fertig sind und der Auftrag auf werkbank steht.
-- p_teil (Schluessel datei = Dateiname, Pflicht; dazu beliebige weitere) wird ins Ergebnis gemischt, dazu
-- ki_hinweis und die Fassung. Ohne Dateinamen false. Auftrag werkbank -> erzeugt.
CREATE OR REPLACE FUNCTION marketing.video_fertig_setzen(p_auftrag uuid, p_teil jsonb) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
    v_w marketing.video_werke;
    v_f int;
    v_teil jsonb := CASE WHEN jsonb_typeof(p_teil) = 'object' THEN p_teil ELSE '{}'::jsonb END;
BEGIN
    IF coalesce(btrim(v_teil->>'datei'), '') = '' THEN RETURN false; END IF;
    SELECT * INTO v_w FROM marketing.video_werke WHERE auftrag = p_auftrag FOR UPDATE;
    IF v_w.auftrag IS NULL THEN RETURN false; END IF;
    IF NOT EXISTS (SELECT 1 FROM marketing.video_szenen WHERE auftrag = p_auftrag)
       OR EXISTS (SELECT 1 FROM marketing.video_szenen WHERE auftrag = p_auftrag AND stand <> 'fertig') THEN
        RETURN false; END IF;
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = p_auftrag AND stand = 'werkbank' AND art = 'video') THEN
        RETURN false; END IF;
    v_f := v_w.video_fassung + 1;
    UPDATE marketing.video_werke
       SET video_fassung = v_f, video_veraltet = false,
           video_datei = v_teil->>'datei', geaendert = now()
     WHERE auftrag = p_auftrag;
    PERFORM auftrag.ergebnis_mischen(p_auftrag, v_teil || jsonb_build_object('ki_hinweis', true, 'fassung', v_f));
    IF NOT auftrag.stand_setzen(p_auftrag, 'werkbank', 'erzeugt') THEN
        RAISE EXCEPTION 'Der Auftrag liess sich nicht auf erzeugt setzen'; END IF;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN true;
END $$;

-- Fuer den Boten: alle Vorschlaege, die er noch bearbeiten muss (wartend: einstellen; offen: auf Antwort
-- warten; zurueckziehen: loeschen; angenommen ohne Haken: abhaken). Sortiert nach Auftrag, nr (Gliederung zuerst).
CREATE OR REPLACE FUNCTION marketing.video_vorschlaege_lebend() RETURNS SETOF jsonb
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
               'kennung', v.kennung, 'auftrag', v.auftrag, 'szene', v.szene, 'tor', v.tor, 'nr', s.nr,
               'fassung', v.fassung, 'stand', v.stand, 'text', v.text,
               'titel', coalesce(w.gliederung->>'titel', a.eingabe->>'thema'),
               'kurz', s.kurz, 'ersatz', v.fassung > 1)
      FROM marketing.video_vorschlaege v
      JOIN marketing.video_werke w ON w.auftrag = v.auftrag
      JOIN auftrag.auftraege a ON a.id = v.auftrag
      LEFT JOIN marketing.video_szenen s ON s.id = v.szene
     WHERE v.stand IN ('wartend', 'offen', 'zurueckziehen') OR (v.stand = 'angenommen' AND NOT v.abgehakt)
     ORDER BY a.angelegt, v.auftrag, s.nr NULLS FIRST, v.angelegt;
$$;

-- Der Bote hat den Vorschlag eingestellt: wartend -> offen. War vorher keiner des Auftrags offen, beginnt
-- eine neue Runde.
CREATE OR REPLACE FUNCTION marketing.video_vorschlag_gepostet(p_kennung text, p_text text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE
    v_v marketing.video_vorschlaege;
    v_keiner boolean;
BEGIN
    SELECT * INTO v_v FROM marketing.video_vorschlaege WHERE kennung = p_kennung FOR UPDATE;
    IF v_v.kennung IS NULL OR v_v.stand <> 'wartend' THEN RETURN false; END IF;
    PERFORM 1 FROM marketing.video_werke WHERE auftrag = v_v.auftrag FOR UPDATE;
    v_keiner := NOT EXISTS (SELECT 1 FROM marketing.video_vorschlaege WHERE auftrag = v_v.auftrag AND stand = 'offen');
    UPDATE marketing.video_vorschlaege SET stand = 'offen', text = p_text, gepostet_am = now()
     WHERE kennung = p_kennung;
    IF v_keiner THEN
        UPDATE marketing.video_werke SET runde = runde + 1, geaendert = now() WHERE auftrag = v_v.auftrag;
    END IF;
    PERFORM marketing._video_zaehlen(v_v.auftrag);
    RETURN true;
END $$;

-- Der Bote hat den zurueckgezogenen Vorschlag entfernt: zurueckziehen -> erledigt.
CREATE OR REPLACE FUNCTION marketing.video_vorschlag_erledigt(p_kennung text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_auftrag uuid;
BEGIN
    UPDATE marketing.video_vorschlaege SET stand = 'erledigt'
     WHERE kennung = p_kennung AND stand = 'zurueckziehen'
    RETURNING auftrag INTO v_auftrag;
    IF v_auftrag IS NULL THEN RETURN false; END IF;
    PERFORM marketing._video_zaehlen(v_auftrag);
    RETURN true;
END $$;

-- Der Bote hat den angenommenen Vorschlag in Rowboat abgehakt.
CREATE OR REPLACE FUNCTION marketing.video_vorschlag_abgehakt(p_kennung text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_auftrag uuid;
BEGIN
    UPDATE marketing.video_vorschlaege SET abgehakt = true
     WHERE kennung = p_kennung AND stand = 'angenommen' AND NOT abgehakt
    RETURNING auftrag INTO v_auftrag;
    IF v_auftrag IS NULL THEN RETURN false; END IF;
    PERFORM marketing._video_zaehlen(v_auftrag);
    RETURN true;
END $$;

REVOKE ALL ON FUNCTION marketing._video_kennung() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_zaehlen(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_werk_json(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_werk_beginnen(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_gliederung_setzen(uuid, jsonb, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_wunsch_setzen(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_entscheiden(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_text_setzen(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_bild_setzen(uuid, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_nehmen(uuid, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_clip_setzen(uuid, int, text, numeric, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_fehler(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_nochmal(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_szene_oeffnen(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hinweis(uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_aufruf_zaehlen(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_arbeit() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_fertig_setzen(uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_vorschlaege_lebend() FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_vorschlag_gepostet(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_vorschlag_erledigt(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_vorschlag_abgehakt(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_gliederung_szenen(jsonb) FROM PUBLIC;
REVOKE ALL ON TABLE marketing.video_werke, marketing.video_szenen, marketing.video_vorschlaege
    FROM PUBLIC, anon, authenticated, sales_app;
-- Kein Grant an sales_app, anon, authenticated: nur die Marketing-API (supabase_admin) fasst diese Tabellen an.

COMMIT;
