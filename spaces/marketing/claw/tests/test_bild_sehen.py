from spaces.marketing.claw import bild_prompt, bild_sehen as bs


def test_beschreiben_schickt_bild_mit_num_ctx(monkeypatch):
    gesendet = []
    monkeypatch.setattr(bild_prompt, "_ollama", lambda pfad, d, zeitlimit=120: gesendet.append((d, zeitlimit)) or
                        {"response": "A night skyline with teal light.\n"})
    assert bs.beschreiben(b"\xff\xd8\xffJPEG") == "A night skyline with teal light."
    d, zeitlimit = gesendet[0]
    assert d["model"] == "qwen2.5vl:3b" and d["images"] and d["keep_alive"] == 0
    assert d["options"]["num_ctx"] == 4096 and zeitlimit == 180


def test_beschreiben_fehler_ist_leer(monkeypatch):
    monkeypatch.setattr(bild_prompt, "_ollama", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("zu lang")))
    assert bs.beschreiben(b"x") == ""


def test_ohne_schrift_entfernt_zitierte_woerter():
    t = 'Skyscrapers at night. Visible text includes "MAYFAIR" and “Majestic Tower” on buildings.'
    ohne = bs.ohne_schrift(t)
    assert "MAYFAIR" not in ohne and "Majestic" not in ohne and "Skyscrapers at night." in ohne


import pytest


@pytest.mark.parametrize("satz", [
    "A sign says CAFE.", "The word MAYFAIR in neon letters.", "Neon sign with the text MAYFAIR.",
    "A signboard saying OPEN.", "A banner displays SALE.", "Letters spelling HELLO.",
    "Bright lights over the PLAZA.", "A shop sign reads open.", "Lettering in gold.",
])
def test_ohne_schrift_entfernt_schriftformulierungen(satz):
    assert bs.ohne_schrift(f"A city at night. {satz} Warm light.") == "A city at night. Warm light."


def test_ohne_schrift_haelt_harmloses():
    assert bs.ohne_schrift("Signs of autumn fill the park.") == "Signs of autumn fill the park."
    assert bs.ohne_schrift("A man's hat and the dog's leash.") == "A man's hat and the dog's leash."
    assert bs.ohne_schrift("The man's hat. A dog's leash.") == "The man's hat. A dog's leash."


def test_frage_verbietet_abschreiben():
    assert "never write out" in bs.FRAGE and bs.ohne_schrift is bild_prompt.ohne_schrift
