"""Skills (OpenClaw-Format): SKILL.md lesen, Befehlsnamen bilden, /befehl aufloesen.

Ein Skill ist ein Ordner `skills/<name>/SKILL.md` mit einzeiligem Frontmatter
(`name`, `description`, optional `user-invocable`, `metadata` als JSON). Zu einem Chat
gehoert ein Skill nur ueber `metadata.vibemind.chats`; ohne diesen Eintrag wird er geladen
und geprueft, ist aber in keinem Chat ein Befehl. Leitfaden: skills/README.md.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_ORDNER = Path(__file__).resolve().parent.parent / "skills"

# Eingebaute Befehle des OpenClaw-Gateways (OpenClaw-Doku "Slash commands"). Ein Skill mit
# so einem Befehlsnamen wuerde vom Gateway verschluckt oder ueberdeckt.
RESERVIERT_OPENCLAW = frozenset(
    "help commands tools skill status tasks approve context btw export tts whoami session "
    "subagents acp focus agents kill steer config mcp plugins debug usage stop restart "
    "activation send reset new compact think verbose trace fast reasoning elevated exec "
    "model models queue bash".split()
)
# Befehle des Telegram-Bots (workers/telegram_bot.py).
RESERVIERT_TELEGRAM = frozenset("neu new reset start hilfe help".split())
RESERVIERT = RESERVIERT_OPENCLAW | RESERVIERT_TELEGRAM


class SkillFehler(ValueError):
    """Skill-Datei unbrauchbar oder Befehl nicht erlaubt."""


@dataclass(frozen=True)
class Skill:
    name: str
    befehl: str
    beschreibung: str
    chats: tuple[str, ...]
    schritte: int | None
    anweisung: str
    pfad: Path
    sichtbar: bool = True  # user-invocable


def befehlsname(name: str) -> str:
    """OpenClaw-Regel: klein, Zeichen ausser a-z 0-9 _ werden _, Rand-_ weg, hoechstens 32."""
    sauber = re.sub(r"[^a-z0-9_]", "_", name.strip().lower())
    sauber = re.sub(r"_+", "_", sauber).strip("_")
    return sauber[:32].strip("_")


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Einzeilige `schluessel: wert`-Zeilen zwischen `---`; Rest ist die Anweisung."""
    text = text.lstrip("﻿")
    zeilen = text.splitlines()
    if not zeilen or zeilen[0].strip() != "---":
        raise SkillFehler("Kein Frontmatter (erste Zeile muss --- sein)")
    kopf: dict[str, str] = {}
    for i, zeile in enumerate(zeilen[1:], start=1):
        if zeile.strip() == "---":
            return kopf, "\n".join(zeilen[i + 1:]).strip()
        if ":" in zeile and not zeile.startswith((" ", "\t", "#")):
            schluessel, _, wert = zeile.partition(":")
            kopf[schluessel.strip()] = wert.strip()
    raise SkillFehler("Frontmatter nicht geschlossen (zweites --- fehlt)")


def _lesen(pfad: Path) -> Skill:
    try:
        kopf, anweisung = frontmatter(pfad.read_text(encoding="utf-8"))
    except SkillFehler as e:
        raise SkillFehler(f"{pfad}: {e}") from None
    name, beschreibung = kopf.get("name", ""), kopf.get("description", "")
    if not name or not beschreibung:
        raise SkillFehler(f"{pfad}: name und description sind Pflicht")
    befehl = befehlsname(name)
    if not befehl:
        raise SkillFehler(f"{pfad}: aus dem Namen {name!r} wird kein Befehl")
    if befehl in RESERVIERT:
        raise SkillFehler(f"{pfad}: Befehl /{befehl} ist reserviert (OpenClaw/Telegram)")
    chats: tuple[str, ...] = ()
    schritte: int | None = None
    roh = kopf.get("metadata", "")
    if roh:
        try:
            meta = json.loads(roh)
        except json.JSONDecodeError:
            raise SkillFehler(f"{pfad}: metadata ist kein gueltiges JSON") from None
        vm = meta.get("vibemind", {}) if isinstance(meta, dict) else {}
        if not isinstance(vm, dict):
            raise SkillFehler(f"{pfad}: metadata.vibemind muss ein Objekt sein")
        c = vm.get("chats", [])
        if not isinstance(c, list) or not all(isinstance(x, str) for x in c):
            raise SkillFehler(f"{pfad}: metadata.vibemind.chats muss eine Liste von Texten sein")
        chats = tuple(c)
        s = vm.get("schritte")
        schritte = s if isinstance(s, int) and not isinstance(s, bool) else None
    return Skill(
        name=name, befehl=befehl, beschreibung=beschreibung, chats=chats, schritte=schritte,
        anweisung=anweisung, pfad=pfad, sichtbar=kopf.get("user-invocable", "true").lower() != "false",
    )


def laden(ordner: Path = SKILLS_ORDNER) -> list[Skill]:
    """Alle `*/SKILL.md`, nach Name sortiert. Doppelte Befehle sind ein Fehler, nie ein stilles `_2`."""
    skills = sorted((_lesen(p) for p in ordner.glob("*/SKILL.md")), key=lambda s: s.name)
    gesehen: dict[str, Skill] = {}
    for s in skills:
        if s.befehl in gesehen:
            raise SkillFehler(f"Befehl /{s.befehl} doppelt: {gesehen[s.befehl].pfad} und {s.pfad}")
        gesehen[s.befehl] = s
    return skills


def befehle(chat: str, ordner: Path = SKILLS_ORDNER) -> list[Skill]:
    return [s for s in laden(ordner) if chat in s.chats and s.sichtbar]


def aufloesen(text: str, chat: str, ordner: Path = SKILLS_ORDNER) -> tuple[Skill, str] | None:
    """`/<befehl> rest` oder `/skill <name> rest` -> (Skill, rest). Kein Treffer -> None."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    verfuegbar = befehle(chat, ordner)
    # Zeilenumbruch nach dem Befehl zaehlt wie Leerraum
    m = re.match(r"(\S+)\s*(.*)", text[1:], re.DOTALL)
    if not m:
        return None
    wort, rest = m.group(1).lower(), m.group(2)
    if wort == "skill":
        # Bindestrich, Unterstrich und Leerraum gelten als gleich; laengster Name zuerst
        for s in sorted(verfuegbar, key=lambda s: -len(s.name)):
            teile = [re.escape(t) for t in re.split(r"[-_\s]+", s.name.strip()) if t]
            if not teile:
                continue
            treffer = re.match(r"[-_\s]+".join(teile) + r"(?=\s|$)\s*(.*)", rest, re.DOTALL | re.IGNORECASE)
            if treffer:
                return s, treffer.group(1).strip()
        return None
    for s in verfuegbar:
        if s.befehl == wort:
            return s, rest.strip()
    return None
