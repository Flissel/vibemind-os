from spaces.marketing.claw import denkspur


class Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _spur(gesendet, uhr=None, antwort=True):
    def senden(d, s):
        gesendet.append((d, [dict(x) for x in s]))
        return antwort
    return denkspur.Spur(senden, uhr=uhr or Uhr(), jetzt=lambda: "08:03:41")


def test_erstes_denken_geht_sofort_dann_gedrosselt():
    g, uhr = [], Uhr()
    sp = _spur(g, uhr)
    sp.denken("a")
    sp.denken("b")
    assert [d for d, _ in g] == ["a"]
    uhr.t = 2.0
    sp.denken("c")
    assert [d for d, _ in g] == ["a", "abc"]


def test_ende_sendet_immer_den_stand():
    g, uhr = [], Uhr()
    sp = _spur(g, uhr)
    sp.denken("a")
    sp.denken("b")
    sp.ende()
    assert g[-1][0] == "ab"


def test_schritte_mit_zeit_und_grenzen():
    g = []
    sp = _spur(g)
    for i in range(70):
        sp.schritt(f"s{i}" + "x" * 300)
    assert len(sp.schritte) == 60
    assert sp.schritte[0]["text"].startswith("s10")
    assert all(len(s["text"]) == 200 and s["zeit"] == "08:03:41" for s in sp.schritte)


def test_denken_kuerzt_vorn():
    g = []
    sp = _spur(g)
    sp.denken("A" * 15_000)
    sp.denken("B" * 15_000)
    t = sp.denken_text
    assert t.startswith(denkspur.GEKUERZT)
    assert len(t) <= denkspur.DENKEN_MAX
    assert t.endswith("B" * 15_000)


def test_sehr_viel_denken_bleibt_begrenzt_und_markiert():
    sp = _spur([])
    for _ in range(100):
        sp.denken("x" * 10_000)
    assert len(sp.denken_text) <= denkspur.DENKEN_MAX
    assert sp.denken_text.startswith(denkspur.GEKUERZT)


def test_korrektur_haengt_trennzeile_und_schritt_an():
    sp = _spur([])
    sp.denken("erst")
    sp.korrektur()
    sp.denken("dann")
    assert sp.denken_text == "erst" + denkspur.KORREKTUR + "dann"
    assert sp.schritte[-1]["text"] == "Korrekturrunde"


def test_senden_false_schaltet_ab():
    g, uhr = [], Uhr()
    sp = _spur(g, uhr, antwort=False)
    sp.denken("a")
    uhr.t = 10
    sp.denken("b")
    sp.ende()
    assert len(g) == 1 and sp.aus


def test_senden_fehler_bleibt_offen():
    aufrufe, uhr = [], Uhr()

    def senden(d, s):
        aufrufe.append(d)
        if len(aufrufe) == 1:
            raise OSError("weg")
        return True
    sp = denkspur.Spur(senden, uhr=uhr, jetzt=lambda: "08:00:00")
    sp.denken("a")            # wirft intern, wird geschluckt
    uhr.t = 2.0
    sp.melden()               # offen geblieben => sendet erneut
    assert aufrufe == ["a", "a"] and not sp.aus


def test_leeres_denken_ignoriert():
    g = []
    sp = _spur(g)
    sp.denken("")
    sp.denken(None)  # type: ignore[arg-type]
    assert g == []
