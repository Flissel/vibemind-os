"""PDF-Seiten als Bild fuer Claude (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §2): die ersten
4 Seiten mit pypdfium2 zu PNG, laengste Kante hoechstens 1600 px. Nur am PC. pdfium ist nicht
fadensicher; Editor- und Marken-Faden laufen im selben Prozess, deshalb eine Sperre."""
from __future__ import annotations

import io
import threading

MAX_SEITEN = 4
MAX_KANTE = 1600
MAX_BYTES = 20 * 1024 * 1024
MAX_SKALA = 4.0
HERKUNFT = "PDF-Seite"
_SPERRE = threading.Lock()


class PdfBildFehler(ValueError):
    """PDF nicht darstellbar; die Meldung wird Hinweis."""


def seiten_name(datei: str, nr: int) -> str:
    return f"{datei}#{nr}"


def ist_seite(herkunft) -> bool:
    return isinstance(herkunft, str) and herkunft.startswith(HERKUNFT)


def seiten(roh: bytes, max_seiten: int = MAX_SEITEN, max_kante: int = MAX_KANTE) -> list[bytes]:
    if not isinstance(roh, (bytes, bytearray)) or not roh.startswith(b"%PDF") or len(roh) > MAX_BYTES:
        raise PdfBildFehler("keine lesbare PDF")
    try:                     # fehlt das Modul (nicht am PC installiert), wird es ein Hinweis - die Runde laeuft weiter
        import pypdfium2 as pdfium
        from PIL import Image
    except ImportError as e:
        raise PdfBildFehler("PDF-Darstellung nicht verfügbar (pypdfium2 fehlt)") from e
    with _SPERRE:
        try:
            pdf = pdfium.PdfDocument(bytes(roh))
        except Exception as e:  # noqa: BLE001 - kaputt, verschluesselt, fremd: alles "nicht lesbar"
            raise PdfBildFehler("PDF nicht lesbar") from e
        try:
            pngs: list[bytes] = []
            for i in range(min(len(pdf), max_seiten)):
                seite = pdf[i]
                try:
                    breite, hoehe = seite.get_size()
                    if breite <= 0 or hoehe <= 0:
                        raise PdfBildFehler("leere Seite")
                    skala = min(max_kante / max(breite, hoehe), MAX_SKALA)
                    bild = seite.render(scale=skala).to_pil()
                finally:
                    seite.close()
                bild.thumbnail((max_kante, max_kante), Image.LANCZOS)
                puffer = io.BytesIO()
                bild.convert("RGB").save(puffer, "PNG", optimize=True)
                pngs.append(puffer.getvalue())
        except PdfBildFehler:
            raise
        except Exception as e:  # noqa: BLE001
            raise PdfBildFehler("PDF-Seite nicht darstellbar") from e
        finally:
            pdf.close()
    if not pngs:
        raise PdfBildFehler("PDF ohne Seiten")
    return pngs
