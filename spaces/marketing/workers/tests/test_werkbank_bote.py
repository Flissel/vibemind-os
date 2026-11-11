"""Werkbank-Bote ohne Netz und ohne Datenbank: planen() rein, durchgang() mit Fake-DB und Fake-RPC."""
import logging

import pytest

from spaces.marketing.workers import werkbank_bote as wb
from spaces.marketing.workers.werkbank_bote import Plan, RowboatStand, planen

BASIS = "https://sales.example.ts.net"
ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def kennung(i):
    return "wb-" + "a" * 8 + ALPHABET[i // 32] + ALPHABET[i % 32]


def V(i, stand, tor="text", ersatz=False, **kw):
    v = {"kennung": kennung(i), "auftrag": "a1", "szene": f"s{i}", "tor": tor, "nr": i + 1, "fassung": 1,
         "stand": stand, "text": None, "titel": "Mein Video", "kurz": f"Szene Nr {i}", "ersatz": ersatz}
    v.update(kw)
    return v


def T(i, **kw):
    return wb.vorschlagstext(V(i, "offen", **kw), BASIS)


def KEPT(text):
    return {"kind": "kept", "key": " ".join(text.split()).lower(), "at": "2026-11-11T10:00:00Z"}


def DISMISSED(text):
    return {"kind": "dismissed", "key": " ".join(text.split()).lower(), "at": "2026-11-11T10:00:00Z"}


# ---------- vorschlagstext / kennung_in ----------

def test_texte_der_drei_tore():
    g = wb.vorschlagstext(V(0, "wartend", tor="gliederung", szene=None, fassung=2), BASIS)
    assert g == f"[Video] Gliederung „Mein Video“ (Fassung 2) · [Ansehen]({BASIS}/marketing/werkbank/v/{kennung(0)})"
    assert wb.vorschlagstext(V(2, "wartend"), BASIS).startswith("[Video] Szene 3 Text: Szene Nr 2 · [Ansehen](")
    assert wb.vorschlagstext(V(3, "wartend", tor="clip"), BASIS).startswith("[Video] Szene 4 Clip ansehen · [Ansehen](")


def test_titel_ohne_markdown_zeichen_einzeilig_und_gekappt():
    v = V(0, "wartend", tor="gliederung", szene=None, titel="Hi @all [x](http://böse)\n\nzweite  Zeile" + "x" * 300)
    t = wb.vorschlagstext(v, BASIS)
    assert "\n" not in t and "@" not in t.split(" · ")[0]
    kopf = t.split(" · ")[0]
    assert "[x]" not in kopf and "(http" not in kopf
    assert len(kopf) < 220
    k = wb.vorschlagstext(V(1, "wartend", kurz="k" * 500 + " (a)"), BASIS).split(" · ")[0]
    assert len(k) <= len("[Video] Szene 2 Text: ") + wb.KURZ_MAX


def test_kennung_in_nur_aus_werkbank_link_und_gross_klein_egal():
    assert wb.kennung_in(T(5)) == kennung(5)
    assert wb.kennung_in(T(5).lower()) == kennung(5)
    assert wb.kennung_in(T(5).upper()) == kennung(5)
    assert wb.kennung_in("[x](https://a/marketing/werkbank/v/wb-zzzzzzzzzzzz)") is None
    assert wb.kennung_in("irgendwas wb-aaaaaaaaaa") is None
    assert wb.kennung_in("") is None


# ---------- planen ----------

def test_erster_block_hoechstens_zehn():
    p = planen([V(i, "wartend") for i in range(14)], RowboatStand(), BASIS)
    assert [k for k, _ in p.hinzufuegen] == [kennung(i) for i in range(10)]
    assert p.hinzufuegen[0][1] == T(0)


def test_naechster_block_erst_wenn_keiner_offen():
    lebend = [V(0, "offen")] + [V(i, "wartend") for i in range(1, 5)]
    assert planen(lebend, RowboatStand(vorschlaege=[T(0)]), BASIS).hinzufuegen == []


def test_ersatz_einer_zurueckgegebenen_szene_kommt_sofort():
    lebend = [V(i, "offen") for i in range(3)] + [V(3, "wartend", ersatz=True), V(4, "wartend")]
    p = planen(lebend, RowboatStand(vorschlaege=[T(i) for i in range(3)]), BASIS)
    assert [k for k, _ in p.hinzufuegen] == [kennung(3)]


def test_ersatz_wartet_bei_zehn_offenen():
    lebend = [V(i, "offen") for i in range(10)] + [V(10, "wartend", ersatz=True)]
    assert planen(lebend, RowboatStand(vorschlaege=[T(i) for i in range(10)]), BASIS).hinzufuegen == []


def test_block_fuellt_nur_bis_zehn_in_gelieferter_reihenfolge_mit_ersatz_dazwischen():
    lebend = [V(0, "wartend"), V(1, "wartend", ersatz=True)] + [V(i, "wartend") for i in range(2, 14)]
    p = planen(lebend, RowboatStand(), BASIS)
    assert [k for k, _ in p.hinzufuegen] == [kennung(i) for i in range(10)]


def test_angenommen_wird_passt_und_abgehakt():
    p = planen([V(0, "offen")], RowboatStand(items=[("key0", T(0), False)], signale=[KEPT(T(0))]), BASIS)
    assert p.entscheiden == [(kennung(0), "passt")] and p.abhaken == [(kennung(0), "key0")]


def test_behalten_signal_ohne_item_ist_passt():
    p = planen([V(0, "offen")], RowboatStand(signale=[KEPT(T(0))]), BASIS)
    assert p.entscheiden == [(kennung(0), "passt")] and p.abhaken == []


def test_verworfen_wird_nochmal():
    p = planen([V(0, "offen")], RowboatStand(signale=[DISMISSED(T(0))]), BASIS)
    assert p.entscheiden == [(kennung(0), "nochmal")] and p.hinzufuegen == []


def test_signal_anderer_kennung_zaehlt_nicht():
    p = planen([V(0, "offen")], RowboatStand(signale=[DISMISSED(T(1))]), BASIS)
    assert p.entscheiden == [] and [k for k, _ in p.hinzufuegen] == [kennung(0)]


def test_eigener_rueckzug_ist_kein_verwerfen():
    p = planen([V(0, "zurueckziehen")], RowboatStand(vorschlaege=[T(0)], signale=[DISMISSED(T(0))]), BASIS)
    assert p.entscheiden == [] and p.zurueckziehen == [(kennung(0), T(0))] and p.erledigt == []


def test_veraltete_fassung_angenommen_wird_nur_abgehakt():
    p = planen([V(0, "zurueckziehen")], RowboatStand(items=[("key0", T(0), False)], signale=[KEPT(T(0))]), BASIS)
    assert p.entscheiden == [] and p.abhaken == [(kennung(0), "key0")] and p.erledigt == [kennung(0)]


def test_zurueckziehen_ohne_spur_ist_gleich_erledigt():
    p = planen([V(0, "zurueckziehen")], RowboatStand(signale=[DISMISSED(T(0))]), BASIS)
    assert p.erledigt == [kennung(0)] and p.entscheiden == [] and p.zurueckziehen == []


def test_verlorener_vorschlag_wird_neu_eingestellt():
    p = planen([V(0, "offen")], RowboatStand(), BASIS)
    assert p.hinzufuegen == [(kennung(0), T(0))] and p.entscheiden == []


def test_verlorener_vorschlag_zaehlt_als_offen():
    p = planen([V(0, "offen"), V(1, "wartend")], RowboatStand(), BASIS)
    assert [k for k, _ in p.hinzufuegen] == [kennung(0)]


def test_angenommen_ohne_haken_wird_abgehakt_auch_ohne_item():
    p = planen([V(0, "angenommen"), V(1, "angenommen")],
               RowboatStand(items=[("key0", T(0), False)]), BASIS)
    assert p.abhaken == [(kennung(0), "key0"), (kennung(1), "")]


def test_schluessel_in_rowboat_sind_kleingeschrieben_und_passen_trotzdem():
    kleinschrift = T(0).lower()
    p = planen([V(0, "offen")], RowboatStand(vorschlaege=[kleinschrift]), BASIS)
    assert p.hinzufuegen == [] and p.entscheiden == []


# ---------- durchgang ----------

class FakeDb:
    def __init__(self, lebend, ok=True, falsch=()):
        self.lebend, self.ok, self.falsch, self.aufrufe = lebend, ok, set(falsch), []

    def query_via_docker(self, sql, params=None, container=None, streng=False):
        assert streng
        self.aufrufe.append(("lebend", None))
        return [{"v": v} for v in self.lebend]

    def query_one(self, sql, params=None, container=None, streng=False):
        assert streng
        name = sql.split("marketing.")[1].split("(")[0]
        self.aufrufe.append((name, params))
        if name == "video_entscheiden":
            return {"r": {"ok": self.ok}}
        return {"ok": name not in self.falsch}


class FakeRpc:
    def __init__(self, vorschlaege=(), items=(), signale=(), entfernt=True, bestaetigt=None):
        self.vorschlaege, self.items, self.signale = list(vorschlaege), list(items), list(signale)
        self.entfernt, self.bestaetigt = entfernt, bestaetigt
        self.aufrufe = []

    def __call__(self, kanal, body):
        self.aufrufe.append((kanal, body))
        if kanal == "todo:get":
            assert body is None
            return {"list": {"blocks": [{"kind": "item", "item": {"key": k, "text": t, "checked": c}}
                                        for k, t, c in self.items]}, "suggestions": self.vorschlaege}
        if kanal == "todo:plannerSignals":
            assert body is None
            return {"signals": self.signale}
        if kanal == "todo:withdrawSuggestion":
            return {"removed": self.entfernt}
        if kanal == "todo:addSuggestions":
            texte = body["texts"] if self.bestaetigt is None else self.bestaetigt
            return {"added": [" ".join(t.split()) for t in texte]}
        if kanal == "todo:setChecked":
            return {"success": True}
        raise AssertionError(kanal)

    def kanaele(self):
        return [k for k, _ in self.aufrufe]


@pytest.fixture(autouse=True)
def basis_gesetzt(monkeypatch):
    monkeypatch.setenv("WERKBANK_UI_BASIS_URL", BASIS)
    monkeypatch.setattr(wb, "_BASIS_GEMELDET", False)


def namen(db):
    return [n for n, _ in db.aufrufe]


def test_durchgang_stellt_ein_und_meldet_gepostet():
    db, rpc = FakeDb([V(0, "wartend"), V(1, "wartend")]), FakeRpc()
    z = wb.durchgang(db, rpc)
    assert z == {"eingestellt": 2, "zurueckgezogen": 0, "abgehakt": 0, "entschieden": 0}
    assert ("todo:addSuggestions", {"texts": [T(0), T(1)]}) in rpc.aufrufe
    gepostet = [p for n, p in db.aufrufe if n == "video_vorschlag_gepostet"]
    assert gepostet == [{"k": kennung(0), "t": T(0)}, {"k": kennung(1), "t": T(1)}]


def test_ersetzen_erst_zurueckziehen_dann_neu():
    lebend = [V(0, "zurueckziehen"), V(0, "wartend", ersatz=True, kennung=kennung(7), fassung=2)]
    db, rpc = FakeDb(lebend), FakeRpc(vorschlaege=[T(0)])
    z = wb.durchgang(db, rpc)
    assert rpc.kanaele() == ["todo:get", "todo:plannerSignals", "todo:withdrawSuggestion", "todo:addSuggestions"]
    assert namen(db) == ["lebend", "video_vorschlag_erledigt", "video_vorschlag_gepostet"]
    assert z["zurueckgezogen"] == 1 and z["eingestellt"] == 1


def test_zurueckgezogen_nur_bei_removed_true_sonst_nicht_erledigt():
    db, rpc = FakeDb([V(0, "zurueckziehen")]), FakeRpc(vorschlaege=[T(0)], entfernt=False)
    z = wb.durchgang(db, rpc)
    assert "video_vorschlag_erledigt" not in namen(db) and z["zurueckgezogen"] == 0


def test_nicht_bestaetigter_vorschlag_bleibt_wartend():
    db, rpc = FakeDb([V(0, "wartend")]), FakeRpc(bestaetigt=[])
    z = wb.durchgang(db, rpc)
    assert "video_vorschlag_gepostet" not in namen(db) and z["eingestellt"] == 0


def test_bestaetigt_wird_mit_dem_von_rowboat_gelieferten_text_gemeldet():
    db, rpc = FakeDb([V(0, "wartend", kurz="a   b")]), FakeRpc()
    wb.durchgang(db, rpc)
    text = [p["t"] for n, p in db.aufrufe if n == "video_vorschlag_gepostet"][0]
    assert text == " ".join(T(0, kurz="a   b").split())


def test_text_schon_in_rowboat_wird_nicht_doppelt_eingestellt_aber_gemeldet():
    db, rpc = FakeDb([V(0, "wartend")]), FakeRpc(vorschlaege=[T(0)])
    z = wb.durchgang(db, rpc)
    assert "todo:addSuggestions" not in rpc.kanaele()
    assert "video_vorschlag_gepostet" in namen(db) and z["eingestellt"] == 1


def test_angenommen_entscheiden_dann_abhaken():
    db = FakeDb([V(0, "offen")])
    rpc = FakeRpc(items=[("key0", T(0), False)], signale=[KEPT(T(0))])
    z = wb.durchgang(db, rpc)
    assert namen(db) == ["lebend", "video_entscheiden", "video_vorschlag_abgehakt"]
    assert ("todo:setChecked", {"key": "key0", "checked": True}) in rpc.aufrufe
    assert db.aufrufe[1][1] == {"k": kennung(0), "u": "passt"}
    assert z["entschieden"] == 1 and z["abgehakt"] == 1


def test_veraltet_angenommen_wird_nicht_entschieden():
    db = FakeDb([V(0, "zurueckziehen")])
    rpc = FakeRpc(items=[("key0", T(0), False)], signale=[KEPT(T(0))])
    wb.durchgang(db, rpc)
    assert "video_entscheiden" not in namen(db)
    assert namen(db) == ["lebend", "video_vorschlag_erledigt"]   # kein pointless abgehakt fuer zurueckziehen


def test_entscheiden_ohne_ok_wird_nicht_gezaehlt():
    db, rpc = FakeDb([V(0, "offen")], ok=False), FakeRpc(signale=[DISMISSED(T(0))])
    assert wb.durchgang(db, rpc)["entschieden"] == 0


def test_ohne_basis_adresse_wird_rowboat_nicht_angefasst(monkeypatch, caplog):
    monkeypatch.delenv("WERKBANK_UI_BASIS_URL")
    db, rpc = FakeDb([V(0, "wartend")]), FakeRpc()
    with caplog.at_level(logging.ERROR, logger="werkbank-bote"):
        wb.durchgang(db, rpc)
        wb.durchgang(db, rpc)
    assert rpc.aufrufe == [] and db.aufrufe == []
    assert len([r for r in caplog.records if "WERKBANK_UI_BASIS_URL" in r.getMessage()]) == 1


def test_fehler_loggt_nur_den_typ(caplog):
    class Boese(Exception):
        pass

    def rpc(kanal, body):
        raise Boese("Bearer geheim-123")

    db = FakeDb([V(0, "wartend")])
    with caplog.at_level(logging.INFO, logger="werkbank-bote"):
        z = wb.durchgang(db, rpc)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "Boese" in text and "geheim" not in text
    assert z["eingestellt"] == 0 and "video_vorschlag_gepostet" not in namen(db)


@pytest.mark.parametrize("antworten", [
    {"todo:get": {"error": "x"}},
    {"todo:plannerSignals": {"error": "x"}},
])
def test_fehlerantwort_ohne_erwarteten_schluessel_ist_ein_gescheiterter_durchgang(antworten, caplog):
    echt = FakeRpc()

    def rpc(kanal, body):
        return antworten.get(kanal, echt(kanal, body))

    db = FakeDb([V(0, "offen")])
    with caplog.at_level(logging.ERROR, logger="werkbank-bote"):
        z = wb.durchgang(db, rpc)
    assert z == {"eingestellt": 0, "zurueckgezogen": 0, "abgehakt": 0, "entschieden": 0}
    assert "ValueError" in caplog.text and namen(db) == ["lebend"]
    assert "todo:addSuggestions" not in echt.kanaele()


def test_rpc_traegt_schluessel_aus_datei_und_host(monkeypatch, tmp_path):
    datei = tmp_path / "key"
    datei.write_text("geheim-schluessel\n", encoding="utf-8")
    monkeypatch.setenv("WERKBANK_ROWBOAT_SCHLUESSEL_DATEI", str(datei))
    monkeypatch.setenv("WERKBANK_ROWBOAT_RPC_URL", "http://127.0.0.1:3220/")
    gesehen = {}

    class Antwort:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"removed": true}'

    def urlopen(anfrage, timeout):
        gesehen.update(url=anfrage.full_url, auth=anfrage.get_header("Authorization"),
                       host=anfrage.get_header("Host"), timeout=timeout, body=anfrage.data)
        return Antwort()

    monkeypatch.setattr(wb.urllib.request, "urlopen", urlopen)
    assert wb._rpc("todo:get", None) == {"removed": True}
    assert gesehen == {"url": "http://127.0.0.1:3220/rpc/todo:get", "auth": "Bearer geheim-schluessel",
                       "host": "127.0.0.1:3220", "timeout": 10, "body": b"null"}


# ---------- wartend mit Spur in Rowboat ----------

def test_wartend_im_fach_wird_als_gepostet_gemeldet_und_zaehlt_als_offen():
    lebend = [V(0, "offen"), V(1, "wartend"), V(2, "wartend", ersatz=True)] + [V(i, "wartend") for i in range(3, 14)]
    p = planen(lebend, RowboatStand(vorschlaege=[T(0), T(1)]), BASIS)
    assert p.gepostet == [(kennung(1), T(1))]
    # 2 offen gezaehlt -> Ersatz kommt, Rest des Blocks nicht (offen_zahl != 0)
    assert [k for k, _ in p.hinzufuegen] == [kennung(2)]


def test_wartend_im_fach_zaehlt_gegen_das_limit():
    lebend = [V(i, "wartend") for i in range(10)] + [V(10, "wartend", ersatz=True)]
    p = planen(lebend, RowboatStand(vorschlaege=[T(i) for i in range(10)]), BASIS)
    assert len(p.gepostet) == 10 and p.hinzufuegen == []


def test_wartend_als_item_oder_kept_wird_direkt_entschieden_und_abgehakt():
    lebend = [V(0, "wartend"), V(1, "wartend")]
    p = planen(lebend, RowboatStand(items=[("key0", T(0), False)], signale=[KEPT(T(1))]), BASIS)
    assert p.entscheiden == [(kennung(0), "passt"), (kennung(1), "passt")]
    assert p.abhaken == [(kennung(0), "key0")] and p.gepostet == [] and p.hinzufuegen == []


def test_wartend_mit_dismissed_signal_wird_nochmal():
    p = planen([V(0, "wartend"), V(1, "wartend")], RowboatStand(signale=[DISMISSED(T(0))]), BASIS)
    assert p.entscheiden == [(kennung(0), "nochmal")]
    assert [k for k, _ in p.hinzufuegen] == [kennung(1)]


def test_durchgang_meldet_wartend_aus_dem_fach_ohne_neu_einzustellen():
    db, rpc = FakeDb([V(0, "offen"), V(1, "wartend")]), FakeRpc(vorschlaege=[T(0), T(1)])
    z = wb.durchgang(db, rpc)
    assert ("video_vorschlag_gepostet", {"k": kennung(1), "t": T(1)}) in db.aufrufe
    assert "todo:addSuggestions" not in rpc.kanaele() and z["eingestellt"] == 1


# ---------- gepostet false ----------

def test_gepostet_false_zieht_den_text_sofort_zurueck():
    db, rpc = FakeDb([V(0, "wartend")], falsch={"video_vorschlag_gepostet"}), FakeRpc()
    z = wb.durchgang(db, rpc)
    assert ("todo:withdrawSuggestion", {"text": T(0)}) in rpc.aufrufe
    assert z["eingestellt"] == 0 and z["zurueckgezogen"] == 1


def test_gepostet_false_und_rueckzug_scheitert_naechster_durchgang_raeumt_auf(caplog):
    db, rpc = FakeDb([V(0, "wartend")], falsch={"video_vorschlag_gepostet"}), FakeRpc(entfernt=False)
    with caplog.at_level(logging.WARNING, logger="werkbank-bote"):
        z = wb.durchgang(db, rpc)
    assert z["zurueckgezogen"] == 0 and "naechste Durchgang" in caplog.text
    # naechster Durchgang: Zeile ist nicht mehr lebend, der Text steht noch im Fach -> verwaist
    db2, rpc2 = FakeDb([]), FakeRpc(vorschlaege=[T(0)])
    z2 = wb.durchgang(db2, rpc2)
    assert ("todo:withdrawSuggestion", {"text": T(0)}) in rpc2.aufrufe and z2["zurueckgezogen"] == 1


def test_verwaister_vorschlag_wird_zurueckgezogen_lebende_nicht():
    p = planen([V(0, "offen")], RowboatStand(vorschlaege=[T(0), T(5)]), BASIS)
    assert p.verwaist == [(kennung(5), T(5))] and p.zurueckziehen == []


def test_verlorener_offener_vorschlag_wird_nicht_als_waise_zurueckgezogen():
    db, rpc = FakeDb([V(0, "offen")], falsch={"video_vorschlag_gepostet"}), FakeRpc()
    z = wb.durchgang(db, rpc)
    assert "todo:withdrawSuggestion" not in rpc.kanaele() and "video_vorschlag_gepostet" not in namen(db)
    assert z["eingestellt"] == 1


# ---------- Zaehler ----------

def test_zaehler_zaehlen_nur_echte_aenderungen():
    db = FakeDb([V(0, "zurueckziehen"), V(1, "angenommen")], falsch={"video_vorschlag_abgehakt"})
    rpc = FakeRpc(vorschlaege=[T(0)])
    z = wb.durchgang(db, rpc)
    assert z == {"eingestellt": 0, "zurueckgezogen": 1, "abgehakt": 0, "entschieden": 0}
