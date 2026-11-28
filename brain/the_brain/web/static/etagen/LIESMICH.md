# Etagen-Ansicht der Entscheidungen

Unteransicht „Etagen (3D)" im Reiter Decisions der Brain-Seite (`/brain`).

- `etagen-logik.js` – reine Logik: Befehle aus `/api/decision_graph/query`, Bündeln, Spuren, Bahnen.
  Tests: `tests/etagen_logik.test.js` (node), angestoßen von `tests/test_etagen.py`.
- `etagen-ansicht.js` – die Seite. Lädt three.js erst beim Öffnen, gibt alles beim Schließen frei,
  zeichnet nur bei Änderung.
- `three.min.js` (r128) und `OrbitControls.js` – unverändert aus three.js, MIT-Lizenz
  (Copyright 2010-2021 Three.js Authors). Dieselben Dateien wie in `voice/electron-app/renderer/lib`.

Die Etage, die entschieden hat, und der Ausgang kommen fertig vom Gehirn
(`core/entscheidung_graph.py`: `etage_aus`, `ausgang_aus`).

Seit Baustufe B liefert das Gehirn je Satz den ganzen Weg mit Dauer je Stufe als drei gleich
lange Listen (`stufen_etage`, `stufen_station`, `stufen_ms`; `core/stufen_protokoll.py`).
Die Ansicht nimmt diesen Weg, wenn er passt. Ältere Sätze ohne Protokoll zeigt sie wie zuvor.
