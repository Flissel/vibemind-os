-- 043_marketing_versandauftraege.sql — sales-claw wird der einzige Versandweg
-- (Betreiber-Entscheid 12.09.2026, Spec
--  docs/superpowers/specs/2026-09-12-sales-claw-einziger-versandweg.md).
--
-- Marketing SCHREIBT, sales-claw VERSENDET. Dazwischen liegt diese
-- Auftragswarteschlange — dasselbe Muster wie die Lead-Bruecke F2/F3
-- (041/042), die zwischen denselben zwei Spaces bereits in beide Richtungen
-- laeuft: eine Tabelle in marketing.*, zwei SECURITY-DEFINER-Funktionen fuer
-- sales_app, sonst kein Recht.
--
-- EIN AUFTRAG IST EINE BITTE, KEIN BEFEHL. Er loest nie einen Versand aus; er
-- erzeugt hoechstens einen Entwurf in sales.drafts mit status='pending'. Die
-- Freigabe bleibt beim Menschen, die Zustellung bei den drei Dispatchern
-- (dispatch.py, mail_dispatch.py, linkedin_dispatch.py).
--
-- WARUM MARKETING NICHT DIREKT IN sales.drafts SCHREIBT: dann liefe der
-- Auftrag an allen Toren vorbei, die in sales-claws entwurf_erstellen
-- haengen — gemeinsame Sperrliste, Loeschantrag, Privat-Flag,
-- UWG-Erstansprache, WhatsApp-Kontakt-Freigabe, Anhangspruefung. Die Tore
-- bleiben, wo sie sind; ein zweiter Ort dafuer waere genau der Fehler, den
-- dieser Entscheid abschafft.

-- ---------------------------------------------------------------------------
-- 1. Kennungs-Normalisierung in SQL
-- ---------------------------------------------------------------------------
-- compliance.sperrliste definiert die Kennungsform (013, CONSTRAINT
-- kennung_form), normalisiert wurde sie bisher nur in Python — zweimal, in
-- spaces/marketing/tools/sperrliste.py und spaces/sales-claw/sales-mcp/
-- sperrliste.py. Diese Funktion hier muss dieselbe Form treffen, sonst sieht
-- sie die Sperren der anderen Seite nicht. Darum steht sie in `compliance`,
-- dem Schema, dem die Form gehoert — und nicht als dritte private Kopie in
-- marketing.*.
--
-- Gespiegelt aus sales-claws sperrliste.py, Zeile fuer Zeile:
--   „(0)"-Vorwahlnull faellt weg, „00" wird „+", nationale Null wird +49
--   (deutscher Betrieb, dokumentierte Annahme), WhatsApp-Chat-IDs
--   (4917...@c.us) liefern ihren Ziffernteil.
CREATE OR REPLACE FUNCTION compliance.kennung_email(p_text text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_e text;
BEGIN
    v_e := lower(btrim(coalesce(p_text, '')));
    IF position('@' in v_e) = 0 THEN RETURN NULL; END IF;
    IF v_e ~ '\s' THEN RETURN NULL; END IF;   -- Leerraum INNEN: keine Adresse
    RETURN 'email:' || v_e;
END $$;

CREATE OR REPLACE FUNCTION compliance.kennung_tel(p_text text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_t       text;
    v_plus    boolean;
    v_ziffern text;
BEGIN
    v_t := btrim(coalesce(p_text, ''));
    IF position('@' in v_t) > 0 THEN          -- WhatsApp-Chat-ID
        v_t := split_part(v_t, '@', 1);
    END IF;
    v_t := replace(v_t, '(0)', '');
    v_plus := left(v_t, 1) = '+';
    v_ziffern := regexp_replace(v_t, '\D', '', 'g');
    IF v_ziffern = '' THEN RETURN NULL; END IF;
    IF left(v_ziffern, 2) = '00' THEN
        v_ziffern := substr(v_ziffern, 3);
    ELSIF NOT v_plus AND left(v_ziffern, 1) = '0' THEN
        v_ziffern := '49' || substr(v_ziffern, 2);
    END IF;
    IF length(v_ziffern) < 6 THEN RETURN NULL; END IF;
    RETURN 'tel:+' || v_ziffern;
END $$;

-- E-Mail zuerst, dann Telefon — genau wie sperrliste.kennungen().
CREATE OR REPLACE FUNCTION compliance.kennung(p_text text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(compliance.kennung_email(p_text), compliance.kennung_tel(p_text));
$$;

-- ---------------------------------------------------------------------------
-- 2. Die Auftragstabelle
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marketing.versandauftraege (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nur die drei Kanaele, die sales-claw wirklich zustellen kann. Telegram
    -- fehlt mit Absicht: sales-claw hat dafuer keinen Dispatcher (Spec §6.1).
    kanal        text NOT NULL CHECK (kanal IN ('whatsapp', 'email', 'linkedin')),
    -- Wie Marketing den Empfaenger kennt: E-Mail oder Telefonnummer. Marketing
    -- kennt keine lead_id — das Aufloesen ist sales-claws Sache.
    empfaenger   text NOT NULL,
    -- Dieselbe Adresse in Sperrlisten-Form. Steht hier, damit der Abgleich und
    -- die Wiederholungserkennung nicht bei jedem Lesen neu normalisieren.
    kennung      text NOT NULL,
    betreff      text NOT NULL DEFAULT '',
    nachricht    text NOT NULL,
    -- BLOSSER Dateiname aus /media-erzeugt, ohne jede Pfadangabe — sales-claws
    -- medien.pruefe weist Pfadanteile ab, das soll hier schon auffallen.
    medien_datei text NOT NULL DEFAULT '',
    kampagne     text NOT NULL DEFAULT '',
    quelle       text NOT NULL DEFAULT '',
    -- md5 der Nachricht: Wiederholungserkennung ohne den ganzen Text zu
    -- vergleichen und ohne ihn zweimal zu indizieren.
    textsumme    text NOT NULL,
    status       text NOT NULL DEFAULT 'offen'
                 CHECK (status IN ('offen', 'angenommen', 'abgelehnt')),
    draft_id     text,
    grund        text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    erledigt_am  timestamptz
);

CREATE INDEX IF NOT EXISTS idx_versandauftraege_offen
    ON marketing.versandauftraege(created_at) WHERE status = 'offen';
CREATE INDEX IF NOT EXISTS idx_versandauftraege_wiederholung
    ON marketing.versandauftraege(kanal, kennung, textsumme, created_at DESC);

-- ---------------------------------------------------------------------------
-- 3. Anlegen — die Marketing-Seite
-- ---------------------------------------------------------------------------
-- Gibt IMMER jsonb zurueck, nie eine Exception fuer etwas, das ein Agent
-- legitim treffen kann: eine Exception rollt die Transaktion zurueck und
-- erreicht den Agenten als roher SQL-Fehler. Eine Absage mit Grund kann er
-- lesen und daraus etwas machen.
--
-- Marketing ruft das als supabase_admin (so laeuft sync/_db.py heute schon);
-- eine marketing_app-Rolle existiert nicht und wird hier keine erfunden.
CREATE OR REPLACE FUNCTION marketing.versandauftrag_anlegen(
    p_kanal        text,
    p_empfaenger   text,
    p_nachricht    text,
    p_betreff      text DEFAULT '',
    p_medien_datei text DEFAULT '',
    p_kampagne     text DEFAULT '',
    p_quelle       text DEFAULT '')
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = marketing, compliance, pg_temp
AS $$
DECLARE
    v_kennung  text;
    v_sperre   text;
    v_summe    text;
    v_alt      uuid;
    v_id       uuid;
    v_datei    text := btrim(coalesce(p_medien_datei, ''));
BEGIN
    IF coalesce(p_kanal, '') NOT IN ('whatsapp', 'email', 'linkedin') THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Unzulaessiger Kanal %L. sales-claw stellt zu: whatsapp, email, '
            'linkedin. Telegram hat dort keinen Versandweg.', coalesce(p_kanal, '')));
    END IF;
    IF length(btrim(coalesce(p_nachricht, ''))) = 0 THEN
        RETURN jsonb_build_object('ok', false, 'grund', 'Die Nachricht ist leer.');
    END IF;

    -- Formpruefung des Anhangfeldes (NICHT die Anhangspruefung selbst — die
    -- steht in sales-claws medien.pruefe und bleibt dort). Hier faellt nur
    -- auf, was als Feld schon falsch ist: ein Pfad statt eines Dateinamens.
    IF v_datei <> '' AND (position('/' in v_datei) > 0 OR position('\' in v_datei) > 0) THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'medien_datei muss der blosse Dateiname sein, ohne Pfad — %L '
            'enthaelt einen Pfadanteil.', v_datei));
    END IF;

    v_kennung := compliance.kennung(p_empfaenger);
    IF v_kennung IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Aus %L laesst sich weder eine E-Mail-Adresse noch eine '
            'Telefonnummer lesen.', coalesce(p_empfaenger, '')));
    END IF;

    -- Die gemeinsame Verbotsliste. Sie deckt auch Marketings eigene
    -- Widerrufe ab: compliance.trg_emails_sperre traegt jeden
    -- unsubscribed_at und jeden zweiten Bounce selbst ein (013). Ein
    -- zweiter Blick in marketing.emails waere Doppelung, keine Sicherheit.
    v_sperre := compliance.ist_gesperrt(v_kennung);
    IF v_sperre IS NOT NULL THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Dieser Empfaenger steht auf der gemeinsamen Verbotsliste (%s). '
            'Es entsteht kein Auftrag.', v_sperre));
    END IF;

    -- Wiederholung: derselbe Kanal, derselbe Empfaenger, derselbe Text
    -- innerhalb von 24 Stunden ist derselbe Auftrag. Ein Agent, der zweimal
    -- dasselbe moechte, soll keine zwei Nachrichten erzeugen.
    v_summe := md5(btrim(p_nachricht));
    SELECT id INTO v_alt FROM marketing.versandauftraege
     WHERE kanal = p_kanal AND kennung = v_kennung AND textsumme = v_summe
       AND created_at > now() - interval '24 hours'
     ORDER BY created_at DESC LIMIT 1;
    IF v_alt IS NOT NULL THEN
        RETURN jsonb_build_object('ok', true, 'id', v_alt, 'wiederholung', true,
            'grund', 'Derselbe Auftrag besteht schon (letzte 24 Stunden) — '
                     'es wurde keiner dazugelegt.');
    END IF;

    INSERT INTO marketing.versandauftraege
        (kanal, empfaenger, kennung, betreff, nachricht, medien_datei,
         kampagne, quelle, textsumme)
    VALUES (p_kanal, btrim(p_empfaenger), v_kennung,
            btrim(coalesce(p_betreff, '')), btrim(p_nachricht), v_datei,
            btrim(coalesce(p_kampagne, '')), btrim(coalesce(p_quelle, '')), v_summe)
    RETURNING id INTO v_id;

    RETURN jsonb_build_object('ok', true, 'id', v_id, 'wiederholung', false,
        'grund', 'Auftrag liegt bereit. sales-claw macht daraus einen Entwurf; '
                 'freigegeben wird er vom Betreiber.');
END $$;

-- ---------------------------------------------------------------------------
-- 4. Lesen und Erledigen — die sales-claw-Seite
-- ---------------------------------------------------------------------------
-- SECURITY DEFINER wie 041/042: sales_app ruft diese zwei Funktionen und hat
-- sonst kein Recht an marketing.versandauftraege.
CREATE OR REPLACE FUNCTION marketing.versandauftraege_offen(p_limit int DEFAULT 20)
RETURNS TABLE (id uuid, kanal text, empfaenger text, betreff text,
               nachricht text, medien_datei text, kampagne text, quelle text,
               seit timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
    SELECT id, kanal, empfaenger, betreff, nachricht, medien_datei, kampagne,
           quelle, created_at
    FROM marketing.versandauftraege
    WHERE status = 'offen'
    ORDER BY created_at ASC
    LIMIT greatest(1, least(coalesce(p_limit, 20), 100));
$$;

CREATE OR REPLACE FUNCTION marketing.versandauftrag_erledigen(
    p_id uuid, p_status text, p_draft_id text, p_grund text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = marketing, pg_temp AS $$
DECLARE v_n int;
BEGIN
    IF p_status NOT IN ('angenommen', 'abgelehnt') THEN
        RAISE EXCEPTION 'versandauftrag_erledigen: status muss angenommen oder abgelehnt sein';
    END IF;
    -- Eine Ablehnung ohne Grund waere fuer Marketing wertlos: der Grund ist
    -- das Einzige, was von sales-claws Toren zurueckkommt.
    IF p_status = 'abgelehnt' AND length(btrim(coalesce(p_grund, ''))) = 0 THEN
        RAISE EXCEPTION 'versandauftrag_erledigen: abgelehnt braucht einen grund';
    END IF;
    IF p_status = 'angenommen' AND length(btrim(coalesce(p_draft_id, ''))) = 0 THEN
        RAISE EXCEPTION 'versandauftrag_erledigen: angenommen braucht eine draft_id';
    END IF;
    UPDATE marketing.versandauftraege
       SET status = p_status, draft_id = nullif(btrim(coalesce(p_draft_id, '')), ''),
           grund = coalesce(p_grund, ''), erledigt_am = now()
     WHERE id = p_id AND status = 'offen';
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n = 1;
END $$;

-- Anlegen ist Marketings Seite. Ohne dieses REVOKE duerfte es JEDER rufen
-- (Postgres gibt EXECUTE auf neue Funktionen an PUBLIC), auch sales_app —
-- der Aufruf scheiterte dann zwar am fehlenden INSERT-Recht, aber erst im
-- Inneren und mit einem Rechte-Fehler statt einer klaren Absage. Die Grenze
-- gehoert an die Tuer, nicht hinter sie.
REVOKE ALL ON FUNCTION marketing.versandauftrag_anlegen(text, text, text, text, text, text, text) FROM PUBLIC;

REVOKE ALL ON FUNCTION marketing.versandauftraege_offen(int) FROM PUBLIC;
REVOKE ALL ON FUNCTION marketing.versandauftrag_erledigen(uuid, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION marketing.versandauftraege_offen(int) TO sales_app;
GRANT EXECUTE ON FUNCTION marketing.versandauftrag_erledigen(uuid, text, text, text) TO sales_app;

-- Die Kennungs-Funktionen darf jeder rufen, der ohnehin an compliance kommt —
-- sie lesen nichts und entscheiden nichts, sie rechnen nur eine Zeichenkette um.
GRANT USAGE ON SCHEMA compliance TO sales_app;
GRANT EXECUTE ON FUNCTION compliance.kennung(text) TO sales_app;
GRANT EXECUTE ON FUNCTION compliance.kennung_email(text) TO sales_app;
GRANT EXECUTE ON FUNCTION compliance.kennung_tel(text) TO sales_app;
