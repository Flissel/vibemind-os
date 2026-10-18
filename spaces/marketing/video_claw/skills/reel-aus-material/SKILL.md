---
name: reel-aus-material
description: "Reel aus vorhandenen Laura-Videos: passende Stellen im Transkript finden; spricht dort eine Person, bleibt ihr Originalton (mit Untertiteln aus dem Transkript), sonst trägt ein eingesprochener Satz den Clip. Bei Aufträgen der Art 'material'."
---

# Reel aus Material

1. **Material klären.** Stehen im Briefing `videos` (Laura-IDs), nimm genau diese. Sonst `material_suchen`
   mit einem Wort aus dem Thema; nimm höchstens 3 Videos, die zum Thema passen. Passt nichts:
   `FEHLER: Kein passendes Material`.
2. **Projekt.** Nimm das Projekt des (ersten) Videos (`projekt_id` aus `material_suchen`). Liegen weitere
   Videos in anderen Projekten, hole sie mit `material_uebernehmen` dazu – **aber ohne Originalton**
   (ihr Transkript bleibt im alten Projekt, sie bekämen keine Untertitel).
3. **Stellen finden.** `transkript` je Video. Suche 3–6 Stellen, die den einen Gedanken tragen
   (`start_frame` ÷ 30 = Sekunde). Bevorzuge Stellen, an denen die Person **mittig** im Bild ist – der
   Hochformat-Zuschnitt nimmt die Mitte.
4. **Je Stelle entscheiden:**
   - **Die Person sagt dort selbst das Richtige** → Originalton-Beat
     `{"originalton": true, "asset_id", "src_in_frame", "src_out_frame"}`, ganze Sätze (Segmentgrenzen).
   - **Das Bild passt, der Ton nicht** (Musik, Bildschirm, Nebensatz) → Beat mit `text`: ein Satz aus dem
     `sprechertext` des Briefings (nichts dazuerfinden).
5. **Einstieg.** Der erste Beat ist der Hook: entweder ein starker Originalton-Satz oder eine `titelkarte`
   mit dem Thema (≤ 6 Wörter). Gesamtlänge ≈ `dauer_s`.
6. **Bauen.** `reel_bauen` mit den Beats, `stimme` aus dem Briefing, `hook` nur wenn keine Titelkarte.
7. Notiz: welche Videos und Stellen, welche mit Originalton, ein Satz. Dann `EXPORT: <id>`.

Der KI-Hinweis („Stimme: KI“) kommt automatisch, sobald ein Beat eingesprochen ist.
