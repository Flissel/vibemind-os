"""Signierter Datei-Link: Signatur, Ablauf, Pfadschutz, Server."""
import urllib.error
import urllib.request

import pytest

from djset.dienst.auftraege import Ablage
from djset.dienst.link import GUELTIG_S, aufloesen, link, signieren, starten

SCHLUESSEL = b"test-schluessel-nur-fuer-tests"
BASIS = "http://link.test:8146"
JETZT = 1_800_000_000.0
LAUF = "lauf-1"
WAV = b"RIFF\x00\x00\x00\x00WAVEdaten" * 50


@pytest.fixture
def ablage(tmp_path):
    return Ablage(tmp_path)


def _fertig(ablage, zustand="fertig", dateien=("videomusik.wav", "videomusik.mp3")):
    auftrag = ablage.anlegen("video", {}, "mcp")
    auftrag.zustand = zustand
    auftrag.lauf = LAUF
    ablage.speichern(auftrag)
    ordner = ablage.daten / "runs" / LAUF
    ordner.mkdir(parents=True, exist_ok=True)
    for name in dateien:
        (ordner / name).write_bytes(WAV)
    return auftrag


def _pfad(url):
    return url[len(BASIS):]


def test_gueltiger_link_200(ablage):
    auftrag = _fertig(ablage)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    assert url.startswith(f"{BASIS}/datei/{auftrag.id}/videomusik.wav?ablauf=")
    status, datei = aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT + 10)
    assert status == 200
    assert datei == ablage.daten / "runs" / LAUF / "videomusik.wav"


def test_fertig_mit_befund_ist_erlaubt(ablage):
    auftrag = _fertig(ablage, zustand="fertig_mit_befund")
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.mp3", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT)[0] == 200


def test_signatur_ist_hex_hmac():
    sig = signieren(SCHLUESSEL, "x", "videomusik.wav", 5)
    assert len(sig) == 64 and int(sig, 16) >= 0
    assert sig != signieren(b"anderer", "x", "videomusik.wav", 5)


def test_abgelaufen_403(ablage):
    auftrag = _fertig(ablage)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    ablauf = int(JETZT) + GUELTIG_S
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), ablauf)[0] == 200
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), ablauf + 1) == (403, None)


def test_falsche_signatur_403(ablage):
    auftrag = _fertig(ablage)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    pfad = _pfad(url)
    kaputt = pfad[:-1] + ("0" if pfad[-1] != "0" else "1")
    assert aufloesen(ablage, SCHLUESSEL, kaputt, JETZT) == (403, None)
    assert aufloesen(ablage, b"anderer", pfad, JETZT) == (403, None)
    # verlaengerter Ablauf ohne neue Signatur
    laenger = pfad.replace(f"ablauf={int(JETZT) + GUELTIG_S}", f"ablauf={int(JETZT) + 99999}")
    assert aufloesen(ablage, SCHLUESSEL, laenger, JETZT) == (403, None)


@pytest.mark.parametrize(
    "query",
    ["", "?ablauf=1", "?sig=abc", "?ablauf=x&sig=abc", "?ablauf=1&ablauf=2&sig=abc", "?ablauf=1&sig=a&sig=b"],
)
def test_fehlende_oder_kaputte_parameter_403(ablage, query):
    auftrag = _fertig(ablage)
    pfad = f"/datei/{auftrag.id}/videomusik.wav{query}"
    assert aufloesen(ablage, SCHLUESSEL, pfad, JETZT) == (403, None)


def test_fremder_name_404(ablage):
    auftrag = _fertig(ablage, dateien=("videomusik.wav", "bericht.md"))
    ablauf = int(JETZT) + GUELTIG_S
    sig = signieren(SCHLUESSEL, auftrag.id, "bericht.md", ablauf)
    pfad = f"/datei/{auftrag.id}/bericht.md?ablauf={ablauf}&sig={sig}"
    assert aufloesen(ablage, SCHLUESSEL, pfad, JETZT) == (404, None)


@pytest.mark.parametrize(
    "name",
    ["../auftrag.json", "%2e%2e%2fauftrag.json", "videomusik.wav/../x", "..%2Fx", "videomusik.wav%2F..%2Fx"],
)
def test_pfad_ausbruch_404(ablage, name):
    auftrag = _fertig(ablage)
    ablauf = int(JETZT) + GUELTIG_S
    # auch mit passender Signatur fuer den rohen Namen darf nichts ausbrechen
    sig = signieren(SCHLUESSEL, auftrag.id, name, ablauf)
    pfad = f"/datei/{auftrag.id}/{name}?ablauf={ablauf}&sig={sig}"
    assert aufloesen(ablage, SCHLUESSEL, pfad, JETZT)[1] is None
    assert aufloesen(ablage, SCHLUESSEL, pfad, JETZT)[0] in (403, 404)


def test_pfad_ausbruch_mit_gueltiger_signatur_ist_404(ablage):
    auftrag = _fertig(ablage)
    ablauf = int(JETZT) + GUELTIG_S
    sig = signieren(SCHLUESSEL, auftrag.id, "../auftrag.json", ablauf)
    pfad = f"/datei/{auftrag.id}/../auftrag.json?ablauf={ablauf}&sig={sig}"
    assert aufloesen(ablage, SCHLUESSEL, pfad, JETZT) == (404, None)


def test_unbekannter_oder_fremdgeformter_auftrag_404(ablage):
    for fremde_id in ("20261124-120000-video-abcd", "..", "x"):
        ablauf = int(JETZT) + GUELTIG_S
        sig = signieren(SCHLUESSEL, fremde_id, "videomusik.wav", ablauf)
        pfad = f"/datei/{fremde_id}/videomusik.wav?ablauf={ablauf}&sig={sig}"
        assert aufloesen(ablage, SCHLUESSEL, pfad, JETZT) == (404, None)


def test_unsignierte_anfrage_verraet_keinen_auftrag(ablage):
    auftrag = _fertig(ablage)
    echt = aufloesen(ablage, SCHLUESSEL, f"/datei/{auftrag.id}/videomusik.wav", JETZT)
    fremd = aufloesen(ablage, SCHLUESSEL, "/datei/20261124-120000-video-abcd/videomusik.wav", JETZT)
    assert echt == fremd == (403, None)


@pytest.mark.parametrize("zustand", ["wartet", "laeuft", "gescheitert", "abgebrochen"])
def test_unfertiger_auftrag_404(ablage, zustand):
    auftrag = _fertig(ablage, zustand=zustand)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT) == (404, None)


def test_fehlende_datei_404(ablage):
    auftrag = _fertig(ablage, dateien=("videomusik.wav",))
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.mp3", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT) == (404, None)


def test_auftrag_ohne_lauf_404(ablage):
    auftrag = ablage.anlegen("video", {}, "mcp")
    auftrag.zustand = "fertig"
    ablage.speichern(auftrag)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT) == (404, None)


def test_kaputter_auftrag_404(ablage):
    auftrag = _fertig(ablage)
    (ablage.ordner(auftrag.id) / "auftrag.json").write_text("{kaputt", encoding="utf-8")
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT) == (404, None)


@pytest.mark.parametrize("lauf", ["...", "NUL", "a*b", "x.", "..", "a b", "a?b", "-x", "a/b", "a\\b"])
def test_feindliche_lauf_id_404(ablage, lauf):
    auftrag = _fertig(ablage)
    auftrag.lauf = lauf
    ablage.speichern(auftrag)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT) == (404, None)


def test_oserror_in_hole_ist_404_und_still(ablage, monkeypatch, capfd):
    auftrag = _fertig(ablage)

    def kaputt(_id):
        raise PermissionError("Zugriffsverletzung")

    monkeypatch.setattr(ablage, "hole", kaputt)
    url = link(BASIS, SCHLUESSEL, auftrag.id, "videomusik.wav", JETZT)
    assert aufloesen(ablage, SCHLUESSEL, _pfad(url), JETZT) == (404, None)

    server = starten("127.0.0.1", 0, ablage, SCHLUESSEL)
    try:
        port = server.server_address[1]
        import time

        echt = link(f"http://127.0.0.1:{port}", SCHLUESSEL, auftrag.id, "videomusik.wav", time.time())
        with pytest.raises(urllib.error.HTTPError) as fehler:
            urllib.request.urlopen(echt, timeout=10)
        assert fehler.value.code == 404
        fehler.value.close()
        try:
            raise ConnectionResetError("weg")
        except ConnectionResetError:
            server.handle_error(None, ("127.0.0.1", 1))
    finally:
        server.shutdown()
        server.server_close()
    assert capfd.readouterr().err == ""


def test_handler_hat_zeitlimit(ablage):
    server = starten("127.0.0.1", 0, ablage, SCHLUESSEL)
    try:
        assert server.RequestHandlerClass.timeout == 30
    finally:
        server.shutdown()
        server.server_close()


def test_server_liefert_bytes(ablage):
    auftrag = _fertig(ablage)
    server = starten("127.0.0.1", 0, ablage, SCHLUESSEL)
    try:
        port = server.server_address[1]
        import time

        url = link(f"http://127.0.0.1:{port}", SCHLUESSEL, auftrag.id, "videomusik.wav", time.time())
        with urllib.request.urlopen(url, timeout=10) as antwort:
            assert antwort.status == 200
            assert antwort.headers["Content-Type"] == "audio/wav"
            assert int(antwort.headers["Content-Length"]) == len(WAV)
            assert antwort.read() == WAV
        mp3 = link(f"http://127.0.0.1:{port}", SCHLUESSEL, auftrag.id, "videomusik.mp3", time.time())
        with urllib.request.urlopen(mp3, timeout=10) as antwort:
            assert antwort.headers["Content-Type"] == "audio/mpeg"

        for schlecht, code in (
            (f"http://127.0.0.1:{port}/datei/{auftrag.id}/videomusik.wav", 403),
            (f"http://127.0.0.1:{port}/anderes", 404),
            (f"http://127.0.0.1:{port}/", 404),
        ):
            with pytest.raises(urllib.error.HTTPError) as fehler:
                urllib.request.urlopen(schlecht, timeout=10)
            assert fehler.value.code == code
            fehler.value.close()

        anfrage = urllib.request.Request(url, data=b"x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as fehler:
            urllib.request.urlopen(anfrage, timeout=10)
        assert fehler.value.code == 404
        fehler.value.close()
    finally:
        server.shutdown()
        server.server_close()
