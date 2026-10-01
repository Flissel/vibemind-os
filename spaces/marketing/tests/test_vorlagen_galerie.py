from spaces.marketing.scripts import vorlagen_galerie


def test_galerie_schreibt_alle(tmp_path):
    assert vorlagen_galerie.main([str(tmp_path), "--akzent", "#c2410c", "--flaeche", "#2f4858"]) == 0
    namen = {p.name for p in tmp_path.iterdir()}
    for n in ("studio", "zeitung", "firmenblatt", "minimal", "klassik", "bildkopf", "tech"):
        assert f"{n}-600.html" in namen and f"{n}-380.html" in namen
    assert "index.html" in namen and any(n.startswith("tech-signal-") for n in namen)
