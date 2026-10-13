# Rowboat space

This space holds the VibeMind Rowboat web app with its plugins (`rowboat/apps/rowboat`), the Rowboat desktop
app (`rowboat/apps/x` and `rowboat/apps/harbor`), and the vault schema (`schema/`). The desktop app and the schema
are pinned copies of the fork and the EHL repo; see `rowboat/UPSTREAM.md`.

All commands below run from the vibemind-os repo root.

## Desktop app

Start the desktop app in dev mode on the VibeMind vault (`~/.rowboat`):

```
python spaces/rowboat/scripts/start_desktop.py --install   # first time and after every sync: build harbor, install the app, schema venv
python spaces/rowboat/scripts/start_desktop.py             # start Rowboat; Ctrl+C stops it
```

`--workdir <folder>` changes the data folder. The dev start sets `ROWBOAT_WORKDIR` to it, so Electron keeps its
profile in `<workdir>/.electron-data`. While Rowboat runs, the vault map is refreshed every 60 s so the 3D brain
graph shows the VibeMind hierarchy. The refresh never edits an existing note (it writes structure notes and
`config/vault-map.json`).

Update the pinned copies from the fork and the EHL repo:

```
python spaces/rowboat/scripts/sync_desktop.py --fork <clone of Flissel/rowboat> --fork-ref vibemind --ehl <clone of Flissel/ehl-munich-2026> --ehl-ref <branch or commit>
```

The sync keeps `node_modules` and the harbor `dist`, and `npm run dev` neither rebuilds harbor nor re-runs
`pnpm install`. Run `start_desktop.py --install` after every sync so the dependencies match the new copy.

Never edit `rowboat/apps/x`, `rowboat/apps/harbor` or `schema/` by hand. Change the fork or the EHL repo and sync;
see `rowboat/UPSTREAM.md`.

## Catalog re-pin

The web app's kernel plugin catalog pin is mirrored in the desktop renderer
(`rowboat/apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts`, `DESKTOP_PLUGIN_CATALOG_PIN`), and
`rowboat/apps/rowboat/test/plugins/catalog-pin-guard.test.ts` fails when the two differ. Do not edit that file here
(the next sync would stop on it as an edit by hand). Change `DESKTOP_PLUGIN_CATALOG_PIN` on the fork branch
`vibemind` (Flissel/rowboat), then run the sync.

Notes:

- After `--install`, run `npm run deps` in `rowboat/apps/x` before renderer tests or `tsc`. The dev start does this itself.
- The web app's `npm ci` (in `rowboat/apps/rowboat`) needs `npm ci` in `rowboat/packages/openai-plugin-runtime` first.
