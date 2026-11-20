"""Kleine Bausteine, mit denen die Tests Bubbles, Ideen und Anhaenge anlegen."""
from __future__ import annotations


def bubble(v, bid: str, titel: str = "Bubble") -> str:
    v.execute("INSERT INTO public.ideas (id, title, status) VALUES (%s, %s, 'active')", (bid, titel))
    return bid


def idee(v, nid: str, bubble_id: str | None, titel: str = "Idee", inhalt: str = "") -> str:
    v.execute(
        "INSERT INTO public.canvas_nodes (id, title, content, linked_idea_id) VALUES (%s, %s, %s, %s)",
        (nid, titel, inhalt, bubble_id),
    )
    return nid


def kante(v, kid: str, von: str, nach: str) -> str:
    v.execute(
        "INSERT INTO public.canvas_edges (id, from_node_id, to_node_id) VALUES (%s, %s, %s)",
        (kid, von, nach),
    )
    return kid


def shuttle(v, sid: str, bubble_id: str) -> str:
    v.execute(
        "INSERT INTO public.shuttles (id, shuttle_id, bubble_id, bubble_name) VALUES (%s, %s, %s, 'B')",
        (sid, sid, bubble_id),
    )
    return sid


def erkundung(v, eid: str, bubble_id: str) -> str:
    v.execute("INSERT INTO public.exploration_sessions (id, root_bubble_id) VALUES (%s, %s)", (eid, bubble_id))
    return eid


def zurueck(v, gruppe: str | None = None) -> dict:
    cur = v.execute("SELECT public.ideen_zurueck(%s)", (gruppe,))
    return cur.fetchone()[0]
