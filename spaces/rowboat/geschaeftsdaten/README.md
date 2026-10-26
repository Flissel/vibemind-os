# Geschäftsdaten → Rowboat

Nächtlicher Lauf (`lauf.py`), der sales-claw und Marketing nur lesend abfragt und je Firma Steckbriefe,
Newsletter-Archiv und Wochenbild unter `knowledge/companys/<Firma>/` ablegt.
Plan: `docs/superpowers/plans/2026-10-26-rowboat-zentrale-teil-b-geschaeftsdaten.md`.

Auf `offload-vm` läuft er über `rowboat-geschaeftsdaten.timer` (02:30 Berlin) als
`rowboat-geschaeftsdaten.service` (`deploy/`). Status steht in `~/rowboat-geschaeftsdaten/stand.json`
und im Journal (`journalctl -u rowboat-geschaeftsdaten -n 50`).

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
