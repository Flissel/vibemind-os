"""Gemeinsames Vokabular aller Pipeline-Stufen.

Harte Regeln aus dem Spec werden hier als Typ-Constraints erzwungen. Was der
Typ nicht zulaesst, kann keine Stufe versehentlich produzieren.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Technique = Literal["blend", "eq_swap", "filter_sweep", "cut", "double_drop"]
Strictness = Literal["strict", "loose"]

# Eine Camelot-Notation: Zahl 1..12 plus A (Moll) oder B (Dur). Beide
# Bestandteile sind Gruppen, damit djset.camelot dasselbe Muster benutzen
# kann statt ein zweites danebenzustellen - zwei Muster driften.
CAMELOT_RE = r"^(1[0-2]|[1-9])([AB])$"

# Untergrenze aus dem Spec: darunter waere es kein Set. Steht hier, damit sie
# an genau einer Stelle definiert ist und im Typ erzwungen wird.
MIN_TRACKS = 3

# Die Stretch-Grenze aus dem Spec Abschnitt 7: plus/minus 6 Prozent. Steht an
# genau dieser Stelle - der Typ MixEntry.timestretch_ratio erzwingt sie damit
# aus derselben Quelle, aus der Designer und Critic rechnen. Wer sie woanders
# neu hinschreibt, hat zwei Grenzen, die auseinanderdriften koennen.
MIN_RATIO = 0.94
MAX_RATIO = 1.06


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
    # Gesang an oder aus fuer das ganze Set (Plan 1c). Aus heisst: nur die
    # Begleitung jedes Songs, etwa fuer Musik unter einem Video.
    gesang: bool = True

    @model_validator(mode="after")
    def _arc_sorted(self) -> "Profile":
        positions = [point.position for point in self.energy_arc]
        if positions != sorted(positions):
            raise ValueError("energy_arc muss nach position aufsteigend sortiert sein")
        return self


class Bpm(BaseModel):
    """Tempo in BPM.

    ACHTUNG zur Konfidenz: `confidence` sagt NICHTS ueber die Tempo-Oktave.
    Sie misst, wie gleichmaessig die erkannten Beatabstaende sind - also die
    Qualitaet des Rasters, nicht die Sicherheit des Tempos. Eine saubere
    Halftime-Lesung (87 statt 174) hat perfekt gleichmaessige Abstaende und
    bekommt deshalb eine Konfidenz nahe 1.0; siehe den Regressionstest
    tests/dsp/test_bpm.py::test_halftime_reading_still_scores_high_confidence.
    Die Oktavenfrage (Spec Abschnitt 7: "BPM halb oder doppelt erkannt")
    darf der Track-Analyst deshalb nicht an diesem Feld entscheiden, sondern
    nur am BPM-Korridor des Profils (djset.dsp.analyze.snap_to_range).

    Der Wert ist derselbe wie `BeatGrid.confidence` - bewusst, weil das
    DSP-Modul nichts Ehrlicheres ueber die Tempo-Sicherheit weiss und ein
    erfundener Zweitwert schlimmer waere als ein benannter gleicher.
    """

    value: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class BeatGrid(BaseModel):
    """Taktraster.

    `confidence` ist die Regelmaessigkeit der Beatabstaende: 1.0 bei exakt
    konstantem Abstand, 0.0 bei voelliger Streuung. Damit kann die
    Agentenschicht ein wackliges Raster erkennen (z. B. Live-Material oder
    ein Track ohne durchgehenden Beat), bevor sie Cue-Punkte darauf setzt.
    """

    first_downbeat_s: float = Field(ge=0)
    bar_length_s: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


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
    """Lautheit in dB-Groessen.

    allow_inf_nan=False auf beiden Feldern: pyloudnorm liefert fuer digitale
    Stille -inf, und -inf ueberlebt den eigenen JSON-Roundtrip nicht -
    model_dump_json() schreibt `null`, model_validate_json() lehnt das ab.
    Genau dieser Pfad ist das Wiederaufnehmen eines Laufs (die CLI liest die
    vorhandenen tracks/*.json). Ein stiller Track darf einen Lauf
    nicht sprengen (Spec Abschnitt 7: kaputte Einzeldatei -> Lauf laeuft
    weiter), deshalb liefert measure_loudness statt -inf den Bodenwert
    -120.0 dB - genau wie true_peak es schon immer getan hat.
    """

    lufs_integrated: float = Field(allow_inf_nan=False)
    true_peak: float = Field(allow_inf_nan=False)


class Verdict(BaseModel):
    """Das Urteil des Track-Analysten ueber einen Track.

    `energy_level` ordnet den Track auf derselben 0..1-Skala ein, auf der das
    Profil seinen energy_arc beschreibt. Bewusst vom Agenten vergeben und nicht
    aus TrackMeasurements.energy gerechnet, aber nicht weil die Kurve
    trackrelativ waere - sie ist gegen eine feste dBFS-Referenz normiert und
    ueber aehnlich gemastertes Material sehr wohl vergleichbar. Der Grund ist
    ein anderer: ihr praktischer Wertebereich fuer fertig gemastertes Material
    liegt zwischen etwa 0,75 und 0,87, waehrend ein vom Modell geschriebener
    Bogen ueber 0,30 bis 0,90 laeuft. Tracks direkt auf diesen Bogen abzubilden
    waere deshalb stark gestaucht. Dasselbe Modell, das den Bogen geschrieben
    hat, ordnet die Tracks darauf ein - das ist die einzige Stelle, an der
    beide Skalen zuverlaessig zusammenfinden.
    """

    usable: bool
    notes: str = Field(min_length=1)
    energy_level: float = Field(ge=0.0, le=1.0)


class LokalesRaster(BaseModel):
    """Das Taktraster eines einzelnen Mischbereichs.

    Fund 3 der Abnahme (23.11.): Songs halten ihr Tempo nicht ueber die ganze
    Laenge (farewell-kiss: Intro 93.0, Outro 95.1, im Mittel 94.06 BPM). Das
    globale BeatGrid ist eine Gerade ueber den ganzen Song und liegt in den
    Mischbereichen neben den echten Beats. Dieses Raster ist eine Gerade nur
    durch die Beats des Bereichs.

    `downbeat_s` ist ein echter erkannter Beat, der dem globalen Takt nach ein
    Taktanfang ist. `streuung_schlaege` ist die Standardabweichung der Beats
    um die Gerade, in Schlaegen: klein heisst gleichmaessig, gross heisst, dass
    die Beat-Erkennung dort unsicher ist.
    """

    downbeat_s: float = Field(ge=0)
    bar_length_s: float = Field(gt=0)
    streuung_schlaege: float = Field(ge=0)
    beats: int = Field(ge=2)

    @property
    def bpm(self) -> float:
        return 4 * 60.0 / self.bar_length_s

    def als_beatgrid(self) -> BeatGrid:
        return BeatGrid(
            first_downbeat_s=self.downbeat_s,
            bar_length_s=self.bar_length_s,
            confidence=max(0.0, min(1.0, 1.0 - 4.0 * self.streuung_schlaege)),
        )


class LokaleRaster(BaseModel):
    """Die lokalen Raster an Anfang und Ende eines Tracks (je None, wenn der
    Bereich zu wenige Beats hat)."""

    intro: LokalesRaster | None = None
    outro: LokalesRaster | None = None


class UnreadableTrack(BaseModel):
    """Eine Datei, die sich nicht messen liess.

    Spec Abschnitt 7: eine kaputte Einzeldatei darf den Lauf nicht sprengen.
    Sie bekommt trotzdem ein Artefakt, damit nachvollziehbar bleibt, warum sie
    nicht im Set ist.
    """

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    error: str = Field(min_length=1)


class TrackMeasurements(BaseModel):
    """Was das DSP-Modul misst. Enthaelt bewusst kein Urteil."""

    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    duration_s: float = Field(gt=0)
    bpm: Bpm
    beatgrid: BeatGrid
    key: Key
    # Gegen eine feste dBFS-Referenz normiert, also ueber aehnlich gemastertes
    # Material vergleichbar - siehe den Docstring von dsp.analyze.energy_curve.
    # Fuer die Einordnung auf profile.energy_arc ist sie trotzdem nicht die
    # richtige Groesse, weil ihr praktischer Bereich viel enger ist als der des
    # Bogens; dafuer gibt es Verdict.energy_level.
    energy: list[float] = Field(min_length=1)
    mix_regions: MixRegions
    loudness: Loudness
    # None bei Messungen von vor Fund 3; der Bestand misst sie nach, der
    # Designer faellt ohne sie auf das globale Raster zurueck.
    lokale_raster: LokaleRaster | None = None

    @model_validator(mode="after")
    def _regions_within_duration(self) -> "TrackMeasurements":
        if self.mix_regions.outro_start_s > self.duration_s:
            raise ValueError("outro_start_s liegt hinter dem Trackende")
        return self


class TrackAnalysis(TrackMeasurements):
    """Messung plus das Urteil des Track-Analysten."""

    verdict: Verdict


class Rejection(BaseModel):
    track_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Shortlist(BaseModel):
    accepted: list[str]
    rejected: list[Rejection]
    shortened: bool
    note: str


class Setlist(BaseModel):
    """Die Reihenfolge, in der die Tracks laufen.

    Eigenes Artefakt, obwohl die Spec es nicht auflistet: der Critic schickt
    Beanstandungen an diese Stufe zurueck, und Erfolgskriterium 7 verlangt, dass
    jede Beanstandung in den Artefakten nachvollziehbar ist. Ohne die Setlist auf
    der Platte waere nicht zu sehen, was der Critic eigentlich bemaengelt hat.
    """

    order: list[str] = Field(min_length=MIN_TRACKS)
    note: str

    @model_validator(mode="after")
    def _no_track_twice(self) -> "Setlist":
        if len(set(self.order)) != len(self.order):
            raise ValueError("jede track_id darf nur einmal in der Setlist vorkommen")
        return self


ComplaintKind = Literal[
    "length", "stretch", "downbeat", "phase", "overlap", "energy_arc", "unknown_track"
]

# Welche Beanstandungen Stufe 5 ueberhaupt beheben kann. Der Architect legt die
# Reihenfolge fest - mehr nicht. Er kann das Set nicht laenger machen (dazu
# fehlt Material), er kann kein Stretch-Verhaeltnis aendern (das haengt am
# Tempo des Tracks) und er kann keinen Cue-Punkt verschieben (das ist Stufe 6).
# Ihn deswegen erneut laufen zu lassen kostet eine volle Runde Architect plus
# Designer und liefert zwangslaeufig dasselbe Ergebnis.
#
# Der Energiebogen und der zu kurze Overlap sind die zwei Faelle, bei denen
# eine andere Reihenfolge tatsaechlich hilft: die Energie haengt an der
# Position, und welcher Track neben welchem steht entscheidet, wie viel
# Intro- und Outro-Material an der Naht zusammenkommt.
REORDERABLE_KINDS: frozenset[str] = frozenset({"energy_arc", "overlap"})


class Complaint(BaseModel):
    """Eine Beanstandung des Critics.

    `kind` ist da, damit die Schleife in cmd_build nicht in den Texten nach
    Stichworten suchen muss, um zu entscheiden, ob eine erneute Runde
    ueberhaupt etwas aendern kann.
    """

    kind: ComplaintKind
    text: str = Field(min_length=1)

    @property
    def reorderable(self) -> bool:
        """Ob Stufe 5 daran etwas aendern kann."""
        return self.kind in REORDERABLE_KINDS


class CritiqueRound(BaseModel):
    """Eine Runde aus Setlist, Plan und dem Urteil darueber."""

    round: int = Field(ge=1)
    order: list[str] = Field(min_length=1)
    complaints: list[Complaint]
    chosen: bool


class CritiqueLog(BaseModel):
    """Das Protokoll aller Critic-Runden eines Laufs.

    Erfolgskriterium 7 verlangt, dass jede Beanstandung in den Artefakten
    nachvollziehbar ist. Auf der Standardausgabe eines unbeaufsichtigten Laufs
    ist sie das nicht - und die Beanstandungen der VERWORFENEN Runden sind
    gerade die, die zur Neuanordnung gefuehrt haben. Ohne dieses Protokoll
    waeren sie samt ihrer Setlists spurlos weg.
    """

    rounds: list[CritiqueRound] = Field(min_length=1)
    chosen_round: int = Field(ge=1)


class Streckabschnitt(BaseModel):
    """Ein Stueck eines Eintrags mit eigenem Stretch-Verhaeltnis.

    Es reicht vom Ende des vorigen Abschnitts (beim ersten: play_from_s) bis
    `bis_s`, beides in Sekunden der QUELLE.
    """

    bis_s: float = Field(gt=0)
    ratio: float = Field(ge=MIN_RATIO, le=MAX_RATIO)


# Toleranz fuer "der letzte Abschnitt endet bei play_to_s" - Fliesskomma.
_ENDE_TOLERANZ_S = 1e-6


class MixEntry(BaseModel):
    """Ein Track im Mixplan.

    Ohne `abschnitte` wird der ganze Eintrag mit `timestretch_ratio` gestreckt.
    Mit `abschnitte` (Fund 3 der Abnahme: Songs halten ihr Tempo nicht) hat
    jedes Stueck sein eigenes Verhaeltnis - Intro und Outro ihr lokales, der
    Koerper `timestretch_ratio`. Quell- und Ausgabezeit rechnen nur
    ausgabe_s/quelle_s um, keine Stufe rechnet das selbst nach.
    """

    track_id: str = Field(min_length=1)
    play_from_s: float = Field(ge=0)
    play_to_s: float = Field(gt=0)
    # Stretch-Grenze aus dem Spec: +-6 %. Im Typ erzwungen, aus denselben
    # Konstanten, die Designer und Critic benutzen.
    timestretch_ratio: float = Field(ge=MIN_RATIO, le=MAX_RATIO)
    gain_db: float
    abschnitte: list[Streckabschnitt] = Field(default_factory=list)
    # Gesang an oder aus (Plan 1c). Aus heisst: nur die Begleitung. Wirkt nur,
    # wenn der Song getrennt ist - set prueft das vor dem ersten Modellaufruf.
    stimme: bool = True

    @model_validator(mode="after")
    def _from_before_to(self) -> "MixEntry":
        if self.play_from_s >= self.play_to_s:
            raise ValueError("play_from_s muss vor play_to_s liegen")
        return self

    @model_validator(mode="after")
    def _abschnitte_decken_den_eintrag(self) -> "MixEntry":
        if not self.abschnitte:
            return self
        grenzen = [self.play_from_s] + [a.bis_s for a in self.abschnitte]
        if any(b <= a for a, b in zip(grenzen, grenzen[1:])):
            raise ValueError("Streckabschnitte muessen nach play_from_s aufsteigen")
        if abs(grenzen[-1] - self.play_to_s) > _ENDE_TOLERANZ_S:
            raise ValueError("der letzte Streckabschnitt muss bei play_to_s enden")
        return self

    def stuecke(self) -> list[tuple[float, float, float]]:
        """(von_s, bis_s, ratio) in Quellsekunden, lueckenlos von play_from_s
        bis play_to_s."""
        if not self.abschnitte:
            return [(self.play_from_s, self.play_to_s, self.timestretch_ratio)]
        result = []
        von = self.play_from_s
        for abschnitt in self.abschnitte:
            result.append((von, abschnitt.bis_s, abschnitt.ratio))
            von = abschnitt.bis_s
        return result

    def ausgabe_s(self, quelle_s: float) -> float:
        """Wie lange nach seinem Einsatz der Eintrag die Quellzeit erreicht."""
        summe = 0.0
        for von, bis, ratio in self.stuecke():
            if quelle_s <= von:
                break
            summe += (min(bis, quelle_s) - von) / ratio
        return summe

    def quelle_s(self, ausgabe_s: float) -> float:
        """Umkehrung von ausgabe_s."""
        rest = ausgabe_s
        stuecke = self.stuecke()
        for von, bis, ratio in stuecke:
            dauer = (bis - von) / ratio
            if rest <= dauer:
                return von + rest * ratio
            rest -= dauer
        von, bis, ratio = stuecke[-1]
        return bis + rest * ratio

    @property
    def ausgabe_dauer_s(self) -> float:
        return self.ausgabe_s(self.play_to_s)


class EqPoint(BaseModel):
    position: float = Field(ge=0.0, le=1.0)
    gain_db: float


class EqCurve(BaseModel):
    low: list[EqPoint] = Field(min_length=2)
    mid: list[EqPoint] = Field(min_length=2)
    high: list[EqPoint] = Field(min_length=2)


class Stimmwechsel(BaseModel):
    """Wo im Uebergang die Stimme vom einen Song zum anderen wechselt (Plan 1c).

    `bei_s` zaehlt ab Transition.start_at_s, in Sekunden der Ausgabe. Die
    Stimme des ausgehenden Songs blendet in `blende_s` VOR diesem Punkt aus,
    die des eingehenden in `blende_s` DANACH ein. Die Begleitung blendet davon
    unberuehrt wie bisher ueber den ganzen Uebergang.
    """

    bei_s: float = Field(gt=0)
    blende_s: float = Field(gt=0)


class Transition(BaseModel):
    """Ein Uebergang zwischen zwei Tracks.

    `overlap_s` ist die MASSGEBLICHE Dauer des Uebergangs, in Sekunden der
    AUSGABE. Ueber sie legt der Renderer seine EQ-Fenster, an ihr misst der
    Critic die Ueberlappung. Keine Stufe leitet die Fensterbreite noch einmal
    selbst her - genau das war der Fehler, den dieses Feld beendet: Stufe 6
    rechnete in Takten des QUELLTRACKS (bar_length_s / timestretch_ratio),
    Stufe 8 in Takten des ZIELTEMPOS (4 * 60 / target_bpm). Beide stimmen nur
    ueberein, solange das Verhaeltnis wirklich target_bpm/source_bpm ist -
    und genau das ist es nicht mehr, sobald ein Track ausserhalb der
    Stretch-Grenze liegt und ungestreckt bleibt. Die Differenz legte der
    Renderer als Fenster VOR die Naht: bei einem Schnitt (eq_out flach auf
    -60 dB) wurde daraus digitale Stille, bevor der neue Track ueberhaupt
    einsetzte - gemessen 0,72 s bei 4 Takten, linear wachsend mit der
    Taktzahl.

    `bars` ist die EFFEKTIVE Taktzahl, nicht die gewuenschte: sie kann unter
    profile.transition.bars_min liegen, wenn das Material an dieser Naht
    weniger hergibt. Ein Track ohne erkennbares Outro liefert einen Takt, auch
    wenn der Korridor bei sechzehn anfaengt - lieber ein kurzer Uebergang als
    Sekunden, die niemand fuellt. Sie ist BESCHREIBEND: sie steht in der
    Tracklist und im Bericht, aber keine Stufe rechnet mehr eine Dauer daraus.

    `overlap_s` darf null sein. Das ist kein Schoenheitsfehler, sondern der
    ehrliche Wert fuer eine Stossnaht: gleichfoermig laut gemastertes Material
    liefert laut find_mix_regions weder Intro noch Outro, die Schnittmenge aus
    beiden Seiten ist dann leer. Ein `gt=0` wuerde build_mixplan an dieser
    Stelle mit einer ValidationError abbrechen lassen - Spec Abschnitt 7
    ("Kein Fehler darf den Lauf haengen lassen") verlangt das Gegenteil, und
    der Critic hat fuer genau diesen Fall seine overlap-Beanstandung.
    """

    from_track: str = Field(min_length=1)
    to_track: str = Field(min_length=1)
    start_at_s: float = Field(ge=0)
    overlap_s: float = Field(ge=0)
    bars: int = Field(ge=1)
    technique: Technique
    eq_out: EqCurve
    eq_in: EqCurve
    # None: kein Wechsel geplant (Stossnaht ohne Ueberlappung, oder ein Plan
    # von vor Plan 1c). Der Renderer legt die Stimmen dann wie den Song.
    stimmwechsel: Stimmwechsel | None = None

    @model_validator(mode="after")
    def _stimmwechsel_im_uebergang(self) -> "Transition":
        wechsel = self.stimmwechsel
        if wechsel is None:
            return self
        toleranz = 1e-6
        if (
            wechsel.blende_s > wechsel.bei_s + toleranz
            or wechsel.bei_s + wechsel.blende_s > self.overlap_s + toleranz
        ):
            raise ValueError(
                f"Stimmwechsel bei {wechsel.bei_s:.3f} s mit {wechsel.blende_s:.3f} s "
                f"Blende passt nicht in den Uebergang von {self.overlap_s:.3f} s"
            )
        return self


class MixPlan(BaseModel):
    target_length_s: float = Field(gt=0)
    # Das Tempo, auf das alle timestretch_ratio gerechnet sind. Vom Code aus
    # profile.bpm.target uebernommen, kein Modellurteil.
    #
    # Der RENDERER braucht es nicht mehr: die Dauer eines Uebergangs steht seit
    # Transition.overlap_s im Plan, statt aus Taktzahl und Zieltempo
    # nachgerechnet zu werden (siehe dort - die zweite Herleitung war ein
    # Fehler). Gebraucht wird es weiter von der QA, die ihr Nahtfenster in
    # Takten des Zieltempos aufspannt und die Naht dagegen misst, und von der
    # Tracklist. Es bleibt damit auch das, was einen handgeschriebenen Mixplan
    # allein testbar macht, ohne tracks/*.json dazuzuladen - die
    # Determinismus-Grenze der Spec.
    target_bpm: float = Field(gt=0)
    entries: list[MixEntry] = Field(min_length=MIN_TRACKS)
    transitions: list[Transition]

    @model_validator(mode="after")
    def _transition_between_each_pair(self) -> "MixPlan":
        if len(self.transitions) != len(self.entries) - 1:
            raise ValueError(
                "zwischen je zwei Tracks muss es genau einen Uebergang geben"
            )
        return self

    @model_validator(mode="after")
    def _track_ids_unique(self) -> "MixPlan":
        ids = [entry.track_id for entry in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("jede track_id darf nur einmal in entries vorkommen")
        return self

    @model_validator(mode="after")
    def _transitions_match_their_neighbours(self) -> "MixPlan":
        """Jeder Uebergang muss genau das Paar verbinden, an dem er steht.

        Der Renderer trifft laut Spec keine eigenen Entscheidungen mehr; er
        muss sich darauf verlassen koennen, dass transitions[i] die Tracks
        entries[i] und entries[i+1] meint. Ohne diese Pruefung waere ein
        Mixplan mit vertauschten oder erfundenen Track-IDs schemagueltig.
        """
        for index, transition in enumerate(self.transitions):
            expected_from = self.entries[index].track_id
            expected_to = self.entries[index + 1].track_id
            if transition.from_track != expected_from:
                raise ValueError(
                    f"transitions[{index}].from_track ist {transition.from_track!r}, "
                    f"erwartet war {expected_from!r}"
                )
            if transition.to_track != expected_to:
                raise ValueError(
                    f"transitions[{index}].to_track ist {transition.to_track!r}, "
                    f"erwartet war {expected_to!r}"
                )
        return self


# `seam` ist eine GEMESSENE Abweichung an der Naht, `seam_unmeasurable` eine
# Naht, an der sich ueberhaupt kein Beatraster messen liess. Zwei Faelle, zwei
# Arten: das Nahtfenster liegt auf Intro- und Outro-Material, dem duennsten
# Teil beider Tracks, waehrend die Toleranz an einem durchgehenden Impulszug
# kalibriert wurde - eine unmessbare Naht ist im Betrieb also zu erwarten und
# darf nicht wie ein Bruch aussehen, wenn jemand die Befunde nach kind filtert.
#
# `stimmen` (Plan 1c): im Uebergang singen zwei Stimmen laenger als erlaubt
# gleichzeitig - gemessen an den Stimmen, die der Renderer gelegt hat.
# `stimmen_ungetrennt`: ein Song im Set hat keine getrennte Stimme, an seinen
# Naehten bleibt die Frage offen.
QaKind = Literal[
    "length", "true_peak", "seam", "seam_unmeasurable", "gap",
    "stimmen", "stimmen_ungetrennt",
]


class QaFinding(BaseModel):
    kind: QaKind
    text: str = Field(min_length=1)


class AudioFormat(BaseModel):
    """Kanaele, Abtastrate und Subtyp der ausgelieferten Datei.

    Gemessen, nicht angenommen: die Spec sagt 44,1 kHz / 24 Bit Stereo zu, der
    Renderer liefert Stereo im Float-Subtyp. Die Abweichung beim Subtyp ist
    bewusst (Float braucht die QA, um ueber 1.0 messen zu koennen) - aber ein
    unbeaufsichtigter Lauf, dessen Artefakte die einzige Rechenschaft sind,
    darf das nicht verschweigen.
    """

    channels: int = Field(ge=1)
    sample_rate: int = Field(gt=0)
    # Der Subtyp, wie libsndfile ihn nennt: FLOAT, PCM_24, PCM_16.
    subtype: str = Field(min_length=1)


# Nur diese drei Arten machen den Bericht rot. `length` und `seam_unmeasurable`
# sind Hinweise: eine Laengenabweichung steht im Mixplan und ein Re-Render
# aendert sie nicht, eine unmessbare Naht ist im Betrieb zu erwarten (siehe
# oben). Beide werden berichtet, blockieren aber nicht.
BLOCKING_KINDS: frozenset[str] = frozenset({"gap", "true_peak", "seam", "stimmen"})


class StimmNaht(BaseModel):
    """Was die QA an einem Uebergang ueber die Stimmen gemessen hat (Plan 1c).

    `wechsel_s` ist die Setzeit des Stimmwechsels, None ohne geplanten
    Wechsel. `gleichzeitig_s` sind die Sekunden im Uebergang, in denen beide
    Stimmen ueber der Schwelle liegen; None, wenn eine Seite ungetrennt ist.
    """

    from_track: str = Field(min_length=1)
    to_track: str = Field(min_length=1)
    start_at_s: float = Field(ge=0)
    wechsel_s: float | None = None
    gleichzeitig_s: float | None = None


class QaReport(BaseModel):
    """Was die QA am fertigen File gemessen hat.

    `ok` heisst: nichts Blockierendes gefunden (siehe BLOCKING_KINDS), Hinweise
    duerfen da sein. Auch ein roter Bericht verhindert die Auslieferung nicht -
    laut Spec wird nach hoechstens einem korrigierten Re-Render ausgeliefert und
    der Befund vermerkt.
    """

    length_s: float = Field(gt=0)
    target_length_s: float = Field(gt=0)
    audio_format: AudioFormat
    lufs_integrated: float = Field(allow_inf_nan=False)
    true_peak: float = Field(allow_inf_nan=False)
    findings: list[QaFinding]
    # Leer, wenn ohne getrennte Stimmen gerendert wurde (Plan 1c).
    stimmen: list[StimmNaht] = Field(default_factory=list)

    @property
    def blocking(self) -> list[QaFinding]:
        """Die Befunde, die den Bericht rot machen."""
        return [f for f in self.findings if f.kind in BLOCKING_KINDS]

    @property
    def hints(self) -> list[QaFinding]:
        """Die Befunde, die nur vermerkt werden."""
        return [f for f in self.findings if f.kind not in BLOCKING_KINDS]

    @property
    def ok(self) -> bool:
        return not self.blocking
