"""Tests fuer den Umzug des Rowboat-Arbeitsordners (~/.rowboat -> offload-vm:/home/debian/rowboat-data).

Die VM wird mit `FakeVm` aus den Spiegel-Tests nachgebildet (ssh -> lokale Ausfuehrung, tar per tarfile).
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rowboat_umzug as ru  # noqa: E402
import wissens_spiegel as ws  # noqa: E402
from test_wissens_spiegel import ZIEL, FakeVm, dateien, schreibe  # noqa: E402

BOT = "123456:GEHEIMER-BOT-SCHLUESSEL"
API = "sk-GEHEIMER-API-SCHLUESSEL"


def kein_pid(_pid: int) -> bool:
    return False


def pc_baum(wurzel: Path) -> None:
    """Ein kleiner, aber vollstaendiger ~/.rowboat mit allem, was nie mit darf."""
    schreibe(wurzel, "knowledge/Notes/Einkauf.md", "Milch")
    schreibe(wurzel, "knowledge/Agent Notes/2026-10-23 split (ä).md", "Umlaut")
    schreibe(wurzel, "knowledge/Projects/p.md", "Projekt")
    schreibe(wurzel, "knowledge/Welcome.md", "Hallo")
    schreibe(wurzel, "knowledge/_konflikte/2026-10-24/Notes/x.md", "alt")
    schreibe(wurzel, "knowledge/.spiegel-eingang/1/Notes/halb.md", "ha")
    schreibe(wurzel, "knowledge/Notes/Thumbs.db", "x")
    schreibe(wurzel, "knowledge/.git/HEAD", "ref: refs/heads/master")
    schreibe(wurzel, "knowledge/.git/objects/ab/cdef", "obj")
    schreibe(wurzel, "knowledge/.bubble_sync_hashes.json", "{}")
    schreibe(wurzel, "config/mcp.json", json.dumps({"mcpServers": {
        "exa": {"url": "https://mcp.exa.ai/mcp"},
        "desktop-commander": {"command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander"], "env": {"A": "1"}},
        "playwright": {"command": "npx", "args": ["@playwright/mcp"]},
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\"],
                       "env": {}},
    }}))
    schreibe(wurzel, "todo.md", "- [ ] Steuer")
    schreibe(wurzel, "todo/liste.json", "[]")
    schreibe(wurzel, "storage/sessions/2026/s1.json", "{}")
    schreibe(wurzel, "bg-tasks/t.json", "{}")
    schreibe(wurzel, "agents/a.md", "a")
    schreibe(wurzel, "skills/s/SKILL.md", "s")
    schreibe(wurzel, "projects/pr.json", "{}")
    schreibe(wurzel, "config/notes.json", "[]")
    schreibe(wurzel, "config/channels.json", json.dumps({
        "whatsapp": {"enabled": True, "allowFrom": ["49"]},
        "telegram": {"enabled": True, "botToken": BOT, "allowFrom": ["42"]},
    }))
    schreibe(wurzel, "config/knowledge_sources.json", json.dumps({"sources": [
        {"id": "gmail", "provider": "gmail", "enabled": True, "artifactDir": "gmail_sync", "syncMode": "file", "scopes": []},
    ]}))
    schreibe(wurzel, "config/server.json", json.dumps({"lanEnabled": False, "port": 1}))
    schreibe(wurzel, "config/models.json", json.dumps({
        "version": 2,
        "providers": {"openai": {"flavor": "openai", "apiKey": API},
                      "openai-compatible": {"flavor": "openai-compatible", "apiKey": "x",
                                            "baseURL": "http://127.0.0.1:8121/v1"}},
        "assistantModel": {"provider": "openai-compatible", "model": "claude-code-sonnet"},
    }))
    for nie in ru.NIE:
        if nie.endswith("/"):
            schreibe(wurzel, nie + "innen.bin", "geheim")
        else:
            schreibe(wurzel, nie, "geheim")
    schreibe(wurzel, "config/models.json.bak-1779171496013", "alt")
    schreibe(wurzel, "config/models.json.v1.bak", "alt")
    schreibe(wurzel, "logs/x.log", "log")
    schreibe(wurzel, "runs/r.jsonl", "{}")
    schreibe(wurzel, "knowledge.bak-20261013-185944/a.md", "alt")


@pytest.fixture
def umgebung(tmp_path):
    pc = tmp_path / "pc" / ".rowboat"
    vm = tmp_path / "vm daten (test)"
    pc_baum(pc)
    vm.mkdir()
    return pc, vm, tmp_path / "lokal" / "zuletzt.json"


def rel(wurzel: Path, pfade: list[Path]) -> set[str]:
    return {p.relative_to(wurzel).as_posix() for p in pfade}


def test_nie_wird_nicht_mitgenommen(tmp_path):
    pc_baum(tmp_path)
    gewaehlt = rel(tmp_path, ru.auswahl(tmp_path))
    for nie in ru.NIE:
        if nie.endswith("/"):
            assert not any(g.startswith(nie) for g in gewaehlt), nie
        else:
            assert nie not in gewaehlt, nie
    assert not any(".bak" in g for g in gewaehlt)
    assert not any(g.startswith(("knowledge/_konflikte/", "knowledge/.spiegel-eingang/")) for g in gewaehlt)
    assert not any(g.startswith(("logs/", "runs/", "knowledge.bak")) for g in gewaehlt)
    assert "knowledge/Notes/Thumbs.db" not in gewaehlt
    assert {
        "knowledge/Notes/Einkauf.md", "knowledge/Agent Notes/2026-10-23 split (ä).md", "knowledge/Welcome.md",
        "todo.md", "todo/liste.json", "storage/sessions/2026/s1.json", "bg-tasks/t.json", "agents/a.md",
        "skills/s/SKILL.md", "projects/pr.json", "config/notes.json", "config/models.json",
        "config/channels.json", "config/server.json", "config/knowledge_sources.json",
        "knowledge/.git/HEAD", "knowledge/.bubble_sync_hashes.json",  # Historie reist einmal mit
    } <= gewaehlt
    assert all(g.split("/")[0] in ru.MITNEHMEN for g in gewaehlt)


def test_gmail_sync_bleibt_draussen(umgebung):
    pc, vm, zustand = umgebung
    assert not any(g.startswith(("gmail_sync/", "calendar_sync/", "Videos/")) for g in rel(pc, ru.auswahl(pc)))
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), pid_lebt=kein_pid) == 0
    assert not (vm / "gmail_sync").exists() and not (vm / "calendar_sync").exists() and not (vm / "Videos").exists()
    quellen = json.loads((vm / "config/knowledge_sources.json").read_text(encoding="utf-8"))["sources"]
    assert [q["enabled"] for q in quellen if q["id"] == "gmail"] == [False]


def test_config_anpassen(tmp_path):
    cfg = tmp_path / "config"
    schreibe(cfg, "channels.json", json.dumps({
        "whatsapp": {"enabled": True, "allowFrom": ["49"]},
        "telegram": {"enabled": True, "botToken": BOT, "allowFrom": ["42"]},
        "anders": {"x": 1},
    }))
    schreibe(cfg, "knowledge_sources.json", json.dumps({"sources": [
        {"id": "gmail", "provider": "gmail", "enabled": True, "artifactDir": "gmail_sync", "syncMode": "file", "scopes": []},
        {"id": "fireflies-meetings", "provider": "meeting", "enabled": True,
         "artifactDir": "knowledge\\Meetings\\fireflies", "syncMode": "file", "scopes": []},
    ], "extra": 7}))
    schreibe(cfg, "server.json", json.dumps({"lanEnabled": False, "port": 1, "alt": True}))
    modelle = json.dumps({"version": 2, "providers": {
        "openai": {"flavor": "openai", "apiKey": API},
        "openai-compatible": {"flavor": "openai-compatible", "baseURL": "http://127.0.0.1:8121/v1"},
        "fern": {"flavor": "openai-compatible", "baseURL": "https://api.example.com/v1"},
    }, "deferBackgroundTasks": True})
    schreibe(cfg, "models.json", modelle)

    ru.config_anpassen(cfg)

    kanaele = json.loads((cfg / "channels.json").read_text(encoding="utf-8"))
    assert kanaele["telegram"] == {"enabled": False, "botToken": "", "allowFrom": ["42"]}
    assert kanaele["whatsapp"] == {"enabled": False, "allowFrom": ["49"]}
    assert kanaele["anders"] == {"x": 1}
    quellen = json.loads((cfg / "knowledge_sources.json").read_text(encoding="utf-8"))
    assert quellen["extra"] == 7
    assert quellen["sources"][0] == {"id": "gmail", "provider": "gmail", "enabled": False,
                                     "artifactDir": "gmail_sync", "syncMode": "file", "scopes": []}
    assert quellen["sources"][1]["enabled"] is True
    assert quellen["sources"][1]["artifactDir"] == "knowledge/Meetings/fireflies"  # Linux-Pfad auf der VM
    assert json.loads((cfg / "server.json").read_text(encoding="utf-8")) == {"lanEnabled": True, "port": 3220}
    m = json.loads((cfg / "models.json").read_text(encoding="utf-8"))
    assert API not in (cfg / "models.json").read_text(encoding="utf-8")
    assert m == {"version": 2, "providers": {  # nur die Schluessel leer, sonst unveraendert
        "openai": {"flavor": "openai", "apiKey": ""},
        "openai-compatible": {"flavor": "openai-compatible", "baseURL": "http://127.0.0.1:8121/v1"},
        "fern": {"flavor": "openai-compatible", "baseURL": "https://api.example.com/v1"},
    }, "deferBackgroundTasks": True}


def test_config_anpassen_entfernt_shell_und_datei_mcp(tmp_path):
    # Rowboats Schema (shared/src/mcp.ts) kennt kein disabled-Feld: die Server fliegen aus der Kopie.
    cfg = tmp_path / "config"
    schreibe(cfg, "mcp.json", json.dumps({"mcpServers": {
        "exa": {"url": "https://mcp.exa.ai/mcp"},
        "desktop-commander": {"command": "npx", "args": ["x"]},
        "playwright": {"command": "npx", "args": ["y"]},
        "filesystem": {"command": "npx", "args": ["z"]},
    }, "extra": 1}))
    ru.config_anpassen(cfg)
    mcp = json.loads((cfg / "mcp.json").read_text(encoding="utf-8"))
    assert mcp == {"mcpServers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}, "extra": 1}


def test_config_anpassen_ohne_mcp_json_legt_keins_an(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    ru.config_anpassen(cfg)
    assert not (cfg / "mcp.json").exists()


def test_config_anpassen_mcp_json_kaputt(tmp_path):
    cfg = tmp_path / "config"
    schreibe(cfg, "mcp.json", json.dumps({"mcpServers": []}))
    with pytest.raises(ru.ConfigFehler):
        ru.config_anpassen(cfg)


def test_umzug_mcp_kopie_ohne_shell_pc_original_unberuehrt(umgebung):
    pc, vm, zustand = umgebung
    vorher = (pc / "config/mcp.json").read_bytes()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), pid_lebt=kein_pid) == 0
    assert sorted(json.loads((vm / "config/mcp.json").read_text(encoding="utf-8"))["mcpServers"]) == ["exa"]
    assert (pc / "config/mcp.json").read_bytes() == vorher


def test_umzug_nimmt_git_einmal_mit_spiegel_ignoriert_es(umgebung):
    pc, vm, zustand = umgebung
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), pid_lebt=kein_pid) == 0
    # Rowboat braucht die Historie auf der VM
    assert (vm / "knowledge/.git/HEAD").read_bytes() == (pc / "knowledge/.git/HEAD").read_bytes()
    assert (vm / "knowledge/.bubble_sync_hashes.json").exists()
    stand = ws.zustand_lesen(zustand)
    assert not any(p.split("/")[0] == ".git" for p in stand)
    assert ".bubble_sync_hashes.json" in stand
    pc_bestand, _ = ws.bestand_lokal(pc / "knowledge")
    vm_bestand, _ = ws.bestand_vm(ZIEL, str(vm / "knowledge"), laeufer=FakeVm())
    assert ws.abgleich_planen(pc_bestand, vm_bestand, stand) == []
    # auch wenn sich danach .git auf einer Seite aendert (Rowboat committet auf der VM)
    (vm / "knowledge/.git/HEAD").write_text("ref: refs/heads/anders", encoding="utf-8")
    vm_bestand, _ = ws.bestand_vm(ZIEL, str(vm / "knowledge"), laeufer=FakeVm())
    assert ws.abgleich_planen(pc_bestand, vm_bestand, stand) == []


def test_umzug_nutzt_sha_des_spiegels():
    assert not hasattr(ru, "_sha")


def test_config_anpassen_legt_fehlenden_gmail_eintrag_an(tmp_path):
    cfg = tmp_path / "config"
    schreibe(cfg, "knowledge_sources.json", json.dumps({"sources": [
        {"id": "granola-meetings", "provider": "meeting", "enabled": True, "artifactDir": "x", "syncMode": "file", "scopes": []},
    ]}))
    ru.config_anpassen(cfg)
    quellen = json.loads((cfg / "knowledge_sources.json").read_text(encoding="utf-8"))["sources"]
    assert [q["id"] for q in quellen] == ["granola-meetings", "gmail"]
    assert quellen[1]["enabled"] is False and quellen[1]["provider"] == "gmail"
    kanaele = json.loads((cfg / "channels.json").read_text(encoding="utf-8"))
    assert kanaele["telegram"]["enabled"] is False and kanaele["telegram"]["botToken"] == ""
    assert kanaele["whatsapp"]["enabled"] is False


@pytest.mark.parametrize("url", ["http://127.0.0.1:8114/v1", "http://localhost/v1", "http://localhost:3000"])
def test_models_mit_anderem_lokalen_port_bricht_ab(tmp_path, url):
    cfg = tmp_path / "config"
    schreibe(cfg, "models.json", json.dumps({"providers": {"p": {"apiKey": API, "baseURL": url}}}))
    with pytest.raises(ru.ConfigFehler) as info:
        ru.config_anpassen(cfg)
    assert "8121" in str(info.value) and API not in str(info.value)


def test_trocken_uebertraegt_nichts(umgebung, caplog):
    pc, vm, zustand = umgebung
    vorher_pc = dateien(pc)
    fake = FakeVm()
    with caplog.at_level(logging.INFO):
        assert ru.umzug(pc, ZIEL, str(vm), zustand, trocken=True, laeufer=fake, pid_lebt=kein_pid) == 0
    assert dateien(vm) == {} and list(vm.iterdir()) == []
    assert not zustand.exists()
    assert len(fake.aufrufe) == 1 and fake.aufrufe[0][0][-2:] == ["python3", "-"]  # nur lesen
    assert dateien(pc) == vorher_pc
    assert "Trockenlauf" in caplog.text and "knowledge" in caplog.text


def test_umzug_nimmt_keine_api_schluessel_mit(umgebung):
    pc, vm, zustand = umgebung
    vorher = (pc / "config/models.json").read_bytes()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), pid_lebt=kein_pid) == 0
    kopie = (vm / "config/models.json").read_text(encoding="utf-8")
    assert API not in kopie
    anbieter = json.loads(kopie)["providers"]
    assert {n: x["apiKey"] for n, x in anbieter.items()} == {"openai": "", "openai-compatible": ""}
    assert anbieter["openai-compatible"]["baseURL"] == "http://127.0.0.1:8121/v1"
    assert json.loads(kopie)["assistantModel"] == {"provider": "openai-compatible", "model": "claude-code-sonnet"}
    assert (pc / "config/models.json").read_bytes() == vorher  # PC-Original byte-gleich


def test_umzug_uebertraegt_passt_config_an_und_schreibt_zustand(umgebung):
    pc, vm, zustand = umgebung
    vorher_pc = dateien(pc)
    fake = FakeVm()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=kein_pid) == 0
    auf_vm = dateien(vm)
    erwartet = rel(pc, ru.auswahl(pc))
    assert set(auf_vm) == erwartet
    for name in erwartet - {"config/channels.json", "config/knowledge_sources.json", "config/server.json",
                            "config/mcp.json", "config/models.json"}:
        assert auf_vm[name] == (pc / name).read_bytes(), name
    assert json.loads(auf_vm["config/server.json"]) == {"lanEnabled": True, "port": 3220}
    tg = json.loads(auf_vm["config/channels.json"])["telegram"]
    assert tg["enabled"] is False and tg["botToken"] == ""
    assert dateien(pc) == vorher_pc  # PC unveraendert, nichts geloescht, Live-Config nicht angefasst
    assert not (vm / ru.EINGANG).exists()
    # Zustand des Spiegels = uebertragener knowledge-Bestand, wie ihn der Spiegel selbst sieht
    stand = ws.zustand_lesen(zustand)
    pc_bestand, _ = ws.bestand_lokal(pc / "knowledge")
    vm_bestand, _ = ws.bestand_vm(ZIEL, str(vm / "knowledge"), laeufer=FakeVm())
    assert {p: e.sha256 for p, e in stand.items()} == {p: e.sha256 for p, e in pc_bestand.items()}
    assert {p: e.sha256 for p, e in vm_bestand.items()} == {p: e.sha256 for p, e in pc_bestand.items()}
    assert ws.abgleich_planen(pc_bestand, vm_bestand, stand) == []
    # config/ geht mit Modus 0600 hoch
    upload = next(kw["input"] for cmd, kw in fake.aufrufe if "tar -C" in " ".join(cmd) and "-xf" in " ".join(cmd))
    with tarfile.open(fileobj=io.BytesIO(upload), mode="r:", encoding="utf-8") as tf:
        modi = {m.name: m.mode for m in tf.getmembers()}
    assert all(m == 0o600 for n, m in modi.items() if n.startswith("config/"))
    assert not (zustand.parent / ws.SPERRDATEI).exists()  # Sperre wieder frei


def test_pruefsummen_abweichung_bricht_ab(umgebung, caplog):
    pc, vm, zustand = umgebung
    fake = FakeVm()

    def nach_upload_veraendern(n: int) -> None:
        if n == len(fake.aufrufe) and n == 4:  # 1 Ziel pruefen, 2 tar, 3 einsetzen, 4 Pruefsummen
            (vm / "knowledge/Notes/Einkauf.md").write_text("verfaelscht", encoding="utf-8")

    fake.vor_aufruf = nach_upload_veraendern
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=kein_pid) == 4
    assert len(fake.aufrufe) == 4
    assert not zustand.exists()
    assert "knowledge/Notes/Einkauf.md" in caplog.text
    assert (pc / "knowledge/Notes/Einkauf.md").read_text(encoding="utf-8") == "Milch"


@pytest.mark.parametrize("besetzt", ["knowledge/Notes/alt.md", "todo.md"])
def test_belegtes_ziel_wird_verweigert(umgebung, besetzt):
    pc, vm, zustand = umgebung
    schreibe(vm, besetzt, "live")
    fake = FakeVm()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=kein_pid) == 3
    assert len(fake.aufrufe) == 1
    assert set(dateien(vm)) == {besetzt}
    assert not zustand.exists()


def test_vorhandene_zieldatei_wird_nicht_ueberschrieben(umgebung):
    pc, vm, zustand = umgebung
    schreibe(vm, "config/models.json", "vm")
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), pid_lebt=kein_pid) == 3
    assert dateien(vm) == {"config/models.json": b"vm"}


def test_fehlender_vm_ordner_wird_verweigert(umgebung):
    pc, vm, zustand = umgebung
    vm.rmdir()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), pid_lebt=kein_pid) == 3
    assert not vm.exists()


@pytest.mark.parametrize("datei, inhalt", [
    ("server.lock", "4711"),
    ("embed.json", json.dumps({"v": 1, "port": 1, "token": "t", "pid": 4711})),
])
def test_laufendes_pc_rowboat_wird_verweigert(umgebung, datei, inhalt):
    pc, vm, zustand = umgebung
    (pc / "server.lock").write_text("1", encoding="ascii")
    (pc / "embed.json").write_text(json.dumps({"pid": 2}), encoding="utf-8")
    (pc / datei).write_text(inhalt, encoding="utf-8")
    fake = FakeVm()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=lambda pid: pid == 4711) == 3
    assert dateien(vm) == {}
    assert ru.rowboat_laeuft(pc, pid_lebt=lambda pid: pid == 4711)
    assert ru.rowboat_laeuft(pc, pid_lebt=kein_pid) == []  # verwaiste Sperren blockieren nicht


def test_laufender_spiegel_haelt_den_umzug_ab(umgebung):
    pc, vm, zustand = umgebung
    zustand.parent.mkdir(parents=True)
    (zustand.parent / ws.SPERRDATEI).write_text(str(os.getpid()), encoding="ascii")
    fake = FakeVm()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=kein_pid) == 3
    assert fake.aufrufe == []
    assert (zustand.parent / ws.SPERRDATEI).exists()  # fremde Sperre bleibt


def test_falscher_shim_port_bricht_vor_der_vm_ab(umgebung):
    pc, vm, zustand = umgebung
    schreibe(pc, "config/models.json", json.dumps({"providers": {"p": {"baseURL": "http://127.0.0.1:8114/v1"}}}))
    fake = FakeVm()
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=kein_pid) == 3
    assert fake.aufrufe == [] and dateien(vm) == {}


def test_vm_nicht_erreichbar(umgebung):
    pc, vm, zustand = umgebung
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(fehler_bei=1), pid_lebt=kein_pid) == 2
    assert dateien(vm) == {} and not zustand.exists()


def test_abgerissener_upload_laesst_kein_halbes_ziel(umgebung):
    pc, vm, zustand = umgebung
    assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(upload_abbrechen=True), pid_lebt=kein_pid) == 2
    assert dateien(vm) == {} and not zustand.exists()


def test_keine_geheimnisse_in_argv_oder_log(umgebung, caplog):
    pc, vm, zustand = umgebung
    fake = FakeVm()
    with caplog.at_level(logging.DEBUG):
        assert ru.umzug(pc, ZIEL, str(vm), zustand, laeufer=fake, pid_lebt=kein_pid) == 0
    for cmd, _ in fake.aufrufe:
        assert not any(BOT in t or API in t or "Einkauf" in t for t in cmd)
    assert BOT not in caplog.text and API not in caplog.text
    assert BOT.encode() not in (vm / "config/channels.json").read_bytes()


def test_main_vorgaben(monkeypatch, tmp_path):
    gesehen = {}

    def fake_umzug(pc_wurzel, ssh_ziel, vm_wurzel, zustand, trocken=False, **_):
        gesehen.update(pc=pc_wurzel, ssh=ssh_ziel, vm=vm_wurzel, zustand=zustand, trocken=trocken)
        return 0

    monkeypatch.setattr(ru, "umzug", fake_umzug)
    monkeypatch.setattr(ru, "_log_einrichten", lambda root: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ru.main(["--trocken"]) == 0
    assert gesehen == {"pc": Path.home() / ".rowboat", "ssh": "offload-vm", "vm": "/home/debian/rowboat-data",
                       "zustand": tmp_path / "VibeMind" / "wissens_spiegel" / "zuletzt.json", "trocken": True}
    assert ws.zustand_standard() == gesehen["zustand"]
