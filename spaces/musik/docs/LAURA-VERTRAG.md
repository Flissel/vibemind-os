# Laura-Vertrag der Videomusik

Stand 2026-11-24 (Plan 3). Diese Datei sagt, was `dj-set video` an Laura übergibt
und wie der Musik-Dienst es dort hinlegt. `dj-set` liefert nur die Datei und den
Vertrag; Laura selbst spricht allein der Musik-Dienst an (nur über ihre API).

## Was ein Lauf liefert

Ordner `runs/<datum>-video-<wunsch>-NN/`:

| Datei | Inhalt |
|---|---|
| `videomusik.wav` | das Stück, 44 100 Hz, Stereo, **genau** `round(sekunden * 44100)` Proben |
| `videomusik.mp3` | dieselbe Musik zum Anhören und Verschicken |
| `videomusik.json` | der Vertrag (unten) |
| `bericht.md` | Wahl, Begründung, Herkunft, Zuschnitt, Pegel, Befunde |
| `ziel.json`, `wahl.json`, `zuschnitt.json` | Zwischenstände, nachvollziehbar |
| `video-mit-musik.mp4` | nur mit `--unterlegen`: neue Datei, das Original bleibt unverändert |

## `videomusik.json`, Feld für Feld

Quelle der Wahrheit: `studio/djset/video/vertrag.py` (`VideomusikErgebnis`).

| Feld | Bedeutung |
|---|---|
| `version` | immer `1` |
| `datei` | Name der WAV im Lauf (`videomusik.wav`) – das ist die Datei für Laura |
| `mp3` | Name der MP3 im Lauf, oder `null` |
| `sekunden` | Ziellänge in Sekunden, ungerundet (aus Frames: `frames * fps_den / fps_num`) |
| `samples` | Probenzahl der WAV, `round(sekunden * sample_rate)` |
| `sample_rate` | 44100 |
| `frames`, `fps_num`, `fps_den` | Länge in Frames und die Bildrate als Bruch (30000/1001 für 29,97). Nur gemeinsam gesetzt, sonst alle drei `null` (Ziel kam aus `--sekunden`) |
| `song_id`, `titel` | der eine Song aus dem Bestand |
| `herkunft` | woher der Song stammt (aus dem Bestand; nur eigene Songs) |
| `start_im_song_s` | wo im Song das Stück beginnt (immer ein Taktanfang) |
| `schluss_s` | wo im **Stück** der letzte Takt beginnt; ab hier klingt es bis zur letzten Probe auf null aus |
| `takte` | ganze Takte bis zum Schluss |
| `bpm` | Tempo des Songs (nicht gestreckt) |
| `gesang` | ob mit Gesang gerendert wurde; `false` = Begleitung aus der lokalen Trennung |
| `lufs`, `true_peak` | gemessener Pegel der WAV (Vorgabe −14 LUFS, höchstens −1,0 dBTP) |
| `laura` | der fertige Clip-Body für Laura, nur wenn `frames` gesetzt ist |

## Der `laura`-Block

Er spiegelt Lauras `TimelineAudioClipBase`
(`spaces/video/laura/services/local-api/src/laura/api/models.py`) samt Grenzen, damit ein
ungültiger Clip schon beim Musik-Studio scheitert und nicht erst als HTTP 422 bei Laura.

| Feld | Wert aus der Videomusik | Lauras Grenze |
|---|---|---|
| `seq_in_frame` | 0 | ≥ 0 |
| `seq_out_frame_exclusive` | `frames` (Ende exklusiv, wie überall in Laura) | > `seq_in_frame` |
| `asset_in_frame` | 0 | ≥ 0 |
| `gain_percent` | 100 | 0–400 |
| `fade_in_frames`, `fade_out_frames` | 0 – der Ausklang steckt schon in der Datei | Summe ≤ Clipdauer |
| `mix_mode` | `mix` | `mix`, `replace_original`, `mute_original` |
| `ducking_percent` | 100 (Originalton bleibt, wie er ist) | 0–100 |
| `label` | der Songtitel | ≤ 200 Zeichen |

`asset_id` fehlt im Block mit Absicht: die vergibt Laura erst beim Import.

## So legt der Musik-Dienst die Musik in Laura

Laura läuft auf dem Mini-PC (`LAURA_API_URL`, Header `X-Laura-Token` aus `LAURA_TOKEN`),
das Musik-Studio am PC. Ein PC-Pfad nützt Laura also nichts. Das Werkzeug
`in_laura_legen(auftrag, timeline_id, gain_percent)` des Musik-Dienstes geht so vor:

1. **Prüfen:** Der Auftrag muss ein fertiges Video sein (`fertig` oder
   `fertig_mit_befund`) und `videomusik.json` mit `laura`-Block, `frames` und Bildrate
   haben (nach Sekunden bestellte Musik hat keinen Block: Fehler). Die Zeitleiste wird
   bei Laura gelesen. Passt die Länge nicht (mehr als 1 Frame Abweichung), meldet das
   Werkzeug es, statt zu schneiden. Weicht die Bildrate der Zeitleiste ab, rechnet es
   über `sekunden` neu; ein Clip ragt nie über das Ende der Zeitleiste.
2. **Datei erreichbar machen:** Der Musik-Dienst reicht die fertige `videomusik.wav`
   über einen kleinen eigenen Link-Server aus (Vorgabe-Port 8146; Umgebung
   `MUSIK_LINK_HOST`, `MUSIK_LINK_PORT`, `MUSIK_LINK_SCHLUESSEL`). Der Link ist signiert
   (HMAC-SHA256), 900 s gültig und gilt nur für `videomusik.wav` und `videomusik.mp3`
   fertiger Aufträge; es gibt keine Verzeichnisliste und keinen freien Pfad. Fehlen
   Host oder Schlüssel, ist der Link aus und `in_laura_legen` meldet das.
3. **Importieren:** `POST /projects/{project_id}/assets/import` mit
   `{"source_url": "<signierter Link>", "display_name": "<songname>.wav"}` → `asset_id`.
   Der Dienst wartet, bis das Asset bereit ist (Asset-Probe bis 300 s).
4. **Anlegen:** `POST /timelines/{timeline_id}/audio-clips` mit dem `laura`-Block plus
   `asset_id`. Der Dienst setzt `seq_out_frame_exclusive` auf die geprüfte Länge,
   `gain_percent` auf den Wunsch (auf Lauras 0–400 begrenzt), `ducking_percent` immer auf
   100 und hängt `musik:<auftrag>` an das Label. Ohne Wunsch gilt: 25, wenn unter der
   Musik Sprache liegt – der Video-Claw hat für die Zeitleiste ein Reel abgelegt
   (`reel-<timeline_id>.json`), oder sie hat schon einen Audio-Clip, dessen Label kein
   `musik:` trägt –, sonst 100.
   Wurde die Musik für eine bestimmte Zeitleiste bestellt (`laura_timeline_id`), legt der
   Dienst sie nur dort ein; eine andere Zeitleiste ist ein Fehler, noch vor jedem Laura-Aufruf.

   ```json
   {
     "asset_id": "<aus Schritt 3>",
     "seq_in_frame": 0,
     "seq_out_frame_exclusive": 900,
     "asset_in_frame": 0,
     "gain_percent": 100,
     "fade_in_frames": 0,
     "fade_out_frames": 0,
     "mix_mode": "mix",
     "ducking_percent": 100,
     "label": "... · musik:<auftrag>"
   }
   ```

   Prüfen, Importieren und Anlegen laufen als ein Schritt je (Auftrag, Zeitleiste). Ein
   zweiter Aufruf findet den Clip über die Marke `musik:<auftrag>` im Label und legt
   keinen zweiten an. Der Dienst löscht in Laura nichts und ändert keinen vorhandenen Clip.

### Rendern und Herunterladen

`laura_rendern(timeline_id, musik_auftrag)` legt einen Auftrag der Art `laura_render`
an (Zeitlimit 900 s). Mit `musik_auftrag` kommt der Render erst dran, wenn diese Musik
nicht mehr wartet oder läuft; dann legt er sie selbst ein (`in_laura_legen`, Lautstärke
wie oben) und rendert danach. Ist die Musik gescheitert oder abgebrochen oder lässt sie
sich nicht einlegen, scheitert der Render mit diesem Grund – es gibt kein Video ohne die
bestellte Musik. Der Dienst startet den Render in Laura (mit den Reel-Optionen des
Video-Claws, falls für die Zeitleiste welche abgelegt sind), wartet auf den Export und
lädt das fertige Video in den Auftragsordner (`laura-export.mp4`). Ein Export über 500 MB
wird nicht geladen; der Fehler nennt dann die Größe. Fertige Aufträge meldet der
Telegram-Bot samt Datei (bis 50 MB); ist die Datei größer, schickt er nur den Bericht
mit der Auftragsnummer, ohne Link.

## Wer wofür zuständig ist

- **Die Musik:** genaue Länge (auf die Probe), Anfang und Schluss auf Taktanfängen,
  Ausklang bis null, Lautheit und Spitzenpegel, Gesang an oder aus.
- **Laura:** wo der Clip in der Timeline liegt, Lautstärke im Mix (`gain_percent`),
  ob der Originalton bleibt (`mix_mode`), und das **Absenken unter Sprache**
  (`ducking_percent`). Die Musik kann das nicht; sie kennt die Sprache im Video nicht.
- `--unterlegen` im Musik-Studio ist nur die schnelle Probe ohne Laura (ffmpeg,
  Musik −6 dB unter dem Originalton, Limiter auf −1 dBFS). Für den echten Schnitt
  gilt der Weg über Laura. `--musik-db` und `--original-ersetzen` wirken nur auf
  `video-mit-musik.mp4` und fließen **nicht** in den `laura`-Block; dort stehen
  immer `gain_percent` 100 und `mix_mode` `mix`, und Laura entscheidet.

## Nicht umgesetzt

Mehrere Songs für lange Videos und Zeitpunkte im Video (Höhepunkt auf Sekunde X).
Das Unterlegen in Laura selbst ist umgesetzt (`in_laura_legen`, siehe oben).
