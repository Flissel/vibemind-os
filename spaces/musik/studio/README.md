# Musik-Studio (djset)

`djset` baut aus einem Genre-Profil und einer Bibliothek eigener Tracks ein
komplettes DJ-Set: Profil, Auswahl, Reihenfolge, Uebergaenge und Mix-Render.
Das Paket kam mit seiner Git-Historie aus dem lokalen Projekt `Musik` in den
Space (`git subtree add`, ohne Squash). Der Kommandozeilenbefehl heisst `dj-set`.

Die Umgebung baut man im Verzeichnis `spaces/musik/studio` (Python 3.11 oder
neuer, ffmpeg mit rubberband im PATH):

    python -m venv .venv
    .venv/Scripts/python -m pip install -e ".[dev]"

Die Studio-Tests laufen nur mit dieser Umgebung, denn sie brauchen librosa und
Co. pytest laeuft mit `filterwarnings = error`, die Ausgabe muss sauber sein:

    PYTHONDONTWRITEBYTECODE=1 .venv/Scripts/python -m pytest -q --basetemp C:/Users/User/AppData/Local/Temp/djset-pytest

Die uebrigen Tests des Space (`agents/`, `api/`) laufen im Wurzelverzeichnis mit
dem System-Python (`python -m pytest -p no:cacheprovider spaces/musik -q`);
`../conftest.py` blendet `studio/` dort aus. Entwurf und Plan stehen in
`docs/superpowers/specs/2026-11-23-musik-studio-design.md` und
`docs/superpowers/plans/2026-11-23-musik-studio-plan-1-erstes-set.md`
(im Wurzelverzeichnis des Repos).
