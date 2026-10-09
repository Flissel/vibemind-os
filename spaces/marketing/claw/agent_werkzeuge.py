"""Werkzeuge des Gestaltungs-Agenten (sales-claw Spec 2026-10-02-newsletter-gestaltung-und-agent-design.md §4.2).
Der Agent antwortet mit einer Liste von Aenderungen; dieses Modul prueft jede streng und wendet sie auf eine
Kopie des Blockdokuments an. Rein: keine Dienste, kein Dateizugriff."""
from __future__ import annotations

import copy
import re
import secrets
from dataclasses import dataclass, field

from spaces.marketing.claw import bildplaetze, gestaltung, schriften

MAX_AENDERUNGEN = 40
MAX_SCHRITT = 80
NEU = re.compile(r"^neu:([1-9][0-9]*)$")
_FARBE = re.compile(r"^#[0-9a-fA-F]{6}$")
EINFUEGBAR = ("Heading", "Text", "Button", "Image", "Divider", "Spacer")
FARB_ROLLEN = ("backdropColor", "canvasColor", "textColor")
FLAECHE_GESPERRT = ("gestaltung", "url", "width", "height")
STRUKTUR_PROPS = ("childrenIds", "columns")
EBENE_FELDER = {
    "bild": {"id", "art", "x", "y", "drehung", "quelle", "breite"},
    "text": {"id", "art", "x", "y", "drehung", "text", "schrift", "gewicht", "kursiv", "groesse", "farbe",
             "ausrichtung", "zeilenabstand"},
}

WERKZEUGE: tuple[str, ...] = (
    "block_einfuegen", "block_aendern", "block_verschieben", "block_loeschen", "farben_setzen",
    "schriften_setzen", "flaeche_anlegen", "ebene_hinzufuegen", "ebene_aendern", "ebene_reihenfolge", "ebene_loeschen",
    "format_setzen", "hintergrund_setzen", "bild_erzeugen", "bild_freistellen", "bild_aus_medien",
    "entwurf_speichern", "export_vorschlagen")

# werkzeug -> (Pflichtparameter, optionale Parameter)
PARAMETER: dict[str, tuple[set, set]] = {
    "block_einfuegen": ({"typ", "nach"}, {"daten"}),
    "block_aendern": ({"id"}, {"props", "style"}),
    "block_verschieben": ({"id", "nach"}, set()),
    "block_loeschen": ({"id"}, set()),
    "farben_setzen": (set(), set(FARB_ROLLEN)),
    "schriften_setzen": (set(), {"anzeige", "text"}),
    "flaeche_anlegen": ({"nach", "format", "hintergrund", "alt"}, set()),
    "ebene_hinzufuegen": ({"flaeche", "ebene"}, set()),
    "ebene_aendern": ({"flaeche", "id", "felder"}, set()),
    "ebene_reihenfolge": ({"flaeche", "ids"}, set()),
    "ebene_loeschen": ({"flaeche", "id"}, set()),
    "format_setzen": ({"flaeche", "format"}, set()),
    "hintergrund_setzen": ({"flaeche", "farbe"}, set()),
    "bild_erzeugen": ({"platz", "hinweis"}, set()),
    "bild_freistellen": ({"platz"}, set()),
    "bild_aus_medien": ({"platz", "quelle"}, set()),
    "entwurf_speichern": ({"notiz"}, set()),
    "export_vorschlagen": ({"newsletter", "flaechen"}, set()),
}

# jede Aenderung darf ein "schritt" tragen (Anzeigetext im Live-Editor); anwenden ignoriert ihn
PARAMETER = {n: (pf, op | {"schritt"}) for n, (pf, op) in PARAMETER.items()}


class WerkzeugFehler(ValueError):
    pass


@dataclass
class Ergebnis:
    bloecke: dict
    geaendert: bool
    bildauftraege: list[dict] = field(default_factory=list)
    export_vorschlag: dict | None = None
    notiz: str = ""


class _Lauf:
    """Zustand eines Anwendens: Arbeitskopie, neu angelegte Flaechen, aufgeschobene Pruefungen."""

    def __init__(self, dok: dict, medien: set[str]):
        self.b = copy.deepcopy(dok)
        self.medien = set(medien)
        self.neu: list[str] = []
        self.bild: list[tuple[str, dict]] = []
        self.export: dict | None = None
        self.notiz: str | None = None

    # ---- Hilfen -------------------------------------------------------------------------------
    def neue_id(self, praefix: str) -> str:
        while True:
            i = f"{praefix}-{secrets.token_hex(3)}"
            if i not in self.b:
                return i

    def ref(self, wert, name: str, *, mit_neu: bool = True) -> str:
        if not isinstance(wert, str) or not wert:
            raise WerkzeugFehler(f"{name} muss eine Block-id sein")
        m = NEU.match(wert)
        if m:
            if not mit_neu:
                raise WerkzeugFehler(f"{name}: neu:<n> ist hier nicht erlaubt")
            n = int(m.group(1))
            if n > len(self.neu):
                raise WerkzeugFehler(f"{wert} verweist auf keine bisher angelegte Fläche")
            if self.neu[n - 1] not in self.b:
                raise WerkzeugFehler(f"{wert} wurde inzwischen gelöscht")
            return self.neu[n - 1]
        if wert not in self.b:
            raise WerkzeugFehler(f"Block {wert} gibt es nicht")
        return wert

    def block(self, wert, name: str) -> str:
        bid = self.ref(wert, name)
        if bid == "root":
            raise WerkzeugFehler("root ist hier nicht erlaubt")
        return bid

    def flaeche(self, wert) -> str:
        bid = self.ref(wert, "flaeche")
        if not self.ist_flaeche(bid):
            raise WerkzeugFehler(f"{bid} ist keine Fläche")
        return bid

    def ist_flaeche(self, bid: str) -> bool:
        b = self.b.get(bid)
        return (isinstance(b, dict) and b.get("type") == "Image"
                and isinstance(((b.get("data") or {}).get("props") or {}).get("gestaltung"), dict))

    def props(self, bid: str) -> dict:
        data = self.b[bid].setdefault("data", {})
        return data.setdefault("props", {})

    def kinderlisten(self, bid: str) -> list[list]:
        b = self.b.get(bid)
        if not isinstance(b, dict):
            return []
        data = b.get("data") or {}
        if bid == "root":
            return [data["childrenIds"]] if isinstance(data.get("childrenIds"), list) else []
        p = data.get("props") or {}
        if b.get("type") == "Container" and isinstance(p.get("childrenIds"), list):
            return [p["childrenIds"]]
        if b.get("type") == "ColumnsContainer" and isinstance(p.get("columns"), list):
            return [c["childrenIds"] for c in p["columns"]
                    if isinstance(c, dict) and isinstance(c.get("childrenIds"), list)]
        return []

    def eltern_liste(self, bid: str) -> list:
        for kandidat in self.b:
            for liste in self.kinderlisten(kandidat):
                if bid in liste:
                    return liste
        raise WerkzeugFehler(f"Block {bid} hängt an keiner Stelle des Newsletters")

    def nachfahren(self, bid: str) -> list[str]:
        aus, stapel = [], [bid]
        while stapel:
            for liste in self.kinderlisten(stapel.pop()):
                for k in liste:
                    if k in self.b and k not in aus:
                        aus.append(k)
                        stapel.append(k)
        return aus

    def einsetzen(self, bid: str, nach) -> None:
        if nach is None:
            self.b["root"]["data"]["childrenIds"].append(bid)
            return
        ziel = self.ref(nach, "nach")
        if ziel == "root":
            raise WerkzeugFehler("nach darf nicht root sein (null = ans Ende)")
        liste = self.eltern_liste(ziel)
        liste.insert(liste.index(ziel) + 1, bid)

    def quelle(self, wert) -> str:
        if not isinstance(wert, str) or not wert:
            raise WerkzeugFehler("quelle muss ein Medienname sein")
        name = wert[len("medien:"):] if wert.startswith("medien:") else wert
        if name not in self.medien:
            raise WerkzeugFehler(f"Bild {name} gibt es nicht in den Medien")
        return f"medien:{name}"

    def gestaltung_schreiben(self, fid: str, g: dict) -> None:
        try:
            g = gestaltung.pruefen(g)
        except gestaltung.GestaltungFehler as e:
            raise WerkzeugFehler(str(e)) from None
        p = self.props(fid)
        p["gestaltung"] = g
        p["height"] = gestaltung.hoehe(g["format"])

    def ebene_pruefen(self, e: dict) -> dict:
        art = e.get("art")
        if art not in EBENE_FELDER:
            raise WerkzeugFehler("art der Ebene muss bild oder text sein")
        fremd = sorted(set(e) - EBENE_FELDER[art])
        if fremd:
            raise WerkzeugFehler(f"Unbekannte Felder in der Ebene: {', '.join(fremd)}")
        if art == "bild" and "quelle" in e:
            e = {**e, "quelle": self.quelle(e["quelle"])}
        return e

    # ---- Werkzeuge ----------------------------------------------------------------------------
    def block_einfuegen(self, a: dict) -> None:
        if a["typ"] not in EINFUEGBAR:
            raise WerkzeugFehler(f"typ muss einer von {', '.join(EINFUEGBAR)} sein")
        daten = a.get("daten", {})
        _nur_keys(daten, {"style", "props"}, "daten")
        style, props = daten.get("style", {}), daten.get("props", {})
        if not isinstance(style, dict) or not isinstance(props, dict):
            raise WerkzeugFehler("daten.style und daten.props müssen Objekte sein")
        for k in ("gestaltung",) + STRUKTUR_PROPS:
            if k in props:
                raise WerkzeugFehler(f"props.{k} kann nicht gesetzt werden (Flächen nur mit flaeche_anlegen)")
        bid = self.neue_id("agent")
        self.einsetzen(bid, a["nach"])
        self.b[bid] = {"type": a["typ"], "data": {"style": copy.deepcopy(style), "props": copy.deepcopy(props)}}

    def block_aendern(self, a: dict) -> None:
        bid = self.ref(a["id"], "id")
        if bid == "root":
            raise WerkzeugFehler("root nur über farben_setzen und schriften_setzen ändern")
        props, style = a.get("props", {}), a.get("style", {})
        if not isinstance(props, dict) or not isinstance(style, dict):
            raise WerkzeugFehler("props und style müssen Objekte sein")
        if not props and not style:
            raise WerkzeugFehler("props oder style mit mindestens einem Eintrag fehlt")
        if "type" in props or "type" in style:
            raise WerkzeugFehler("type ist unveränderlich")
        if self.ist_flaeche(bid) and any(k in props for k in FLAECHE_GESPERRT):
            raise WerkzeugFehler("Fläche nur mit den Flächen-Werkzeugen ändern")
        if "gestaltung" in props:
            raise WerkzeugFehler("props.gestaltung nur mit flaeche_anlegen und den Flächen-Werkzeugen")
        for k in STRUKTUR_PROPS:
            if k in props:
                raise WerkzeugFehler(f"props.{k} nur mit block_einfuegen, block_verschieben, block_loeschen ändern")
        data = self.b[bid].setdefault("data", {})
        data.setdefault("style", {}).update(copy.deepcopy(style))
        data.setdefault("props", {}).update(copy.deepcopy(props))

    def block_verschieben(self, a: dict) -> None:
        bid = self.block(a["id"], "id")
        ziel = None if a["nach"] is None else self.ref(a["nach"], "nach")
        if ziel is not None and (ziel == bid or ziel in self.nachfahren(bid)):
            raise WerkzeugFehler("Ein Block kann nicht hinter sich selbst oder seine Nachfahren")
        self.eltern_liste(bid).remove(bid)
        self.einsetzen(bid, ziel)

    def block_loeschen(self, a: dict) -> None:
        bid = self.block(a["id"], "id")
        self.eltern_liste(bid).remove(bid)
        for k in [bid] + self.nachfahren(bid):
            self.b.pop(k, None)

    def farben_setzen(self, a: dict) -> None:
        farben = {k: v for k, v in a.items() if k not in ("werkzeug", "schritt")}
        if not farben:
            raise WerkzeugFehler("mindestens eine Farbe (backdropColor, canvasColor, textColor)")
        for k, v in farben.items():
            if not isinstance(v, str) or not _FARBE.fullmatch(v):
                raise WerkzeugFehler(f"{k} muss #RRGGBB sein")
        self.b["root"]["data"].update(farben)

    def schriften_setzen(self, a: dict) -> None:
        neu = {k: a[k] for k in ("anzeige", "text") if k in a}
        if not neu:
            raise WerkzeugFehler("mindestens anzeige oder text")
        for k, v in neu.items():
            if not isinstance(v, str) or v not in schriften.REGISTER:
                raise WerkzeugFehler(f"{k}: unbekannte Schrift {v}")
        alt = self.b["root"]["data"].get("schriften")
        paar = {**(alt if isinstance(alt, dict) else {}), **neu}
        if not all(isinstance(paar.get(k), str) for k in ("anzeige", "text")):
            raise WerkzeugFehler("Schriftpaar braucht anzeige und text")
        self.b["root"]["data"]["schriften"] = {"anzeige": paar["anzeige"], "text": paar["text"]}

    def flaeche_anlegen(self, a: dict) -> None:
        if a["format"] not in gestaltung.FORMATE:
            raise WerkzeugFehler("format muss quer, quadrat, hoch oder banner sein")
        alt = a["alt"]
        if not isinstance(alt, str) or not alt.strip() or len(alt) > 200:
            raise WerkzeugFehler("alt ist Pflicht (höchstens 200 Zeichen)")
        try:
            g = gestaltung.pruefen({"version": 1, "format": a["format"], "hintergrund": a["hintergrund"], "ebenen": []})
        except gestaltung.GestaltungFehler as e:
            raise WerkzeugFehler(str(e)) from None
        bid = self.neue_id("agent")
        self.einsetzen(bid, a["nach"])
        self.b[bid] = {"type": "Image", "data": {
            "style": {"padding": {"top": 16, "bottom": 16, "left": 24, "right": 24}},
            "props": {"url": None, "alt": alt, "width": gestaltung.BREITE, "height": gestaltung.hoehe(g["format"]),
                      "contentAlignment": "middle", "linkHref": None, "gestaltung": g}}}
        self.neu.append(bid)

    def ebene_hinzufuegen(self, a: dict) -> None:
        fid = self.flaeche(a["flaeche"])
        e = a["ebene"]
        if not isinstance(e, dict):
            raise WerkzeugFehler("ebene muss ein Objekt sein")
        g = copy.deepcopy(self.props(fid)["gestaltung"])
        if "id" not in e:
            vorhanden = {x.get("id") for x in g["ebenen"]}
            while True:
                eid = f"e-{secrets.token_hex(3)}"
                if eid not in vorhanden:
                    break
            e = {**e, "id": eid}
        g["ebenen"].append(self.ebene_pruefen(e))
        self.gestaltung_schreiben(fid, g)

    def ebene_aendern(self, a: dict) -> None:
        fid = self.flaeche(a["flaeche"])
        felder = a["felder"]
        if not isinstance(felder, dict) or not felder:
            raise WerkzeugFehler("felder muss ein Objekt mit mindestens einem Eintrag sein")
        if "id" in felder or "art" in felder:
            raise WerkzeugFehler("id und art einer Ebene sind unveränderlich")
        g = copy.deepcopy(self.props(fid)["gestaltung"])
        for i, e in enumerate(g["ebenen"]):
            if e["id"] == a["id"]:
                g["ebenen"][i] = self.ebene_pruefen({**e, **felder})
                break
        else:
            raise WerkzeugFehler(f"Ebene {a['id']} gibt es nicht")
        self.gestaltung_schreiben(fid, g)

    def ebene_reihenfolge(self, a: dict) -> None:
        fid = self.flaeche(a["flaeche"])
        ids = a["ids"]
        g = copy.deepcopy(self.props(fid)["gestaltung"])
        if (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
                or sorted(ids) != sorted(e["id"] for e in g["ebenen"])):
            raise WerkzeugFehler("ids muss genau alle Ebenen-ids der Fläche je einmal enthalten (unten → oben)")
        nach_id = {e["id"]: e for e in g["ebenen"]}
        g["ebenen"] = [nach_id[i] for i in ids]
        self.gestaltung_schreiben(fid, g)

    def ebene_loeschen(self, a: dict) -> None:
        fid = self.flaeche(a["flaeche"])
        g = copy.deepcopy(self.props(fid)["gestaltung"])
        rest = [e for e in g["ebenen"] if e["id"] != a["id"]]
        if len(rest) == len(g["ebenen"]):
            raise WerkzeugFehler(f"Ebene {a['id']} gibt es nicht")
        g["ebenen"] = rest
        self.gestaltung_schreiben(fid, g)

    def format_setzen(self, a: dict) -> None:
        fid = self.flaeche(a["flaeche"])
        if a["format"] not in gestaltung.FORMATE:
            raise WerkzeugFehler("format muss quer, quadrat, hoch oder banner sein")
        g = copy.deepcopy(self.props(fid)["gestaltung"])
        g["format"] = a["format"]
        self.gestaltung_schreiben(fid, g)

    def hintergrund_setzen(self, a: dict) -> None:
        fid = self.flaeche(a["flaeche"])
        g = copy.deepcopy(self.props(fid)["gestaltung"])
        g["hintergrund"] = a["farbe"]
        self.gestaltung_schreiben(fid, g)

    # Bild- und Abschlusswerkzeuge wirken auf den Endstand der Bloecke -> aufgeschoben
    def bild_erzeugen(self, a: dict) -> None:
        if not isinstance(a["hinweis"], str) or len(a["hinweis"]) > 500:
            raise WerkzeugFehler("hinweis muss Text sein (höchstens 500 Zeichen)")
        self.bild.append(("bild_erzeugen", a))

    def bild_freistellen(self, a: dict) -> None:
        self.bild.append(("bild_freistellen", a))

    def bild_aus_medien(self, a: dict) -> None:
        a = {**a, "quelle": self.quelle(a["quelle"])}
        self.bild.append(("bild_aus_medien", a))

    def entwurf_speichern(self, a: dict) -> None:
        if self.notiz is not None:
            raise WerkzeugFehler("nur einmal je Antwort")
        n = a["notiz"]
        if not isinstance(n, str) or len(n) > 200:
            raise WerkzeugFehler("notiz muss Text sein (höchstens 200 Zeichen)")
        self.notiz = n

    def export_vorschlagen(self, a: dict) -> None:
        if self.export is not None:
            raise WerkzeugFehler("nur einmal je Antwort")
        if not isinstance(a["newsletter"], bool):
            raise WerkzeugFehler("newsletter muss true oder false sein")
        fl = a["flaechen"]
        if not isinstance(fl, list) or not all(isinstance(x, str) for x in fl):
            raise WerkzeugFehler("flaechen muss eine Liste aus Flächen-ids oder \"alle\" sein")
        self.export = {"newsletter": a["newsletter"], "flaechen": [x if x == "alle" else self.flaeche(x) for x in fl]}

    # ---- Abschluss ----------------------------------------------------------------------------
    def reihenfolge(self) -> list[str]:
        aus: list[str] = []
        stapel = ["root"]
        while stapel:
            bid = stapel.pop()
            for liste in reversed(self.kinderlisten(bid)):
                for k in reversed(liste):
                    stapel.append(k)
            if bid != "root":
                aus.append(bid)
        return aus

    def abschliessen(self) -> tuple[list[dict], dict | None]:
        plaetze = {p.id for p in bildplaetze.finde(self.b)}
        auftraege: list[dict] = []
        for name, a in self.bild:
            pid = a["platz"]
            if not isinstance(pid, str) or pid not in plaetze:
                raise _Spaet(name, f"{pid} ist kein Bildplatz")
            if name == "bild_aus_medien":
                self.props(pid)["url"] = a["quelle"]
            else:
                auftraege.append({"platz": pid, "modus": "neu" if name == "bild_erzeugen" else "freistellen",
                                  "hinweis": a.get("hinweis", "") if name == "bild_erzeugen" else ""})
        export = None
        if self.export is not None:
            alle = [b for b in self.reihenfolge() if self.ist_flaeche(b)]
            ids: list[str] = []
            for x in self.export["flaechen"]:
                for f in (alle if x == "alle" else [x]):
                    if f not in ids:
                        if not self.ist_flaeche(f):
                            raise _Spaet("export_vorschlagen", f"{f} ist keine Fläche")
                        ids.append(f)
            export = {"newsletter": self.export["newsletter"], "flaechen": ids}
        return auftraege, export


class _Spaet(Exception):
    def __init__(self, werkzeug: str, grund: str):
        self.werkzeug, self.grund = werkzeug, grund


def _nur_keys(wert, erlaubt: set, name: str) -> None:
    if not isinstance(wert, dict):
        raise WerkzeugFehler(f"{name} muss ein Objekt sein")
    fremd = sorted(set(wert) - erlaubt)
    if fremd:
        raise WerkzeugFehler(f"Unbekannte Parameter in {name}: {', '.join(fremd)}")


def _form_pruefen(a) -> str:
    if not isinstance(a, dict):
        raise WerkzeugFehler("Jede Änderung muss ein Objekt sein")
    name = a.get("werkzeug")
    if not isinstance(name, str) or name not in WERKZEUGE:
        raise WerkzeugFehler(f"{name if isinstance(name, str) else '?'}: unbekanntes Werkzeug")
    pflicht, optional = PARAMETER[name]
    schritt = a.get("schritt")
    if "schritt" in a and (not isinstance(schritt, str) or len(schritt) > MAX_SCHRITT):
        raise WerkzeugFehler(f"{name}: schritt höchstens {MAX_SCHRITT} Zeichen")
    given = set(a) - {"werkzeug", "schritt"}
    fehlt, fremd = sorted(pflicht - given), sorted(given - pflicht - optional)
    if fehlt:
        raise WerkzeugFehler(f"{name}: Parameter fehlt: {', '.join(fehlt)}")
    if fremd:
        raise WerkzeugFehler(f"{name}: Unbekannter Parameter: {', '.join(fremd)}")
    return name


def anwenden(dok: dict, aenderungen: list, medien: set[str]) -> Ergebnis:
    if not isinstance(aenderungen, list):
        raise WerkzeugFehler("Die Änderungen müssen eine Liste sein")
    if len(aenderungen) > MAX_AENDERUNGEN:
        raise WerkzeugFehler(f"Höchstens {MAX_AENDERUNGEN} Änderungen je Antwort")
    if not isinstance(dok, dict) or not isinstance((dok.get("root") or {}).get("data", {}).get("childrenIds"), list):
        raise WerkzeugFehler("Newsletter hat kein gültiges root")
    lauf = _Lauf(dok, medien)
    for a in aenderungen:
        name = _form_pruefen(a)
        try:
            getattr(lauf, name)(a)
        except WerkzeugFehler as e:
            raise WerkzeugFehler(f"{name}: {e}") from None
        except (KeyError, TypeError, AttributeError, ValueError):
            raise WerkzeugFehler(f"{name}: Parameter haben die falsche Form") from None
    try:
        auftraege, export = lauf.abschliessen()
    except _Spaet as e:
        raise WerkzeugFehler(f"{e.werkzeug}: {e.grund}") from None
    return Ergebnis(bloecke=lauf.b, geaendert=lauf.b != dok, bildauftraege=auftraege,
                    export_vorschlag=export, notiz=lauf.notiz or "")
