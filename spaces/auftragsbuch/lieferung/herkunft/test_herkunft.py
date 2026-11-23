"""Tests fuer herkunft.py. Eigenstaendig lauffaehig: pytest <diese Datei>."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import herkunft  # noqa: E402
from herkunft import (  # noqa: E402
    ABLEHNUNG,
    EINGAENGE_BETREIBER,
    braucht_betreiber,
    eingang_pruefen,
    erlaubt,
)

SCHLUESSEL = "test-schluessel-nur-fuer-den-test"


def _hash(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


UMGEBUNG = {
    "BRAIN_EINGANG_APP_SHA256": _hash(SCHLUESSEL),
    "BRAIN_EINGANG_TELEGRAM_BETREIBER_SHA256": _hash("anderer-schluessel"),
}


def test_konstanten():
    assert EINGAENGE_BETREIBER == frozenset({"app", "telegram-betreiber"})
    assert ABLEHNUNG == "Nur aus einer Eingabe des Betreibers"


def test_richtiger_schluessel_liefert_eingang():
    assert eingang_pruefen("app", SCHLUESSEL, UMGEBUNG) == "app"


def test_bindestrich_name_nutzt_unterstrich_variable():
    assert eingang_pruefen("telegram-betreiber", "anderer-schluessel", UMGEBUNG) == "telegram-betreiber"


def test_hash_gross_geschrieben_in_umgebung_wird_akzeptiert():
    env = {"BRAIN_EINGANG_APP_SHA256": _hash(SCHLUESSEL).upper()}
    assert eingang_pruefen("app", SCHLUESSEL, env) == "app"


def test_falscher_schluessel():
    assert eingang_pruefen("app", "falsch", UMGEBUNG) == "unbekannt"


def test_schluessel_eines_anderen_eingangs():
    assert eingang_pruefen("app", "anderer-schluessel", UMGEBUNG) == "unbekannt"


def test_leerer_oder_fehlender_schluessel():
    assert eingang_pruefen("app", "", UMGEBUNG) == "unbekannt"
    assert eingang_pruefen("app", None, UMGEBUNG) == "unbekannt"


def test_unbekannter_eingangsname():
    env = dict(UMGEBUNG, BRAIN_EINGANG_DASHBOARD_SHA256=_hash(SCHLUESSEL))
    assert eingang_pruefen("dashboard", SCHLUESSEL, env) == "unbekannt"
    assert eingang_pruefen("", SCHLUESSEL, env) == "unbekannt"
    assert eingang_pruefen(None, SCHLUESSEL, env) == "unbekannt"


def test_fehlende_umgebungsvariable():
    assert eingang_pruefen("app", SCHLUESSEL, {}) == "unbekannt"
    assert eingang_pruefen("app", SCHLUESSEL, {"BRAIN_EINGANG_APP_SHA256": ""}) == "unbekannt"


def test_kaputte_eingaben_werfen_nie():
    assert eingang_pruefen(123, SCHLUESSEL, UMGEBUNG) == "unbekannt"  # type: ignore[arg-type]
    assert eingang_pruefen("app", 123, UMGEBUNG) == "unbekannt"  # type: ignore[arg-type]
    assert eingang_pruefen("app", SCHLUESSEL, None) == "unbekannt"  # type: ignore[arg-type]
    assert eingang_pruefen("app", "sch\udcfcl", UMGEBUNG) == "unbekannt"
    assert eingang_pruefen("app", SCHLUESSEL, {"BRAIN_EINGANG_APP_SHA256": "kein-hex"}) == "unbekannt"


def test_braucht_betreiber():
    assert braucht_betreiber({"required_origin": "betreiber"}) is True
    assert braucht_betreiber({}) is False
    assert braucht_betreiber({"required_origin": "anderes"}) is False
    assert braucht_betreiber({"required_origin": None}) is False
    assert braucht_betreiber(None) is False  # type: ignore[arg-type]


def test_erlaubt():
    streng = {"required_origin": "betreiber"}
    assert erlaubt("unbekannt", streng) is False
    assert erlaubt(None, streng) is False
    assert erlaubt("dashboard", streng) is False
    assert erlaubt("app", streng) is True
    assert erlaubt("telegram-betreiber", streng) is True
    assert erlaubt("unbekannt", {}) is True
    assert erlaubt(None, {}) is True
    assert erlaubt("app", {}) is True


def test_schluessel_kommt_in_keinem_repr_vor():
    module_text = repr(vars(herkunft))
    ergebnis = eingang_pruefen("app", SCHLUESSEL, UMGEBUNG)
    for text in (repr(ergebnis), module_text, repr(EINGAENGE_BETREIBER), ABLEHNUNG):
        assert SCHLUESSEL not in text


def test_fehler_meldet_keinen_schluessel():
    try:
        eingang_pruefen("app", SCHLUESSEL, None)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover
        assert SCHLUESSEL not in repr(exc)


# --- Endpruefung: kein Fail-open bei Zielen am Auftragsbuch ---

def test_unbekanntes_auftragsbuch_werkzeug_ohne_ereignis_wird_abgelehnt():
    from herkunft import erlaubt_werkzeug
    for herkunft in ("app", "telegram-betreiber", "unbekannt", None):
        assert erlaubt_werkzeug(herkunft, "auftragsbuch", "rowboat_vorschlaege_anlegen", None) is False
        assert erlaubt_werkzeug(herkunft, "auftragsbuch", "rowboat_vorschlaege_anlegen", {}) is False


def test_lesen_und_probe_ohne_ereignis_bleiben_erlaubt():
    from herkunft import erlaubt_werkzeug
    for tool in ("auftrag_lesen", "auftrag_probe"):
        assert erlaubt_werkzeug("unbekannt", "auftragsbuch", tool, None) is True


def test_auftragsbuch_werkzeug_mit_ereignis_folgt_dem_verzeichnis():
    from herkunft import erlaubt_werkzeug
    ereignis = {"required_origin": "betreiber"}
    assert erlaubt_werkzeug("app", "auftragsbuch", "rowboat_vorschlaege_anlegen", ereignis) is True
    assert erlaubt_werkzeug("unbekannt", "auftragsbuch", "rowboat_vorschlaege_anlegen", ereignis) is False
    assert erlaubt_werkzeug("unbekannt", "auftragsbuch", "sales_post_pruefen", {"tool": "x"}) is True


def test_anderer_server_ohne_ereignis_wie_bisher():
    from herkunft import erlaubt_werkzeug
    assert erlaubt_werkzeug("unbekannt", "spaces-ideas", "idea_create", None) is True
    assert erlaubt_werkzeug(None, "spaces-rowboat", "rowboat_status", {}) is True
    assert erlaubt_werkzeug("unbekannt", "spaces-desktop", "x", {"required_origin": "betreiber"}) is False


def test_erlaubt_werkzeug_wirft_nie():
    from herkunft import erlaubt_werkzeug
    assert erlaubt_werkzeug("app", 5, None, "kaputt") in (True, False)  # type: ignore[arg-type]
    assert erlaubt_werkzeug("app", "auftragsbuch", None, None) is False  # type: ignore[arg-type]


# --- Herkunfts-Objekt: "getippt" (master) und Eingang (Block 2) in einem Wert ---

def test_getippt_hat_vorrang_und_der_eingang_wird_nicht_geprueft():
    from herkunft import herkunft_bestimmen
    aufrufe = []

    def eingang():
        aufrufe.append(1)
        return "app"

    assert herkunft_bestimmen(True, eingang) == {"art": "getippt"}
    assert aufrufe == []  # der Eingang wird nur geprueft, wenn nicht getippt
    assert herkunft_bestimmen(True, "app") == {"art": "getippt"}


def test_kein_getippt_plus_gueltiger_eingang():
    from herkunft import herkunft_bestimmen
    assert herkunft_bestimmen(False, lambda: "app") == {"art": "eingang:app"}
    assert herkunft_bestimmen(False, lambda: "telegram-betreiber") == {"art": "eingang:telegram-betreiber"}


def test_weder_noch_ergibt_art_none():
    from herkunft import herkunft_bestimmen
    for eingang in ("unbekannt", "", None, "dashboard", 5, lambda: "unbekannt", lambda: 1 / 0):
        assert herkunft_bestimmen(False, eingang) == {"art": None}
    assert herkunft_bestimmen("ja", lambda: "app") == {"art": "eingang:app"}  # nur echtes True zaehlt als getippt


def test_weitergegebenes_getippt_ist_betreiber_aber_nie_getippt():
    from herkunft import eingabe_herkunft, ist_betreiber, weitergeben
    h = {"art": "getippt"}
    assert eingabe_herkunft(h) == "getippt"
    w = weitergeben(h)
    assert w == {"art": "getippt", "weitergegeben": True}
    assert h == {"art": "getippt"}  # Kopie, Original unveraendert
    assert eingabe_herkunft(w) is None
    assert ist_betreiber(w) is True
    assert eingabe_herkunft(weitergeben(w)) is None


def test_telegram_eingang_ist_betreiber_aber_nie_getippt():
    from herkunft import eingabe_herkunft, ist_betreiber
    h = {"art": "eingang:telegram-betreiber"}
    assert ist_betreiber(h) is True
    assert eingabe_herkunft(h) is None
    assert ist_betreiber(None) is False and ist_betreiber({"art": None}) is False
    assert ist_betreiber({"art": "eingang:dashboard"}) is False


def test_erlaubt_mit_objekt():
    ereignis = {"required_origin": "betreiber"}
    for h in ({"art": "getippt"}, {"art": "eingang:app"}, {"art": "getippt", "weitergegeben": True}):
        assert erlaubt(h, ereignis) is True
    for h in ({"art": None}, {"art": "eingang:dashboard"}, None):
        assert erlaubt(h, ereignis) is False
        assert erlaubt(h, {}) is True


def test_erlaubt_werkzeug_mit_objekt_am_auftragsbuch_bleibt_fail_closed():
    from herkunft import erlaubt_werkzeug
    betreiber = {"art": "getippt"}
    assert erlaubt_werkzeug(betreiber, "auftragsbuch", "rowboat_vorschlaege_anlegen", None) is False
    assert erlaubt_werkzeug(betreiber, "auftragsbuch", "rowboat_vorschlaege_anlegen", {}) is False
    assert erlaubt_werkzeug({"art": None}, "auftragsbuch", "auftrag_lesen", None) is True
    ereignis = {"required_origin": "betreiber"}
    assert erlaubt_werkzeug(betreiber, "auftragsbuch", "rowboat_vorschlaege_anlegen", ereignis) is True
    assert erlaubt_werkzeug({"art": None}, "auftragsbuch", "rowboat_vorschlaege_anlegen", ereignis) is False


def test_alte_zeichenketten_form_bleibt_erlaubt():
    from herkunft import erlaubt_werkzeug, ist_betreiber
    ereignis = {"required_origin": "betreiber"}
    assert erlaubt("app", ereignis) is True
    assert erlaubt("unbekannt", ereignis) is False
    assert erlaubt_werkzeug("telegram-betreiber", "auftragsbuch", "x", ereignis) is True
    assert ist_betreiber("app") is True
    assert ist_betreiber("getippt") is False  # kein Eingangsname


def test_falsche_formen_ergeben_nie_true_und_werfen_nie():
    from herkunft import eingabe_herkunft, erlaubt_werkzeug, ist_betreiber, weitergeben
    ereignis = {"required_origin": "betreiber"}
    for h in ([], ["getippt"], 5, 1.5, {"art": 5}, {"art": ["getippt"]}, {}, {"x": "getippt"}, object(), True):
        assert ist_betreiber(h) is False, h
        assert eingabe_herkunft(h) is None, h
        assert erlaubt(h, ereignis) is False, h
        assert erlaubt_werkzeug(h, "auftragsbuch", "rowboat_vorschlaege_anlegen", ereignis) is False, h
        w = weitergeben(h)
        assert eingabe_herkunft(w) is None and ist_betreiber(w) is False, h
    # weitergegeben ist False, fehlt oder ist True; alles andere ist eine falsche Form
    assert eingabe_herkunft({"art": "getippt", "weitergegeben": 0}) is None
    assert eingabe_herkunft({"art": "getippt", "weitergegeben": "nein"}) is None


# --- Fix 1: rohe Zeichenkette zaehlt nicht, gespeicherter Rest wird nie "getippt" ---

def test_rohe_zeichenkette_als_eingang_zaehlt_nicht():
    from herkunft import herkunft_bestimmen
    for roh in ("app", "telegram-betreiber", "unbekannt"):
        assert herkunft_bestimmen(False, roh) == {"art": None}
    assert herkunft_bestimmen(True, "app") == {"art": "getippt"}


def test_gespeicherter_rest_wird_nie_getippt_aber_bleibt_betreiber():
    from herkunft import eingabe_herkunft, ist_betreiber, weitergeben
    gespeichert = {"art": "getippt"}  # alter Rest oder manipulierte Datei: nicht weitergegeben
    w = weitergeben(gespeichert)
    assert eingabe_herkunft(w) is None
    assert ist_betreiber(w) is True
    assert weitergeben(w) == w  # idempotent
    assert weitergeben(weitergeben(gespeichert)) == w


def test_weitergeben_falscher_form_ergibt_art_none():
    from herkunft import eingabe_herkunft, ist_betreiber, weitergeben
    for roh in (None, "kaputt", 5, [], {"art": 5}, {"art": "getippt", "weitergegeben": "ja"}):
        w = weitergeben(roh)
        assert w["art"] is None and w["weitergegeben"] is True, roh
        assert eingabe_herkunft(w) is None and ist_betreiber(w) is False, roh
