---
name: reel-aus-material
description: "Reel aus vorhandenen Laura-Videos: passende Stellen im Transkript finden, 3–6 Beats mit je einem gesprochenen Satz, Hochformat mit Untertiteln. Bei Aufträgen der Art 'material'."
---

# Reel aus Material

1. **Material klären.** Stehen im Briefing `videos` (Laura-IDs), nimm genau diese. Sonst `material_suchen`
   mit einem Wort aus dem Thema; nimm höchstens 3 Videos, deren Name oder Transkript zum Thema passt.
   Passt nichts: `FEHLER: Kein passendes Material`.
2. **Stellen finden.** `transkript` je Video. Suche 3–6 Stellen, die den einen Gedanken tragen. Merke dir je
   Stelle den Start in Frames (`src_in_frame` = Sekunde × Bildrate, steht im Transkript).
   Bevorzuge Stellen, an denen die Person **mittig** im Bild ist – der Hochformat-Zuschnitt nimmt die Mitte.
3. **Projekt.** `projekt_anlegen` mit dem Titel des Auftrags; `material_uebernehmen` für jedes genutzte Video
   (die zurückgegebene asset_id gilt ab jetzt).
4. **Sprechertext.** Steht im Briefing `sprechertext`, verteile ihn auf die Beats (Satz für Satz, nichts
   dazuerfinden). Sonst schreibe 3–6 Sätze nach den Grundregeln, belegt durch das Transkript.
   Der erste Satz ist der Hook.
5. **Bauen.** `reel_bauen` mit den Beats `{text, asset_id, src_in_frame}`, `stimme` aus dem Briefing,
   `hook` aus dem Briefing (oder dein erster Satz, gekürzt auf eine Zeile). Gesamtlänge ≈ `dauer_s`.
6. Notiz: welche Videos und Stellen, ein Satz. Dann `EXPORT: <id>`.

Der KI-Hinweis („Stimme: KI“) kommt automatisch, weil die Stimme künstlich ist.
