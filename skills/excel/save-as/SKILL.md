---
name: excel-save-as
description: Speichere die aktive Excel-Mappe unter einem neuen Pfad/Namen.
app: excel
agents: ["*"]
trigger: "speichern unter|save as|excel save copy"
inputs:
  - {name: path, type: string, description: "Vollständiger Pfad inkl. Dateiname und .xlsx-Endung"}
expected_state:
  description: "Der Window-Title von Excel zeigt den neuen Dateinamen ohne 'Speichern unter'-Dialog im Vordergrund."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Save-As öffnen** — `handoff_action(action_type="hotkey", keys="f12")` (F12 = Speichern unter in Excel; Ctrl+Shift+S geht in vielen Versionen ebenfalls).
2. **Dialog warten** — `handoff_action(action_type="sleep", seconds=1)`. Der Datei-Dialog braucht einen Moment.
3. **Pfad eingeben** — `handoff_action(action_type="type", text="{path}")`. Der Datei-Dialog hat ein Path-Input-Field, in dem ein vollständiger Pfad inkl. `.xlsx` direkt angenommen wird.
4. **Bestätigen** — `handoff_action(action_type="press", key="enter")`.
5. **Falls Überschreiben-Dialog erscheint** — `vision_analyze(mode="state_analysis", prompt="Erscheint ein Bestätigungsdialog 'Datei existiert bereits'? JSON {dialog_open: bool}")`. Wenn ja: `handoff_action(press, key="left")` + `enter` für „Ja".
6. **Validieren** — Window-Title via `handoff_get_focus`. Erfolg wenn der Title den neuen Dateinamen enthält.

# Adjustments

- Bei Excel im Office-365-Mode wird F12 manchmal von OneDrive abgefangen — dann Workaround: `alt+f` → `s` → `s` (Datei-Menü → Speichern unter → Speichern unter), das ist die robustere Tastenfolge.
