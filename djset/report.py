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


# Sicherheitsabstand der einen erlaubten Korrektur zur Decke. Zwei Gruende,
# und schon der erste allein genuegt:
#
# 1. Genau auf die Decke zu zielen heisst, sie zur Haelfte zu verfehlen. Der
#    Weg vom gemessenen Spitzenwert ueber 20*log10 zurueck in einen Faktor und
#    durch float32 wieder hinaus trifft -1.0 dB nur bis auf etwa 1e-7 dB, mit
#    zufaelligem Vorzeichen - und qa.py vergleicht mit einem strikten `>`.
#    Gemessen ueber die echte Folge aus cmd_build: die zweite Messung ergab
#    -0.999999709 dB, also 2,9e-07 dB ueber der Decke, und der Lauf meldete
#    einen Befund fuer ein File, das die Grenze um drei Zehnmillionstel dB
#    reisst. Erfolgskriterium 5 galt damit als verletzt, obwohl es das nicht war.
# 2. Einen Renderer exakt auf eine harte Grenze zu zielen ist auch ohne die
#    Fliesskommafrage schlechte Praxis: die True-Peak-Messung ist selbst eine
#    Schaetzung (oversampled), und zwischen Messung und Auslieferung liegt ein
#    zweiter Render.
#
# 0,3 dB: weit ueber dem Fliesskommarauschen, hoerbar ist der Unterschied nicht.
CORRECTION_HEADROOM_DB = 0.3


def correction_gain_db(report: QaReport) -> float | None:
    """Die Verstaerkung fuer den einen Re-Render, oder None wenn keiner hilft.

    Nur ein True-Peak-Befund ist eine reine Pegelfrage: mit niedrigerem
    Master-Gain neu gerendert, ist er behoben. Eine Laengenabweichung steht im
    Mixplan - derselbe Plan noch einmal gerendert liefert dieselbe Laenge,
    also hilft hier kein Gain. Diese Funktion entscheidet nur, OB und WIEVIEL
    Gain hilft; ob ueberhaupt neu gerendert wird, entscheidet die aufrufende
    Stufe.

    Gezielt wird UNTER die Decke, nicht auf sie - siehe
    CORRECTION_HEADROOM_DB.
    """
    if not any(finding.kind == "true_peak" for finding in report.findings):
        return None
    return TRUE_PEAK_CEILING_DB - CORRECTION_HEADROOM_DB - report.true_peak


def _status_line(qa: QaReport, corrected: bool) -> str:
    """Die eine Zeile, die beantwortet, ob der Lauf ohne Nachlesen brauchbar ist.

    `corrected` wird VOR `qa.ok` geprueft. Der normale Erfolgsfall einer
    Korrektur ist genau der, bei dem der neu gemessene Report danach sauber
    ist (`qa.ok` also wahr ist) - wuerde `qa.ok` zuerst geprueft, wuerde
    dieser Fall als "Keine Befunde - sauber ausgeliefert." gemeldet und die
    Korrektur damit aus der einen Zeile verschwinden, die genau dafuer da ist
    (Fund aus Fix Round 1: der Reviewer produzierte exakt diesen Fall).
    Spec Abschnitt 7 verlangt Auslieferung "mit Vermerk" - ein korrigierter,
    jetzt sauberer Lauf ist ein eigenes, meldenswertes Ergebnis, kein
    unkorrigierter sauberer Lauf.
    """
    if corrected:
        if qa.ok:
            return (
                "Einmal mit korrigiertem Pegel neu gerendert, danach ohne "
                "Befund."
            )
        return (
            "Befunde vorhanden, danach einmal mit korrigiertem Pegel neu "
            "gerendert und mit Vermerk ausgeliefert."
        )
    if qa.ok:
        return "Keine Befunde - sauber ausgeliefert."
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

    Welcher `qa`-Report bei `corrected=True` uebergeben wird, entscheidet die
    aufrufende Stufe (Task 8), nicht diese Funktion - sie validiert das nicht.
    Er MUSS aber der neu gemessene Report des korrigierten Re-Renders sein,
    nicht der urspruengliche, der die Korrektur erst ausgeloest hat: "Das
    fertige Set" und die Statuszeile lesen `length_s`, `lufs_integrated` und
    `true_peak` direkt aus `qa` und wuerden sonst Werte zeigen, die nicht mehr
    zum tatsaechlich ausgelieferten File passen - der Bericht behauptet dann
    faelschlich, was ausgeliefert wurde, sei noch der Pegel, der die
    Korrektur ausgeloest hat.
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
