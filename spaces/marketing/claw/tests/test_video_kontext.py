"""Kontext der Video-Werkbank: Markenwissen + Rowboat, ohne Kontakte und Notizen, wirft nie."""
from spaces.marketing.claw import video_kontext
from spaces.marketing.claw.video_kontext import holen

LANG = " Das ist ein ausreichend langer Satz, damit die Datei nicht als leer gilt und Inhalt hat."


def firma(tmp_path, monkeypatch):
    wurzel = tmp_path / "companys"
    ordner = wurzel / "Acme"
    (ordner / "kontakte").mkdir(parents=True)
    (ordner / "Agent-Notizen").mkdir()
    (ordner / "Preise").mkdir()
    (ordner / "Marke.md").write_text("# Marke\nWir bauen Schulungen für den Mittelstand." + LANG, encoding="utf-8")
    (ordner / "Preise" / "Liste.md").write_text("Schulung kostet 1.500 Euro." + LANG, encoding="utf-8")
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
