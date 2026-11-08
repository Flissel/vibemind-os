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
                     "begruendung", "briefing", "skript"):
        assert verboten not in sql, verboten
    assert "grant select on all tables" not in sql and "bypassrls" not in sql
    # Termin-Thema und -Ort sind seit 08.11.2026 erlaubt, aber nur fuer Termine.
    for schluessel in ("'thema'", "'ort'"):
        assert sql.count(schluessel) == 1
        assert f"case when a.type = 'termin' then a.payload->>{schluessel} end" in sql


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


def test_ereignisse_termin_details_am_ende():
    sql = lesen("rowboat_leser.sql")
    teil = sql[sql.index("rowboat_ereignisse with (security_barrier)"):]
    for spalte in ("termin_dauer", "termin_thema", "termin_ort"):
        assert "case when a.type = 'termin' then a.payload->>" in teil
        assert spalte in teil
    # neue Spalten nach empfohlen_von_lead (create or replace view darf nur hinten anfuegen)
    assert teil.index("empfohlen_von_lead") < teil.index("termin_dauer")


def test_post_offen_regel_wie_sales_ui():
    sql = lesen("rowboat_leser.sql")
    teil = sql[sql.index("rowboat_post_offen"):]
    for bedingung in ("m.ziel = 'posteingang'", "m.status = 'eingeordnet'", "m.erledigt_am is null",
                      "m.abgeglichen_am is not null", "m.geloescht_am is null", "m.besitzer = 'felix'"):
        assert bedingung in teil
    for verboten in ("m.text", "m.auszug", "m.anhaenge", "m.an "):
        assert verboten not in teil
    assert "security_barrier" in teil
    assert "grant select on sales.rowboat_post_offen to rowboat_leser" in sql


def test_post_offen_gesperrt_und_sensibel_leeren():
    sql = lesen("rowboat_leser.sql")
    teil = sql[sql.index("rowboat_post_offen"):]
    assert "case when g.gesperrt or m.sensibel is not false then null else m.betreff end" in teil
    # sensible Mail: auch Absender (Adresse und Name) leer, nicht nur der Betreff
    assert "case when g.gesperrt or m.sensibel is not false then null else ad.adr end as absender_klein" in teil
    assert "case when g.gesperrt or m.sensibel is not false then null else" in teil
    assert "end as von_anzeige" in teil


def test_post_offen_adresse_einheitlich_normalisiert_und_fail_closed():
    sql = lesen("rowboat_leser.sql")
    teil = " ".join(sql[sql.index("rowboat_post_offen"):].split())
    norm = "lower(btrim(coalesce(substring(m.von from '<([^>]+)>'), m.von), E' \\t\\r\\n')) as adr"
    assert teil.count(norm) == 1
    assert "lower(btrim(l.email, E' \\t\\r\\n')) = ad.adr" in teil
    assert "trim(m.von)" not in teil
    assert "ad.adr is null or ad.adr = ''" in teil


def test_verify_post_offen():
    sql = lesen("verify_rowboat_leser.sql").lower()
    assert "rowboat_post_offen" in sql and "sales.post_mails" in sql and "information_schema.columns" in sql
