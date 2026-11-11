"""Der Werkbank-Bote (Video-Werkbank Stufe 1, 11.11.2026): spiegelt jedes Tor eines Videos (Gliederung,
Szenentext, Szenenclip) als Vorschlag nach Rowboat, liest zurueck, ob der Betreiber ihn behalten oder verworfen hat,
ersetzt veraltete Vorschlaege und hakt angenommene ab. Laeuft auf der VM, ohne Sprachmodell.

Muster ist freigabe_bote.py aus sales-claw. Regeln:
- Rowboat wird nur ueber POST /rpc/<kanal> des headless Servers angesprochen. Der Schluessel ist ein enger
  Rowboat-Schluessel in einer Datei und erscheint nie in Log oder Fehlertext (geloggt wird nur der Typname).
- Die Datenbank wird nur ueber die Funktionen aus 099_video_werkbank.sql angefasst, nie per INSERT/UPDATE/DELETE.
- Ein eigener Rueckzug schreibt in Rowboat kein Signal; deshalb ist ein Signal zu einem Vorschlag, den Marketing
  zurueckzieht, nie eine Entscheidung des Betreibers.
- Hoechstens MAX_OFFEN Vorschlaege gleichzeitig offen. Der naechste Block kommt erst, wenn keiner mehr offen ist;
  der Ersatz fuer eine zurueckgegebene Szene kommt sofort, solange weniger als MAX_OFFEN offen sind.
- Fehlt WERKBANK_UI_BASIS_URL, fasst der Bote weder Rowboat noch die Datenbank an."""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from spaces.marketing.sync import _db

LOG = logging.getLogger("werkbank-bote")

MAX_OFFEN = 10
TAKT_S = 60
RPC_ZEITLIMIT_S = 10
ROWBOAT_HOST = "127.0.0.1:3220"
TITEL_MAX = 160
KURZ_MAX = 80

_KENNUNG_IM_LINK = re.compile(r"/marketing/werkbank/v/(wb-[a-z2-7]{10})\)", re.IGNORECASE)
_STOPP = threading.Event()
_BASIS_GEMELDET = False


def _rpc_url() -> str:
    return os.environ.get("WERKBANK_ROWBOAT_RPC_URL", "http://127.0.0.1:3220").strip().rstrip("/")


def _schluessel() -> str:
    datei = os.environ.get("WERKBANK_ROWBOAT_SCHLUESSEL_DATEI", "").strip()
    if not datei:
        raise RuntimeError("Keine Schluesseldatei eingestellt")
    with open(datei, encoding="utf-8") as f:
        return f.read().strip()


def _bereinigen(text, grenze: int, ersatz: str = "") -> str:
    """Modelltext fuer eine Vorschlagszeile: ohne @[]()-Zeichen und Anfuehrungszeichen, einzeilig, gekappt."""
    ohne = re.sub(r"[@\[\]()„“\"]", "", str(text or ""))
    einzeilig = re.sub(r"\s+", " ", ohne).strip()[:grenze].strip()
    return einzeilig or ersatz


_URL_ESCAPE = {"@": "%40", "[": "%5B", "]": "%5D", "(": "%28", ")": "%29"}


def _url_sicher(url) -> str:
    return "".join(_URL_ESCAPE.get(c, c) for c in str(url))


def vorschlagstext(v: dict, basis: str) -> str:
    link = f"[Ansehen]({_url_sicher(basis.rstrip('/'))}/marketing/werkbank/v/{v['kennung']})"
    if v.get("tor") == "gliederung" or v.get("szene") is None:
        titel = _bereinigen(v.get("titel"), TITEL_MAX, "Ohne Titel")
        kopf = f"[Video] Gliederung „{titel}“ (Fassung {int(v.get('fassung') or 1)})"
    elif v.get("tor") == "text":
        kopf = f"[Video] Szene {v.get('nr')} Text: {_bereinigen(v.get('kurz'), KURZ_MAX, 'ohne Titel')}"
    else:
        kopf = f"[Video] Szene {v.get('nr')} Clip ansehen"
    return f"{kopf} · {link}"


def kennung_in(text: str) -> str | None:
    """Nur eine Kennung aus einem Werkbank-Link zaehlt; die letzte im Text gewinnt. Rowboat setzt Schluessel in
    Kleinbuchstaben; das Alphabet der Kennung (a-z2-7) ueberlebt das."""
    treffer = _KENNUNG_IM_LINK.findall(text or "")
    return treffer[-1].lower() if treffer else None


@dataclass
class RowboatStand:
    vorschlaege: list[str] = field(default_factory=list)
    items: list[tuple[str, str, bool]] = field(default_factory=list)  # (key, text, checked)
    signale: list[dict] = field(default_factory=list)


@dataclass
class Plan:
    hinzufuegen: list[tuple[str, str]] = field(default_factory=list)    # (Kennung, Text)
    zurueckziehen: list[tuple[str, str]] = field(default_factory=list)  # (Kennung, Vorschlagstext in Rowboat)
    abhaken: list[tuple[str, str]] = field(default_factory=list)        # (Kennung, Item-Key; "" = nur in der DB)
    entscheiden: list[tuple[str, str]] = field(default_factory=list)    # (Kennung, passt | nochmal)
    erledigt: list[str] = field(default_factory=list)


def _signal_zu(signale: list[dict], art: str, kennung: str) -> bool:
    return any(s.get("kind") == art and kennung_in(str(s.get("key", ""))) == kennung for s in signale)


def planen(lebend: list[dict], stand: RowboatStand, basis: str) -> Plan:
    plan = Plan()
    vorschlag_text: dict[str, str] = {}
    for text in stand.vorschlaege:
        k = kennung_in(text)
        if k:
            vorschlag_text.setdefault(k, text)
    items_je: dict[str, list[tuple[str, str, bool]]] = {}
    for item in stand.items:
        k = kennung_in(item[1])
        if k:
            items_je.setdefault(k, []).append(item)

    offen_zahl = 0
    wartend: list[dict] = []
    for v in lebend:
        k = v["kennung"].lower()
        s = v.get("stand")
        items = items_je.get(k, [])
        ungehakt = [i for i in items if not i[2]]
        if s == "zurueckziehen":                                           # Regel 1: nie eine Entscheidung
            if k in vorschlag_text:
                plan.zurueckziehen.append((v["kennung"], vorschlag_text[k]))
                continue
            plan.abhaken.extend((v["kennung"], i[0]) for i in ungehakt)
            plan.erledigt.append(v["kennung"])
        elif s == "offen":                                                 # Regel 2
            if items or _signal_zu(stand.signale, "kept", k):
                plan.entscheiden.append((v["kennung"], "passt"))
                plan.abhaken.extend((v["kennung"], i[0]) for i in ungehakt)
            elif k in vorschlag_text:
                offen_zahl += 1
            elif _signal_zu(stand.signale, "dismissed", k):
                plan.entscheiden.append((v["kennung"], "nochmal"))
            else:                                                          # in Rowboat verloren gegangen
                plan.hinzufuegen.append((v["kennung"], vorschlagstext(v, basis)))
                offen_zahl += 1
        elif s == "angenommen":                                            # Regel 3
            plan.abhaken.extend((v["kennung"], i[0]) for i in ungehakt)
            if not ungehakt:
                plan.abhaken.append((v["kennung"], ""))
        elif s == "wartend":
            wartend.append(v)

    plazt = MAX_OFFEN - offen_zahl                                         # Regel 4
    for v in wartend:
        if plazt <= 0:
            break
        if v.get("ersatz") or offen_zahl == 0:
            plan.hinzufuegen.append((v["kennung"], vorschlagstext(v, basis)))
            plazt -= 1
    return plan


def _rpc(kanal: str, body):
    anfrage = urllib.request.Request(
        f"{_rpc_url()}/rpc/{kanal}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {_schluessel()}",
                 "Host": ROWBOAT_HOST,
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(anfrage, timeout=RPC_ZEITLIMIT_S) as antwort:
            return json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # Status und Typ ins Log, nie der Rumpf
        e.close()
        LOG.error("RPC %s: %s %d", kanal, type(e).__name__, e.code)
        raise


def _items_sammeln(items, ziel: list) -> None:
    for item in items:
        ziel.append((str(item["key"]), str(item["text"]), bool(item["checked"])))
        _items_sammeln(item.get("children") or [], ziel)


def _stand_lesen(rpc) -> RowboatStand:
    """Beide Kanaele brauchen den Rumpf null. Fehlt der erwartete Schluessel (Fehlerantwort sieht sonst wie eine
    leere Liste aus), ist der Durchgang gescheitert, nicht "nichts da"."""
    todo = rpc("todo:get", None)
    if not isinstance(todo, dict) or not isinstance(todo.get("list"), dict) or "blocks" not in todo["list"]:
        raise ValueError("todo:get ohne Liste")
    items: list[tuple[str, str, bool]] = []
    _items_sammeln([b["item"] for b in todo["list"]["blocks"] if b.get("kind") == "item"], items)
    sig = rpc("todo:plannerSignals", None)
    if not isinstance(sig, dict) or not isinstance(sig.get("signals"), list):
        raise ValueError("todo:plannerSignals ohne Signale")
    return RowboatStand(vorschlaege=[str(s) for s in todo.get("suggestions") or []],
                        items=items, signale=list(sig["signals"]))


def _lebend_lesen(db) -> list[dict]:
    zeilen = db.query_via_docker("SELECT marketing.video_vorschlaege_lebend() AS v", streng=True)
    return [z["v"] for z in zeilen if isinstance(z.get("v"), dict)]


def _ja(db, sql: str, params: dict) -> dict | None:
    return db.query_one(sql, params, streng=True)


def durchgang(db=_db, rpc=_rpc) -> dict:
    global _BASIS_GEMELDET
    zaehler = {"eingestellt": 0, "zurueckgezogen": 0, "abgehakt": 0, "entschieden": 0}
    basis = os.environ.get("WERKBANK_UI_BASIS_URL", "").strip().rstrip("/")
    if not basis:
        if not _BASIS_GEMELDET:
            _BASIS_GEMELDET = True
            LOG.error("WERKBANK_UI_BASIS_URL fehlt - Rowboat wird nicht angefasst.")
        return zaehler
    try:
        lebend = _lebend_lesen(db)
        stand = _stand_lesen(rpc)
        plan = planen(lebend, stand, basis)

        for kennung, text in plan.zurueckziehen:
            antwort = rpc("todo:withdrawSuggestion", {"text": text})
            if isinstance(antwort, dict) and antwort.get("removed") is True:
                zaehler["zurueckgezogen"] += 1
                _ja(db, "SELECT marketing.video_vorschlag_erledigt(%(k)s) AS ok", {"k": kennung})

        for kennung, urteil in plan.entscheiden:
            zeile = _ja(db, "SELECT marketing.video_entscheiden(%(k)s, %(u)s, 'rowboat') AS r",
                        {"k": kennung, "u": urteil})
            if zeile and isinstance(zeile.get("r"), dict) and zeile["r"].get("ok") is True:
                zaehler["entschieden"] += 1

        nicht_getickt: set[str] = set()
        for kennung, key in plan.abhaken:
            if key:
                antwort = rpc("todo:setChecked", {"key": key, "checked": True})
                if isinstance(antwort, dict) and antwort.get("success") is False:
                    LOG.warning("setChecked abgelehnt - der naechste Durchgang versucht es erneut.")
                    nicht_getickt.add(kennung)
                    continue
            _ja(db, "SELECT marketing.video_vorschlag_abgehakt(%(k)s) AS ok", {"k": kennung})
            zaehler["abgehakt"] += 1

        for kennung in plan.erledigt:
            if kennung not in nicht_getickt:
                _ja(db, "SELECT marketing.video_vorschlag_erledigt(%(k)s) AS ok", {"k": kennung})

        schon_da = {kennung_in(t) for t in stand.vorschlaege}
        neu = [(k, t) for k, t in plan.hinzufuegen if k.lower() not in schon_da]
        bestaetigt = {k: t for k, t in plan.hinzufuegen if k.lower() in schon_da}  # DB hinkt hinterher
        if neu:
            antwort = rpc("todo:addSuggestions", {"texts": [t for _, t in neu]})
            for text in (antwort.get("added") if isinstance(antwort, dict) else None) or []:
                k = kennung_in(text)
                if k:
                    bestaetigt[k] = str(text)
        for kennung, text in plan.hinzufuegen:
            if kennung.lower() in bestaetigt:
                _ja(db, "SELECT marketing.video_vorschlag_gepostet(%(k)s, %(t)s) AS ok",
                    {"k": kennung, "t": bestaetigt[kennung.lower()]})
                zaehler["eingestellt"] += 1
    except Exception as e:  # noqa: BLE001 - nur der Typ ins Log, nie der Text (Schluessel)
        LOG.error("Durchgang abgebrochen: %s", type(e).__name__)
        return zaehler
    LOG.info("Durchgang: %s", zaehler)
    return zaehler


def _stopp_anfordern(signum, _rahmen) -> None:
    LOG.info("%s empfangen - nach diesem Durchgang ist Schluss.", signal.Signals(signum).name)
    _STOPP.set()


def _logging_einrichten() -> None:
    if LOG.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def main() -> int:
    _logging_einrichten()
    signal.signal(signal.SIGTERM, _stopp_anfordern)
    signal.signal(signal.SIGINT, _stopp_anfordern)
    LOG.info("Start: takt=%ds max_offen=%d", TAKT_S, MAX_OFFEN)
    while not _STOPP.is_set():
        durchgang()
        _STOPP.wait(TAKT_S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
