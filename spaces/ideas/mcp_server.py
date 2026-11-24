"""Versioned MCP server for the deterministic Ideas and Bubbles operations.

OpenFang starts this server over stdio.  It deliberately has no local
Supabase defaults and no LLM/provider dependency: every tool call requires
the Proxmox Supabase endpoint and service-role credential from its process
environment.
"""

from __future__ import annotations

import contextvars
import datetime
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping


SERVER_NAME = "spaces-ideas"
SERVER_VERSION = "1.3.0"
PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_SECONDS = 15
BUBBLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
IDEA_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
EDGE_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$")
CHANGE_SOURCES = ("agent", "hand", "zurueck")
# Ab hier loescht bubble_delete mit_karten nicht in einem Zug (das Gehirn sagt es ehrlich).
BUBBLE_DELETE_MAX_KARTEN = 200
CHANGE_ERROR_CODES = ("aenderung_konflikt", "aenderung_unbekannt", "aenderung_nicht_zuruecknehmbar")
# Nur diese Meldungen von ideen_zurueck duerfen woertlich an den Aufrufer zurueck.
CHANGE_ERROR_PATTERN = re.compile(
    r"^(?:" + "|".join(CHANGE_ERROR_CODES) + r"): [A-Za-z0-9._:-]{1,200}(?: [A-Za-z0-9._:-]{1,200})?$"
)
CHANGE_LIST_ROW_CAP = 500
CHANGE_LIST_ENTRY_CAP = 20
_ID_SCHEMA_PATTERN = "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$"
# Dieselben Formate wie in brain/the_brain/core/supabase_ideas_ops.py (_FORMAT_MAP).
IDEA_FORMATS = (
    "note", "table", "action_list", "pros_cons", "hierarchy", "technical_specs",
    "kanban", "mindmap", "swot", "user_story", "flowchart",
)
ARRANGE_ORDERS = ("position", "title", "score")
ARRANGE_NODE_CAP = 100
ARRANGE_CELL_WIDTH = 240   # Karte 200 breit + 40 Abstand
ARRANGE_CELL_HEIGHT = 140  # Karte 100 hoch + 40 Abstand
AUTO_LINK_THRESHOLD = 0.30  # wie auto_link_op im Gehirn
AUTO_LINK_NODE_CAP = 500  # reine Rechnung auf Titeln (125 000 Paare), schreibt hoechstens 'limit' Kanten
AUTO_LINK_ID_CHUNK = 100  # so viele Kennungen je Abfrage der vorhandenen Kanten
IDEA_PREVIEW_CHARS = 200  # idea_list zeigt so viele Zeichen des Inhalts; alles Weitere ueber idea_get
IDEA_COUNT_CAP = 5000  # bis hierhin zaehlt idea_list die wahre Gesamtzahl (nur Kennungen)
IDEA_LIST_SHORT_SELECT ="id,title,content,node_type,x,y,linked_idea_id,metadata,content_json"
REASON_ENTRY_CAP = 50 # so viele Begruendungen haelt eine Bubble, die aeltesten fallen weg
GROUP_MAX = 12  # so viele Gruppen nimmt idea_group
GROUP_NAME_CHARS = 60
GROUP_ROWS = 10  # so viele Karten untereinander; eine groessere Gruppe bekommt eine weitere Spalte
GROUP_HEAD_KEY = "gruppen_kopf"  # metadata der Ueberschrift-Karten von idea_group

TOOLS: list[dict[str, Any]] = [
    {
        "name": "bubble_list",
        "description": "List top-level Idea-space bubbles.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100}},
        },
    },
    {
        "name": "bubble_get",
        "description": "Get a top-level bubble by its durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "bubble_create",
        "description": "Create a top-level bubble in the Ideas table.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "description": {"type": "string", "default": ""},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["title"],
        },
    },
    {
        "name": "bubble_update",
        "description": "Update a top-level bubble by durable id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id"],
        },
    },
    {
        "name": "bubble_delete",
        "description": (
            "Delete one top-level bubble by durable id. With 'mit_karten' its canvas ideas (and their "
            "connections) are deleted too, in the same change; more than 200 cards: nothing is deleted "
            "(too_many_cards)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1}, "mit_karten": {"type": "boolean"}},
            "required": ["id"],
        },
    },
    {
        "name": "bubble_promote",
        "description": (
            "Promote one canonical top-level bubble into a project. Only a bubble that passes the "
            "promotion gate (no empty or loose cards, a rating, a white paper) is promoted; the project "
            "keeps a fixed hand-over of white paper, cards and connections in metadata.uebergabe."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
            },
            "required": ["bubble_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_list",
        "description": (
            "List canvas ideas, optionally scoped to a bubble id, top left first. Short by default: an "
            "object {karten, gezeigt, gesamt, gekuerzt}; each card has id, title, format, score, position, "
            "the first characters of the text and a readable 'karte'. 'gesamt' is the true number of ideas, "
            "counted even when the list is cut at 'limit'. 'full': true returns whole rows as a plain list; "
            "for the whole text of one idea use idea_read or idea_get."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100},
                "full": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "idea_get",
        "description": "Get one canvas idea by durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "idea_read",
        "description": (
            "Read one canvas idea the way a person reads it: German title and kind, the whole content as "
            "sections of plain lines (no field names, no JSON), its rating and its connections by title. "
            "Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_create",
        "description": "Create a canvas idea inside a durable bubble id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "content": {"type": "string", "default": ""},
                "node_type": {"type": "string", "default": "note"},
                "x": {"type": "integer", "default": 0},
                "y": {"type": "integer", "default": 0},
                # Vorspeicher: die App darf die Kennung vorgeben, damit die vorgemerkte
                # Karte und die gespeicherte Zeile dieselbe Kennung tragen.
                "id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
            },
            "required": ["bubble_id", "title"],
        },
    },
    {
        "name": "idea_update",
        "description": (
            "Update a canvas idea by durable id. 'content_append' adds text after the stored text "
            "(a text that only repeats the title counts as empty); it excludes 'content'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "content_append": {"type": "string", "minLength": 1, "maxLength": 20000},
                "node_type": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "idea_delete",
        "description": "Delete a canvas idea and its incident edges by durable id.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}}, "required": ["id"]},
    },
    {
        "name": "idea_connect",
        "description": "Create a durable canvas edge between two canvas idea ids.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
                "to_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$",
                },
                "edge_type": {
                    "type": "string",
                    "default": "related",
                    "minLength": 1,
                    "maxLength": 64,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9:_-]{0,63}$",
                },
            },
            "required": ["from_id", "to_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_disconnect",
        "description": "Delete one durable canvas edge by id.",
        "inputSchema": {"type": "object", "properties": {"edge_id": {"type": "string", "minLength": 1}}, "required": ["edge_id"]},
    },
    {
        "name": "idea_format",
        "description": (
            "Switch how one canvas idea is displayed (table, action_list, pros_cons, ...). "
            "The text is kept; 'revert' restores the format before the last switch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "format": {"type": "string", "enum": [*IDEA_FORMATS, "revert"]},
            },
            "required": ["id", "format"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_arrange",
        "description": (
            "Arrange all canvas ideas of one bubble in a grid. 'order' picks the sequence: "
            "position (keep the rough current order), title, or score (highest first)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "order": {"type": "string", "enum": list(ARRANGE_ORDERS), "default": "position"},
                "columns": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["bubble_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_auto_link",
        "description": (
            "Connect canvas ideas of one bubble whose titles share enough words "
            "(word overlap, no model). Existing connections are kept."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
            },
            "required": ["bubble_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_rate",
        "description": (
            "Store a rating from 0 to 100 on one canvas idea. The card shows the number only; "
            "a reason goes to bewertung_begruenden."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["id", "score"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bewertung_begruenden",
        "description": (
            "Store the reason for a rating apart from the cards: one entry on the bubble with the text "
            "and links to the canvas ideas it refers to. The ideas must belong to that bubble."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                "idea_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                    "maxItems": 20,
                    "uniqueItems": True,
                    "default": [],
                },
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["bubble_id", "text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bewertung_entfernen",
        "description": (
            "Remove one rating reason from the bubble. 'nummer' counts as shown, newest first; with "
            "'idea_id' it counts only the reasons that link to that canvas idea."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "nummer": {"type": "integer", "minimum": 1, "maximum": REASON_ENTRY_CAP},
                "idea_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
            },
            "required": ["bubble_id", "nummer"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bubble_rate",
        "description": "Store a rating from 0 to 100 on one top-level bubble.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["id", "score"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_document",
        "description": (
            "Write a document card (summary or white paper) into a bubble: creates it under the given id, "
            "or replaces title, text and sources of the document card with that id. Written by the brain "
            "after it checked the model's answer; the text is Markdown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "bubble_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_SCHEMA_PATTERN},
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "art": {"type": "string", "enum": ["zusammenfassung", "white_paper"]},
                "content": {"type": "string", "minLength": 1, "maxLength": 300000},
                "quellen": {"type": "array", "maxItems": 500,
                            "items": {"type": "string", "pattern": _ID_SCHEMA_PATTERN}},
            },
            "required": ["id", "bubble_id", "title", "art", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bubble_wurzel",
        "description": (
            "Set or clear the root card of a bubble (metadata.wurzel). A set root takes precedence over "
            "the computed one when a white paper is built. Without 'id': clear it. Undoable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "pattern": _ID_SCHEMA_PATTERN},
                "id": {"type": "string", "pattern": _ID_SCHEMA_PATTERN},
            },
            "required": ["bubble_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "idea_group",
        "description": (
            "Lay out the cards of one bubble in groups: one column per group with a heading card on top "
            "(metadata.gruppen_kopf), the cards below. Heading cards of an earlier grouping are reused or "
            "removed. Cards not named stay where they are. 'verbinden': also connect each heading card to "
            "its cards. One undoable change."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bubble_id": {"type": "string", "pattern": _ID_SCHEMA_PATTERN},
                "gruppen": {
                    "type": "array", "minItems": 1, "maxItems": GROUP_MAX,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": GROUP_NAME_CHARS},
                            "ids": {"type": "array", "minItems": 1, "maxItems": ARRANGE_NODE_CAP,
                                    "items": {"type": "string", "pattern": _ID_SCHEMA_PATTERN}},
                        },
                        "required": ["name", "ids"],
                        "additionalProperties": False,
                    },
                },
                "verbinden": {"type": "boolean", "default": False},
            },
            "required": ["bubble_id", "gruppen"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aenderung_liste",
        "description": (
            "List the most recent recorded changes to ideas and bubbles, newest first, "
            "grouped the way they can be undone (one plan = one group). Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "quelle": {"type": "string", "enum": ["agent", "hand", "zurueck"]},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "aenderung_zurueck",
        "description": (
            "Undo one recorded change to ideas and bubbles as a whole (zurueck, undo). "
            "Without 'gruppe': the most recent change made by an agent that is still open. "
            "With 'gruppe': exactly that change, as named by aenderung_liste. "
            "Fails without any partial effect if a row was changed again afterwards."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"gruppe": {"type": "string", "minLength": 1, "maxLength": 200}},
            "additionalProperties": False,
        },
    },
]


# "Zurueck": Schreibende Werkzeuge nehmen wahlweise die Kennung der Aenderung entgegen
# (das Gehirn gibt seine Plan-Kennung mit). Sie reist als Kopf X-Aenderung-Id zur
# Datenbank; dort fassen die Ausloeser alle Schreibvorgaenge mit derselben Kennung zu
# einer Aenderung zusammen, die aenderung_zurueck in einem Zug zuruecknimmt. Ohne
# Kennung gilt ein Schreibvorgang wie bisher als Handaenderung.
CHANGE_WRITE_TOOLS = frozenset({
    "bubble_create", "bubble_update", "bubble_delete", "bubble_promote",
    "idea_create", "idea_update", "idea_delete", "idea_connect", "idea_disconnect",
    "idea_format", "idea_arrange", "idea_auto_link", "idea_rate", "bubble_rate",
    "bewertung_begruenden", "bewertung_entfernen", "idea_document", "bubble_wurzel", "idea_group",
})
for _tool in TOOLS:
    if _tool["name"] in CHANGE_WRITE_TOOLS:
        _tool["inputSchema"].setdefault("properties", {})["aenderung_id"] = {
            "type": "string", "minLength": 1, "maxLength": 200,
        }
del _tool
_CHANGE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("aenderung_id", default=None)


class ToolError(Exception):
    """Expected, safe-to-return tool errors."""


def _required_string(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"invalid_arguments: '{name}' must be a non-empty string")
    return value.strip()


def _canonical_bubble_id(arguments: Mapping[str, Any]) -> str:
    """Require the stored bubble id verbatim; names and aliases are not accepted."""
    value = arguments.get("bubble_id")
    if not isinstance(value, str) or not value or value != value.strip():
        raise ToolError("invalid_arguments: 'bubble_id' must be a canonical non-empty string")
    if set(arguments) != {"bubble_id"}:
        raise ToolError("invalid_arguments: bubble_promote only accepts 'bubble_id'")
    if BUBBLE_ID_PATTERN.fullmatch(value) is None:
        raise ToolError("invalid_arguments: 'bubble_id' must be a canonical durable id")
    return value


def _canonical_idea_connect_arguments(arguments: Mapping[str, Any]) -> tuple[str, str, str]:
    """Validate the opaque edge contract without rewriting caller input."""
    allowed = {"from_id", "to_id", "edge_type"}
    if not set(arguments).issubset(allowed):
        raise ToolError("invalid_arguments: idea_connect only accepts from_id, to_id, and edge_type")

    def _opaque_id(name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value or value != value.strip():
            raise ToolError(f"invalid_arguments: '{name}' must be a canonical durable id")
        if IDEA_ID_PATTERN.fullmatch(value) is None:
            raise ToolError(f"invalid_arguments: '{name}' must be a canonical durable id")
        return value

    from_id = _opaque_id("from_id")
    to_id = _opaque_id("to_id")
    if from_id == to_id:
        raise ToolError("invalid_arguments: an idea cannot connect to itself")
    edge_type = arguments.get("edge_type", "related")
    if (
        not isinstance(edge_type, str)
        or not edge_type
        or edge_type != edge_type.strip()
        or EDGE_TYPE_PATTERN.fullmatch(edge_type) is None
    ):
        raise ToolError("invalid_arguments: 'edge_type' must be a canonical safe token")
    return from_id, to_id, edge_type


def _read_exact_canvas_node(durable_id: str, argument_name: str) -> None:
    """Require one exact node readback before any edge lookup or mutation."""
    try:
        response = _request(
            "GET",
            "canvas_nodes",
            params={"select": "id", "id": f"eq.{durable_id}", "limit": "1"},
        )
    except ToolError as exc:
        raise ToolError(f"idea_connect_node_unverified: {argument_name}") from exc
    if (
        not isinstance(response, list)
        or len(response) != 1
        or not isinstance(response[0], Mapping)
        or response[0].get("id") != durable_id
    ):
        raise ToolError(f"idea_connect_node_unverified: {argument_name}")


def _normalize_connected_edge(
    response: Any,
    *,
    from_id: str,
    to_id: str,
    edge_type: str,
    error: str,
    allow_reverse: bool,
    require_edge_type_match: bool,
) -> dict[str, str]:
    """Accept exactly one attested edge row and expose only its contract fields."""
    if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], Mapping):
        raise ToolError(error)
    row = response[0]
    edge_id = row.get("id")
    actual_from_id = row.get("from_node_id")
    actual_to_id = row.get("to_node_id")
    actual_edge_type = row.get("edge_type")
    if (
        not isinstance(edge_id, str)
        or IDEA_ID_PATTERN.fullmatch(edge_id) is None
        or not isinstance(actual_from_id, str)
        or not isinstance(actual_to_id, str)
        or not isinstance(actual_edge_type, str)
        or EDGE_TYPE_PATTERN.fullmatch(actual_edge_type) is None
        or (require_edge_type_match and actual_edge_type != edge_type)
        or (actual_from_id, actual_to_id) not in (
            ((from_id, to_id), (to_id, from_id)) if allow_reverse else ((from_id, to_id),)
        )
    ):
        raise ToolError(error)
    return {
        "edge_id": edge_id,
        "from_id": actual_from_id,
        "to_id": actual_to_id,
        "edge_type": actual_edge_type,
    }


def _read_existing_connected_edge(
    from_id: str,
    to_id: str,
    edge_type: str,
) -> dict[str, str] | None:
    """Perform fixed-direction exact filters; never interpolate a PostgREST expression."""
    query = {"select": "id,from_node_id,to_node_id,edge_type", "limit": "1"}
    for source_id, target_id in ((from_id, to_id), (to_id, from_id)):
        try:
            response = _request(
                "GET",
                "canvas_edges",
                params={
                    **query,
                    "from_node_id": f"eq.{source_id}",
                    "to_node_id": f"eq.{target_id}",
                },
            )
        except ToolError as exc:
            raise ToolError("idea_connect_lookup_unverified") from exc
        if response == []:
            continue
        return _normalize_connected_edge(
            response,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            error="idea_connect_existing_unverified",
            allow_reverse=True,
            require_edge_type_match=False,
        )
    return None


def _bounded_limit(arguments: Mapping[str, Any], default: int) -> int:
    value = arguments.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ToolError("invalid_arguments: 'limit' must be an integer from 1 to 100")
    return value


def _config(env: Mapping[str, str]) -> tuple[str, str]:
    url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise ToolError(
            "configuration_error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"
        )
    if not url.startswith(("http://", "https://")):
        raise ToolError("configuration_error: SUPABASE_URL must use http or https")
    return url, key


def _request(
    method: str,
    path: str,
    *,
    params: Mapping[str, str] | None = None,
    body: Any = None,
    env: Mapping[str, str] | None = None,
    error_detail: bool = False,
) -> Any:
    url, key = _config(env or os.environ)
    endpoint = f"{url}/rest/v1/{path.lstrip('/')}"
    if params:
        endpoint = f"{endpoint}?{urllib.parse.urlencode(params)}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    change_id = _CHANGE_ID.get()
    if change_id and method != "GET":
        headers["X-Aenderung-Id"] = change_id
    request = urllib.request.Request(
        endpoint,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = _change_error(exc) if error_detail else None
        raise ToolError(detail or f"supabase_http_error: status={exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ToolError("supabase_unreachable") from exc
    except OSError as exc:
        raise ToolError("supabase_unreachable") from exc
    if not raw.strip():
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError("supabase_invalid_response") from exc


def _change_error(exc: urllib.error.HTTPError) -> str | None:
    """Return the documented ideen_zurueck error, never other database text."""
    try:
        message = json.loads(exc.read(4096).decode("utf-8")).get("message")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(message, str):
        return None
    if CHANGE_ERROR_PATTERN.fullmatch(message):
        return message
    for code in CHANGE_ERROR_CODES:
        if message.startswith(f"{code}:"):
            return code
    return None


def _only_arguments(arguments: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolError(f"invalid_arguments: unknown argument '{unknown[0]}'")


def _short_idea(row: Mapping[str, Any]) -> dict[str, Any]:
    """What canvas and chat need of one idea: no full text, no stored renderings."""
    metadata = row.get("metadata")
    rating = metadata.get("bewertung") if isinstance(metadata, Mapping) else None
    content = row.get("content")
    content = content if isinstance(content, str) else ""
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "format": _card_format(row),
        "score": rating.get("wert") if isinstance(rating, Mapping) else None,
        "node_type": row.get("node_type"),
        "x": row.get("x"),
        "y": row.get("y"),
        "linked_idea_id": row.get("linked_idea_id"),
        "preview": content[:IDEA_PREVIEW_CHARS],
        "content_length": len(content),
        "karte": _card_short(row),
    }


# ── Kartenansicht ────────────────────────────────────────────────────────────
# Was eine Karte einem Menschen zeigt: deutsche Beschriftungen, kurze Zeilen, keine
# Kennungen, kein JSON, keine Feldnamen. Gemessen am Bestand (spaces/ideas/KARTEN.md):
# bei 91 von 241 Karten steht der Inhalt nur im strukturierten Feld ``content_json``,
# der Text wiederholt bloss den Titel. ``format_schema.type`` ist dort "object" (ein
# JSON-Schema), das Format steht in ``content_json.type``.

FORMAT_NAMES = {
    "note": "Notiz", "table": "Tabelle", "action_list": "Aufgabenliste", "pros_cons": "Pro und Contra",
    "hierarchy": "Gliederung", "technical_specs": "Anforderungen", "kanban": "Kanban", "mindmap": "Mindmap",
    "swot": "SWOT-Analyse", "user_story": "Nutzergeschichten", "flowchart": "Ablauf",
    "comparison_table": "Vergleich", "research": "Recherche",
    "zusammenfassung": "Zusammenfassung", "white_paper": "White Paper",
}
# Dokument-Karten (idea_document): node_type "dokument", die Art steht in metadata.art.
DOCUMENT_KINDS = ("zusammenfassung", "white_paper")
DOCUMENT_NODE_TYPE = "dokument"
DOCUMENT_MAX_CHARS = 300_000
DOCUMENT_MAX_SOURCES = 500
FORMAT_ALIASES = {"pros_cons_table": "pros_cons"}
# Vorlagentext der alten Formatier-Agenten: sieht aus wie Inhalt, sagt aber nichts.
TEMPLATE_TEXTS = frozenset({
    "Konkrete Anforderung", "Betroffene Komponente", "Was analysiert wird", "Technische Spezifikation",
    "Gesamtbewertung der aktuellen Situation", "SWOT Analyse", "Mind Map", "User Stories", "Gliederung",
})
CARD_LINES = 3          # so viele Zeilen zeigt die Karte zugeklappt
CARD_LINE_CHARS = 120   # so lang darf eine Zeile auf der Karte sein
# (Art der Kante, liegt die gelesene Karte am Anfang?) -> was die andere Karte fuer sie ist.
# Im Bestand zeigt parent_child vom Oberpunkt zum Unterpunkt (Buehnen -> Lichtanlage) und
# expanded_from von der Ausgangskarte zur Weiterentwicklung (Helferliste -> Helferliste Druck).
EDGE_NAMES = {
    ("parent_child", True): "Unterpunkt", ("parent_child", False): "Oberpunkt",
    ("expanded_from", True): "Weiterentwicklung", ("expanded_from", False): "Ausgangspunkt",
}
READ_EDGE_CAP = 50
_MD_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+)")  # Nummern bleiben: sie tragen die Reihenfolge
_MD_MARKS = re.compile(r"(\*\*|__|`|~~)")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RESEARCH_TITLE = re.compile(r"^Research:\s*(.*?)\s*(\d{4})-(\d{2})-(\d{2})(?:\s+[\d:]+)?\s*$")


def _card_format(row: Mapping[str, Any]) -> str:
    """The idea's format name as stored: content_json.type, else the schema's const."""
    if row.get("node_type") == DOCUMENT_NODE_TYPE:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        return metadata.get("art") if metadata.get("art") in DOCUMENT_KINDS else "white_paper"
    structured = row.get("content_json")
    fmt = structured.get("type") if isinstance(structured, Mapping) else None
    if not isinstance(fmt, str) or not fmt:
        schema = row.get("format_schema")
        if isinstance(schema, Mapping):
            properties = schema.get("properties")
            kind = properties.get("type") if isinstance(properties, Mapping) else None
            fmt = kind.get("const") if isinstance(kind, Mapping) else None
            if not isinstance(fmt, str) or not fmt:
                fmt = schema.get("type") if schema.get("type") != "object" else None
    if (not isinstance(fmt, str) or not fmt) and row.get("node_type") == "research":
        fmt = "research"
    fmt = FORMAT_ALIASES.get(fmt, fmt) if isinstance(fmt, str) else None
    return fmt or "note"


def _plain(value: Any, limit: int | None = CARD_LINE_CHARS) -> str:
    """One line for people: no markdown marks, no template text, cut with an ellipsis."""
    if not isinstance(value, str):
        return ""
    text = _MD_MARKS.sub("", _MD_PREFIX.sub("", value)).strip()
    text = " ".join(text.split())
    if not text or text in TEMPLATE_TEXTS:
        return ""
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"


def _card_title(row: Mapping[str, Any]) -> str:
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    research = _RESEARCH_TITLE.match(title)
    if research:
        title = f"Recherche: {research.group(1)}"
    title = " ".join(title.replace("_", " ").split())
    return title or "Ohne Titel"


def _card_source(row: Mapping[str, Any]) -> str | None:
    """Where the card came from, said in one short sentence; None for a card typed by hand."""
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    research = _RESEARCH_TITLE.match(title)
    if research or row.get("node_type") == "research":
        parts = ["Recherche"]
        if research:
            parts[0] += f" vom {research.group(4)}.{research.group(3)}.{research.group(2)}"
        count = metadata.get("citation_count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            parts.append(_count(count, "Quelle", "Quellen"))
        return " · ".join(parts)
    if row.get("node_type") == DOCUMENT_NODE_TYPE:
        sources = metadata.get("quellen")
        count = len(sources) if isinstance(sources, list) else 0
        return f"aus {_count(count, 'Karte', 'Karten')}" if count else None
    if metadata.get("ai_generated"):
        origin = _plain(metadata.get("source_title"))
        return f"Vorschlag aus „{origin.replace('_', ' ')}“" if origin else "Vorschlag"
    return None


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _field(item: Any, *names: str) -> Any:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        for name in names:
            if item.get(name):
                return item.get(name)
    return None


def _text_sections(text: str, title: str, limit: int | None) -> list[tuple[str | None, list[str]]]:
    """Markdown or plain text split by headings; a text that only repeats the title says nothing."""
    if not text.strip() or text.strip() == title.strip():
        return []
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for raw in text.splitlines():
        heading = _MD_HEADING.match(raw.strip())
        if heading:
            sections.append((_plain(heading.group(2), limit) or None, []))
            continue
        stripped = raw.strip()
        if not stripped or set(stripped) <= set("-|: =*_"):
            continue
        if stripped.startswith("|"):
            stripped = " · ".join(cell.strip() for cell in stripped.strip("|").split("|") if cell.strip())
        line = _plain(stripped, limit)
        if line:
            sections[-1][1].append(line)
    return [(head, lines) for head, lines in sections if lines or head]


def _structured_sections(fmt: str, data: Mapping[str, Any], limit: int | None) -> tuple[list[tuple[str | None, list[str]]], str | None]:
    """Sections and a short count for each known format; unknown shapes give nothing."""
    def lines(items: Any, *names: str) -> list[str]:
        return [line for line in (_plain(_field(item, *names), limit) for item in _items(items)) if line]

    if fmt == "pros_cons":
        pros, cons = lines(data.get("pros"), "point"), lines(data.get("cons"), "point")
        return [("Pro", pros), ("Contra", cons)], f"{len(pros)} Pro · {len(cons)} Contra"
    if fmt == "swot":
        parts = [("Stärken", "Stärke", "strengths"), ("Schwächen", "Schwäche", "weaknesses"),
                 ("Chancen", "Chance", "opportunities"), ("Risiken", "Risiko", "threats")]
        found = [(name, one, lines(data.get(key), "point")) for name, one, key in parts]
        count = " · ".join(_count(len(items), one, name) for name, one, items in found if items)
        return [(name, items) for name, _, items in found], count or None
    if fmt == "technical_specs":
        found = lines(data.get("specifications"), "requirement")
        return [("Anforderungen", found)], _count(len(found), "Anforderung", "Anforderungen") if found else None
    if fmt == "user_story":
        stories = []
        for story in _items(data.get("stories")):
            if isinstance(story, Mapping):
                line = _plain(" ".join(part for part in (story.get("role"), story.get("want")) if isinstance(part, str)), limit)
                if line:
                    stories.append(line)
        return [("Nutzergeschichten", stories)], _count(len(stories), "Geschichte", "Geschichten") if stories else None
    if fmt == "flowchart":
        steps = [_plain(_field(node, "label"), limit) for node in _items(data.get("nodes"))
                 if isinstance(node, Mapping) and node.get("type") not in ("start", "end")]
        steps = [step for step in steps if step]
        return [("Schritte", steps)], _count(len(steps), "Schritt", "Schritte") if steps else None
    if fmt == "mindmap":
        sections = []
        for branch in _items(data.get("branches")):
            head = _plain(_field(branch, "label"), limit)
            if head:
                sections.append((head, lines(branch.get("children") if isinstance(branch, Mapping) else None, "label")))
        center = data.get("center")
        middle = _plain(_field(center, "description", "label"), limit)
        if middle:
            sections.insert(0, (None, [middle]))
        count = sum(1 for head, _ in sections if head)
        return sections, _count(count, "Zweig", "Zweige") if count else None
    if fmt == "hierarchy":
        sections = []
        for level in _items(data.get("levels")):
            for item in _items(level.get("items") if isinstance(level, Mapping) else None):
                head = _plain(_field(item, "name"), limit)
                if head:
                    sections.append((head, lines(item.get("children") if isinstance(item, Mapping) else None, "name")))
        return sections, _count(len(sections), "Punkt", "Punkte") if sections else None
    if fmt == "table":
        headers = [_plain(cell, limit) for cell in _items(data.get("headers"))]
        rows = [" · ".join(_plain(cell, limit) for cell in row if _plain(cell, limit))
                for row in _items(data.get("rows")) if isinstance(row, list)]
        rows = [row for row in rows if row]
        head = " · ".join(cell for cell in headers if cell) or None
        return [(head, rows)], _count(len(rows), "Zeile", "Zeilen") if rows else None
    if fmt == "action_list":
        states = {"completed": " (erledigt)", "in_progress": " (in Arbeit)", "blocked": " (blockiert)"}
        tasks, done = [], 0
        for item in _items(data.get("items")):
            task = _plain(_field(item, "task"), limit)
            if task:
                status = item.get("status") if isinstance(item, Mapping) else None
                done += status == "completed"
                tasks.append(task + states.get(status, ""))
        return [("Aufgaben", tasks)], f"{done} von {len(tasks)} erledigt" if tasks else None
    if fmt == "kanban":
        # Lauf 10 (34): Ein gespeichertes Board mit doppelten Spalten ("Offen" zweimal, "Fertig"
        # neben "Erledigt") zeigt jede Spalte einmal; gleiche Statuswoerter werden zusammengelegt.
        sections = []
        by_name: dict[str, list[str]] = {}
        for column in _items(data.get("columns")):
            head = _plain(_field(column, "name"), limit)
            if head:
                head = KANBAN_NAMES.get(head.casefold(), head)
                found = lines(column.get("cards") if isinstance(column, Mapping) else None, "title")
                if head.casefold() in by_name:
                    # Lauf 11: Die zweite "Offen"-Spalte trug dieselben Karten noch einmal
                    # ("2 Karten in 6 Spalten"); eine Karte steht je Spalte nur einmal.
                    schon = by_name[head.casefold()]
                    schon.extend(k for k in found if k not in schon)
                else:
                    by_name[head.casefold()] = found
                    sections.append((head, found))
        cards = sum(len(found) for _, found in sections)
        return sections, f"{_count(cards, 'Karte', 'Karten')} in {_count(len(sections), 'Spalte', 'Spalten')}" if sections else None
    if fmt == "comparison_table":
        options = lines(data.get("options"), "name")
        return [("Optionen", options)], _count(len(options), "Option", "Optionen") if options else None
    return [], None


def _live_text(row: Mapping[str, Any]) -> str:
    """The card's text: ``content_json.text`` when it says something, else ``content``.

    Arbeitslauf 2: on formatted cards ``content_json.text`` was empty while the appended
    text stood in ``content``; the empty field won and the card looked unchanged.
    """
    structured = row.get("content_json") if isinstance(row.get("content_json"), Mapping) else {}
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    text = structured.get("text")
    if isinstance(text, str) and text.strip() and text.strip() != title.strip():
        return text
    content = row.get("content")
    return content if isinstance(content, str) else ""


def _card_parts(row: Mapping[str, Any], limit: int | None) -> tuple[str, list[tuple[str | None, list[str]]], str | None, bool]:
    """(format, sections, count, is_text) for one card; sections hold only readable lines."""
    fmt = _card_format(row)
    title = row.get("title") if isinstance(row.get("title"), str) else ""
    structured = row.get("content_json") if isinstance(row.get("content_json"), Mapping) else {}
    sections, count = _structured_sections(fmt, structured, limit)
    sections = [(head, found) for head, found in sections if found]
    text = _live_text(row)
    if sections:
        # Ergaenzter Text auf einer Tabelle/Kanban steht unter der Struktur, nicht unsichtbar.
        extra = [line for _, found in _text_sections(text, title, limit) for line in found]
        if extra and structured.get("text") == text:
            sections = [*sections, ("Notiz", extra)]
        return fmt, sections, count, False
    sections = _text_sections(text, title, limit)
    headings = sum(1 for head, _ in sections if head)
    count = f"{headings} Abschnitte" if headings > 1 else None
    return fmt, sections, count, True


def _card_short(row: Mapping[str, Any]) -> dict[str, Any]:
    """The closed card: title, kind, up to three readable lines, a count, where it came from."""
    fmt, sections, count, is_text = _card_parts(row, CARD_LINE_CHARS)
    labelled = fmt in ("pros_cons", "swot", "kanban")
    if is_text:
        summary = next((found for head, found in sections
                        if head and re.search(r"summary|zusammenfassung|kurzfassung|fazit", head, re.I)), None)
        if summary is None:
            summary = next((found for head, found in sections if found and not all(":" in line[:40] for line in found)),
                           next((found for _, found in sections if found), []))
        shown = summary[:CARD_LINES]
    else:
        shown = []
        for head, found in sections:
            for line in found:
                shown.append(f"{head}: {line}" if labelled and head else line)
        shown = shown[:CARD_LINES]
    return {
        "titel": _card_title(row),
        "art": FORMAT_NAMES.get(fmt, "Notiz"),
        "zeilen": shown,
        "umfang": count,
        "quelle": _card_source(row),
        "leer": not shown,
    }


def _card_read(row: Mapping[str, Any], edges: list[dict[str, Any]], titles: Mapping[str, str]) -> dict[str, Any]:
    """The opened card: everything readable, sections in order, connections by title."""
    fmt, sections, count, is_text = _card_parts(row, None)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    rating = metadata.get("bewertung")
    score = rating.get("wert") if isinstance(rating, Mapping) else None
    links = []
    for edge in edges:
        starts_here = edge.get("from_node_id") == row.get("id")
        other = edge.get("to_node_id") if starts_here else edge.get("from_node_id")
        if isinstance(other, str) and other in titles:
            links.append({"id": other, "titel": titles[other],
                          "art": EDGE_NAMES.get((edge.get("edge_type"), starts_here), "verwandt")})
    text = None
    if is_text:
        raw = _live_text(row)
        title = row.get("title") if isinstance(row.get("title"), str) else ""
        text = raw if isinstance(raw, str) and raw.strip() and raw.strip() != title.strip() else None
    return {
        "id": row.get("id"),
        "titel": _card_title(row),
        "art": FORMAT_NAMES.get(fmt, "Notiz"),
        "umfang": count,
        "quelle": _card_source(row),
        "bewertung": score,
        "abschnitte": [{"ueberschrift": head, "zeilen": found} for head, found in sections],
        "text": text,
        "verbindungen": links,
        "leer": not any(found for _, found in sections),
    }


def _durable_id(arguments: Mapping[str, Any], name: str) -> str:
    """Require a stored id verbatim; it is safe to place into a PostgREST filter."""
    value = arguments.get(name)
    if not isinstance(value, str) or IDEA_ID_PATTERN.fullmatch(value) is None:
        raise ToolError(f"invalid_arguments: '{name}' must be a canonical durable id")
    return value


def _score(arguments: Mapping[str, Any]) -> int:
    value = arguments.get("score")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ToolError("invalid_arguments: 'score' must be an integer from 0 to 100")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _bubble_nodes(bubble_id: str, select: str, cap: int = ARRANGE_NODE_CAP) -> list[dict[str, Any]]:
    """Read the canvas ideas of one bubble; refuse to act on a cut-off list.

    The refusal names the real count and the limit, so the chat can say what works
    ("Ordnen geht bis 100 Notizen; hier sind es 101") instead of a bare error.
    """
    rows = _request(
        "GET",
        "canvas_nodes",
        params={
            "select": select,
            "linked_idea_id": f"eq.{bubble_id}",
            "order": "id.asc",
            "limit": str(cap + 1),
        },
    )
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("id"), str) and IDEA_ID_PATTERN.fullmatch(row["id"])
        for row in rows
    ):
        raise ToolError("supabase_invalid_response")
    if len(rows) > cap:
        total = _count_ideas(f"eq.{bubble_id}")
        counted = f"{total} ideas" if total is not None else "more ideas"
        raise ToolError(f"too_many_ideas: {counted} in this bubble, limit {cap}")
    return rows


def _grid_positions(nodes: list[dict[str, Any]], order: str, columns: int | None) -> list[tuple[int, int]]:
    """Sort ``nodes`` in place and return one grid cell per node, anchored top left."""
    if order == "title":
        nodes.sort(key=lambda node: (str(node.get("title") or "").casefold(), node["id"]))
    elif order == "score":
        def rating(node: Mapping[str, Any]) -> float:
            metadata = node.get("metadata")
            value = metadata.get("bewertung") if isinstance(metadata, Mapping) else None
            return _number(value.get("wert")) if isinstance(value, Mapping) else -1.0
        nodes.sort(key=lambda node: (-rating(node), node["id"]))
    else:
        # Zeilenweise lesen: erst nach Rasterzeile, dann von links nach rechts.
        nodes.sort(key=lambda node: (
            round(_number(node.get("y")) / ARRANGE_CELL_HEIGHT), _number(node.get("x")), node["id"],
        ))
    if columns is None:
        columns = max(1, math.ceil(math.sqrt(len(nodes))))
    origin_x = round(min(_number(node.get("x")) for node in nodes))
    origin_y = round(min(_number(node.get("y")) for node in nodes))
    return [
        (origin_x + (index % columns) * ARRANGE_CELL_WIDTH, origin_y + (index // columns) * ARRANGE_CELL_HEIGHT)
        for index in range(len(nodes))
    ]


def _is_group_head(node: Mapping[str, Any]) -> bool:
    metadata = node.get("metadata")
    return isinstance(metadata, Mapping) and metadata.get(GROUP_HEAD_KEY) is True


def _group_arguments(value: Any) -> list[dict[str, Any]]:
    """The groups of idea_group, strictly: names are plain one-line text, every id at most once."""
    if not isinstance(value, list) or not 1 <= len(value) <= GROUP_MAX:
        raise ToolError(f"invalid_arguments: 'gruppen' must be a list of 1 to {GROUP_MAX} groups")
    seen: set[str] = set()
    groups = []
    for group in value:
        if not isinstance(group, Mapping) or set(group) != {"name", "ids"}:
            raise ToolError("invalid_arguments: 'gruppen' items must have exactly 'name' and 'ids'")
        name = group["name"].strip() if isinstance(group["name"], str) else ""
        if not name or len(name) > GROUP_NAME_CHARS or any(ord(c) < 32 for c in name):
            raise ToolError(f"invalid_arguments: 'name' must be one line of 1 to {GROUP_NAME_CHARS} characters")
        ids = group["ids"]
        if (not isinstance(ids, list) or not ids
                or not all(isinstance(i, str) and IDEA_ID_PATTERN.fullmatch(i) for i in ids)):
            raise ToolError("invalid_arguments: 'ids' must be a non-empty list of idea ids")
        if seen & set(ids) or len(set(ids)) != len(ids):
            raise ToolError("invalid_arguments: 'ids' must name every idea at most once")
        seen.update(ids)
        groups.append({"name": name, "ids": list(ids)})
    return groups


def _group_layout(groups: list[dict[str, Any]], cards: Mapping[str, Mapping[str, Any]],
                  ) -> tuple[list[tuple[int, int]], dict[str, tuple[int, int]]]:
    """One column per group (more for groups over GROUP_ROWS), heading cell on top.

    Anchored at the top left of the grouped cards. Cards that are not grouped stay where
    they are; if one of them lies in the new block, the block starts below it.
    """
    grouped = {i for group in groups for i in group["ids"]}
    staying = [node for key, node in cards.items() if key not in grouped]
    origin_x = round(min(_number(cards[i].get("x")) for i in grouped))
    origin_y = round(min(_number(cards[i].get("y")) for i in grouped))
    spans = [max(1, math.ceil(len(group["ids"]) / GROUP_ROWS)) for group in groups]
    width = sum(spans) * ARRANGE_CELL_WIDTH
    height = (min(GROUP_ROWS, max(len(group["ids"]) for group in groups)) + 1) * ARRANGE_CELL_HEIGHT
    in_block = [node for node in staying
                if origin_x - ARRANGE_CELL_WIDTH < _number(node.get("x")) < origin_x + width
                and origin_y - ARRANGE_CELL_HEIGHT < _number(node.get("y")) < origin_y + height]
    if in_block:
        origin_y = round(max(_number(node.get("y")) for node in in_block)) + ARRANGE_CELL_HEIGHT
    heads: list[tuple[int, int]] = []
    places: dict[str, tuple[int, int]] = {}
    column = 0
    for group, span in zip(groups, spans):
        heads.append((origin_x + column * ARRANGE_CELL_WIDTH, origin_y))
        for index, key in enumerate(group["ids"]):
            places[key] = (origin_x + (column + index // GROUP_ROWS) * ARRANGE_CELL_WIDTH,
                           origin_y + (index % GROUP_ROWS + 1) * ARRANGE_CELL_HEIGHT)
        column += span
    return heads, places


def _group(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """idea_group: heading cards, card positions and (optionally) connections in one change."""
    _only_arguments(arguments, {"bubble_id", "gruppen", "verbinden"})
    bubble_id = _durable_id(arguments, "bubble_id")
    groups = _group_arguments(arguments.get("gruppen"))
    connect = arguments.get("verbinden", False)
    if not isinstance(connect, bool):
        raise ToolError("invalid_arguments: 'verbinden' must be true or false")
    nodes = _bubble_nodes(bubble_id, "id,title,x,y,metadata", ARRANGE_NODE_CAP + GROUP_MAX)
    heads = sorted((node for node in nodes if _is_group_head(node)),
                   key=lambda node: (_number(node.get("y")), _number(node.get("x")), node["id"]))
    cards = {node["id"]: node for node in nodes if not _is_group_head(node)}
    if len(cards) > ARRANGE_NODE_CAP:
        raise ToolError(f"too_many_ideas: {len(cards)} ideas in this bubble, limit {ARRANGE_NODE_CAP}")
    for group in groups:
        for key in group["ids"]:
            if key not in cards:
                raise ToolError(f"idea_not_in_bubble: {key}")
    head_places, places = _group_layout(groups, cards)

    result_groups, positions, edges = [], [], []
    for index, (group, (x, y)) in enumerate(zip(groups, head_places)):
        if index < len(heads):
            head_id = heads[index]["id"]
            # Verbindungen einer frueheren Gruppierung gehoeren zur alten Gruppe.
            _request("DELETE", "canvas_edges", params={"from_node_id": f"eq.{head_id}"})
            _request("PATCH", "canvas_nodes", params={"id": f"eq.{head_id}"},
                     body={"title": group["name"], "x": x, "y": y})
        else:
            head_id = uuid.uuid4().hex[:8]
            _request("POST", "canvas_nodes", body={
                "id": head_id, "node_type": "note", "title": group["name"], "content": "", "x": x, "y": y,
                "linked_idea_id": bubble_id,
                "metadata": {"width": 200.0, "height": 100.0, GROUP_HEAD_KEY: True},
            })
        positions.append({"id": head_id, "x": x, "y": y})
        result_groups.append({"name": group["name"], "kopf_id": head_id, "ids": group["ids"]})
    removed = []
    for head in heads[len(groups):]:
        _request("DELETE", "canvas_edges", params={"from_node_id": f"eq.{head['id']}"})
        _request("DELETE", "canvas_edges", params={"to_node_id": f"eq.{head['id']}"})
        _request("DELETE", "canvas_nodes", params={"id": f"eq.{head['id']}"})
        removed.append(head["id"])
    moved = 0
    for key, (x, y) in places.items():
        positions.append({"id": key, "x": x, "y": y})
        if _number(cards[key].get("x")) == x and _number(cards[key].get("y")) == y:
            continue
        _request("PATCH", "canvas_nodes", params={"id": f"eq.{key}"}, body={"x": x, "y": y})
        moved += 1
    if connect:
        for group in result_groups:
            for key in group["ids"]:
                _request("POST", "canvas_edges", body={
                    "id": uuid.uuid4().hex, "from_node_id": group["kopf_id"], "to_node_id": key,
                    "edge_type": "related"})
                edges.append({"from_id": group["kopf_id"], "to_id": key})
    return {"bubble_id": bubble_id, "gruppen": result_groups, "positionen": positions,
            "verschoben": moved, "entfernt": removed, "kanten": edges}


def _title_words(title: Any) -> frozenset[str]:
    return frozenset(word for word in re.findall(r"\w+", str(title or "").casefold()) if len(word) > 2)


def _link_candidates(nodes: list[dict[str, Any]]) -> list[tuple[float, str, str]]:
    """Pairs whose titles overlap enough (Jaccard on words), strongest first."""
    words = [(node["id"], _title_words(node.get("title"))) for node in nodes]
    pairs: list[tuple[float, str, str]] = []
    for index, (first_id, first_words) in enumerate(words):
        for second_id, second_words in words[index + 1:]:
            union = first_words | second_words
            if not union:
                continue
            overlap = len(first_words & second_words) / len(union)
            if overlap >= AUTO_LINK_THRESHOLD:
                pairs.append((overlap, first_id, second_id))
    pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))
    return pairs


TABLE_MAX_ROWS = 50
TABLE_MAX_COLUMNS = 8
TABLE_CELL_CHARS = 200
_TABLE_RULE = re.compile(r"^:?-{2,}:?$")
_TABLE_PAIR = re.compile(r"^(?:[-*•]\s*)?(?P<k>[^:|;\t]{1,60}?)\s*:\s+(?P<v>\S.*)$")


def _table_from_text(text: str, title: str) -> dict[str, Any] | None:
    """Columns and rows from text that already has them; ``None`` if it has none.

    No model: a Markdown table, lines split by tab, ";" or "|" into the same number of
    cells (first line = header), or "Name: Wert" per line. Anything else needs a model.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and title.strip() and lines[0] == title.strip():
        lines = lines[1:]
    if len(lines) < 2:
        return None

    def cell(value: str) -> str:
        return value.strip()[:TABLE_CELL_CHARS]

    grid: list[list[str]] | None = None
    if all(line.startswith("|") and line.endswith("|") and len(line) > 2 for line in lines):
        grid = [[cell(c) for c in line[1:-1].split("|")] for line in lines]
        grid = [row for row in grid if not all(_TABLE_RULE.fullmatch(c) for c in row if c) or not any(row)]
    else:
        for separator in ("\t", ";", "|"):
            split = [[cell(c) for c in line.split(separator)] for line in lines]
            if all(len(row) >= 2 for row in split) and len({len(row) for row in split}) == 1:
                grid = split
                break
    if grid is not None and len(grid) >= 2 and len({len(row) for row in grid}) == 1 and len(grid[0]) >= 2:
        columns = grid[0][:TABLE_MAX_COLUMNS]
        rows = [row[:TABLE_MAX_COLUMNS] for row in grid[1:1 + TABLE_MAX_ROWS]]
        return {"columns": columns, "rows": rows}
    pairs = [_TABLE_PAIR.match(line) for line in lines]
    if all(pairs):
        return {"columns": ["Punkt", "Angabe"],
                "rows": [[cell(m.group("k")), cell(m.group("v"))] for m in pairs[:TABLE_MAX_ROWS] if m]}
    return None


KANBAN_COLUMNS = ("Offen", "In Arbeit", "Erledigt")
KANBAN_MAX_COLUMNS = 8
_KANBAN_STATUS = re.compile(
    r"^(?P<k>offen|todo|to do|to-do|geplant|in arbeit|doing|l(?:ä|ae)uft|blockiert|erledigt|fertig|done|"
    r"sp(?:ä|ae)ter)\s*:\s*(?P<v>.*)$", re.IGNORECASE)
_KANBAN_HEAD = re.compile(r"^(?:#{1,6}\s*(?P<h>.+?)|(?P<c>[^:]{1,40}?)\s*:)\s*$")
_KANBAN_ITEM = re.compile(r"^(?:[-*•+]\s+|\d{1,3}[.)]\s+)?(?:\[(?P<x>[ xX])\]\s*)?(?P<t>.*)$")


def _kanban_from_text(text: str, title: str) -> dict[str, Any] | None:
    """Columns with cards from the card's text, without a model; ``None`` if nothing is left.

    Lauf 5: "Kanban" only switched the kind; the board stayed empty. Headings ("## Offen",
    "In Arbeit:") and status lines ("Offen: Logo") start columns, lines below are cards.
    Without any of them every line is a card in "Offen" ("[x]" goes to "Erledigt"),
    next to empty "In Arbeit" and "Erledigt".
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and title.strip() and lines[0] == title.strip():
        lines = lines[1:]
    columns: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    count = 0

    def column(name: str) -> dict[str, Any] | None:
        name = name.strip()[:TABLE_CELL_CHARS]
        # Lauf 10 (34): "Fertig" und "Erledigt" sind dasselbe Statuswort; eine Spalte je Status.
        name = KANBAN_NAMES.get(name.casefold(), name)
        key = name.casefold()
        if key not in by_name:
            if len(columns) >= KANBAN_MAX_COLUMNS:
                return None
            by_name[key] = {"name": name, "cards": []}
            columns.append(by_name[key])
        return by_name[key]

    def card(target: dict[str, Any] | None, value: str) -> None:
        nonlocal count
        value = value.strip()[:TABLE_CELL_CHARS]
        if target is not None and value and count < TABLE_MAX_ROWS:
            count += 1
            target["cards"].append({"id": f"k{count}", "title": value})

    current: dict[str, Any] | None = None
    loose: list[tuple[bool, str]] = []
    for line in lines:
        status = _KANBAN_STATUS.match(line)
        head = _KANBAN_HEAD.match(line)
        kopf = (head.group("h") or head.group("c") or "").strip() if head else ""
        if status:
            current = column(status.group("k")[:1].upper() + status.group("k")[1:])
            card(current, status.group("v"))
        elif head and _KANBAN_STATUS.match(kopf + ":"):
            # Lauf 8: nur ein Statuswort ("## Offen", "In Arbeit:") beginnt eine Spalte. Eine
            # beliebige Ueberschrift ("Aufgaben:") ist keine Spalte; ihre Zeilen gehen nach "Offen".
            current = column(kopf[:1].upper() + kopf[1:])
        elif head:
            current = None
        else:
            item = _KANBAN_ITEM.match(line)
            done = bool(item and item.group("x") in ("x", "X"))
            value = item.group("t") if item else line
            if current is None:
                loose.append((done, value))
            else:
                card(current, value)
    if not columns:
        for name in KANBAN_COLUMNS:
            column(name)
        for done, value in loose:
            card(by_name["erledigt" if done else "offen"], value)
    elif loose:
        # Lauf 8: Zeilen ohne Statuszeile gingen in die erste Spalte, also etwa nach "In Arbeit".
        # Sie gehoeren nach "Offen" ("[x]" nach "Erledigt"); beide Spalten entstehen bei Bedarf.
        for done, value in loose:
            ziel = "Erledigt" if done else "Offen"
            spalte = by_name.get(ziel.casefold()) or column(ziel)
            card(spalte, value)
    if columns and "offen" not in by_name and len(columns) < KANBAN_MAX_COLUMNS:
        column("Offen")
    # "Offen" steht immer vorn.
    columns.sort(key=lambda s: 0 if s["name"].casefold() == "offen" else 1)
    # Lauf 8: Die Huelle (rich_content_renderer.renderKanban) schreibt den Spaltenkopf weiss
    # auf column.color, ohne Farbe auf Hellgrau: die Spalten waren nicht zu sehen.
    for n, col in enumerate(columns):
        col["color"] = KANBAN_COLORS.get(col["name"].casefold(), KANBAN_COLOR_CYCLE[n % len(KANBAN_COLOR_CYCLE)])
    if not count:
        return None
    # Nur Spalten: die Huelle zeichnet Karte und Diagramm aus columns (seit 6742fe7); die alten
    # Felder todo/doing/done liest sie nur noch bei Boards ohne columns.
    return {"columns": columns}


# Gleichbedeutende Statuswoerter tragen einen Spaltennamen (Lauf 10: "Fertig" neben "Erledigt").
KANBAN_NAMES = {"offen": "Offen", "todo": "Offen", "to do": "Offen", "to-do": "Offen",
                "in arbeit": "In Arbeit", "doing": "In Arbeit", "läuft": "In Arbeit", "laeuft": "In Arbeit",
                "erledigt": "Erledigt", "fertig": "Erledigt", "done": "Erledigt",
                "geplant": "Geplant", "blockiert": "Blockiert", "später": "Später", "spaeter": "Später"}


KANBAN_COLOR_CYCLE = ("#6c757d", "#0d6efd", "#198754", "#6f42c1", "#fd7e14", "#20c997", "#dc3545", "#495057")
KANBAN_COLORS = {
    "offen": "#6c757d", "todo": "#6c757d", "to do": "#6c757d", "to-do": "#6c757d", "geplant": "#6c757d",
    "später": "#6c757d", "spaeter": "#6c757d",
    "in arbeit": "#0d6efd", "doing": "#0d6efd", "läuft": "#0d6efd", "laeuft": "#0d6efd",
    "blockiert": "#dc3545",
    "erledigt": "#198754", "fertig": "#198754", "done": "#198754",
}


def _change_kind(row: Mapping[str, Any]) -> str:
    """What one protocol row changed, in words the brain turns into a sentence."""
    table, operation = row.get("tabelle"), row.get("vorgang")
    if table == "public.canvas_edges":
        return "verbindung"
    if table == "public.ideas":
        return "bubble"
    if table != "public.canvas_nodes":
        return "sonst"
    if operation == "DELETE":
        return "karte_geloescht"
    if row.get("kopf") is not None:
        return "ueberschrift"
    if operation == "INSERT":
        return "karte_neu"
    if row.get("vorher_content") != row.get("nachher_content"):
        return "text"
    if row.get("vorher_titel") != row.get("nachher_titel"):
        return "titel"
    if row.get("vorher_x") != row.get("nachher_x") or row.get("vorher_y") != row.get("nachher_y"):
        return "position"
    return "sonst"


def _change_kinds(result: Mapping[str, Any]) -> dict[str, int] | None:
    """Lauf 18 (51): count what an undone change consisted of ("text": 1, "position": 27, ...).

    The database function names rows and their operation, not which fields an update
    touched. This reads the protocol of the group afterwards (read only). Only needed when
    a card was updated; any failure leaves the answer as the database gave it.
    """
    group, entries = result.get("gruppe"), result.get("eintraege")
    if not isinstance(group, str) or not isinstance(entries, list) or not any(
        isinstance(e, Mapping) and e.get("tabelle") == "public.canvas_nodes" and e.get("vorgang") == "UPDATE"
        for e in entries
    ):
        return None
    try:
        rows = _request("GET", "ideen_aenderungen", params={
            "select": (
                "tabelle,vorgang,"
                "vorher_content:vorher->>content,nachher_content:nachher->>content,"
                "vorher_titel:vorher->>title,nachher_titel:nachher->>title,"
                "vorher_x:vorher->>x,nachher_x:nachher->>x,vorher_y:vorher->>y,nachher_y:nachher->>y,"
                "kopf:nachher->metadata->>" + GROUP_HEAD_KEY
            ),
            "gruppe": f"eq.{group}",
            "order": "id.asc",
            "limit": str(CHANGE_LIST_ROW_CAP),
        })
    except Exception:  # noqa: BLE001 - the undo itself is done; only the wording gets poorer
        return None
    if not isinstance(rows, list) or not rows or not all(isinstance(row, Mapping) for row in rows):
        return None
    kinds: dict[str, int] = {}
    for row in rows:
        kind = _change_kind(row)
        kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def _change_groups(rows: Any, limit: int) -> list[dict[str, Any]]:
    """Fold protocol rows (newest first) into the groups ideen_zurueck undoes."""
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ToolError("supabase_invalid_response")
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("gruppe")
        if not isinstance(name, str):
            raise ToolError("supabase_invalid_response")
        group = groups.setdefault(name, {
            "gruppe": name,
            "quelle": row.get("quelle"),
            "erstellt_am": row.get("erstellt_am"),
            "zurueckgenommen": True,
            "anzahl": 0,
            "eintraege": [],
        })
        group["anzahl"] += 1
        group["erstellt_am"] = row.get("erstellt_am")  # rows come newest first
        if row.get("zurueckgenommen_am") is None:
            group["zurueckgenommen"] = False
        if len(group["eintraege"]) < CHANGE_LIST_ENTRY_CAP:
            group["eintraege"].append({
                "tabelle": row.get("tabelle"),
                "vorgang": row.get("vorgang"),
                "zeilen_id": row.get("zeilen_id"),
                "titel": row.get("vorher_titel") or row.get("nachher_titel")
                or row.get("vorher_name") or row.get("nachher_name"),
            })
    ordered = list(groups.values())
    if len(rows) >= CHANGE_LIST_ROW_CAP and len(ordered) > 1:
        ordered.pop()  # the oldest group may be cut off by the row cap
    return ordered[:limit]


def _with_appended_content(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """"Ergaenze bei X: ..." without the old text in the request: append to what is stored.

    A text that only repeats the title (201 of 241 cards, see KARTEN.md) counts as empty,
    so the card does not start with its own title twice.
    """
    append = arguments.get("content_append")
    if "content" in arguments:
        raise ToolError("invalid_arguments: 'content_append' and 'content' exclude each other")
    if not isinstance(append, str) or not append.strip():
        raise ToolError("invalid_arguments: 'content_append' must be a non-empty string")
    idea_id = _durable_id(arguments, "id")
    node = _first_row(_request(
        "GET", "canvas_nodes",
        params={"select": "id,title,content,content_json", "id": f"eq.{idea_id}", "limit": "1"},
    ))
    if node is None:
        raise ToolError("idea_not_found")
    old = node.get("content") if isinstance(node.get("content"), str) else ""
    title = node.get("title") if isinstance(node.get("title"), str) else ""
    keep = old.rstrip() if old.strip() and old.strip() != title.strip() else ""
    merged = f"{keep}\n\n{append.strip()}" if keep else append.strip()
    rest = {key: value for key, value in arguments.items() if key != "content_append"}
    return {**rest, "content": merged, "_node": node}


def _synced_structured(node: Mapping[str, Any] | None, content: str) -> dict[str, Any] | None:
    """Formatted card: the app renders ``content_json``, the text lives in ``content``.

    Arbeitslauf 2: "Ergaenze bei ..." on a table or kanban card wrote ``content``, the card
    kept showing the old (often empty) ``content_json.text``. The text field follows the
    stored text; structured parts (rows, columns) stay as they are.
    """
    structured = node.get("content_json") if isinstance(node, Mapping) else None
    if not isinstance(structured, Mapping) or not isinstance(structured.get("type"), str):
        return None
    title = node.get("title") if isinstance(node.get("title"), str) else ""
    text = content if content.strip() != title.strip() else ""
    return {**structured, "text": text}


def _patch_fields(
    arguments: Mapping[str, Any],
    allowed: set[str],
    *,
    string_fields: set[str],
    string_list_fields: set[str] | None = None,
    integer_fields: set[str] | None = None,
) -> dict[str, Any]:
    fields = {key: arguments[key] for key in allowed if key in arguments}
    if not fields:
        raise ToolError("invalid_arguments: at least one mutable field is required")
    for field in string_fields & fields.keys():
        if not isinstance(fields[field], str):
            raise ToolError(f"invalid_arguments: '{field}' must be a string")
    for field in (string_list_fields or set()) & fields.keys():
        value = fields[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ToolError(f"invalid_arguments: '{field}' must be an array of strings")
    for field in (integer_fields or set()) & fields.keys():
        value = fields[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError("invalid_arguments: 'x' and 'y' must be integers")
    return fields


def _first_row(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _normalize_idea_title_content(title: str, content: str) -> tuple[str, str]:
    """Keep canvas titles compact using the current SupabaseIdeasClient rules."""
    if len(title) > 90 and not content:
        separator_index = -1
        for separator in (": ", ":\n", "\n", " - ", " — "):
            index = title.find(separator)
            if 0 < index <= 90:
                separator_index = index
                break
        if separator_index > 0:
            content = title[separator_index:].lstrip(":-—\n ").strip()
            title = title[:separator_index].strip()
        else:
            content = title.strip()
            title = title[:80].rstrip() + "…"
    if len(title) > 120:
        if not content:
            content = title
        title = title[:117].rstrip() + "…"
    return title, content


# ── Befoerdern mit Uebergabe (befoerdern-uebergabe.md, Freigabe des Betreibers 24.11.2026) ──
# Das Projekt bekommt einen festen Stand der Bubble: White Paper, Karten, Verbindungen, Bewertung.
# Befoerdert wird nur, was die gemeinsame Sinnpruefung besteht (spaces/_contract/kartenregeln.py,
# dieselbe Datei liest die Software-Design-Ansicht). Alles unter der aenderung_id des Plans:
# "Zurueck" nimmt Projekt und Verknuepfung zusammen zurueck.
UEBERGABE_VERSION = 1
UEBERGABE_MAX_ZEICHEN = 600_000
UEBERGABE_MAX_KARTEN = 1000
_KARTENREGELN: Any = None


def _kartenregeln() -> Any:
    """The shared card rules, loaded by path (spaces/_contract/kartenregeln.py)."""
    global _KARTENREGELN
    if _KARTENREGELN is None:
        import importlib.util
        pfad = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_contract", "kartenregeln.py")
        spec = importlib.util.spec_from_file_location("vibemind_kartenregeln", pfad)
        if spec is None or spec.loader is None or not os.path.isfile(pfad):
            raise ToolError("kartenregeln_missing")
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        _KARTENREGELN = modul
    return _KARTENREGELN


def _bewertung(bubble: Mapping[str, Any]) -> dict[str, Any] | None:
    """The bubble's rating, or None (rule F2b in the shared card rules)."""
    return _kartenregeln().bewertung_der_bubble(bubble)


def _bubble_kanten(ids: list[str]) -> list[dict[str, Any]]:
    """All edges that start at a card of the bubble (read in chunks of 100 ids)."""
    kanten: list[dict[str, Any]] = []
    for start in range(0, len(ids), 100):
        teil = ids[start:start + 100]
        zeilen = _request("GET", "canvas_edges", params={
            "select": "from_node_id,to_node_id,edge_type", "from_node_id": f"in.({','.join(teil)})"})
        if not isinstance(zeilen, list):
            raise ToolError("supabase_invalid_response")
        kanten += [z for z in zeilen if isinstance(z, dict)]
    return kanten


def _pruefsumme(stand: Mapping[str, Any]) -> str:
    import hashlib
    kern = {k: v for k, v in stand.items() if k not in ("am", "pruefsumme")}
    text = json.dumps(kern, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _art(regeln: Any, karte: Mapping[str, Any]) -> str:
    if regeln.ist_gruppenkopf(karte):
        return "gruppe"
    if regeln.ist_white_paper(karte):
        return "white_paper"
    return "dokument" if regeln.ist_dokument(karte) else "notiz"


def _uebergabe_bauen(bubble: Mapping[str, Any], bubble_id: str) -> dict[str, Any]:
    """Read the bubble's cards and edges, run the promotion gate, build the fixed hand-over.

    Raises ``befoerdern_nicht_bereit: <gruende>`` when the gate fails (nothing is written) and
    ``befoerdern_zu_gross`` above UEBERGABE_MAX_ZEICHEN.
    """
    regeln = _kartenregeln()
    zeilen = _bubble_nodes(bubble_id, "id,node_type,title,content,content_json,metadata,updated_at",
                           UEBERGABE_MAX_KARTEN)
    # Die Regeln lesen "content"; formatierte Karten tragen ihren Text in content_json.
    karten = [{**z, "content": _live_text(z)} for z in zeilen]
    ids = [k["id"] for k in karten]
    kanten = [k for k in _bubble_kanten(ids) if k.get("to_node_id") in set(ids)] if ids else []
    bewertung = _bewertung(bubble)
    pruefung = regeln.sinnpruefung(karten, kanten, bewertung)
    if not pruefung.get("bestanden"):
        ids_text = ";".join(f"{feld}={','.join(map(str, pruefung.get(feld) or []))}"
                            for feld in ("leer", "lose") if pruefung.get(feld))
        raise ToolError("befoerdern_nicht_bereit: " + ",".join(pruefung.get("gruende") or [])
                        + (f" ({ids_text})" if ids_text else ""))

    papiere = sorted((k for k in karten if regeln.ist_white_paper(k)),
                     key=lambda k: str(k.get("updated_at") or ""))
    papier = papiere[-1] if papiere else None
    koepfe = {k["id"]: str(k.get("title") or "") for k in karten if regeln.ist_gruppenkopf(k)}
    gruppe_von: dict[str, str] = {}
    for kante in kanten:
        if kante.get("from_node_id") in koepfe and kante.get("to_node_id") not in koepfe:
            gruppe_von.setdefault(str(kante["to_node_id"]), koepfe[kante["from_node_id"]])

    white_paper = None
    if papier is not None:
        meta = papier.get("metadata") if isinstance(papier.get("metadata"), Mapping) else {}
        white_paper = {
            "node_id": papier["id"], "titel": str(papier.get("title") or ""), "text": papier["content"],
            "zeichen": len(papier["content"]), "quellen": list(meta.get("quellen") or []),
            "geaendert_am": papier.get("updated_at"),
        }
    stand: dict[str, Any] = {
        "version": UEBERGABE_VERSION,
        "bubble_id": bubble_id,
        "titel": str(bubble.get("title") or "").strip(),
        "am": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "bewertung": bewertung,
        "white_paper": white_paper,
        # Ueberschrift-Karten stehen mit art "gruppe" drin, damit ihre Kanten die Karten verbinden
        # (die Ansicht rechnet die Sinnpruefung aus karten/kanten nach).
        "karten": [{"id": k["id"], "titel": str(k.get("title") or ""), "text": k["content"],
                    "art": _art(regeln, k), "gruppe": gruppe_von.get(k["id"])}
                   for k in karten if papier is None or k["id"] != papier["id"]],
        "kanten": [{"von": k["from_node_id"], "nach": k["to_node_id"], "art": k.get("edge_type") or "related"}
                   for k in kanten],
        "pruefung": pruefung,
    }
    stand["pruefsumme"] = _pruefsumme(stand)
    groesse = len(json.dumps(stand, ensure_ascii=False))
    if groesse > UEBERGABE_MAX_ZEICHEN:
        raise ToolError(f"befoerdern_zu_gross: {groesse} characters, limit {UEBERGABE_MAX_ZEICHEN}")
    return stand


def _befoerdert(project: Mapping[str, Any], stand: Mapping[str, Any]) -> dict[str, Any]:
    """Short receipt: the project without the bulky hand-over, plus where the app goes next."""
    metadata = project.get("metadata") if isinstance(project.get("metadata"), Mapping) else {}
    papier = stand.get("white_paper") if isinstance(stand.get("white_paper"), Mapping) else None
    karten = stand.get("karten") if isinstance(stand.get("karten"), list) else []
    return {
        **{k: v for k, v in project.items() if k != "metadata"},
        "metadata": {k: v for k, v in metadata.items() if k != "uebergabe"},
        "ziel": {"space": "swedesign", "projekt_id": project.get("id")},
        "karten": sum(1 for k in karten if isinstance(k, Mapping) and k.get("art") != "gruppe"),
        "white_paper_vom": papier.get("geaendert_am") if papier else None,
        "pruefsumme": stand.get("pruefsumme"),
    }


def _compensate_project(project_id: str, error: str) -> None:
    """Remove an unlinked project and never turn an uncertain promotion into success."""
    try:
        _request("DELETE", "projects", params={"id": f"eq.{project_id}"})
    except ToolError as exc:
        raise ToolError(f"{error}; compensation_failed: {exc}") from exc
    raise ToolError(error)


def _count_ideas(bubble_filter: str | None) -> int | None:
    """How many ideas there are, counted on ids only; None when the count cannot be trusted."""
    params = {"select": "id", "limit": str(IDEA_COUNT_CAP + 1)}
    if bubble_filter:
        params["linked_idea_id"] = bubble_filter
    try:
        ids = _request("GET", "canvas_nodes", params=params)
    except ToolError:
        return None
    if not isinstance(ids, list) or len(ids) > IDEA_COUNT_CAP:
        return None
    return len(ids)


def _is_definitive_project_post_error(error: ToolError) -> bool:
    match = re.fullmatch(r"supabase_http_error: status=(\d{3})", str(error))
    return match is not None and 400 <= int(match.group(1)) < 500


def _owned_promotion_project(
    project: Mapping[str, Any],
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
) -> bool:
    metadata = project.get("metadata")
    return (
        project.get("id") == project_id
        and project.get("from_idea_id") == bubble_id
        and isinstance(metadata, Mapping)
        and metadata.get("promotion_attempt_id") == promotion_attempt_id
        and metadata.get("source_bubble_id") == bubble_id
    )


def _read_owned_promotion_project(
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read back a project and attest it belongs to this promotion attempt."""
    try:
        project = _first_row(_request(
            "GET",
            "projects",
            params={"select": "*", "id": f"eq.{project_id}", "limit": "1"},
        ))
    except ToolError as exc:
        return None, f"readback_failed: {exc}"
    if project is None:
        return None, "not_found"
    if not _owned_promotion_project(
        project,
        project_id=project_id,
        bubble_id=bubble_id,
        promotion_attempt_id=promotion_attempt_id,
    ):
        return None, "ownership_mismatch"
    return project, None


def _reconcile_ambiguous_project_post_failure(
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
    create_error: str,
) -> None:
    """Clean up only a project conclusively owned by an ambiguous failed POST."""
    project, reason = _read_owned_promotion_project(
        project_id=project_id,
        bubble_id=bubble_id,
        promotion_attempt_id=promotion_attempt_id,
    )
    if project is None:
        if reason == "not_found":
            raise ToolError(create_error)
        if reason is not None and reason.startswith("readback_failed: "):
            reason = reason.removeprefix("readback_failed: ")
        raise ToolError(f"{create_error}; cleanup_skipped_unverified: {reason}")
    _compensate_project(project_id, create_error)


def _reconcile_unrepresented_project_create(
    *,
    project_id: str,
    bubble_id: str,
    promotion_attempt_id: str,
) -> None:
    """Fail closed after an HTTP-success create without a verifiable representation."""
    project, reason = _read_owned_promotion_project(
        project_id=project_id,
        bubble_id=bubble_id,
        promotion_attempt_id=promotion_attempt_id,
    )
    if project is None:
        raise ToolError(f"bubble_promotion_create_unverified: {reason}")
    _compensate_project(project_id, "bubble_promotion_create_unverified")


def call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    """Call one explicit Ideas/Bubbles operation; no arbitrary table access.

    Writing tools accept an optional ``aenderung_id``; it is taken out of the
    arguments before the tool sees them and travels as a request header.
    """
    if "aenderung_id" not in arguments:
        return _call_tool(name, arguments)
    change_id = arguments["aenderung_id"]
    if name not in CHANGE_WRITE_TOOLS:
        raise ToolError("invalid_arguments: 'aenderung_id' is only accepted by writing tools")
    if (
        not isinstance(change_id, str)
        or not 1 <= len(change_id) <= 200
        or change_id != change_id.strip()
        or not change_id.isascii()
        or not change_id.isprintable()
    ):
        raise ToolError("invalid_arguments: 'aenderung_id' must be a printable ASCII string of 1 to 200 characters")
    token = _CHANGE_ID.set(change_id)
    try:
        return _call_tool(name, {k: v for k, v in arguments.items() if k != "aenderung_id"})
    finally:
        _CHANGE_ID.reset(token)


def _document_write(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create or replace one document card; never touches a card of another kind or bubble."""
    _only_arguments(arguments, {"id", "bubble_id", "title", "art", "content", "quellen"})
    node_id = _durable_id(arguments, "id")
    bubble_id = _durable_id(arguments, "bubble_id")
    title = _required_string(arguments, "title")
    if len(title) > 200:
        raise ToolError("invalid_arguments: 'title' must be at most 200 characters")
    art = arguments.get("art")
    if art not in DOCUMENT_KINDS:
        raise ToolError("invalid_arguments: 'art' must be 'zusammenfassung' or 'white_paper'")
    content = arguments.get("content")
    if not isinstance(content, str) or not content.strip() or len(content) > DOCUMENT_MAX_CHARS:
        raise ToolError(f"invalid_arguments: 'content' must be a non-empty string of at most {DOCUMENT_MAX_CHARS} characters")
    sources = arguments.get("quellen", [])
    if (not isinstance(sources, list) or len(sources) > DOCUMENT_MAX_SOURCES
            or not all(isinstance(s, str) and IDEA_ID_PATTERN.fullmatch(s) for s in sources)):
        raise ToolError(f"invalid_arguments: 'quellen' must be at most {DOCUMENT_MAX_SOURCES} durable ids")
    metadata = {"width": 320.0, "height": 220.0, "art": art, "quellen": list(dict.fromkeys(sources))}
    existing = _first_row(_request(
        "GET", "canvas_nodes",
        params={"select": "id,node_type,linked_idea_id", "id": f"eq.{node_id}", "limit": "1"},
    ))
    if existing is not None:
        if existing.get("node_type") != DOCUMENT_NODE_TYPE or existing.get("linked_idea_id") != bubble_id:
            raise ToolError("idea_document_conflict")
        updated = _first_row(_request(
            "PATCH", "canvas_nodes", params={"id": f"eq.{node_id}"},
            body={"title": title, "content": content, "metadata": metadata},
        ))
        if updated is None:
            raise ToolError("idea_not_found")
        return updated
    created = _first_row(_request(
        "POST", "canvas_nodes",
        body={"id": node_id, "node_type": DOCUMENT_NODE_TYPE, "title": title, "content": content,
              "x": 0, "y": -300, "linked_idea_id": bubble_id, "metadata": metadata},
    ))
    if created is None:
        raise ToolError("idea_document_not_written")
    return created


def _call_tool(name: str, arguments: Mapping[str, Any]) -> Any:
    """Dispatch to the single tool implementation (arguments without aenderung_id)."""
    if name == "bubble_list":
        return _request(
            "GET",
            "ideas",
            params={
                "select": "id,title,parent_id,score,status,metadata",
                "parent_id": "is.null",
                "limit": str(_bounded_limit(arguments, 20)),
            },
        )
    if name == "bubble_get":
        return _request("GET", "ideas", params={"select": "*", "id": f"eq.{_required_string(arguments, 'id')}", "parent_id": "is.null", "limit": "1"})
    if name == "bubble_create":
        tags = arguments.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ToolError("invalid_arguments: 'tags' must be an array of strings")
        title = _required_string(arguments, "title")
        description = arguments.get("description", "")
        if not isinstance(description, str):
            raise ToolError("invalid_arguments: 'description' must be a string")
        existing = _first_row(_request(
            "GET",
            "ideas",
            params={"select": "*", "title": f"ilike.{title}", "parent_id": "is.null", "limit": "1"},
        ))
        if existing is not None:
            return existing
        return _request(
            "POST",
            "ideas",
            body={
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "description": description,
                "parent_id": None,
                "score": 0,
                "status": "active",
                "source": "spaces-ideas-mcp",
                "tags": tags,
                "metadata": {},
            },
        )
    if name == "bubble_update":
        return _request(
            "PATCH",
            "ideas",
            params={"id": f"eq.{_required_string(arguments, 'id')}", "parent_id": "is.null"},
            body=_patch_fields(
                arguments,
                {"title", "description", "tags"},
                string_fields={"title", "description"},
                string_list_fields={"tags"},
            ),
        )
    if name == "bubble_delete":
        bubble_id = _required_string(arguments, "id")
        mit_karten = arguments.get("mit_karten", False)
        if not isinstance(mit_karten, bool):
            raise ToolError("invalid_arguments: 'mit_karten' must be a boolean")
        if mit_karten:
            # Entscheidung des Betreibers (24.11.2026): die Karten gehen mit. Nur wenn es die Bubble
            # gibt; die Verbindungen der Karten gehen per ON DELETE CASCADE mit, die Begruendungen
            # stehen in der Bubble selbst. Alles unter derselben aenderung_id: "Zurueck" holt Bubble,
            # Karten und Verbindungen. Mehr als BUBBLE_DELETE_MAX_KARTEN: nichts loeschen.
            if _first_row(_request("GET", "ideas", params={
                    "select": "id", "id": f"eq.{bubble_id}", "parent_id": "is.null", "limit": "1"})) is None:
                return []
            karten = _request("GET", "canvas_nodes", params={
                "select": "id", "linked_idea_id": f"eq.{bubble_id}", "limit": str(BUBBLE_DELETE_MAX_KARTEN + 1)})
            if not isinstance(karten, list):
                raise ToolError("supabase_invalid_response")
            if len(karten) > BUBBLE_DELETE_MAX_KARTEN:
                raise ToolError(f"too_many_cards: more than {BUBBLE_DELETE_MAX_KARTEN} cards, nothing deleted")
            geloescht = _request("DELETE", "canvas_nodes", params={"linked_idea_id": f"eq.{bubble_id}"})
            if not isinstance(geloescht, list):
                raise ToolError("supabase_invalid_response")
            kennungen = [str(k["id"]) for k in geloescht if isinstance(k, Mapping) and k.get("id")]
            zeilen = _request("DELETE", "ideas", params={"id": f"eq.{bubble_id}", "parent_id": "is.null"})
            if not isinstance(zeilen, list):
                raise ToolError("supabase_invalid_response")
            # Die Nachpruefung liest genau diese Karten zurueck (weg, nicht nur losgeloest).
            return [{**z, "karten_geloescht": kennungen} if isinstance(z, Mapping) else z for z in zeilen]
        return _request("DELETE", "ideas", params={"id": f"eq.{bubble_id}", "parent_id": "is.null"})
    if name == "bubble_promote":
        bubble_id = _canonical_bubble_id(arguments)
        bubble = _first_row(_request(
            "GET",
            "ideas",
            params={
                "select": "*",
                "id": f"eq.{bubble_id}",
                "parent_id": "is.null",
                "limit": "1",
            },
        ))
        if bubble is None:
            raise ToolError("bubble_not_found")

        title = bubble.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ToolError("bubble_invalid: title must be a non-empty string")
        if bubble.get("promoted_to_project_id"):
            raise ToolError("bubble_already_promoted")
        # Erst pruefen und den Stand bauen, dann schreiben: faellt die Pruefung durch, entsteht nichts.
        stand = _uebergabe_bauen(bubble, bubble_id)
        project_id = uuid.uuid4().hex[:8]
        promotion_attempt_id = uuid.uuid4().hex
        project_row = {
            "id": project_id,
            "name": title.strip(),
            "description": str(bubble.get("description") or "").strip(),
            "status": "active",
            "from_idea_id": bubble_id,
            "metadata": {
                "source_space": "bubbles",
                "source_bubble_id": bubble_id,
                "source_score": bubble.get("score", 0),
                "promotion_attempt_id": promotion_attempt_id,
                "uebergabe": stand,
            },
        }
        try:
            project = _first_row(_request("POST", "projects", body=project_row))
        except ToolError as exc:
            create_error = f"bubble_promotion_create_failed: {exc}"
            if _is_definitive_project_post_error(exc):
                raise ToolError(create_error) from exc
            _reconcile_ambiguous_project_post_failure(
                project_id=project_id,
                bubble_id=bubble_id,
                promotion_attempt_id=promotion_attempt_id,
                create_error=create_error,
            )
        if project is None:
            _reconcile_unrepresented_project_create(
                project_id=project_id,
                bubble_id=bubble_id,
                promotion_attempt_id=promotion_attempt_id,
            )
        if not _owned_promotion_project(
            project,
            project_id=project_id,
            bubble_id=bubble_id,
            promotion_attempt_id=promotion_attempt_id,
        ):
            raise ToolError("bubble_promotion_create_unverified")

        link_error: str | None = None
        try:
            linked = _first_row(_request(
                "PATCH",
                "ideas",
                params={
                    "id": f"eq.{bubble_id}",
                    "parent_id": "is.null",
                    "promoted_to_project_id": "is.null",
                },
                body={"status": "promoted", "promoted_to_project_id": project_id},
            ))
            if linked is None:
                link_error = "link_returned_no_row"
            elif linked.get("id") != bubble_id or linked.get("promoted_to_project_id") != project_id:
                link_error = "link_mismatch"
        except ToolError as exc:
            link_error = str(exc)
        if link_error is not None:
            _compensate_project(project_id, f"bubble_promotion_link_failed: {link_error}")
        return _befoerdert(project, stand)
    if name == "idea_list":
        bubble_id = arguments.get("bubble_id")
        full = arguments.get("full", False)
        if not isinstance(full, bool):
            raise ToolError("invalid_arguments: 'full' must be true or false")
        limit = _bounded_limit(arguments, 50)
        params = {
            "select": "*" if full else IDEA_LIST_SHORT_SELECT,
            # Lesereihenfolge am Canvas: oben links zuerst; "die ersten" heisst damit etwas.
            "order": "y.asc,x.asc,id.asc",
            "limit": str(limit),
        }
        if bubble_id is not None:
            if not isinstance(bubble_id, str) or not bubble_id.strip():
                raise ToolError("invalid_arguments: 'bubble_id' must be a non-empty string")
            params["linked_idea_id"] = f"eq.{bubble_id.strip()}"
        rows = _request("GET", "canvas_nodes", params=params)
        if full or not isinstance(rows, list):
            return rows
        cards = [_short_idea(row) for row in rows if isinstance(row, Mapping)]
        total: int | None = len(cards)
        if len(rows) >= limit:
            total = _count_ideas(params.get("linked_idea_id"))
        return {
            "karten": cards,
            "gezeigt": len(cards),
            "gesamt": total,  # None: Zaehlung nicht moeglich, mindestens "gezeigt"
            "gekuerzt": total is None or total > len(cards),
        }
    if name == "idea_get":
        return _request("GET", "canvas_nodes", params={"select": "*", "id": f"eq.{_required_string(arguments, 'id')}", "limit": "1"})
    if name == "idea_read":
        _only_arguments(arguments, {"id"})
        idea_id = _durable_id(arguments, "id")
        node = _first_row(_request(
            "GET", "canvas_nodes",
            params={"select": "id,title,content,node_type,metadata,content_json,format_schema", "id": f"eq.{idea_id}", "limit": "1"},
        ))
        if node is None:
            raise ToolError("idea_not_found")
        edges = _request(
            "GET", "canvas_edges",
            params={"select": "from_node_id,to_node_id,edge_type",
                    "or": f"(from_node_id.eq.{idea_id},to_node_id.eq.{idea_id})", "limit": str(READ_EDGE_CAP)},
        )
        edges = [edge for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []
        others = sorted({
            other for edge in edges
            for other in (edge.get("from_node_id"), edge.get("to_node_id"))
            if isinstance(other, str) and other != idea_id and IDEA_ID_PATTERN.fullmatch(other)
        })
        titles: dict[str, str] = {}
        if others:
            rows = _request("GET", "canvas_nodes", params={"select": "id,title", "id": f"in.({','.join(others)})"})
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                    titles[row["id"]] = _card_title(row)
        return _card_read(node, edges, titles)
    if name == "idea_document":
        return _document_write(arguments)
    if name == "idea_create":
        node_type = arguments.get("node_type", "note")
        content = arguments.get("content", "")
        x, y = arguments.get("x", 0), arguments.get("y", 0)
        if not isinstance(node_type, str) or not node_type.strip():
            raise ToolError("invalid_arguments: 'node_type' must be a non-empty string")
        if not isinstance(content, str):
            raise ToolError("invalid_arguments: 'content' must be a string")
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
            raise ToolError("invalid_arguments: 'x' and 'y' must be integers")
        title, content = _normalize_idea_title_content(
            _required_string(arguments, "title"), content,
        )
        bubble_id = _required_string(arguments, "bubble_id")
        node_id = _durable_id(arguments, "id") if "id" in arguments else uuid.uuid4().hex[:8]
        existing = _first_row(_request(
            "GET",
            "canvas_nodes",
            params={"select": "*", "linked_idea_id": f"eq.{bubble_id}", "title": f"ilike.{title}", "limit": "1"},
        ))
        if existing is not None:
            return existing
        return _request(
            "POST",
            "canvas_nodes",
            body={
                "id": node_id,
                "node_type": node_type.strip(),
                "title": title,
                "content": content,
                "x": x,
                "y": y,
                "linked_idea_id": bubble_id,
                "metadata": {"width": 200.0, "height": 100.0},
            },
        )
    if name == "idea_update":
        node = None
        if "content_append" in arguments:
            arguments = _with_appended_content(arguments)
            node = arguments.pop("_node")
        body = _patch_fields(
            arguments,
            {"title", "content", "node_type", "x", "y"},
            string_fields={"title", "content", "node_type"},
            integer_fields={"x", "y"},
        )
        if isinstance(body.get("content"), str):
            if node is None:
                node = _first_row(_request("GET", "canvas_nodes", params={
                    "select": "id,title,content_json", "id": f"eq.{_required_string(arguments, 'id')}", "limit": "1"}))
            synced = _synced_structured(node, body["content"])
            if synced is not None:
                body["content_json"] = synced
        return _request(
            "PATCH",
            "canvas_nodes",
            params={"id": f"eq.{_required_string(arguments, 'id')}"},
            body=body,
        )
    if name == "idea_delete":
        idea_id = _required_string(arguments, "id")
        _request("DELETE", "canvas_edges", params={"from_node_id": f"eq.{idea_id}"})
        _request("DELETE", "canvas_edges", params={"to_node_id": f"eq.{idea_id}"})
        return _request("DELETE", "canvas_nodes", params={"id": f"eq.{idea_id}"})
    if name == "idea_connect":
        from_id, to_id, edge_type = _canonical_idea_connect_arguments(arguments)
        _read_exact_canvas_node(from_id, "from_id")
        _read_exact_canvas_node(to_id, "to_id")
        existing = _read_existing_connected_edge(from_id, to_id, edge_type)
        if existing is not None:
            return existing
        try:
            created = _request(
                "POST",
                "canvas_edges",
                body={
                    "id": uuid.uuid4().hex,
                    "from_node_id": from_id,
                    "to_node_id": to_id,
                    "edge_type": edge_type,
                },
            )
        except ToolError as exc:
            raise ToolError("idea_connect_create_unverified") from exc
        return _normalize_connected_edge(
            created,
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            error="idea_connect_create_unverified",
            allow_reverse=False,
            require_edge_type_match=True,
        )
    if name == "idea_disconnect":
        return _request("DELETE", "canvas_edges", params={"id": f"eq.{_required_string(arguments, 'edge_id')}"})
    if name == "idea_format":
        _only_arguments(arguments, {"id", "format"})
        idea_id = _durable_id(arguments, "id")
        fmt = arguments.get("format")
        if fmt != "revert" and fmt not in IDEA_FORMATS:
            raise ToolError("invalid_arguments: 'format' must be one of " + ", ".join(IDEA_FORMATS) + " or revert")
        node = _first_row(_request(
            "GET",
            "canvas_nodes",
            params={
                "select": "id,title,content,node_type,content_json,previous_content_json",
                "id": f"eq.{idea_id}",
                "limit": "1",
            },
        ))
        if node is None:
            raise ToolError("idea_not_found")
        current = node.get("content_json")
        if not isinstance(current, Mapping):
            current = {"type": node.get("node_type") or "note", "text": node.get("content") or ""}
        if fmt == "revert":
            previous = node.get("previous_content_json")
            if not isinstance(previous, Mapping):
                raise ToolError("idea_format_nothing_to_revert")
            previous_type = previous.get("type")
            patch = {
                "format_schema": {"type": previous_type if previous_type in IDEA_FORMATS else "note"},
                "content_json": dict(previous),
            }
        else:
            # Ohne Text zeigt die Huelle eine leere Tabelle oder ein leeres Kanban
            # (Arbeitslauf 23.11.2026). Ein Text, der nur den Titel wiederholt, zaehlt als leer.
            text = str(node.get("content") or "").strip()
            if fmt != "note" and (not text or text == str(node.get("title") or "").strip()):
                raise ToolError("idea_format_no_content")
            structured: dict[str, Any] = {"type": fmt, "title": node.get("title") or "", "text": node.get("content") or ""}
            if fmt == "table":
                # Die Huelle zeichnet eine Tabelle nur aus columns/rows (format-renderer.js).
                # Ohne erkennbare Spalten aendert sich nichts (Arbeitslauf 2: nur die Art wechselte).
                table = _table_from_text(_live_text(node) or "", str(node.get("title") or ""))
                if table is None:
                    raise ToolError("idea_format_table_no_columns")
                structured.update(table)
            if fmt == "kanban":
                board = _kanban_from_text(_live_text(node) or "", str(node.get("title") or ""))
                if board is None:
                    raise ToolError("idea_format_no_content")
                structured.update(board)
            patch = {
                "format_schema": {"type": fmt},
                "content_json": structured,
                "previous_content_json": dict(current),
                "last_formatted": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        updated = _first_row(_request("PATCH", "canvas_nodes", params={"id": f"eq.{idea_id}"}, body=patch))
        if updated is None:
            raise ToolError("idea_format_unverified")
        return updated
    if name == "idea_arrange":
        _only_arguments(arguments, {"bubble_id", "order", "columns"})
        bubble_id = _durable_id(arguments, "bubble_id")
        order = arguments.get("order", "position")
        if order not in ARRANGE_ORDERS:
            raise ToolError("invalid_arguments: 'order' must be position, title or score")
        columns = arguments.get("columns")
        if columns is not None and (isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= 20):
            raise ToolError("invalid_arguments: 'columns' must be an integer from 1 to 20")
        nodes = _bubble_nodes(bubble_id, "id,title,x,y,metadata")
        if not nodes:
            return {"bubble_id": bubble_id, "anzahl": 0, "verschoben": 0, "positionen": []}
        targets = _grid_positions(nodes, order, columns)
        moved = 0
        for node, (x, y) in zip(nodes, targets):
            if _number(node.get("x")) == x and _number(node.get("y")) == y:
                continue
            _request("PATCH", "canvas_nodes", params={"id": f"eq.{node['id']}"}, body={"x": x, "y": y})
            moved += 1
        return {
            "bubble_id": bubble_id,
            "anzahl": len(nodes),
            "verschoben": moved,
            "positionen": [{"id": node["id"], "x": x, "y": y} for node, (x, y) in zip(nodes, targets)],
        }
    if name == "idea_auto_link":
        _only_arguments(arguments, {"bubble_id", "limit"})
        bubble_id = _durable_id(arguments, "bubble_id")
        limit = arguments.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ToolError("invalid_arguments: 'limit' must be an integer from 1 to 50")
        # Reine Rechnung auf Titeln: bis AUTO_LINK_NODE_CAP Karten (Ordnen bleibt bei 100,
        # weil es jede Karte einzeln verschiebt).
        nodes = _bubble_nodes(bubble_id, "id,title", AUTO_LINK_NODE_CAP)
        candidates = _link_candidates(nodes)
        created: list[dict[str, Any]] = []
        if candidates:
            linked: set[frozenset[Any]] = set()
            # Die Kennungen sind gegen IDEA_ID_PATTERN geprueft: kein Komma, keine Klammer.
            # In Teilen, damit die Adresse auch bei 500 Karten kurz bleibt.
            for start in range(0, len(nodes), AUTO_LINK_ID_CHUNK):
                id_list = "(" + ",".join(node["id"] for node in nodes[start:start + AUTO_LINK_ID_CHUNK]) + ")"
                edges = _request(
                    "GET",
                    "canvas_edges",
                    params={"select": "from_node_id,to_node_id", "from_node_id": f"in.{id_list}", "limit": "5000"},
                )
                if not isinstance(edges, list):
                    raise ToolError("supabase_invalid_response")
                linked.update(
                    frozenset((edge.get("from_node_id"), edge.get("to_node_id")))
                    for edge in edges if isinstance(edge, Mapping)
                )
            for overlap, from_id, to_id in candidates:
                if len(created) >= limit:
                    break
                if frozenset((from_id, to_id)) in linked:
                    continue
                _request(
                    "POST",
                    "canvas_edges",
                    body={"id": uuid.uuid4().hex, "from_node_id": from_id, "to_node_id": to_id, "edge_type": "related"},
                )
                created.append({"from_id": from_id, "to_id": to_id, "ueberlappung": round(overlap, 2)})
        return {"bubble_id": bubble_id, "anzahl": len(created), "angelegt": created}
    if name == "idea_rate":
        _only_arguments(arguments, {"id", "score"})
        idea_id = _durable_id(arguments, "id")
        rating: dict[str, Any] = {"wert": _score(arguments)}
        node = _first_row(_request(
            "GET", "canvas_nodes", params={"select": "id,metadata", "id": f"eq.{idea_id}", "limit": "1"},
        ))
        if node is None:
            raise ToolError("idea_not_found")
        metadata = node.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        metadata["bewertung"] = rating
        updated = _first_row(_request(
            "PATCH", "canvas_nodes", params={"id": f"eq.{idea_id}"}, body={"metadata": metadata},
        ))
        if updated is None:
            raise ToolError("idea_rate_unverified")
        return updated
    if name == "bewertung_begruenden":
        _only_arguments(arguments, {"bubble_id", "text", "idea_ids", "score"})
        bubble_id = _durable_id(arguments, "bubble_id")
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ToolError("invalid_arguments: 'text' must be a non-empty string of at most 2000 characters")
        idea_ids = arguments.get("idea_ids", [])
        if not isinstance(idea_ids, list) or len(idea_ids) > 20:
            raise ToolError("invalid_arguments: 'idea_ids' must be an array of at most 20 ids")
        for idea_id in idea_ids:
            if not isinstance(idea_id, str) or IDEA_ID_PATTERN.fullmatch(idea_id) is None:
                raise ToolError("invalid_arguments: 'idea_ids' must contain canonical durable ids")
        if len(set(idea_ids)) != len(idea_ids):
            raise ToolError("invalid_arguments: 'idea_ids' must not repeat an id")
        entry: dict[str, Any] = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "karten": list(idea_ids)}
        if "score" in arguments:
            entry["wert"] = _score(arguments)
        bubble = _first_row(_request(
            "GET",
            "ideas",
            params={"select": "id,metadata", "id": f"eq.{bubble_id}", "parent_id": "is.null", "limit": "1"},
        ))
        if bubble is None:
            raise ToolError("bubble_not_found")
        if idea_ids:
            # Ein Verweis darf nie ins Leere oder in eine fremde Bubble zeigen.
            # Nur die genannten Karten lesen: so gilt keine Obergrenze fuer die Bubble.
            rows = _request(
                "GET",
                "canvas_nodes",
                params={
                    "select": "id",
                    "id": "in.(" + ",".join(idea_ids) + ")",
                    "linked_idea_id": f"eq.{bubble_id}",
                    "limit": str(len(idea_ids)),
                },
            )
            if not isinstance(rows, list):
                raise ToolError("supabase_invalid_response")
            known = {row.get("id") for row in rows if isinstance(row, Mapping)}
            missing = [idea_id for idea_id in idea_ids if idea_id not in known]
            if missing:
                raise ToolError(f"idea_not_in_bubble: {missing[0]}")
        metadata = bubble.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        entries = metadata.get("bewertungen")
        entries = [item for item in entries if isinstance(item, Mapping)] if isinstance(entries, list) else []
        entry["erstellt_am"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        metadata["bewertungen"] = [*entries, entry][-REASON_ENTRY_CAP:]
        updated = _first_row(_request(
            "PATCH",
            "ideas",
            params={"id": f"eq.{bubble_id}", "parent_id": "is.null"},
            body={"metadata": metadata},
        ))
        if updated is None:
            raise ToolError("bewertung_unverified")
        return {"bubble_id": bubble_id, "bewertung": entry}
    if name == "bewertung_entfernen":
        # Eine Begruendung wieder wegnehmen; "Zurueck" holt sie ueber das Aenderungsbuch (ideas).
        _only_arguments(arguments, {"bubble_id", "nummer", "idea_id"})
        bubble_id = _durable_id(arguments, "bubble_id")
        nummer = arguments.get("nummer")
        if not isinstance(nummer, int) or isinstance(nummer, bool) or not 1 <= nummer <= REASON_ENTRY_CAP:
            raise ToolError(f"invalid_arguments: 'nummer' must be an integer from 1 to {REASON_ENTRY_CAP}")
        idea_id = _durable_id(arguments, "idea_id") if "idea_id" in arguments else None
        bubble = _first_row(_request(
            "GET",
            "ideas",
            params={"select": "id,metadata", "id": f"eq.{bubble_id}", "parent_id": "is.null", "limit": "1"},
        ))
        if bubble is None:
            raise ToolError("bubble_not_found")
        metadata = bubble.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        entries = metadata.get("bewertungen")
        entries = [item for item in entries if isinstance(item, Mapping)] if isinstance(entries, list) else []
        # Gezaehlt wie angezeigt: neueste zuerst (spaces/ideas/ANZEIGE.md, "Begruendungen").
        stellen = [i for i in range(len(entries) - 1, -1, -1)
                   if idea_id is None or idea_id in (entries[i].get("karten") or [])]
        if nummer > len(stellen):
            raise ToolError("bewertung_not_found")
        removed = entries[stellen[nummer - 1]]
        metadata["bewertungen"] = [item for i, item in enumerate(entries) if i != stellen[nummer - 1]]
        updated = _first_row(_request(
            "PATCH",
            "ideas",
            params={"id": f"eq.{bubble_id}", "parent_id": "is.null"},
            body={"metadata": metadata},
        ))
        if updated is None:
            raise ToolError("bewertung_unverified")
        return {"bubble_id": bubble_id, "entfernt": dict(removed)}
    if name == "bubble_wurzel":
        _only_arguments(arguments, {"bubble_id", "id"})
        bubble_id = _durable_id(arguments, "bubble_id")
        root_id = _durable_id(arguments, "id") if "id" in arguments else None
        bubble = _first_row(_request(
            "GET",
            "ideas",
            params={"select": "id,metadata", "id": f"eq.{bubble_id}", "parent_id": "is.null", "limit": "1"},
        ))
        if bubble is None:
            raise ToolError("bubble_not_found")
        if root_id is not None:
            card = _first_row(_request(
                "GET",
                "canvas_nodes",
                params={"select": "id", "id": f"eq.{root_id}", "linked_idea_id": f"eq.{bubble_id}", "limit": "1"},
            ))
            if card is None:
                raise ToolError(f"idea_not_in_bubble: {root_id}")
        metadata = bubble.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        if root_id is None:
            metadata.pop("wurzel", None)
        else:
            metadata["wurzel"] = root_id
        updated = _first_row(_request(
            "PATCH",
            "ideas",
            params={"id": f"eq.{bubble_id}", "parent_id": "is.null"},
            body={"metadata": metadata},
        ))
        if updated is None:
            raise ToolError("bubble_wurzel_unverified")
        return {"bubble_id": bubble_id, "wurzel": root_id}
    if name == "idea_group":
        return _group(arguments)
    if name == "bubble_rate":
        _only_arguments(arguments, {"id", "score"})
        bubble_id = _durable_id(arguments, "id")
        updated = _first_row(_request(
            "PATCH",
            "ideas",
            params={"id": f"eq.{bubble_id}", "parent_id": "is.null"},
            body={"score": _score(arguments)},
        ))
        if updated is None:
            raise ToolError("bubble_not_found")
        return updated
    if name == "aenderung_liste":
        _only_arguments(arguments, {"limit", "quelle"})
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ToolError("invalid_arguments: 'limit' must be an integer from 1 to 50")
        params = {
            "select": (
                "id,gruppe,quelle,erstellt_am,tabelle,vorgang,zeilen_id,zurueckgenommen_am,"
                "vorher_titel:vorher->>title,nachher_titel:nachher->>title,"
                "vorher_name:vorher->>name,nachher_name:nachher->>name"
            ),
            "order": "id.desc",
            "limit": str(CHANGE_LIST_ROW_CAP),
        }
        if "quelle" in arguments:
            source = arguments["quelle"]
            if source not in CHANGE_SOURCES:
                raise ToolError("invalid_arguments: 'quelle' must be agent, hand or zurueck")
            params["quelle"] = f"eq.{source}"
        return _change_groups(_request("GET", "ideen_aenderungen", params=params), limit)
    if name == "aenderung_zurueck":
        _only_arguments(arguments, {"gruppe"})
        body: dict[str, Any] = {}
        if "gruppe" in arguments:
            group = arguments["gruppe"]
            if (
                not isinstance(group, str)
                or not 1 <= len(group) <= 200
                or group != group.strip()
                or not group.isprintable()
            ):
                raise ToolError("invalid_arguments: 'gruppe' must be a group name from aenderung_liste")
            body["p_gruppe"] = group
        result = _request("POST", "rpc/ideen_zurueck", body=body, error_detail=True)
        if not isinstance(result, Mapping) or "anzahl" not in result:
            raise ToolError("supabase_invalid_response")
        kinds = _change_kinds(result)
        return {**result, "arten": kinds} if kinds else result
    raise ToolError(f"unknown_tool: {name}")


def _tool_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def handle_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Handle the supported JSON-RPC MCP methods without writing to stdout."""
    request_id = message.get("id")
    method = message.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": "invalid_arguments: params must be an object"}, is_error=True)}
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": "invalid_arguments: name and arguments are required"}, is_error=True)}
        try:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(call_tool(name, arguments))}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result({"error": str(exc)}, is_error=True)}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "method not found"},
    }


def main() -> int:
    """Serve JSON-RPC lines over stdio until the input ends.

    Bytes in, bytes out: the request is UTF-8 whatever the console code page says, and
    the response is pure ASCII (``ensure_ascii``). On Windows a redirected stdout is
    cp1252 unless PYTHONIOENCODING is set; an arrow or emoji in a note then made
    ``print`` fail and ended the process ("Transport closed" in OpenFang, 2026-11-23).
    One failing request must never end the loop.
    """
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for raw_line in stdin:
        if not raw_line.strip():
            continue
        request_id = None
        try:
            message = json.loads(raw_line.decode("utf-8"))
            if not isinstance(message, Mapping):
                raise ValueError("request must be an object")
            request_id = message.get("id")
            response = handle_message(message)
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        except Exception as exc:  # noqa: BLE001 - never let one request end the server
            response = {
                "jsonrpc": "2.0", "id": request_id,
                "result": _tool_result({"error": f"internal_error: {type(exc).__name__}"}, is_error=True),
            }
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
