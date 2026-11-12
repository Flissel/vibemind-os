"""Kontext der Video-Werkbank: Markenwissen (nur Erlaubt-Liste) + Rowboat, ohne Kontakte und Notizen, wirft nie."""
import os

import pytest

from spaces.marketing.claw import video_kontext
from spaces.marketing.claw.video_kontext import holen

LANG = " Das ist ein ausreichend langer Satz, damit die Datei nicht als leer gilt und Inhalt hat."


def firma(tmp_path, monkeypatch):
    wurzel = tmp_path / "companys"
    ordner = wurzel / "Acme"
    (ordner / "kontakte").mkdir(parents=True)
    (ordner / "Agent-Notizen").mkdir()
    (ordner / "Video-Material").mkdir()
    (ordner / "Marke.md").write_text("# Marke\nWir bauen Schulungen für den Mittelstand." + LANG, encoding="utf-8")
    (ordner / "Video-Material" / "Preise.md").write_text("Schulung kostet 1.500 Euro." + LANG, encoding="utf-8")
    (ordner / "kontakte" / "Max Mustermann.md").write_text("Max Mustermann, Schulung, Telefon." + LANG, encoding="utf-8")
    (ordner / "Agent-Notizen" / "Notiz.md").write_text("Schulung Notiz vom Agenten." + LANG, encoding="utf-8")
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(wurzel))


def rowboat_ok():
    def quellen():
        return {"ok": True, "daten": [{"id": "s1", "name": "Schulungen"}, {"id": "s2", "name": "Kontakte"}]}

    def dokumente(qid, mit_inhalt):
        assert mit_inhalt is True
        if qid == "s2":
            return {"ok": True, "daten": [{"id": "d9", "name": "Erika", "content": "Schulung Erika." + LANG}]}
        return {"ok": True, "daten": [
            {"id": "d1", "name": "Angebot 2026", "typ": "text", "content": "Schulung Angebot 2026 mit 12 Terminen." + LANG},
            {"id": "d2", "name": "Angebot 2026", "content": "Schulung Doppelt." + LANG},
            {"id": "d3", "name": "Leer", "content": ""},
            {"id": "d4", "content": "Schulung ohne Namen." + LANG}]}
    return quellen, dokumente


def test_kontakte_und_notizen_nie_im_kontext(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    q, d = rowboat_ok()
    k = holen("Acme", "Schulung für den Mittelstand", quellen=q, dokumente=d)
    namen = {x.name for x in k.dokumente}
    assert namen and not any("kontakt" in n.casefold() or "notiz" in n.casefold() or "Erika" in n for n in namen)
    assert not any("Mustermann" in x.text or "Erika" in x.text for x in k.dokumente)


def test_rowboat_aus_ergibt_hinweis_statt_fehler(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    k = holen("Acme", "Schulung", quellen=lambda: {"ok": False, "fehler": "weg"}, dokumente=lambda *a: {})
    assert any("Rowboat" in h for h in k.hinweise) and "Marke.md" in {x.name for x in k.dokumente}

    def wirft():
        raise OSError("kaputt")
    k = holen("Acme", "Schulung", quellen=wirft, dokumente=lambda *a: {})
    assert any("Rowboat" in h for h in k.hinweise)


def test_dokumentnamen_tragen_die_quelle(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    q, d = rowboat_ok()
    k = holen("Acme", "Schulung Angebot 2026 Preise", quellen=q, dokumente=d)
    namen = [x.name for x in k.dokumente]
    assert {"Marke.md", "Schulungen/Angebot 2026"} <= set(namen)
    assert namen.count("Schulungen/Angebot 2026") == 1                 # Rowboat-Doppel zaehlt einmal
    assert {x.herkunft for x in k.dokumente} == {"marke", "rowboat"}
    assert not any(n.endswith("/Leer") for n in namen)


def test_ohne_firmenordner_und_ohne_rowboat_wirft_nie(tmp_path, monkeypatch):
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(tmp_path / "gibtsnicht"))
    k = holen("Nix", "Frage", quellen=lambda: {"ok": False}, dokumente=lambda *a: {})
    assert k.dokumente == [] and len(k.hinweise) == 2


def test_budget_gilt_hart_und_marke_bleibt(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    q, d = rowboat_ok()
    frei = holen("Acme", "Schulung Angebot Preise", budget=100_000, quellen=q, dokumente=d)
    assert len(frei.dokumente) >= 3
    marke = next(x for x in frei.dokumente if x.name == "Marke.md")
    rest = [x for x in frei.dokumente if x is not marke]
    budget = len(marke.text) + len(rest[0].text) + 5          # Platz fuer hoechstens ein weiteres Dokument
    k = holen("Acme", "Schulung Angebot Preise", budget=budget, quellen=q, dokumente=d)
    assert "Marke.md" in {x.name for x in k.dokumente}
    assert sum(len(x.text) for x in k.dokumente) <= budget and len(k.dokumente) < len(frei.dokumente)
    k = holen("Acme", "Schulung Angebot Preise", budget=1, quellen=q, dokumente=d)
    assert [x.name for x in k.dokumente] == ["Marke.md"] and any("Budget" in h for h in k.hinweise)


def test_uebersprungene_rowboat_eintraege_stehen_in_den_hinweisen(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    q, d = rowboat_ok()
    k = holen("Acme", "Schulung", quellen=q, dokumente=d)
    assert any("2 Dokumente ohne Text übersprungen" in h for h in k.hinweise)


# --- Erlaubt-Liste ----------------------------------------------------------------------------

def _schreiben(ordner, rel, text=None):
    text = (text or f"Schulung Mittelstand Thema Termine Bereiche Wochenbild {rel.replace('/', ' ')}.") + LANG
    ziel = ordner / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text, encoding="utf-8")


def _namen(k):
    return {x.name for x in k.dokumente}


def _frage(*rels):
    return FRAGE + " " + " ".join(r.replace("/", " ").replace(".", " ") for r in rels)


def _leer():
    return dict(quellen=lambda *a, **k: [], dokumente=lambda *a, **k: [])


FRAGE = "Schulung Mittelstand Thema Termine Bereiche Wochenbild Person Notizen Newsletter"


def test_erlaubte_dateien_kommen_und_andere_nie(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    ordner = tmp_path / "companys" / "Acme"
    for rel in ("Markenhandbuch.md", "Newsletter/2026-01-01 Titel.md", "Video-Material/thema.md",
                "Termine/2026-01-02 10-00 Erika Beispiel.md", "Bereiche/x.md", "Wochenbild/y.md",
                "Notizen.md", "Unbekannt/z.md", "Marke-Alt.md", "Preise/Liste.md"):
        _schreiben(ordner, rel)
    k = holen("Acme", _frage("Markenhandbuch.md", "Newsletter/2026-01-01 Titel.md", "Video-Material/thema.md",
                             "Termine/2026-01-02 10-00 Erika Beispiel.md", "Bereiche/x.md", "Wochenbild/y.md",
                             "Notizen.md", "Unbekannt/z.md", "Marke-Alt.md", "Preise/Liste.md"), **_leer())
    namen = _namen(k)
    assert {"Marke.md", "Markenhandbuch.md", "Newsletter/2026-01-01 Titel.md", "Video-Material/thema.md"} <= namen
    assert not any(n.split("/")[0] in ("Termine", "Bereiche", "Wochenbild", "Unbekannt", "Preise") for n in namen)
    assert not namen & {"Notizen.md", "Marke-Alt.md"}


def test_grossschreibung_der_erlaubten_namen_egal(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    ordner = tmp_path / "companys" / "Acme"
    _schreiben(ordner, "MARKENHANDBUCH.MD")
    _schreiben(ordner, "NEWSLETTER/a.md")
    _schreiben(ordner, "video-material/b.md")
    namen = _namen(holen("Acme", _frage("MARKENHANDBUCH.MD", "NEWSLETTER/a.md", "video-material/b.md"), **_leer()))
    assert {"markenhandbuch.md", "newsletter/a.md", "video-material/b.md"} <= {n.casefold() for n in namen}


def test_sperrwoerter_gelten_auch_in_erlaubten_ordnern(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    ordner = tmp_path / "companys" / "Acme"
    _schreiben(ordner, "Video-Material/Kontakte.md")
    _schreiben(ordner, "Video-Material/thema.md")
    k = holen("Acme", _frage("Video-Material/Kontakte.md", "Video-Material/thema.md"), **_leer())
    assert "Video-Material/thema.md" in _namen(k) and "Video-Material/Kontakte.md" not in _namen(k)


def test_hinweis_zaehlt_ausgelassene_ohne_namen(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    ordner = tmp_path / "companys" / "Acme"
    for i in range(3):
        _schreiben(ordner, f"Termine/2026-01-0{i} Erika{i}.md")
    _schreiben(ordner, "Bereiche/Vertrauliches.md")
    k = holen("Acme", FRAGE, **_leer())
    # Preise/ ist in diesem Fixture bereits Video-Material; ausserhalb sind 5 Dateien (4 + Agent-Notizen/Notiz.md des Fixtures)
    hinweis = next(h for h in k.hinweise if "außerhalb der erlaubten Ordner" in h)
    assert hinweis.startswith("5 Datei(en) außerhalb der erlaubten Ordner nicht gelesen")
    assert "Marke.md, Markenhandbuch.md, Newsletter/, Video-Material/" in hinweis
    alle = " ".join(k.hinweise)
    assert not any(w in alle for w in ("Erika", "Vertrauliches", "Termine/", "Bereiche/"))


def test_verknuepfung_nach_aussen_wird_ignoriert(tmp_path, monkeypatch):
    firma(tmp_path, monkeypatch)
    ordner = tmp_path / "companys" / "Acme"
    aussen = tmp_path / "aussen"
    aussen.mkdir()
    (aussen / "geheim.md").write_text("Schulung geheim Person." + LANG, encoding="utf-8")
    try:
        os.symlink(aussen / "geheim.md", ordner / "Video-Material" / "link.md")
        os.symlink(aussen, ordner / "Newsletter", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks brauchen auf diesem System erhoehte Rechte (Windows ohne Entwicklermodus)")
    k = holen("Acme", FRAGE + " geheim", **_leer())
    assert not any("geheim" in x.text or "link" in x.name or x.name.startswith("Newsletter") for x in k.dokumente)
