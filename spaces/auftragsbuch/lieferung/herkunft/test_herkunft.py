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
