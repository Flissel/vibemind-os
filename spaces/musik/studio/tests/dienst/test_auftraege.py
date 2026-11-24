"""Auftragsablage: Dateien, ID-Pruefung, Reihenfolge."""
import re
from datetime import datetime, timedelta

import pytest

from djset.dienst.auftraege import Ablage


def test_anlegen_und_hole(tmp_path):
    ablage = Ablage(tmp_path)
    a = ablage.anlegen("set", {"wunsch": "house"}, "telegram")
    assert re.match(r"^[0-9]{8}-[0-9]{6}-set-[0-9a-f]{4}$", a.id)
    assert a.zustand == "wartet" and a.kanal == "telegram"
    assert (tmp_path / "auftraege" / a.id / "auftrag.json").is_file()
    assert ablage.hole(a.id).eingabe == {"wunsch": "house"}


def test_fremde_id_ist_keyerror(tmp_path):
    ablage = Ablage(tmp_path)
    with pytest.raises(KeyError):
        ablage.hole("..\\x")
    with pytest.raises(KeyError):
        ablage.hole("20261124-120000-video-zzzz")
    with pytest.raises(KeyError):
        ablage.hole("20261124-120000-video-abcd")  # gueltig geformt, nicht vorhanden


def test_video_vor_wartendem_set(tmp_path):
    ablage = Ablage(tmp_path)
    ablage.anlegen("set", {}, "mcp")
    ablage.anlegen("video", {}, "mcp")
    assert ablage.naechster().art == "video"


def test_laura_render_vor_video_und_nach_erstellt(tmp_path):
    ablage = Ablage(tmp_path)
    erstes = ablage.anlegen("video", {}, "mcp")
    erstes.erstellt = erstes.erstellt - timedelta(minutes=5)
    ablage.speichern(erstes)
    ablage.anlegen("video", {}, "mcp")
    laura = ablage.anlegen("laura_render", {}, "mcp")
    assert ablage.naechster().id == laura.id
    laura.zustand = "fertig"
    ablage.speichern(laura)
    assert ablage.naechster().id == erstes.id


def test_alle_und_leer(tmp_path):
    ablage = Ablage(tmp_path)
    assert ablage.alle() == [] and ablage.naechster() is None
    ablage.anlegen("aufnehmen", {}, "app")
    assert len(ablage.alle()) == 1


def test_zeitstempel_sind_datetime(tmp_path):
    a = Ablage(tmp_path).anlegen("video", {}, "mcp")
    assert isinstance(a.erstellt, datetime)
