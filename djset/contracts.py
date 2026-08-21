"""Gemeinsames Vokabular aller Pipeline-Stufen.

Harte Regeln aus dem Spec werden hier als Typ-Constraints erzwungen. Was der
Typ nicht zulaesst, kann keine Stufe versehentlich produzieren.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Technique = Literal["blend", "eq_swap", "filter_sweep", "cut", "double_drop"]
Strictness = Literal["strict", "loose"]

CAMELOT_RE = r"^(1[0-2]|[1-9])[AB]$"

# Untergrenze aus dem Spec: darunter waere es kein Set. Steht hier, damit sie
# an genau einer Stelle definiert ist und im Typ erzwungen wird.
MIN_TRACKS = 3


class BpmRange(BaseModel):
    target: float = Field(gt=0)
    min: float = Field(gt=0)
    max: float = Field(gt=0)

    @model_validator(mode="after")
    def _target_within_range(self) -> "BpmRange":
        if self.min > self.max:
            raise ValueError("bpm.min darf nicht groesser als bpm.max sein")
        if not self.min <= self.target <= self.max:
            raise ValueError("bpm.target muss zwischen min und max liegen")
        return self


class TransitionRules(BaseModel):
    bars_min: int = Field(ge=1)
    bars_max: int = Field(ge=1)
    techniques: list[Technique] = Field(min_length=1)

    @model_validator(mode="after")
    def _bars_ordered(self) -> "TransitionRules":
        if self.bars_min > self.bars_max:
            raise ValueError("transition.bars_min darf nicht groesser als bars_max sein")
        return self


class EnergyPoint(BaseModel):
    position: float = Field(ge=0.0, le=1.0)
    energy: float = Field(ge=0.0, le=1.0)


class TrackCount(BaseModel):
    needed: int = Field(ge=MIN_TRACKS)
    request: int = Field(ge=MIN_TRACKS)

    @model_validator(mode="after")
    def _request_covers_needed(self) -> "TrackCount":
        if self.request < self.needed:
            raise ValueError("track_count.request muss mindestens needed sein")
        return self


class StructureRules(BaseModel):
    min_intro_bars: int = Field(ge=0)
    min_outro_bars: int = Field(ge=0)


class Profile(BaseModel):
    genre: str = Field(min_length=1)
    length_minutes: float = Field(gt=0)
    bpm: BpmRange
    transition: TransitionRules
    harmonic_strictness: Strictness
    energy_arc: list[EnergyPoint] = Field(min_length=2)
    track_count: TrackCount
    style_vocab: list[str] = Field(min_length=1)
    structure: StructureRules

    @model_validator(mode="after")
    def _arc_sorted(self) -> "Profile":
        positions = [point.position for point in self.energy_arc]
        if positions != sorted(positions):
            raise ValueError("energy_arc muss nach position aufsteigend sortiert sein")
        return self


class Bpm(BaseModel):
    value: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class BeatGrid(BaseModel):
    first_downbeat_s: float = Field(ge=0)
    bar_length_s: float = Field(gt=0)


class Key(BaseModel):
    camelot: str = Field(pattern=CAMELOT_RE)
    confidence: float = Field(ge=0.0, le=1.0)


class MixRegions(BaseModel):
    intro_end_s: float = Field(ge=0)
    outro_start_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _intro_before_outro(self) -> "MixRegions":
        if self.intro_end_s >= self.outro_start_s:
            raise ValueError("intro_end_s muss vor outro_start_s liegen")
        return self


class Loudness(BaseModel):
    lufs_integrated: float
    true_peak: float


class Verdict(BaseModel):
    usable: bool
    notes: str


class TrackMeasurements(BaseModel):
    """Was das DSP-Modul misst. Enthaelt bewusst kein Urteil."""

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    duration_s: float = Field(gt=0)
    bpm: Bpm
    beatgrid: BeatGrid
    key: Key
    energy: list[float] = Field(min_length=1)
    mix_regions: MixRegions
    loudness: Loudness

    @model_validator(mode="after")
    def _regions_within_duration(self) -> "TrackMeasurements":
        if self.mix_regions.outro_start_s > self.duration_s:
            raise ValueError("outro_start_s liegt hinter dem Trackende")
        return self


class TrackAnalysis(TrackMeasurements):
    """Messung plus das Urteil des Track-Analysten."""

    verdict: Verdict


class Rejection(BaseModel):
    track_id: str
    reason: str = Field(min_length=1)


class Shortlist(BaseModel):
    accepted: list[str]
    rejected: list[Rejection]
    shortened: bool
    note: str


class MixEntry(BaseModel):
    track_id: str = Field(min_length=1)
    play_from_s: float = Field(ge=0)
    play_to_s: float = Field(gt=0)
    # Stretch-Grenze aus dem Spec: +-6 %. Im Typ erzwungen.
    timestretch_ratio: float = Field(ge=0.94, le=1.06)
    gain_db: float

    @model_validator(mode="after")
    def _from_before_to(self) -> "MixEntry":
        if self.play_from_s >= self.play_to_s:
            raise ValueError("play_from_s muss vor play_to_s liegen")
        return self


class EqPoint(BaseModel):
    position: float = Field(ge=0.0, le=1.0)
    gain_db: float


class EqCurve(BaseModel):
    low: list[EqPoint] = Field(min_length=2)
    mid: list[EqPoint] = Field(min_length=2)
    high: list[EqPoint] = Field(min_length=2)


class Transition(BaseModel):
    from_track: str = Field(min_length=1)
    to_track: str = Field(min_length=1)
    start_at_s: float = Field(ge=0)
    bars: int = Field(ge=1)
    technique: Technique
    eq_out: EqCurve
    eq_in: EqCurve


class MixPlan(BaseModel):
    target_length_s: float = Field(gt=0)
    entries: list[MixEntry] = Field(min_length=MIN_TRACKS)
    transitions: list[Transition]

    @model_validator(mode="after")
    def _transition_between_each_pair(self) -> "MixPlan":
        if len(self.transitions) != len(self.entries) - 1:
            raise ValueError(
                "zwischen je zwei Tracks muss es genau einen Uebergang geben"
            )
        return self
