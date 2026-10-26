"""Tests fuer die Datenquelle ueber rowboat_leser (Teil B, Task 4)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from geschaeftsdaten_fake import FakeQuelle, kontakt  # noqa: E402
from quelle import DbFehler, Kontakt, PsqlQuelle  # noqa: E402

C = "debian-supabase-db-1"
LID = "aaaaaaaa-0000-4000-8000-000000000001"
NID = "bbbbbbbb-0000-4000-8000-000000000001"


class Laeufer:
    def __init__(self, stdout: str = "[]", rc: int = 0, stderr: str = "") -> None:
        self.stdout, self.rc, self.stderr = stdout, rc, stderr
        self.aufrufe: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kw):
        self.aufrufe.append((argv, kw))
        return SimpleNamespace(returncode=self.rc, stdout=self.stdout, stderr=self.stderr)


def _kontakt_zeile(**ueber) -> dict:
    z = {"id": LID, "gesperrt": False, "archiviert": False, "name": "Max", "company": None, "title": None,
         "status": "new", "consent_status": "opt_in", "source": "manuell", "email_klein": "m@x.de",
         "art": "person", "bereiche": ["bav", 3, "it"], "created_at": "2026-10-01T09:00:00.123456+00:00",
         "updated_at": "2026-10-20T11:00:00+02:00"}
    z.update(ueber)
    return z


def test_argv_ohne_geheimnis():
    l = Laeufer()
    PsqlQuelle(C, laeufer=l).kontakte("sales")
    argv, kw = l.aufrufe[0]
    assert argv == ["docker", "exec", "-i", C, "psql", "-h", "127.0.0.1", "-U", "rowboat_leser", "-d", "postgres",
                    "-v", "ON_ERROR_STOP=1", "-At", "-f", "-"]
    assert "rowboat_kontakte" in kw["input"] and "sales." in kw["input"]
    assert "env" not in kw


def test_json_zu_kontakten():
    l = Laeufer(json.dumps([_kontakt_zeile(), _kontakt_zeile(id="x", bereiche=None, updated_at=None)]))
    k1, k2 = PsqlQuelle(C, laeufer=l).kontakte("sales_ivan")
    assert isinstance(k1, Kontakt) and k1.bereiche == ("bav", "it") and k1.email_klein == "m@x.de"
    assert k1.created_at.tzinfo is not None and k1.updated_at.utcoffset() == timedelta(hours=2)
    assert k2.bereiche == () and k2.updated_at is None


def test_ereignisse_versand_marken():
    q = PsqlQuelle(C, laeufer=Laeufer(json.dumps([{
        "lead_id": LID, "typ": "termin", "zeit": "2026-10-01T09:00:00+00:00", "termin_uid": "u1",
        "termin_datum": "2026-10-30", "termin_uhrzeit": "10:00", "empfohlen_von_name": None,
        "empfohlen_von_lead": None}])))
    (e,) = q.ereignisse("sales")
    assert e.typ == "termin" and e.termin_datum == "2026-10-30" and e.zeit.tzinfo is not None
    q = PsqlQuelle(C, laeufer=Laeufer(json.dumps([{"newsletter": NID, "erster_versand": "2026-10-24T22:30:00+00:00",
                                                    "empfaenger": 2}])))
    (v,) = q.versand("sales")
    assert v.empfaenger == 2 and v.erster_versand.tzinfo is not None
    q = PsqlQuelle(C, laeufer=Laeufer(json.dumps([{"id": "vibemind", "name": "VibeMind"}])))
    assert q.marken() == {"vibemind": "VibeMind"}


def test_hat_sichten():
    assert PsqlQuelle(C, laeufer=Laeufer('[{"da": true}]')).hat_sichten("sales") is True
    l = Laeufer('[{"da": false}]')
    assert PsqlQuelle(C, laeufer=l).hat_sichten("sales_ivan") is False
    sql = l.aufrufe[0][1]["input"]
    assert "to_regclass('sales_ivan.rowboat_kontakte')" in sql
    # Neuer Laden ohne USAGE-Recht: kein "permission denied for schema", sondern false.
    assert "nspname = 'sales_ivan'" in sql and "has_schema_privilege('sales_ivan', 'usage')" in sql
    assert sql.index("has_schema_privilege") < sql.index("to_regclass")


def test_fake_quelle_vertrag_hat_sichten():
    q = FakeQuelle(ohne_sichten=("sales_neu",))
    assert q.hat_sichten("sales") is True and q.hat_sichten("sales_neu") is False


def test_ungueltiges_schema_wirft_vor_aufruf():
    l = Laeufer()
    q = PsqlQuelle(C, laeufer=l)
    for schema in ("sales; drop", "sales_test", "Sales", "public"):
        with pytest.raises(ValueError):
            q.kontakte(schema)
        with pytest.raises(ValueError):
            q.hat_sichten(schema)
    assert l.aufrufe == []


def test_ungueltige_ids_und_zeiten_werfen_vor_aufruf():
    l = Laeufer()
    q = PsqlQuelle(C, laeufer=l)
    with pytest.raises(ValueError):
        q.newsletter_inhalte([NID, "x') or true --"])
    with pytest.raises(ValueError):
        q.medien(datetime(2026, 10, 19), datetime(2026, 10, 26))
    assert q.newsletter_inhalte([]) == []
    assert l.aufrufe == []


def test_newsletter_inhalte_nur_freigegeben():
    l = Laeufer(json.dumps([{"id": NID, "mandant": "vibemind", "titel": "T", "format": "bloecke",
                             "felder": {"betreff": "B"}, "bloecke": {"root": {}}, "seite": None}]))
    (i,) = PsqlQuelle(C, laeufer=l).newsletter_inhalte([NID, NID])
    sql = l.aufrufe[0][1]["input"]
    assert "status = 'freigegeben'" in sql and "freigegebene_fassung" in sql and sql.count(NID) == 1
    assert i.format == "bloecke" and i.bloecke == {"root": {}} and i.seite is None


def test_medien():
    von = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)
    l = Laeufer(json.dumps([{"art": "reel", "mandant": "vibemind", "titel": "R", "dauer_s": 61.5,
                             "fertig_am": "2026-10-20T10:00:00+00:00"},
                            {"art": "podcast", "mandant": "vibemind", "titel": "P", "dauer_s": None,
                             "fertig_am": "2026-10-21T10:00:00+00:00"}]))
    r, p = PsqlQuelle(C, laeufer=l).medien(von, von + timedelta(days=7))
    sql = l.aufrufe[0][1]["input"]
    assert "status = 'fertig'" in sql and "not archiviert" in sql
    assert r.art == "reel" and r.dauer_s == 61.5 and p.dauer_s is None


def test_psql_fehler_wird_dbfehler_ohne_stderr_text():
    l = Laeufer(rc=1, stderr='ERROR: invalid input "Erika Mustermann"')
    with pytest.raises(DbFehler) as info:
        PsqlQuelle(C, laeufer=l).kontakte("sales")
    assert "Erika" not in str(info.value) and "1" in str(info.value)


def test_kaputte_antwort_wird_dbfehler():
    for stdout in ("kein json", '{"a": 1}', json.dumps([{"id": LID}])):
        with pytest.raises(DbFehler):
            PsqlQuelle(C, laeufer=Laeufer(stdout)).kontakte("sales")


def test_zeitueberschreitung_wird_dbfehler():
    def laeufer(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    with pytest.raises(DbFehler):
        PsqlQuelle(C, laeufer=laeufer).marken()


def test_nur_select():
    l = Laeufer()
    q = PsqlQuelle(C, laeufer=l)
    von = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)
    q.marken(); q.hat_sichten("sales"); q.kontakte("sales"); q.ereignisse("sales"); q.versand("sales")
    q.newsletter_inhalte([NID]); q.medien(von, von + timedelta(days=7))
    assert len(l.aufrufe) == 7
    for _, kw in l.aufrufe:
        anfang = kw["input"].lstrip().lower()
        assert anfang.startswith("select") or anfang.startswith("with")
        assert ";" not in kw["input"].rstrip().rstrip(";")


def test_fake_quelle_und_fabrik():
    q = FakeQuelle(kontakte={"sales": [kontakt(name="A")]}, ohne_sichten=("sales_ivan",))
    assert [k.name for k in q.kontakte("sales")] == ["A"] and q.hat_sichten("sales_ivan") is False
    with pytest.raises(DbFehler):
        FakeQuelle(fehler=True).kontakte("sales")
