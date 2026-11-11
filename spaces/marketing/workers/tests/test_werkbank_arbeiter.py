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

    def szene_bild(self, sid, bildart, bild):
        self._("szene_bild", sid=sid, bildart=bildart, bild=bild)
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
    assert "C:\\" not in grund and "Sprechtext" not in grund and SATZ[:20] not in grund and grund.endswith(").") or "." in grund
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
        raise ApiFehler(409, "nicht mehr")
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


# --- Hochladen --------------------------------------------------------------------------------

class UploadApi(wa.WerkbankApi):
    def __init__(self, skript):
        self.skript, self.log, self.schlafen = list(skript), [], lambda s: None

    def _senden(self, methode, pfad, koerper=None, typ="application/json", zeit=60):
        self.log.append((methode, pfad, koerper if methode != "PUT" else len(koerper)))
        aktion = self.skript.pop(0) if self.skript else None
        if callable(aktion):
            return aktion(methode, pfad, koerper)
        if methode == "POST" and pfad.endswith("/beginnen"):
            return 200, {"ok": True, "laenge": 0}
        if methode == "PUT":
            off = int(pfad.rsplit("=", 1)[1])
            return 200, {"laenge": off + len(koerper)}
        return 200, {"ok": True}


def test_hochladen_in_teilen_mit_sha256(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "TEIL_BYTES", 10)
    f = tmp_path / "wb-0123abcd-s01-f0.mp4"
    f.write_bytes(bytes(range(25)))
    api = UploadApi([])
    api.hochladen(AID_A, f.name, f)
    puts = [x for x in api.log if x[0] == "PUT"]
    assert [p[1].rsplit("=", 1)[1] for p in puts] == ["0", "10", "20"] and [p[2] for p in puts] == [10, 10, 5]
    import hashlib
    assert json.loads(api.log[-1][2]) == {"sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    assert json.loads(api.log[0][2]) == {"bytes": 25}


def test_hochladen_setzt_nach_409_an_der_gemeldeten_laenge_fort(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "TEIL_BYTES", 10)
    f = tmp_path / "x.mp4"
    f.write_bytes(bytes(range(25)))
    api = UploadApi([None, lambda m, p, k: (200, {"laenge": 10}), lambda m, p, k: (409, {"detail": "Offset", "laenge": 20})])
    api.hochladen(AID_A, "x.mp4", f)
    puts = [x[1].rsplit("=", 1)[1] for x in api.log if x[0] == "PUT"]
    assert puts == ["0", "10", "20"]          # nach dem 409 geht es bei 20 weiter, nicht bei 10 noch einmal


def test_hochladen_gibt_nach_drei_wiederholungen_auf(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "TEIL_BYTES", 10)
    f = tmp_path / "x.mp4"
    f.write_bytes(bytes(range(25)))
    kaputt = lambda m, p, k: (503, {})    # noqa: E731
    api = UploadApi([None] + [kaputt] * 10)
    with pytest.raises(ApiFehler):
        api.hochladen(AID_A, "x.mp4", f)
    assert len([x for x in api.log if x[0] == "PUT"]) == 1 + wa.WIEDERHOLUNGEN


def test_hochladen_netzfehler_wird_wiederholt(tmp_path, monkeypatch):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"abc")

    def weg(m, p, k):
        raise ConnectionResetError()
    api = UploadApi([None, weg, weg])
    api.hochladen(AID_A, "x.mp4", f)
    assert len([x for x in api.log if x[0] == "PUT"]) == 3 and api.log[-1][1].endswith("/abschliessen")


def test_api_fehler_traegt_laenge_bei_409():
    class A(wa.WerkbankApi):
        def __init__(self):
            pass

        def _senden(self, *a, **k):
            return 409, {"detail": "falscher Offset", "laenge": 7}
    with pytest.raises(ApiFehler) as e:
        A()._anfrage("PUT", "/x")
    assert e.value.code == 409 and e.value.laenge == 7


def test_ports_und_url():
    assert wa.PORT == 8151 and wa.TAKT_S == 30 and wa.LLM_URL.endswith(":8150/v1")
    assert wa.arbeitsordner("0123abcd", 3).parts[-4:] == ("VibeMind", "werkbank", "0123abcd", "s3")
