"""Statische Pruefung der Migration 101 (gemeinsamer Empfangskern, Huellen fuer Sales und Rowboat) und ihrer Verifikation.

Kein Datenbankzugriff: Der echte Lauf ist die migration_probe (096, 098, 100, 101 + verify_101 in ROLLBACK).
"""
import re
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "db"
MIGRATION = DB / "101_auftragsbuch_empfang.sql"
VERIFY = DB / "verify_101.sql"

KERN = {
    "_empfang_naechster": "auftrag._empfang_naechster(text, text, interval)",
    "_empfang_abschliessen": "auftrag._empfang_abschliessen(text, uuid, jsonb, text)",
    "_empfang_fehler": "auftrag._empfang_fehler(text, uuid, text, text)",
}
SALES = {
    "sales_empfang_naechster": "auftrag.sales_empfang_naechster(text, interval)",
    "sales_empfang_abschliessen": "auftrag.sales_empfang_abschliessen(uuid, jsonb, text)",
    "sales_empfang_fehler": "auftrag.sales_empfang_fehler(uuid, text, text)",
}
ROWBOAT = {
    "rowboat_empfang_naechster": "auftrag.rowboat_empfang_naechster(text, interval)",
    "rowboat_empfang_abschliessen": "auftrag.rowboat_empfang_abschliessen(uuid, jsonb, text)",
    "rowboat_empfang_fehler": "auftrag.rowboat_empfang_fehler(uuid, text, text)",
    "rowboat_empfang_bekannt": "auftrag.rowboat_empfang_bekannt(text, text[])",
}
# Altbestand, der hier ersetzt wird (gleiche Signatur und Rueckgabe)
ERSETZT = ("auftrag._vergeben", "auftrag.brain_lesen")

FESTE_TEXTE = (
    "Der Laden fehlt",
    "Leihdauer ausserhalb 0 bis 15 Minuten",
    "Bearbeitung bei Sales abgebrochen",
    "Bearbeitung bei Rowboat abgebrochen",
    "Fehler ohne Angabe",
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code_zeilen(s: str) -> list[str]:
    return [z.strip() for z in s.splitlines() if z.strip() and not z.strip().startswith("--")]


def _funktionen(s: str) -> dict[str, str]:
    teile = re.split(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+", s)[1:]
    return {re.match(r"(auftrag\.\w+)", t).group(1): t for t in teile}


def test_kopf_transaktion_und_sperrwunsch():
    s = _sql()
    assert s.startswith("-- RUNBOOK: nach 100. Idempotent, eine Transaktion. Danach verify_101 (in ROLLBACK).")
    zeilen = _code_zeilen(s)
    assert zeilen[0] == "BEGIN;"
    assert zeilen[1] == "SET LOCAL lock_timeout = '5s';"
    assert zeilen[-1] == "COMMIT;"
    assert zeilen.count("BEGIN;") == 1 and zeilen.count("COMMIT;") == 1


def test_idempotent_und_ohne_drop():
    s = _sql()
    assert not re.search(r"\bDROP\s+(FUNCTION|TABLE|ROLE|SCHEMA)\b", s, flags=re.IGNORECASE)
    assert not re.search(r"CREATE\s+FUNCTION", s, flags=re.IGNORECASE)
    assert "CREATE ROLE rowboat_empfang NOLOGIN;" in s
    assert "NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rowboat_empfang')" in s
    assert "password" not in s.lower()


def test_definierte_funktionen_sind_genau_die_erwarteten():
    s = _sql()
    soll = set(f"auftrag.{n}" for n in (*KERN, *SALES, *ROWBOAT)) | set(ERSETZT)
    assert set(_funktionen(s)) == soll
    assert len(re.findall(r"CREATE\s+OR\s+REPLACE\s+FUNCTION", s)) == len(soll)


def test_jede_funktion_security_definer_mit_festem_search_path():
    for name, teil in _funktionen(_sql()).items():
        kopf = teil.split("AS $$", 1)[0]
        assert "SECURITY DEFINER" in kopf, name
        assert "SET search_path = auftrag, pg_temp" in kopf, name


def test_revoke_public_fuer_jede_neue_funktion():
    s = _sql()
    for sig in (*KERN.values(), *SALES.values(), *ROWBOAT.values()):
        assert f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC;" in s, sig


def test_execute_genau_sales_app_drei_und_rowboat_vier_kern_ohne():
    s = _sql()
    grants = re.findall(r"^GRANT\s.*$", s, flags=re.MULTILINE)
    erwartet = [f"GRANT EXECUTE ON FUNCTION {sig} TO sales_app;" for sig in SALES.values()]
    erwartet += [f"GRANT EXECUTE ON FUNCTION {sig} TO rowboat_empfang;" for sig in ROWBOAT.values()]
    erwartet += ["GRANT USAGE ON SCHEMA auftrag TO rowboat_empfang;"]
    assert sorted(grants) == sorted(erwartet), grants
    for sig in KERN.values():
        assert not re.search(rf"GRANT[^;]*{re.escape(sig)}", s)
    assert not re.search(r"GRANT\s+(?:ALL|SELECT|INSERT|UPDATE|DELETE)\b", s)
    assert "ALL FUNCTIONS" not in s
    for name in ERSETZT:
        assert not re.search(rf"GRANT[^;]*{re.escape(name)}", s), name


def test_huellen_haben_festen_empfaenger_und_gleiche_rueckgabe():
    s = _sql()
    f = _funktionen(s)
    for n, rueck in (("naechster", "jsonb"), ("abschliessen", "boolean"), ("fehler", "boolean")):
        for an in ("sales", "rowboat"):
            teil = f[f"auftrag.{an}_empfang_{n}"]
            assert re.search(rf"RETURNS\s+{rueck}\b", teil.split("AS $$", 1)[0]), (an, n)
            assert f"auftrag._empfang_{n}('{an}'," in teil, (an, n)
    # Parameternamen der Sales-Huellen bleiben (sonst scheitert CREATE OR REPLACE)
    assert "sales_empfang_naechster(p_laden text, p_lease interval)" in s
    assert "sales_empfang_abschliessen(p_id uuid, p_ergebnis jsonb, p_laden text)" in s
    assert "sales_empfang_fehler(p_id uuid, p_fehler text, p_laden text)" in s


def test_kern_verhalten_wie_sales_vorlage():
    f = _funktionen(_sql())
    nae = f["auftrag._empfang_naechster"]
    assert "von = 'brain'" in nae and "an = p_an" in nae and "laden = p_laden" in nae
    assert "p_lease <= interval '0'" in nae and "p_lease > interval '15 minutes'" in nae
    assert "FOR UPDATE SKIP LOCKED" in nae
    assert "stand = 'in_arbeit' AND vergeben_bis < now()" in nae
    assert "frist_bis > now()" in nae
    assert "versuche = versuche + 1" in nae
    abs_ = f["auftrag._empfang_abschliessen"]
    assert "von = 'brain'" in abs_ and "an = p_an" in abs_ and "laden = p_laden" in abs_
    assert "frist_bis > now() AND vergeben_bis >= now()" in abs_
    assert "pg_column_size(p_ergebnis) > 65536" in abs_
    assert "jsonb_typeof(p_ergebnis) IS DISTINCT FROM 'object'" in abs_
    feh = f["auftrag._empfang_fehler"]
    assert "von = 'brain'" in feh and "an = p_an" in feh and "laden = p_laden" in feh
    assert "stand = 'in_arbeit'" in feh
    assert "left(" in feh and ", 300)" in feh
    for teil in (nae, abs_, feh):
        assert "p_an NOT IN ('sales', 'rowboat')" in teil


def test_feste_gruende_woertlich():
    s = _sql()
    for text in FESTE_TEXTE:
        assert text in s, text


def test_vergeben_schliesst_gehirn_aus_und_bleibt_sonst_gleich():
    v = _funktionen(_sql())["auftrag._vergeben"]
    kopf = v.split("AS $$", 1)[0]
    assert "AND von <> 'brain'" in v
    assert "RETURNS jsonb" in kopf
    assert "(p_an text, p_video boolean, p_lease interval)" in kopf
    assert "FOR UPDATE SKIP LOCKED" in v and "Zwei Versuche ohne Ergebnis" in v
    assert "auftrag._uebergabe(a)" in v


def test_brain_lesen_laesst_ueberfaellige_ablaufen():
    b = _funktionen(_sql())["auftrag.brain_lesen"]
    kopf = b.split("AS $$", 1)[0]
    assert "brain_lesen(p_id uuid) RETURNS jsonb" in kopf
    assert "STABLE" not in kopf
    assert "stand = 'abgelaufen'" in b
    assert "stand IN ('offen', 'in_arbeit')" in b and "frist_bis < now()" in b
    assert "von = 'brain'" in b
    assert "to_jsonb(a)" in b


def test_bekannt_liest_nur_fertige_rowboat_vorschlaege_im_eigenen_laden():
    b = _funktionen(_sql())["auftrag.rowboat_empfang_bekannt"]
    assert "RETURNS text[]" in b.split("AS $$", 1)[0]
    for teil in ("an = 'rowboat'", "von = 'brain'", "art = 'vorschlaege_anlegen'", "stand = 'fertig'",
                 "laden = p_laden", "'punkte'", "'punkt_id'"):
        assert teil in b, teil


def test_keine_art_freigeschaltet():
    s = _sql()
    assert not re.search(r"INSERT\s+INTO\s+auftrag\.brain_arten", s, flags=re.IGNORECASE)
    assert "brain_arten" not in "\n".join(_code_zeilen(s))


def test_altes_naechster_und_anlegen_nicht_angefasst():
    s = _sql()
    assert "auftrag.naechster(" not in s
    assert "auftrag.stand_setzen(" not in s
    assert "CREATE OR REPLACE FUNCTION auftrag.anlegen" not in s


def test_verify_101_inhalt():
    v = VERIFY.read_text(encoding="utf-8")
    assert not re.search(r"^[ \t]*(BEGIN|COMMIT|ROLLBACK)[ \t]*;[ \t]*$", v, flags=re.IGNORECASE | re.MULTILINE)
    assert "password" not in v.lower()
    assert v.count("DO $$") == v.count("END $$;")
    for n in range(1, 7):
        assert f"101.{n}:" in v, n
    # brain/probe/1 muss vorhanden sein, aber nicht einzige Zeile
    assert "an = 'brain' AND art = 'probe' AND version = 1" in v
    assert "count(*) FROM auftrag.brain_arten" not in v
    for rolle in ("rowboat_empfang", "sales_app"):
        assert rolle in v
    for sig in (*SALES.values(), *ROWBOAT.values(), *KERN.values()):
        assert sig.replace(", ", ",") in v, sig
    assert "has_function_privilege('public'" in v
    assert "rolcanlogin" in v
    assert "FROM pg_proc" in v and "nspname = 'auftrag'" in v
