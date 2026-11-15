"""80-%-Warnung des Event-Shims an Felix (system/budget_melden.py).

Der Shim schreibt beim Erreichen von `warnen_ab` eine Zeile art=warnung in
laufzeit/shim-budget/<datum>.jsonl. Ein Host-Timer liest sie und schickt jede Warnung genau
einmal per Telegram. Der Shim kennt keinen Telegram-Token; Ausgaben nennen nie Token oder Chat.
"""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from spaces.event_management.system import budget_melden as bm

TOKEN = "123456:geheimer-token-xyz"
CHAT = "4711"
HEUTE = date(2026, 11, 14)


def _protokoll(ordner: Path, tag: date, *zeilen: dict) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    text = "".join("\n" + json.dumps(z) + "\n" for z in zeilen)
    with (ordner / f"{tag:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
        f.write(text)


WARNUNG = {"art": "warnung", "agent": "event-management-claw", "heute": 240, "grenze": 300}


def test_meldet_jede_warnung_genau_einmal(tmp_path: Path):
    ordner, zustand = tmp_path / "shim-budget", tmp_path / "zustand.json"
    _protokoll(ordner, HEUTE, {"art": "pruefung", "agent": "event-management-claw"}, WARNUNG)
    gesendet: list[str] = []
    assert bm.neue_melden(ordner, zustand, gesendet.append, HEUTE) == 1
    assert bm.neue_melden(ordner, zustand, gesendet.append, HEUTE) == 0
    assert len(gesendet) == 1
    text = gesendet[0]
    assert "event-management-claw" in text and "240" in text and "300" in text and "80 %" in text
    assert "2026-11-14" in text


def test_gestern_wird_nachgeholt_vorgestern_nicht(tmp_path: Path):
    ordner, zustand = tmp_path / "shim-budget", tmp_path / "zustand.json"
    _protokoll(ordner, date(2026, 11, 13), WARNUNG)
    _protokoll(ordner, date(2026, 11, 12), WARNUNG)
    gesendet: list[str] = []
    assert bm.neue_melden(ordner, zustand, gesendet.append, HEUTE) == 1
    assert "2026-11-13" in gesendet[0]


def test_kaputte_und_fremde_zeilen_stoeren_nicht(tmp_path: Path):
    ordner, zustand = tmp_path / "shim-budget", tmp_path / "zustand.json"
    ordner.mkdir()
    (ordner / "2026-11-14.jsonl").write_text(
        '{"art": "warn\n\n[1,2]\n{"art": "warnung"}\n' + json.dumps(WARNUNG) + "\n", encoding="utf-8")
    gesendet: list[str] = []
    assert bm.neue_melden(ordner, zustand, gesendet.append, HEUTE) == 1


def test_fehlschlag_wird_beim_naechsten_lauf_wiederholt(tmp_path: Path):
    ordner, zustand = tmp_path / "shim-budget", tmp_path / "zustand.json"
    _protokoll(ordner, HEUTE, WARNUNG)

    def kaputt(_text: str) -> None:
        raise bm.MeldeFehler("Telegram nicht erreichbar")

    with pytest.raises(bm.MeldeFehler):
        bm.neue_melden(ordner, zustand, kaputt, HEUTE)
    gesendet: list[str] = []
    assert bm.neue_melden(ordner, zustand, gesendet.append, HEUTE) == 1


def test_kaputter_zustand_sendet_nichts(tmp_path: Path):
    # Review 1: ein unlesbarer Zustand darf nicht "alles neu senden" bedeuten (fail-closed).
    ordner, zustand = tmp_path / "shim-budget", tmp_path / "zustand.json"
    _protokoll(ordner, HEUTE, WARNUNG)
    zustand.write_text("{kaputt", encoding="utf-8")
    gesendet: list[str] = []
    with pytest.raises(bm.MeldeFehler):
        bm.neue_melden(ordner, zustand, gesendet.append, HEUTE)
    assert gesendet == []


def test_nicht_schreibbarer_zustand_sendet_nichts(tmp_path: Path):
    # Review 1: erst muss der Zustand schreibbar sein, dann wird gesendet (sonst Doppel alle 5 min).
    ordner = tmp_path / "shim-budget"
    _protokoll(ordner, HEUTE, WARNUNG)
    zustand = tmp_path / "fehlt" / "zustand.json"
    gesendet: list[str] = []
    with pytest.raises(bm.MeldeFehler):
        bm.neue_melden(ordner, zustand, gesendet.append, HEUTE)
    assert gesendet == []


def test_main_zustandsfehler_exit_1_ohne_traceback(tmp_path: Path, monkeypatch, capsys):
    ordner = tmp_path / "shim-budget"
    _protokoll(ordner, date.today(), WARNUNG)
    tokendatei = tmp_path / "token"
    tokendatei.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setattr(bm, "telegram_senden", lambda *a: None)
    env = {"BUDGET_ORDNER": str(ordner), "BUDGET_ZUSTAND": str(tmp_path / "fehlt" / "z.json"),
           "BUDGET_TELEGRAM_TOKEN_FILE": str(tokendatei), "BUDGET_TELEGRAM_CHAT_ID": CHAT}
    assert bm.main(["lauf"], env) == 1
    assert "Zustand" in capsys.readouterr().err


def test_ohne_ordner_nichts_zu_tun(tmp_path: Path):
    assert bm.neue_melden(tmp_path / "fehlt", tmp_path / "z.json", lambda _t: None, HEUTE) == 0


def test_zustand_vergisst_alte_tage(tmp_path: Path):
    ordner, zustand = tmp_path / "shim-budget", tmp_path / "zustand.json"
    zustand.write_text(json.dumps(["2026-10-01|event-management-claw"]), encoding="utf-8")
    _protokoll(ordner, HEUTE, WARNUNG)
    bm.neue_melden(ordner, zustand, lambda _t: None, HEUTE)
    assert json.loads(zustand.read_text(encoding="utf-8")) == ["2026-11-14|event-management-claw"]


class _Antwort(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_telegram_senden_baut_die_anfrage_und_nennt_den_token_nie():
    gesehen = {}

    def oeffnen(anfrage, timeout):
        gesehen["url"] = anfrage.full_url
        gesehen["daten"] = json.loads(anfrage.data.decode("utf-8"))
        return _Antwort(b'{"ok": true}')

    bm.telegram_senden(TOKEN, CHAT, "Hallo", oeffnen=oeffnen)
    assert gesehen["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert gesehen["daten"] == {"chat_id": CHAT, "text": "Hallo"}

    def http_fehler(anfrage, timeout):
        raise urllib.error.HTTPError(anfrage.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(bm.MeldeFehler) as e:
        bm.telegram_senden(TOKEN, CHAT, "Hallo", oeffnen=http_fehler)
    assert TOKEN not in str(e.value) and CHAT not in str(e.value) and "401" in str(e.value)


def test_main_ohne_einstellungen_nennt_nur_namen(tmp_path: Path, capsys):
    assert bm.main(["lauf"], {}) == 2
    err = capsys.readouterr().err
    assert "BUDGET_ORDNER" in err and "BUDGET_TELEGRAM_TOKEN_FILE" in err


def test_main_sendet_ueber_telegram(tmp_path: Path, monkeypatch, capsys):
    ordner = tmp_path / "shim-budget"
    _protokoll(ordner, date.today(), WARNUNG)
    tokendatei = tmp_path / "token"
    tokendatei.write_text(TOKEN + "\r\n", encoding="utf-8")
    gesendet = []
    monkeypatch.setattr(bm, "telegram_senden", lambda t, c, text: gesendet.append((t, c, text)))
    env = {"BUDGET_ORDNER": str(ordner), "BUDGET_ZUSTAND": str(tmp_path / "z.json"),
           "BUDGET_TELEGRAM_TOKEN_FILE": str(tokendatei), "BUDGET_TELEGRAM_CHAT_ID": CHAT}
    assert bm.main(["lauf"], env) == 0
    assert len(gesendet) == 1 and gesendet[0][0] == TOKEN and gesendet[0][1] == CHAT
    aus = capsys.readouterr()
    assert TOKEN not in aus.out + aus.err and CHAT not in aus.out + aus.err
    assert "1 Warnung" in aus.err


def test_main_meldefehler_exit_1_ohne_werte(tmp_path: Path, monkeypatch, capsys):
    ordner = tmp_path / "shim-budget"
    _protokoll(ordner, date.today(), WARNUNG)
    tokendatei = tmp_path / "token"
    tokendatei.write_text(TOKEN, encoding="utf-8")

    def kaputt(*_a):
        raise bm.MeldeFehler("Telegram antwortet mit 401")

    monkeypatch.setattr(bm, "telegram_senden", kaputt)
    env = {"BUDGET_ORDNER": str(ordner), "BUDGET_ZUSTAND": str(tmp_path / "z.json"),
           "BUDGET_TELEGRAM_TOKEN_FILE": str(tokendatei), "BUDGET_TELEGRAM_CHAT_ID": CHAT}
    assert bm.main(["lauf"], env) == 1
    aus = capsys.readouterr()
    assert TOKEN not in aus.out + aus.err and "401" in aus.err
