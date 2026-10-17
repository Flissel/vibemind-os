# Video-Claw – Reels mit Laura – Implementierungsplan

> **Fuer agentische Umsetzer:** Schritte als Checkboxen (`- [ ]`). TDD je Task, enger Scope, Conventional Commits auf Deutsch.

**Ziel:** Kurze Hochformat-Videos (Reels, 9:16, 15–60 s) entstehen auf Auftrag – aus vorhandenem Laura-Material **oder** aus Text plus Markenbildern –, mit deutscher Stimme, mitlaufenden Untertiteln und Hinweis „Stimme: KI“, wenn KI im Spiel ist. Das fertige `.mp4` liegt im Medienordner der Firma und geht – wie Podcast und Newsletter – **nur** ueber Entwurf und menschliche Freigabe in sales-claw hinaus (LinkedIn-Video, WhatsApp).

**User-Entscheide (17.10.2026):**
- E1 Beide Stufen in einem Zug: Reel aus Material **und** Reel aus Text + Bildern.
- E2 Eigener Claw (Video-Claw), bedient ueber Skills. Der Marketing-Agent bleibt bei Laura **lesend** und beauftragt nur.
- E3 KI-Hinweis nur bei KI-Stimme bzw. KI-Gesicht („Stimme: KI“); reine Echtaufnahmen ohne Hinweis.

---

## Befunde, auf denen der Plan steht (gemessen 17.10.2026)

1. **Laura kann fast alles fuer ein Reel aus Material** (`spaces/video/laura/services/local-api/src/laura/`):
   `POST /projects/{pid}/narrated-reel` (Beats `{text, asset_id, src_in_frame}` → Timeline mit Stimme), `POST /timelines/{tid}/render-reel` (1080×1920 Mittel-Crop, Karaoke-Untertitel, `hook_text`, loudnorm). Jobs: `GET /jobs/{id}`, Exporte `GET /exports/{id}` (`ready` kommt **vor** `object_key`), `GET /exports/{id}/download-url`.
   - Falle: `narrated-reel` mit `render=true` rendert **quer** (`ai/handlers.py:953`) → immer `render=false` + `render-reel`.
   - `disclosure_text` ist Pflicht und wird immer eingebrannt (Default „KI · synthetisch“) → widerspricht E3.
   - Keine Bitraten-/Groessengrenze; sales-claw nimmt hoechstens **15 MB** (`sales-mcp/medien.py:60`).
2. **Fuer Text + Bilder fehlt in Laura alles:** Probe kennt nur `video|audio` (`ingest/probe.py:87`), keine Standbilder mit Dauer, keine Titelkarten, kein Logo-Overlay, `generate-video` ist ein grauer Stub. Zoom-Code liegt ungenutzt in `render/zoom.py`.
3. **Stimme:** Lauras Sidecar-Backend ruft `POST {LAURA_VOICEOVER_URL}/voiceover` (Vertrag: `text, duration_frames, fps_num, fps_den, sample_rate, language, voice_id, fit_to_slot` → WAV) und `GET /healthz`. Der live laufende Marketing-Sprechdienst (:8899, ChatterboxMultilingual, Deutsch, Stimmen aus `E:\marketing-stimmen`) spricht `POST /sprechen` – anderer Vertrag. Lauras eigener Chatterbox-Sidecar (:8898) ist die englische v1 und laeuft nur mit Docker Desktop (derzeit aus).
4. **Der Werkzeug-Agent laeuft nicht ueber openclaw:** Die Shim gibt nie `tool_calls` zurueck (Memory `shim-tool-bridge`); openclaw-eigene Werkzeuge (Skill-Datei lesen, `sessions_spawn`) kommen durch die Shim nicht an. Werkzeuge muessen **in** die Claude-CLI (`SHIM_EXTRA_MCP_CONFIG`). Die sales-claw-Chats laufen ueber Arbeiter mit `marketing_ohne_werkzeuge`. → Der Video-Claw folgt dem **Podcast-Muster** (DB-Auftrag → PC-Arbeiter), nicht dem openclaw-Gateway.
5. **Versand existiert schon:** `versand_beauftragen(kanal=linkedin_post|whatsapp, medien_datei=…)` → sales-claw-Entwurf → Freigabe → `linkedin_dispatch.py` laedt **ein** `.mp4` als Video hoch; WhatsApp `send-video`. Keine Aenderung am Versandweg noetig.
6. Laura-API laeuft auf der VM (`deploy-api-1`, CPU/ffmpeg), GPU-Dienste am PC; VM erreicht den PC ueber Tailscale `100.84.127.84` (so schon `LAURA_ANALYSIS_URL`).

## Architektur

```
Betreiber (sales-claw /marketing/reels)  ─┐
Marketing-Agent: reel_beauftragen(…)     ─┴─▶ Marketing-API POST /api/video/auftraege
                                                 └─ marketing.video_auftraege (Migration 072, nur DB-Funktionen)
PC: Video-Claw-Arbeiter (:8136) ── holt Auftrag (Lease) ──▶ Claude-CLI ueber eigene Shim (:8118)
      System-Prompt = AGENTS.md + Skill-Index; Read nur auf skills/
      MCP: video-Werkzeuge (:8137) = enge Laura-Huellen (schreibend) + Material lesen
           └─▶ Laura-API (VM) ─ Stimme ─▶ Marketing-Sprechdienst :8899 /voiceover (Tailscale)
      Ergebnis-.mp4 ─▶ POST /api/video/arbeiter/{id}/datei ─▶ Medienordner: vid-<id8>-<slug>.mp4
Marketing-Agent / Betreiber ─ versand_beauftragen(linkedin_post|whatsapp, medien_datei=vid-…mp4) ─▶ sales-claw-Freigabe
```

Grundsaetze: Der Video-Claw **versendet nie** und legt keine Marketing-Entwuerfe an; er liefert eine Datei. Er hat **kein** Generalwerkzeug `laura_api` – nur benannte Huellen mit gepruefter Eingabe. Ein Auftrag = ein Reel; Abbruch/Fehler landet mit Grund im Auftrag.

## Globale Vorgaben

- vibemind-os: Worktree `C:\Users\User\ClaudeWork\wt-marketing-hand`, je Phase ein Branch `claude/video-claw-<phase>` von `origin/master`; auf master erst nach User-OK (Migration vorher).
- Laura: eigenes Repo (`spaces/video/laura`, Remotes `origin`=Vibemind-LAB, `flissel`); neuer Worktree auf `C:` (nicht `E:`), Branch `claude/reel-bausteine`; Push an **beide** Remotes (Memory `laura-suite`); Suite in Abschnitten im Vordergrund, nie `schtasks`; Schemaversionen nie festnageln.
- sales-claw: Worktree `C:\Users\User\ClaudeWork\wt-sales-baukasten`, Tests nur im VM-Container (`/tmp/sc-nl-test`).
- Neue `marketing.*`-Funktionen: `REVOKE ALL … FROM PUBLIC`, Schreiben nur ueber Funktionen (API liest per `SELECT … FROM (<sql>)`).
- Schluessel nie ausgeben; Agent-Routen per `X-Agent-Key`, Arbeiter per `X-Bild-Key` (wie Ton).
- Nie mehrere Claude-CLI-Laeufe parallel (Host-RAM); der Arbeiter nimmt immer nur **einen** Auftrag.
- ComfyUI nie beenden; Dienste per PID am Port stoppen.

---

## Phase A – Laura lernt Reel-Bausteine (Laura-Repo)

- [ ] **A1 KI-Hinweis nach E3.** `render-reel`: `disclosure_text` optional (`null`/leer = kein Hinweis); neues Feld `kennzeichnung: "keine"|"stimme"|"gesicht"|"synthetisch"` → Text „Stimme: KI“ / „Gesicht: KI“ / „KI · synthetisch“. Alte Aufrufer ohne Feld behalten das bisherige Verhalten.
  Tests: ohne Feld wie bisher; `keine` → kein drawtext; `stimme` → „Stimme: KI“; Text wird escaped (`:`/`'` in drawtext).
- [ ] **A2 Groessengrenze.** `render-reel` bekommt `max_bytes` (Vorgabe aus `LAURA_REEL_MAX_BYTES`, sonst keine). Zielbitrate aus Dauer berechnen (`max_bytes*8*0.92/dauer − Audio 128k`), H.264 `-maxrate/-bufsize`, nach dem Render pruefen; zu gross → ein zweiter Durchlauf mit 80 % Bitrate, dann Fehler mit Grund.
  Tests: Bitratenrechnung (Grenzfaelle 15 s/60 s), zweiter Durchlauf wird ausgeloest, Fehlertext.
- [ ] **A3 narrated-reel hochkant.** Feld `ausgabe: "quer"|"reel"`; bei `reel` ruft der Job nach dem Timeline-Bau `render-reel` mit den Reel-Feldern (Untertitel aus der Stimme, `caption_source=voiceover`) statt `/render`. Behebt Falle 1.1 an der Wurzel.
- [ ] **A4 Standbilder.** Import erkennt Bilder (`png|jpg|jpeg|webp`) als Typ `image`; `POST /projects/{pid}/stills` `{asset_id, dauer_s 1–10, bewegung: "ruhig"|"zoom_rein"|"zoom_raus"|"schwenk"}` rendert per ffmpeg (`zoompan`, 30 fps, 1080×1920, Bild per Blur-Fill eingepasst – nicht beschnitten) einen Clip und legt ihn als Video-Asset an. Nutzt `render/zoom.py`, wo moeglich.
  Tests: Bildimport (Typ, Abmessungen), Dauer = Frames, Hochformat bei Querbild ohne Beschnitt (Blur-Fill), ungueltige Werte → 422.
- [ ] **A5 Titelkarten.** `POST /projects/{pid}/titelkarte` `{text ≤ 90 Zeichen, unterzeile?, hintergrund "#rrggbb", textfarbe, schrift (Datei aus fester Liste), logo_asset_id?, dauer_s}` → Video-Asset (Text mittig, sichere Raender 8 %, Zeilenumbruch, leichte Einblendung).
  Tests: Umbruch langer Texte, Farbe/Schrift validiert, Logo eingepasst.
- [ ] **A6 Logo-Einblendung.** `render-reel` optional `logo_asset_id`, `logo_position` (`oben_rechts` Vorgabe), `logo_breite_prozent` (8–25). Kein Logo = keine Aenderung.
- [ ] **A7 Upload statt Pfad.** Bilder/Logos kommen aus dem Marketing-Medienordner: Import per `source_url` von einer kurzlebigen Marketing-Download-URL (kein neuer Laura-Upload-Endpunkt). Test: URL-Import eines PNG wird `image`.
- [ ] **A8 Laura-Suite** in Abschnitten gruen; OpenAPI-Beschreibungen der neuen Felder; Deploy auf der VM (Image `docker save | ssh … docker load`, `compose up --no-deps --no-build -d api`) **nach User-OK**.

## Phase B – Deutsche Stimme fuer Laura

- [ ] **B1 `/voiceover` + `/healthz` im Marketing-Sprechdienst** (`spaces/marketing/claw/ton_dienst.py`): nimmt Lauras Vertrag an, spricht mit dem schon geladenen Multilingual-Modell (`language` → `sprache`, `voice_id` → Stimmprobe aus `E:\marketing-stimmen`), resampelt auf `sample_rate`, bei `fit_to_slot` per ffmpeg `atempo` (0,8–1,25) auf `duration_frames` – sonst Stille anfuegen/kuerzen wie Lauras Sidecar. Ein Modell, ein Sprech-Lock fuer beide Routen.
  Tests: Vertragspruefung (Pflichtfelder, positive Ganzzahlen), Dauerberechnung, `fit_to_slot`-Faktor, unbekannte Stimme → 4xx.
- [ ] **B2 Erreichbarkeit:** Dienst zusaetzlich auf der Tailscale-Adresse; Windows-Firewall-Regel **nur** fuer `100.67.177.45` (VM) auf Port 8899. Laura-VM: `LAURA_VOICEOVER_URL=http://100.84.127.84:8899`, `LAURA_VOICEOVER_BACKEND=sidecar` in `deploy/.env`. *(Firewall-Regel = Aenderung am Host → User-OK.)*
- [ ] **B3 Probe ueber denselben Weg:** von der VM `POST /timelines/{tid}/voiceover` mit deutschem Text → Export hoeren.

## Phase C – Video-Claw (vibemind-os, `spaces/marketing/video_claw/`)

- [ ] **C1 Migration 072 `marketing.video_auftraege`:** `id, mandant, art ('material'|'bilder'), briefing jsonb, status ('offen','in_arbeit','fertig','fehler','abgebrochen'), lease_bis, versuche, datei (^vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.mp4$), dauer_s, bytes, laura_export_id, fehler, protokoll jsonb, angelegt_von ('agent'|'betreiber'), erstellt_am, geaendert_am`. Funktionen `video_auftrag_anlegen`, `video_naechster(lease)`, `video_verlaengern`, `video_abschliessen`, `video_abbrechen`; REVOKE FROM PUBLIC; `verify_072.sql` (Lease, Doppelabholung, Statuswechsel, Rechte).
- [ ] **C2 API `api/video.py`:** Agent `POST /api/video/auftraege` (Briefing pruefen: Art, 15–60 s, Sprechertext ≤ 900 Zeichen, Material-IDs bzw. Medien-Namen der Firma, Stimme, Kennzeichnung), `GET /api/video/auftraege`; Pult `GET/POST /api/pult/video…` (Liste, anlegen als betreiber, abbrechen); Arbeiter `naechster`, `verlaengern`, `datei` (MP4-Magie `ftyp`, ≤ 15 MB, Name aus Auftrag, `medien_mandant.zuordnen`), `fertig`, `fehler`; Download-URL fuer Medien (fuer A7).
- [ ] **C3 Video-Werkzeuge (MCP, :8137, FastMCP):** `material_suchen`, `transkript`, `szenen` (lesen, wie heute), `projekt_anlegen`, `bild_importieren(medien_name)`, `standbild(asset, dauer, bewegung)`, `titelkarte(…)`, `reel_bauen(beats, stimme, kennzeichnung, hook, logo)`, `auftrag_status`, `auftrag_protokoll(notiz)`. Jede Huelle prueft Eingaben selbst, kennt nur ihre Laura-Route, gibt kompakte Ergebnisse zurueck; Laura-Token nur im Host-Prozess. **Kein** `laura_api`.
- [ ] **C4 Skills** (`video_claw/skills/<name>/SKILL.md`) + `AGENTS.md`:
  - `reel-aus-material` – Material finden (Transkript/Szenen), EIN Gedanke, Einstieg in den ersten 2 s, 3–6 Beats, Sprechertext kuerzer als das Bild, Untertitel an, Kennzeichnung „stimme“.
  - `reel-aus-bildern` – Titelkarte → 3–5 Standbilder mit wechselnder Bewegung → Abschlusskarte mit Handlung; Markenfarben/-schrift aus dem Markenprofil der Firma; Logo.
  - `kurzfassung` – langes Video → bester Abschnitt (Laura `shorts-candidates`), Hook-Text.
  Gemeinsame Regeln (AGENTS.md): nichts erfinden, Produktaussagen nur mit Beleg (wie Marketing), nie versenden, ≤ 15 MB, bei Zweifel Auftrag mit Grund als Fehler abschliessen statt raten.
- [ ] **C5 Arbeiter `workers/video_claw.py` (:8136):** holt einen Auftrag, baut den Prompt (AGENTS.md + Skill-Index + Briefing + Markenprofil), ruft die eigene Shim (:8118, `SHIM_EXTRA_MCP_CONFIG=video-mcp.json`, Read nur auf `skills/`), verlaengert die Lease, laedt am Ende das Export-MP4 von Laura (auf `object_key` warten, sonst `path`/Download-URL), prueft Groesse/Magie, liefert ab. Zeitlimit je Auftrag 25 min; Budget-Waechter wie die anderen Agenten.
  Tests: Prompt-Bau, Lease-Verlaengerung, Ergebnis-Download-Fallback, Fehlerpfad (Laura 5xx, zu gross, Zeitlimit), nur ein Auftrag gleichzeitig.
- [ ] **C6 Startskript:** Eintraege `marketing_video_werkzeuge` (8137), `marketing_video_shim` (8118), `marketing_video_claw` (8136) in `marketing-dienste-starten.ps1`.

## Phase D – Bedienung

- [ ] **D1 Marketing-Agent:** Werkzeuge `reel_beauftragen(art, briefing…)` und `reel_auftraege()` im Sidecar (Agent-Schluessel auch an `/api/video/`), Freigabeliste der Shim; Hausstil fuer Sprechertext.
- [ ] **D2 sales-claw `/marketing/reels`:** Liste der Auftraege je Firma mit Status, Vorschau (`<video>` aus dem Medienordner, ohne JS), Formular „Neues Reel“ (Art, Thema, Sprechertext, Material bzw. Bilder aus den Medien, Stimme, Kennzeichnung), Abbrechen; Menueeintrag „Reels“. Danach Knopf „Als LinkedIn-Post vorbereiten“ → bestehender Entwurfsweg (`post_entwurf_erstellen` mit `medien_datei`).
- [ ] **D3 Freigabe zeigt das Video:** pruefen, ob ein sales-claw-Entwurf mit `.mp4` in der Freigabe abspielbar ist; sonst Vorschau ergaenzen (CSP `media-src 'self'`).
- [ ] **D4 Kampagnenplan:** Plan-Eintrag Art `video` kann auf einen Video-Auftrag zeigen (Feld `inhalt` bleibt Entwurf; neues optionales Feld spaeter, nicht in diesem Plan).

## Phase E – Livegang (nur nach User-OK, Reihenfolge)

1. Laura-Deploy (A) auf der VM, Probe `render-reel` mit `kennzeichnung=keine`.
2. B2 Firewall + Laura-Env, Probe deutsche Stimme von der VM.
3. Migration 072 (Probe → anwenden → verify).
4. vibemind-os master, VM `update.sh`, PC-Dienste neu.
5. sales-claw `feat/stufe-1-fundament`, `update.sh`.
6. **Ende-zu-Ende-Probe je Art:** ein Reel aus Material, ein Reel aus Bildern, beide < 15 MB, Hinweis korrekt, als LinkedIn-Entwurf vorbereitet (nicht freigegeben).

## Risiken

- **Host-RAM:** zweite Shim + Claude-CLI-Lauf + Chatterbox auf einer GPU; Arbeiter strikt seriell, Video-Claw pausiert, wenn der Podcast-Arbeiter spricht (gemeinsamer Sprech-Lock im Dienst).
- **VM-Platte** (Laura-Renders, 18 GB frei): Exporte nach Abholung im Auftrag vermerken; Aufraeumen der Laura-Exporte als eigene Folgeaufgabe.
- **Mittel-Crop** schneidet Personen am Rand ab (kein Face-Tracking) – Skill weist an, Material mit zentrierter Person zu waehlen; Face-Tracking (`analysis/crop_controller.py`) ist Folgeaufgabe.
- **Tailscale-Eingang am PC** fuer 8899: eng per Firewall auf die VM-Adresse.

## Entscheide zu den offenen Fragen (17.10.2026)

- E4 Bestellweg: Seite `/marketing/reels` **und** Agent-Werkzeug `reel_beauftragen`.
- E5 Stimme: Laura nutzt den Podcast-Sprechdienst mit; Firewall-Freigabe fuer 8899 **nur** fuer die VM-Adresse `100.67.177.45`.
- E6 Laura-Repo: Aenderungen auf Branch, Suite gruen, Merge nach `main` und VM-Deploy erst beim Livegang nach User-OK.
