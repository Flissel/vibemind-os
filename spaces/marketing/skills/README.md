# Leitfaden für Marketing-Skills

Skills sind Ablaufbeschreibungen für das Modell (Ordner mit `SKILL.md`). Dieser Leitfaden
gilt für alle Skills unter `spaces/marketing/skills/`. Der Lader ist `claw/skills.py`.

## 1. Ablage und Format

`skills/<name>/SKILL.md` im OpenClaw-Format: Frontmatter zwischen zwei `---`-Zeilen,
**einzeilig** (`schluessel: wert`). Pflicht sind `name` und `description`. Optional:
`user-invocable: true|false` und `metadata` als einzeiliges JSON. Unbekannte Schlüssel
ignoriert der Lader. Alles nach dem zweiten `---` ist die Anweisung (`skill.anweisung`).

## 2. Benennung

`name` in `kebab-case`. Der Befehl ist der bereinigte Name nach OpenClaw-Regel: klein, alles
außer `a-z 0-9 _` wird `_`, Rand-`_` entfallen, höchstens 32 Zeichen (`marke-neu` wird
`/marke_neu`). Reserviert sind die eingebauten OpenClaw-Befehle und die des Telegram-Bots
(Listen mit Quelle in `claw/skills.py`). Ein reservierter oder doppelter Befehl ist ein
Fehler: `laden()` wirft `SkillFehler`, der Test `test_echte_skills_gueltig` schlägt fehl.
Es gibt nie ein stilles `_2`.

## 3. Einbindung in unsere Chats

Ein Skill gehört zu einem Chat nur über `metadata.vibemind.chats`, z. B.
`metadata: {"vibemind":{"chats":["marke"]}}`. Fehlt der Eintrag, wird der Skill geladen und
geprüft, ist aber in keinem Chat ein Befehl (so bleibt `newsletter-bild` unberührt). Für
Skills mit Chat gilt `description` ≤ 100 Zeichen (der Test prüft es). Der Chat ruft
`skills.befehle(chat)` für die Befehlsliste und `skills.aufloesen(text, chat)` für
`/<befehl> text` und `/skill <name> text` (Bindestrich, Unterstrich und Leerraum gelten
gleich). Der Aufrufer hängt `skill.anweisung` an den System-Prompt der Runde. Das geschieht
bei uns, weil der Marketing-Shim keine OpenClaw-Werkzeuge durchreicht: Der Skill-Text allein
führt das Modell, Schreibzugriffe macht der Code.

## 4. Einbindung ins OpenClaw-Gateway (optional)

Den Ordner in die Workspace-Saat kopieren (`anbinden.sh`). Voraussetzung ist ein passendes
Sidecar-Werkzeug, denn der Skill darf nur Werkzeuge voraussetzen, die das Gateway hat.
Achtung: Anfragen über OpenClaw zählen als „extra usage“.

## 5. Regeln für Skill-Texte

- Eine Frage pro Runde; Fortschritt sichtbar machen.
- Keine erfundenen Rechts- oder Zahlenangaben: Rechtstexte nur wörtlich aus Betreiber-
  nachricht oder gelesener Seite.
- Entscheidungen, die der Betreiber treffen muss (z. B. Vermittler ja/nein), stellt der Skill
  als Frage, er entscheidet sie nie.
- Der Text ist nur die halbe Absicherung: Was rechtlich oder finanziell zählt, prüft
  zusätzlich der Code (Wortlaut-Vergleich, Pflichtfelder, Datenbankregeln). Verlasse dich nie
  allein auf die Anweisung.

## 6. Testen

- **Lader-Test** (`claw/tests/test_skills.py`): Datei gültig, Befehl frei, Inhalt vollständig.
- **Prompt-Test:** Der Skill-Text landet im System-Prompt der Runde.
- **Ende-zu-Ende** auf der VM mit einer Wegwerf-Marke, danach abbrechen und den
  Wegwerf-Mandanten per SQL entfernen.
