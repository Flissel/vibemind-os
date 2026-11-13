-- 007_bereitstellung.sql: Bereitstellungs-Arbeiter (system/bereitstellung.py).
-- Wird nur als Admin angewendet (db/anwenden.py). Wiederholbar (if not exists).
--
-- schluessel_id: der Schluessel, den der Arbeiter fuer einen `anlegen`-Auftrag ausgestellt
-- hat. Ein `abbauen` widerruft genau diese Schluessel (nur solche, die zum Ziel des
-- Auftrags passen: Art und Event bzw. Person). Verschwindet der Schluessel mit seinem Event
-- (Kaskade aus 004), bleibt der Auftrag mit null stehen.
--
-- Spaltenrechte fuer event_app: 004 gab insert auf die ganze Tabelle. Ab hier nur die
-- Spalten eines neuen Auftrags (wie verbinden.bestaetigen und das Archivieren/Loeschen sie
-- schreiben); status, schritt, fehler, gestartet, beendet und schluessel_id setzt allein der
-- Arbeiter mit der Admin-DSN. Sonst koennte eine Leitung einen Auftrag mit fremdem
-- schluessel_id einschleusen und den Arbeiter beim Abbau einen fremden Schluessel
-- widerrufen lassen.
--
-- angelegt: Default clock_timestamp() statt now(). now() ist der Transaktionsbeginn; ein
-- `abbauen` und das folgende `anlegen` aus derselben Transaktion (verbinden.bestaetigen)
-- bekaemen denselben Wert, und der Arbeiter arbeitet strikt nach angelegt. event_app darf
-- angelegt nicht selbst setzen (sonst koennte ein Auftrag sich vordraengeln).
--
-- Die Policies bleiben wie in 004/006: ein `abbauen` beim Archivieren oder vor dem Loeschen
-- eines Events laeuft in derselben Transaktion VOR dem Loeschen, die bestaetigte Verbindung
-- ist dann noch da (bereitstellung_chat), und bereitstellung_anlegen erlaubt Leitung der
-- Firma bzw. Betreiber.

alter table event.bereitstellung
    add column if not exists schluessel_id uuid references event.schluessel (id)
        on delete set null;

alter table event.bereitstellung
    alter column angelegt set default clock_timestamp();

revoke insert on event.bereitstellung from event_app;
grant insert (art, ziel, event_id, person_id, chat_id, agent_id)
    on event.bereitstellung to event_app;

-- Der Arbeiter sucht offene Auftraege in Reihenfolge und je Agent.
create index if not exists bereitstellung_offen
    on event.bereitstellung (angelegt, id) where status in ('offen', 'laeuft');
create index if not exists bereitstellung_agent
    on event.bereitstellung (agent_id, angelegt);
