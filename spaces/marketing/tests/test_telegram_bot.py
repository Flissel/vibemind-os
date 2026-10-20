"""Eigener Telegram-Bot fuer Marketing (20.10.2026) - ohne Netz: Telegram und Shim sind Attrappen."""
import json
import urllib.error

import pytest

from spaces.marketing.workers import telegram_bot as tb

CHAT = 1092040975
TOKEN = "123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"


def nachricht(text="Hallo", chat=CHAT, absender=CHAT, art="private", uid=1, extra=None):
    m = {"message_id": uid, "chat": {"id": chat, "type": art}, "from": {"id": absender, "is_bot": False}}
    if text is not None:
        m["text"] = text
    m.update(extra or {})
    return {"update_id": uid, "message": m}


class Telegram:
    """Attrappe der Bot-API: merkt sich gesendete Nachrichten."""

    def __init__(self):
        self.gesendet, self.tippen = [], 0

    def senden(self, chat, text):
        self.gesendet.append((chat, text))

    def tippt(self, chat):
        self.tippen += 1


def test_nur_der_betreiber_im_privatchat_kommt_durch():
    assert tb.erlaubt(nachricht(), CHAT)
    assert not tb.erlaubt(nachricht(absender=42), CHAT)                 # fremder Absender
    assert not tb.erlaubt(nachricht(chat=42, absender=42), CHAT)        # fremder Chat
    assert not tb.erlaubt(nachricht(chat=-100123, art="group"), CHAT)   # Gruppe, auch mit dem Betreiber darin
    assert not tb.erlaubt({"update_id": 5, "edited_message": nachricht()["message"]}, CHAT)
    assert not tb.erlaubt(nachricht(), 0)                               # ohne konfigurierte Chat-ID: niemand


def test_lange_antworten_werden_an_absatzgrenzen_geteilt():
    text = ("Absatz " + "x" * 2500 + "\n\n") * 3
    teile = tb.zerlegen(text)
    assert all(len(t) <= tb.TELEGRAM_MAX for t in teile)
    assert "".join(teile).replace("\n", "") == text.replace("\n", "")
    assert tb.zerlegen("") == ["(leere Antwort)"]


def test_verlauf_haelt_hoechstens_die_letzten_wechsel(tmp_path):
    v = tb.Verlauf(tmp_path / "v.json")
    for i in range(tb.VERLAUF_WECHSEL + 5):
        v.anhaengen(CHAT, f"frage {i}", f"antwort {i}")
    nachrichten = tb.Verlauf(tmp_path / "v.json").nachrichten(CHAT)   # neu geladen: liegt auf der Platte
    assert len(nachrichten) == 2 * tb.VERLAUF_WECHSEL
    assert nachrichten[0] == {"role": "user", "content": f"frage {5}"}
    v.leeren(CHAT)
    assert v.nachrichten(CHAT) == []


def test_anfrage_an_den_shim(tmp_path, monkeypatch):
    gesehen = {}

    def frage(url, koerper, zeitlimit):
        gesehen.update(url=url, koerper=koerper, zeitlimit=zeitlimit)
        return {"choices": [{"message": {"content": "Es gibt 3 Entwuerfe."}}]}

    monkeypatch.setattr(tb, "_post_json", frage)
    v = tb.Verlauf(tmp_path / "v.json")
    v.anhaengen(CHAT, "Hallo", "Hallo!")
    antwort = tb.antwort_holen("Welche Entwuerfe gibt es?", v.nachrichten(CHAT), system="SYS")
    assert antwort == ("Es gibt 3 Entwuerfe.", True)
    assert gesehen["url"].endswith("/chat/completions")
    k = gesehen["koerper"]
    assert k["model"] == "claude-code-sonnet" and k.get("stream") is not True
    assert [m["role"] for m in k["messages"]] == ["system", "user", "assistant", "user"]
    assert k["messages"][0]["content"] == "SYS" and k["messages"][-1]["content"] == "Welche Entwuerfe gibt es?"


def _http_fehler(code, rumpf):
    import io
    return urllib.error.HTTPError("http://x", code, "x", {}, io.BytesIO(json.dumps(rumpf).encode()))


def test_budget_und_fehler_werden_verstaendlich(monkeypatch):
    monkeypatch.setattr(tb, "_post_json", lambda *a: (_ for _ in ()).throw(
        _http_fehler(429, {"error": {"message": "budget: abgelehnt (tagesgrenze) fuer marketing-telegram"}})))
    text, ok = tb.antwort_holen("x", [], system="S")
    assert "Tagesgrenze" in text and ok is False
    monkeypatch.setattr(tb, "_post_json", lambda *a: (_ for _ in ()).throw(
        _http_fehler(502, {"error": {"message": "Claude Code CLI exited 1: ..."}})))
    assert tb.antwort_holen("x", [], system="S")[0].startswith("Das hat nicht geklappt")
    monkeypatch.setattr(tb, "_post_json", lambda *a: (_ for _ in ()).throw(urllib.error.URLError("refused")))
    assert "nicht erreichbar" in tb.antwort_holen("x", [], system="S")[0]


def test_eine_nachricht_verarbeiten(tmp_path, monkeypatch):
    t = Telegram()
    v = tb.Verlauf(tmp_path / "v.json")
    monkeypatch.setattr(tb, "antwort_holen", lambda text, verlauf, system: (f"Antwort auf {text}", True))
    tb.verarbeiten(nachricht("Hallo"), t, v, CHAT, system="S")
    assert t.gesendet == [(CHAT, "Antwort auf Hallo")] and t.tippen >= 1
    assert v.nachrichten(CHAT)[-1] == {"role": "assistant", "content": "Antwort auf Hallo"}


def test_fehlertext_kommt_nicht_in_den_verlauf(tmp_path, monkeypatch):
    t = Telegram()
    v = tb.Verlauf(tmp_path / "v.json")
    monkeypatch.setattr(tb, "antwort_holen", lambda *a: ("Das hat nicht geklappt (HTTP 502).", False))
    tb.verarbeiten(nachricht("Hallo"), t, v, CHAT, system="S")
    assert t.gesendet[-1][1].startswith("Das hat nicht geklappt") and v.nachrichten(CHAT) == []


def test_fremde_nachricht_wird_still_verworfen(tmp_path, monkeypatch):
    t = Telegram()
    monkeypatch.setattr(tb, "antwort_holen", lambda *a: pytest.fail("darf nicht fragen"))
    tb.verarbeiten(nachricht("Hallo", chat=42, absender=42), t, tb.Verlauf(tmp_path / "v.json"), CHAT, system="S")
    assert t.gesendet == []                       # keine Antwort an Fremde, nicht einmal eine Absage


def test_befehle_und_nicht_text(tmp_path, monkeypatch):
    t = Telegram()
    v = tb.Verlauf(tmp_path / "v.json")
    v.anhaengen(CHAT, "a", "b")
    monkeypatch.setattr(tb, "antwort_holen", lambda *a: pytest.fail("Befehle fragen kein Modell"))
    tb.verarbeiten(nachricht("/neu"), t, v, CHAT, system="S")
    assert v.nachrichten(CHAT) == [] and "neu" in t.gesendet[-1][1].lower()
    tb.verarbeiten(nachricht("/hilfe"), t, v, CHAT, system="S")
    assert "/neu" in t.gesendet[-1][1]
    tb.verarbeiten(nachricht(None, extra={"sticker": {"file_id": "x"}}), t, v, CHAT, system="S")
    assert "Text" in t.gesendet[-1][1]


def test_token_steht_in_keiner_fehlermeldung():
    assert TOKEN not in tb.ohne_token(f"https://api.telegram.org/bot{TOKEN}/getUpdates fehlgeschlagen", TOKEN)
    assert "***" in tb.ohne_token(f"bot{TOKEN}", TOKEN)


def test_systemprompt_liegt_bei_und_verbietet_den_versand():
    text = tb.systemprompt()
    assert "versend" in text.lower() and "newsletter_chat" in text


# ─── Fotos und Sprachnachrichten (20.10.2026) ───────────────────────────────

class TelegramMitDateien(Telegram):
    def __init__(self, dateien):
        super().__init__()
        self.dateien = dateien

    def datei(self, file_id):
        return self.dateien[file_id]


def test_foto_geht_als_bild_mit_beschriftung_an_claude(tmp_path, monkeypatch):
    gesehen = {}

    def holen(inhalt, verlauf, system):
        gesehen["inhalt"] = inhalt
        return "Schoenes Bild.", True

    monkeypatch.setattr(tb, "antwort_holen", holen)
    t = TelegramMitDateien({"gross": b"\xff\xd8\xffJPEGDATEN"})
    v = tb.Verlauf(tmp_path / "v.json")
    foto = {"photo": [{"file_id": "klein", "file_size": 10}, {"file_id": "gross", "file_size": 999}],
            "caption": "Passt das zum Newsletter?"}
    tb.verarbeiten(nachricht(None, extra=foto), t, v, CHAT, system="S")
    teile = gesehen["inhalt"]
    assert teile[0] == {"type": "text", "text": "Passt das zum Newsletter?"}
    assert teile[1]["type"] == "image_url" and teile[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert v.nachrichten(CHAT)[0] == {"role": "user", "content": "[Foto] Passt das zum Newsletter?"}   # kein Bild im Verlauf
    assert t.gesendet[-1][1] == "Schoenes Bild."


def test_zu_grosses_foto_wird_abgelehnt(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "antwort_holen", lambda *a: pytest.fail("darf nicht fragen"))
    t = TelegramMitDateien({})
    foto = {"photo": [{"file_id": "x", "file_size": tb.DATEI_MAX + 1}]}
    tb.verarbeiten(nachricht(None, extra=foto), t, tb.Verlauf(tmp_path / "v.json"), CHAT, system="S")
    assert "zu gross" in t.gesendet[-1][1]


def test_sprachnachricht_wird_am_pc_umgeschrieben_und_wie_text_behandelt(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "umschreiben", lambda daten: "Welche Entwuerfe gibt es?")
    monkeypatch.setattr(tb, "antwort_holen", lambda inhalt, verlauf, system: (f"Antwort auf {inhalt}", True))
    t = TelegramMitDateien({"ton": b"OggS..."})
    v = tb.Verlauf(tmp_path / "v.json")
    tb.verarbeiten(nachricht(None, extra={"voice": {"file_id": "ton", "file_size": 2000, "duration": 3}}), t, v, CHAT,
                   system="S")
    assert t.gesendet[0][1].startswith("🎤 Verstanden: Welche Entwuerfe gibt es?")
    assert t.gesendet[-1][1] == "Antwort auf Welche Entwuerfe gibt es?"
    assert v.nachrichten(CHAT)[0] == {"role": "user", "content": "Welche Entwuerfe gibt es?"}


def test_leere_umschrift_fragt_nicht(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "umschreiben", lambda daten: "  ")
    monkeypatch.setattr(tb, "antwort_holen", lambda *a: pytest.fail("darf nicht fragen"))
    t = TelegramMitDateien({"ton": b"OggS"})
    tb.verarbeiten(nachricht(None, extra={"voice": {"file_id": "ton", "file_size": 10}}), t,
                   tb.Verlauf(tmp_path / "v.json"), CHAT, system="S")
    assert "nicht verstanden" in t.gesendet[-1][1]


def test_andere_dateien_bekommen_eine_klare_absage(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "antwort_holen", lambda *a: pytest.fail("darf nicht fragen"))
    t = TelegramMitDateien({})
    tb.verarbeiten(nachricht(None, extra={"document": {"file_id": "d", "file_name": "a.pdf"}}), t,
                   tb.Verlauf(tmp_path / "v.json"), CHAT, system="S")
    assert "Text, Fotos und Sprachnachrichten" in t.gesendet[-1][1]
