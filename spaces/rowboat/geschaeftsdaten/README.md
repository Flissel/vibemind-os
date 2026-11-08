# Geschäftsdaten → Rowboat

Nächtlicher Lauf (`lauf.py`), der sales-claw und Marketing nur lesend abfragt und je Firma Steckbriefe,
Newsletter-Archiv und Wochenbild unter `knowledge/companys/<Firma>/` ablegt.
Plan: `docs/superpowers/plans/2026-10-26-rowboat-zentrale-teil-b-geschaeftsdaten.md`.

Auf `offload-vm` läuft er über `rowboat-geschaeftsdaten.timer` (02:30 Berlin) als
`rowboat-geschaeftsdaten.service` (`deploy/`). Status steht in `~/rowboat-geschaeftsdaten/stand.json`
und im Journal (`journalctl -u rowboat-geschaeftsdaten -n 50`).

## Verbindungen im Rowboat-Graph

Rowboat zieht Kanten nur aus `[[Wiki-Links]]` (Text oder Frontmatter), nicht aus Ordnern. Damit kein
Steckbrief lose im Graph hängt, legt der Lauf je Firma unter `Bereiche/` eine Übersicht pro Ordner von
`Kontakte/` an (`bAV.md`, …, `Ohne Bereich.md`, `Archiv.md`) plus `Bereiche/_overview.md`:

- Steckbrief: `parent: "[[companys/<Firma>/Bereiche/<Hauptbereich>]]"`, weitere Bereiche als
  `weitere_bereiche: "[[…]], [[…]]"`.
- Bereichs-Übersicht → `parent` auf `Bereiche/_overview`; diese → `parent` auf `companys/<Firma>/Marke`,
  falls die Notiz existiert.
- Übersichten enthalten nur Bereichsnamen und Zahlen (keine Personendaten, kein Datum), Schlüssel
  `uebersicht:<Ordner>`. Wird ein Bereich leer, entfernt der Lauf seine Übersicht – zuletzt, nach
  Datenschutz-Löschungen und Umzügen, und ohne den Massen-Wächter zu zählen.
- Vom Lauf archivierte Steckbriefe verweisen auf `Bereiche/Archiv`; die Übersicht dazu entsteht aus dem
  Bestand, nach einer frischen Archivierung also spätestens in der nächsten Nacht.

## Runbook: Lauf von Hand, auch mit Freigabe des Massen-Wächters

`systemctl start rowboat-geschaeftsdaten` reicht keine Optionen durch. Für einen Handlauf startest du
dasselbe Programm mit derselben Umgebung wie die Einheit: Benutzer `debian`, Arbeitsordner `/home/debian`,
keine Umgebungsvariablen (es gelten die Vorgaben in `lauf.py`). Vorher prüfen, dass der Dienst nicht
gerade läuft (`systemctl is-active rowboat-geschaeftsdaten` ergibt `inactive`).

```sh
# 1. Erst trocken: zeigt die Zählungen, schreibt nichts (auch kein stand.json)
sudo -u debian -H sh -c 'cd /home/debian && /usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/geschaeftsdaten/lauf.py --trocken --massen-ok VibeMind'

# 2. Dann echt, nur für diesen einen Lauf freigegeben
sudo -u debian -H sh -c 'cd /home/debian && /usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/geschaeftsdaten/lauf.py --massen-ok VibeMind'
```

- `--massen-ok <Firma>` (Ordnername unter `companys/`, Groß-/Kleinschreibung egal, mehrfach möglich)
  lässt die Archivierungen dieser Firma trotz Massen-Wächter (mehr als 20 % und mehr als 5 Steckbriefe auf
  einmal) **nur in diesem Lauf** durch. Der nächste Timer-Lauf schützt wieder.
- Die Obergrenze von 15 Löschungen je Lauf gilt weiter. Größere Mengen verteilen sich deshalb über
  mehrere Läufe. Die Freigabe muss dann bei jedem Handlauf erneut gegeben werden, bis
  `stand.json` für die Firma `zurueckgestellt: 0` zeigt.
- Vorher klären, warum so viele Kontakte fehlen (Laden ohne Sichten? leere Quelle?). Der Wächter
  stoppt genau solche Fehler.

## Stündlich: Post und Termine

`stuendlich.py` läuft jede Stunde zur Minute 5 (`rowboat-post-termine.timer`) und hält zwei Dinge frisch,
damit sie nicht bis zur Nacht warten müssen:

- die Termin-Notizen unter `companys/<Firma>/Termine/` samt `_overview` (wie der Nachtlauf, aus denselben
  Ereignissen und Steckbrief-Pfaden),
- die Post-Notizen `Post/_overview.md` und `Post/Zu beantworten.md` (offene Mails der sales-ui; ohne
  Stichtag `POST_AUFRAEUMEN_AB` in `~/sales-claw/.env` gilt nichts als offen und es wird keine Post gelesen).

**Was der Lauf nicht tut:** Er löscht, archiviert und verschiebt nie. Eine abgesagte oder verschwundene
Termin-Notiz, die Notiz eines gesperrten Kontakts und die alte Datei eines Termins mit neuem Pfad bleiben
bis zur nächsten Nacht liegen; das erledigt allein der Nachtlauf (mit Obergrenze und Massen-Wächter). Er
schreibt nur bei inhaltlicher Änderung, legt keine `stand.json` an (die gehört dem Nachtlauf) und
protokolliert nur Zählungen. Steckbrief-Links in der Post-Notiz gibt es nur für Absender, die im Laden
`sales` ein Steckbrief-Kontakt sind.

**Gemeinsame Sperre:** Beide Läufe nehmen `flock` auf `/home/debian/rowboat-geschaeftsdaten/lauf.lock`
(überschreibbar mit `GESCHAEFTSDATEN_SPERRE`). Ist sie belegt, setzt die Stunde aus
(„Geschaeftsdaten-Lauf aktiv, Stunde ausgesetzt“, Exit 0); der Nachtlauf wartet bis zu 10 Minuten, danach
Exit 1 und `stand.json` mit `fehler: "Sperre"`. Die Sperre fällt mit dem Prozess weg.

**Befehle:**

```sh
# Trocken: zeigt die Zählungen, schreibt nichts
sudo -u debian -H sh -c 'cd /home/debian && /usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/geschaeftsdaten/stuendlich.py --trocken'
# Sofort eine Stunde laufen lassen
sudo systemctl start rowboat-post-termine
# Protokoll
journalctl -u rowboat-post-termine -n 50 --no-pager
systemctl list-timers rowboat-post-termine.timer
```

Exit-Codes: 0 ok oder ausgesetzt; 1 Teilerfolg oder Konfig-Fehler; 2 DB-Fehler vor jedem Schreiben.

**Einrichten:** Die Einheiten aus `deploy/` nach `/etc/systemd/system/` kopieren, dann
`sudo systemctl daemon-reload && sudo systemctl enable --now rowboat-post-termine.timer`. Der Timer hat
`Persistent=false`: verpasste Stunden werden nicht nachgeholt.

**Rückweg:** `sudo systemctl disable --now rowboat-post-termine.timer`. Die Notizen bleiben stehen; den
Termin-Teil pflegt weiter der Nachtlauf. `Post/_overview.md` und `Post/Zu beantworten.md` kann man dann von
Hand löschen (eigene Notizen erkennt man an `erzeugt_von: rowboat-geschaeftsdaten`).
