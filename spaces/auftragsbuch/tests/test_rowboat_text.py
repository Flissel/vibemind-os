from __future__ import annotations

from spaces.auftragsbuch.rowboat.text import kennung_in, vorschlag

ID = "0123456789abcdef"


def _punkt(**kw) -> dict:
    p = {"punkt_id": ID, "titel": "Angebot pruefen", "frist": "2026-12-01", "link": "/aufgaben/7"}
    p.update(kw)
    return p


def test_format_voll():
    assert vorschlag(_punkt()) == (
        f"[Gehirn] Angebot pruefen · fällig 2026-12-01 · [Öffnen](/aufgaben/7) · #{ID}"
    )


def test_ohne_frist_ohne_link():
    assert vorschlag(_punkt(frist=None, link="")) == f"[Gehirn] Angebot pruefen · #{ID}"


def test_nur_frist():
    assert vorschlag(_punkt(link="")) == f"[Gehirn] Angebot pruefen · fällig 2026-12-01 · #{ID}"


def test_nur_link():
    assert vorschlag(_punkt(frist=None, link="https://x.de/a")) == (
        f"[Gehirn] Angebot pruefen · [Öffnen](https://x.de/a) · #{ID}"
    )


def test_titel_mit_klammern_bereinigt():
    t = vorschlag(_punkt(titel="Mail an [Anna](x) @b\nzweite Zeile"))
    kopf = t.split(" · ")[0]
    for zeichen in "[]()@\n":
        assert zeichen not in kopf.removeprefix("[Gehirn]")
    assert "\n" not in t
    assert "Mail an Anna x b zweite Zeile" in t


def test_titel_hoechstens_160_zeichen():
    t = vorschlag(_punkt(titel="a" * 200, frist=None, link=""))
    assert t == f"[Gehirn] {'a' * 160} · #{ID}"


def test_link_klammern_prozent_kodiert():
    t = vorschlag(_punkt(link="/a(1)[2]@x"))
    assert "[Öffnen](/a%281%29%5B2%5D%40x)" in t


def test_freigabe_link_wird_weggelassen():
    for link in ("/freigaben#fg-1", "/freigaben%23fg-1", "/FREIGABEN%23FG-1", "/Freigaben%2523fg-1"):
        t = vorschlag(_punkt(link=link))
        assert "Öffnen" not in t, link
        assert t.endswith(f"· #{ID}")


def test_kennung_in_findet_am_ende():
    assert kennung_in(vorschlag(_punkt())) == ID


def test_kennung_uebersteht_kleinschreibung():
    assert kennung_in(f"[GEHIRN]   Foo  ·  #{ID.upper()}  ") == ID


def test_kennung_in_ohne_treffer():
    assert kennung_in("kein id") is None
    assert kennung_in(f"#{ID} danach") is None
    assert kennung_in("#abc") is None
    assert kennung_in(None) is None  # type: ignore[arg-type]


def test_steuerzeichen_im_titel_entfernt():
    t = vorschlag(_punkt(titel="a\x00b\x07c\x1bd\x7fe\tf", frist=None, link=""))
    assert t == f"[Gehirn] a b c d e f · #{ID}"
    assert not any(ord(z) < 32 or ord(z) == 127 for z in t)
