# Wissens-Prüfung → Rowboat-Dashboard

Nächtlicher Lauf (`pruefung.py`), der zählt, wie gut Rowboats Notizen zusammenhängen, und das Ergebnis als
Dashboard-Notiz `knowledge/Kanal/Dashboards/Wissens-Gesundheit.md` ablegt: Notizen, lose Notizen (ohne jede
Linie im Graph), Verbindungen, tote Verbindungen, Inseln, je Ordner und je Quelle (`erzeugt_von`), dazu ein
Verlauf (`~/rowboat-wissens-pruefung/verlauf.json`, höchstens 60 Läufe).

Gezählt wird nach Rowboats Graph-Regeln: Eine Linie entsteht nur aus `[[voller/Pfad]]` (Text oder
Frontmatter) mit exakter Schreibweise. Kurzformen wie `[[Notiz]]` sind im Editor klickbar, ergeben im Graph
aber keine Linie und zählen hier als tot. `Meetings/` und versteckte Ordner zählen nicht mit. Tote Ziele unter
`People/` und `companys/*/Kontakte/` werden nur gezählt, nie genannt.

Auf `offload-vm`: `rowboat-wissens-pruefung.timer` (03:15 Berlin, nach den Geschäftsdaten um 02:30).

```bash
python3 ~/marketing-os/spaces/rowboat/wissens_pruefung/pruefung.py --trocken   # nur ausgeben
sudo systemctl start rowboat-wissens-pruefung.service                            # einmal echt
journalctl -u rowboat-wissens-pruefung -n 20
```

Einrichten (einmalig):

```bash
sudo cp ~/marketing-os/spaces/rowboat/wissens_pruefung/deploy/rowboat-wissens-pruefung.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rowboat-wissens-pruefung.timer
```

Rückweg: `sudo systemctl disable --now rowboat-wissens-pruefung.timer`, Unit-Dateien löschen, Notiz und
`~/rowboat-wissens-pruefung/` entfernen.
