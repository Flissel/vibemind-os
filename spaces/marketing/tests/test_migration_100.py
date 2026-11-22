"""Statische Pruefung der Migration 100 (Datenbank-Huellen fuer das Gehirn) und ihrer Verifikation.

Kein Datenbankzugriff: Der echte Lauf ist die migration_probe (100 + verify_100 in ROLLBACK).
"""
import re
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "db"
MIGRATION = DB / "100_auftragsbuch_brain.sql"
VERIFY = DB / "verify_100.sql"

NEUE_FUNKTIONEN = {
    "brain_anlegen": "auftrag.brain_anlegen(text, text, int, text, text, jsonb, text, int)",
    "brain_lesen": "auftrag.brain_lesen(uuid)",
    "brain_probe_erledigen": "auftrag.brain_probe_erledigen(uuid)",
}

ABLEHNUNGEN = (
    "Unbekannte Auftragsart",
    "Auftrag braucht Laden und Mandant",
    "Frist ausserhalb 1 bis 120 Minuten",
    "Plan-Kennung ungueltig",
    "Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag",
    "Tagesgrenze erreicht: hoechstens 50 Proben pro Tag",
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code_zeilen(s: str) -> list[str]:
    return [z.strip() for z in s.splitlines() if z.strip() and not z.strip().startswith("--")]


def test_kopf_und_transaktion():
    s = _sql()
    assert s.startswith("-- RUNBOOK: nach 099. Idempotent, eine Transaktion. Danach verify_100 (in ROLLBACK).")
    zeilen = _code_zeilen(s)
    assert zeilen[0] == "BEGIN;"
    assert zeilen[-1] == "COMMIT;"
    assert zeilen.count("BEGIN;") == 1 and zeilen.count("COMMIT;") == 1


def test_altbestand_wird_nicht_neu_definiert():
    s = _sql()
    assert "CREATE OR REPLACE FUNCTION auftrag.anlegen" not in s
    assert "auftrag.stand_setzen(" not in s
    definiert = re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(auftrag\.\w+)", s, flags=re.IGNORECASE)
    assert sorted(definiert) == sorted(f"auftrag.{n}" for n in NEUE_FUNKTIONEN)


def test_neue_funktionen_security_definer_und_revoke():
    s = _sql()
    teile = re.split(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+", s)[1:]
    assert len(teile) == len(NEUE_FUNKTIONEN)
    for teil in teile:
        kopf = teil.split("AS $$", 1)[0]
        assert "SECURITY DEFINER" in kopf, kopf[:60]
        assert "SET search_path = auftrag, pg_temp" in kopf, kopf[:60]
    for name, signatur in NEUE_FUNKTIONEN.items():
        assert f"REVOKE ALL ON FUNCTION {signatur} FROM PUBLIC;" in s, name
        assert f"GRANT EXECUTE ON FUNCTION {signatur} TO brain_app;" in s, name


def test_rolle_ohne_passwort_und_nur_diese_rechte():
    s = _sql()
    assert "password" not in s.lower()
    assert "NOLOGIN" in s
    assert "GRANT USAGE ON SCHEMA auftrag TO brain_app;" in s
    # nur die drei neuen Funktionen, keine Tabellenrechte, kein Rechte-Sammelgrant
    grants = re.findall(r"^GRANT\s.*$", s, flags=re.MULTILINE)
    assert len(grants) == 1 + len(NEUE_FUNKTIONEN), grants
    assert not re.search(r"GRANT\s+(?:ALL|SELECT|INSERT|UPDATE|DELETE)\b", s)
    assert "ALL FUNCTIONS" not in s


def test_spalten_tabelle_und_check():
    s = _sql()
    assert "art_version int" in s and "plan_id text" in s
    assert "ADD COLUMN IF NOT EXISTS art_version int" in s
    assert "ADD COLUMN IF NOT EXISTS plan_id text" in s
    assert "CREATE TABLE IF NOT EXISTS auftrag.brain_arten" in s
    assert "PRIMARY KEY (an, art, version)" in s
    assert "('brain', 'probe', 1)" in s
    assert "CHECK (art IN ('reel', 'video') OR von = 'brain')" in s
    assert "auftraege_stand_chk" not in s


def test_brain_anlegen_schreibt_selbst_und_zaehlt_unter_sperre():
    s = _sql()
    assert "pg_advisory_xact_lock(hashtext('auftrag:brain'))" in s
    assert "'Europe/Berlin'" in s
    for text in ABLEHNUNGEN:
        assert f"RAISE EXCEPTION '{text}'" in s, text


def test_verify_100_deckt_die_ablehnungen_und_die_rolle_ab():
    v = VERIFY.read_text(encoding="utf-8")
    for text in ABLEHNUNGEN:
        assert text in v, text
    assert "Unbekannte Art des Auftrags" in v
    assert "brain_app" in v
    for signatur in ("brain_anlegen(text,text,int,text,text,jsonb,text,int)", "brain_lesen(uuid)",
                     "brain_probe_erledigen(uuid)"):
        assert signatur in v, signatur
    # die Probe haengt BEGIN/COMMIT selbst an und entfernt eigene Klammerzeilen
    assert not re.search(r"^[ \t]*(BEGIN|COMMIT|ROLLBACK)[ \t]*;[ \t]*$", v, flags=re.IGNORECASE | re.MULTILINE)
    assert "password" not in v.lower()


def test_verify_100_blockstruktur():
    v = VERIFY.read_text(encoding="utf-8")
    for n in range(1, 7):
        assert f"100.{n}:" in v, n
    assert v.count("DO $$") == v.count("END $$;")


def test_sperrwunsch_bricht_nach_fuenf_sekunden_ab():
    zeilen = _code_zeilen(_sql())
    assert zeilen[1] == "SET LOCAL lock_timeout = '5s';"


def test_tabellenrechte_nur_fuer_vorhandene_rollen_entzogen():
    s = _sql()
    assert "REVOKE ALL ON TABLE auftrag.brain_arten FROM PUBLIC;" in s
    # anon/authenticated gibt es nicht ueberall: je Rolle erst auf Existenz pruefen
    block = s.split("REVOKE ALL ON TABLE auftrag.brain_arten FROM PUBLIC;", 1)[1].split("END $$;", 1)[0]
    assert "ARRAY['anon', 'authenticated', 'sales_app']" in block
    assert "FROM pg_roles WHERE rolname = r" in block
    assert "format('REVOKE ALL ON TABLE auftrag.brain_arten FROM %I', r)" in block


def test_verify_100_prueft_tabellenrechte_und_alle_funktionen_des_schemas():
    v = VERIFY.read_text(encoding="utf-8")
    assert "ARRAY['public', 'anon', 'authenticated', 'sales_app']" in v
    for recht in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert f"'{recht}'" in v
    assert "has_table_privilege(v_rolle, 'auftrag.brain_arten', v_recht)" in v
    # Schleife ueber pg_proc im Schema auftrag: brain_app darf genau die drei Funktionen
    assert "FROM pg_proc" in v and "nspname = 'auftrag'" in v
    assert "has_function_privilege('brain_app', p.oid, 'EXECUTE')" in v
