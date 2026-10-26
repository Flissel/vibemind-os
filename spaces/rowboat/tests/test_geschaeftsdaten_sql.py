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


def test_rolle_in_einer_transaktion():
    zeilen = [z.strip().lower() for z in lesen("rowboat_leser.sql").splitlines()
              if z.strip() and not z.strip().startswith("--")]
    assert zeilen[0] == "\set on_error_stop on" and zeilen[1] == "begin;" and zeilen[-1] == "commit;"
    assert sum(z == "begin;" for z in zeilen) == 1 and sum(z == "commit;" for z in zeilen) == 1


def test_empfehler_name_nur_mit_liste_und_ohne_sperre():
    sql = " ".join(lesen("rowboat_leser.sql").lower().split())
    # Name und id des Empfehlungsgebers nur, wenn die Liste gefunden wurde und der Geber nicht gesperrt ist.
    assert sql.count("e.id is not null and (e.empfohlen_von_lead is null or ek.gesperrt = false)") == 1
    assert "e.id is not null and ek.gesperrt = false then e.empfohlen_von_lead" in sql
    assert "not coalesce(ek.gesperrt, false)" not in sql


def test_verify_allgemeine_negativpruefung():
    sql = lesen("verify_rowboat_leser.sql").lower()
    assert "has_table_privilege('rowboat_leser'" in sql and "has_column_privilege('rowboat_leser'" in sql
    for erlaubt in ("rowboat_kontakte", "rowboat_ereignisse", "rowboat_newsletter",
                    "array['id', 'name']", "array['inhalt', 'fassung', 'format', 'felder', 'bloecke', 'seite']"):
        assert erlaubt in sql, erlaubt
