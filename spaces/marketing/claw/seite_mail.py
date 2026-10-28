"""Mail-Fassung einer Collage (freie Flaeche): MJML aus dem Streifen-Plan (seite_streifen), Textfassung und
Medienliste. Textbaender werden echter Mail-Text (mj-text/mj-button, mail-sicherer Schriftstapel, nie
Webschrift), Bildbaender werden zu Bildstuecken (mj-image, 600 breit), die seite_bilder rendert. Die Namen der
Stuecke kommen von aussen, in der Reihenfolge von stuecke(). Pflicht-Fuss, Platzhalter und mrml-Aufruf wie
bei den Bloecken (bloecke_mjml).

masse (optional, ueberall gleich): gemessene Huellen der gerenderten Ebenen (seite_bilder.render_und_messen).
Mit ihnen plant seite_streifen, Stuecke werden danach geschnitten, und Textbaender rechnen Abstaende und
Mindesthoehen mit der gerenderten statt der deklarierten Hoehe. stuecke() und mjml() muessen dieselben masse
bekommen."""
from __future__ import annotations

import html as _html
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from spaces.marketing.claw import bloecke_mjml, schriften, seite_html, seite_streifen, vorlagen_marke
from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw.bloecke_mjml import RenderFehler

ALT_MAX = 300
_ANREDE = "{vorname}"
_FUSS_TEXT = "#666666"
_AUSRICHTUNG = {"links": "left", "mitte": "center", "rechts": "right"}
MITTE_TOLERANZ = 4   # Einheiten: ein Knopf, dessen Mitte so nah an 300 liegt, gilt als mittig
VORSCHAU_MAX = 110   # Zeichen; so viel zeigen Mailprogramme neben dem Betreff


def vorschau_aus_seite(seite: dict) -> str:
    """Vorschautext, wenn keiner gesetzt ist (Newsletter-Runde 28.10.2026: er fehlte meist, und das Postfach
    zeigte dann den Anfang des Impressums). Der erste Satz des obersten Fliesstexts, ohne Anrede-Zeile und Links."""
    texte = sorted((e for e in seite.get("ebenen") or []
                    if isinstance(e, dict) and e.get("art") == "text" and e.get("rolle", "TEXT") == "TEXT"
                    and isinstance(e.get("text"), str) and e["text"].strip()),
                   key=lambda e: float(e.get("y", 0)) - float(e.get("hoehe", 0)) / 2)
    for e in texte:
        zeilen = [z.strip() for z in e["text"].splitlines() if z.strip()]
        if zeilen and _ANREDE in zeilen[0] and len(zeilen[0]) <= 40:
            zeilen = zeilen[1:]                        # „Hallo {vorname},“ gehoert nicht in die Vorschau
        text = " ".join(seite_html.TEXTLINK.sub(r"\1", " ".join(zeilen)).replace(_ANREDE, "").split())
        if not text:
            continue
        satz = next((text[:i + 1] for i, z in enumerate(text) if z in ".!?" and (i + 1 == len(text) or
                                                                                text[i + 1] == " ")), text)
        return satz if len(satz) <= VORSCHAU_MAX else satz[:VORSCHAU_MAX - 1].rsplit(" ", 1)[0] + "…"
    return ""


@dataclass
class Stueck:
    nr: int
    oben: float
    unten: float
    alt: str
    link: str | None


def _a(wert) -> str:
    return _html.escape(str(wert if wert is not None else ""), quote=True)


def _px(zahl: float) -> str:
    return f"{max(0, round(float(zahl), 2)):g}px"


def _pz(teil: float, ganz: float = seite_mod.BREITE) -> str:
    """Seitlicher Abstand/Breite in Prozent (von 600 bzw. vom Rest neben dem Polster): so schrumpfen Text- und
    Knopfbaender am Handy mit, statt in fester Summe von 600 Pixeln ueberzustehen."""
    return f"{max(0.0, round(float(teil) / float(ganz) * 100, 4)):g}%"


def _stapel(s: dict, rolle: str) -> str:
    """Mail-sicherer Schriftstapel der Rolle (nie die Webschrift)."""
    return schriften.REGISTER[seite_mod.rolle_schrift(s, rolle)]["stapel"]


def _schlicht(text: str) -> str:
    """Text ohne Auszeichnung; Links nur mit ihrer Beschriftung."""
    return bloecke_mjml.schlicht(seite_html.TEXTLINK.sub(lambda m: m.group(1), text))


def _vorbereitet(seite: dict, masse: seite_streifen.Masse | None = None
                 ) -> tuple[dict, list[seite_streifen.Band], dict[str, dict]]:
    s = seite_mod.pruefen(seite)
    return s, seite_streifen.plan(s, masse), {e["id"]: e for e in s["ebenen"]}


def _alt(ebenen: list[dict]) -> str:
    teile = [" ".join(_schlicht(e["text"]).split()) for e in ebenen if e["art"] in ("text", "knopf")]
    alt = " ".join(t for t in teile if t)
    if not alt:
        return ""            # schmueckendes Stueck: alt="" (Vorleseprogramme ueberspringen es)
    return alt if len(alt) <= ALT_MAX else alt[:ALT_MAX - 1].rstrip() + "…"


def _stuecke(baender: list[seite_streifen.Band], nach_id: dict[str, dict],
             masse: seite_streifen.Masse | None = None) -> list[Stueck]:
    aus: list[Stueck] = []
    for b in baender:
        if b.art != "bild":
            continue
        ebenen = seite_streifen.lesereihenfolge([nach_id[i] for i in b.ebenen], masse)
        link = next((e["link"] for e in ebenen if e["art"] == "knopf"), None)
        aus.append(Stueck(len(aus) + 1, b.oben, b.unten, _alt(ebenen), link))
    return aus


def stuecke(seite: dict, masse: seite_streifen.Masse | None = None) -> list[Stueck]:
    """Die Bildstuecke der Seite (ein Stueck je Bildband), von oben nach unten, nr ab 1."""
    masse = seite_streifen.masse_pruefen(masse) or None
    _, baender, nach_id = _vorbereitet(seite, masse)
    return _stuecke(baender, nach_id, masse)


def _im_bild(seite: dict, treffer: Callable[[dict], bool], masse: seite_streifen.Masse | None = None) -> list[str]:
    masse = seite_streifen.masse_pruefen(masse) or None
    _, baender, nach_id = _vorbereitet(seite, masse)
    ids: list[str] = []
    for b in baender:
        if b.art == "bild":
            ids += [i for i in b.ebenen if nach_id[i]["art"] in ("text", "knopf")
                    and treffer(nach_id[i]) and i not in ids]
    return ids


def anrede_in_bild(seite: dict, masse: seite_streifen.Masse | None = None) -> list[str]:
    """IDs der Text- und Knopf-Ebenen mit {vorname}, die in einem Bildband landen (dort ginge die Anrede
    verloren)."""
    return _im_bild(seite, lambda e: _ANREDE in e["text"], masse)


def links_in_bild(seite: dict, masse: seite_streifen.Masse | None = None) -> list[str]:
    """IDs der Textebenen mit einem Textlink [..](https://..), die in einem Bildband landen (im Bild waere der
    Link nicht anklickbar; Spec-Entscheidung 10). Knoepfe zaehlen nicht: ihr Link verlinkt das Stueck."""
    return _im_bild(seite, lambda e: e["art"] == "text" and bool(seite_html.TEXTLINK.search(e["text"])), masse)


def _knopf_lage(l: float, r: float) -> tuple[str, str, str, str]:
    """(align, padding-left, padding-right, width) eines Knopfs mit dem Rahmen l..r: ausgerichtet nach der Lage
    seiner Mitte, Polster und Breite in Prozent (Breite relativ zum Platz neben dem Polster)."""
    breite, mitte = r - l, (l + r) / 2
    if abs(mitte - seite_mod.BREITE / 2) <= MITTE_TOLERANZ:
        return "center", "0px", "0px", _pz(breite)
    if mitte < seite_mod.BREITE / 2:
        return "left", _pz(l), "0px", _pz(breite, seite_mod.BREITE - l)
    return "right", "0px", _pz(seite_mod.BREITE - r), _pz(breite, r)


def _ebene(s: dict, e: dict, oben: float, unten_abstand: float, hoehe: float | None = None) -> str:
    """hoehe: Mindesthoehe eines TEXTkastens (gemessen); ohne Messung die deklarierte hoehe. Fuer Knoepfe ohne
    Wirkung: mj-button bekommt immer die deklarierte hoehe (der Knopf waechst in seite_html nicht, er hat eine
    feste Hoehe)."""
    l, _, r, _ = seite_streifen.rahmen(e)
    senkrecht = f'padding-top="{_px(oben)}" padding-bottom="{_px(unten_abstand)}"'
    schrift = bloecke_mjml.attr_schrift(_stapel(s, e["rolle"]))
    if e["art"] == "knopf":
        align, links, rechts, breite = _knopf_lage(l, r)
        return (f'<mj-button {senkrecht} padding-left="{links}" padding-right="{rechts}" href="{_a(e["link"])}" '
                f'align="{align}" width="{breite}" '
                f'height="{_px(e["hoehe"])}" inner-padding="0px 12px" border-radius="{_px(e["rundung"])}" '
                f'background-color="{_a(e["farbe"])}" color="{_a(e["textfarbe"])}" font-family="{schrift}" '
                f'font-size="{_px(e["groesse"])}" font-weight="{e["gewicht"]}">{_a(e["text"])}</mj-button>')
    extra = ""
    if e["buchstabenabstand"]:
        extra += f' letter-spacing="{e["buchstabenabstand"]:g}em"'
    if e["grossbuchstaben"]:
        extra += ' text-transform="uppercase"'
    if e["kursiv"]:
        extra += ' font-style="italic"'
    # Die deklarierte Kastenhoehe ist nur MINDESThoehe (min-height aus der geprueften Zahl): laeuft der Text in
    # der Ersatzschrift laenger, schiebt er die folgenden Elemente nach unten statt ueber sie zu laufen. Outlook
    # (Word) kennt min-height nicht - dort entfaellt nur der Leerraum unter kurzem Text.
    polster = f'{senkrecht} padding-left="{_pz(l)}" padding-right="{_pz(seite_mod.BREITE - r)}"'
    inhalt = seite_html.inline(e["text"], link_farbe=e["farbe"])
    mindest = _px(e["hoehe"] if hoehe is None else hoehe)
    return (f'<mj-text {polster} align="{_AUSRICHTUNG[e["ausrichtung"]]}" '
            f'color="{_a(e["farbe"])}" '
            f'font-family="{schrift}" font-size="{_px(e["groesse"])}" font-weight="{e["gewicht"]}" '
            f'line-height="{e["zeilenabstand"]:g}"{extra}><div style="min-height:{mindest}">{inhalt}</div>'
            f'</mj-text>')


def _textband(s: dict, b: seite_streifen.Band, nach_id: dict[str, dict],
              masse: seite_streifen.Masse | None = None) -> str:
    farbe = _a(s["hintergrund"]["farbe"])
    ebenen = seite_streifen.lesereihenfolge([nach_id[i] for i in b.ebenen], masse)
    if not ebenen:
        inhalt = f'<mj-spacer height="{_px(b.unten - b.oben)}" />'
    else:
        # Invariante aus seite_streifen: Ebenen eines Textbands ueberlappen sich senkrecht nicht (sie stehen
        # untereinander), also ist o >= cur. Die max()-Klemmen sind nur Absicherung (Abstaende nie negativ).
        teile, cur = [], b.oben
        for n, e in enumerate(ebenen):
            _, o, _, u = seite_streifen.huelle(e, masse)
            gemessen = u - o if masse and e["id"] in masse else None
            o, u = max(o, b.oben), min(u, b.unten)
            rest = b.unten - max(cur, u) if n == len(ebenen) - 1 else 0
            teile.append(_ebene(s, e, o - cur, rest, gemessen))
            cur = max(cur, u)
        inhalt = "".join(teile)
    return f'<mj-section background-color="{farbe}" padding="0"><mj-column>{inhalt}</mj-column></mj-section>'


def _bildband(s: dict, st: Stueck, adresse: str) -> str:
    href = f' href="{_a(st.link)}"' if st.link else ""
    return (f'<mj-section background-color="{_a(s["hintergrund"]["farbe"])}" padding="0"><mj-column>'
            f'<mj-image src="{_a(adresse)}" width="600px" padding="0" alt="{_a(st.alt)}"{href} />'
            f'</mj-column></mj-section>')


def fuss(seite: dict, pflichtteil: dict, fuer_versand: bool) -> str:
    """Pflicht-Fuss der Collage-Mail (MJML-Section) in der Hintergrundfarbe der Seite."""
    s = seite_mod.pruefen(seite)
    return bloecke_mjml.pflicht_fuss(pflichtteil or {}, _FUSS_TEXT, s["hintergrund"]["farbe"], fuer_versand)


def mjml(seite: dict, stuecke_namen: list[str], *, betreff: str, vorschautext: str, pflichtteil: dict,
         bild_adresse: Callable[[str], str], fuer_versand: bool, fuss_ersatz: str | None = None,
         masse: seite_streifen.Masse | None = None) -> str:
    """fuer_versand: {vorname} und {abmeldelink} bleiben stehen (sales-claw setzt sie je Empfaenger ein),
    sonst [Vorname] und [Abmeldelink]. stuecke_namen[i] gehoert zu stuecke(seite)[i]. fuss_ersatz: steht statt
    des Pflicht-Fusses im MJML (eine Marke, die der Aufrufer spaeter durch den aktuellen Fuss ersetzt)."""
    masse = seite_streifen.masse_pruefen(masse) or None
    s, baender, nach_id = _vorbereitet(seite, masse)
    alle = _stuecke(baender, nach_id, masse)
    if len(stuecke_namen) != len(alle):
        raise RenderFehler(f"Die Mail-Fassung passt nicht zur Seite ({len(stuecke_namen)} Bildstücke "
                           f"statt {len(alle)}) – bitte neu erzeugen")
    rumpf, k = [], 0
    for b in baender:
        if b.art == "text":
            rumpf.append(_textband(s, b, nach_id, masse))
        else:
            rumpf.append(_bildband(s, alle[k], bild_adresse(stuecke_namen[k])))
            k += 1
    farbe = s["hintergrund"]["farbe"]
    fuss = (fuss_ersatz if fuss_ersatz is not None
            else bloecke_mjml.pflicht_fuss(pflichtteil or {}, _FUSS_TEXT, farbe, fuer_versand))
    schrift = bloecke_mjml.attr_schrift(_stapel(s, "TEXT"))
    # Farbschema wie bei den Block-Mails (dunkle Seite -> dark), helle Seiten ausdruecklich light: Mailprogramme
    # sollen weder die Textbaender noch die Bildstuecke eigenmaechtig umfaerben.
    schema = "dark" if vorlagen_marke.ist_dunkel(farbe) else "light"
    meta = (f'<mj-raw><meta name="color-scheme" content="{schema}"><meta name="supported-color-schemes" '
            f'content="{schema}"></mj-raw>')
    vorschau = (vorschautext or "").strip() or vorschau_aus_seite(s)
    quelle = (f'<mjml><mj-head><mj-title>{_a(betreff)}</mj-title><mj-preview>{_a(vorschau)}</mj-preview>{meta}'
              f'<mj-attributes><mj-all font-family="{schrift}" /></mj-attributes></mj-head>'
              f'<mj-body background-color="{_a(farbe)}" width="{seite_mod.BREITE}px">{"".join(rumpf)}{fuss}'
              f'</mj-body></mjml>')
    return quelle if fuer_versand else quelle.replace(_ANREDE, "[Vorname]")


def rendern(seite: dict, stuecke_namen: list[str], *, betreff: str, vorschautext: str, pflichtteil: dict,
            bild_adresse: Callable[[str], str], fuer_versand: bool) -> str:
    """MJML -> HTML ueber mrml (Fehler als RenderFehler wie bei den Bloecken)."""
    return bloecke_mjml.mjml_zu_html(mjml(seite, stuecke_namen, betreff=betreff, vorschautext=vorschautext,
                                          pflichtteil=pflichtteil, bild_adresse=bild_adresse,
                                          fuer_versand=fuer_versand))


def text_fassung(seite: dict, pflichtteil: dict, masse: seite_streifen.Masse | None = None) -> str:
    """text/plain-Teil: alle sichtbaren Text- und Knopf-Ebenen in Lesereihenfolge, Knoepfe als 'Text: url',
    dann der Pflichtteil. Platzhalter bleiben stehen. Mit masse dieselbe gemessene Lesereihenfolge wie die
    Alt-Texte der Stuecke."""
    masse = seite_streifen.masse_pruefen(masse) or None
    _, baender, nach_id = _vorbereitet(seite, masse)
    sichtbar = [nach_id[i] for b in baender for i in b.ebenen]
    gesehen: set[str] = set()
    teile: list[str] = []
    for e in seite_streifen.lesereihenfolge(sichtbar, masse):
        if e["id"] in gesehen or e["art"] not in ("text", "knopf"):
            continue
        gesehen.add(e["id"])
        if e["art"] == "knopf":
            teile.append(f"{bloecke_mjml.schlicht(e['text'])}: {e['link']}")
        elif bloecke_mjml.schlicht(e["text"]):
            teile.append(bloecke_mjml.schlicht(e["text"]))
    return bloecke_mjml.text_mit_fuss(teile, pflichtteil)


def versand_medien(stuecke_namen: list[str]) -> list[dict]:
    """Bildstuecke als eingebettete Bilder fuer den Versand (Form wie bloecke_mjml.versand_medien)."""
    return [{"name": n, "cid": urllib.parse.quote(n), "sw": False} for n in stuecke_namen]
