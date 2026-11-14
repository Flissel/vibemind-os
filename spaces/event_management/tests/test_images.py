"""Struktur der Image-Dateien (Etappe 4, Task 1). Nur Textpruefung, kein Docker-Aufruf."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

RAUM = Path(__file__).resolve().parent.parent
SYSTEM_DOCKERFILE = RAUM / "Dockerfile.system"
WEB_DOCKERFILE = RAUM / "web" / "Dockerfile"
IGNORE_SYSTEM = RAUM / ".dockerignore"
IGNORE_WEB = RAUM / "web" / ".dockerignore"
NEXT_CONFIG = RAUM / "web" / "next.config.ts"
COMPOSE = RAUM / "docker-compose.yml"


def _zeilen(pfad: Path) -> list[str]:
    return [z.strip() for z in pfad.read_text(encoding="utf-8").splitlines()]


def _basen(pfad: Path) -> list[str]:
    return [z.split()[1] for z in _zeilen(pfad) if z.upper().startswith("FROM ")]


def test_dockerfile_system_basis_gepinnt() -> None:
    basen = _basen(SYSTEM_DOCKERFILE)
    assert basen
    for b in basen:
        assert "@sha256:" in b or re.search(r":\d+\.\d+\.\d+(-[a-z]+)?$", b), b
        assert "latest" not in b


def test_dockerfile_web_basis_gepinnt() -> None:
    for b in _basen(WEB_DOCKERFILE):
        assert "@sha256:" in b or re.search(r":\d+\.\d+\.\d+(-[a-z]+)?$", b), b
        assert "latest" not in b


def test_dockerfiles_laufen_nicht_als_root() -> None:
    for pfad in (SYSTEM_DOCKERFILE, WEB_DOCKERFILE):
        users = [z.split(None, 1)[1].strip() for z in _zeilen(pfad) if z.upper().startswith("USER ")]
        assert users, pfad
        letzter = users[-1].split(":")[0]
        assert letzter not in ("root", "0"), pfad


def test_keine_geheimnisse_im_kontext() -> None:
    pflicht = ["laufzeit/", "werte.local.json", "*.env", "build/", "node_modules/",
               ".next/", "tests/", ".superpowers/"]
    for pfad in (IGNORE_SYSTEM, IGNORE_WEB):
        eintraege = set(_zeilen(pfad))
        for p in pflicht:
            assert p in eintraege or "**/" + p in eintraege, (pfad.name, p)


def test_web_rewrite_aus_build_arg() -> None:
    assert "process.env.EVENT_API_URL" in NEXT_CONFIG.read_text(encoding="utf-8")
    text = WEB_DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ARG EVENT_API_URL", text, re.M)
    # ENV im Build-Stage, damit `next build` den Wert sieht.
    assert re.search(r"^ENV EVENT_API_URL=\$\{?EVENT_API_URL\}?", text, re.M)


def test_web_standalone() -> None:
    assert re.search(r"output:\s*'standalone'", NEXT_CONFIG.read_text(encoding="utf-8"))
    text = WEB_DOCKERFILE.read_text(encoding="utf-8")
    assert ".next/standalone" in text
    assert ".next/static" in text


def test_compose_tags_pflicht() -> None:
    dienste = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    erwartet = {"event-api": "EVENT_SYSTEM_TAG", "event-mcp": "EVENT_SYSTEM_TAG",
                "event-web": "EVENT_WEB_TAG"}
    for name, var in erwartet.items():
        image = dienste[name]["image"]
        m = re.fullmatch(r"event-management/(system|web):\$\{" + var + r":\?([^}]*)\}", image)
        assert m, (name, image)
        assert m.group(2) and "-" not in m.group(2), image


def test_compose_build_abschnitte() -> None:
    dienste = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert dienste["event-api"]["build"]["dockerfile"] == "Dockerfile.system"
    assert dienste["event-web"]["build"]["context"] == "./web"
    assert dienste["event-web"]["build"]["args"]["EVENT_API_URL"] == "http://event-api:8170"
