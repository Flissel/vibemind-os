"""Texttests gegen das Rollen-Skript `rowboat_leser.sql` (Teil B, Task 1).

Die Datenbank-Pruefung selbst steht in `verify_rowboat_leser.sql` und laeuft auf der VM
in einer Transaktion mit ROLLBACK. Hier wird nur der Text geprueft: keine Inhaltsspalten,
Sperr-Regel in jeder Datenspalte, Laden-Schleife ohne `sales_test`, Rolle read-only.
"""
from __future__ import annotations

from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "geschaeftsdaten" / "db"


def lesen(name: str) -> str:
    return (DB / name).read_text(encoding="utf-8")


def test_kein_zugriff_auf_inhaltsspalten():
    sql = lesen("rowboat_leser.sql").lower()
    for verboten in ("body", "body_html", "notes", "phone", "payload->>'text'", "payload->>'inhalt'",
                     "begruendung", "briefing", "skript", "'thema'", "'ort'"):
        assert verboten not in sql, verboten
    assert "grant select on all tables" not in sql and "bypassrls" not in sql


def test_sicht_nullt_gesperrte():
    sql = lesen("rowboat_leser.sql").lower()
    assert "enrichment ? '_privat'" in sql and "enrichment ? '_loeschantrag'" in sql
    assert sql.count("case when p.gesperrt then null") >= 10   # alle Datenspalten


def test_schleife_ohne_sales_test_und_rolle_read_only():
    sql = lesen("rowboat_leser.sql").lower()
    assert "^sales(_[a-z][a-z0-9_]{0,30})?$" in sql and "<> 'sales_test'" in sql
    assert "default_transaction_read_only = on" in sql and "password null" in sql


def test_verify_endet_mit_rollback():
    assert lesen("verify_rowboat_leser.sql").strip().lower().endswith("rollback;")
