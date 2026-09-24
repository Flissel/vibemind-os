-- verify_047.sql — Wiederaufnahme liegen gebliebener Vorlagen-Auftraege (047).
-- Jede Verletzung bricht ab (psql -v ON_ERROR_STOP=1). Alles, was Zeilen
-- anlegt, laeuft in einer Transaktion und wird am Ende zurueckgerollt.
--
-- Szenarien laufen NACHEINANDER, jedes raeumt seine eigene Zeile am Ende
-- weg: der partielle Unique-Index idx_vorlagenauftraege_ein_offener (046)
-- laesst systemweit nur EINE Zeile mit status NOT IN ('freigegeben',
-- 'gescheitert') gleichzeitig zu (art ist ohnehin nur 'terminkarte').
BEGIN;

-- Rechte: kein Laden darf die Wartungsfunktion aufrufen - sie ist reine
-- Arbeiter-Wartung, kein Auftragsweg fuer Sales.
DO $$
BEGIN
    IF has_function_privilege('sales_app',
         'marketing.vorlagenauftraege_wiederaufnehmen(interval)', 'EXECUTE') THEN
        RAISE EXCEPTION 'PROBE: sales_app darf vorlagenauftraege_wiederaufnehmen aufrufen';
    END IF;
END $$;

-- Szenario 1: eine FRISCHE in_arbeit-Zeile (updated_at = jetzt) bleibt
-- unberuehrt - 0 Zeilen wiederaufgenommen, Status/fehlversuche unveraendert.
DO $$
DECLARE v_id uuid; v_n integer;
BEGIN
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe047', 'terminkarte', 'frisch', 'terminkarte', 'in_arbeit', 1, 0, now())
    RETURNING id INTO v_id;

    v_n := marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'PROBE: eine frische Zeile wurde angefasst (% Zeilen)', v_n;
    END IF;
    PERFORM 1 FROM marketing.vorlagenauftraege
     WHERE id = v_id AND status = 'in_arbeit' AND fehlversuche = 0;
    IF NOT FOUND THEN RAISE EXCEPTION 'PROBE: frische Zeile wurde veraendert'; END IF;

    DELETE FROM marketing.vorlagenauftraege WHERE id = v_id;
END $$;

-- Szenario 2: STALE (updated_at 20 Min. alt), runde=1 -> Ziel 'neu',
-- fehlversuche 0 -> 1, der feste Fehlertext steht.
DO $$
DECLARE v_id uuid; v_n integer; v_status text; v_fehlversuche int; v_fehler text;
BEGIN
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe047', 'terminkarte', 'stale-r1', 'terminkarte', 'in_arbeit', 1, 0,
            now() - interval '20 minutes')
    RETURNING id INTO v_id;

    v_n := marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'PROBE: erwartet 1 wiederaufgenommene Zeile, war %', v_n;
    END IF;

    SELECT status, fehlversuche, fehler INTO v_status, v_fehlversuche, v_fehler
      FROM marketing.vorlagenauftraege WHERE id = v_id;
    IF v_status <> 'neu' THEN
        RAISE EXCEPTION 'PROBE: erwartet Status neu, war %', v_status;
    END IF;
    IF v_fehlversuche <> 1 THEN
        RAISE EXCEPTION 'PROBE: fehlversuche erwartet 1, war %', v_fehlversuche;
    END IF;
    IF v_fehler <> 'Bearbeitung abgebrochen (Zeitueberschreitung) - erneut eingereiht' THEN
        RAISE EXCEPTION 'PROBE: falscher Fehlertext: %', v_fehler;
    END IF;

    DELETE FROM marketing.vorlagenauftraege WHERE id = v_id;
END $$;

-- Szenario 3: STALE, runde=2 (schon einmal 'nein' bekommen) -> Ziel
-- 'nachbessern', nicht 'neu'.
DO $$
DECLARE v_id uuid; v_n integer; v_status text; v_fehlversuche int;
BEGIN
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe047', 'terminkarte', 'stale-r2', 'terminkarte', 'in_arbeit', 2, 0,
            now() - interval '20 minutes')
    RETURNING id INTO v_id;

    v_n := marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'PROBE: erwartet 1 wiederaufgenommene Zeile, war %', v_n;
    END IF;

    SELECT status, fehlversuche INTO v_status, v_fehlversuche
      FROM marketing.vorlagenauftraege WHERE id = v_id;
    IF v_status <> 'nachbessern' THEN
        RAISE EXCEPTION 'PROBE: erwartet Status nachbessern, war %', v_status;
    END IF;
    IF v_fehlversuche <> 1 THEN
        RAISE EXCEPTION 'PROBE: fehlversuche erwartet 1, war %', v_fehlversuche;
    END IF;

    DELETE FROM marketing.vorlagenauftraege WHERE id = v_id;
END $$;

-- Szenario 4: STALE, bereits 2 Fehlversuche -> der DRITTE Fehlschlag muss
-- auf 'gescheitert' gehen (dieselbe 3er-Schwelle wie zurueckstellen()).
DO $$
DECLARE v_id uuid; v_n integer; v_status text; v_fehlversuche int;
BEGIN
    INSERT INTO marketing.vorlagenauftraege
        (laden, art, beschreibung, vorlage, status, runde, fehlversuche, updated_at)
    VALUES ('probe047', 'terminkarte', 'stale-3x', 'terminkarte', 'in_arbeit', 1, 2,
            now() - interval '20 minutes')
    RETURNING id INTO v_id;

    v_n := marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'PROBE: erwartet 1 wiederaufgenommene Zeile, war %', v_n;
    END IF;

    SELECT status, fehlversuche INTO v_status, v_fehlversuche
      FROM marketing.vorlagenauftraege WHERE id = v_id;
    IF v_status <> 'gescheitert' THEN
        RAISE EXCEPTION 'PROBE: erwartet Status gescheitert, war %', v_status;
    END IF;
    IF v_fehlversuche <> 3 THEN
        RAISE EXCEPTION 'PROBE: fehlversuche erwartet 3, war %', v_fehlversuche;
    END IF;

    DELETE FROM marketing.vorlagenauftraege WHERE id = v_id;
END $$;

-- Ergaenzend: ein Aufruf auf eine leere Kandidatenmenge liefert 0, nicht
-- etwa einen Fehler.
DO $$
DECLARE v_n integer;
BEGIN
    v_n := marketing.vorlagenauftraege_wiederaufnehmen('15 minutes');
    IF v_n <> 0 THEN
        RAISE EXCEPTION 'PROBE: erwartet 0 bei leerer Kandidatenmenge, war %', v_n;
    END IF;
END $$;

ROLLBACK;
\echo verify_047: alle Proben bestanden
