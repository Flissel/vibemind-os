# Messschritt 0 — liest `claude -p` auf dem Abo ein Foto?

**Datum:** 2026-09-24
**Skript:** `spaces/marketing/claw/scripts/messschritt_foto.py`
**Frage:** Kann ein direkter `claude -p`-Aufruf (Operator-Abo, kein API-Key) eine
Bilddatei per `Read`-Werkzeug lesen und die beschrifteten Formularfelder als
JSON zurückgeben? Relevant, weil der OpenAI-kompatible Shim
(`render_messages`) Bildteile verwirft — der direkte CLI-Aufruf ist der
einzige Weg für Task 5/6.

## Umgebungsanpassung

Dieser Messlauf fand in einer **verschachtelten** Claude-Code-Session statt
(`CLAUDECODE=1`, `CLAUDE_CODE_CHILD_SESSION=1` u. a. im Elternprozess-Environment).
Ein kurzer Sondierungsaufruf (`claude -p "Reply with exactly: OK" --output-format
json --model sonnet`, unverändertes Environment) lief jedoch ohne Nesting-Fehler
durch (`result: "OK"`, `is_error: false`). Die CLI hat die Verschachtelung in
diesem Fall **nicht** blockiert — es waren keine `CLAUDECODE`/`CLAUDE_CODE_*`-
Entfernungen aus dem Subprozess-Environment nötig, und das Skript macht daran
auch nichts (`subprocess.run` erbt das Environment unverändert).

Eine andere Anpassung war nötig: `claude` liegt zwar auf dem PATH, aber
`where claude` löst zuerst `C:\Users\User\bin\claude.exe` auf — ein kaputter
Wrapper, der intern versucht, `C:\Users\User\AppData\Roaming\npm\claude.cmd`
zu starten, was fehlschlägt (`Der Befehl "...\npm\claude.cmd" ist entweder
falsch geschrieben oder konnte nicht gefunden werden.`, Exitcode 1). Der
echte Client liegt unter `%USERPROFILE%\.local\bin\claude.exe`. Das Skript
ruft deshalb den **absoluten Pfad** `os.path.join(os.environ["USERPROFILE"],
".local", "bin", "claude.exe")` statt des bloßen Kommandonamens `"claude"`
auf.

## Gemessener Aufruf (argv)

```python
argv = [
    r"C:\Users\User\.local\bin\claude.exe",
    "-p",
    PROMPT,  # siehe Skript, wörtlich uebernommen aus dem Brief
    "--output-format", "json",
    "--allowedTools", "Read",
    "--model", "sonnet",
]
```

ausgeführt mit `cwd=<Temp-Ordner mit karte.png>`, `capture_output=True,
text=True, encoding="utf-8", timeout=300`, Environment unverändert vom
Elternprozess geerbt.

Run-Kommando:
```
C:\Users\User\Desktop\Vibemind_V1\.venv\Scripts\python.exe vibemind-os\spaces\marketing\claw\scripts\messschritt_foto.py
```
(im Worktree tatsächlich ausgeführt unter
`vibemind-os\.worktrees\setup-agent\spaces\marketing\claw\scripts\messschritt_foto.py`,
siehe Aufgaben-Hinweis zum Arbeitsort.)

## Rohausgabe des Messlaufs

```
Rueckgabewert: 0  Dauer: 14.3 s
Gefunden: ['Kunde', 'Telefon', 'Datum', 'Uhrzeit', 'Ort', 'Thema', 'Berater']
Treffer: 7/7
EXITCODE=0
```

## Ergebnis

| Messgröße | Wert |
|---|---|
| Rückgabewert | `0` |
| Dauer | `14.3 s` (Grenze: < 120 s) |
| Erwartete Felder | `Kunde, Telefon, Datum, Uhrzeit, Ort, Thema, Berater` |
| Gefundene Felder | `Kunde, Telefon, Datum, Uhrzeit, Ort, Thema, Berater` (exakt, Reihenfolge identisch) |
| Treffer | `7/7` |

## Gate-Verdikt

**7/7 → Bild-Route ist nutzbar.** Task 5 übernimmt die oben gemessene
argv-Form wörtlich (absoluter Pfad zu `claude.exe` statt bloßem `"claude"`,
`-p`, Prompt, `--output-format json`, `--allowedTools Read`,
`--model sonnet`), inklusive des Hinweises, dass `claude` auf PATH auf
diesem Host nicht zuverlässig ist und durch den absoluten Pfad ersetzt
werden muss.

## Offen

Sobald der Betreiber ein echtes Foto einer Terminkarte schickt:
`python messschritt_foto.py <foto>` erneut ausführen und die gefundenen
Felder (kein Soll-Ist-Vergleich möglich, da `erwartet=None` bei echtem Foto)
unten anhängen.

_(Noch kein echtes Foto erhalten — Stand 2026-09-24.)_
