-- RUNBOOK: nach 101. Idempotent, eine Transaktion. Danach verify_102 (in ROLLBACK).
-- 102: Higgsfield-Uebergaenge in der Video-Werkbank. Ein Uebergang liegt zwischen zwei fertigen Szenen (von -> nach)
-- und kostet Credits. Darum bestellt die Werkbank nie selbst: der Arbeiter SCHLAEGT VOR (Bilder, Auftragstext,
-- Preis), ein Mensch gibt je Clip auf der Sales-Seite frei, erst dann bestellt der Arbeiter. Die Regeln, die das
-- Geld schuetzen, stehen hier in SQL (Spec docs/superpowers/specs/2026-11-24-higgsfield-clip-freigabe-design.md):
--   * Entscheiden nur ueber den Weg 'seite' und nur im Stand vorgeschlagen (der zweite Klick wird abgelehnt).
--   * Der gesehene Preis muss dem gespeicherten gleichen; aendert sich der Preis vor dem Bestellen, geht der Clip
--     zurueck auf vorgeschlagen.
--   * Eine Bestellung ohne vermerkte Higgsfield-Nummer wird nie wiederholt: nach Ablauf der Leihe geht sie auf fehler.
--   * Eine neue Variante einer der beiden Szenen macht den Clip ueberholt; bestellt wird nie von selbst nach.
-- "Clip" heisst in der Werkbank schon das Szenenvideo, darum hier ueberall "hf".
BEGIN;

-- Dritter Wert fuer den Uebergang IN eine Szene (von der Szene davor).
ALTER TABLE marketing.video_szenen DROP CONSTRAINT IF EXISTS video_szenen_uebergang_chk;
ALTER TABLE marketing.video_szenen ADD CONSTRAINT video_szenen_uebergang_chk
    CHECK (uebergang IN ('schnitt', 'blende', 'higgsfield'));

-- Auftragstext: 1 bis 1000 Zeichen, keine Steuerzeichen (er geht als ein Argument an das Programm).
CREATE OR REPLACE FUNCTION marketing._video_hf_prompt_ok(p_text text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT p_text IS NOT NULL AND length(btrim(p_text)) BETWEEN 1 AND 1000 AND p_text !~ '[[:cntrl:]]';
$$;

CREATE TABLE IF NOT EXISTS marketing.video_hf_clips (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    auftrag         uuid NOT NULL REFERENCES marketing.video_werke(auftrag),
    art             text NOT NULL DEFAULT 'uebergang'
                    CONSTRAINT video_hf_clips_art_chk CHECK (art IN ('uebergang', 'szene')),
    von_szene       uuid REFERENCES marketing.video_szenen(id),
    nach_szene      uuid NOT NULL REFERENCES marketing.video_szenen(id),
    -- Varianten der beiden Szenen-Clips, aus denen die Standbilder stammen (aendert sich eine: ueberholt)
    von_variante    int,
    nach_variante   int NOT NULL,
    versuch         int NOT NULL,               -- laufende Nummer je (auftrag, nach_szene), Teil des Dateinamens
    start_bild      text,
    end_bild        text,
    prompt          text NOT NULL CONSTRAINT video_hf_clips_prompt_chk CHECK (marketing._video_hf_prompt_ok(prompt)),
    modell          text NOT NULL CONSTRAINT video_hf_clips_modell_chk CHECK (modell ~ '^[a-z0-9_]{1,40}$'),
    dauer_s         int NOT NULL CONSTRAINT video_hf_clips_dauer_chk CHECK (dauer_s BETWEEN 1 AND 15),
    preis           numeric NOT NULL CONSTRAINT video_hf_clips_preis_chk CHECK (preis >= 0 AND preis <= 1000),
    stand           text NOT NULL DEFAULT 'vorgeschlagen'
                    CONSTRAINT video_hf_clips_stand_chk CHECK (stand IN
                        ('vorgeschlagen', 'freigegeben', 'bestellt', 'fertig', 'verworfen', 'fehler', 'ueberholt')),
    hf_auftrag      text CONSTRAINT video_hf_clips_nummer_chk CHECK (hf_auftrag ~ '^[A-Za-z0-9][A-Za-z0-9_-]{5,79}$'),
    vergeben_bis    timestamptz,
    datei           text,
    clip_dauer_s    numeric,
    preis_bestellt  numeric,
    fehler          text,
    hinweis         text,
    entschieden_von text,
    entschieden_am  timestamptz,
    bestellt_am     timestamptz,
    fertig_am       timestamptz,
    angelegt        timestamptz NOT NULL DEFAULT now(),
    geaendert       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT video_hf_clips_uebergang_chk CHECK (art <> 'uebergang' OR (von_szene IS NOT NULL AND von_variante IS NOT NULL)),
    CONSTRAINT video_hf_clips_versuch_uni UNIQUE (auftrag, nach_szene, versuch)
);
CREATE INDEX IF NOT EXISTS video_hf_clips_stand_idx ON marketing.video_hf_clips (stand);
-- Je (Auftrag, Ziel-Szene) hoechstens ein lebender Clip.
CREATE UNIQUE INDEX IF NOT EXISTS video_hf_clips_lebend_uni ON marketing.video_hf_clips (auftrag, nach_szene)
    WHERE stand IN ('vorgeschlagen', 'freigegeben', 'bestellt', 'fertig', 'fehler');

-- Passt der Clip noch zu den beiden Szenen (beide fertig, dieselben Varianten, Uebergang weiter higgsfield)?
CREATE OR REPLACE FUNCTION marketing._video_hf_aktuell(p_clip marketing.video_hf_clips) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM marketing.video_szenen n JOIN marketing.video_szenen v ON v.id = p_clip.von_szene
         WHERE n.id = p_clip.nach_szene AND n.uebergang = 'higgsfield'
           AND n.stand = 'fertig' AND v.stand = 'fertig'
           AND n.variante = p_clip.nach_variante AND v.variante = p_clip.von_variante);
$$;

-- Stellen, an denen ein Uebergang vorgesehen ist und beide Szenen fertig sind, aber kein Clip zu den aktuellen
-- Varianten steht (weder lebend noch verworfen): dort muss der Arbeiter noch vorschlagen.
CREATE OR REPLACE FUNCTION marketing._video_hf_fehlend(p_auftrag uuid) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'nach_szene', n.id, 'nach_nr', n.nr, 'nach_datei', n.clip_datei, 'nach_variante', n.variante,
               'nach_kurz', n.kurz, 'stufe', n.stufe,
               'von_szene', v.id, 'von_nr', v.nr, 'von_datei', v.clip_datei, 'von_variante', v.variante,
               'von_kurz', v.kurz, 'von_stufe', v.stufe) ORDER BY n.nr), '[]'::jsonb)
      FROM marketing.video_szenen n
      JOIN marketing.video_szenen v ON v.auftrag = n.auftrag AND v.nr = n.nr - 1
     WHERE n.auftrag = p_auftrag AND n.uebergang = 'higgsfield'
       AND n.stand = 'fertig' AND v.stand = 'fertig' AND n.clip_datei IS NOT NULL AND v.clip_datei IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM marketing.video_hf_clips h
                        WHERE h.auftrag = n.auftrag AND h.nach_szene = n.id
                          AND h.stand IN ('vorgeschlagen', 'freigegeben', 'bestellt', 'fertig', 'fehler', 'verworfen')
                          AND h.nach_variante = n.variante AND h.von_szene = v.id AND h.von_variante = v.variante);
$$;

-- Darf das Video noch nicht gebaut werden? Ja, solange ein Clip auf den Menschen oder auf Higgsfield wartet, ein
-- fertiger Clip nicht mehr zu seinen Szenen passt oder ein Vorschlag noch fehlt.
CREATE OR REPLACE FUNCTION marketing._video_hf_blockiert(p_auftrag uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM marketing.video_hf_clips h
                    WHERE h.auftrag = p_auftrag
                      AND (h.stand IN ('vorgeschlagen', 'freigegeben', 'bestellt', 'fehler')
                           OR (h.stand = 'fertig' AND NOT marketing._video_hf_aktuell(h))))
        OR jsonb_array_length(marketing._video_hf_fehlend(p_auftrag)) > 0;
$$;

-- Fertige, noch passende Uebergaenge fuer das Zusammenfuegen: [{nach_nr, datei, dauer_s}].
CREATE OR REPLACE FUNCTION marketing._video_hf_fertige(p_auftrag uuid) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object('nach_nr', n.nr, 'datei', h.datei, 'dauer_s', h.clip_dauer_s)
                              ORDER BY n.nr), '[]'::jsonb)
      FROM marketing.video_hf_clips h JOIN marketing.video_szenen n ON n.id = h.nach_szene
     WHERE h.auftrag = p_auftrag AND h.stand = 'fertig' AND h.datei IS NOT NULL AND marketing._video_hf_aktuell(h);
$$;

-- Zaehlstand ins Auftragsergebnis (wie 099), dazu hf_wartet (Clips, die auf eine Freigabe warten), hf_fehler und
-- hf_credits (Summe der bestellten Clips).
CREATE OR REPLACE FUNCTION marketing._video_zaehlen(p_auftrag uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_w marketing.video_werke;
    v_n int;
    v_je jsonb;
    v_wartet int;
    v_seit timestamptz;
    v_tor text;
    v_hf_wartet int;
    v_hf_fehler int;
    v_hf_credits numeric;
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
    SELECT count(*) FILTER (WHERE stand = 'vorgeschlagen'), count(*) FILTER (WHERE stand = 'fehler'),
           coalesce(sum(preis_bestellt), 0)
      INTO v_hf_wartet, v_hf_fehler, v_hf_credits
      FROM marketing.video_hf_clips WHERE auftrag = p_auftrag;
    PERFORM auftrag.ergebnis_mischen(p_auftrag, jsonb_build_object('werkbank', jsonb_build_object(
        'szenen', v_n, 'je_stand', v_je, 'wartet', v_wartet, 'wartet_tor', v_tor, 'wartet_seit', v_seit,
        'runde', v_w.runde, 'hinweise', v_w.hinweise, 'aufrufe', v_w.aufrufe,
        'hf_wartet', v_hf_wartet, 'hf_fehler', v_hf_fehler, 'hf_credits', v_hf_credits)));
END $$;

-- Arbeitsform eines Werks (wie 099), dazu hf_blockiert und hf_uebergaenge fuer das Zusammenfuegen.
CREATE OR REPLACE FUNCTION marketing._video_werk_json(p_auftrag uuid) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'auftrag', w.auftrag, 'mandant', w.mandant, 'eingabe', a.eingabe,
        'gliederung_stand', w.gliederung_stand, 'gliederung_fassung', w.gliederung_fassung,
        'gliederung', w.gliederung, 'gliederung_wunsch', w.gliederung_wunsch,
        'gliederung_wuensche', to_jsonb(w.gliederung_wuensche), 'gliederung_aufgegeben', w.gliederung_aufgegeben,
        'quellen', w.quellen, 'stimme', w.stimme,
        'szenen', coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.nr)
                              FROM marketing.video_szenen s WHERE s.auftrag = w.auftrag), '[]'::jsonb),
        'alle_fertig', EXISTS (SELECT 1 FROM marketing.video_szenen s WHERE s.auftrag = w.auftrag)
                       AND NOT EXISTS (SELECT 1 FROM marketing.video_szenen s
                                        WHERE s.auftrag = w.auftrag AND s.stand <> 'fertig'),
        'video_fassung', w.video_fassung, 'video_veraltet', w.video_veraltet, 'aufrufe', w.aufrufe,
        'video_versuch', w.video_versuch, 'video_fehler', w.video_fehler,
        'hf_blockiert', marketing._video_hf_blockiert(w.auftrag),
        'hf_uebergaenge', marketing._video_hf_fertige(w.auftrag))
      FROM marketing.video_werke w JOIN auftrag.auftraege a ON a.id = w.auftrag
     WHERE w.auftrag = p_auftrag;
$$;

-- Szenentext einstellen (wie 099). Neu: uebergang 'higgsfield' ist erlaubt; fuer die erste Szene und ab der
-- siebten Higgsfield-Stelle eines Werks wird daraus still 'schnitt' (hoechstens 6 Higgsfield-Stellen je Video).
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
       OR v_uebergang NOT IN ('schnitt', 'blende', 'higgsfield') THEN RETURN NULL; END IF;
    IF v_uebergang = 'higgsfield' AND (v_s.nr <= 1 OR (SELECT count(*) FROM marketing.video_szenen
            WHERE auftrag = v_s.auftrag AND id <> v_s.id AND uebergang = 'higgsfield') >= 6) THEN
        v_uebergang := 'schnitt';
    END IF;
    v_f := v_s.fassung + 1;
    UPDATE marketing.video_szenen
       SET sprechtext = p_text->>'sprechtext',
           bildart = v_bildart,
           bild = CASE WHEN jsonb_typeof(p_text->'bild') = 'object' THEN p_text->'bild' ELSE bild END,
           uebergang = v_uebergang,
           quellen = CASE WHEN jsonb_typeof(p_text->'quellen') = 'array' THEN p_text->'quellen' ELSE quellen END,
           belege = CASE WHEN jsonb_typeof(p_text->'belege') = 'array' THEN p_text->'belege' ELSE '[]'::jsonb END,
           stand = 'text_wartet', fassung = v_f, rueckgabe_von = NULL, fehler = NULL, aufgegeben = NULL,
           -- Rueckgabe vom Clip-Tor: der alte Clip gehoert zum alten Text, die Szene wird neu erzeugt
           variante = CASE WHEN v_s.rueckgabe_von = 'clip' THEN variante + 1 ELSE variante END,
           clip_datei = CASE WHEN v_s.rueckgabe_von = 'clip' THEN NULL ELSE clip_datei END,
           dauer_s = CASE WHEN v_s.rueckgabe_von = 'clip' THEN NULL ELSE dauer_s END,
           wuensche = marketing._video_wunsch_anhaengen(wuensche, wunsch, coalesce(v_s.rueckgabe_von, 'text')),
           wunsch = NULL,
           geaendert = now()
     WHERE id = p_szene;
    v_k := marketing._video_kennung();
    INSERT INTO marketing.video_vorschlaege (kennung, auftrag, szene, tor, fassung, stand)
    VALUES (v_k, v_s.auftrag, p_szene, 'text', v_f, 'wartend');
    PERFORM marketing._video_zaehlen(v_s.auftrag);
    RETURN v_k;
END $$;

-- Fertiges Video melden (wie 099). Neu: nicht, solange ein Higgsfield-Uebergang offen ist.
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
    IF marketing._video_hf_blockiert(p_auftrag) THEN RETURN false; END IF;
    v_f := v_w.video_fassung + 1;
    UPDATE marketing.video_werke
       SET video_fassung = v_f, video_veraltet = false, video_fehler = NULL,
           video_datei = v_teil->>'datei', geaendert = now()
     WHERE auftrag = p_auftrag;
    PERFORM auftrag.ergebnis_mischen(p_auftrag, v_teil || jsonb_build_object('ki_hinweis', true, 'fassung', v_f));
    IF NOT auftrag.stand_setzen(p_auftrag, 'werkbank', 'erzeugt') THEN
        RAISE EXCEPTION 'Der Auftrag liess sich nicht auf erzeugt setzen'; END IF;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN true;
END $$;

-- Abgleich je Werk (der Higgsfield-Arbeiter ruft ihn jede Runde): Clips, die nicht mehr zu ihren Szenen passen,
-- werden ueberholt (bestellte laufen zu Ende und werden danach ueberholt). Rueckgabe: {fehlend: [...]} - die
-- Stellen, an denen ein Vorschlag fehlt. Nur fuer Auftraege in werkbank, sonst ist die Liste leer.
CREATE OR REPLACE FUNCTION marketing.video_hf_abgleichen(p_auftrag uuid) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE v_n int;
BEGIN
    PERFORM 1 FROM marketing.video_werke WHERE auftrag = p_auftrag FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM auftrag.auftraege
                                 WHERE id = p_auftrag AND art = 'video' AND stand = 'werkbank') THEN
        RETURN jsonb_build_object('fehlend', '[]'::jsonb);
    END IF;
    UPDATE marketing.video_hf_clips h
       SET stand = 'ueberholt', vergeben_bis = NULL, geaendert = now()
     WHERE h.auftrag = p_auftrag AND h.stand IN ('vorgeschlagen', 'freigegeben', 'fertig', 'fehler')
       AND NOT marketing._video_hf_aktuell(h);
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n > 0 THEN PERFORM marketing._video_zaehlen(p_auftrag); END IF;
    RETURN jsonb_build_object('fehlend', marketing._video_hf_fehlend(p_auftrag));
END $$;

-- Vorschlag anlegen: {start_bild, end_bild, prompt, dauer_s, preis, modell, von_variante, nach_variante}.
-- Nur wenn die Stelle wirklich fehlt (beide Szenen fertig, die genannten Varianten aktuell, kein Clip dazu).
-- Bestellt wird nichts. Gibt die id zurueck, sonst NULL und nichts aendert sich.
CREATE OR REPLACE FUNCTION marketing.video_hf_vorschlagen(p_auftrag uuid, p_nach_szene uuid, p_teil jsonb)
RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE
    v_f jsonb;
    v_id uuid;
    v_versuch int;
BEGIN
    IF jsonb_typeof(p_teil) IS DISTINCT FROM 'object' OR NOT marketing._video_hf_prompt_ok(p_teil->>'prompt')
       OR coalesce(p_teil->>'start_bild', '') = '' OR coalesce(p_teil->>'end_bild', '') = ''
       OR jsonb_typeof(p_teil->'preis') IS DISTINCT FROM 'number'
       OR jsonb_typeof(p_teil->'dauer_s') IS DISTINCT FROM 'number' THEN
        RETURN NULL; END IF;
    PERFORM 1 FROM marketing.video_werke WHERE auftrag = p_auftrag FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM auftrag.auftraege
                                 WHERE id = p_auftrag AND art = 'video' AND stand = 'werkbank') THEN
        RETURN NULL; END IF;
    SELECT f INTO v_f FROM jsonb_array_elements(marketing._video_hf_fehlend(p_auftrag)) f
     WHERE (f->>'nach_szene')::uuid = p_nach_szene;
    IF v_f IS NULL OR (v_f->>'nach_variante')::int IS DISTINCT FROM (p_teil->>'nach_variante')::int
       OR (v_f->>'von_variante')::int IS DISTINCT FROM (p_teil->>'von_variante')::int THEN
        RETURN NULL; END IF;
    SELECT coalesce(max(versuch), 0) + 1 INTO v_versuch FROM marketing.video_hf_clips
     WHERE auftrag = p_auftrag AND nach_szene = p_nach_szene;
    INSERT INTO marketing.video_hf_clips (auftrag, art, von_szene, nach_szene, von_variante, nach_variante, versuch,
                                          start_bild, end_bild, prompt, modell, dauer_s, preis)
    VALUES (p_auftrag, 'uebergang', (v_f->>'von_szene')::uuid, p_nach_szene, (v_f->>'von_variante')::int,
            (v_f->>'nach_variante')::int, v_versuch, p_teil->>'start_bild', p_teil->>'end_bild',
            btrim(p_teil->>'prompt'), coalesce(p_teil->>'modell', 'kling3_0'), (p_teil->>'dauer_s')::int,
            (p_teil->>'preis')::numeric)
    RETURNING id INTO v_id;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN v_id;
END $$;

-- Urteil des Menschen: erzeugen | verwerfen, ausschliesslich ueber den Weg 'seite'.
--   erzeugen:  nur aus vorgeschlagen, Auftrag in werkbank, Clip noch aktuell, p_preis_gesehen = preis, Text gueltig.
--              Der (geaenderte) Text wird gespeichert, der Clip geht auf freigegeben.
--   verwerfen: aus vorgeschlagen, fertig oder fehler -> verworfen (an der Stelle bleibt ein normaler Schnitt).
-- Rueckgabe {"ok": true, "stand": ...} oder {"ok": false, "grund": weg|stand|ueberholt|preis|prompt|auftrag}.
CREATE OR REPLACE FUNCTION marketing.video_hf_entscheiden(p_id uuid, p_urteil text, p_prompt text,
    p_preis_gesehen numeric, p_von text, p_weg text) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_h marketing.video_hf_clips;
    v_von text := left(coalesce(nullif(btrim(p_von), ''), 'unbekannt'), 60);
BEGIN
    IF p_weg IS DISTINCT FROM 'seite' THEN RETURN jsonb_build_object('ok', false, 'grund', 'weg'); END IF;
    IF p_urteil IS NULL OR p_urteil NOT IN ('erzeugen', 'verwerfen') THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'stand'); END IF;
    SELECT * INTO v_h FROM marketing.video_hf_clips WHERE id = p_id FOR UPDATE;
    IF v_h.id IS NULL THEN RETURN jsonb_build_object('ok', false, 'grund', 'stand'); END IF;

    IF p_urteil = 'verwerfen' THEN
        IF v_h.stand NOT IN ('vorgeschlagen', 'fertig', 'fehler') THEN
            RETURN jsonb_build_object('ok', false, 'grund', 'stand'); END IF;
        UPDATE marketing.video_hf_clips
           SET stand = 'verworfen', vergeben_bis = NULL, entschieden_von = v_von, entschieden_am = now(), geaendert = now()
         WHERE id = p_id;
        PERFORM marketing._video_zaehlen(v_h.auftrag);
        RETURN jsonb_build_object('ok', true, 'stand', 'verworfen');
    END IF;

    IF v_h.stand <> 'vorgeschlagen' THEN RETURN jsonb_build_object('ok', false, 'grund', 'stand'); END IF;
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = v_h.auftrag AND art = 'video' AND stand = 'werkbank') THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'auftrag'); END IF;
    IF NOT marketing._video_hf_aktuell(v_h) THEN
        UPDATE marketing.video_hf_clips SET stand = 'ueberholt', geaendert = now() WHERE id = p_id;
        PERFORM marketing._video_zaehlen(v_h.auftrag);
        RETURN jsonb_build_object('ok', false, 'grund', 'ueberholt');
    END IF;
    IF p_preis_gesehen IS NULL OR p_preis_gesehen <> v_h.preis THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'preis', 'preis', v_h.preis); END IF;
    IF NOT marketing._video_hf_prompt_ok(p_prompt) THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'prompt'); END IF;
    UPDATE marketing.video_hf_clips
       SET stand = 'freigegeben', prompt = btrim(p_prompt), hinweis = NULL, fehler = NULL,
           entschieden_von = v_von, entschieden_am = now(), geaendert = now()
     WHERE id = p_id;
    PERFORM marketing._video_zaehlen(v_h.auftrag);
    RETURN jsonb_build_object('ok', true, 'stand', 'freigegeben');
END $$;

-- Naechsten Clip fuer den Higgsfield-Arbeiter vergeben. Vorher: Bestellungen OHNE vermerkte Higgsfield-Nummer, deren
-- Leihe abgelaufen ist, gehen auf fehler (der Arbeiter ist mitten im Bestellen abgebrochen; ob Higgsfield den
-- Auftrag hat, weiss niemand - es wird NIE neu bestellt). Vergeben wird: der aelteste freigegebene Clip eines
-- Auftrags in werkbank (-> bestellt) oder ein bestellter MIT Nummer, dessen Leihe abgelaufen ist (weiter warten).
CREATE OR REPLACE FUNCTION marketing.video_hf_nehmen(p_lease interval) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_h marketing.video_hf_clips;
    v_alt record;
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    FOR v_alt IN
        UPDATE marketing.video_hf_clips
           SET stand = 'fehler', vergeben_bis = NULL, geaendert = now(),
               fehler = 'Die Bestellung wurde unterbrochen. Bitte bei Higgsfield nachsehen, ob der Clip dort entstanden ist.'
         WHERE stand = 'bestellt' AND hf_auftrag IS NULL AND vergeben_bis < now()
        RETURNING id, auftrag, versuch, nach_szene
    LOOP
        PERFORM marketing.video_hinweis(v_alt.auftrag, 'hf_' || replace(left(v_alt.id::text, 8), '-', ''),
            'Ein Higgsfield-Übergang wurde beim Bestellen unterbrochen und wartet auf eine Entscheidung.');
        PERFORM marketing._video_zaehlen(v_alt.auftrag);
    END LOOP;
    SELECT h.* INTO v_h FROM marketing.video_hf_clips h
     WHERE (h.stand = 'freigegeben'
            AND EXISTS (SELECT 1 FROM auftrag.auftraege a WHERE a.id = h.auftrag AND a.art = 'video' AND a.stand = 'werkbank'))
        OR (h.stand = 'bestellt' AND h.hf_auftrag IS NOT NULL AND h.vergeben_bis < now())
     ORDER BY (h.stand = 'bestellt') DESC, h.entschieden_am, h.angelegt
     LIMIT 1
     FOR UPDATE SKIP LOCKED;
    IF v_h.id IS NULL THEN RETURN NULL; END IF;
    UPDATE marketing.video_hf_clips
       SET stand = 'bestellt', vergeben_bis = now() + p_lease, bestellt_am = coalesce(bestellt_am, now()), geaendert = now()
     WHERE id = v_h.id
    RETURNING * INTO v_h;
    PERFORM marketing._video_zaehlen(v_h.auftrag);
    RETURN to_jsonb(v_h) || jsonb_build_object(
        'mandant', (SELECT mandant FROM marketing.video_werke WHERE auftrag = v_h.auftrag),
        'nach_nr', (SELECT nr FROM marketing.video_szenen WHERE id = v_h.nach_szene),
        'von_nr', (SELECT nr FROM marketing.video_szenen WHERE id = v_h.von_szene));
END $$;

-- Preis unmittelbar vor dem Bestellen: gleich dem freigegebenen -> true. Sonst geht der Clip mit dem neuen Preis
-- zurueck auf vorgeschlagen (der Mensch muss neu klicken) -> false. Nur fuer bestellt ohne Nummer.
CREATE OR REPLACE FUNCTION marketing.video_hf_preis_pruefen(p_id uuid, p_preis numeric) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_h marketing.video_hf_clips;
BEGIN
    SELECT * INTO v_h FROM marketing.video_hf_clips WHERE id = p_id FOR UPDATE;
    IF v_h.id IS NULL OR v_h.stand <> 'bestellt' OR v_h.hf_auftrag IS NOT NULL OR p_preis IS NULL OR p_preis < 0 THEN
        RETURN false; END IF;
    IF p_preis = v_h.preis THEN RETURN true; END IF;
    UPDATE marketing.video_hf_clips
       SET stand = 'vorgeschlagen', vergeben_bis = NULL, bestellt_am = NULL, entschieden_von = NULL, entschieden_am = NULL,
           hinweis = 'Der Preis hat sich geändert: vorher ' || v_h.preis || ' Credits. Bitte neu freigeben.',
           preis = p_preis, geaendert = now()
     WHERE id = p_id;
    PERFORM marketing._video_zaehlen(v_h.auftrag);
    RETURN false;
END $$;

-- Higgsfields Auftragsnummer vermerken (gleich nach dem Bestellen, vor dem Warten). Nur einmal.
CREATE OR REPLACE FUNCTION marketing.video_hf_auftrag_merken(p_id uuid, p_hf_auftrag text, p_preis numeric)
RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_auftrag uuid;
BEGIN
    IF p_hf_auftrag IS NULL OR p_hf_auftrag !~ '^[A-Za-z0-9][A-Za-z0-9_-]{5,79}$' THEN RETURN false; END IF;
    UPDATE marketing.video_hf_clips
       SET hf_auftrag = p_hf_auftrag, preis_bestellt = coalesce(p_preis, preis), geaendert = now()
     WHERE id = p_id AND stand = 'bestellt' AND hf_auftrag IS NULL
    RETURNING auftrag INTO v_auftrag;
    IF v_auftrag IS NULL THEN RETURN false; END IF;
    PERFORM marketing._video_zaehlen(v_auftrag);
    RETURN true;
END $$;

-- Leihe eines bestellten Clips verlaengern (der Arbeiter wartet auf Higgsfield); false = aufhoeren.
CREATE OR REPLACE FUNCTION marketing.video_hf_weiter(p_id uuid, p_lease interval) RETURNS boolean
LANGUAGE plpgsql AS $$
BEGIN
    IF p_lease IS NULL THEN RAISE EXCEPTION 'Die Leihdauer fehlt'; END IF;
    UPDATE marketing.video_hf_clips SET vergeben_bis = now() + p_lease, geaendert = now()
     WHERE id = p_id AND stand = 'bestellt';
    RETURN FOUND;
END $$;

-- Fertigen Uebergang melden: nur aus bestellt mit Nummer.
CREATE OR REPLACE FUNCTION marketing.video_hf_fertig(p_id uuid, p_datei text, p_dauer_s numeric) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_auftrag uuid;
BEGIN
    IF coalesce(btrim(p_datei), '') = '' OR p_dauer_s IS NULL OR p_dauer_s <= 0 THEN RETURN false; END IF;
    UPDATE marketing.video_hf_clips
       SET stand = 'fertig', vergeben_bis = NULL, datei = p_datei, clip_dauer_s = p_dauer_s, fehler = NULL,
           fertig_am = now(), geaendert = now()
     WHERE id = p_id AND stand = 'bestellt' AND hf_auftrag IS NOT NULL
    RETURNING auftrag INTO v_auftrag;
    IF v_auftrag IS NULL THEN RETURN false; END IF;
    PERFORM marketing._video_zaehlen(v_auftrag);
    RETURN true;
END $$;

-- Bestellen oder Laden ist gescheitert: bestellt -> fehler (wartet auf einen Menschen), dazu ein Hinweis.
CREATE OR REPLACE FUNCTION marketing.video_hf_fehler(p_id uuid, p_grund text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_h marketing.video_hf_clips;
BEGIN
    UPDATE marketing.video_hf_clips
       SET stand = 'fehler', vergeben_bis = NULL,
           fehler = left(coalesce(nullif(btrim(p_grund), ''), 'Unbekannter Fehler'), 300), geaendert = now()
     WHERE id = p_id AND stand = 'bestellt'
    RETURNING * INTO v_h;
    IF v_h.id IS NULL THEN RETURN false; END IF;
    PERFORM marketing.video_hinweis(v_h.auftrag, 'hf_' || replace(left(v_h.id::text, 8), '-', ''),
        'Ein Higgsfield-Übergang ist gescheitert und wartet auf eine Entscheidung.');
    PERFORM marketing._video_zaehlen(v_h.auftrag);
    RETURN true;
END $$;

-- "Nochmal mit Aenderung": aus fertig, fehler oder verworfen entsteht ein NEUER Vorschlag mit dem neuen Text
-- (dieselben Bilder, derselbe Preis als Anhalt); der alte wird verworfen. Bestellt wird erst nach einem neuen
-- Klick. Nur ueber den Weg 'seite', Auftrag in werkbank, Clip noch aktuell. Gibt die neue id zurueck, sonst NULL.
CREATE OR REPLACE FUNCTION marketing.video_hf_nochmal(p_id uuid, p_prompt text, p_von text, p_weg text) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE
    v_h marketing.video_hf_clips;
    v_id uuid;
    v_versuch int;
BEGIN
    IF p_weg IS DISTINCT FROM 'seite' OR NOT marketing._video_hf_prompt_ok(p_prompt) THEN RETURN NULL; END IF;
    SELECT * INTO v_h FROM marketing.video_hf_clips WHERE id = p_id FOR UPDATE;
    IF v_h.id IS NULL OR v_h.stand NOT IN ('fertig', 'fehler', 'verworfen') THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM auftrag.auftraege WHERE id = v_h.auftrag AND art = 'video' AND stand = 'werkbank')
       OR NOT marketing._video_hf_aktuell(v_h) THEN RETURN NULL; END IF;
    UPDATE marketing.video_hf_clips
       SET stand = 'verworfen', vergeben_bis = NULL, geaendert = now()
     WHERE id = p_id AND stand <> 'verworfen';
    IF EXISTS (SELECT 1 FROM marketing.video_hf_clips WHERE auftrag = v_h.auftrag AND nach_szene = v_h.nach_szene
                  AND stand IN ('vorgeschlagen', 'freigegeben', 'bestellt', 'fertig', 'fehler')) THEN
        RAISE EXCEPTION 'An dieser Stelle lebt schon ein anderer Clip'; END IF;
    SELECT coalesce(max(versuch), 0) + 1 INTO v_versuch FROM marketing.video_hf_clips
     WHERE auftrag = v_h.auftrag AND nach_szene = v_h.nach_szene;
    INSERT INTO marketing.video_hf_clips (auftrag, art, von_szene, nach_szene, von_variante, nach_variante, versuch,
                                          start_bild, end_bild, prompt, modell, dauer_s, preis, hinweis)
    VALUES (v_h.auftrag, v_h.art, v_h.von_szene, v_h.nach_szene, v_h.von_variante, v_h.nach_variante, v_versuch,
            v_h.start_bild, v_h.end_bild, btrim(p_prompt), v_h.modell, v_h.dauer_s, v_h.preis,
            'Neuer Versuch von ' || left(coalesce(nullif(btrim(p_von), ''), 'unbekannt'), 60) || '.')
    RETURNING id INTO v_id;
    PERFORM marketing._video_zaehlen(v_h.auftrag);
    RETURN v_id;
END $$;

-- "Ohne Uebergang weiter" fuer eine Stelle, an der noch kein Vorschlag steht (etwa weil der Higgsfield-Arbeiter
-- nicht laeuft): der Uebergang der Szene wird zum normalen Schnitt. Nur ueber den Weg 'seite', Auftrag in werkbank
-- und nur, solange an der Stelle kein Clip lebt (lebende Clips werden ueber video_hf_entscheiden verworfen).
CREATE OR REPLACE FUNCTION marketing.video_hf_ohne(p_auftrag uuid, p_nach_nr int, p_weg text) RETURNS boolean
LANGUAGE plpgsql AS $$
DECLARE v_s marketing.video_szenen;
BEGIN
    IF p_weg IS DISTINCT FROM 'seite' THEN RETURN false; END IF;
    PERFORM 1 FROM marketing.video_werke WHERE auftrag = p_auftrag FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM auftrag.auftraege
                                 WHERE id = p_auftrag AND art = 'video' AND stand = 'werkbank') THEN
        RETURN false; END IF;
    SELECT * INTO v_s FROM marketing.video_szenen WHERE auftrag = p_auftrag AND nr = p_nach_nr FOR UPDATE;
    IF v_s.id IS NULL OR v_s.uebergang IS DISTINCT FROM 'higgsfield' THEN RETURN false; END IF;
    IF EXISTS (SELECT 1 FROM marketing.video_hf_clips WHERE auftrag = p_auftrag AND nach_szene = v_s.id
                  AND stand IN ('vorgeschlagen', 'freigegeben', 'bestellt', 'fertig', 'fehler')) THEN
        RETURN false; END IF;
    UPDATE marketing.video_szenen SET uebergang = 'schnitt', geaendert = now() WHERE id = v_s.id;
    PERFORM marketing._video_zaehlen(p_auftrag);
    RETURN true;
END $$;

-- Fuer die Sales-Seite: je Ziel-Szene der juengste Clip, der nicht ueberholt ist, dazu die Summe der bestellten
-- Credits des Videos und die Zahl der Stellen, an denen der Vorschlag noch vorbereitet wird.
CREATE OR REPLACE FUNCTION marketing.video_hf_liste(p_auftrag uuid) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'clips', coalesce((SELECT jsonb_agg(z.j ORDER BY z.nr) FROM (
            SELECT DISTINCT ON (h.nach_szene) n.nr,
                   jsonb_build_object(
                       'id', h.id, 'art', h.art, 'stand', h.stand, 'versuch', h.versuch, 'nach_nr', n.nr, 'von_nr', v.nr,
                       'start_bild', h.start_bild, 'end_bild', h.end_bild, 'prompt', h.prompt, 'modell', h.modell,
                       'dauer_s', h.dauer_s, 'preis', h.preis, 'preis_bestellt', h.preis_bestellt, 'datei', h.datei,
                       'clip_dauer_s', h.clip_dauer_s, 'fehler', h.fehler, 'hinweis', h.hinweis,
                       'entschieden_von', h.entschieden_von, 'entschieden_am', h.entschieden_am,
                       'bestellt_am', h.bestellt_am, 'fertig_am', h.fertig_am,
                       'aktuell', marketing._video_hf_aktuell(h)) AS j
              FROM marketing.video_hf_clips h
              JOIN marketing.video_szenen n ON n.id = h.nach_szene
              LEFT JOIN marketing.video_szenen v ON v.id = h.von_szene
             WHERE h.auftrag = p_auftrag AND h.stand <> 'ueberholt'
             ORDER BY h.nach_szene, h.versuch DESC) z), '[]'::jsonb),
        'credits', (SELECT coalesce(sum(preis_bestellt), 0) FROM marketing.video_hf_clips WHERE auftrag = p_auftrag),
        'bestellt', (SELECT count(*) FROM marketing.video_hf_clips WHERE auftrag = p_auftrag AND hf_auftrag IS NOT NULL),
        'vorbereitung', (SELECT coalesce(jsonb_agg(f->'nach_nr'), '[]'::jsonb)
                           FROM jsonb_array_elements(marketing._video_hf_fehlend(p_auftrag)) f));
$$;

REVOKE ALL ON FUNCTION marketing._video_hf_prompt_ok(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_hf_aktuell(marketing.video_hf_clips) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_hf_fehlend(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_hf_blockiert(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing._video_hf_fertige(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_abgleichen(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_vorschlagen(uuid, uuid, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_entscheiden(uuid, text, text, numeric, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_nehmen(interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_preis_pruefen(uuid, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_auftrag_merken(uuid, text, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_weiter(uuid, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_fertig(uuid, text, numeric) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_fehler(uuid, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_nochmal(uuid, text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_ohne(uuid, int, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.video_hf_liste(uuid) FROM PUBLIC;
REVOKE ALL ON TABLE marketing.video_hf_clips FROM PUBLIC, anon, authenticated, sales_app;
-- Kein Grant an sales_app, anon, authenticated: nur die Marketing-API (supabase_admin) fasst diese Tabelle an.

COMMIT;
