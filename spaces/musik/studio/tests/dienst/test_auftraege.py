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


def test_render_wartet_auf_musik(tmp_path):
    """I1(a): ein Render mit Musik-Auftrag kommt erst dran, wenn die Musik nicht mehr wartet/laeuft."""
    ablage = Ablage(tmp_path)
    musik = ablage.anlegen("video", {"wunsch": "x"}, "telegram")
    render = ablage.anlegen("laura_render", {"timeline_id": "tl1", "musik_auftrag": musik.id}, "telegram")
    assert ablage.naechster().id == musik.id          # nicht der Render, obwohl Vorrang 0
    musik.zustand = "laeuft"
    ablage.speichern(musik)
    assert ablage.naechster() is None                 # Musik laeuft: Render wartet weiter
    for ende in ("fertig", "gescheitert", "abgebrochen"):
        musik.zustand = ende
        ablage.speichern(musik)
        assert ablage.naechster().id == render.id, ende


def test_id_muss_ganz_passen(tmp_path):
    """F1: `fullmatch` statt `match` - ein Zeilenumbruch am Ende ist keine gueltige ID."""
    ablage = Ablage(tmp_path)
    with pytest.raises(KeyError):
        ablage.ordner("20261124-120000-video-abcd\n")


def test_alle_und_leer(tmp_path):
    ablage = Ablage(tmp_path)
    assert ablage.alle() == [] and ablage.naechster() is None
    ablage.anlegen("aufnehmen", {}, "app")
    assert len(ablage.alle()) == 1


def test_zeitstempel_sind_datetime(tmp_path):
    a = Ablage(tmp_path).anlegen("video", {}, "mcp")
    assert isinstance(a.erstellt, datetime)
