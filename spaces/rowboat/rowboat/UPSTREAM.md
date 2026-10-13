# Pinned upstream code – do not edit by hand

`apps/x` and `apps/harbor` (Rowboat desktop app) and `../schema` (vault schema) are copies made by
`spaces/rowboat/scripts/sync_desktop.py`. Change the fork or the EHL repo, then run the script again;
it refuses to run when these copies were edited here.

- Fork: https://github.com/Flissel/rowboat (branch `vibemind`)
- Fork commit: `163563e0e8434ec3309efbb5d74b5d022e2304f7`
- Upstream base: rowboatlabs/rowboat `72e9d8c`
- EHL repo: https://github.com/Flissel/ehl-munich-2026 (`schema/`)
- EHL commit: `44c1024c7804c696bc283bd14a9d08bd87a38230`

## Patches on top of upstream (EHL hierarchy graph, VibeMind)

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
