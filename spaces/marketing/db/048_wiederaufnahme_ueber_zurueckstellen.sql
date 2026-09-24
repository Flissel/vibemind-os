-- 048_wiederaufnahme_ueber_zurueckstellen.sql — vorlagenauftraege_wieder-
-- aufnehmen() ruft jetzt marketing.vorlagenauftrag_zurueckstellen() auf,
-- statt dessen Zurueckstell-Regeln ein zweites Mal zu duplizieren (Fix
-- round 1, Befund 2, Task 6, 2026-09-24-terminkarten). 047 ist bereits in
-- Produktion angewendet - diese Migration aendert NUR den Funktionskoerper
-- (CREATE OR REPLACE); Signatur, REVOKE und Grants bleiben unveraendert.
-- Additiv, idempotent, eine Transaktion.
--
-- Warum das wichtig ist: 045 definiert die Zurueckstell-Regeln
-- (fehlversuche+1; runde>1 -> nachbessern, sonst neu; 3. Fehlschlag ->
-- gescheitert) GENAU EINMAL in vorlagenauftrag_zurueckstellen(). 047s erste
-- Fassung kopierte dieselbe CASE-Logik ein zweites Mal in
-- vorlagenauftraege_wiederaufnehmen() hinein - eine kuenftige Aenderung an
-- der Schwelle (z. B. von 3 auf 5 Fehlversuche, oder ein zusaetzlicher
-- Zwischenstatus) haette nur EINE der beiden Stellen treffen koennen, je
-- nachdem, wer sie fand, und die beiden Funktionen waeren stillschweigend
-- auseinandergelaufen. Diese Fassung ruft stattdessen fuer jede liegen
-- gebliebene Zeile vorlagenauftrag_zurueckstellen() direkt auf - die
-- Zurueckstell-Regeln existieren jetzt an genau einer Stelle im Code.
--
-- FOR UPDATE SKIP LOCKED (statt nur FOR UPDATE wie in 047) auf der
-- Auswahl-Anfrage: eine Zeile, an der GERADE ein anderer gleichzeitiger
-- Aufrufer arbeitet (und die deshalb ohnehin schon gesperrt waere), soll
-- die Wiederaufnahme nicht blockieren - sie ist entweder zu frisch, um
-- schon als liegen geblieben zu gelten, oder wird beim naechsten Aufruf
-- erneut betrachtet, sobald die andere Transaktion committet hat.
BEGIN;

CREATE OR REPLACE FUNCTION marketing.vorlagenauftraege_wiederaufnehmen(p_alter interval)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    v_id uuid;
    v_anzahl integer := 0;
BEGIN
    FOR v_id IN
        SELECT id FROM marketing.vorlagenauftraege
         WHERE status = 'in_arbeit' AND updated_at < now() - p_alter
           FOR UPDATE SKIP LOCKED
    LOOP
        PERFORM marketing.vorlagenauftrag_zurueckstellen(v_id,
            'Bearbeitung abgebrochen (Zeitueberschreitung) - erneut eingereiht');
        v_anzahl := v_anzahl + 1;
    END LOOP;
    RETURN v_anzahl;
END $$;

-- Unveraendert gegenueber 047: Wartungsfunktion, nur fuer den Vorlagen-
-- Arbeiter (laeuft als supabase_admin - Eigentuemer duerfen ohne GRANT
-- ausfuehren). Kein Laden braucht sie je, also kein GRANT an
-- sales_app/sales_app_<x>.
REVOKE ALL ON FUNCTION marketing.vorlagenauftraege_wiederaufnehmen(interval) FROM PUBLIC;

COMMIT;
