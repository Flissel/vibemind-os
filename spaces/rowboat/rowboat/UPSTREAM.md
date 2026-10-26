# Pinned upstream code – do not edit by hand

`apps/x` and `apps/harbor` (Rowboat desktop app) and `../schema` (vault schema) are copies made by
`spaces/rowboat/scripts/sync_desktop.py`. Change the fork or the EHL repo, then run the script again;
it refuses to run when these copies were edited here.

To re-pin the kernel plugin catalog, change `DESKTOP_PLUGIN_CATALOG_PIN` on branch `vibemind` of
Flissel/rowboat (in `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`), then run the sync.

- Fork: https://github.com/Flissel/rowboat (branch `vibemind`)
- Fork commit: `143f0dc22e7cbbae1393f74002ddb630648f47e2`
- Upstream base: rowboatlabs/rowboat `72e9d8c`
- EHL repo: https://github.com/Flissel/ehl-munich-2026 (`schema/`)
- EHL commit: `44c1024c7804c696bc283bd14a9d08bd87a38230`

## Patches on top of upstream (EHL hierarchy graph, VibeMind)

- 143f0dc2 feat(copilot): Wissensfragen zuerst wissen-suchen, Zentrale-Werkzeuge per Skill (VibeMind)
- 244f0c84 fix(channels): todo-propose nicht fuer Kanal-Laeufe (Teil E, Runde 5)
- d8761438 fix(channels): Quarantaene ohne uebersprungene Zeichenketten (Teil E, Runde 4)
- 47540379 fix(channels): Quarantaene prueft jedes Zeichenketten-Argument (Teil E, Runde 4)
- 7bf3414c fix(channels): Hintergrund-Scanner ueberspringen knowledge/Kanal/ (Teil E, Runde 4)
- dfa5c512 fix(channels): Kanal-Quarantaene fuer Kanal-Laeufe (Teil E, Runde 4)
- e8d08f73 fix(channels): Sandbox fuer Hintergrundlaeufe (Teil E, Runde 3)
- 4220ba7d fix(channels): Task-Fences auch mit ~~~, 4+ Backticks und language-task erkennen (Teil E, Runde 3)
- 43317da6 fix(channels): keine Autorun-Notizen aus Kanal- und Hintergrundlaeufen (Teil E, Runde 2)
- 277b2613 fix(channels): fetch-url im Kanal mit Zeitlimit und begrenztem Lesen (Teil E, Runde 2)
- 56b3bc1f fix(channels): Waechter gegen Klammer-Globs, Live-Notizen, Agent Notes (Teil E, Runde 1)
- d698dc9f fix(channels): fetch-url in Kanal-Laeufen ohne interne Ziele (Teil E, Runde 1)
- 3b48f3e9 fix(channels): Kanal-Laeufe ohne Aussenwirkung (Teil E)
- 71e9256e fix(channels): Laufzeit setzt Kanal-Lauf-Waechter durch (Teil E)
- ddc59079 fix(channels): Kanal-Lauf-Waechter und Turn-Flag (Teil E)
- 13e01004 merge: origin/vibemind (3D-Graph-Fixes, open-url) nach Upstream-Merge
- 4482ca5a merge: upstream/main (48 Commits bis 31931b32) in vibemind
- 31931b32 x: Brain graph no longer freezes the app or collapses into one pile (#1225)
- 2f751572 feat: add Attio CRM integration to Spaces (#1184)
- 24de6be1 Floating collapsed sidebar, one Assistant header bar (#1221)
- 6307e52d x: Threads view lists every thread, with All / Named / By me (#1199)
- c11e8845 x: Spaces — a "Show sidebar" button replaces the hidden edge sliver (#1216)
- f7d1cad6 Sidebar: smoother collapse, floating panel, keyboard shortcuts (#1215)
- f9db4c91 Main sidebar redesign: one row height, Spaces under Assistant, account footer, smoother collapse (#1214)
- 2fc21e50 Spaces redesign: calmer header, collapsible sidebar, quieter composer, motion (#1213)
- 4d438407 Separate space members into people and integrations (#1211)
- 02c8e18a Agents page: every agent in the org, and an owner renames theirs (#1212)
- d791b542 Merge pull request #1210 from rowboatlabs/ramnique/blob-unicode-filenames
- 71b134be harbor: serve blobs whose name is beyond ASCII (macOS screenshots)
- f1bb6a11 fix(graph): Kinder starten bei ihrem Elternknoten, erstes Layout vorab
- 7a4c04c9 Admin controls in Spaces: remove from a space or the org, roles, visibility, invite policy (#1209)
- c6187eeb fix(graph): Kamera aus Startpositionen, WebGL-Kontext freigeben
- 08b2be3f Team invites: invite to the org with an optional list of spaces, and default spaces (#1208)
- 1aaafd39 fix(graph): 3D-Graph nach Notiz und Zurueck wieder sichtbar, ruhiger Start
- 36ec9eac Profiles: edit your own name and title (PUT /v1/me) (#1207)
- 04c4d968 x: archive, unarchive and delete a space from the desktop app (#1194)
- 9d4f5340 merge: origin/vibemind (Integrationen-Wartezeit) in feat/embed-bridge
- bc350d8c fix(vibemind): Integrationen-Aktionen warten bis 60 s auf OpenFang (Status bleibt 10 s)
- 7cd4d7da feat(server): Navigation (open-url) ueber den Einbettungszugang
- b74e2a3f fix(main): Einbettungsmodus registriert kein rowboat://-Protokoll und beendet sich nicht ohne Fenster
- 65bade5c feat(main): Einbettungsmodus --embed fuer die VibeMind-Multiverse-Ansicht
- 83ae1ef4 fix(server): Quell-Stream abbrechen, wenn der Client waehrend serveApp abbricht
- 517544b7 feat(server): app://-Inhalte ueber den Einbettungszugang (Token, Range, Streaming)
- ff95c720 fix(server): Einbettungszugang schliesst alle Verbindungen und faengt synchrone Fehler ab
- 27e4a6ab feat(server): Einbettungszugang fuer die VibeMind-Multiverse-Ansicht (WebSocket)
- eeec20a0 feat(shared): Push-Kanaele fuer die VibeMind-Einbettung einordnen
- 0176747f Mobile: three confirmations before deleting an account (#1205)
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
- eb312295 Merge pull request #1198 from rowboatlabs/ramnique/web-join-polish
- a1faf7e0 Web: Join finishes after sign-in, in the browser's own words
- 33f88286 Merge pull request #1197 from rowboatlabs/ramnique/toasts-visible
- 911cde75 x: toasts from @/lib/toast are drawn again
- 5afd4a5b Merge pull request #1196 from rowboatlabs/ramnique/spaces-web-notifications
- 0c643395 Web: notifications, and the unread count in the tab
- 81d6f3e2 harbor: delete a space (cascade foreign keys + the delete route) (#1189)
- 6005d699 feat(analytics): add telemetry opt-out for PostHog (#415) (#1192)
- ef2b538f fix(vibemind): convert only top-level video links, give Settings dialog an accessible name
- 163563e0 fix(vibemind): load several video links in one note, add VibeMind notices
- e9a400ab feat(vibemind): video links that survive saving, tolerant graph file read
- 5172524a feat(vibemind): plugin settings tab, session on the 72e9d8c IPC
- e6853c2f fix(graph): vault map keys are exact paths, not wiki links
- 0d296b50 feat(graph): place notes without hierarchy front matter via config/vault-map.json
- f8933f98 harbor: archive and unarchive a shared space (#1186)
- 5a003123 Account deletion: leave every org, keep the name (#1188)
- f94f6726 Merge pull request #1187 from rowboatlabs/ramnique/spaces-web-account
- cce21598 Web: an account menu, Sign out, and a sign-in page
- 8ca1d049 Merge pull request #1185 from rowboatlabs/ramnique/spaces-web-links
- 703baa41 Web: shared links and invites open in the browser
- 1b7797bc Harbor: a link opened in a browser offers the web app
- f2d0ce76 fix(graph): 3D view no longer jitters while orbiting, wider shells
- 2453c4dc feat(graph): 3D brain graph with one sphere shell per hierarchy level
- 0d802cbe x: hide sidebar account popovers while the sidebar is collapsed (#1166)
- 08fa05a7 Improve Outlook rate-limit handling with resilient cooldowns (#1156)
- 8849ce83 feat(graph): radial-force hierarchy layout, rings and typed edge styles
- 5c6e922c feat(graph): typed edges and hierarchy info in the brain graph data
- 7fa41841 feat(graph): ring forces and initial ring positions for the hierarchy
- 8994c95f feat(graph): parse the vault schema's hierarchy from front matter
- 68d8d1c8 Merge pull request #1183 from rowboatlabs/ramnique/spaces-web-files
- 7c458127 Web: calls, images and files on Harbor's session cookie
- c0c6d060 Harbor: the web app's session cookie, on every face
- 8fae505a Merge pull request #1182 from rowboatlabs/ramnique/spaces-web-theme
- de7a75d6 Web: load the app's theme, which the desktop gets through App.tsx
- c3fb515c Merge pull request #1181 from rowboatlabs/ramnique/spaces-web-vercel-scripts
- 7eb315da Web: Vercel's install and build as scripts, under its 256-character cap
- 7e725f36 Merge pull request #1180 from rowboatlabs/ramnique/spaces-web-vercel-build
- df6cb6f0 Web: a deploy build Vercel can run from a fresh checkout
- 96332297 Merge pull request #1178 from rowboatlabs/ramnique/spaces-web-app
- cacfa8d1 Merge pull request #1176 from rowboatlabs/ramnique/spaces-web-shell
- 511bd96b Spaces: the web app, the renderer's Spaces section served from a domain
- 7f04ea3a Harbor: CORS for the browser origins the deployment lists
- d6ae8a33 Spaces: one table of the channels that are a single Harbor call
