# rowboat, video, desktop gegen den Vertrag

**Datum:** 2026-09-12
**Verträge:** `spaces/_contract/{rowboat,video,desktop}.contract.yaml`
**Werkzeug:** `space_cli intake --contract <datei> --target .`

Drei weitere bestehende Spaces sind nach ADR-0004 als schlanke Verträge
erfasst. `writes` ist in allen drei Fällen am Code abgelesen, nicht am Namen
geraten — bei rowboat und video hätte der Name in die Irre geführt.

## Ergebnis

| Space   | Capabilities | schreibend | Lücken |
|---------|--------------|-----------|--------|
| rowboat | 7            | 0         | **0 — vollständig** |
| video   | 8            | 6         | 6 |
| desktop | 1            | 1         | 1 |

Damit sind sieben Spaces gemessen: bubbles 2, coding 5, desktop 1, ideas 2,
rowboat 0, schedule 5, video 6 — **21 offene Stellen**, alle derselben Art:
eine schreibende Capability ohne unabhängigen `truth:`-Validator.

## rowboat — vollständig, weil er nichts tut

Sechs der sieben Capabilities haben dasselbe Ziel `openfang:rowboat-chat`
und laufen durch dieselbe Funktion: `RoarbootClient.chat()` →
`POST {ROWBOAT_URL}/api/v1/{projectId}/chat`. Die Antwort ist Text; er wird
zurückgegeben und per `print(json.dumps(...))` an die Electron-UI gesendet.

Die Namen täuschen: `rowboat_email_draft` legt **keinen** Entwurf ab,
`rowboat_deck` **keine** Datei, `rowboat_meeting_brief` **kein** Dokument.
Alle drei setzen nur einen Prompt ab und geben Text zurück
(`roarboot_client.py:239-260`). Die siebte, `rowboat_status`, ist ein
HEAD-Aufruf gegen `ROWBOAT_URL` (`spaces/rowboat/mcp_server.py:97`) mit
`truth:http_ok` — ein Erreichbarkeitsbeleg, kein Schreibbeleg.

**„Vollständig" heißt hier also nicht „fertig", sondern: dieser Space greift
nicht in die Welt ein.** Die Maschinerie, die es täte, hat keine Capability:

* `start_docker` / `stop_docker` / `restart_docker`
  (`spaces/rowboat/tools/docker_tools.py`) — die Registry führt dafür die
  Ereignisse `rowboat.docker.start` / `.stop`, aber keine Capability zeigt
  darauf.
* `process_voice_note` — schreibt ausdrücklich in den Wissensgraph
  („Process this voice note and update relevant knowledge"), ebenfalls ohne
  Capability.

Nebenbefund: der Agent `rowboat-chat` hat **kein** Template unter
`openfang/agents/`. Das ist Absicht — `scripts/sync_openfang_agents.py:163`
nimmt ihn zusammen mit `brain-coder` und `brain-fallback` als handgepflegt
von der Erzeugung aus.

## video — sauber gebaut, Beleg an der falschen Stelle

Alle acht teilen das Ziel `direct:spaces.video.execution_target:execute_video`,
der Verteiler trennt sie aber klar (`spaces/video/execution_target.py:221`):

* **lesend:** `video_status`, `video_team_status` (Provider-Verfügbarkeit,
  bzw. `_read_job` bei mitgegebener `job_id`).
* **schreibend:** die sechs `_MEDIA_EVENTS` — jede legt sofort einen
  Job-Beleg unter `$VIBEMIND_VIDEO_JOB_DIR` bzw. `~/.vibemind/video-jobs/`
  an und startet einen Thread, der Mediendateien erzeugt.

Bemerkenswert: **dieser Space prüft sich bereits unabhängig, und zwar
richtig.** `_run_job` zieht vor dem Lauf einen Schnappschuss der
Artefakt-Wurzeln (`_artifact_snapshot`), vergleicht danach
(`_changed_artifacts`) und prüft jeden gemeldeten Pfad mit
`resolved.is_file()`. Ein Medien-Ereignis ohne Artefakt-Nachweis wird
`status: failed` — eine echte Rückfrage ans Dateisystem, kein Selbstbericht.

Nur landet dieser Beleg im **Job-Datensatz**, nicht beim Aufrufer:
`execute_video` kehrt sofort mit `status: accepted` zurück. Ein naiver
`truth:file_exists` direkt nach dem Aufruf schlüge deshalb fehl, obwohl
nichts kaputt ist. Der richtige Validator müsste den Job-Beleg nachschlagen
— das kann `world_observer` heute nicht (8 Checks, keiner liest eine
Job-Datei). **Das ist die eigentliche Lücke von video: kein fehlender
Eintrag, sondern ein fehlender Check-Typ.**

## desktop — ein Sammeleingang ohne Tor

Genau **eine** Capability: `desktop_skill` → `openfang:skill-coordinator`.
Sie schreibt unstrittig; die Kern-Werkzeuge des Ziels stehen in
`brain/the_brain/core/tool_scope_selector.py:34`: `handoff_action` (klickt
und tippt auf dem echten Windows-Desktop), `app_launch_or_focus`,
`skill_save_and_index`. Einen Validator hat sie nicht.

Die Registry führt für den Space **28 Ereignisse** — `messaging.send`,
`openclaw.message.send`, `openclaw.fill_form`, `desktop.click`,
`desktop.type` und weitere. Keines deklariert `required_provenance` oder
`execution.kind: mcp`, und keines wird über die Capability-Ebene erreicht:
`canonical_space_event_id` kennt nur 18 fest eingetragene Namen
(`capability_targets.py:1268`), `desktop_skill` ist keiner davon und bildet
auf sich selbst ab. `resolve_registry_execution_target` gibt damit `None`
zurück, und `_mcp_authority` — die einzige Stelle, die Provenance erzwingt
(`capability_targets.py:1156`) — wird nie betreten.

Dazu: die Registry nennt als Agenten des Space `brain-desktop`, die einzige
Capability zeigt aber auf `skill-coordinator`. Für den gibt es kein Template
und er steht auch nicht auf der Schutzliste. Ob er im laufenden Daemon
existiert, ist **offen** — OpenFang lief bei dieser Messung nicht.

## Der allgemeine Befund

Die desktop-Beobachtung gilt fast überall. Gemessen über die ganze Registry:

```
Ereignisse gesamt                 : 129
davon execution.kind: mcp         :   6
davon geschlossene Provenance     :   6
```

Die sechs sind `bubble.create`, `idea.connect`, `research.summarize`,
`research.to_idea`, `rowboat.status`, `minibook.status`. **Nur diese sechs
laufen unter dem geschlossenen Provenance-Tor.** Alle anderen 123 Ereignisse
beschreiben, was ein Agent kann — sie sind kein durchgesetzter Pfad.

Das ist kein Fehler in den Verträgen, sondern das, was sie sichtbar machen
sollten. Es ist auch die Antwort auf die Frage, was beim Verdrahten zuerst
zu tun ist: nicht 21 Validatoren nachtragen, sondern entscheiden, welche
Ereignisse überhaupt unter das Tor gehören.

## Belege

* `space_cli intake` gegen alle sieben Verträge, Ausgabe oben.
* 158 Space-Tests grün (`pytest tests/test_space_*.py`).
