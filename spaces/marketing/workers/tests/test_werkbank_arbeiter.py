"""Werkbank-Arbeiter: Fake-API zeichnet die Aufrufe auf; Modell, Stimme, Render und Kontext sind Fakes."""
import http.client
import io
import json
import re
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from spaces.marketing.claw.video_kontext import Dokument, Kontext
from spaces.marketing.workers import werkbank_arbeiter as wa
from spaces.marketing.workers.bild_worker import ApiFehler
from spaces.marketing.workers.werkbank_arbeiter import BudgetErschoepft, ein_durchlauf

AID_A = "0123abcd-1111-4222-8333-444455556666"
AID_B = "feedbeef-1111-4222-8333-444455556666"
SATZ = ("Das ist der erste Satz über das Thema der Szene und er ist lang genug. "
        "Hier folgt der zweite Satz mit einer kurzen Erklärung für die Zuschauer. "
        "Zum Schluss kommt ein dritter Satz, der alles zusammenfasst und überleitet. "
        "Danach geht es im nächsten Abschnitt ganz in Ruhe weiter.")
BILD = {"schritte": [{"vorlage": "titel", "ab_satz": 0, "zeile": "Titel", "unterzeile": "Unter"},
                     {"vorlage": "stichpunkte", "ab_satz": 1, "ueberschrift": "Punkte", "punkte": ["eins", "zwei"]}]}
KONTEXT = Kontext([Dokument("Marke.md", "Wir sind VibeMind. Bei uns kostet es 30 % weniger.", "marke")], [])


def szene_dict(nr, **kw):
    s = {"nr": nr, "sprechtext": SATZ, "bildart": "slide", "bild": BILD, "uebergang": "schnitt",
         "belege": [], "ohne_sachaussage": True, "aendert_text": True}
    s.update(kw)
    return s


def gliederung_json(anzahl=8):
    szenen = [{"kurz": f"Szene {i}", "bildart": "slide", "quellen": ["Marke.md"] if i == 1 else []}
              for i in range(1, anzahl + 1)]
    return json.dumps({"titel": "Titel", "hook": "Hook", "stufen": [
        {"name": "Einstieg", "szenen": szenen[:3]}, {"name": "Schluss", "szenen": szenen[3:]}], "fehlt": []})


def zeile(nr, aid=AID_A, stand="geplant", **kw):
    z = {"id": f"sid-{aid[:4]}-{nr}", "auftrag": aid, "nr": nr, "stufe": "Einstieg", "kurz": f"Szene {nr}",
         "bildart": "slide", "quellen": [], "stand": stand, "variante": 0, "fassung": 0, "wunsch": None,
         "rueckgabe_von": None, "sprechtext": None, "bild": {}}
    z.update(kw)
    return z


def werk(aid=AID_A, stand="geplant", szenen=(), **kw):
    w = {"auftrag": aid, "mandant": "VibeMind", "eingabe": {"thema": "Thema", "beschreibung": "Text", "minuten": 4},
         "gliederung_stand": stand, "gliederung_fassung": 0, "gliederung": None, "gliederung_wunsch": None,
         "quellen": [], "stimme": "anna", "szenen": list(szenen), "alle_fertig": False, "video_fassung": 0,
         "video_veraltet": False, "aufrufe": 0}
    w.update(kw)
    return w


GLIEDERUNG = json.loads(gliederung_json(8))


class FakeApi:
    def __init__(self, werke=(), naechster=None, nehmen=None):
        self.werke, self.neu, self.nehmen = list(werke), naechster, dict(nehmen or {})
        self.log, self.weiter_ok = [], True

    def rufe(self, name):
        return [a for n, a in self.log if n == name]

    def zaehlt(self, name):
        return len(self.rufe(name))

    def _(self, art, /, **a):
        self.log.append((art, a))

    def naechster(self):
        self._("naechster")
        return self.neu

    def arbeit(self):
        return self.werke

    def weiter(self, aid):
        self._("weiter", aid=aid)
        return self.weiter_ok

    def frist(self, aid):
        self._("frist", aid=aid)
        return True

    def gliederung(self, aid, gliederung, quellen):
        self._("gliederung", aid=aid, gliederung=gliederung, quellen=quellen)
        return "wb-aaaaaaaaaa"

    def szene_text(self, sid, koerper):
        self._("szene_text", sid=sid, nr=int(sid.rsplit("-", 1)[1]), **koerper)
        return "wb-bbbbbbbbbb"

    def szene_bild(self, sid, bildart, bild, quellen=None, belege=None):
        self._("szene_bild", sid=sid, bildart=bildart, bild=bild, quellen=quellen, belege=belege)
        return True

    def _szene_zeile(self, sid):
        for w in self.werke:
            for s in w.get("szenen") or []:
                if str(s.get("id")) == sid:
                    return s
        return None

    def szene_aufgegeben(self, sid, grund, wunsch):
        """Wie der Server: Grund an der Szene, wenn der Wunsch noch derselbe ist."""
        self._("szene_aufgegeben", sid=sid, grund=grund, wunsch=wunsch)
        s = self._szene_zeile(sid)
        if s is None or str(s.get("wunsch") or "") != str(wunsch or ""):
            return False
        s["aufgegeben"] = grund
        return True

    def gliederung_aufgegeben(self, aid, grund, wunsch):
        self._("gliederung_aufgegeben", aid=aid, grund=grund, wunsch=wunsch)
        for w in self.werke:
            if w["auftrag"] == aid:
                w["gliederung_aufgegeben"] = grund
                return True
        return False

    def video_gescheitert(self, aid, versuch, grund):
        self._("video_gescheitert", aid=aid, versuch=versuch, grund=grund)
        for w in self.werke:
            if w["auftrag"] == aid:
                w["video_fehler"] = grund
        return True

    def szene_nehmen(self, aid):
        self._("szene_nehmen", aid=aid)
        return self.nehmen.get(aid)

    def szene_clip(self, sid, variante, datei, dauer_s, stimme, engine):
        self._("szene_clip", sid=sid, variante=variante, datei=datei, dauer_s=dauer_s, stimme=stimme, engine=engine)
        return "wb-cccccccccc"

    def szene_fehler(self, sid, grund):
        self._("szene_fehler", sid=sid, grund=grund)
        return True

    def hinweis(self, aid, key, satz):
        self._("hinweis", aid=aid, key=key, satz=satz)
        return True

    def aufruf(self, aid):
        self._("aufruf", aid=aid)
        return 1

    def hochladen(self, aid, name, pfad):
        self._("hochladen", aid=aid, name=name, existiert=Path(pfad).is_file())


class Fragen:
    """Fake fuer shim_fragen: Gliederungsfragen bekommen `gliederung`, Szenenfragen eine Antwort je gefragter Szene."""

    def __init__(self, gliederung=None, szene=None):
        self.gliederung = gliederung if gliederung is not None else gliederung_json()
        self.szene = szene or (lambda nr, nachricht: szene_dict(nr))
        self.nachrichten = []

    def __call__(self, system, nachricht):
        self.nachrichten.append(nachricht)
        if "Schreibe die Gliederung" in nachricht:
            return self.gliederung
        nrn = sorted({int(n) for n in re.findall(r"^Szene (\d+):", nachricht, re.M)})
        return json.dumps({"szenen": [self.szene(n, nachricht) for n in nrn]})


def wirft(fehler):
    def f(system, nachricht):
        raise fehler
    return f


def sprechen_fake(text, stimme, ordner, **_):
    ordner.mkdir(parents=True, exist_ok=True)
    wav = ordner / "stimme.wav"
    wav.write_bytes(b"RIFF")
    sprechen_fake.gerufen.append((text, stimme))
    return SimpleNamespace(saetze=[1, 2, 3, 4], engine="elevenlabs", rueckfall=True, wav=wav, dauer_s=12.0)


sprechen_fake.gerufen = []


def rendern_fake(szene, gesprochen, marke, ordner, ziel):
    Path(ziel).write_bytes(b"mp4")
    return 12.04


@pytest.fixture
def F(tmp_path):
    sprechen_fake.gerufen.clear()
    return dict(kontext=lambda mandant, frage: KONTEXT, sprechen=sprechen_fake, rendern=rendern_fake,
                warte=lambda s: None, marke=lambda m: {}, ordner=lambda aid8, nr: tmp_path / aid8 / f"s{nr}",
                brief=lambda: "Brief")


# --- Gliederung -------------------------------------------------------------------------------

def test_neuer_auftrag_bekommt_gliederung(F):
    api = FakeApi([werk()], naechster={"id": AID_A})
    assert ein_durchlauf(api, fragen=Fragen(), **F) == "gliederung"
    assert api.rufe("gliederung")[0]["quellen"] == ["Marke.md"] and api.zaehlt("aufruf") == 1
    assert api.zaehlt("naechster") == 1


def test_gliederung_wunsch_steht_im_prompt(F):
    api = FakeApi([werk(gliederung_wunsch="Bitte kürzer", gliederung=GLIEDERUNG, gliederung_fassung=1)])
    f = Fragen()
    ein_durchlauf(api, fragen=f, **F)
    assert "Bitte kürzer" in f.nachrichten[0] and "Bisherige Gliederung" in f.nachrichten[0]


def test_unbrauchbare_gliederung_wird_einmal_nachgefragt_dann_gemeldet(F):
    api = FakeApi([werk()])
    f = Fragen(gliederung="kein json")
    assert ein_durchlauf(api, fragen=f, **F) == "fehler"
    assert len(f.nachrichten) == 2 and api.zaehlt("aufruf") == 2 and "Fehler" in f.nachrichten[1]
    h = api.rufe("hinweis")
    assert [x["key"] for x in h] == ["gliederung_f0"] and h[0]["satz"] == "Die Gliederung ließ sich nicht schreiben."
    ein_durchlauf(api, fragen=f, **F)                    # derselbe Stand wird nicht sofort erneut erfragt
    assert len(f.nachrichten) == 2


# --- Szenentexte ------------------------------------------------------------------------------

def test_zwoelf_szenen_brauchen_zwei_aufrufe(F):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(n) for n in range(1, 13)])])
    assert ein_durchlauf(api, fragen=Fragen(), **F) == "texte"
    assert api.zaehlt("aufruf") == 2 and len(api.rufe("szene_text")) == 12
    t = api.rufe("szene_text")[0]
    assert t["sprechtext"] and t["bild"] == BILD and t["uebergang"] == "schnitt" and t["quellen"] == []


def test_durchgefallene_szene_kommt_nicht_ans_tor(F):
    def szene(nr, nachricht):
        if nr == 3:
            return szene_dict(3, sprechtext=SATZ + " Es spart 40 % Kosten.")
        return szene_dict(nr)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(n) for n in range(1, 6)])])
    f = Fragen(szene=szene)
    ein_durchlauf(api, fragen=f, **F)
    assert 3 not in [r["nr"] for r in api.rufe("szene_text")] and len(api.rufe("szene_text")) == 4
    assert api.rufe("hinweis")[0]["key"].startswith("quelle_s3") and "Szene 3" in api.rufe("hinweis")[0]["satz"]
    assert api.zaehlt("aufruf") == 2          # ein Block plus EINE gesammelte Nachfrage
    assert "40" in f.nachrichten[1] and "Szene 1" not in f.nachrichten[1].split("# Zu schreibende Szenen")[1].split("#")[0]
    ein_durchlauf(api, fragen=f, **F)         # bleibt geplant, wird ohne neuen Wunsch nicht noch einmal erfragt
    assert len(api.rufe("hinweis")) == 1
    assert "Szene 3:" not in "".join(n.split("# Zu schreibende Szenen")[1] for n in f.nachrichten[2:])


def test_nachfrage_kann_durchgefallene_szene_retten(F):
    def szene(nr, nachricht):
        if nr == 2 and "Deine letzte Antwort" not in nachricht:
            return szene_dict(2, sprechtext=SATZ + " Es spart 40 % Kosten.")
        return szene_dict(nr)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(n) for n in range(1, 4)])])
    ein_durchlauf(api, fragen=Fragen(szene=szene), **F)
    assert sorted(r["nr"] for r in api.rufe("szene_text")) == [1, 2, 3] and not api.rufe("hinweis")


def test_rueckgabe_nur_bild_geht_ohne_text_tor(F):
    z = zeile(2, wunsch="anderes Bild", rueckgabe_von="clip", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[z])])
    f = Fragen(szene=lambda nr, n: szene_dict(nr, sprechtext="ganz anders", aendert_text=False))
    ein_durchlauf(api, fragen=f, **F)
    assert api.rufe("szene_bild") and not api.rufe("szene_text")
    assert "anderes Bild" in f.nachrichten[0]


def test_rueckgabe_mit_textaenderung_geht_ans_text_tor(F):
    z = zeile(2, wunsch="neuer Text", rueckgabe_von="clip", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[z])])
    ein_durchlauf(api, fragen=Fragen(), **F)
    assert api.rufe("szene_text") and not api.rufe("szene_bild")


def test_rueckgaben_gehen_je_szene_einzeln_ans_modell(F):
    zs = [zeile(n, wunsch="neu", rueckgabe_von="text", sprechtext=SATZ, bild=BILD) for n in (1, 2)] + [zeile(3)]
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=zs)])
    ein_durchlauf(api, fragen=Fragen(), **F)
    assert api.zaehlt("aufruf") == 3


# --- Erzeugen ---------------------------------------------------------------------------------

def test_clip_wird_gesprochen_gerendert_hochgeladen(F, tmp_path):
    z = zeile(3, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})
    assert ein_durchlauf(api, fragen=Fragen(), **F) == "clip"
    assert api.rufe("hochladen")[0]["name"] == "wb-0123abcd-s03-f0.mp4" and api.rufe("hochladen")[0]["existiert"]
    c = api.rufe("szene_clip")[0]
    assert c["engine"] == "chatterbox" and c["variante"] == 0 and c["datei"] == "wb-0123abcd-s03-f0.mp4"
    assert c["stimme"] == "anna" and c["dauer_s"] == 12.04 and sprechen_fake.gerufen == [(SATZ, "anna")]
    assert not (tmp_path / "0123abcd" / "s3").exists()      # nach dem Upload aufgeraeumt


def test_clipname_traegt_die_variante(F):
    z = zeile(12, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD, variante=2)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})
    ein_durchlauf(api, **F)
    assert api.rufe("hochladen")[0]["name"] == "wb-0123abcd-s12-f2.mp4" and api.rufe("szene_clip")[0]["variante"] == 2


def test_renderfehler_meldet_szene_und_haelt_andere_nicht_auf(F, tmp_path):
    za, zb = zeile(3, AID_A, "wird_erzeugt", sprechtext=SATZ, bild=BILD), zeile(1, AID_B, "wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(AID_A, "text_ok", [za]), werk(AID_B, "text_ok", [zb])], nehmen={AID_A: za, AID_B: zb})

    def rendern(szene, gesprochen, marke, ordner, ziel):
        if szene["id"] == za["id"]:
            raise RuntimeError(f"Kaputt in {ordner} bei {szene['sprechtext']}")
        return rendern_fake(szene, gesprochen, marke, ordner, ziel)
    F["rendern"] = rendern
    ein_durchlauf(api, **F)
    assert api.rufe("szene_fehler")[0]["sid"] == za["id"] and api.rufe("szene_clip")[0]["sid"] == zb["id"]
    assert (tmp_path / "0123abcd" / "s3").exists() and not (tmp_path / "feedbeef" / "s1").exists()   # Fehler bleibt zur Diagnose


@pytest.mark.parametrize("wo, fehler", [
    ("sprechen", http.client.RemoteDisconnected("Sprechtext Anna C:\\geheim")),
    ("sprechen", OSError("C:\\Users\\geheim\\stimme.wav")),
    ("rendern", ValueError(SATZ)),
    ("hochladen", ApiFehler(500, "C:\\Users\\x " + SATZ)),
])
def test_fehlertext_ohne_prompt_und_pfad(F, wo, fehler):
    z = zeile(3, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})
    if wo == "sprechen":
        F["sprechen"] = lambda *a, **k: (_ for _ in ()).throw(fehler)
    elif wo == "rendern":
        F["rendern"] = lambda *a, **k: (_ for _ in ()).throw(fehler)
    else:
        api.hochladen = lambda *a, **k: (_ for _ in ()).throw(fehler)
    assert ein_durchlauf(api, **F) == "fehler"
    grund = api.rufe("szene_fehler")[0]["grund"]
    assert "C:\\" not in grund and "Sprechtext" not in grund and SATZ[:20] not in grund and "geheim" not in grund
    assert len(grund) < 120 and type(fehler).__name__ in grund


def test_hoechstens_eine_szene_je_werk_und_durchlauf(F):
    zs = [zeile(n, stand="text_ok", sprechtext=SATZ, bild=BILD) for n in (1, 2)]
    api = FakeApi([werk(szenen=zs, stand="text_ok")], nehmen={AID_A: zs[0]})
    ein_durchlauf(api, **F)
    assert api.zaehlt("szene_nehmen") == 1 and len(api.rufe("szene_clip")) == 1


def test_stale_variante_409_beim_clip_ist_kein_fehler(F):
    z = zeile(3, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})

    def clip(*a, **k):
        raise ApiFehler(409, "Die Szene nimmt gerade keinen Clip an")
    api.szene_clip = clip
    assert ein_durchlauf(api, **F) != "fehler" and not api.rufe("szene_fehler")


# --- Budget -----------------------------------------------------------------------------------

def test_budget_erschoepft_meldet_einmal_und_rendert_weiter(F):
    z = zeile(1, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(AID_A), werk(AID_B, "text_ok", [z])], nehmen={AID_B: z})
    assert ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), **F) == "budget"
    ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), **F)
    h = api.rufe("hinweis")
    assert len({r["key"] for r in h}) == 1 and len(h) == 1 and api.rufe("szene_clip")
    assert re.fullmatch(r"[a-z0-9_]{1,60}", h[0]["key"]) and h[0]["key"].startswith("budget_")
    assert h[0]["satz"] == "Das Tagesbudget für Modell-Aufrufe ist aufgebraucht – morgen geht es weiter."


def test_budget_vor_erster_gliederung_verlaengert_frist(F):
    api = FakeApi([werk()])
    ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), **F)
    assert api.zaehlt("frist") >= 1 and api.zaehlt("aufruf") == 0


def test_budget_ist_kein_aufruf_und_aus_dem_laufenden_werk_keine_frist(F):
    api = FakeApi([werk()])
    api.weiter_ok = False                                  # Auftrag steht schon auf werkbank
    ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), **F)
    assert api.zaehlt("frist") == 0 and api.zaehlt("hinweis") == 1


def test_shim_429_budget_wird_budget_erschoepft(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError("u", 429, "x", {}, io.BytesIO(b'{"error": {"message": "budget: x", "type": "budget"}}'))
    monkeypatch.setattr(wa.urllib.request, "urlopen", urlopen)
    with pytest.raises(BudgetErschoepft):
        wa.shim_fragen("s", "n")


def test_shim_anderer_fehler_ist_llmfehler_ohne_inhalt(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b'{"error": {"message": "geheim"}}'))
    monkeypatch.setattr(wa.urllib.request, "urlopen", urlopen)
    with pytest.raises(wa.LlmFehler) as e:
        wa.shim_fragen("s", "n")
    assert "geheim" not in str(e.value)


# --- Fertigstellen ----------------------------------------------------------------------------

def test_fertigstellen_nur_wenn_alle_fertig_und_video_fehlt_oder_veraltet(F):
    gerufen = []
    hook = lambda api, w: gerufen.append(w["auftrag"])    # noqa: E731
    api = FakeApi([werk(AID_A, "text_ok", alle_fertig=True), werk(AID_B, "text_ok", alle_fertig=True, video_fassung=1),
                   werk("11112222-0000-4000-8000-000000000000", "text_ok", alle_fertig=True, video_fassung=1, video_veraltet=True),
                   werk("33334444-0000-4000-8000-000000000000", "text_ok")])
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "video"
    assert gerufen == [AID_A, "11112222-0000-4000-8000-000000000000"]
    gerufen.clear()
    ein_durchlauf(api, fertigstellen=None, **F)
    assert gerufen == []


def _fertige_szene(nr, **kw):
    return zeile(nr, **{"stand": "fertig", "variante": 0, "clip_datei": f"wb-0123abcd-s{nr:02d}-f0.mp4", **kw})


def test_fehlgeschlagenes_video_wird_nicht_alle_30_s_neu_versucht(F):
    gerufen = []

    def hook(api, w):
        gerufen.append(1)
        return "fehler"
    api = FakeApi([werk(AID_A, "text_ok", alle_fertig=True, szenen=[_fertige_szene(1), _fertige_szene(2)])])
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "fehler"
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "wartet"
    assert gerufen == [1]


def test_fehlgeschlagenes_video_geht_wieder_nach_neu_freigegebener_szene(F):
    gerufen = []

    def hook(api, w):
        gerufen.append(1)
        return "fehler"
    api = FakeApi([werk(AID_A, "text_ok", alle_fertig=True, szenen=[_fertige_szene(1), _fertige_szene(2)])])
    ein_durchlauf(api, fertigstellen=hook, **F)
    api.werke = [werk(AID_A, "text_ok", alle_fertig=True,
                      szenen=[_fertige_szene(1), _fertige_szene(2, variante=1, clip_datei="wb-0123abcd-s02-f1.mp4")])]
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "fehler"
    assert gerufen == [1, 1]


def test_ausnahme_beim_fertigstellen_wird_ebenfalls_geparkt(F):
    gerufen = []

    def hook(api, w):
        gerufen.append(1)
        raise RuntimeError("weg")
    api = FakeApi([werk(AID_A, "text_ok", alle_fertig=True, szenen=[_fertige_szene(1)])])
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "fehler"
    ein_durchlauf(api, fertigstellen=hook, **F)
    assert gerufen == [1]


def test_vorgabe_ist_video_zusammen_fertigstellen():
    import inspect

    from spaces.marketing.workers import video_zusammen
    assert inspect.signature(ein_durchlauf).parameters["fertigstellen"].default is video_zusammen.fertigstellen


class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _gedaechtnis(api):
    return api.__dict__["_werkbank_gedaechtnis"]


def test_budget_pause_endet_und_wird_im_lauf_nicht_verschoben(F):
    uhr, api, f = Uhr(), FakeApi([werk()]), Fragen()
    ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), jetzt=uhr, **F)
    ende = _gedaechtnis(api)["budget_bis"]
    assert ende == uhr.t + wa.BUDGET_PAUSE_S
    for _ in range(10):                                    # mitten in der Pause: nichts gefragt, Ende bleibt
        uhr.t += 30
        assert ein_durchlauf(api, fragen=f, jetzt=uhr, **F) == "budget"
    assert not f.nachrichten and _gedaechtnis(api)["budget_bis"] == ende
    uhr.t = ende + 1                                       # Pause vorbei: es wird wieder gefragt
    assert ein_durchlauf(api, fragen=f, jetzt=uhr, **F) == "gliederung" and f.nachrichten


def test_budget_pause_wird_bei_neuer_absage_neu_gesetzt(F):
    uhr, api = Uhr(), FakeApi([werk()])
    ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), jetzt=uhr, **F)
    uhr.t += wa.BUDGET_PAUSE_S + 1
    ein_durchlauf(api, fragen=wirft(BudgetErschoepft()), jetzt=uhr, **F)
    assert _gedaechtnis(api)["budget_bis"] == uhr.t + wa.BUDGET_PAUSE_S
    assert api.zaehlt("hinweis") == 1 and api.zaehlt("frist") == 1      # Hinweis und Frist nur einmal je Tag


def test_unbrauchbare_gliederung_bleibt_geparkt_bis_fassung_oder_wunsch_sich_aendern(F):
    uhr, api, f = Uhr(), FakeApi([werk()]), Fragen(gliederung="kein json")
    ein_durchlauf(api, fragen=f, jetzt=uhr, **F)
    assert len(f.nachrichten) == 2
    uhr.t += 3 * 86400                                     # kein zeitgesteuertes Neufragen
    ein_durchlauf(api, fragen=f, jetzt=uhr, **F)
    assert len(f.nachrichten) == 2 and api.zaehlt("hinweis") == 1 and api.zaehlt("aufruf") == 2
    api.werke = [werk(gliederung_wunsch="Anders bitte", gliederung=GLIEDERUNG, gliederung_fassung=1)]
    ein_durchlauf(api, fragen=f, jetzt=uhr, **F)           # neuer Wunsch: ein neuer Versuch
    assert len(f.nachrichten) == 4
    assert [h["key"] for h in api.rufe("hinweis")] == ["gliederung_f0", "gliederung_f1"]


def test_geparkte_gliederung_haelt_den_auftrag_nicht_am_leben(F):
    api, f = FakeApi([werk()]), Fragen(gliederung="kein json")
    ein_durchlauf(api, fragen=f, **F)
    n = api.zaehlt("weiter")
    ein_durchlauf(api, fragen=f, **F)
    assert api.zaehlt("weiter") == n                       # Lease und Frist regeln den Rest


@pytest.mark.parametrize("text, erwartet", [
    (SATZ + " Es spart 40 % Kosten.", "Szene 3: Quelle fehlt – bitte Material ergänzen."),
    ("Zu kurz.", "Szene 3: Der Text passt nicht in die Länge – bitte Wunsch ergänzen."),
])
def test_endgueltige_hinweise_nennen_die_art_des_problems(F, text, erwartet):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    ein_durchlauf(api, fragen=Fragen(szene=lambda nr, n: szene_dict(nr, sprechtext=text)), **F)
    h = api.rufe("hinweis")
    assert len(h) == 1 and h[0]["satz"] == erwartet and re.fullmatch(r"[a-z0-9_]{1,60}", h[0]["key"])


def test_endgueltiger_hinweis_bild_und_neutral(F):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    ein_durchlauf(api, fragen=Fragen(szene=lambda nr, n: szene_dict(nr, bild={"schritte": [{"vorlage": "titel"}]})), **F)
    assert api.rufe("hinweis")[0]["satz"] == "Szene 3: Die Bildschritte ließen sich nicht bauen – bitte Wunsch ergänzen."
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    ein_durchlauf(api, fragen=Fragen(szene=lambda nr, n: szene_dict(nr + 1)), **F)    # Szene fehlt in der Antwort
    assert api.rufe("hinweis")[0]["satz"] == "Szene 3 ließ sich nicht schreiben – bitte Wunsch ergänzen."


QUELLE_FEHLT = "Szene 3: Quelle fehlt – bitte Material ergänzen."


def _durchfall(nr, nachricht):
    return szene_dict(nr, sprechtext=SATZ + " Es spart 40 % Kosten.")


def test_aufgegebene_szene_wird_gemeldet_und_nach_neuem_wunsch_genau_einmal_neu_versucht(F):
    """C1: zweimal durchgefallen -> Grund am Server -> kein Neufragen (auch nicht nach einem Neustart) -> neuer
    Wunsch loescht den Grund -> genau ein neuer Versuch (Frage + Nachfrage) -> wieder aufgegeben, neu gemeldet."""
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    f = Fragen(szene=_durchfall)
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 2
    a = api.rufe("szene_aufgegeben")
    assert a == [{"sid": "sid-0123-3", "grund": QUELLE_FEHLT, "wunsch": ""}]
    assert api.werke[0]["szenen"][0]["aufgegeben"] == QUELLE_FEHLT
    ein_durchlauf(api, fragen=f, **F)
    neustart = FakeApi(api.werke)                          # neues Gedaechtnis: der Grund am Server reicht
    ein_durchlauf(neustart, fragen=f, **F)
    assert len(f.nachrichten) == 2 and neustart.zaehlt("aufruf") == 0
    # Betreiber: neuer Wunsch (der Server loescht den Grund und haengt den Wunsch an die Historie)
    api.werke[0]["szenen"][0].update(aufgegeben=None, wunsch="Nimm die 30 %", wuensche=[{"text": "Nimm die 30 %"}])
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 4 and "Nimm die 30 %" in f.nachrichten[2]
    assert api.rufe("szene_aufgegeben")[-1]["wunsch"] == "Nimm die 30 %"
    keys = [h["key"] for h in api.rufe("hinweis")]
    assert len(keys) == 2 and len(set(keys)) == 2 and all(re.fullmatch(r"[a-z0-9_]{1,60}", k) for k in keys)
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 4


def test_aufgabe_ohne_server_haelt_das_gedaechtnis(F):
    """Scheitert die Meldung, merkt sich der Arbeiter die Aufgabe selbst (wie bisher), bis der Wunsch sich aendert."""
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    api.szene_aufgegeben = lambda *a: (_ for _ in ()).throw(OSError("Netz"))
    f = Fragen(szene=_durchfall)
    ein_durchlauf(api, fragen=f, **F)
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 2


def test_aufgegebene_gliederung_wird_gemeldet_und_nach_neuem_wunsch_einmal_neu_versucht(F):
    api, f = FakeApi([werk(gliederung=GLIEDERUNG, gliederung_fassung=1)]), Fragen(gliederung="kein json")
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 2
    g = api.rufe("gliederung_aufgegeben")
    assert len(g) == 1 and g[0]["aid"] == AID_A and g[0]["wunsch"] == "" and "Gliederung" in g[0]["grund"]
    ein_durchlauf(FakeApi(api.werke), fragen=f, **F)       # Neustart: der Grund am Server reicht
    assert len(f.nachrichten) == 2
    api.werke[0].update(gliederung_aufgegeben=None, gliederung_wunsch="Kürzer", gliederung_wuensche=[{"text": "Kürzer"}])
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 4 and "Kürzer" in f.nachrichten[2]
    assert [h["key"] for h in api.rufe("hinweis")] == ["gliederung_f1", "gliederung_f1_w1"]
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 4


def test_aufgegebene_gliederung_vor_erster_fassung_haelt_den_lease_nicht(F):
    api = FakeApi([werk(gliederung_aufgegeben="Die Gliederung ließ sich nicht schreiben.")])
    ein_durchlauf(api, fragen=Fragen(), **F)
    assert api.zaehlt("weiter") == 0 and api.zaehlt("aufruf") == 0


def test_szenentext_traegt_die_geprueften_belege(F):
    belege = [{"aussage": "Bei uns kostet es 30 % weniger.", "quelle": "Marke.md"}]

    def szene(nr, nachricht):
        return szene_dict(nr, sprechtext=SATZ + " Es kostet 30 % weniger.", belege=belege, ohne_sachaussage=False)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(1)])])
    ein_durchlauf(api, fragen=Fragen(szene=szene), **F)
    t = api.rufe("szene_text")[0]
    assert t["belege"] == belege and t["quellen"] == ["Marke.md"]


def test_nur_bild_traegt_quellen_und_belege(F):
    belege = [{"aussage": "Bei uns kostet es 30 % weniger.", "quelle": "Marke.md"}]
    z = zeile(2, wunsch="anderes Bild", rueckgabe_von="clip", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[z])])
    f = Fragen(szene=lambda nr, n: szene_dict(nr, aendert_text=False, belege=belege, ohne_sachaussage=False))
    ein_durchlauf(api, fragen=f, **F)
    b = api.rufe("szene_bild")[0]
    assert b["quellen"] == ["Marke.md"] and b["belege"] == belege


def test_gescheitertes_video_wird_gemeldet_und_nach_neu_zusammenfuegen_einmal_neu_versucht(F):
    """I1: der Arbeiter parkt (Fassung, Versuch); der Grund liegt am Server (video_fehler); "Video neu
    zusammenfügen" erhoeht den Versuch und loescht den Grund -> genau ein neuer Versuch."""
    gerufen = []

    def hook(api, w):
        gerufen.append(w.get("video_versuch"))
        api.video_gescheitert(w["auftrag"], int(w.get("video_versuch") or 0), "Das Video ließ sich nicht zusammenfügen.")
        return "fehler"
    api = FakeApi([werk(AID_A, "text_ok", alle_fertig=True, szenen=[_fertige_szene(1)], video_versuch=0)])
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "fehler"
    ein_durchlauf(FakeApi(api.werke), fertigstellen=hook, **F)          # Neustart: video_fehler haelt
    assert gerufen == [0]
    api.werke[0].update(video_versuch=1, video_fehler=None)
    ein_durchlauf(api, fertigstellen=hook, **F)
    ein_durchlauf(api, fertigstellen=hook, **F)
    assert gerufen == [0, 1]


def test_problemarten_nach_den_echten_pruefsaetzen():
    assert wa._problem(['Die Zahl "40" im Sprechtext hat keinen Beleg.'])[0] == "quelle"
    assert wa._problem(["Der Sprechtext hat 12 Wörter, erlaubt sind 35 bis 110.", "Unbekannte Bildart"])[0] == "laenge"
    assert wa._problem(["Schritt 2: ab_satz steigt nicht"])[0] == "bild"
    assert wa._problem(["Ein Bild braucht 2 bis 5 Schritte."])[0] == "bild"
    assert wa._problem(["Die Szene ist kein Objekt."])[0] == "text"


def test_anderer_409_beim_clip_ist_der_fehler_der_szene(F):
    z = zeile(3, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})

    def clip(*a, **k):
        raise ApiFehler(409, "Die Datei liegt nicht im Ordner")
    api.szene_clip = clip
    assert ein_durchlauf(api, **F) == "fehler" and api.rufe("szene_fehler")[0]["sid"] == z["id"]


def test_gescheiterte_fehlermeldung_wird_nachgeholt_und_die_szene_nicht_neu_gerendert(F):
    z = zeile(3, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})
    gerendert, gemeldet = [], []

    def rendern(*a, **k):
        gerendert.append(1)
        raise RuntimeError("x")
    F["rendern"] = rendern

    def fehler(sid, grund):
        gemeldet.append(sid)
        if len(gemeldet) < 3:
            raise OSError("Netz")
        api.nehmen.clear()                                 # der Server kennt den Fehler: die Szene wird nicht mehr vergeben
        return True
    api.szene_fehler = fehler
    ein_durchlauf(api, **F)                                # Rendern scheitert, Melden scheitert
    ein_durchlauf(api, **F)                                # Nachmelden scheitert noch, Szene wird nicht gerendert
    ein_durchlauf(api, **F)                                # Nachmelden klappt
    ein_durchlauf(api, **F)
    assert len(gerendert) == 1 and len(gemeldet) == 3


def test_lease_wird_vor_gliederung_und_nachfrage_erneuert(F):
    api, f = FakeApi([werk()]), Fragen(gliederung="kein json")
    ein_durchlauf(api, fragen=f, **F)
    namen = [n for n, _ in api.log]
    assert namen.count("weiter") >= 3 and namen.index("weiter") < namen.index("aufruf")   # am Anfang, vor der Nachfrage
    assert namen.index("weiter") < namen.index("naechster")


def test_lease_aller_auftraege_wird_vor_naechster_erneuert_auch_hinter_langen_renders(F):
    alt = werk(AID_A)                                      # alter Auftrag, noch vor der ersten Gliederung
    z = zeile(1, AID_B, "wird_erzeugt", sprechtext=SATZ, bild=BILD)
    neu = werk(AID_B, "text_ok", [z], gliederung=GLIEDERUNG, gliederung_fassung=1)
    api = FakeApi([neu, alt], nehmen={AID_B: z})
    ein_durchlauf(api, fragen=Fragen(), **F)
    namen = [(n, a.get("aid")) for n, a in api.log]
    vor = namen[:namen.index(("naechster", None))]
    assert ("weiter", AID_A) in vor and ("weiter", AID_B) not in vor     # nur Werke vor der ersten Gliederung


def test_nachfrage_wartet_kurz(F):
    pausen = []
    F["warte"] = pausen.append
    ein_durchlauf(FakeApi([werk()]), fragen=Fragen(gliederung="kein json"), **F)
    assert pausen == [wa.NOCH_EINMAL_PAUSE_S]


# 12.11.2026: 8150/8151 gehoeren der Lern-Huelle (spaces.learning.huelle); Werkbank = 8154/8155.
def test_ports_und_url():
    assert wa.PORT == 8155 and wa.TAKT_S == 30 and wa.LLM_URL.endswith(":8154/v1")
    assert wa.arbeitsordner("0123abcd", 3).parts[-4:] == ("VibeMind", "werkbank", "0123abcd", "s3")


# --- N1: gescheiterte Meldungen werden nachgeholt -------------------------------------------------

def _wackelt(echt, fehler):
    """Eine Meldung, die nacheinander die Ausnahmen aus `fehler` wirft und danach `echt` ruft."""
    fehler = list(fehler)

    def melden(*a):
        melden.gerufen.append(a)
        if fehler:
            raise fehler.pop(0)
        return echt(*a)
    melden.gerufen = []
    return melden


def test_gescheiterte_szenen_aufgabe_wird_nachgemeldet_ohne_neue_frage(F):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    api.szene_aufgegeben = melden = _wackelt(api.szene_aufgegeben, [OSError("Netz"), ApiFehler(503, "weg")])
    f = Fragen(szene=_durchfall)
    ein_durchlauf(api, fragen=f, **F)                      # zweimal gefragt, Meldung scheitert
    assert len(f.nachrichten) == 2 and not api.werke[0]["szenen"][0].get("aufgegeben")
    ein_durchlauf(api, fragen=f, **F)                      # Nachmelden scheitert noch (503): nichts gefragt
    assert len(melden.gerufen) == 2 and len(f.nachrichten) == 2
    ein_durchlauf(api, fragen=f, **F)                      # Nachmelden klappt: Grund (und Wunschfeld) am Server
    assert melden.gerufen[-1] == ("sid-0123-3", QUELLE_FEHLT, "")
    assert api.werke[0]["szenen"][0]["aufgegeben"] == QUELLE_FEHLT
    ein_durchlauf(api, fragen=f, **F)                      # gemeldet ist gemeldet
    assert len(melden.gerufen) == 3 and len(f.nachrichten) == 2 and api.zaehlt("aufruf") == 2


def test_gescheiterte_gliederungs_aufgabe_wird_nachgemeldet_ohne_neue_frage(F):
    api, f = FakeApi([werk(gliederung=GLIEDERUNG, gliederung_fassung=1)]), Fragen(gliederung="kein json")
    api.gliederung_aufgegeben = melden = _wackelt(api.gliederung_aufgegeben, [OSError("Netz")])
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 2 and not api.werke[0].get("gliederung_aufgegeben")
    ein_durchlauf(api, fragen=f, **F)
    assert len(melden.gerufen) == 2 and api.werke[0]["gliederung_aufgegeben"] == wa.GRUND_GLIEDERUNG
    ein_durchlauf(api, fragen=f, **F)
    assert len(melden.gerufen) == 2 and len(f.nachrichten) == 2


def test_gescheitertes_video_wird_nachgemeldet_und_nicht_neu_zusammengefuegt(F):
    """Der echte Meldeweg von video_zusammen (_scheitern faengt die Ausnahme selbst ab)."""
    from spaces.marketing.workers import video_zusammen
    gerufen = []

    def hook(api, w):
        gerufen.append(1)
        video_zusammen._scheitern(api, w["auftrag"], "video_f1_v0", 0, "Das Video ließ sich nicht zusammenfügen.")
        return "fehler"
    api = FakeApi([werk(AID_A, "text_ok", alle_fertig=True, szenen=[_fertige_szene(1)], video_versuch=0)])
    api.video_gescheitert = melden = _wackelt(api.video_gescheitert, [OSError("Netz"), TimeoutError()])
    assert ein_durchlauf(api, fertigstellen=hook, **F) == "fehler"
    assert not api.werke[0].get("video_fehler")
    ein_durchlauf(api, fertigstellen=hook, **F)            # Nachmelden scheitert noch: nicht neu zusammengefuegt
    assert gerufen == [1] and len(melden.gerufen) == 2
    ein_durchlauf(api, fertigstellen=hook, **F)            # Nachmelden klappt: Knopf "Video neu zusammenfügen"
    assert melden.gerufen[-1] == (AID_A, 0, "Das Video ließ sich nicht zusammenfügen.")
    assert api.werke[0]["video_fehler"]
    ein_durchlauf(api, fertigstellen=hook, **F)
    assert gerufen == [1] and len(melden.gerufen) == 3


def test_nachmelden_endet_mit_einer_antwort_des_servers(F):
    """4xx (oder False) ist eine Antwort: nicht endlos wiederholen; die Szene bleibt geparkt."""
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    api.szene_aufgegeben = melden = _wackelt(api.szene_aufgegeben,
                                             [OSError("Netz"), ApiFehler(404, "Unbekannte Szene")])
    f = Fragen(szene=_durchfall)
    for _ in range(4):
        ein_durchlauf(api, fragen=f, **F)
    assert len(melden.gerufen) == 2 and len(f.nachrichten) == 2
    assert _gedaechtnis(api)["meldungen_offen"] == {}


def test_nach_neustart_ohne_meldung_wird_neu_versucht_und_dann_gemeldet(F):
    """Ein Neustart verliert die offene Meldung. Der Zustand folgt aus dem Server: ohne Grund dort wird die Szene
    neu versucht (zwei Aufrufe) und, scheitert sie wieder, dann gemeldet."""
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    api.szene_aufgegeben = lambda *a: (_ for _ in ()).throw(OSError("Netz"))
    f = Fragen(szene=_durchfall)
    ein_durchlauf(api, fragen=f, **F)
    neu = FakeApi(api.werke)                               # neues Gedaechtnis
    ein_durchlauf(neu, fragen=f, **F)
    assert len(f.nachrichten) == 4 and neu.rufe("szene_aufgegeben")[0]["grund"] == QUELLE_FEHLT
    assert api.werke[0]["szenen"][0]["aufgegeben"] == QUELLE_FEHLT
    ein_durchlauf(neu, fragen=f, **F)
    assert len(f.nachrichten) == 4


# --- N4: Grenzen der API und 422 -------------------------------------------------------------------

BELEG_OK = {"aussage": "Bei uns kostet es 30 % weniger.", "quelle": "Marke.md"}


@pytest.mark.parametrize("belege, satz, grenze", [
    ([{**BELEG_OK, "aussage": BELEG_OK["aussage"] + " " + "x" * 500}],
     "Szene 3: Eine Aussage ist zu lang – bitte Wunsch ergänzen.", "höchstens 500"),
    ([BELEG_OK] * 41, "Szene 3: Zu viele Aussagen – bitte Wunsch ergänzen.", "höchstens 40"),
])
def test_belege_ueber_den_api_grenzen_gehen_den_normalen_aufgabeweg(F, belege, satz, grenze):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    f = Fragen(szene=lambda nr, n: szene_dict(nr, sprechtext=SATZ + " Es kostet 30 % weniger.", belege=belege,
                                              ohne_sachaussage=False))
    assert ein_durchlauf(api, fragen=f, **F) == "fehler"
    assert api.zaehlt("szene_text") == 0 and len(f.nachrichten) == 2 and grenze in f.nachrichten[1]
    assert api.rufe("hinweis")[0]["satz"] == satz and api.rufe("szene_aufgegeben")[0]["grund"] == satz
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 2


NEUTRAL = "Szene {nr} ließ sich nicht schreiben – bitte Wunsch ergänzen."


def test_422_beim_szenentext_wird_nachgefragt_und_dann_aufgegeben(F):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])

    def text(sid, koerper):
        api._("szene_text", sid=sid)
        raise ApiFehler(422, "belege: ungültiger Eintrag")
    api.szene_text = text
    f = Fragen()
    assert ein_durchlauf(api, fragen=f, **F) == "fehler"
    assert api.zaehlt("szene_text") == 2 and len(f.nachrichten) == 2
    assert "Die Werkbank nahm die Szene nicht an: belege: ungültiger Eintrag" in f.nachrichten[1]
    satz = NEUTRAL.format(nr=3)
    assert api.rufe("hinweis")[0]["satz"] == satz and api.rufe("szene_aufgegeben")[0]["grund"] == satz
    assert api.rufe("hinweis")[0]["key"].startswith("text_s3")
    ein_durchlauf(api, fragen=f, **F)                      # kein Neufragen in jedem Lauf
    assert len(f.nachrichten) == 2 and api.zaehlt("szene_text") == 2


def test_422_einmal_dann_nimmt_die_nachfrage_die_szene(F):
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[zeile(3)])])
    echt, abgelehnt = api.szene_text, []

    def text(sid, koerper):
        if not abgelehnt:
            abgelehnt.append(sid)
            raise ApiFehler(422, "sprechtext: 1 bis 4000 Zeichen")
        return echt(sid, koerper)
    api.szene_text = text
    f = Fragen()
    assert ein_durchlauf(api, fragen=f, **F) == "texte"
    assert len(f.nachrichten) == 2 and api.zaehlt("szene_text") == 1 and api.zaehlt("szene_aufgegeben") == 0


def test_422_beim_nur_bild_geht_denselben_weg(F):
    z = zeile(2, wunsch="anderes Bild", rueckgabe_von="clip", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(stand="text_ok", gliederung=GLIEDERUNG, szenen=[z])])

    def bild(*a, **k):
        api._("szene_bild")
        raise ApiFehler(422, "bild.schritte: 2 bis 5 Schritte")
    api.szene_bild = bild
    f = Fragen(szene=lambda nr, n: szene_dict(nr, aendert_text=False))
    ein_durchlauf(api, fragen=f, **F)
    assert api.zaehlt("szene_bild") == 2 and len(f.nachrichten) == 2
    assert api.rufe("szene_aufgegeben")[0] == {"sid": z["id"], "grund": NEUTRAL.format(nr=2), "wunsch": "anderes Bild"}
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 2


def test_422_bei_der_gliederung_wird_aufgegeben_statt_jeden_lauf_neu_gefragt(F):
    api = FakeApi([werk()])

    def gliederung(aid, g, q):
        api._("gliederung", aid=aid)
        raise ApiFehler(422, "Jede Szene braucht einen Kurztitel")
    api.gliederung = gliederung
    f = Fragen()
    assert ein_durchlauf(api, fragen=f, **F) == "fehler"
    assert api.rufe("gliederung_aufgegeben")[0]["grund"] == wa.GRUND_GLIEDERUNG
    ein_durchlauf(api, fragen=f, **F)
    assert len(f.nachrichten) == 1 and api.zaehlt("gliederung") == 1


def test_problemarten_der_grenzen_und_des_422():
    from spaces.marketing.claw import video_texte
    assert wa._problem([video_texte.GRENZE_VIELE + " 41 Belege"])[0] == "viele"
    assert wa._problem(['Die Quelle "x" wurde nicht geholt.', video_texte.GRENZE_AUSSAGE + " 612"])[0] == "aussage"
    assert wa._problem([video_texte.GRENZE_QUELLE + " 201"])[0] == "quellname"
    assert wa._problem([wa.ABGELEHNT + " belege: ungültiger Eintrag"]) == ("text", wa.SAETZE_PROBLEM["text"])
    assert all(re.fullmatch(r"[a-z0-9_]+", art) for art in wa.SAETZE_PROBLEM)


# --- Stimme nicht bereit: keine Szene nehmen (12.11.2026) ---------------------------------------

def test_keine_szene_solange_die_stimme_nicht_bereit_ist(F, caplog):
    import logging
    z = zeile(5, stand="wird_erzeugt", sprechtext=SATZ, bild=BILD)
    api = FakeApi([werk(szenen=[z], stand="text_ok")], nehmen={AID_A: z})
    bereit = [False]
    with caplog.at_level(logging.INFO, logger="werkbank_arbeiter"):
        for _ in range(3):
            ein_durchlauf(api, stimme_bereit=lambda: bereit[0], **F)
        assert api.rufe("szene_nehmen") == [] and api.rufe("szene_fehler") == [] and api.rufe("hinweis") == []
        assert sprechen_fake.gerufen == []
        assert sum("nicht bereit" in r.getMessage() for r in caplog.records) == 1      # eine Zeile je Zustandswechsel
        bereit[0] = True
        assert ein_durchlauf(api, stimme_bereit=lambda: bereit[0], **F) == "clip"
    assert len(api.rufe("szene_nehmen")) == 1 and api.rufe("szene_clip")
    assert sum(r.getMessage() == "Stimme bereit" for r in caplog.records) == 1
