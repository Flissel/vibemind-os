# Video-Werkbank: HyperFrames-Einrichtung (Stand 11.11.2026)

HyperFrames rendert HTML zu MP4 (Chrome-Frames + FFmpeg). Aufruf nur ueber `workers/video_rendern.py`.

## Befunde der Probe (Task 1)

| Frage | Befund |
| --- | --- |
| Version | `hyperframes` **0.9.33**, exakt gepinnt in `package.json` (Lizenz Apache-2.0). Node v24.4.0, FFmpeg 8.1 (Systeminstallation). |
| Telemetrie | `npx hyperframes telemetry disable` ist ausgefuehrt (persistiert in der Benutzerkonfiguration dieses PCs, nicht im Repo). Zusaetzlich setzt `video_rendern.rendern()` fuer den Kindprozess `DO_NOT_TRACK=1`. Auf einem anderen Rechner beides wiederholen. |
| Ton im MP4 unter Windows | **Ja.** `<audio src data-start data-duration>` wird direkt als AAC-Spur ins MP4 geschrieben (Probe: `mean_volume` -21.1 dB bei 440-Hz-Sinus, nicht stumm). Der Rueckfall `rendern(..., ton=wav)` funktioniert ebenfalls (getestet, 15.6 s), ist aber nicht noetig. |
| GSAP noetig? | **Nein.** Reine CSS-`@keyframes` (Klasse `rein` in `probe/index.html`) werden gefunden und frameweise gesucht; Frame bei 0,1 s zeigt den Text halb eingeblendet. Es gibt in der Probe keinen Timeline-/GSAP-Code und kein CDN. HyperFrames-Dokumentation (`references/runtimes.md`): CSS-Keyframes sind fuer einfache Schleifen/Dekoration gedacht (endliche Wiederholungen, `animation-fill-mode: both`); komplexe Choreografie waere GSAP. `lib/` bleibt daher leer. |
| Renderdauer 10 s Full-HD, 30 fps | CLI-Meldung 13.6 s (Setup 4.3 s, Capture 7.4 s mit 6 Workern, Encode 0.2 s, Assemble 0.4 s); Pytest-Probe inkl. Prozessstart 15 s; Rueckfall mit ffmpeg-Nachlauf 15.6 s. Rechner: i9-14900, 32 Kerne, 32 GB. Faustregel etwa 1,4x Echtzeit. |
| Chrome-Download | Keiner noetig: `chrome-headless-shell` 152.0.7977.75 lag schon im gemeinsamen Puppeteer-Cache `C:\Users\User\.cache\puppeteer\` (seit 08.09.2026, der ganze Cache ca. 0,7 GB). Neue Rechner laden beim ersten Render selbst; `npx hyperframes doctor` zeigt den Pfad. |
| Groesse | `node_modules/` ca. 105 MB (nicht eingecheckt, `.gitignore`). |

## Abweichungen vom Plan

- Die CLI-Flags `--composition`, `--output`, `--fps` sind echt (`render --help`, v0.9.33); kein Unterschied zum Plan.
  Zusatz: `-c` darf eine einzelne HTML-Datei sein, ist aber relativ zum Projektordner. `video_rendern` uebergibt absolute Pfade und startet mit `cwd=video_werkbank`.
- Das Wurzelelement der Probe traegt zusaetzlich `data-fps="30"`. Kein Timeline-Skript noetig, solange nur CSS-Animation und zeitgesteuerte `.clip`-Elemente genutzt werden.
- `rendern(..., ton=...)` rendert erst nach `<ziel>.roh.mp4` und setzt dann die Tonspur per ffmpeg; die Rohdatei wird danach geloescht.
- `npm audit` meldet 2 Schwachstellen (undici, transitiv ueber hyperframes; moderate/high). Nicht angefasst; der Renderer laedt nur lokale Dateien.

## Kompositionsschema (fuer spaetere Tasks)

Wurzel: `data-composition-id`, `data-start="0"`, `data-duration`, `data-width="1920"`, `data-height="1080"`, `data-fps="30"`.
Kinder: `id`, `class="clip"`, `data-start`, `data-duration`, `data-track-index`. Ton: `<audio id src data-start data-duration data-track-index data-volume="1">`,
`src` relativ zur HTML-Datei. Schriften: Inter, Montserrat, Poppins u. a. sind gebuendelt (Namen direkt nutzbar).

## Probe nachspielen

```
cd spaces/marketing/video_werkbank && npm ci
ffmpeg -f lavfi -i sine=frequency=440:duration=10 -ar 48000 probe/ton.wav   # der Test legt sie selbst an
python -m pytest spaces/marketing/workers/tests/test_video_rendern.py -q
```
