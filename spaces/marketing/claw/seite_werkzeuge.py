"""Werkzeuge des Assistenten fuer Collage-Seiten (format 'seite', Plan 2026-10-20-freie-flaeche-stufe3-assistent
Task 3). Das Modell antwortet mit einer Liste von Aenderungen; jede wird EINZELN auf eine Kopie der Seite angewendet
und mit seite.pruefen geprueft. Eine ungueltige Aenderung wird verworfen, ihr Grund kommt mit Nummer in die Hinweise
(das Modell sieht ihn in der naechsten Runde), alle uebrigen gelten. Gespeichert wird nur das normalisierte
Dokument. Bilder: bild_erzeugen/bild_aendern/bild_freistellen erzeugen nur Bildauftraege (Platz = Ebenen-ID, wie
bei Bloecken die Block-ID), bild_aus_medien/logo_einsetzen setzen die quelle sofort. Rein: keine Dienste, kein
Dateizugriff."""
from __future__ import annotations

import copy
import secrets
from dataclasses import dataclass, field

from spaces.marketing.claw import bildplaetze, seite
from spaces.marketing.claw.agent_werkzeuge import MAX_SCHRITT, WerkzeugFehler

MAX_AENDERUNGEN = 40
MAX_HINWEIS = 500
MAX_NOTIZ = 200
KEIN_KNOPF = "Kein Knopf auf der Seite"

WERKZEUGE: tuple[str, ...] = (
    "seite_hoehe", "seite_hintergrund", "schriften_setzen", "ebene_hinzufuegen", "ebene_aendern",
    "ebene_reihenfolge", "ebene_loeschen", "bild_erzeugen", "bild_aendern", "bild_freistellen",
    "bild_aus_medien", "logo_einsetzen", "fertig")

# werkzeug -> (Pflichtparameter, optionale Parameter); jede Aenderung darf zusaetzlich "schritt" tragen
PARAMETER: dict[str, tuple[set, set]] = {
    "seite_hoehe": ({"hoehe"}, set()),
    "seite_hintergrund": (set(), {"farbe", "bild"}),
    "schriften_setzen": (set(), {"anzeige", "text"}),
    "ebene_hinzufuegen": ({"ebene"}, {"nach"}),
    "ebene_aendern": ({"id", "felder"}, set()),
    "ebene_reihenfolge": ({"ids"}, set()),
    "ebene_loeschen": ({"id"}, set()),
    "bild_erzeugen": ({"ebene", "hinweis"}, {"vorlagen"}),
    "bild_aendern": ({"ebene", "anweisung"}, {"vorlagen"}),
    "bild_freistellen": ({"ebene"}, set()),
    "bild_aus_medien": ({"ebene", "quelle"}, set()),
    "logo_einsetzen": ({"ebene", "quelle"}, set()),
    "fertig": ({"notiz"}, set()),
}
PARAMETER = {n: (pf, op | {"schritt"}) for n, (pf, op) in PARAMETER.items()}

_MODUS = {"bild_erzeugen": "neu", "bild_aendern": "aendern", "bild_freistellen": "freistellen"}


@dataclass
class ErgebnisSeite:
    seite: dict
    geaendert: bool
    bildauftraege: list[dict] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)
    fertig: bool = False
    notiz: str = ""


class _Unbekannt(WerkzeugFehler):
    """Verweis auf eine Ebene, die es (nicht mehr) gibt - beim Nachspielen 'uebersprungen'."""

    def __init__(self, eid):
        super().__init__(f"Ebene {eid} gibt es nicht")
        self.eid = eid


class _Lauf:
    def __init__(self, s: dict, medien: set[str]):
        self.s = s                          # immer eine gueltige, normalisierte Seite
        self.medien = {m for m in medien if isinstance(m, str)}
        self.auftraege: list[tuple[int, str, dict]] = []   # (Nr., Werkzeug, Auftrag)

    # ---- Hilfen -------------------------------------------------------------------------------
    def nr(self, s: dict, eid) -> int:
        if isinstance(eid, str):
            for i, e in enumerate(s["ebenen"]):
                if e["id"] == eid:
                    return i
        raise _Unbekannt(eid)

    def quelle(self, wert) -> str:
        """Mediennamen mit oder ohne medien:; nur aus den Medien (oder ein Platzhalter = leerer Bildplatz)."""
        if not isinstance(wert, str) or not wert:
            raise WerkzeugFehler("quelle muss ein Medienname sein")
        name = wert[len("medien:"):] if wert.startswith("medien:") else wert
        if name not in self.medien and not bildplaetze.PLATZHALTER.match("medien:" + name):
            raise WerkzeugFehler(f"Bild {name} gibt es nicht in den Medien")
        return f"medien:{name}"

    def bild_ebene(self, s: dict, eid) -> dict:
        e = s["ebenen"][self.nr(s, eid)]
        if e["art"] != "bild":
            raise WerkzeugFehler(f"Ebene {eid} ist keine Bild-Ebene")
        return e

    def vorlagen(self, wert, hoechstens: int) -> list[str]:
        if wert is None:
            return []
        if not isinstance(wert, list) or not all(isinstance(n, str) for n in wert):
            raise WerkzeugFehler("vorlagen muss eine Liste von Mediennamen sein")
        namen = [self.quelle(n)[len("medien:"):] for n in wert]
        if len(set(namen)) != len(namen):
            raise WerkzeugFehler("vorlagen: ein Name ist doppelt")
        if len(namen) > hoechstens:
            raise WerkzeugFehler(f"vorlagen: höchstens {hoechstens}")
        return namen

    def neue_id(self, s: dict) -> str:
        vorhanden = {e["id"] for e in s["ebenen"]}
        while (eid := f"a-{secrets.token_hex(3)}") in vorhanden:
            pass
        return eid

    # ---- Seitenwerkzeuge: aendern eine Kopie, pruefen() entscheidet -----------------------------
    def seite_hoehe(self, s: dict, a: dict) -> None:
        s["hoehe"] = a["hoehe"]

    def seite_hintergrund(self, s: dict, a: dict) -> None:
        if "farbe" not in a and "bild" not in a:
            raise WerkzeugFehler("mindestens farbe oder bild")
        if "farbe" in a:
            s["hintergrund"]["farbe"] = a["farbe"]
        if "bild" in a:
            s["hintergrund"]["bild"] = None if a["bild"] is None else self.quelle(a["bild"])

    def schriften_setzen(self, s: dict, a: dict) -> None:
        if "anzeige" not in a and "text" not in a:
            raise WerkzeugFehler("mindestens anzeige oder text")
        for k in ("anzeige", "text"):
            if k in a:
                s["schriften"][k] = a[k]

    def ebene_hinzufuegen(self, s: dict, a: dict) -> None:
        e = a["ebene"]
        if not isinstance(e, dict):
            raise WerkzeugFehler("ebene muss ein Objekt sein")
        e = copy.deepcopy(e)
        if "id" not in e:
            e["id"] = self.neue_id(s)
        if e.get("art") == "bild":
            if "quelle" in e:
                e["quelle"] = self.quelle(e["quelle"])
            else:   # leerer Bildplatz, den bild_erzeugen fuellt
                b, h = e.get("breite"), e.get("hoehe")
                ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1 for v in (b, h))
                e["quelle"] = "medien:" + (bildplaetze.platzhalter_name(round(b), round(h)) if ok
                                           else "platzhalter-4x3.png")
        nach = a.get("nach")
        stelle = len(s["ebenen"]) if nach is None else self.nr(s, nach) + 1
        s["ebenen"].insert(stelle, e)

    def ebene_aendern(self, s: dict, a: dict) -> None:
        felder = a["felder"]
        if not isinstance(felder, dict) or not felder:
            raise WerkzeugFehler("felder muss ein Objekt mit mindestens einem Eintrag sein")
        if "id" in felder or "art" in felder:
            raise WerkzeugFehler("id und art einer Ebene sind unveränderlich")
        i = self.nr(s, a["id"])
        felder = copy.deepcopy(felder)
        if s["ebenen"][i]["art"] == "bild" and "quelle" in felder:
            felder["quelle"] = self.quelle(felder["quelle"])
        s["ebenen"][i] = {**s["ebenen"][i], **felder}

    def ebene_reihenfolge(self, s: dict, a: dict) -> None:
        ids = a["ids"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise WerkzeugFehler("ids muss eine Liste von Ebenen-IDs sein")
        nach_id = {e["id"]: e for e in s["ebenen"]}
        for i in ids:
            if i not in nach_id:
                raise _Unbekannt(i)
        if sorted(ids) != sorted(nach_id):
            raise WerkzeugFehler("ids muss genau alle Ebenen-IDs der Seite je einmal enthalten (unten → oben)")
        s["ebenen"] = [nach_id[i] for i in ids]

    def ebene_loeschen(self, s: dict, a: dict) -> None:
        del s["ebenen"][self.nr(s, a["id"])]

    def bild_aus_medien(self, s: dict, a: dict) -> None:
        self.bild_ebene(s, a["ebene"])["quelle"] = self.quelle(a["quelle"])

    def logo_einsetzen(self, s: dict, a: dict) -> None:
        e = self.bild_ebene(s, a["ebene"])
        e["quelle"] = self.quelle(a["quelle"])
        e["passung"] = "einpassen"          # ein Logo wird nie beschnitten

    # ---- Bildauftraege: aendern die Seite nicht ---------------------------------------------------
    def auftrag(self, name: str, a: dict) -> dict:
        self.bild_ebene(self.s, a["ebene"])
        if name == "bild_erzeugen":
            h = a["hinweis"]
            if not isinstance(h, str) or len(h) > MAX_HINWEIS:
                raise WerkzeugFehler(f"hinweis muss Text sein (höchstens {MAX_HINWEIS} Zeichen)")
            return {"platz": a["ebene"], "modus": "neu", "hinweis": h, "vorlagen": self.vorlagen(a.get("vorlagen"), 3)}
        if name == "bild_aendern":
            h = a["anweisung"]
            if not isinstance(h, str) or not h.strip() or len(h) > MAX_HINWEIS:
                raise WerkzeugFehler(f"anweisung muss Text sein (1-{MAX_HINWEIS} Zeichen)")
            return {"platz": a["ebene"], "modus": "aendern", "hinweis": h,
                    "vorlagen": self.vorlagen(a.get("vorlagen"), 2)}
        return {"platz": a["ebene"], "modus": "freistellen", "hinweis": "", "vorlagen": []}


SEITEN_WERKZEUGE = ("seite_hoehe", "seite_hintergrund", "schriften_setzen", "ebene_hinzufuegen", "ebene_aendern",
                    "ebene_reihenfolge", "ebene_loeschen", "bild_aus_medien", "logo_einsetzen")


def _form_pruefen(a) -> str:
    if not isinstance(a, dict):
        raise WerkzeugFehler("Jede Änderung muss ein Objekt sein")
    name = a.get("werkzeug")
    if not isinstance(name, str) or name not in WERKZEUGE:
        raise WerkzeugFehler(f"{name if isinstance(name, str) else '?'}: unbekanntes Werkzeug")
    pflicht, optional = PARAMETER[name]
    schritt = a.get("schritt")
    if "schritt" in a and (not isinstance(schritt, str) or len(schritt) > MAX_SCHRITT):
        raise WerkzeugFehler(f"schritt höchstens {MAX_SCHRITT} Zeichen")
    given = set(a) - {"werkzeug", "schritt"}
    fehlt, fremd = sorted(pflicht - given), sorted(given - pflicht - optional)
    if fehlt:
        raise WerkzeugFehler(f"Parameter fehlt: {', '.join(fehlt)}")
    if fremd:
        raise WerkzeugFehler(f"Unbekannter Parameter: {', '.join(fremd)}")
    return name


def _kasten(e: dict) -> tuple[float, float, float, float]:
    """Achsenparalleler Kasten (links, oben, rechts, unten) aus Mitte und Mass; Drehung bleibt unberuecksichtigt."""
    return (e["x"] - e["breite"] / 2, e["y"] - e["hoehe"] / 2, e["x"] + e["breite"] / 2, e["y"] + e["hoehe"] / 2)


def _ueberlappt(a: dict, b: dict) -> bool:
    al, ao, ar, au = _kasten(a)
    bl, bo, br, bu = _kasten(b)
    return min(ar, br) > max(al, bl) and min(au, bu) > max(ao, bo)


def _gueltiges_bild(quelle, medien: set[str]) -> bool:
    if bildplaetze.ist_leer(quelle) or not quelle.startswith("medien:"):
        return False
    return quelle[len("medien:"):] in medien


def _abschluss(s: dict, auftraege: list[dict], medien: set[str]) -> list[str]:
    hinweise: list[str] = []
    for e in s["ebenen"]:
        if e["art"] != "text" or not (seite._ANREDE in e["text"] or seite._TEXTLINK.search(e["text"])):
            continue
        andere = [o["id"] for o in s["ebenen"] if o is not e and _ueberlappt(e, o)]
        if andere:
            hinweise.append(f"Ebene {e['id']}: Anrede oder Link überlappt {', '.join(andere)} – "
                            "diesen Text frei und ungedreht stellen")
    if not any(e["art"] == "knopf" for e in s["ebenen"]):
        hinweise.append(KEIN_KNOPF)
    mit_auftrag = {a["platz"] for a in auftraege}
    for e in s["ebenen"]:
        if e["art"] == "bild" and e["id"] not in mit_auftrag and not _gueltiges_bild(e["quelle"], medien):
            hinweise.append(f"Bild-Ebene {e['id']} hat kein gültiges Bild – bild_erzeugen oder bild_aus_medien")
    return hinweise


def _ausfuehren(s_ein: dict, aenderungen: list, medien: set[str], nachspiel: bool) -> ErgebnisSeite:
    if not isinstance(aenderungen, list):
        raise WerkzeugFehler("Die Änderungen müssen eine Liste sein")
    try:
        start = seite.pruefen(s_ein)
    except seite.SeiteFehler as e:
        raise WerkzeugFehler(f"Die Seite ist ungültig: {e}") from None
    hinweise: list[str] = []
    if len(aenderungen) > MAX_AENDERUNGEN:
        hinweise.append(f"Höchstens {MAX_AENDERUNGEN} Änderungen je Antwort – "
                        f"{len(aenderungen) - MAX_AENDERUNGEN} weitere verworfen")
        aenderungen = aenderungen[:MAX_AENDERUNGEN]
    lauf = _Lauf(copy.deepcopy(start), medien)
    fertig: tuple[int, str] | None = None
    andere = False

    def melden(nr: int, name: str, grund: str) -> None:
        hinweise.append(f"Änderung {nr} ({name}): {grund}")

    for nr, a in enumerate(aenderungen, 1):
        name = a.get("werkzeug") if isinstance(a, dict) and isinstance(a.get("werkzeug"), str) else "?"
        try:
            name = _form_pruefen(a)
            if name == "fertig":
                n = a["notiz"]
                if not isinstance(n, str) or len(n) > MAX_NOTIZ:
                    raise WerkzeugFehler(f"notiz muss Text sein (höchstens {MAX_NOTIZ} Zeichen)")
                if fertig is not None:
                    raise WerkzeugFehler("nur einmal je Antwort")
                fertig = (nr, n)
                continue
            andere = True
            if name in _MODUS:
                lauf.auftraege.append((nr, name, lauf.auftrag(name, a)))
                continue
            kopie = copy.deepcopy(lauf.s)
            getattr(lauf, name)(kopie, a)
            lauf.s = seite.pruefen(kopie)
            if name in ("bild_aus_medien", "logo_einsetzen"):   # das Bild ist jetzt da, offene Auftraege entfallen
                for alt in [x for x in lauf.auftraege if x[2]["platz"] == a["ebene"]]:
                    lauf.auftraege.remove(alt)
                    melden(alt[0], alt[1], f"entfällt, Ebene {a['ebene']} hat jetzt ein Bild aus den Medien")
        except _Unbekannt as e:
            melden(nr, name, f"übersprungen – Ebene {e.eid} gibt es inzwischen nicht mehr" if nachspiel else str(e))
        except (WerkzeugFehler, seite.SeiteFehler) as e:
            melden(nr, name, str(e))
        except (KeyError, TypeError, AttributeError, ValueError, IndexError):
            melden(nr, name, "Parameter haben die falsche Form")

    # Bildauftraege gegen den Endstand: Ebene noch da und Bild-Ebene, aendern/freistellen nur auf einem echten Bild
    auftraege: list[dict] = []
    for nr, name, auf in lauf.auftraege:
        try:
            e = lauf.bild_ebene(lauf.s, auf["platz"])
        except _Unbekannt:
            melden(nr, name, f"übersprungen – Ebene {auf['platz']} wurde gelöscht")
            continue
        except WerkzeugFehler as f:
            melden(nr, name, str(f))
            continue
        if name != "bild_erzeugen" and bildplaetze.ist_leer(e["quelle"]):
            melden(nr, name, "Hier ist noch kein Bild – nimm bild_erzeugen")
            continue
        auftraege.append(auf)

    ist_fertig, notiz = False, ""
    if fertig is not None:
        if andere:
            melden(fertig[0], "fertig", "gilt nur ohne weitere Änderungen – die Änderungen wurden übernommen")
        else:
            ist_fertig, notiz = True, fertig[1]
    hinweise += _abschluss(lauf.s, auftraege, lauf.medien)
    return ErgebnisSeite(seite=lauf.s, geaendert=lauf.s != start, bildauftraege=auftraege, hinweise=hinweise,
                         fertig=ist_fertig, notiz=notiz)


def anwenden(seite_dok: dict, aenderungen: list, medien: set[str]) -> ErgebnisSeite:
    """Wendet die Aenderungen einzeln an; ungueltige werden mit Nummer und Grund in hinweise verworfen. Wirft
    WerkzeugFehler nur, wenn aenderungen keine Liste oder die Ausgangsseite selbst ungueltig ist."""
    return _ausfuehren(seite_dok, aenderungen, medien, nachspiel=False)


def nachspielen(seite_neu: dict, aenderungen: list, medien: set[str]) -> ErgebnisSeite:
    """Dieselbe Logik auf einer neueren Fassung (veraltete Basis): Aenderungen an inzwischen geloeschten Ebenen
    werden mit Hinweis 'uebersprungen' ausgelassen, der Rest gilt."""
    return _ausfuehren(seite_neu, aenderungen, medien, nachspiel=True)
