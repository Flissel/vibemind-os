---
name: kurzfassung
description: "Langes Video kürzen: die stärksten 15–60 Sekunden finden und als Reel ausgeben, mit kurzem Einstiegssatz. Bei Aufträgen der Art 'kurzfassung'."
---

# Kurzfassung

1. **Video.** Das lange Video steht im Briefing (`video`). `transkript` lesen.
2. **Stelle wählen.** Ein zusammenhängender Abschnitt oder 2–4 kurze Stellen, die zusammen den einen Gedanken
   tragen (Thema aus dem Briefing). Keine Stelle mitten im Satz beginnen oder enden lassen.
3. **Projekt.** `projekt_anlegen`, `material_uebernehmen` für das Video.
4. **Sprechertext.** Hier spricht die Person im Video selbst. Je Beat ein **kurzer** Einordnungssatz
   (≤ 8 Wörter) – oder, wenn das Briefing `sprechertext` enthält, genau dieser. Die Beats legen den Clip so,
   dass er an der gewählten Stelle beginnt (`src_in_frame`).
5. **Bauen.** `reel_bauen` mit den Beats, `hook` = Thema in einer Zeile.
6. Notiz: welche Stelle (Sekunden) und warum, ein Satz. Dann `EXPORT: <id>`.

Hinweis: Weil ein Sprecher-Satz pro Beat gesprochen wird, ist dieser Weg für Material mit gutem eigenem Ton
nur eine Notlösung – passt keine Einordnung, lieber `FEHLER: Kurzfassung ohne Einsprechen nicht möglich`.
