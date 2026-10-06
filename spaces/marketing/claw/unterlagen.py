"""Text aus hochgeladenen Unterlagen (PDF, DOCX, TXT, MD) fuer den Gestaltungs-Agenten.

Rein: kein Modell, kein Netz, keine Dateien. `text_aus` wirft nie; was nicht
lesbar ist (kaputt, verschluesselt, leer, fremde Endung), ergibt "".
Grosse Eingaben werden schon beim Lesen begrenzt, nicht erst danach.
"""
import io
import os

MAX_ZEICHEN = 20_000
# Beim Lesen wird weit ueber MAX_ZEICHEN hinaus nichts mehr gesammelt.
LESE_GRENZE = 4 * MAX_ZEICHEN
_MAX_SEITEN = 200
_VERMERK = " [gekürzt]"


def _normal(text: str) -> str:
    return " ".join(text.split())


def _pdf_text(roh: bytes) -> str:
    from pypdf import PdfReader

    leser = PdfReader(io.BytesIO(roh))
    if leser.is_encrypted and not leser.decrypt(""):
        return ""
    teile: list[str] = []
    laenge = 0
    for seite in leser.pages[:_MAX_SEITEN]:
        t = seite.extract_text() or ""
        teile.append(t)
        laenge += len(t)
        if laenge >= LESE_GRENZE:
            break
    return " ".join(teile)


def _docx_text(roh: bytes) -> str:
    from docx import Document

    dok = Document(io.BytesIO(roh))
    teile: list[str] = []
    laenge = 0

    def sammeln(t: str) -> bool:
        nonlocal laenge
        teile.append(t)
        laenge += len(t)
        return laenge >= LESE_GRENZE

    for absatz in dok.paragraphs:
        if sammeln(absatz.text):
            return " ".join(teile)
    for tabelle in dok.tables:
        for zeile in tabelle.rows:
            for zelle in zeile.cells:
                if sammeln(zelle.text):
                    return " ".join(teile)
    return " ".join(teile)


def _klartext(roh: bytes) -> str:
    roh = roh[: LESE_GRENZE * 4]
    try:
        return roh.decode("utf-8")
    except UnicodeDecodeError:
        return roh.decode("latin-1")


_LESER = {
    ".pdf": _pdf_text,
    ".docx": _docx_text,
    ".txt": _klartext,
    ".md": _klartext,
}


def text_aus(name: str, roh: bytes) -> str:
    """Lesbarer Text einer Unterlage, Whitespace normalisiert; "" wenn keiner. Wirft nie."""
    try:
        leser = _LESER.get(os.path.splitext(str(name))[1].lower())
        if leser is None:
            return ""
        return _normal(leser(roh))[:LESE_GRENZE]
    except Exception:  # Parser-Fehler jeder Art (pypdf, zipfile, XML) = nicht lesbar
        return ""


def unterlagen_text(
    dateien: list[tuple[str, bytes]], max_zeichen: int = MAX_ZEICHEN
) -> tuple[str, list[str]]:
    """("Unterlage: a.pdf\\n…\\n\\nUnterlage: b.docx\\n…", hinweise).

    Ueberlange Summen werden anteilig gekuerzt, jede gekuerzte Unterlage endet auf
    "[gekürzt]". Dateien ohne Text erscheinen nur als Hinweis.
    """
    gelesen: list[tuple[str, str]] = []
    hinweise: list[str] = []
    for name, roh in dateien:
        t = text_aus(name, roh)
        if t:
            gelesen.append((name, t))
        else:
            hinweise.append(f"{name} hat keinen lesbaren Text")
    gesamt = sum(len(t) for _, t in gelesen)
    abschnitte: list[str] = []
    for name, t in gelesen:
        if gesamt > max_zeichen:
            t = t[: max_zeichen * len(t) // gesamt].rstrip() + _VERMERK
        abschnitte.append(f"Unterlage: {name}\n{t}")
    return "\n\n".join(abschnitte), hinweise
