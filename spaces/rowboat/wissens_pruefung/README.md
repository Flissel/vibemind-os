# Wissens-Prüfung → Rowboat-Dashboard

Nächtlicher Lauf (`pruefung.py`), der zählt, wie gut Rowboats Notizen zusammenhängen, und das Ergebnis als
Dashboard-Notiz `knowledge/Kanal/Dashboards/Wissens-Gesundheit.md` ablegt: Notizen, lose Notizen (ohne jede
Linie im Graph), Verbindungen, tote Verbindungen, Inseln, je Ordner und je Quelle (`erzeugt_von`), dazu ein
Verlauf (`~/rowboat-wissens-pruefung/verlauf.json`, höchstens 60 Läufe).

Gezählt wird nach Rowboats Graph-Regeln: Eine Linie entsteht nur aus `[[voller/Pfad]]` (Text oder
Frontmatter) mit exakter Schreibweise. Kurzformen wie `[[Notiz]]` sind im Editor klickbar, ergeben im Graph
aber keine Linie und zählen hier als tot. `Meetings/` und versteckte Ordner zählen nicht mit. Tote Ziele unter
`People/` und `companys/*/Kontakte/` werden nur gezählt, nie genannt.

Dazu kommt die **Ordnungs-Karte** `config/vault-map.json` neben `knowledge/`: Eine Notiz ohne
`parent: "[[…]]"` im Kopf bekommt von dort eine Linie zu ihrem Platz (z. B. `Agents/…` → `Areas/Agents`),
wenn es die Eltern-Notiz gibt. Das Dashboard zeigt, wie viele Linien erst die Karte zieht und wie viele
Notizen weder `parent:` noch einen Platz in der Karte haben (steigt diese Zahl, ist die Karte veraltet).

## Ordnungs-Karte erneuern (`karte.py`)

Rowboat läuft am PC im Fern-Modus und liest die Karte aus `~/rowboat-data/config/vault-map.json` auf der VM.
`start_desktop.py` erneuert sie am PC nur außerhalb des Fern-Modus; deshalb erneuert sie auf der VM
`rowboat-vault-map.timer` alle 10 Minuten. `karte.py` ruft `ehl_schema` aus `spaces/rowboat/schema` (Sync-Kopie,
nie von Hand ändern) wie `ehl_schema.cli map --profile vibemind` auf. Die Karte wird nur bei inhaltlicher
Änderung geschrieben. Fehlende Strukturnotizen werden angelegt, bestehende Notizen nie geändert. Ins Journal
geht eine Zeile je Lauf. Der VM-Checkout braucht dafür `spaces/rowboat/schema` im Sparse-Checkout.

Auf `offload-vm`: `rowboat-wissens-pruefung.timer` (03:15 Berlin, nach den Geschäftsdaten um 02:30).

```bash
python3 ~/marketing-os/spaces/rowboat/wissens_pruefung/pruefung.py --trocken   # nur ausgeben
sudo systemctl start rowboat-wissens-pruefung.service                            # einmal echt
journalctl -u rowboat-wissens-pruefung -n 20
```

Einrichten (einmalig):

```bash
cd ~/marketing-os && git sparse-checkout add spaces/rowboat/schema
sudo cp ~/marketing-os/spaces/rowboat/wissens_pruefung/deploy/rowboat-{wissens-pruefung,vault-map}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rowboat-wissens-pruefung.timer rowboat-vault-map.timer
```

Rückweg: `sudo systemctl disable --now rowboat-wissens-pruefung.timer rowboat-vault-map.timer`, Unit-Dateien
löschen, Notiz und `~/rowboat-wissens-pruefung/` entfernen. Die Karte selbst bleibt liegen. Rowboat liest sie
weiter, sie veraltet nur wieder.
