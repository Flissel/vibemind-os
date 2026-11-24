# Laura-Vertrag der Videomusik

Stand 2026-11-23 (Plan 2). Diese Datei sagt, was `dj-set video` an Laura übergibt
und wie Plan 3 es dort hinlegt. Plan 2 liefert nur die Datei und den Vertrag; Laura
selbst wird in Plan 2 nicht angefasst.

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

## Wie Plan 3 die Musik in Laura bringt

Laura läuft auf dem Mini-PC (`LAURA_API_URL`), das Musik-Studio am PC. Ein PC-Pfad
nützt Laura also nichts.

1. **Datei erreichbar machen:** die WAV unter einem Link anbieten, den der Mini-PC
   abrufen kann (Musik-Dienst in Plan 3), statt eines Pfads auf dem PC.
2. **Importieren:** `POST /projects/{project_id}/assets/import` mit
   `{"source_url": "<link zur videomusik.wav>", "display_name": "<titel>"}` → `asset_id`.
   Laura holt URL-Quellen über ihren Abruf-Job (`ingest.fetch`); dass ein direkter
   WAV-Link dort sauber als Ton ankommt, prüft Plan 3 als Erstes.
3. **Anlegen:** `POST /timelines/{timeline_id}/audio-clips` mit dem `laura`-Block plus
   `asset_id`:

   ```json
   {
     "asset_id": "<aus Schritt 2>",
     "seq_in_frame": 0,
     "seq_out_frame_exclusive": 900,
     "asset_in_frame": 0,
     "gain_percent": 100,
     "fade_in_frames": 0,
     "fade_out_frames": 0,
     "mix_mode": "mix",
     "ducking_percent": 100,
     "label": "..."
   }
   ```

   Die Frames zählen in der Bildrate der Sequence; `fps_num`/`fps_den` im Vertrag
   sagen, in welcher Rate die Musik gemessen wurde. Weicht die Sequence ab, rechnet
   Plan 3 über `sekunden` neu, statt die Frames blind zu übernehmen.

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

## Nicht in Plan 2

Mehrere Songs für lange Videos, Zeitpunkte im Video (Höhepunkt auf Sekunde X) und
automatisches Unterlegen in Laura. Das automatische Unterlegen kommt in Plan 3.
