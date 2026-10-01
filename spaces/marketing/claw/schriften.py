"""Schrift-Register der Newsletter-Vorlagen (sales-claw Spec 2026-10-01-newsletter-
vorlagen-profi-design.md §4). Alle Schriften stehen unter der SIL Open Font
License und werden von sales-ui unter /marketing/schrift/ ausgeliefert - nie
von Google (DSGVO). Die IDs muessen mit sales-mcp/schriften.py (sales-claw)
und dem Editor-Schema uebereinstimmen."""
from __future__ import annotations

REGISTER: dict[str, dict] = {
    "cormorant": {"familie": "Cormorant Garamond", "stapel": "Georgia, 'Times New Roman', serif",
                  "dateien": [(400, "normal"), (400, "italic")]},
    "dm-sans": {"familie": "DM Sans", "stapel": "Arial, Helvetica, sans-serif",
                "dateien": [(400, "normal"), (700, "normal")]},
    "playfair": {"familie": "Playfair Display", "stapel": "Georgia, 'Times New Roman', serif",
                 "dateien": [(900, "normal")]},
    "poppins": {"familie": "Poppins", "stapel": "Arial, Helvetica, sans-serif",
                "dateien": [(400, "normal"), (600, "normal"), (700, "normal")]},
    "young-serif": {"familie": "Young Serif", "stapel": "Georgia, serif", "dateien": [(400, "normal")]},
    "manrope": {"familie": "Manrope", "stapel": "Arial, Helvetica, sans-serif",
                "dateien": [(300, "normal"), (400, "normal"), (700, "normal")]},
    "bodoni": {"familie": "Bodoni Moda", "stapel": "Didot, Georgia, serif",
               "dateien": [(500, "normal"), (500, "italic")]},
    "montserrat": {"familie": "Montserrat", "stapel": "Arial, Helvetica, sans-serif",
                   "dateien": [(400, "normal"), (600, "normal")]},
    "josefin": {"familie": "Josefin Sans", "stapel": "'Trebuchet MS', Arial, sans-serif",
                "dateien": [(300, "normal"), (700, "normal")]},
    "oxanium": {"familie": "Oxanium", "stapel": "'Trebuchet MS', Arial, sans-serif",
                "dateien": [(600, "normal"), (700, "normal")]},
    "rajdhani": {"familie": "Rajdhani", "stapel": "'Arial Narrow', Arial, sans-serif",
                 "dateien": [(500, "normal"), (600, "normal")]},
}


def css_familie(sid: str) -> str:
    s = REGISTER.get(sid) if isinstance(sid, str) else None
    return f"'{s['familie']}', {s['stapel']}" if s else ""
