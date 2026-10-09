"""PDF-Seiten als Bild (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §2)."""
import io

import pytest
from PIL import Image

from spaces.marketing.claw import pdf_bilder


def _pdf(seiten=5, groesse=(300, 200)) -> bytes:
    bilder = [Image.new("RGB", groesse, (i * 40, 100, 200)) for i in range(seiten)]
    puffer = io.BytesIO()
    bilder[0].save(puffer, "PDF", save_all=True, append_images=bilder[1:])
    return puffer.getvalue()


def test_bild_pdf_ohne_textebene_ergibt_hoechstens_vier_seiten():
    pngs = pdf_bilder.seiten(_pdf(5))
    assert len(pngs) == 4
    for png in pngs:
        bild = Image.open(io.BytesIO(png))
        assert bild.format == "PNG" and max(bild.size) <= 1600


def test_eine_seite_ist_eine_seite():
    assert len(pdf_bilder.seiten(_pdf(1))) == 1


def test_grosse_seite_wird_auf_1600_begrenzt():
    bild = Image.open(io.BytesIO(pdf_bilder.seiten(_pdf(1, (3000, 2000)))[0]))
    assert max(bild.size) == 1600


@pytest.mark.parametrize("roh", [b"%PDF-1.4 kaputt", b"kein pdf", b""])
def test_kaputte_pdf_ist_fehler(roh):
    with pytest.raises(pdf_bilder.PdfBildFehler):
        pdf_bilder.seiten(roh)


def test_namen_und_herkunft():
    assert pdf_bilder.seiten_name("karte.pdf", 2) == "karte.pdf#2"
    assert pdf_bilder.ist_seite("PDF-Seite 2") and not pdf_bilder.ist_seite("Anhang")


def test_fehlendes_pypdfium2_ist_ein_hinweis_kein_absturz(monkeypatch):
    """T6: fehlt das Modul, wird es PdfBildFehler (Hinweis) - kein ImportError, der Chat- und Editor-Runde kippt."""
    import builtins
    echt = builtins.__import__

    def ohne(name, *a, **kw):
        if name == "pypdfium2" or name.startswith("pypdfium2."):
            raise ImportError("No module named 'pypdfium2'")
        return echt(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", ohne)
    with pytest.raises(pdf_bilder.PdfBildFehler, match="pypdfium2 fehlt"):
        pdf_bilder.seiten(b"%PDF-1.4\n%%EOF")
