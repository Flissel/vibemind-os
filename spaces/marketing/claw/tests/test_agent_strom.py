import json

from spaces.marketing.claw.agent_strom import StromLeser

BS = chr(92)

A1 = {"werkzeug": "block_loeschen", "id": "kopf", "schritt": "Titel entfernen"}
A2 = {"werkzeug": "block_aendern", "id": "text1", "props": {"text": "a } b ] c"}, "schritt": "Text setzen"}
A3 = {"werkzeug": "block_aendern", "id": "text1", "props": {"text": 'er sagte "hi" ' + BS + BS + ' ende'}, "schritt": "Zitat"}
GANZ = json.dumps({"antwort": "Fertig.", "aenderungen": [A1, A2, A3]}, ensure_ascii=False)


def alles(text, n=None):
    s = StromLeser()
    aus = []
    if n is None:
        aus += s.futter(text)
    else:
        for i in range(0, len(text), n):
            aus += s.futter(text[i:i + n])
    return s, aus


def test_ganzer_text_in_einem_stueck():
    _, aus = alles(GANZ)
    assert aus == [A1, A2, A3]


def test_zeichenweise_und_andere_chunkgroessen_gleich():
    for n in (1, 2, 3, 5, 7, 13):
        _, aus = alles(GANZ, n)
        assert aus == [A1, A2, A3], n


def test_aenderung_erst_beim_schliessenden_klammer():
    s = StromLeser()
    text = '{"aenderungen": [' + json.dumps(A2)
    assert s.futter(text[:-1]) == []
    assert s.futter(text[-1:]) == [A2]


def test_escape_ueber_chunk_grenze():
    s = StromLeser()
    kopf = '{"aenderungen": [{"werkzeug": "block_loeschen", "id": "x", "schritt": "er sagte ' + BS
    assert s.futter(kopf) == []            # Chunk endet direkt nach dem Backslash
    assert s.futter('"hi' + BS) == []      # \" ist ein Escape, dann Backslash am Chunk-Ende
    assert s.futter(BS + ' }') == []       # \ ist ein Paar, dann } im String
    assert s.futter('"}') == [{"werkzeug": "block_loeschen", "id": "x", "schritt": 'er sagte "hi' + BS + ' }'}]

def test_vortext_und_codezaun():
    text = "Ich baue das neu.\n\n```json\n" + GANZ + "\n```\n"
    for n in (None, 1, 4):
        _, aus = alles(text, n)
        assert aus == [A1, A2, A3]


def test_vortext_mit_geschweifter_klammer_ohne_aenderungen():
    _, aus = alles('Hinweis {kein json} ' + GANZ)
    assert aus == [A1, A2, A3]


def test_zwei_aenderungen_in_einem_stueck():
    s = StromLeser()
    assert s.futter('{"aenderungen": [' + json.dumps(A1) + "," + json.dumps(A2) + ",") == [A1, A2]
    assert s.futter(json.dumps(A3) + "]}") == [A3]


def test_antwort_nach_aenderungen():
    text = json.dumps({"aenderungen": [A1, A2], "antwort": "Kurz { ] }"})
    for n in (None, 1):
        s, aus = alles(text, n)
        assert aus == [A1, A2]
        assert s.text == text


def test_antwort_vor_aenderungen_mit_aenderungen_im_string():
    text = json.dumps({"antwort": 'siehe "aenderungen": [ {', "aenderungen": [A1]})
    _, aus = alles(text, 1)
    assert aus == [A1]


def test_leeres_array():
    s, aus = alles('{"antwort": "Frage?", "aenderungen": []}', 1)
    assert aus == [] and s.fehler == []


def test_unvollstaendiges_element_gibt_nichts_aus():
    s, aus = alles('{"aenderungen": [' + json.dumps(A1) + ', {"werkzeug": "block_loes')
    assert aus == [A1]
    s, aus = alles('{"aenderungen": [{"werkzeug": "block_loes')
    assert aus == []


def test_verschachtelte_objekte_und_arrays_in_aenderung():
    a = {"werkzeug": "ebene_reihenfolge", "flaeche": "f", "ids": ["a", "b"], "x": {"y": [1, {"z": 2}]}}
    _, aus = alles(json.dumps({"aenderungen": [a, A1]}), 1)
    assert aus == [a, A1]


def test_nicht_parsebares_element_wird_uebersprungen_und_gemerkt():
    s, aus = alles('{"aenderungen": [{"werkzeug": nope}, ' + json.dumps(A1) + "]}", 3)
    assert aus == [A1]
    assert len(s.fehler) == 1 and isinstance(s.fehler[0], str)


def test_nur_aenderungen_des_obersten_objekts():
    text = '{"antwort": "x", "meta": {"aenderungen": [{"a": 1}]}, "aenderungen": [' + json.dumps(A1) + "]}"
    _, aus = alles(text, 1)
    assert aus == [A1]


def test_nichts_mehr_nach_array_ende():
    s = StromLeser()
    s.futter('{"aenderungen": [' + json.dumps(A1) + '], "antwort": "x {"')
    assert s.futter('"a": 1}') == []


def test_text_sammelt_alles():
    s, _ = alles("Vor " + GANZ, 5)
    assert s.text == "Vor " + GANZ
