"""Vault notes: read a vault, split and extend flat front matter, parse [[links]]."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

LINK = re.compile(r"\[\[([^\]\n]+)\]\]")
PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "none", "y", "n"}
# Rowboat's own memory files: it reads them line by line (inbox) or puts them verbatim into every chat
# prompt (user, preferences), so they must never get front matter.
ROWBOAT_OWNED = {"Agent Notes/inbox.md", "Agent Notes/user.md", "Agent Notes/preferences.md"}


class FrontMatterError(ValueError):
    """The front matter is not valid YAML or not a mapping."""


@dataclass
class Note:
    path: str                 # vault-relative, forward slashes, without ".md"
    raw: str                  # the file as read (decoded, original newlines)
    front: dict | None        # parsed front matter; None if the note has none (or it is broken)
    front_text: str           # the lines between the --- fences, "\n"-separated
    body: str                 # everything after the closing fence, "\n" newlines
    newline: str              # "\r\n" or "\n", as found in the file
    error: str | None = None  # why the front matter could not be read


def _normalise(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def split(text: str) -> tuple[dict | None, str, str]:
    """(front matter or None, raw front matter lines, body). Accepts a BOM and CRLF."""
    t = text.lstrip("﻿").replace("\r\n", "\n")
    if not t.startswith("---\n"):
        return None, "", t
    if t.startswith("---\n---\n"):
        return {}, "", t[8:]
    end = t.find("\n---\n", 3)
    if end == -1:
        if t.endswith("\n---"):
            end = len(t) - 4
        else:
            return None, "", t
    raw = t[4:end]
    body = t[end + 5:]
    try:
        data = yaml.safe_load(raw) or {}
    except (yaml.YAMLError, ValueError) as err:  # ValueError: e.g. an impossible date like 2026-02-30
        raise FrontMatterError(f"front matter is not valid YAML: {err}") from err
    if not isinstance(data, dict):
        raise FrontMatterError("front matter is not a mapping")
    return {str(k): _normalise(v) for k, v in data.items()}, raw, body


def format_value(value) -> str:
    """One YAML value as it is written into front matter: plain when safe, JSON-quoted otherwise."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return json.dumps([str(v) for v in value], ensure_ascii=False)
    text = str(value)
    if DATE.fullmatch(text) or (PLAIN.fullmatch(text) and text.lower() not in RESERVED):
        return text
    return json.dumps(text, ensure_ascii=False)


def render_front(fields: dict) -> str:
    return "\n".join(f"{key}: {format_value(value)}" for key, value in fields.items())


def note_text(fields: dict, body: str) -> str:
    return f"---\n{render_front(fields)}\n---\n{body}"


def add_keys(note: Note, fields: dict) -> str:
    """The note's text with every key of `fields` it does not have yet appended to its front matter.
    Existing lines stay first and unchanged; the file's newline style is kept."""
    existing = note.front or {}
    lines = [line for line in note.front_text.split("\n") if line.strip()] if note.front is not None else []
    lines += [f"{key}: {format_value(value)}" for key, value in fields.items() if key not in existing]
    text = "---\n" + "\n".join(lines) + "\n---\n" + note.body
    return text.replace("\n", "\r\n") if note.newline == "\r\n" else text


def link_target(raw: str) -> str:
    return raw.split("|", 1)[0].split("#", 1)[0].strip()


def links(text: str) -> list[str]:
    return [t for t in (link_target(m) for m in LINK.findall(text)) if t]


def link_value(value) -> str | None:
    """Target of a value that is exactly one [[link]], else None."""
    m = LINK.fullmatch(value.strip()) if isinstance(value, str) else None
    return link_target(m.group(1)) if m else None


def parent_target(note: Note) -> str | None:
    return link_value((note.front or {}).get("parent"))


def read_vault(vault: Path) -> tuple[dict[str, Note], set[str]]:
    """All notes (path without .md -> Note) and all file paths. Skips hidden folders such as .git, symlinks
    (a write must never leave the vault) and Rowboat's own memory files (ROWBOAT_OWNED)."""
    notes: dict[str, Note] = {}
    files: set[str] = set()
    for file in sorted(vault.rglob("*")):
        rel = file.relative_to(vault)
        if file.is_symlink() or not file.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        key = rel.as_posix()
        if key in ROWBOAT_OWNED:
            continue
        files.add(key)
        if file.suffix != ".md":
            continue
        data = file.read_bytes()
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as err:
            raw = data.decode("utf-8", errors="replace")
            notes[key[:-3]] = Note(key[:-3], raw, None, "", raw.replace("\r\n", "\n"),
                                   "\r\n" if "\r\n" in raw else "\n", f"not UTF-8: {err}")
            continue
        newline = "\r\n" if "\r\n" in raw else "\n"
        try:
            front, front_text, body = split(raw)
            error = None
        except FrontMatterError as err:
            front, front_text, body, error = None, "", raw.replace("\r\n", "\n"), str(err)
        notes[key[:-3]] = Note(key[:-3], raw, front, front_text, body, newline, error)
    return notes, files


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
