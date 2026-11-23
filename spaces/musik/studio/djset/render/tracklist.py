"""Stufe 8: die Tracklist zum fertigen Set."""

from pathlib import Path

from djset.contracts import MixPlan, TrackAnalysis


def timestamp(seconds: float) -> str:
    """Minuten und Sekunden, wie man sie in eine Tracklist schreibt."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def render_tracklist(
    plan: MixPlan, analyses: dict[str, TrackAnalysis]
) -> str:
    """Die Tracklist als Markdown-Tabelle."""
    starts = [0.0] + [t.start_at_s for t in plan.transitions]
    lines = [
        f"# Tracklist ({timestamp(plan.target_length_s)}, {plan.target_bpm:.0f} BPM)",
        "",
        "| Zeit | Track | Tonart | Uebergang danach |",
        "| --- | --- | --- | --- |",
    ]
    for index, entry in enumerate(plan.entries):
        analysis = analyses.get(entry.track_id)
        key = analysis.key.camelot if analysis else "?"
        name = Path(analysis.path).stem if analysis else entry.track_id
        if index < len(plan.transitions):
            transition = plan.transitions[index]
            after = f"{transition.technique}, {transition.bars} Takte"
        else:
            after = "-"
        lines.append(
            f"| {timestamp(starts[index])} | {name} ({entry.track_id}) "
            f"| {key} | {after} |"
        )
    return "\n".join(lines) + "\n"
