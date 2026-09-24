-- 047_vorlagenauftraege_wiederaufnahme.sql — Wiederaufnahme liegen
-- gebliebener Vorlagen-Auftraege (Controller ruling 22, Task 6,
-- 2026-09-24-terminkarten).
--
-- Der Vorlagen-Arbeiter (spaces/marketing/workers/vorlagen_worker.py) faengt
-- jede Ausnahme aus entwerfen()/vorlegen() im Prozess selbst ab und stellt
-- den Auftrag ueber vorlagenauftrag_zurueckstellen() zurueck - aber das
-- deckt nur Fehler waehrend eines laufenden Prozesses ab. Stirbt der
-- Arbeiter hart (Kill, Neustart des Rechners, OOM) zwischen
-- vorlagenauftrag_uebernehmen() und dem naechsten Schritt, bleibt die Zeile
-- auf 'in_arbeit' stehen - uebernehmen() waehlt nur 'neu'/'nachbessern' aus,
-- kein Codepfad holt eine 'in_arbeit'-Zeile je von selbst wieder zurueck.
--
-- Diese Funktion ist reine Wartung: jede Zeile mit status='in_arbeit', deren
-- updated_at aelter als p_alter ist, wird mit GENAU denselben Regeln wie
-- vorlagenauftrag_zurueckstellen() zurueckgestuft (fehlversuche+1; runde>1
-- -> nachbessern, sonst neu; der dritte Fehlschlag -> gescheitert). Additiv,
-- idempotent (CREATE OR REPLACE), eine Transaktion, ruehrt keine bestehenden
-- Daten an ausser den tatsaechlich betroffenen Zeilen.
BEGIN;

CREATE OR REPLACE FUNCTION marketing.vorlagenauftraege_wiederaufnehmen(p_alter interval)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v_anzahl integer;
BEGIN
    WITH betroffen AS (
        SELECT id, runde, fehlversuche
          FROM marketing.vorlagenauftraege
         WHERE status = 'in_arbeit' AND updated_at < now() - p_alter
           FOR UPDATE
    )
    UPDATE marketing.vorlagenauftraege a
       SET status = CASE
               WHEN b.fehlversuche + 1 >= 3 THEN 'gescheitert'
               WHEN b.runde > 1 THEN 'nachbessern'
               ELSE 'neu'
           END,
           fehlversuche = b.fehlversuche + 1,
           fehler = 'Bearbeitung abgebrochen (Zeitueberschreitung) - erneut eingereiht'
      FROM betroffen b
     WHERE a.id = b.id;
    GET DIAGNOSTICS v_anzahl = ROW_COUNT;
    RETURN v_anzahl;
END $$;

-- Wartungsfunktion, nur fuer den Vorlagen-Arbeiter (laeuft als
-- supabase_admin - Eigentuemer duerfen ohne GRANT ausfuehren). Kein Laden
-- braucht sie je, also kein GRANT an sales_app/sales_app_<x>.
REVOKE ALL ON FUNCTION marketing.vorlagenauftraege_wiederaufnehmen(interval) FROM PUBLIC;

COMMIT;
