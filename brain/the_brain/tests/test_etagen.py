"""Etagen-Ansicht der Entscheidungen (Plan 28.11.2026, Baustufe A).

Geprueft wird: die Zuordnung Stufe/Weg -> Etage und Ausgang, dass beide Felder an der
Entscheidung haengen und auch ausserhalb der Ansicht hinausgehen (nur Kennungen), dass die
Seite eingebunden ist und die Dateien da sind, und die Logik der Seite ueber node.
Was nur in der laufenden App pruefbar ist (Bild, Speicher des Grafikkontexts), steht im Commit.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))

from core import decision_graph as dgmod  # noqa: E402
from core import entscheidung_graph as eg  # noqa: E402
from web.routers import introspection as intro  # noqa: E402

STATISCH = WURZEL / "web" / "static" / "etagen"
SEITE = WURZEL / "web" / "templates" / "unified_brain_dashboard.html"


@pytest.mark.parametrize("stufe, weg, etage", [
    ("abkuerzung-regex", "plan-executor", "muster"),
    ("ideen-fest", "plan-executor", "muster"),
    ("lesesatz-fest", "plan-executor", "muster"),
    ("desktop-fest", "plan-executor", "muster"),
    ("ideen-lokal", "ideen-lokal", "muster"),
    ("lesen-fehlt", "lesen-fehlt", "muster"),
    ("planer", "plan-executor", "planung"),
    ("som-planner", "som-planner", "planung"),
    ("easy-chat", "easy-chat", "planung"),
    ("chat", "chat", "planung"),
    ("meta-reject", "meta-reject", "einordnung"),
    ("no-plan", "no-plan", "einordnung"),
    ("hoeflichkeit", "hoeflichkeit", "gespraech"),
    ("ideen-rueckfrage", "ideen-rueckfrage", "gespraech"),
    ("recherche-gestartet", "recherche-gestartet", "gespraech"),
    ("coding-fehler", "coding-fehler", "gespraech"),
    ("sales", "sales", "gespraech"),
    ("plan", "ganz-neuer-weg", "unbekannt"),
    ("", "", "unbekannt"),
    (None, None, "unbekannt"),
])
def test_etage_aus(stufe, weg, etage):
    assert eg.etage_aus(stufe, weg) == etage
    assert etage in eg.ETAGEN or etage == "unbekannt"


def test_die_stufe_aus_der_plan_id_schlaegt_den_weg():
    # Ein fester Weg fuehrt einen Einschritt-Plan aus: der Weg heisst dann "plan-executor".
    assert eg.etage_aus("ideen-fest", "plan-executor") == "muster"
    # Unbekannte Vorsilbe ("plan"), aber bekannter Weg: der Weg entscheidet.
    assert eg.etage_aus("plan", "plan-executor") == "planung"


def test_jede_stufe_aus_einer_plan_id_hat_eine_etage():
    for _vorsilbe, stufe in eg._STUFE_NACH_VORSILBE:
        assert eg.etage_aus(stufe, "plan-executor") != "unbekannt", stufe


@pytest.mark.parametrize("weg, ok, ausgang", [
    ("plan-executor", True, "erledigt"),
    ("plan-executor", False, "fehler"),
    ("ideen-rueckfrage", True, "rueckfrage"),
    ("sales", None, "rueckfrage"),
    ("meta-reject", True, "absage"),
    ("lesen-fehlt", False, "absage"),
    ("recherche-fehler", None, "fehler"),
    ("recherche-gestartet", True, "angenommen"),
    ("hoeflichkeit", True, "antwort"),
    ("easy-chat", True, "antwort"),
    ("ohne-weg", None, "antwort"),
])
def test_ausgang_aus(weg, ok, ausgang):
    assert eg.ausgang_aus(weg, ok) == ausgang


def test_entscheidung_traegt_etage_und_ausgang():
    nodes, edges = [], []
    dgmod.DecisionGraph._entscheidung_teil([
        {"eid": "ent_aa11bb22", "trace_id": "aa11bb22", "weg": "plan-executor", "stufe": "ideen-fest",
         "ok": True, "bereich": "ideas", "text": "Leg eine Notiz zu Thema A an", "ts": 1.0},
        {"eid": "ent_cc33dd44", "trace_id": "cc33dd44", "weg": "meta-reject", "stufe": "meta-reject",
         "ok": True, "ts": 2.0},
    ], nodes, edges)
    ents = {n["data"]["id"]: n["data"] for n in nodes if n["data"]["type"] == "entscheidung"}
    assert (ents["ent_aa11bb22"]["etage"], ents["ent_aa11bb22"]["ausgang"]) == ("muster", "erledigt")
    assert (ents["ent_cc33dd44"]["etage"], ents["ent_cc33dd44"]["ausgang"]) == ("einordnung", "absage")


def test_etage_und_ausgang_gehen_auch_ohne_text_hinaus():
    nodes, edges = [], []
    dgmod.DecisionGraph._entscheidung_teil([
        {"eid": "ent_aa11bb22", "trace_id": "aa11bb22", "weg": "plan-executor", "stufe": "planer",
         "ok": False, "bereich": "ideas", "text": "Plane drei Schritte fuer das Beispielprojekt", "ts": 1.0},
    ], nodes, edges)
    sicher = intro._ohne_text({"connected": True, "nodes": nodes, "edges": edges})
    ent = next(n["data"] for n in sicher["nodes"] if n["data"]["type"] == "entscheidung")
    assert (ent["etage"], ent["ausgang"]) == ("planung", "fehler")
    assert "Beispielprojekt" not in str(sicher)


def test_dateien_der_ansicht_sind_da():
    for name in ("etagen-logik.js", "etagen-ansicht.js", "three.min.js", "OrbitControls.js"):
        assert (STATISCH / name).is_file(), name
    assert "SPDX-License-Identifier: MIT" in (STATISCH / "three.min.js").read_text(encoding="utf-8")[:300]


def test_seite_bindet_die_ansicht_ein():
    html = SEITE.read_text(encoding="utf-8")
    assert '/static/etagen/etagen-logik.js' in html
    assert '/static/etagen/etagen-ansicht.js' in html
    assert 'id="wsEtagen"' in html and 'id="wsEtagenKnopf"' in html
    assert "window.EtagenAnsicht.daten(data)" in html
    # three.js kommt nicht mit der Seite, sondern erst beim Oeffnen der Ansicht.
    assert "three.min.js" not in html


def test_ansicht_schreibt_nur_maskierten_text_in_die_seite():
    """Der Satztext kommt nur an einer Stelle in die Seite, und dort maskiert."""
    import re

    js = (STATISCH / "etagen-ansicht.js").read_text(encoding="utf-8")
    stellen = re.findall(r"[^\n]*\b\w+\.text\b(?!Content)[^\n]*", js)
    assert len(stellen) == 1, stellen
    assert "b.text ? '„' + esc(b.text) + '“'" in stellen[0]


def test_logik_der_seite_unter_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node nicht installiert")
    lauf = subprocess.run(  # noqa: S603
        [node, "--test", str(Path(__file__).with_name("etagen_logik.test.js"))],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
        text=True, encoding="utf-8", errors="replace",
    )
    assert lauf.returncode == 0, lauf.stdout[-3000:]
