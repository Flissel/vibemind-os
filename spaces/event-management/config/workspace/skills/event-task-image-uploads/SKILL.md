---
name: event-task-image-uploads
description: Foto aus dem Chat an eine Aufgabe der Event-App hängen.
---

# Fotos an Aufgaben hängen

Nimm diesen Ablauf, wenn jemand ein Bild schickt, das zu einer Aufgabe gehört —
sei es zu einer neuen ("der Anhänger muss noch abgeholt werden") oder zu einer
vorhandenen ("das Foto gehört zur Bar-Aufgabe").

Das Foto geht **nicht** durch ein MCP-Werkzeug. Die Werkzeuge tauschen Text
aus; ein Bild als Base64 sprengt sowohl das Kontextfenster als auch die
Größengrenze der Anfrage. Es wird darum als Datei hochgeladen — das ist der
Grund für den Umweg über die Shell.

## Ablauf

**1. Erst die Aufgabe, dann das Foto.**
Neue Aufgabe mit `create_task`, vorhandene über `list_tasks` finden. In beiden
Fällen brauchst du die Task-ID. `actor` ist der Anzeigename der Person, die
geschrieben hat — nie dein eigener.

Der Text der Nachricht wird der Titel, das was du auf dem Bild siehst die
`description`. Der Titel bleibt kurz, er ist die Kennung.

**2. Den Pfad des eingegangenen Bildes nehmen.**
Eingehende Bilder liegen unter `/home/node/.openclaw/workspace/media/inbound/`.
Nimm den Pfad aus der aktuellen Nachricht. Rate ihn nicht — wenn du ihn nicht
hast, nimm die zuletzt geänderte Datei:

```bash
find /home/node/.openclaw/workspace/media/inbound -type f -printf '%T@ %p\n' \
  | sort -rn | head -1 | cut -d' ' -f2-
```

**3. Hochladen.**
Adresse und Token stehen in der OpenClaw-Config, nicht in Umgebungsvariablen —
lies sie von dort. Die MCP-Adresse endet auf `/api/mcp`; dieser Teil muss weg,
bevor `/api/mcp/task-image` drangehängt wird.

```bash
cfg=/home/node/.openclaw/openclaw.json
url=$(node -e "const c=require(process.argv[1]); process.stdout.write(c.mcp.servers['event-app'].url.replace(/\/api\/mcp\/?$/, ''))" "$cfg")
auth=$(node -e "const c=require(process.argv[1]); process.stdout.write(c.mcp.servers['event-app'].headers.Authorization)" "$cfg")

curl -s -X POST \
  -H "Authorization: $auth" \
  -F "file=@<Bildpfad>" \
  "$url/api/mcp/task-image?taskId=<TaskID>&actor=<Anzeigename>"
```

**4. Antwort prüfen, bevor du Vollzug meldest.**
Erfolg ist genau `{"status":"ok", ...}`. Alles andere heißt: Die Aufgabe steht,
das Foto nicht. Sag das dann auch — sonst sucht später jemand ein Bild, das es
nie gab.

## Was du zurückmeldest

- Hat geklappt: `„<Titel>" ist angelegt, das Foto hängt dran.`
- Foto fehlgeschlagen: `„<Titel>" ist angelegt, aber das Foto hängt nicht dran: <Meldung der App>.`

## Grenzen

- Erlaubt sind JPEG, PNG, WebP und GIF bis 10 MB. Größeres lehnt die App mit
  Begründung ab, das gibst du unverändert weiter.
- Ein zweites Foto zur selben Aufgabe **ersetzt** das erste. Wenn unklar ist, ob
  ersetzt oder eine zweite Aufgabe gemeint war, frag nach.
- Token, Adressen und Header gehören nie in den Chat — auch nicht als Beleg,
  dass etwas funktioniert hat.
- Kosten liest du nicht aus einem Bild ab. Steht keine Zahl in der Nachricht,
  bleibt das Feld leer.
