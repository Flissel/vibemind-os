"""Datenblock eines Videokonzepts lesen und pruefen: Python-Nachbau von ``readKonzept`` und ``checkVideoKonzept``.

Stand: 2026-11-14, folgt Fork-Commit aa793dce (``konzept.ts`` dort unveraendert seit e93a3e93; 236 Pruef-Faelle).

Vorbild ist der Rowboat-Fork, ``apps/x/packages/shared/src/konzept.ts`` (``scan``, ``parseTag``, ``findBlock``,
``readKonzept``, ``checkVideoKonzept``). Massstab sind die gemeinsamen Pruefvektoren ``konzept_vektoren.json`` aus
demselben Commit: fuer jeden Fall gleiche Gueltigkeit, gleicher erster Fehlersatz, gleiches ``video``. So kann
"die Seite zeigt A, die Werkbank bekommt B" nicht still entstehen.

VENDORING-REGEL: Diese Datei wird BYTE-GLEICH nach sales-claw kopiert und ist dort der einzige Pruefer. Deshalb:
genau eine Datei, nur Standardbibliothek, keine Importe aus diesem Repo, keine Ein-/Ausgabe, nur reine Funktionen,
und die Quelle ist reines ASCII (Umlaute und unsichtbare Zeichen als ``\\uXXXX``-Escapes; ein Editor kann so keine
Zeichen der Trim-Menge still veraendern). Aenderungen nur hier (vibemind-os
``spaces/rowboat/deploy/headless/auftraege/``) und danach erneut kopieren.

Oeffentlich:

- ``lesen(html) -> (daten | None, satz | None)``: wie ``readKonzept``. ``daten`` = ``{"art", "bezuege", "video"?}``
  (``video`` ungeprueft, nur bei ``art == "videokonzept"``). Unbrauchbarer Block: ``(None, satz)``. Kein Block:
  ``(None, None)`` (wie ``readKonzept``: kein Satz). Der Hinweis zu ``bezuege`` ohne ``[[...]]`` entfaellt (kein Fehler).
- ``video_pruefen(daten) -> satz | None``: erster Satz von ``checkVideoKonzept(daten.video)``.
- ``video_pruefen_objekt(video) -> satz | None``: dieselben Regeln fuer ein schon gelesenes ``video`` (Sales bekommt
  JSON, kein HTML; dort ist ``4.0`` eine float-Zahl und gilt wie in JavaScript als ganze Zahl).
- ``js_trim(s) -> str``: ``String.prototype.trim`` (fuer ``beschreibung`` und jeden Vergleich getrimmter Felder).
- ``utf16_laenge(s) -> int``: ``String.length`` (UTF-16-Einheiten).

JavaScript-Semantik, bewusst nachgebaut: Laengen in UTF-16-Einheiten (``String.length``); ``trim`` und ``\\s`` mit
genau der Zeichenmenge von ``String.prototype.trim`` (mit U+FEFF und U+00A0, ohne U+0085 und U+200B); ``JSON.parse``
mit eigenem Leser ohne Rekursion (lehnt NaN/Infinity, fuehrende Nullen, rohe Steuerzeichen und BOM ab; doppelte
Schluessel: der letzte gilt; ganzzahlige Zahlen wie ``4.0`` werden ``int``); der Lese-Deckel von 1 MiB zaehlt
UTF-16-Einheiten. Alles laeuft in linearer Zeit, ohne Regex mit verschachtelten Quantoren, ohne ``html.parser``.
"""
from __future__ import annotations

import re

KONZEPT_BLOCK_ID = "rowboat-konzept"
LESE_MAX = 1_048_576            # KONZEPT_LESE_MAX (UTF-16-Einheiten)
BLOCK_MAX = 20_000              # KONZEPT_BLOCK_MAX (UTF-16-Einheiten, roh)
BILDARTEN = ("slide", "avatar_slide", "avatar_gross", "clip")

# String.prototype.trim und \s in JavaScript: WhiteSpace (inkl. Zs) und LineTerminator.
_JS_WS = ("\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
          "\u2028\u2029\u202f\u205f\u3000\ufeff")
_WS_KLASSE = "\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff"

_TAG_ANFANG = re.compile(r"<[!?/A-Za-z]")
_NAME = re.compile(f"[^{_WS_KLASSE}/>]*")
_WS_ODER_SCHRAEG = re.compile(f"[{_WS_KLASSE}/]*")
_WS = re.compile(f"[{_WS_KLASSE}]*")
_ATTR_REST = re.compile(f"[^{_WS_KLASSE}=>/]*")
_UNGEQUOTET = re.compile(f"[^{_WS_KLASSE}>]*")
_ENDE_FOLGT = frozenset(_JS_WS + "/>")


def js_trim(s: str) -> str:
    """``String.prototype.trim``: entfernt genau die JS-Leerraum-Menge (mit U+FEFF/U+00A0, ohne U+0085/U+200B)."""
    return s.strip(_JS_WS)


def utf16_laenge(s: str) -> int:
    """``String.length``: UTF-16-Einheiten (einzelne Surrogate zaehlen eins)."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _begrenzt(h: str) -> str:
    if len(h) * 2 <= LESE_MAX:
        return h
    roh = h.encode("utf-16-le", "surrogatepass")
    if len(roh) // 2 <= LESE_MAX:
        return h
    # wie html.slice(0, LESE_MAX): ein Astralzeichen auf der Grenze bleibt als einzelnes hohes Surrogat stehen
    return roh[: 2 * LESE_MAX].decode("utf-16-le", "surrogatepass")


# --- Scanner (scan/parseTag im Fork), hier nur bis zum ersten Datenblock ----------------------------------

def _tag(h: str, start: int):
    """``parseTag``: -> (schliessend, name, attrs, ende) oder None, wenn das Tag nicht abgeschlossen ist."""
    n = len(h)
    j = start + 1
    schliessend = j < n and h[j] == "/"
    if schliessend:
        j += 1
    ns = j
    j = _NAME.match(h, j).end()
    name = h[ns:j].lower()
    attrs: dict[str, str] = {}
    while True:
        j = _WS_ODER_SCHRAEG.match(h, j).end()
        if j >= n:
            return None
        if h[j] == ">":
            return schliessend, name, attrs, j + 1
        a = j
        j = _ATTR_REST.match(h, j + 1).end()        # mindestens ein Zeichen (auch ein fuehrendes "=")
        attr = h[a:j].lower()
        k = _WS.match(h, j).end()
        wert = ""
        if k < n and h[k] == "=":
            k = _WS.match(h, k + 1).end()
            if k >= n:
                return None
            q = h[k]
            if q in "\"'":
                e = h.find(q, k + 1)
                if e == -1:
                    return None
                wert = h[k + 1:e]
                j = e + 1
            else:
                vs = k
                k = _UNGEQUOTET.match(h, k).end()
                wert = h[vs:k]
                j = k
        if attr not in attrs:
            attrs[attr] = wert


def _ende_von(h: str, name: str, ab: int) -> int:
    """Erste Stelle von ``</name`` (Gross/Klein egal, nur ASCII) mit folgendem Leerraum, ``/`` oder ``>``; sonst -1."""
    laenge = len(name)
    p = h.find("</", ab)
    while p != -1:
        teil = h[p + 2:p + 2 + laenge]
        folgt = p + 2 + laenge
        if teil.isascii() and teil.lower() == name and folgt < len(h) and h[folgt] in _ENDE_FOLGT:
            return p
        p = h.find("</", p + 1)
    return -1


def _ist_datenblock(name: str, attrs: dict[str, str]) -> bool:
    if name != "script" or attrs.get("id") != KONZEPT_BLOCK_ID:
        return False
    return js_trim(attrs.get("type", "").split(";")[0]).lower() == "application/json"


def _block_finden(html: str) -> str | None:
    """``findBlock``: Rohtext des ersten Datenblocks in Dokumentreihenfolge, sonst None."""
    h = _begrenzt(html)
    n = len(h)
    i = 0
    while i < n:
        m = _TAG_ANFANG.search(h, i)            # ein einzelnes "<" ohne Folgezeichen ist Text
        if m is None:
            return None
        lt = m.start()
        c = h[lt + 1]
        if h.startswith("<!--", lt):
            e = h.find("-->", lt + 4)
            if e == -1:
                return None
            i = e + 3
        elif c in "!?":
            e = h.find(">", lt + 2)
            if e == -1:
                return None
            i = e + 1
        else:
            t = _tag(h, lt)
            if t is None:
                return None
            schliessend, name, attrs, ende = t
            i = ende
            if schliessend or name not in ("script", "style"):
                continue
            e = _ende_von(h, name, ende)
            koerper = h[ende:] if e == -1 else h[ende:e]
            if _ist_datenblock(name, attrs):
                return koerper
            if e == -1:
                return None
            e = h.find(">", e)
            if e == -1:
                return None
            i = e + 1
    return None


# --- JSON.parse ohne Rekursion --------------------------------------------------------------------------

_JSON_WS = re.compile(r"[ \t\n\r]*")
_ZAHL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_TEXT_LAUF = re.compile(r'[^"\\\x00-\x1f]*')
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _zahl(text: str):
    wert = float(text)                       # korrekt gerundet wie Number(); 1e400 -> inf wie in JavaScript
    return int(wert) if wert.is_integer() else wert


def _text(s: str, i: int) -> tuple[str, int]:
    """JSON-Zeichenkette ab ``s[i] == '"'``; Surrogat-Escapes werden wie in UTF-16 zu Paaren zusammengefuegt."""
    if i >= len(s) or s[i] != '"':
        raise ValueError("Zeichenkette erwartet")
    teile = []
    surrogat = False
    j = i + 1
    while True:
        m = _TEXT_LAUF.match(s, j)
        teile.append(m.group())
        j = m.end()
        if j >= len(s):
            raise ValueError("Zeichenkette ohne Ende")
        z = s[j]
        if z == '"':
            text = "".join(teile)
            if surrogat:
                text = text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
            return text, j + 1
        if z != "\\":
            raise ValueError("rohes Steuerzeichen")
        e = s[j + 1] if j + 1 < len(s) else ""
        if e == "u":
            h = _HEX4.match(s, j + 2)
            if h is None:
                raise ValueError("ungueltiges \\u")
            cp = int(h.group(), 16)
            surrogat = surrogat or 0xD800 <= cp <= 0xDFFF
            teile.append(chr(cp))
            j = h.end()
        elif e in _ESCAPES:
            teile.append(_ESCAPES[e])
            j += 2
        else:
            raise ValueError("ungueltiges Escape")


def _json_parse(s: str):
    """Wie ``JSON.parse``; ValueError bei ungueltigem JSON."""
    n = len(s)
    i = _JSON_WS.match(s, 0).end()
    stapel: list[list] = []                  # [Behaelter, Schluessel] je offener Ebene
    while True:
        # einen Wert ab i lesen
        if i >= n:
            raise ValueError("Wert erwartet")
        c = s[i]
        if c == "{":
            i = _JSON_WS.match(s, i + 1).end()
            if i < n and s[i] == "}":
                wert, i = {}, i + 1
            else:
                schluessel, i = _text(s, i)
                i = _JSON_WS.match(s, i).end()
                if i >= n or s[i] != ":":
                    raise ValueError(": erwartet")
                i = _JSON_WS.match(s, i + 1).end()
                stapel.append([{}, schluessel])
                continue
        elif c == "[":
            i = _JSON_WS.match(s, i + 1).end()
            if i < n and s[i] == "]":
                wert, i = [], i + 1
            else:
                stapel.append([[], None])
                continue
        elif c == '"':
            wert, i = _text(s, i)
        elif c == "-" or "0" <= c <= "9":
            m = _ZAHL.match(s, i)
            if m is None:
                raise ValueError("ungueltige Zahl")
            wert, i = _zahl(m.group()), m.end()
        elif s.startswith("true", i):
            wert, i = True, i + 4
        elif s.startswith("false", i):
            wert, i = False, i + 5
        elif s.startswith("null", i):
            wert, i = None, i + 4
        else:
            raise ValueError("unerwartetes Zeichen")
        # den Wert einhaengen und fertige Ebenen schliessen
        while True:
            if not stapel:
                i = _JSON_WS.match(s, i).end()
                if i != n:
                    raise ValueError("Text nach dem Wert")
                return wert
            ebene = stapel[-1]
            behaelter = ebene[0]
            ist_objekt = isinstance(behaelter, dict)
            if ist_objekt:
                behaelter[ebene[1]] = wert
            else:
                behaelter.append(wert)
            i = _JSON_WS.match(s, i).end()
            if i >= n:
                raise ValueError("Ende erwartet")
            z = s[i]
            if z == ",":
                i = _JSON_WS.match(s, i + 1).end()
                if ist_objekt:
                    schluessel, i = _text(s, i)
                    i = _JSON_WS.match(s, i).end()
                    if i >= n or s[i] != ":":
                        raise ValueError(": erwartet")
                    i = _JSON_WS.match(s, i + 1).end()
                    ebene[1] = schluessel
                break
            if z == ("}" if ist_objekt else "]"):
                i += 1
                wert = behaelter
                stapel.pop()
                continue
            raise ValueError(", oder Ende erwartet")


# --- readKonzept / checkVideoKonzept ----------------------------------------------------------------------

def lesen(html: str) -> tuple[dict | None, str | None]:
    """``readKonzept``: (daten, None), (None, Fehlersatz) oder (None, None), wenn kein Datenblock da ist."""
    roh = _block_finden(html)
    if roh is None:
        return None, None
    if len(roh) > BLOCK_MAX or utf16_laenge(roh) > BLOCK_MAX:
        return None, f"Der Datenblock ist l\u00e4nger als {BLOCK_MAX} Zeichen und wird ignoriert."
    try:
        geparst = _json_parse(roh)
    except ValueError:
        return None, "Der Datenblock enth\u00e4lt kein g\u00fcltiges JSON und wird ignoriert."
    if not isinstance(geparst, dict):
        return None, "Der Datenblock muss ein JSON-Objekt sein und wird ignoriert."
    art = geparst.get("art")
    if not isinstance(art, str) or art not in ("konzept", "videokonzept"):
        return None, 'Im Datenblock fehlt "art" mit dem Wert "konzept" oder "videokonzept".'
    bezuege = geparst.get("bezuege")
    if not isinstance(bezuege, list) or not all(isinstance(b, str) for b in bezuege):
        return None, 'Im Datenblock muss "bezuege" eine Liste von Zeichenketten sein.'
    daten: dict = {"art": art, "bezuege": bezuege}
    if art == "videokonzept":
        if not isinstance(geparst.get("video"), dict):
            return None, 'Beim Videokonzept fehlt im Datenblock das Objekt "video".'
        daten["video"] = geparst["video"]
    return daten, None


def _ist_zahl(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text_laenge(v, kleinst: int, groesst: float) -> bool:
    return isinstance(v, str) and utf16_laenge(js_trim(v)) >= kleinst and utf16_laenge(v) <= groesst


def _ist_auftragsnummer(v) -> bool:
    return isinstance(v, str) and len(v) == 8 and all(z in "0123456789abcdef" for z in v)


def _video_fehler(v) -> list[str]:
    """Alle Saetze von ``checkVideoKonzept`` in fester Reihenfolge (Teil des Vertrags mit dem Fork)."""
    if not isinstance(v, dict):
        return ["Das Videokonzept fehlt oder ist kein Objekt."]
    unendlich = float("inf")
    f: list[str] = []
    if not _text_laenge(v.get("thema"), 3, 300):
        f.append("Das Thema muss 3 bis 300 Zeichen lang sein.")
    if not _text_laenge(v.get("zielgruppe"), 1, unendlich):
        f.append("Die Zielgruppe fehlt.")
    elif utf16_laenge(v["zielgruppe"]) > 300:
        f.append("Die Zielgruppe darf h\u00f6chstens 300 Zeichen lang sein.")
    minuten = v.get("minuten")
    if not _ist_zahl(minuten) or not 3 <= minuten <= 5:
        f.append("Die L\u00e4nge muss zwischen 3 und 5 Minuten liegen.")
    elif not (isinstance(minuten, int) or minuten.is_integer()):
        f.append("Die L\u00e4nge muss eine ganze Zahl von Minuten sein.")
    if not _text_laenge(v.get("hook"), 1, unendlich):
        f.append("Der Hook fehlt.")
    kern_ok = _text_laenge(v.get("kernaussage"), 1, unendlich)
    if not kern_ok:
        f.append("Die Kernaussage fehlt.")
    abschluss_ok = _text_laenge(v.get("abschluss"), 1, unendlich)
    if not abschluss_ok:
        f.append("Der Abschluss fehlt.")
    if kern_ok and abschluss_ok and (
            utf16_laenge(js_trim(v["kernaussage"])) + 2 + utf16_laenge(js_trim(v["abschluss"])) > 4000):   # trim + "\n\n" + trim
        f.append("Kernaussage und Abschluss d\u00fcrfen zusammen h\u00f6chstens 4000 Zeichen lang sein.")
    if "auftrag" not in v or (v["auftrag"] is not None and not _ist_auftragsnummer(v["auftrag"])):
        f.append("Der Auftrag muss leer (null) oder eine Nummer aus genau 8 Zeichen 0-9 und a-f (klein) sein.")
    if "firma" in v and not isinstance(v["firma"], str):
        f.append("Die Firma muss ein Text sein.")
    stufen = v.get("stufen")
    if not isinstance(stufen, list):
        f.append("Die Stufen fehlen oder sind keine Liste.")
        return f
    if len(stufen) > 8:
        f.append("Es sind h\u00f6chstens 8 Stufen erlaubt.")
    szenen_gesamt = 0
    stufen_ok = kurz_ok = bildart_ok = notiz_ok = True
    for s in stufen:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not isinstance(s.get("szenen"), list):
            stufen_ok = False
            continue
        for sz in s["szenen"]:
            szenen_gesamt += 1
            if not isinstance(sz, dict):
                kurz_ok = bildart_ok = False
                continue
            kurz = sz.get("kurz")
            if not isinstance(kurz, str) or utf16_laenge(kurz) > 300:
                kurz_ok = False
            bildart = sz.get("bildart")
            if not isinstance(bildart, str) or bildart not in BILDARTEN:
                bildart_ok = False
            if "notiz" in sz and not isinstance(sz["notiz"], str):
                notiz_ok = False
    if not stufen_ok:
        f.append("Jede Stufe braucht einen Namen und eine Liste von Szenen.")
    if szenen_gesamt < 8 or szenen_gesamt > 15:
        f.append(f"Es sind insgesamt 8 bis 15 Szenen n\u00f6tig, gefunden wurden {szenen_gesamt}.")
    if not kurz_ok:
        f.append("Jede Szene braucht eine Kurzbeschreibung mit h\u00f6chstens 300 Zeichen.")
    if not bildart_ok:
        f.append("Die Bildart jeder Szene muss slide, avatar_slide, avatar_gross oder clip sein.")
    if not notiz_ok:
        f.append("Eine Notiz muss ein Text sein.")
    return f


def video_pruefen_objekt(video) -> str | None:
    """Erster Satz von ``checkVideoKonzept(video)`` oder None, wenn das Videokonzept gueltig ist."""
    fehler = _video_fehler(video)
    return fehler[0] if fehler else None


def video_pruefen(konzept: dict) -> str | None:
    """Erster Satz von ``checkVideoKonzept(daten.video)`` fuer das Ergebnis von ``lesen`` (bei ``art: "konzept"``
    fehlt ``video``: "Das Videokonzept fehlt ...")."""
    return video_pruefen_objekt(konzept.get("video") if isinstance(konzept, dict) else None)
