-- 092_versandauftraege_kanaele.sql — Postkanaele fuer Versandauftraege
-- (Plan „Beitraege auf weitere Kanaele", Task 10). Marketing darf eigene
-- Beitraege auch fuer Bluesky, Mastodon, den Telegram-Kanal, Discord und dev.to
-- beauftragen. Es bleibt eine BITTE: sales-claw ist der einzige Absender.
--
-- RUNBOOK: Diese Migration ersetzt versandauftrag_anlegen (Koerper aus 043,
-- drei Aenderungen: Kanalliste, Postkanal-Zweig, Absagetexte mit Kanalnamen).
-- Ein Replay von 043 ueberschreibt die Funktion wieder mit dem alten Stand.
-- Danach 092 erneut einspielen und verify_043 + verify_092 zusammen proben:
--   migration_probe 092_... verify_043.sql verify_092.sql
-- Kein SECURITY DEFINER, keine neuen GRANTs: CREATE OR REPLACE mit gleicher
-- Signatur behaelt die Rechte, sales_app bekommt kein EXECUTE.

BEGIN;

ALTER TABLE marketing.versandauftraege DROP CONSTRAINT IF EXISTS versandauftraege_kanal_check;
ALTER TABLE marketing.versandauftraege ADD CONSTRAINT versandauftraege_kanal_check
    CHECK (kanal IN ('whatsapp', 'email', 'linkedin', 'linkedin_post', 'telegram',
                     'bluesky_post', 'mastodon_post', 'telegram_kanal_post',
                     'discord_post', 'devto_post'));

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
    IF coalesce(p_kanal, '') NOT IN ('whatsapp', 'email', 'linkedin',
                                     'linkedin_post', 'telegram',
                                     'bluesky_post', 'mastodon_post',
                                     'telegram_kanal_post', 'discord_post',
                                     'devto_post') THEN
        RETURN jsonb_build_object('ok', false, 'grund', format(
            'Unzulaessiger Kanal %L. sales-claw stellt zu: whatsapp, email, '
            'telegram, linkedin (Nachricht an einen Kontakt) und '
            'linkedin_post, bluesky_post, mastodon_post, telegram_kanal_post, '
            'discord_post, devto_post (Beitrag auf den eigenen Kanal, ohne '
            'Empfaenger).',
            coalesce(p_kanal, '')));
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

    IF p_kanal IN ('linkedin_post', 'bluesky_post', 'mastodon_post',
                   'telegram_kanal_post', 'discord_post', 'devto_post') THEN
        -- Ein Beitrag geht an NIEMANDEN. Es gibt keinen Empfaenger, also auch
        -- keine Kennung, keine Einwilligung und keine Verbotslisten-Frage —
        -- die Konstante haelt nur die Wiederholungserkennung am Laufen.
        IF length(btrim(coalesce(p_empfaenger, ''))) > 0 THEN
            RETURN jsonb_build_object('ok', false, 'grund', format(
                '%s ist ein Beitrag auf den eigenen Kanal und hat '
                'keinen Empfaenger. Fuer eine Nachricht AN jemanden ist der '
                'Kanal linkedin der richtige.', p_kanal));
        END IF;
        -- sales-claws post_entwurf_erstellen macht aus dem Thema den Betreff
        -- („Post: <thema>") und erkennt daran, ob es zu diesem Thema schon
        -- einen Beitrag gibt. Ohne Thema faellt der Auftrag erst drueben
        -- durch — also hier fragen, wo es noch billig ist.
        IF length(btrim(coalesce(p_betreff, ''))) = 0 THEN
            RETURN jsonb_build_object('ok', false, 'grund', format(
                '%s braucht ein Thema — schreib es ins Feld betreff (bei '
                'devto_post ist es der Titel). sales-claw macht daraus den '
                'Betreff „Post: <thema>" und erkennt daran Doppelungen.',
                p_kanal));
        END IF;
        v_kennung := 'post:eigenes-profil';
    ELSIF p_kanal = 'telegram' THEN
        -- EINE CHAT-ID IST KEINE TELEFONNUMMER. Sie sieht einer zum
        -- Verwechseln aehnlich (die des Betreibers hat zehn Ziffern), und
        -- compliance.kennung_tel machte daraus `tel:+49…` — eine fremde
        -- Festnetznummer. Deshalb eine eigene Form, `tg:<ziffern>`, die auch
        -- gar nicht in compliance.sperrliste passt (deren CHECK kennt nur
        -- email: und tel:).
        --
        -- Die Verbotsliste greift trotzdem, nur woanders: sales-claw loest
        -- die chat_id einem KONTAKT zu, und dessen E-Mail und Telefonnummer
        -- pruefen dort dieselben Tore wie bei jedem anderen Kanal. Wer
        -- irgendwo „nein" gesagt hat, bekommt auch hier nichts.
        IF btrim(coalesce(p_empfaenger, '')) !~ '^[0-9]{1,18}$'
           OR btrim(coalesce(p_empfaenger, '')) ~ '^0+$' THEN
            RETURN jsonb_build_object('ok', false, 'grund', format(
                'Telegram adressiert ueber eine positive numerische chat_id '
                '(z. B. 1092040975) — %L ist keine.', coalesce(p_empfaenger, '')));
        END IF;
        v_kennung := 'tg:' || btrim(p_empfaenger);
    ELSE
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

COMMIT;
