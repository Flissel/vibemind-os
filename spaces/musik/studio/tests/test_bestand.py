import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
import soundfile as sf

from djset.bestand import Bestand, BestandFehler, SongEintrag, pruefe_herkunft
from djset.render.stretch import ffmpeg_path
from tests.fixtures.make_audio import click_track

HEUTE = date(2026, 11, 23)


@pytest.fixture
def bestand(tmp_path: Path) -> Bestand:
    return Bestand(tmp_path / "bestand")


@pytest.fixture
def eingang_wav(bestand: Bestand) -> Path:
    return click_track(bestand.eingang / "lied.wav", bpm=128.0, bars=16)


@pytest.fixture
def eingang_wav_anders(bestand: Bestand) -> Path:
    return click_track(bestand.eingang / "anders.wav", bpm=120.0, bars=16)


@pytest.fixture
def eingang_mp3_48k(bestand: Bestand, tmp_path: Path) -> Path:
    quelle = click_track(tmp_path / "quelle.wav", bpm=128.0, bars=4)
    ziel = bestand.eingang / "lied.mp3"
    subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(quelle), "-ar", "48000", str(ziel)],
        check=True,
    )
    return ziel


def test_aufnehmen_wandelt_und_traegt_ein(bestand, eingang_mp3_48k):
    eintrag = bestand.aufnehmen("lied.mp3", "Mein Lied", "suno-eigen", HEUTE)
    assert eintrag.id == "mein-lied"
    assert eintrag.datei == "mein-lied.wav"
    info = sf.info(bestand.pfad("mein-lied"))
    assert (info.samplerate, info.subtype) == (44_100, "FLOAT")
    assert bestand.alle() == [eintrag]
    assert (bestand.eingang / "lied.mp3").is_file()


@pytest.mark.parametrize(
    "name", ["../x.wav", "a/b.wav", "a\\b.wav", "C:\\x.wav", "C:x.wav", "", "..", "x:.wav"]
)
def test_aufnehmen_lehnt_pfade_ab(bestand, name):
    with pytest.raises(BestandFehler):
        bestand.aufnehmen(name, "X", "suno-eigen", HEUTE)


def test_aufnehmen_lehnt_fehlende_datei_und_verzeichnis_ab(bestand):
    (bestand.eingang / "ordner.wav").mkdir()
    for name in ("gibtsnicht.wav", "ordner.wav"):
        with pytest.raises(BestandFehler):
            bestand.aufnehmen(name, "X", "suno-eigen", HEUTE)


def test_aufnehmen_lehnt_unbekannte_endung_ab(bestand):
    (bestand.eingang / "notizen.txt").write_text("hallo")
    with pytest.raises(BestandFehler, match="Endung"):
        bestand.aufnehmen("notizen.txt", "X", "suno-eigen", HEUTE)


@pytest.mark.parametrize("herkunft", ["", "spotify", "lizenz:", "lizenz:  ", "unbekannt"])
def test_aufnehmen_ohne_gueltige_herkunft(bestand, eingang_wav, herkunft):
    with pytest.raises(BestandFehler):
        bestand.aufnehmen("lied.wav", "X", herkunft, HEUTE)
    assert bestand.alle() == []


@pytest.mark.parametrize(
    "herkunft", ["suno-eigen", "eigen-aufgenommen", "lizenz:CC-BY 4.0"]
)
def test_pruefe_herkunft_laesst_gueltiges_durch(herkunft):
    assert pruefe_herkunft(herkunft) == herkunft


def test_dieselbe_datei_zweimal(bestand, eingang_wav):
    bestand.aufnehmen("lied.wav", "Erstes", "suno-eigen", HEUTE)
    shutil.copy(bestand.eingang / "lied.wav", bestand.eingang / "kopie.wav")
    with pytest.raises(BestandFehler, match="erstes"):
        bestand.aufnehmen("kopie.wav", "Zweites", "suno-eigen", HEUTE)
    assert [e.id for e in bestand.alle()] == ["erstes"]


def test_gleicher_titel_zweimal(bestand, eingang_wav, eingang_wav_anders):
    bestand.aufnehmen("lied.wav", "Mein Lied", "suno-eigen", HEUTE)
    with pytest.raises(BestandFehler, match="mein-lied"):
        bestand.aufnehmen("anders.wav", "Mein Lied", "suno-eigen", HEUTE)
    assert [e.id for e in bestand.alle()] == ["mein-lied"]


def test_umlaute_und_leerzeichen_im_titel(bestand, eingang_wav):
    eintrag = bestand.aufnehmen("lied.wav", "Über den Wolken", "suno-eigen", HEUTE)
    assert eintrag.id
    assert eintrag.titel == "Über den Wolken"
    assert bestand.hole(eintrag.id) == eintrag


def test_titel_ohne_slug_wird_abgelehnt(bestand, eingang_wav):
    with pytest.raises(BestandFehler):
        bestand.aufnehmen("lied.wav", "!!!", "suno-eigen", HEUTE)
    assert bestand.alle() == []


def test_fehlgeschlagene_umwandlung_hinterlaesst_nichts(bestand):
    (bestand.eingang / "kaputt.wav").write_text("das ist kein audio")
    with pytest.raises(BestandFehler):
        bestand.aufnehmen("kaputt.wav", "Kaputt", "suno-eigen", HEUTE)
    assert bestand.alle() == []
    assert list(bestand.songs.iterdir()) == []


def test_suno_id_wird_gespeichert(bestand, eingang_wav):
    eintrag = bestand.aufnehmen(
        "lied.wav", "Lied", "suno-eigen", HEUTE, suno_id="abc-123"
    )
    assert bestand.hole("lied").suno_id == "abc-123"
    assert eintrag.aufgenommen_am == HEUTE
    assert len(eintrag.pruefsumme) == 64


def test_messen_legt_die_messung_ab(bestand, eingang_wav):
    bestand.aufnehmen("lied.wav", "Lied", "suno-eigen", HEUTE)
    eintrag = bestand.messen("lied")
    assert eintrag.messung is not None
    assert eintrag.messung.id == "lied"
    assert Path(eintrag.messung.path) == bestand.pfad("lied")
    assert bestand.hole("lied").messung == eintrag.messung
    daten = json.loads((bestand.songs / "lied.json").read_text(encoding="utf-8"))
    assert daten["messung"]["id"] == "lied"


def test_unbekannte_id(bestand):
    for aufruf in (bestand.hole, bestand.messen, bestand.pfad):
        with pytest.raises(BestandFehler):
            aufruf("gibtsnicht")
    with pytest.raises(BestandFehler):
        bestand.hole("../x")


def test_alle_ist_nach_id_sortiert(bestand, eingang_wav, eingang_wav_anders):
    bestand.aufnehmen("lied.wav", "Zebra", "suno-eigen", HEUTE)
    bestand.aufnehmen("anders.wav", "Affe", "suno-eigen", HEUTE)
    assert [e.id for e in bestand.alle()] == ["affe", "zebra"]
    assert all(isinstance(e, SongEintrag) for e in bestand.alle())
