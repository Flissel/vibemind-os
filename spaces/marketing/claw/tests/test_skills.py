"""skills: Lader fuer SKILL.md (OpenClaw-Format), Befehlsnamen, Aufloesung von /befehl."""
import pytest

from spaces.marketing.claw import skills
from spaces.marketing.claw.skills import (
    RESERVIERT, SkillFehler, aufloesen, befehle, befehlsname, frontmatter, laden,
)

SCHRITTE = [
    "Name und Webseite", "Pflichtangaben für Newsletter", "Wer und was", "Aussehen",
    "Stimme", "Belege", "Zusammenfassung und Bestätigung",
]


def _skill(ordner, verzeichnis, name, beschreibung="Eine Probe", extra=""):
    pfad = ordner / verzeichnis
    pfad.mkdir(parents=True)
    (pfad / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {beschreibung}\n{extra}---\n\nAnweisung hier\nzweite Zeile\n",
        encoding="utf-8",
    )


def _mit_chat(extra_chat="marke"):
    return f'metadata: {{"vibemind":{{"chats":["{extra_chat}"],"schritte":3}}}}\n'


def test_befehlsname_openclaw_regel():
    assert befehlsname("marke-neu") == "marke_neu"
    assert befehlsname("  Foo.Bar!! ") == "foo_bar"
    assert len(befehlsname("a" * 40)) == 32


def test_reserviert_enthaelt_beide_listen():
    assert {"new", "reset", "help", "skill", "status", "stop", "model", "config", "bash"} <= RESERVIERT
    assert {"neu", "start", "hilfe"} <= RESERVIERT
    assert RESERVIERT == skills.RESERVIERT_OPENCLAW | skills.RESERVIERT_TELEGRAM


@pytest.mark.parametrize("name", ["new", "Neu"])
def test_reservierter_name_ist_fehler(tmp_path, name):
    _skill(tmp_path, "x", name)
    with pytest.raises(SkillFehler):
        laden(tmp_path)


def test_doppelter_befehl_ist_fehler(tmp_path):
    _skill(tmp_path, "a", "marke-neu")
    _skill(tmp_path, "b", "marke_neu")
    with pytest.raises(SkillFehler):
        laden(tmp_path)


def test_fehlende_pflichtfelder_und_metadata(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text("---\nname: a\n---\ntext\n", encoding="utf-8")
    with pytest.raises(SkillFehler):
        laden(tmp_path)
    (tmp_path / "a" / "SKILL.md").write_text("kein kopf\n", encoding="utf-8")
    with pytest.raises(SkillFehler):
        laden(tmp_path)
    (tmp_path / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: d\nmetadata: {kaputt\n---\ntext\n", encoding="utf-8")
    with pytest.raises(SkillFehler):
        laden(tmp_path)


def test_frontmatter_und_anweisung(tmp_path):
    kopf, rest = frontmatter("---\nname: x\nfoo: bar\ndescription: d: mit Doppelpunkt\n---\nErste\nZweite\n")
    assert kopf["foo"] == "bar"
    assert kopf["description"] == "d: mit Doppelpunkt"
    assert rest.startswith("Erste")
    _skill(tmp_path, "x", "probe", extra="foo: bar\n")
    (s,) = laden(tmp_path)
    assert s.anweisung.startswith("Anweisung hier")
    assert s.befehl == "probe"
    assert s.chats == () and s.schritte is None


def test_ohne_chats_kein_befehl(tmp_path):
    _skill(tmp_path, "x", "probe")
    assert len(laden(tmp_path)) == 1
    assert befehle("marke", tmp_path) == []


def test_user_invocable_false_ist_kein_befehl(tmp_path):
    _skill(tmp_path, "x", "probe", extra=_mit_chat() + "user-invocable: false\n")
    assert befehle("marke", tmp_path) == []


def test_aufloesen_varianten(tmp_path):
    _skill(tmp_path, "mn", "marke-neu", extra=_mit_chat())
    _skill(tmp_path, "an", "anders", extra=_mit_chat("newsletter"))
    (skill,) = befehle("marke", tmp_path)
    assert aufloesen("/marke_neu Probe GmbH https://x.de", "marke", tmp_path) == (skill, "Probe GmbH https://x.de")
    for text in ("/skill marke-neu Probe", "/skill marke_neu Probe", "/skill marke neu Probe"):
        assert aufloesen(text, "marke", tmp_path) == (skill, "Probe")
    assert aufloesen("/MARKE_NEU", "marke", tmp_path) == (skill, "")
    for text in ("/new", "/foo", "hallo", "/anders x"):
        assert aufloesen(text, "marke", tmp_path) is None
    assert aufloesen("/anders x", "newsletter", tmp_path) is not None


def test_echte_skills_gueltig():
    alle = laden()
    for s in alle:
        if s.chats:
            assert len(s.beschreibung) <= 100, s.name
    assert "newsletter-bild" in {s.name for s in alle}
    for chat in ("marke", "newsletter", "plan", "reels"):
        assert "newsletter-bild" not in {s.name for s in befehle(chat)}


def test_marke_neu_inhalt():
    assert [s.befehl for s in befehle("marke")] == ["marke_neu"]
    (s,) = befehle("marke")
    assert s.schritte == 7
    for titel in SCHRITTE:
        assert titel in s.anweisung, titel
    for text in ("{abmeldelink}", "Finanz- oder Versicherungsvermittler", "genau eine", "pflichtteil",
                 "uebersprungen", "vermittler", "Marke anlegen"):
        assert text in s.anweisung, text
