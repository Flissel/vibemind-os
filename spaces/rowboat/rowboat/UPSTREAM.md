# Pinned upstream code – do not edit by hand

`apps/x` and `apps/harbor` (Rowboat desktop app) and `../schema` (vault schema) are copies made by
`spaces/rowboat/scripts/sync_desktop.py`. Change the fork or the EHL repo, then run the script again;
it refuses to run when these copies were edited here.

To re-pin the kernel plugin catalog, change `DESKTOP_PLUGIN_CATALOG_PIN` on branch `vibemind` of
Flissel/rowboat (in `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`), then run the sync.

- Fork: https://github.com/Flissel/rowboat (branch `vibemind`)
- Fork commit: `b74e2a3f64100af1f1260672b91b5357899ee776`
- Upstream base: rowboatlabs/rowboat `72e9d8c`
- EHL repo: https://github.com/Flissel/ehl-munich-2026 (`schema/`)
- EHL commit: `44c1024c7804c696bc283bd14a9d08bd87a38230`

## Patches on top of upstream (EHL hierarchy graph, VibeMind)

- b74e2a3f fix(main): Einbettungsmodus registriert kein rowboat://-Protokoll und beendet sich nicht ohne Fenster
- 65bade5c feat(main): Einbettungsmodus --embed fuer die VibeMind-Multiverse-Ansicht
- 83ae1ef4 fix(server): Quell-Stream abbrechen, wenn der Client waehrend serveApp abbricht
- 517544b7 feat(server): app://-Inhalte ueber den Einbettungszugang (Token, Range, Streaming)
- ff95c720 fix(server): Einbettungszugang schliesst alle Verbindungen und faengt synchrone Fehler ab
- 27e4a6ab feat(server): Einbettungszugang fuer die VibeMind-Multiverse-Ansicht (WebSocket)
- eeec20a0 feat(shared): Push-Kanaele fuer die VibeMind-Einbettung einordnen
- dfb4f38a fix(vibemind): Integrationen-Listen nur bei Verbindung ok, schnelle Abfrage sofort neu planen
- f17a38bd fix(vibemind): OpenFang-Schluessel aus Kindprozess-Umgebung entfernen, detail nur wo gezeigt, gepruefte URL oeffnen
- 5ea5159e fix(vibemind): Integrationen in Typen, Palette, Tour und ausgeklappter Seitenleiste
- e312d34e feat(vibemind): Seitenleisten-Eintrag Integrationen unter Apps
- 18269618 feat(vibemind): Seite Integrationen (Zustand, Knoepfe, Rueckfragen, Freigaben-Abzeichen, Abfrage nur sichtbar)
- 32523b9b feat(vibemind): Main-Prozess bedient die Integrationen-Kanaele (OpenFang auf 127.0.0.1:4200)
- bb4e8921 fix(vibemind): werkzeuge nur als Ganzzahl >= 0 an den Renderer, Schema-Tests ergaenzt
- aaf21f3d feat(vibemind): IPC-Vertrag fuer die Integrationen-Seite (strikte Schemas, ID-Regel wie OpenFang)
- 6482d569 fix(vibemind): Integrationen-Client haertet Status-Fehler, Redirect, Meldungsklassen und add-Folgefehler
- 6ddb239b feat(vibemind): OpenFang-Integrationen-Client im Core (Positivliste, URL-Pruefung, feste Meldungen)
- ef2b538f fix(vibemind): convert only top-level video links, give Settings dialog an accessible name
- 163563e0 fix(vibemind): load several video links in one note, add VibeMind notices
- e9a400ab feat(vibemind): video links that survive saving, tolerant graph file read
- 5172524a feat(vibemind): plugin settings tab, session on the 72e9d8c IPC
- e6853c2f fix(graph): vault map keys are exact paths, not wiki links
- 0d296b50 feat(graph): place notes without hierarchy front matter via config/vault-map.json
- f2d0ce76 fix(graph): 3D view no longer jitters while orbiting, wider shells
- 2453c4dc feat(graph): 3D brain graph with one sphere shell per hierarchy level
- 8849ce83 feat(graph): radial-force hierarchy layout, rings and typed edge styles
- 5c6e922c feat(graph): typed edges and hierarchy info in the brain graph data
- 7fa41841 feat(graph): ring forces and initial ring positions for the hierarchy
- 8994c95f feat(graph): parse the vault schema's hierarchy from front matter
