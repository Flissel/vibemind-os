"""Das OpenFang-Gateway reicht nur die letzte Nutzernachricht weiter: Nachrichten falten, JSON aus Text lesen."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


APP = Path(__file__).resolve().parents[1] / "app"


class _Client:
    """Gibt vorbereitete Antworten der Reihe nach zurueck und merkt sich die Anfragen."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.anfragen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.anfragen.append(kwargs)
        text = self.antworten.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _llm(monkeypatch, antworten):
    client = _Client(antworten)
    shared = ModuleType("vibemind_shared")
    shared.get_client_sync = lambda role: client
    shared.get_model = lambda role: "openfang:brain-forecaster"
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("app.utils.llm_client", None)
    spec = importlib.util.spec_from_file_location("app.utils.llm_client", APP / "utils" / "llm_client.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "app.utils.llm_client", module)
    spec.loader.exec_module(module)
    return module, module.LLMClient(), client


SYSTEM = {"role": "system", "content": "Leite Begriffsarten ab und gib sie als JSON aus."}
NUTZER = {"role": "user", "content": "Stadtfest: Buehne, Staende, Helfer."}


def test_system_und_nutzer_werden_zu_einer_nutzernachricht(monkeypatch):
    modul, llm, client = _llm(monkeypatch, ["ok"])
    llm.chat([SYSTEM, NUTZER])
    gesendet = client.anfragen[0]["messages"]
    assert len(gesendet) == 1 and gesendet[0]["role"] == "user"
    text = gesendet[0]["content"]
    assert text.index("### ANWEISUNG") < text.index("### EINGABE")
    assert SYSTEM["content"] in text and NUTZER["content"] in text
    assert "AUSGABEFORMAT" not in text


def test_einzelne_nutzernachricht_bleibt_unveraendert(monkeypatch):
    modul, llm, client = _llm(monkeypatch, ["ok"])
    llm.chat([NUTZER])
    assert client.anfragen[0]["messages"] == [NUTZER]


def test_nachrichten_ohne_text_werden_nicht_gefaltet(monkeypatch):
    modul, _, _ = _llm(monkeypatch, [])
    bild = [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
    assert modul.fold_messages(bild, json_only=True) is bild


def test_chat_json_verlangt_json_im_text_und_liest_es(monkeypatch):
    modul, llm, client = _llm(monkeypatch, ['{"entity_types": ["Buehne"]}'])
    assert llm.chat_json([SYSTEM, NUTZER]) == {"entity_types": ["Buehne"]}
    text = client.anfragen[0]["messages"][0]["content"]
    assert text.rstrip().endswith(modul._JSON_HINWEIS)
    assert len(client.anfragen) == 1


@pytest.mark.parametrize("antwort", [
    '```json\n{"a": 1}\n```',
    'Hier ist das Ergebnis:\n{"a": 1}\nIch hoffe, das hilft.',
    '## Bewertung\nText mit {unpaarer Klammer.\n\n{"a": 1}',
])
def test_json_wird_aus_fliesstext_und_zaeunen_gelesen(monkeypatch, antwort):
    _, llm, client = _llm(monkeypatch, [antwort])
    assert llm.chat_json([SYSTEM, NUTZER]) == {"a": 1}
    assert len(client.anfragen) == 1


def test_fliesstext_ohne_json_loest_genau_eine_nachfrage_aus(monkeypatch):
    modul, llm, client = _llm(monkeypatch, ["## Bewertung: Konzept reif.", '{"a": 2}'])
    assert llm.chat_json([SYSTEM, NUTZER]) == {"a": 2}
    assert len(client.anfragen) == 2
    zweite = client.anfragen[1]["messages"]
    assert len(zweite) == 1 and zweite[0]["role"] == "user"
    assert "### BISHERIGE ANTWORT\n## Bewertung: Konzept reif." in zweite[0]["content"]
    assert modul._NACHFRAGE in zweite[0]["content"]


def test_zweimal_kein_json_ist_ein_fehler_ohne_dritten_aufruf(monkeypatch):
    _, llm, client = _llm(monkeypatch, ["nur Text", "wieder nur Text " + "x" * 900])
    with pytest.raises(ValueError) as fehler:
        llm.chat_json([SYSTEM, NUTZER])
    assert len(client.anfragen) == 2
    assert str(fehler.value).startswith("Invalid JSON format from LLM: wieder nur Text")
    assert len(str(fehler.value)) < 400


def test_eine_liste_allein_zaehlt_nicht_als_objekt_im_fliesstext(monkeypatch):
    modul, _, _ = _llm(monkeypatch, [])
    with pytest.raises(ValueError):
        modul.extract_json("Vorschlag: [1, 2, 3] und mehr Text")
    assert modul.extract_json("[1, 2]") == [1, 2]
