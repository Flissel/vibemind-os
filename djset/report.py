"""Der Bericht ueber einen Lauf, und die eine erlaubte Korrektur.

Spec Abschnitt 7: findet die QA Clipping oder eine Laengenabweichung, gibt es
genau einen korrigierten Re-Render, danach wird ausgeliefert und vermerkt. Nur
der Pegel ist so korrigierbar - die Laenge steht im Mixplan, und erneutes
Rendern aendert daran nichts.

Der Lauf ist unbeaufsichtigt: niemand sieht ihm zu, und dieser Bericht ist
danach das einzige Fenster in seine Entscheidungen. Erfolgskriterium 7
verlangt deshalb, dass jede Aussortierung und jede Critic-Beanstandung darin
nachvollziehbar ist - nicht nur im Erfolgsfall, auch dann, wenn nichts zu
melden war.
"""

from djset.contracts import CritiqueLog, QaReport, Shortlist, UnreadableTrack
from djset.qa import TRUE_PEAK_CEILING_DB


def correction_gain_db(report: QaReport) -> float | None:
    """Die Verstaerkung fuer den einen Re-Render, oder None wenn keiner hilft.

    Nur ein True-Peak-Befund ist eine reine Pegelfrage: mit niedrigerem
    Master-Gain neu gerendert, ist er behoben. Eine Laengenabweichung steht im
    Mixplan - derselbe Plan noch einmal gerendert liefert dieselbe Laenge,
    also hilft hier kein Gain. Diese Funktion entscheidet nur, OB und WIEVIEL
    Gain hilft; ob ueberhaupt neu gerendert wird, entscheidet die aufrufende
    Stufe.
    """
    if not any(finding.kind == "true_peak" for finding in report.findings):
        return None
    return TRUE_PEAK_CEILING_DB - report.true_peak


def _status_line(qa: QaReport, corrected: bool) -> str:
    """Die eine Zeile, die beantwortet, ob der Lauf ohne Nachlesen brauchbar ist."""
    if qa.ok:
        return "Keine Befunde - sauber ausgeliefert."
    if corrected:
        return (
            "Befunde vorhanden, danach einmal mit korrigiertem Pegel neu "
            "gerendert und mit Vermerk ausgeliefert."
        )
    return "Befunde vorhanden, mit Vermerk ausgeliefert (siehe unten)."


def render_qa_report(
    qa: QaReport,
    shortlist: Shortlist,
    unreadable: list[UnreadableTrack],
    critique: CritiqueLog | None,
    corrected: bool = False,
) -> str:
    """Der Bericht als Markdown.

    Reihenfolge bewusst gewaehlt fuer jemanden, der die Datei nach einem
    unbeaufsichtigten Lauf zum ersten Mal aufschlaegt: zuerst die eine
    Statuszeile, die ohne Weiterlesen beantwortet, ob ausgeliefert werden
    konnte, dann die Messwerte, dann die Befunde, die die Statuszeile
    begruenden - danach alles, was Erfolgskriterium 7 nachvollziehbar halten
    muss (Aussortierungen, unlesbare Dateien, Critic-Beanstandungen ueber alle
    Runden). Jede Sektion nennt explizit "keine"/"nichts", statt bei leerem
    Inhalt zu fehlen - ein Leser soll nie raten muessen, ob eine Sektion
    weggelassen wurde oder tatsaechlich nichts zu melden war.
    """
    lines = ["# QA-Bericht", "", f"**Status:** {_status_line(qa, corrected)}", ""]

    lines += [
        "## Das fertige Set",
        "",
        f"- Laenge: {qa.length_s / 60:.1f} Minuten (geplant "
        f"{qa.target_length_s / 60:.1f})",
        f"- Lautheit: {qa.lufs_integrated:.1f} LUFS",
        f"- True Peak: {qa.true_peak:.1f} dB",
    ]
    if corrected:
        lines.append("- Einmal mit korrigiertem Pegel neu gerendert.")
    lines.append("")

    lines += ["## Befunde der QA", ""]
    if qa.findings:
        lines += [f"- **{f.kind}**: {f.text}" for f in qa.findings]
    else:
        lines.append("- Keine.")
    lines.append("")

    lines += ["## Aussortiertes Material", ""]
    if shortlist.rejected:
        lines += [f"- `{r.track_id}`: {r.reason}" for r in shortlist.rejected]
    else:
        lines.append("- Nichts aussortiert.")
    # shortlist.note traegt die Begruendung des Curators immer, nicht nur bei
    # einer Kuerzung (Shortlist.shortened haengt nur an, ob weniger Tracks als
    # angefragt uebrig blieben) - sie an `shortened` zu haengen wuerde die
    # eigentliche Erklaerung im Normalfall verschweigen.
    lines += ["", shortlist.note, ""]

    lines += ["## Nicht lesbare Dateien", ""]
    if unreadable:
        lines += [f"- `{u.id}` ({u.path}): {u.error}" for u in unreadable]
    else:
        lines.append("- Keine.")
    lines.append("")

    lines += ["## Beanstandungen des Critics", ""]
    if critique is None or not critique.rounds:
        lines.append("- Keine.")
    else:
        for round_ in critique.rounds:
            # CritiqueRound traegt sein eigenes `chosen`-Flag und zaehlt seine
            # Runde ab eins. Nicht gegen einen Schleifenindex vergleichen -
            # chosen_round ist ebenfalls einsbasiert, aber round_.chosen ist
            # direkter und kann nicht auseinanderdriften, falls Runden je
            # anders gezaehlt wuerden.
            mark = " (uebernommen)" if round_.chosen else ""
            lines.append(f"### Runde {round_.round}{mark}")
            if round_.complaints:
                lines += [f"- **{c.kind}**: {c.text}" for c in round_.complaints]
            else:
                lines.append("- Keine.")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
