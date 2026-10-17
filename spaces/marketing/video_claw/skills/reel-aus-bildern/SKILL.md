---
name: reel-aus-bildern
description: "Reel ohne Filmmaterial: Titelkarte, 3–5 Markenbilder mit wechselnder Bewegung, Schlusskarte mit Handlung; Farben und Logo der Firma. Bei Aufträgen der Art 'bilder'."
---

# Reel aus Bildern

Aufbau (Gesamtlänge ≈ `dauer_s` aus dem Briefing):

| Teil | Clip | Satz |
|---|---|---|
| 1 | `titelkarte` mit dem Hook (≤ 6 Wörter), 2–3 s | der Hook, gesprochen |
| 2–5 | je ein `standbild`, 3–6 s | je ein Satz zum Bild |
| letzter | `titelkarte` mit der Handlung („Jetzt Termin sichern“), 2–3 s | die Handlung, gesprochen |

1. **Projekt.** `projekt_anlegen` mit dem Titel des Auftrags.
2. **Bilder holen.** `bild_importieren` für jedes Bild aus `bilder` und für das `logo` (falls im Briefing).
   Nur Namen aus dem Briefing – keine anderen.
3. **Farben.** `farben.hintergrund` / `farben.text` aus dem Briefing. Fehlen sie: dunkles Petrol `#0f2422`
   und fast Weiß `#e9fbf6`.
4. **Karten.** Titelkarte und Schlusskarte mit diesen Farben, Logo auf die Schlusskarte (nicht auf die erste).
   Text kurz: die Karte ist kein Absatz.
5. **Standbilder.** Bewegung abwechseln: `zoom_rein`, `schwenk`, `zoom_raus`, `ruhig` – nie zweimal dieselbe
   hintereinander. Dauer je Bild ≥ Länge seines Satzes (2,5 Wörter/s) plus 0,5 s.
6. **Sprechertext.** Aus `sprechertext` im Briefing, Satz für Satz auf die Teile verteilt. Nichts dazuerfinden;
   ist er zu lang für `dauer_s`, kürze ohne den Sinn zu ändern und notiere das.
7. **Bauen.** `reel_bauen` mit den Beats (alle `src_in_frame: 0`), `stimme` aus dem Briefing, ohne `hook`
   (der steht schon auf der Titelkarte), `logo_asset_id` nur wenn die Schlusskarte kein Logo trägt.
8. Notiz: Bildfolge und Bewegungen, ein Satz. Dann `EXPORT: <id>`.

Der KI-Hinweis („Stimme: KI“) kommt automatisch; Bilder und Karten sind keine KI.
