"""Musik-Claw-Bot: Erlaubnis, Meldungen mit Datei, Zerlegen, Zustand, Token-Schutz.
Kein Netz: Telegram ist ein aufzeichnendes Ersatzobjekt, der Musik-Dienst eine Ersatzfunktion."""
import json
import logging
import urllib.error
from pathlib import Path

import pytest

from djset.claw import telegram as bot

CHAT = 4242
TOKEN = "123456:GEHEIMER-BOT-TOKEN-abcdef"
MB = 1024 * 1024


# ---------------------------------------------------------------- Ersatzobjekte


class FakeApi:
    """Zeichnet in `ablauf` mit, in welcher Reihenfolge Telegram und Dienst angesprochen wurden."""

    def __init__(self, ablauf: list):
        self.ablauf = ablauf
        self.texte: list[str] = []
        self.dateien: list[tuple] = []
        # False oder eine Ausnahmeklasse (TelegramAbgelehnt / TelegramVorlaeufig)
        self.datei_fehler = False
        self.text_fehler = False
        self.versuche_text = 0
        self.versuche_datei = 0

    def senden(self, text, chat_id=None):
        self.versuche_text += 1
        if self.text_fehler:
            raise self.text_fehler("Telegram sendMessage: weg")
        self.texte.append(text)
        self.ablauf.append(("senden", text))

    def datei_senden(self, art, name, daten, text, chat_id=None):
        self.versuche_datei += 1
        if self.datei_fehler:
            raise self.datei_fehler("Telegram sendAudio: zu gross")
        self.dateien.append((art, name, daten, text))
        self.ablauf.append(("datei", art, name))

    def tippt(self, chat_id=None):
        pass


class FakeDienst:
    """Der Musik-Dienst: Auftraege nach ID, Dateien nach (ID, Name), Aufrufliste."""

    def __init__(self, ablauf: list):
        self.ablauf = ablauf
        self.auftraege: dict[str, dict] = {}
        self.dateien: dict[tuple[str, str], bytes] = {}
        self.gesamt: dict[tuple[str, str], int] = {}
        self.gemeldet: set[str] = set()
        self.aufrufe: list[tuple] = []
        self.tot = False
        self.posts = 0
        self.gemeldet_antwort: dict | None = None

    def add(self, art="video", zustand="fertig", **felder):
        nr = len(self.auftraege) + 1
        auftrag_id = f"20261124-1015{nr:02d}-{art}-ab{nr:02d}"
        eintrag = {"ok": True, "id": auftrag_id, "art": art, "zustand": zustand, "grund": "",
                   "kanal": "telegram", "eingabe": {"wunsch": "ruhig und warm"}, "dateien": [],
                   "bericht": "", "befunde": [], "laenge_s": 30.0, "titel": "Abendlicht"}
        eintrag.update(felder)
        self.auftraege[auftrag_id] = eintrag
        return auftrag_id

    @staticmethod
    def _json(daten, status=200):
        return status, json.dumps(daten).encode("utf-8"), None

    def __call__(self, methode, pfad, koerper=None):
        self.aufrufe.append((methode, pfad, koerper))
        if self.tot:
            raise OSError("Dienst nicht erreichbar")
        if methode == "GET" and pfad == "/auftraege?kanal=telegram&offen=1":
            offen = [{"id": i, "art": a["art"], "zustand": a["zustand"], "grund": a["grund"]}
                     for i, a in self.auftraege.items() if i not in self.gemeldet]
            return self._json({"ok": True, "auftraege": offen})
        teile = pfad.split("/")
        if methode == "GET" and len(teile) == 3:
            a = self.auftraege.get(teile[2])
            return self._json(a) if a else self._json({"ok": False}, 404)
        if methode == "GET" and len(teile) == 5 and teile[3] == "datei":
            schluessel = (teile[2], teile[4])
            if schluessel not in self.dateien:
                return self._json({"ok": False}, 404)
            daten = self.dateien[schluessel]
            self.ablauf.append(("holen", teile[4]))
            return 200, daten, self.gesamt.get(schluessel, len(daten))
        if methode == "POST" and len(teile) == 4 and teile[3] == "gemeldet":
            assert koerper == {}, "POST braucht einen JSON-Koerper ({}), sonst 415"
            self.posts += 1
            if self.gemeldet_antwort is not None:       # Dienst antwortet 200, aber {"ok": false}
                return self._json(self.gemeldet_antwort)
            self.gemeldet.add(teile[2])
            self.ablauf.append(("gemeldet", teile[2]))
            return self._json({"ok": True})
        raise AssertionError(f"unerwartet: {methode} {pfad}")


@pytest.fixture
def ablauf():
    return []


@pytest.fixture
def api(ablauf):
    return FakeApi(ablauf)


@pytest.fixture
def dienst(ablauf):
    return FakeDienst(ablauf)


def _update(chat=CHAT, von=CHAT, typ="private", text="hallo", schluessel="message"):
    return {"update_id": 7, schluessel: {"chat": {"id": chat, "type": typ}, "from": {"id": von}, "text": text}}


# ---------------------------------------------------------------- Erlaubnis


def test_fremder_chat_wird_ignoriert(api):
    assert bot.erlaubt(_update(), CHAT)
    assert not bot.erlaubt(_update(chat=99, von=99), CHAT)           # fremder Chat
    assert not bot.erlaubt(_update(von=99), CHAT)                    # richtiger Chat, fremder Absender
    assert not bot.erlaubt(_update(typ="group"), CHAT)               # Gruppe
    assert not bot.erlaubt(_update(schluessel="edited_message"), CHAT)
    assert not bot.erlaubt(_update(), 0)                             # ohne Chat-ID nie
    gefragt = []
    verlauf = bot.Verlauf(Path("unbenutzt.json"))
    bot.verarbeiten(_update(chat=99, von=99), api, verlauf, CHAT, "sys",
                    holen=lambda *a: gefragt.append(a) or "x")
    assert gefragt == [] and api.texte == []                         # keine Antwort, nicht einmal eine Absage


# ---------------------------------------------------------------- Meldungen


def test_melden_schickt_mp3_und_markiert(api, dienst, ablauf):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"MP3-BYTES"
    assert bot.melden(api, dienst, CHAT) == 1
    assert len(api.dateien) == 1
    art, name, daten, text = api.dateien[0]
    assert (art, name, daten) == ("audio", "videomusik.mp3", b"MP3-BYTES")
    assert "Abendlicht" in text and i in text
    assert ablauf == [("holen", "videomusik.mp3"), ("datei", "audio", "videomusik.mp3"), ("gemeldet", i)]


def test_melden_zweimal_schickt_einmal(api, dienst):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    assert bot.melden(api, dienst, CHAT) == 1
    assert bot.melden(api, dienst, CHAT) == 0
    assert len(api.dateien) == 1 and api.texte == []


def test_set_schickt_set_mp3(api, dienst):
    i = dienst.add("set", eingabe={"wunsch": "Sonnenuntergang", "length": 20}, titel=None, laenge_s=None)
    dienst.dateien[(i, "set.mp3")] = b"SET"
    assert bot.melden(api, dienst, CHAT) == 1
    assert api.dateien[0][:3] == ("audio", "set.mp3", b"SET")
    assert "Sonnenuntergang" in api.dateien[0][3]


def test_zu_gross_nur_bericht(api, dienst):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"nur-der-anfang"
    dienst.gesamt[(i, "videomusik.mp3")] = 60 * MB
    assert bot.melden(api, dienst, CHAT) == 1
    assert api.dateien == []
    assert len(api.texte) == 1
    assert f"Datei zu groß für Telegram (60 MB), liegt im Auftrag {i}" in api.texte[0]
    assert i in dienst.gemeldet


def test_grenze_50_mb_genau_geht_noch(api, dienst):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x" * 10
    dienst.gesamt[(i, "videomusik.mp3")] = 50 * MB
    assert bot.melden(api, dienst, CHAT) == 1 and len(api.dateien) == 1


def test_laura_render_schickt_video(api, dienst):
    i = dienst.add("laura_render", eingabe={"timeline_id": "t1"}, titel=None, laenge_s=None)
    dienst.dateien[(i, "laura-export.mp4")] = b"MP4"
    assert bot.melden(api, dienst, CHAT) == 1
    assert api.dateien[0][:3] == ("video", "laura-export.mp4", b"MP4")
    assert "Laura" in api.dateien[0][3]
    assert i in dienst.gemeldet


def test_gescheitert_nur_bericht_mit_grund(api, dienst):
    i = dienst.add("video", zustand="gescheitert", grund="zu wenig Speicher", titel=None, laenge_s=None)
    assert bot.melden(api, dienst, CHAT) == 1
    assert api.dateien == []
    assert "gescheitert" in api.texte[0] and "zu wenig Speicher" in api.texte[0]
    assert not any("datei" in p for _, p, _k in dienst.aufrufe if _ == "GET")  # keine Datei angefragt
    assert i in dienst.gemeldet


def test_aufnehmen_nur_bericht(api, dienst):
    i = dienst.add("aufnehmen", titel=None, laenge_s=None, eingabe={"titel": "Neuer Song"})
    assert bot.melden(api, dienst, CHAT) == 1
    assert api.dateien == [] and len(api.texte) == 1 and "Neuer Song" in api.texte[0]
    assert i in dienst.gemeldet


def test_befunde_stehen_im_bericht(api, dienst):
    i = dienst.add("video", zustand="fertig_mit_befund", befunde=["Pegel knapp", "Ende abrupt"])
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    bot.melden(api, dienst, CHAT)
    text = api.dateien[0][3]
    assert "Pegel knapp" in text and "Ende abrupt" in text and len(text) <= 1024


def test_datei_fehlt_bericht_geht_trotzdem_und_wird_markiert(api, dienst):
    i = dienst.add("video")          # keine Datei im Dienst: 404
    assert bot.melden(api, dienst, CHAT) == 1
    assert api.dateien == [] and len(api.texte) == 1
    assert "Datei" in api.texte[0] and "Abendlicht" in api.texte[0]
    assert i in dienst.gemeldet
    assert bot.melden(api, dienst, CHAT) == 0      # keine Endlosschleife


def test_telegram_lehnt_datei_ab_dann_text_und_markiert(api, dienst):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    api.datei_fehler = bot.TelegramAbgelehnt          # 4xx: die Datei geht nicht, der Text schon
    assert bot.melden(api, dienst, CHAT) == 1
    assert len(api.texte) == 1 and i in dienst.gemeldet


class Uhr:
    def __init__(self):
        self.jetzt = 1_000_000.0

    def __call__(self):
        return self.jetzt


def _stand():
    # Vor der Korrektur gab es keinen Meldestand: dann reicht ein dict (das Verhalten ist das Thema).
    return getattr(bot, "Meldestand", dict)()


def test_gemeldet_ok_false_schickt_nicht_erneut(api, dienst, caplog):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    dienst.gemeldet_antwort = {"ok": False, "fehler": "OSError"}      # HTTP 200, aber nicht gespeichert
    stand = _stand()
    with caplog.at_level(logging.WARNING, logger="djset.claw.telegram"):
        gesendet = [bot.melden(api, dienst, CHAT, stand) for _ in range(3)]
    assert gesendet == [1, 0, 0]
    assert len(api.dateien) == 1 and api.texte == []        # Telegram bekam genau einen Bericht
    assert dienst.posts == 3                                # gemeldet wurde dreimal versucht
    assert i not in dienst.gemeldet
    # sobald der Dienst wieder speichert, wird markiert, ohne nochmal zu senden
    dienst.gemeldet_antwort = None
    assert bot.melden(api, dienst, CHAT, stand) == 0
    assert i in dienst.gemeldet and len(api.dateien) == 1


def test_gemeldet_gibt_nach_10_fehlschlaegen_auf_und_sendet_nie_neu(api, dienst, caplog):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    dienst.gemeldet_antwort = {"ok": False}
    stand = _stand()
    with caplog.at_level(logging.WARNING, logger="djset.claw.telegram"):
        for _ in range(15):
            bot.melden(api, dienst, CHAT, stand)
    assert len(api.dateien) == 1 and dienst.posts == 10
    fehler = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(fehler) == 1 and i in fehler[0].getMessage()


def test_kurzer_telegram_ausfall_verliert_keine_meldung(api, dienst):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    api.text_fehler = api.datei_fehler = bot.TelegramVorlaeufig
    uhr, stand = Uhr(), _stand()
    for _ in range(10):                                   # 10 Runden in 270 s: Telegram nicht erreichbar
        assert bot.melden(api, dienst, CHAT, stand, uhr=uhr) == 0
        assert i not in dienst.gemeldet and dienst.posts == 0
        uhr.jetzt += 30
    api.text_fehler = api.datei_fehler = False            # Telegram ist wieder da
    assert bot.melden(api, dienst, CHAT, stand, uhr=uhr) == 1
    assert len(api.dateien) == 1 and i in dienst.gemeldet


def test_langer_telegram_ausfall_gibt_nach_6_stunden_auf(api, dienst, caplog):
    i = dienst.add("video")
    api.text_fehler = api.datei_fehler = bot.TelegramVorlaeufig
    uhr, stand = Uhr(), _stand()
    assert bot.melden(api, dienst, CHAT, stand, uhr=uhr) == 0
    uhr.jetzt += 6 * 3600 - 1
    assert bot.melden(api, dienst, CHAT, stand, uhr=uhr) == 0 and i not in dienst.gemeldet
    uhr.jetzt += 2
    with caplog.at_level(logging.WARNING, logger="djset.claw.telegram"):
        bot.melden(api, dienst, CHAT, stand, uhr=uhr)
    assert i in dienst.gemeldet
    assert any(i in r.getMessage() for r in caplog.records)


def test_telegram_4xx_gibt_nach_3_versuchen_auf(api, dienst, caplog):
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"
    api.text_fehler = api.datei_fehler = bot.TelegramAbgelehnt
    stand = _stand()
    for _ in range(2):
        assert bot.melden(api, dienst, CHAT, stand) == 0
        assert i not in dienst.gemeldet
    with caplog.at_level(logging.WARNING, logger="djset.claw.telegram"):
        assert bot.melden(api, dienst, CHAT, stand) == 0
    assert i in dienst.gemeldet
    assert any(i in r.getMessage() for r in caplog.records)


def test_telegram_fehlerklassen(monkeypatch):
    def http(code):
        def urlopen(*_a, **_k):
            raise urllib.error.HTTPError("u", code, "x", {}, None)
        return urlopen

    tg = bot.TelegramApi(TOKEN, CHAT)
    for code, klasse in ((400, bot.TelegramAbgelehnt), (403, bot.TelegramAbgelehnt),
                         (429, bot.TelegramVorlaeufig), (502, bot.TelegramVorlaeufig)):
        monkeypatch.setattr(bot.urllib.request, "urlopen", http(code))
        with pytest.raises(klasse):
            tg.senden("x")
        with pytest.raises(klasse):
            tg.datei_senden("audio", "a.mp3", b"x", "t")

    def aus(*_a, **_k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(bot.urllib.request, "urlopen", aus)
    with pytest.raises(bot.TelegramVorlaeufig):
        tg.senden("x")
    monkeypatch.setattr(bot, "_post_json", lambda *a: {"ok": False, "description": "chat not found"})
    with pytest.raises(bot.TelegramAbgelehnt):
        tg.senden("x")


def test_telegram_401_ist_vorlaeufig(monkeypatch):
    """T8: ein kaputter Token (401) verwirft keine Berichte; es gilt das 6-h-Fenster. 403 bleibt endgueltig."""
    def urlopen(*_a, **_k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    tg = bot.TelegramApi(TOKEN, CHAT)
    monkeypatch.setattr(bot.urllib.request, "urlopen", urlopen)
    with pytest.raises(bot.TelegramVorlaeufig):
        tg.senden("x")
    with pytest.raises(bot.TelegramVorlaeufig):
        tg.datei_senden("audio", "a.mp3", b"x", "t")
    monkeypatch.setattr(bot, "_post_json", lambda *a: {"ok": False, "error_code": 401,
                                                        "description": "Unauthorized"})
    with pytest.raises(bot.TelegramVorlaeufig):
        tg.senden("x")


def test_llm_zeitlimit_mindestens_shim_timeout():
    """M7: die Shim laeuft mit --timeout 1800; der Bot darf nicht frueher aufgeben."""
    assert bot.LLM_ZEITLIMIT_S >= 1800


def test_dienst_nicht_erreichbar_gibt_null(api, dienst):
    dienst.add("video")
    dienst.tot = True
    assert bot.melden(api, dienst, CHAT) == 0
    assert api.texte == [] and api.dateien == []


def test_mehrere_auftraege_in_einer_runde(api, dienst):
    a = dienst.add("video")
    b = dienst.add("set", titel=None, laenge_s=None)
    dienst.dateien[(a, "videomusik.mp3")] = b"a"
    dienst.dateien[(b, "set.mp3")] = b"b"
    assert bot.melden(api, dienst, CHAT) == 2
    assert [d[1] for d in api.dateien] == ["videomusik.mp3", "set.mp3"]


def test_bericht_ohne_pfade_und_kurz():
    text = bot.bericht_text({"id": "20261124-101501-video-ab01", "art": "video", "zustand": "fertig_mit_befund",
                             "titel": "T" * 500, "laenge_s": 75.0, "grund": "g" * 900,
                             "befunde": [f"befund {n} " + "b" * 300 for n in range(9)], "eingabe": {}})
    assert len(text) <= 1000
    assert "1:15" in text


# ---------------------------------------------------------------- Zerlegen, Hilfe, Verlauf


def test_zerlegen_haelt_grenze():
    teile = bot.zerlegen("a" * 9000)
    assert all(len(t) <= bot.TELEGRAM_MAX for t in teile) and "".join(teile) == "a" * 9000
    assert bot.zerlegen("   ") == ["(leere Antwort)"]


def test_verarbeiten_antwortet_und_merkt_sich_den_verlauf(api, tmp_path):
    verlauf = bot.Verlauf(tmp_path / "verlauf.json")
    gesehen = []

    def holen(text, vorher, system):
        gesehen.append((text, list(vorher), system))
        return "Antwort 1"

    bot.verarbeiten(_update(text="Was liegt im Bestand?"), api, verlauf, CHAT, "SYSTEM", holen=holen)
    assert api.texte == ["Antwort 1"]
    assert gesehen == [("Was liegt im Bestand?", [], "SYSTEM")]
    bot.verarbeiten(_update(text="und jetzt?"), api, verlauf, CHAT, "SYSTEM", holen=holen)
    assert gesehen[1][1] == [{"role": "user", "content": "Was liegt im Bestand?"},
                             {"role": "assistant", "content": "Antwort 1"}]
    # der Verlauf ueberlebt einen Neustart
    assert bot.Verlauf(tmp_path / "verlauf.json").nachrichten(CHAT)[-1]["content"] == "Antwort 1"


def test_neu_leert_den_verlauf_und_hilfe_fragt_nicht_nach(api, tmp_path):
    verlauf = bot.Verlauf(tmp_path / "v.json")
    verlauf.anhaengen(CHAT, "a", "b")

    def nie(*_):
        raise AssertionError("darf nicht fragen")

    bot.verarbeiten(_update(text="/neu"), api, verlauf, CHAT, "s", holen=nie)
    assert verlauf.nachrichten(CHAT) == []
    bot.verarbeiten(_update(text="/hilfe@MusikBot"), api, verlauf, CHAT, "s", holen=nie)
    assert "Bestand" in api.texte[-1]


def test_llm_fehler_kommt_als_text_und_nicht_in_den_verlauf(api, tmp_path):
    verlauf = bot.Verlauf(tmp_path / "v.json")

    def kaputt(*_):
        raise bot.AntwortFehler("Der Assistent ist gerade nicht erreichbar")

    bot.verarbeiten(_update(text="hi"), api, verlauf, CHAT, "s", holen=kaputt)
    assert api.texte == ["Der Assistent ist gerade nicht erreichbar"]
    assert verlauf.nachrichten(CHAT) == []


def test_nichttext_bekommt_hinweis(api, tmp_path):
    update = {"update_id": 1, "message": {"chat": {"id": CHAT, "type": "private"}, "from": {"id": CHAT},
                                          "photo": [{"file_id": "x"}]}}
    bot.verarbeiten(update, api, bot.Verlauf(tmp_path / "v.json"), CHAT, "s", holen=lambda *a: "x")
    assert len(api.texte) == 1 and "Text" in api.texte[0]


# ---------------------------------------------------------------- Zustand


def test_versatz_atomar_und_einstellbar(tmp_path):
    assert bot.versatz_lesen(tmp_path / "zustand") == 0
    bot.versatz_schreiben(tmp_path / "zustand", 77)
    assert bot.versatz_lesen(tmp_path / "zustand") == 77
    assert [p.name for p in (tmp_path / "zustand").iterdir()] == ["versatz.txt"]   # kein tmp-Rest
    (tmp_path / "zustand" / "versatz.txt").write_text("kaputt")
    assert bot.versatz_lesen(tmp_path / "zustand") == 0


def test_zustand_vorgabe_unter_home(monkeypatch):
    monkeypatch.delenv("MUSIK_TELEGRAM_ZUSTAND", raising=False)
    assert bot.zustand_ordner() == Path.home() / ".vibemind" / "musik-telegram"
    monkeypatch.setenv("MUSIK_TELEGRAM_ZUSTAND", "C:/irgendwo")
    assert bot.zustand_ordner() == Path("C:/irgendwo")


# ---------------------------------------------------------------- LLM


def test_antwort_holen_nutzt_shim_url_modell_und_system(monkeypatch):
    gesehen = {}

    def post(url, koerper, zeitlimit):
        gesehen.update(url=url, koerper=koerper, zeitlimit=zeitlimit)
        return {"choices": [{"message": {"content": "Hallo Felix"}}]}

    monkeypatch.delenv("MUSIK_TELEGRAM_LLM_URL", raising=False)
    monkeypatch.setattr(bot, "_post_json", post)
    antwort = bot.antwort_holen("Was gibt es?", [{"role": "user", "content": "a"}], "SYS")
    assert antwort == "Hallo Felix"
    assert gesehen["url"] == "http://127.0.0.1:8152/v1/chat/completions"
    assert gesehen["zeitlimit"] == 1800          # M7: wie --timeout 1800 der Shim
    assert gesehen["koerper"]["model"] == "claude-code-sonnet"
    assert gesehen["koerper"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert gesehen["koerper"]["messages"][-1] == {"role": "user", "content": "Was gibt es?"}


def test_antwort_holen_fehler_werden_verstaendlich(monkeypatch):
    def tot(*_):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(bot, "_post_json", tot)
    with pytest.raises(bot.AntwortFehler) as e:
        bot.antwort_holen("x", [], "s")
    assert "nicht erreichbar" in str(e.value)

    def budget(*_):
        raise urllib.error.HTTPError("u", 429, "x", {}, None)

    monkeypatch.setattr(bot, "_post_json", budget)
    with pytest.raises(bot.AntwortFehler) as e:
        bot.antwort_holen("x", [], "s")
    assert "Tagesgrenze" in str(e.value)


# ---------------------------------------------------------------- Token


def test_bot_token_in_keinem_log(monkeypatch, caplog):
    assert bot.ohne_token(f"fehler bei {TOKEN}", TOKEN) == "fehler bei ***"
    assert bot.ohne_token("ohne", "") == "ohne"

    # 1) Fehler aus der Telegram-Schicht tragen den Token nie
    def kaputt(*_a, **_k):
        raise urllib.error.URLError(f"Verbindung zu https://api.telegram.org/bot{TOKEN}/sendMessage")

    monkeypatch.setattr(bot.urllib.request, "urlopen", kaputt)
    tg = bot.TelegramApi(TOKEN, CHAT)
    for aufruf in (lambda: tg.senden("x"), lambda: tg.updates(0),
                   lambda: tg.datei_senden("audio", "a.mp3", b"x", "t")):
        with pytest.raises(RuntimeError) as e:
            aufruf()
        assert TOKEN not in str(e.value) and TOKEN not in repr(e.value)
        assert e.value.__cause__ is None and e.value.__suppress_context__

    # 2) Der Log-Filter ersetzt ihn auch dort, wo ihn ein Fremder hineinschreibt
    logger = logging.getLogger("djset.claw.test_token")
    logger.addFilter(bot.TokenFilter(TOKEN))
    with caplog.at_level(logging.INFO, logger="djset.claw.test_token"):
        logger.warning("kaputt: %s", f"https://api.telegram.org/bot{TOKEN}/getUpdates")
        logger.warning(f"direkt {TOKEN}")
    assert TOKEN not in caplog.text and "***" in caplog.text

    # 3) Auch die Meldungsrunde loggt ihn nicht, wenn die Telegram-Schicht ihn im Fehler hatte
    ablauf: list = []
    api = FakeApi(ablauf)
    dienst = FakeDienst(ablauf)
    i = dienst.add("video")
    dienst.dateien[(i, "videomusik.mp3")] = b"x"

    def senden_mit_token(*_a, **_k):
        raise RuntimeError(f"Telegram sendAudio: {TOKEN}")

    api.datei_senden = senden_mit_token
    api.text_fehler = bot.TelegramVorlaeufig
    logging.getLogger("djset.claw.telegram").addFilter(bot.TokenFilter(TOKEN))
    with caplog.at_level(logging.DEBUG, logger="djset.claw.telegram"):
        bot.melden(api, dienst, CHAT, token=TOKEN)
    assert TOKEN not in caplog.text


def test_multipart_enthaelt_datei_und_felder(monkeypatch):
    gesehen = {}

    class Antwort:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "result": {}}'

    def urlopen(req, timeout=None):
        gesehen.update(url=req.full_url, kopf=dict(req.header_items()), daten=req.data, zeit=timeout)
        return Antwort()

    monkeypatch.setattr(bot.urllib.request, "urlopen", urlopen)
    bot.TelegramApi(TOKEN, CHAT).datei_senden("video", "laura-export.mp4", b"\x00MP4\xff", "Fertig")
    assert gesehen["url"].endswith("/sendVideo")
    kopf = {k.lower(): v for k, v in gesehen["kopf"].items()}
    assert kopf["content-type"].startswith("multipart/form-data; boundary=")
    grenze = kopf["content-type"].split("boundary=")[1].encode()
    daten = gesehen["daten"]
    assert daten.count(b"--" + grenze) == 4 and daten.endswith(b"--" + grenze + b"--\r\n")
    assert b'name="video"; filename="laura-export.mp4"' in daten and b"\x00MP4\xff" in daten
    assert f'name="chat_id"\r\n\r\n{CHAT}'.encode() in daten
    assert b'name="caption"\r\n\r\nFertig' in daten
    with pytest.raises(ValueError):
        bot.TelegramApi(TOKEN, CHAT).datei_senden("sticker", "a", b"", "")


# ---------------------------------------------------------------- Dienst-Anfrage (echte Funktion, ohne Netz)


def test_dienst_anfrage_sendet_json_ohne_origin(monkeypatch):
    gesehen = {}

    class Antwort:
        status = 200
        headers = {"Content-Length": "17"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b'{"ok": true}'

    def urlopen(req, timeout=None):
        gesehen.update(url=req.full_url, methode=req.get_method(), daten=req.data,
                       kopf={k.lower(): v for k, v in req.header_items()})
        return Antwort()

    monkeypatch.delenv("MUSIK_STUDIO_URL", raising=False)
    monkeypatch.setattr(bot.urllib.request, "urlopen", urlopen)
    status, body, gesamt = bot.dienst_anfrage("POST", "/auftraege/x/gemeldet", {})
    assert (status, body, gesamt) == (200, b'{"ok": true}', 17)
    assert gesehen["url"] == "http://127.0.0.1:8148/auftraege/x/gemeldet"
    assert gesehen["methode"] == "POST" and gesehen["daten"] == b"{}"
    assert gesehen["kopf"]["content-type"] == "application/json"
    assert "origin" not in gesehen["kopf"]


def test_dienst_anfrage_http_fehler_ist_ein_status(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setattr(bot.urllib.request, "urlopen", urlopen)
    assert bot.dienst_anfrage("GET", "/auftraege/x/datei/set.mp3")[0] == 404


# ---------------------------------------------------------------- Start, Prompt, MCP-Datei


def test_main_ohne_token_oder_chat_endet_mit_klarer_meldung(monkeypatch):
    monkeypatch.setattr(bot, "umgebung_laden", lambda: None)
    monkeypatch.delenv("MUSIK_TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "5")
    with pytest.raises(SystemExit) as e:
        bot.main()
    assert "MUSIK_TELEGRAM_TOKEN" in str(e.value)
    monkeypatch.setenv("MUSIK_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    with pytest.raises(SystemExit) as e:
        bot.main()
    assert "TELEGRAM_CHAT_ID" in str(e.value) and TOKEN not in str(e.value)


def test_umgebung_laedt_nur_fehlende_aus_dem_aeusseren_env(tmp_path, monkeypatch):
    from djset.dienst import server

    env = tmp_path / ".env"
    env.write_text(f'MUSIK_TELEGRAM_TOKEN="{TOKEN}"\nTELEGRAM_CHAT_ID=1\n', encoding="utf-8")
    monkeypatch.setattr(server, "_aeusseres_env", lambda: env)
    monkeypatch.delenv("MUSIK_TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "9")
    bot.umgebung_laden()
    import os
    assert os.environ["MUSIK_TELEGRAM_TOKEN"] == TOKEN and os.environ["TELEGRAM_CHAT_ID"] == "9"
    monkeypatch.delenv("MUSIK_TELEGRAM_TOKEN", raising=False)


def test_systemprompt_liegt_am_paketort_und_nicht_am_arbeitsordner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pfad = bot.prompt_pfad()
    assert pfad.name == "AGENTS.md" and pfad.parent.name == "claw" and pfad.parent.parent.name == "musik"
    text = bot.systemprompt()
    for werkzeug in ("bestand_zeigen", "videomusik_bestellen", "laura_videos_suchen", "in_laura_legen",
                     "laura_rendern", "auftrag_zeigen"):
        assert werkzeug in text
    assert "Pfad" in text


def test_systemprompt_musik_unter_video_in_einem_zug():
    """I1(c): bestellen und sofort mit musik_auftrag rendern; nie getrennt einlegen."""
    text = bot.systemprompt()
    assert "laura_rendern(timeline_id, musik_auftrag=" in text
    assert "Nie `in_laura_legen` getrennt vor `laura_rendern`" in text
    assert "gain_percent" in text


def test_musik_mcp_json_wie_vereinbart():
    daten = json.loads((bot.prompt_pfad().parent / "musik-mcp.json").read_text(encoding="utf-8"))
    musik = daten["mcpServers"]["musik"]
    assert musik == {"type": "http", "url": "http://127.0.0.1:8148/mcp",
                     "headers": {"X-Musik-Kanal": "telegram"}, "timeout": 1200000}
    erwartet = {"bestand_zeigen", "song_aufnehmen", "set_bestellen", "videomusik_bestellen",
                "auftrag_zeigen", "auftrag_abbrechen", "in_laura_legen", "laura_videos_suchen",
                "laura_rendern"}
    assert set(daten["allowedTools"]) == {f"mcp__musik__{n}" for n in erwartet}


def test_pyproject_hat_den_startbefehl():
    pyproject = bot.prompt_pfad().parents[1] / "studio" / "pyproject.toml"
    assert 'musik-telegram = "djset.claw.telegram:main"' in pyproject.read_text(encoding="utf-8")
