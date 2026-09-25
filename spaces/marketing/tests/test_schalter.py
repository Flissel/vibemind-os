"""Gegen-Schalter Marketing -> Sales (sales-claw Spec 2026-09-25-marketing-
schalter-design.md §3.3). Die Pruefung laeuft in node gegen die echte
Datei, die der Browser laedt — kein Nachbau in Python."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "mockup" / "schalter.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node fehlt")


def _node(ausdruck: str):
    skript = (f"const s = require({json.dumps(str(JS))});"
              f"process.stdout.write(JSON.stringify({ausdruck}));")
    r = subprocess.run([NODE, "-e", skript], capture_output=True,
                       text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("gut", [
    "https://vibemind-offload-1.tail6c7d61.ts.net",
    "https://vibemind-offload-1.tail6c7d61.ts.net:8445/kontakte",
])
def test_tailnet_https_wird_angenommen(gut):
    assert _node(f"s.rueckwegPruefen({json.dumps(gut)})") == gut


@pytest.mark.parametrize("schlecht", [
    "", "http://x.tail6c7d61.ts.net", "javascript:alert(1)",
    "https://x.ts.net.boese.de", "https://boese.de/?x.ts.net",
    "https://boese.de#.ts.net", "https://ts.net", "https://boese.de",
    "//x.ts.net", "nicht mal eine adresse",
])
def test_alles_andere_wird_abgewiesen(schlecht):
    assert _node(f"s.rueckwegPruefen({json.dumps(schlecht)})") == ""


def test_parameter_schlaegt_gemerkten_wert_und_wird_gemerkt():
    ausdruck = """(() => { const m = {};
      const sp = {getItem: k => m[k] ?? null, setItem: (k, v) => { m[k] = v; }};
      sp.setItem('sales_rueckweg', 'https://alt.tail6c7d61.ts.net');
      const r = s.rueckwegBestimmen('?zurueck=' +
        encodeURIComponent('https://neu.tail6c7d61.ts.net'), sp);
      return [r, m.sales_rueckweg]; })()"""
    assert _node(ausdruck) == ["https://neu.tail6c7d61.ts.net"] * 2


def test_ohne_parameter_gilt_der_gemerkte():
    ausdruck = """s.rueckwegBestimmen('', {getItem: () =>
      'https://alt.tail6c7d61.ts.net', setItem: () => {}})"""
    assert _node(ausdruck) == "https://alt.tail6c7d61.ts.net"


def test_gemerkter_boeser_wert_zaehlt_nicht():
    ausdruck = """s.rueckwegBestimmen('', {getItem: () =>
      'https://boese.de', setItem: () => {}})"""
    assert _node(ausdruck) == ""


def test_werfender_speicher_bricht_nichts():
    ausdruck = """s.rueckwegBestimmen('?zurueck=' +
      encodeURIComponent('https://neu.tail6c7d61.ts.net'),
      {getItem: () => { throw new Error('privat'); },
       setItem: () => { throw new Error('privat'); }})"""
    assert _node(ausdruck) == "https://neu.tail6c7d61.ts.net"


def test_seite_bindet_schalter_ein():
    html = (JS.parent / "index.html").read_text(encoding="utf-8")
    assert '<script src="schalter.js"></script>' in html
    assert 'id="schalter-sales"' in html
