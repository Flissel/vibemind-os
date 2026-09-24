-- verify_048.sql — Aequivalenz-Probe: vorlagenauftraege_wiederaufnehmen()
-- (048, ueber vorlagenauftrag_zurueckstellen()) liefert fuer jede Zeile
-- GENAU das, was ein direkter Aufruf von vorlagenauftrag_zurueckstellen()
-- auf einer Zeile im selben Ausgangszustand liefert - keine eigene
-- Duplizierung der Zurueckstell-Regeln mehr (Fix round 1, Befund 2).
--
-- verify_047.sql deckt weiterhin die Verhaltens-Faelle ab (frisch bleibt
-- unberuehrt, leere Kandidatenmenge -> 0) und bleibt unveraendert gueltig,
-- weil 048 die Signatur und das beobachtbare Verhalten von
-- vorlagenauftraege_wiederaufnehmen() unveraendert laesst (CREATE OR
-- REPLACE, reiner Koerpertausch - dieselben drei Ziele: neu/nachbessern/
-- gescheitert). Diese Datei fuegt NUR die Aequivalenz-Probe hinzu.
--
-- Wie in verify_045/046/047: eine Transaktion, am Ende ROLLBACK, nichts
-- bleibt in der Datenbank stehen. Die drei Szenarien laufen nacheinander,
-- jedes raeumt seine Zeilen selbst weg (M-4-Unique-Index: nur eine offene
-- terminkarte-Zeile gleichzeitig, art ist ohnehin nur 'terminkarte').
BEGIN;

-- Szenario 1: runde=1, fehlversuche=0 -> Referenzziel 'neu'.
DO $$
DECLARE
    v_a uuid; v_b uuid;
    v_status_a text; v_fehlversuche_a int; v_fehler_a text;
    v_status_b text; v_fehlversuche_b int; v_fehler_b text;
BEGIN
    -- A: direkter zurueckstellen()-Aufruf, als Referenz.
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe048a', 'terminkarte', 'referenz-r1', 'terminkarte', 'in_arbeit', 1, 0, now())
    RETURNING id INTO v_a;
    PERFORM marketing.vorlagenauftrag_zurueckstellen(v_a,
        'Bearbeitung abgebrochen (Zeitueberschreitung) - erneut eingereiht');
    SELECT status, fehlversuche, fehler INTO v_status_a, v_fehlversuche_a, v_fehler_a
      FROM marketing.vorlagenauftraege WHERE id = v_a;
    DELETE FROM marketing.vorlagenauftraege WHERE id = v_a;

    -- B: ueber wiederaufnehmen() (STALE), derselbe Ausgangszustand.
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe048b', 'terminkarte', 'wiederauf-r1', 'terminkarte', 'in_arbeit', 1, 0,
            now() - interval '20 minutes')
    RETURNING id INTO v_b;
    PERFORM marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    SELECT status, fehlversuche, fehler INTO v_status_b, v_fehlversuche_b, v_fehler_b
      FROM marketing.vorlagenauftraege WHERE id = v_b;
    DELETE FROM marketing.vorlagenauftraege WHERE id = v_b;

    IF v_status_a IS DISTINCT FROM v_status_b
       OR v_fehlversuche_a IS DISTINCT FROM v_fehlversuche_b
       OR v_fehler_a IS DISTINCT FROM v_fehler_b THEN
        RAISE EXCEPTION 'PROBE: Aequivalenz runde=1 verletzt: zurueckstellen=(%,%,%) wiederaufnehmen=(%,%,%)',
            v_status_a, v_fehlversuche_a, v_fehler_a, v_status_b, v_fehlversuche_b, v_fehler_b;
    END IF;
    IF v_status_a <> 'neu' THEN
        RAISE EXCEPTION 'PROBE: erwartet neu als Referenz, war %', v_status_a;
    END IF;
END $$;

-- Szenario 2: runde=2, fehlversuche=0 -> Referenzziel 'nachbessern'.
DO $$
DECLARE
    v_a uuid; v_b uuid;
    v_status_a text; v_fehlversuche_a int; v_fehler_a text;
    v_status_b text; v_fehlversuche_b int; v_fehler_b text;
BEGIN
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe048a', 'terminkarte', 'referenz-r2', 'terminkarte', 'in_arbeit', 2, 0, now())
    RETURNING id INTO v_a;
    PERFORM marketing.vorlagenauftrag_zurueckstellen(v_a,
        'Bearbeitung abgebrochen (Zeitueberschreitung) - erneut eingereiht');
    SELECT status, fehlversuche, fehler INTO v_status_a, v_fehlversuche_a, v_fehler_a
      FROM marketing.vorlagenauftraege WHERE id = v_a;
    DELETE FROM marketing.vorlagenauftraege WHERE id = v_a;

    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe048b', 'terminkarte', 'wiederauf-r2', 'terminkarte', 'in_arbeit', 2, 0,
            now() - interval '20 minutes')
    RETURNING id INTO v_b;
    PERFORM marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    SELECT status, fehlversuche, fehler INTO v_status_b, v_fehlversuche_b, v_fehler_b
      FROM marketing.vorlagenauftraege WHERE id = v_b;
    DELETE FROM marketing.vorlagenauftraege WHERE id = v_b;

    IF v_status_a IS DISTINCT FROM v_status_b
       OR v_fehlversuche_a IS DISTINCT FROM v_fehlversuche_b
       OR v_fehler_a IS DISTINCT FROM v_fehler_b THEN
        RAISE EXCEPTION 'PROBE: Aequivalenz runde=2 verletzt: zurueckstellen=(%,%,%) wiederaufnehmen=(%,%,%)',
            v_status_a, v_fehlversuche_a, v_fehler_a, v_status_b, v_fehlversuche_b, v_fehler_b;
    END IF;
    IF v_status_a <> 'nachbessern' THEN
        RAISE EXCEPTION 'PROBE: erwartet nachbessern als Referenz, war %', v_status_a;
    END IF;
END $$;

-- Szenario 3: bereits 2 Fehlversuche -> der DRITTE Fehlschlag -> Referenzziel
-- 'gescheitert', fehlversuche=3.
DO $$
DECLARE
    v_a uuid; v_b uuid;
    v_status_a text; v_fehlversuche_a int; v_fehler_a text;
    v_status_b text; v_fehlversuche_b int; v_fehler_b text;
BEGIN
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe048a', 'terminkarte', 'referenz-3x', 'terminkarte', 'in_arbeit', 1, 2, now())
    RETURNING id INTO v_a;
    PERFORM marketing.vorlagenauftrag_zurueckstellen(v_a,
        'Bearbeitung abgebrochen (Zeitueberschreitung) - erneut eingereiht');
    SELECT status, fehlversuche, fehler INTO v_status_a, v_fehlversuche_a, v_fehler_a
      FROM marketing.vorlagenauftraege WHERE id = v_a;
    DELETE FROM marketing.vorlagenauftraege WHERE id = v_a;

    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe048b', 'terminkarte', 'wiederauf-3x', 'terminkarte', 'in_arbeit', 1, 2,
            now() - interval '20 minutes')
    RETURNING id INTO v_b;
    PERFORM marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    SELECT status, fehlversuche, fehler INTO v_status_b, v_fehlversuche_b, v_fehler_b
      FROM marketing.vorlagenauftraege WHERE id = v_b;
    DELETE FROM marketing.vorlagenauftraege WHERE id = v_b;

    IF v_status_a IS DISTINCT FROM v_status_b
       OR v_fehlversuche_a IS DISTINCT FROM v_fehlversuche_b
       OR v_fehler_a IS DISTINCT FROM v_fehler_b THEN
        RAISE EXCEPTION 'PROBE: Aequivalenz dritter Fehlschlag verletzt: zurueckstellen=(%,%,%) wiederaufnehmen=(%,%,%)',
            v_status_a, v_fehlversuche_a, v_fehler_a, v_status_b, v_fehlversuche_b, v_fehler_b;
    END IF;
    IF v_status_a <> 'gescheitert' THEN
        RAISE EXCEPTION 'PROBE: erwartet gescheitert als Referenz, war %', v_status_a;
    END IF;
    IF v_fehlversuche_a <> 3 THEN
        RAISE EXCEPTION 'PROBE: fehlversuche erwartet 3, war %', v_fehlversuche_a;
    END IF;
END $$;

-- Rechte unveraendert: sales_app darf weiterhin nicht.
DO $$
BEGIN
    IF has_function_privilege('sales_app',
         'marketing.vorlagenauftraege_wiederaufnehmen(interval)', 'EXECUTE') THEN
        RAISE EXCEPTION 'PROBE: sales_app darf vorlagenauftraege_wiederaufnehmen aufrufen';
    END IF;
END $$;

ROLLBACK;
\echo verify_048: alle Proben bestanden
