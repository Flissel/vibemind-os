# Rowboat space

This space holds the VibeMind Rowboat web app with its plugins (`rowboat/apps/rowboat`), the Rowboat desktop
app (`rowboat/apps/x` and `rowboat/apps/harbor`), and the vault schema (`schema/`). The desktop app and the schema
are pinned copies of the fork and the EHL repo; see `rowboat/UPSTREAM.md`.

## Desktop app

Start the desktop app in dev mode on the VibeMind vault (`~/.rowboat`):

```
python spaces/rowboat/scripts/start_desktop.py --install   # once: build harbor, install the app, schema venv
python spaces/rowboat/scripts/start_desktop.py             # start Rowboat; Ctrl+C stops it
```

`--workdir <folder>` changes the data folder. While Rowboat runs, the vault map is refreshed every 60 s so the
3D brain graph shows the VibeMind hierarchy. The refresh never edits a note.

Update the pinned copies from the fork and the EHL repo:

```
python spaces/rowboat/scripts/sync_desktop.py --fork <clone of Flissel/rowboat> --fork-ref vibemind --ehl <clone of Flissel/ehl-munich-2026> --ehl-ref <branch or commit>
```

Never edit `rowboat/apps/x`, `rowboat/apps/harbor` or `schema/` by hand. Change the fork or the EHL repo and sync;
see `rowboat/UPSTREAM.md`.

Notes:

- After `--install`, run `npm run deps` in `rowboat/apps/x` before renderer tests or `tsc`. The dev start does this itself.
- The web app's `npm ci` (in `rowboat/apps/rowboat`) needs `npm ci` in `rowboat/packages/openai-plugin-runtime` first.
