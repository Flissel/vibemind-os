from djset.render.tracklist import render_tracklist, timestamp
from tests.fixtures.analyses import many


def test_timestamps_are_minutes_and_seconds() -> None:
    assert timestamp(0.0) == "0:00"
    assert timestamp(61.4) == "1:01"
    assert timestamp(3599.0) == "59:59"
    assert timestamp(3661.0) == "61:01"


def test_every_track_appears_with_its_start(profile) -> None:
    from djset.agents.designer import TransitionChoice, build_mixplan
    from djset.contracts import Setlist

    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = build_mixplan(
        setlist,
        tracks,
        [TransitionChoice(technique="blend", bars=16) for _ in range(3)],
        profile,
    )

    text = render_tracklist(plan, {t.id: t for t in tracks})

    for track in tracks:
        assert track.id in text
    assert text.count("\n|") >= 4
    assert "0:00" in text


def test_the_starts_match_the_plan(profile) -> None:
    """Prueft die Zeitspalte gegen die im Mixplan stehenden Positionen.

    Die urspruengliche Fassung dieses Tests (`test_the_starts_are_in_order`)
    parste ihre eigene Ausgabe zurueck, ohne sie gegen etwas Unabhaengiges zu
    pruefen: sie nahm blind Spalte 1 jeder Zeile und verglich die Liste mit
    ihrer eigenen sortierten Fassung. Das haette auch bestanden, wenn
    render_tracklist in jeder Zeile "0:00" geschrieben haette (eine bereits
    sortierte, aber falsche Liste), und es haette bei vertauschten Spalten
    stillschweigend die falsche Spalte als "Zeit" genommen, solange Parser und
    Renderer sich einig gewesen waeren.

    Diese Fassung ermittelt die Zeit-Spalte ueber die Kopfzeile (nicht ueber
    einen festen Index) und vergleicht jede Zeile gegen die Startzeiten, die
    unabhaengig aus dem Mixplan berechnet werden - derselben Quelle, aus der
    auch der Renderer sie lesen muss: Eintrag 0 bei 0, jeder weitere bei
    transitions[i].start_at_s.
    """
    from djset.agents.designer import TransitionChoice, build_mixplan
    from djset.contracts import Setlist

    tracks = many(4)
    setlist = Setlist(order=[t.id for t in tracks], note="")
    plan = build_mixplan(
        setlist,
        tracks,
        [TransitionChoice(technique="blend", bars=16) for _ in range(3)],
        profile,
    )

    text = render_tracklist(plan, {t.id: t for t in tracks})
    lines = text.splitlines()

    table_lines = [line for line in lines if line.startswith("| ")]
    header_cells = [cell.strip() for cell in table_lines[0].split("|")[1:-1]]
    zeit_column = header_cells.index("Zeit")

    data_rows = [line for line in table_lines[1:] if not line.startswith("| ---")]
    assert len(data_rows) == len(plan.entries)

    actual_times = [
        row.split("|")[1:-1][zeit_column].strip() for row in data_rows
    ]

    expected_starts = [0.0] + [t.start_at_s for t in plan.transitions]
    expected_times = [timestamp(s) for s in expected_starts]

    assert actual_times == expected_times
