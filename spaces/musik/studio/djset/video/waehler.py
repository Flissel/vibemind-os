"""Der Waehler: das Modell nimmt Song, Einstieg und Gesang fuer ein Video.

Vorher filtert der Code, was ueberhaupt tragen kann (gemessen, Tempo sicher,
lang genug); das Modell waehlt nur noch nach Geschmack.
"""

from djset.agents.base import AgentClient, Budget, run_agent
from djset.bestand import SongEintrag
from djset.tempo import tempo_unsicher
from djset.video.laenge import VideoFehler
from djset.video.vertrag import VideoWahl
from djset.video.zuschnitt import traegt_s

PROMPT = """Du waehlst die Musik fuer ein Video.

Wunsch: {wunsch}
Laenge der Musik: {ziel_s:.0f} Sekunden

Diese {anzahl} Songs kommen in Frage (gemessen):

| ID | Titel | Tempo (BPM) | Tonart (Camelot) | Laenge | mittlere Energie | getrennt |
|----|-------|-------------|------------------|--------|------------------|----------|
{tabelle}

Lege fest:
- song_id: genau eine ID aus der Tabelle
- einstieg: "hoehepunkt", wenn der Wunsch Energie oder einen Hoehepunkt
  verlangt, sonst "anfang"
- gesang: nur true, wenn der Wunsch ausdruecklich Gesang verlangt (unter einem
  Video liegt meist Sprache), sonst false
- begruendung: ein Satz, warum dieser Song

Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."""


def _laenge(sekunden: float) -> str:
    gesamt = round(sekunden)
    return f"{gesamt // 60}:{gesamt % 60:02d}"


def _tabelle(songs: list[SongEintrag]) -> str:
    zeilen = []
    for song in songs:
        messung = song.messung
        if messung is None:
            raise VideoFehler(f"Song {song.id!r} hat keine Messung")
        energie = sum(messung.energy) / len(messung.energy)
        getrennt = "ja" if song.stimmen is not None else "nein"
        zeilen.append(
            f"| {song.id} | {song.titel} | {messung.bpm.value:.1f} | "
            f"{messung.key.camelot} | {_laenge(messung.duration_s)} | "
            f"{energie:.2f} | {getrennt} |"
        )
    return "\n".join(zeilen)


def kandidaten(
    songs: list[SongEintrag], ziel_s: float, gesang_noetig_getrennt: bool
) -> list[SongEintrag]:
    """Songs mit Messung, sicherem Tempo und genug Laenge; in Eingabereihenfolge.

    Braucht es Gesang oder Begleitung einzeln, kommen nur getrennte Songs in
    Frage."""
    gemessen = [s for s in songs if s.messung is not None]
    unsicher = tempo_unsicher(
        {s.id: s.messung.bpm.value for s in gemessen if s.messung},
        {s.id: s.tempo_abschnitte or [] for s in gemessen},
    )
    ergebnis = []
    for song in gemessen:
        messung = song.messung
        if messung is None or song.id in unsicher:
            continue
        if traegt_s(messung.beatgrid, messung.duration_s) < ziel_s:
            continue
        if gesang_noetig_getrennt and song.stimmen is None:
            continue
        ergebnis.append(song)
    return ergebnis


def video_waehlen(
    client: AgentClient,
    wunsch: str,
    ziel_s: float,
    kandidaten: list[SongEintrag],
    budget: Budget,
) -> VideoWahl:
    if not kandidaten:
        raise VideoFehler("Kein Song kommt in Frage (gemessen, Tempo sicher, lang genug)")
    wahl = run_agent(
        client,
        PROMPT.format(
            wunsch=wunsch,
            ziel_s=ziel_s,
            anzahl=len(kandidaten),
            tabelle=_tabelle(kandidaten),
        ),
        VideoWahl,
        budget,
    )
    erlaubt = [s.id for s in kandidaten]
    if wahl.song_id not in erlaubt:
        raise VideoFehler(
            f"Das Modell nannte {wahl.song_id!r}, erlaubt sind: {', '.join(erlaubt)}"
        )
    return wahl
