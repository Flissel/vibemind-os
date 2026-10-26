"""Tests fuer das Zerlegen von Notizen in Abschnitte (Bedeutungssuche, Teil C)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wissen_suche.zerlegen import MAX_ZEICHEN, zerlegen  # noqa: E402


def test_zerlegt_nach_ueberschriften():
    a = zerlegen("Notes/Plan.md", "# A\n" + "x " * 300 + "\n## B\n" + "y " * 300)
    assert [s.ueberschrift for s in a] == ["A", "A > B"]
    assert a[0].text.startswith("Plan / Notes / A\n\n")


def test_lange_abschnitte_an_absaetzen_geteilt():
    absatz = ("Satz. " * 80).strip()                      # ~480 Zeichen
    a = zerlegen("x.md", "# H\n\n" + "\n\n".join([absatz] * 12))
    assert len(a) >= 3 and all(len(s.text) <= MAX_ZEICHEN + 120 for s in a)


def test_kleine_abschnitte_verschmolzen():
    a = zerlegen("x.md", "# A\nkurz\n# B\nauch kurz\n# C\n" + "z " * 200)
    assert len(a) == 1


def test_raute_im_codeblock_ist_keine_ueberschrift():
    a = zerlegen("x.md", "# A\n```\n# kein Titel\n```\n" + "t " * 150)
    assert [s.ueberschrift for s in a] == ["A"]


def test_yaml_kopf_bleibt_im_ersten_abschnitt():
    a = zerlegen("companys/VibeMind/Kontakte/Altersvorsorge/Max Muster.md",
                 "---\nschlagwoerter: [bav, person]\n---\n# Max\n" + "m " * 150)
    assert "schlagwoerter" in a[0].text and "Max Muster / companys/VibeMind/Kontakte/Altersvorsorge" in a[0].text


def test_leere_datei_ergibt_nichts():
    assert zerlegen("x.md", "  \n") == []


def test_bom_und_crlf():
    assert zerlegen("Ü b.md", "\ufeff# Ä\r\n" + "ö " * 150)[0].ueberschrift == "Ä"


def test_einzelner_riesenabsatz_hart_an_satzgrenze_geteilt():
    a = zerlegen("x.md", "# H\n" + "Wort wort wort. " * 400)
    assert len(a) >= 3
    assert all(len(s.text) <= MAX_ZEICHEN + 120 for s in a)
    assert all(s.text.rstrip().endswith(".") for s in a[:-1])


def test_hoechstens_200_abschnitte(caplog):
    teile = "".join(f"# T{i}\n" + "w " * 150 + "\n" for i in range(250))
    a = zerlegen("x.md", teile)
    assert len(a) == 200
    assert [s.nr for s in a] == list(range(200))
    assert "200" in caplog.text
