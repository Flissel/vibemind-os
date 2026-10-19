# Sprech-Dienst: Feintuning für englische Begriffe im deutschen Satz – Plan (für später)

> Status: **geparkt** (User 19.10.2026: „für später planen“). Nichts davon ist gebaut.

**Problem:** Chatterbox Multilingual spricht einen deutschen Satz durchgehend deutsch. Englische Begriffe
(„Sales-Bot“, „Bot“, „LinkedIn“) bekommen deutschen Akzent.

**Schon versucht (18./19.10.2026), alles vom User per Gehör verworfen:**
- Lautschrift-Ersatz im Ausspracheverzeichnis (`Sejls-Bott`, `Säils-Bott`, `Sehls-Bott`, `Sejls-Bot`)
- Regler Ausdruck/Taktung/Temperatur (7 Kombinationen)
- Satz in Sprachstücke teilen (englische Stücke mit `language_id=en`): hörbarer Bruch und deutlich
  andere Stimme an den Übergängen

Gebaut und weiter nutzbar: `aussprache.json` und `einstellungen.json` im Stimmenordner `E:\marketing-stimmen`
(vibemind-os `4d8f221f`, `1ca5d265`), Stimmproben `eigene-a/-b/-c.wav` aus den Poetry-Aufnahmen.

## Ansatz

LoRA-Feintuning des T3-Teils (Llama-Backbone, Text → Sprach-Token). Sprach-Vokabular, Vocoder (S3Gen) und
Speaker-Encoder bleiben eingefroren – so machen es die bekannten LoRA-Anpassungen:
- Swahili: https://huggingface.co/msingiai/sauti_tts_v2 (Clips mit ASR-CER > 10 % aussortiert)
- Indisch: https://huggingface.co/reenigne314/chatterbox-indic-lora (Rank 32 auf q/k/v/o)
- Trainingsskripte (Community, WIP): https://github.com/stlohrey/chatterbox-finetuning
- Finnisch-Beispiel: https://huggingface.co/RASMUS/Chatterbox-Finnish/blob/main/train.py

Deutsch ist Teil des Modells; ein Tokenizer-Umbau ist nicht nötig. Ziel ist nur: englische Begriffe im
deutschen Satz englisch aussprechen – idealerweise gleich in der Stimme des Users.

## Phase 0 – Mini-Test (Abbruchstelle)

- [ ] **0.1 Satzliste** (~50 Sätze): deutsche Alltagssätze aus Vertrieb/Marketing, je 1–2 englische Begriffe
  (Sales, Bot, Bots, LinkedIn, WhatsApp, Reel, Podcast, Pipeline, Lead, Follow-up, Call, Meeting, Feedback,
  Newsletter, Team). Jede Silbe der Begriffe einmal betont und unbetont. Ablage als Text + Nummer.
- [ ] **0.2 Aufnahme durch den User** (~10 min): ruhiger Raum, Handy nah, je Satz eine Datei oder eine lange
  Aufnahme mit Pausen (dann automatisch an Pausen schneiden).
- [ ] **0.3 Aufbereiten**: schneiden, 24 kHz mono, Lautheit angleichen; Whisper/Analyse-Laufzeit prüft jede
  Aufnahme gegen den Satztext, CER > 10 % fliegt raus.
- [ ] **0.4 Trainingsumgebung**: eigene venv neben `chatterbox-venv` (nicht dieselbe – der Live-Dienst darf
  nicht brechen), Skript aus `chatterbox-finetuning` auf das **Multilingual**-Modell anpassen, LoRA rank 16–32,
  fp16, kleine Batch. GPU-Speicher messen; ComfyUI währenddessen nicht nutzen (nie beenden).
- [ ] **0.5 Hörvergleich**: 5 feste Testsätze, Basis vs. LoRA, gleiche Stimmprobe. User entscheidet.
  **Weiter nur, wenn „Sales“/„Bot“ hörbar englischer klingen und das Deutsch nicht schlechter wird.**

## Phase 1 – Voller Datensatz (nur nach Go aus 0.5)

- [ ] ~300 Sätze (45–60 min Aufnahme), dieselbe Aufbereitung, Training über Nacht.
- [ ] Hörvergleich wie 0.5, zusätzlich 3 Sätze ganz ohne Anglizismen (Regression des Deutschen).

## Phase 2 – Einbinden

- [ ] Sprech-Dienst lädt optional einen LoRA-Adapter (`TON_LORA=<pfad>`), Vorgabe aus; Schalter je Stimme in
  `einstellungen.json`.
- [ ] Vorladen beim Start bleibt; Speicherbedarf mit Adapter messen.
- [ ] Tests: Adapter fehlt/kaputt → Dienst spricht ohne Adapter weiter (fail-soft), nie ein Ausfall.
- [ ] Live erst nach User-OK; Rückweg = Adapter abschalten.

## Risiken

- Trainingswerkzeuge sind Community-Arbeit (WIP), nicht offiziell; VRAM/Dauer nirgends dokumentiert.
- Zu wenig oder einseitige Daten verschlechtern das normale Deutsch (darum Regressionssätze).
- 12 GB GPU geteilt mit ComfyUI/FLUX.2 – Training nur in ruhigen Zeiten.
- Lizenz: Basis-Code MIT; vor produktivem Einsatz Lizenz der Modellgewichte prüfen.

## Bis dahin (nicht beschlossen)

Option: Sprechtexte ohne Anglizismen schreiben (Regel in Video-Claw-Skills, Podcast- und Marketing-
Werkzeugbeschreibungen + Prüfung mit deutschen Vorschlägen). Vom User am 19.10. noch nicht entschieden.
