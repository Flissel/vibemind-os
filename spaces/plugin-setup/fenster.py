"""Das Eingabefenster -- die einzige Stelle, an der ein Wert entsteht.

Drei Regeln, alle aus E5 der Spec und alle von Tests gehalten:
  - Der Wert reist per POST. Nie als Query-Parameter -- der landet in
    Zugriffslogs, die niemand als Geheimnisspeicher betrachtet.
  - Keine Antwort spiegelt den Wert zurueck, auch nicht maskiert.
  - Ein Fehlschlag zeigt den STATUSCODE des Anbieters, nie den
    Antwortkoerper (D3 der Vorgaenger-Spec).

Dieses Modul kennt weder Datenbank noch OpenFang. Es bekommt den
Schreibweg als Argument -- so laufen seine Tests ohne Netz, und sie
koennen pruefen, WELCHE Argumente ankommen.
"""
from __future__ import annotations

import html as _html

import anfragen

_KOPF = (
    "<!doctype html><meta charset='utf-8'>"
    "<title>Plugin-Einrichtung</title>"
    "<style>body{font:16px system-ui;margin:3rem auto;max-width:34rem}"
    "input{width:100%;padding:.6rem;font:inherit}"
    "button{margin-top:1rem;padding:.6rem 1.2rem;font:inherit}"
    "code{background:#f2f2f2;padding:.1rem .3rem}</style>"
)


def seite_fuer(a: "anfragen.Anfrage") -> str:
    ref = _html.escape(a.referenz)
    if a.art == "oauth":
        return (f"{_KOPF}<h1>Anmeldung noetig</h1>"
                f'<p>Fuer <code>{ref}</code> meldest du dich beim Anbieter an. '
                f"Der Token wird direkt hier entgegengenommen und nie angezeigt.</p>"
                f'<form method="post"><button>Anmeldung starten</button></form>')
    return (f"{_KOPF}<h1>Schluessel eintragen</h1>"
            f'<p>Fuer <code>{ref}</code>. Der Wert wird sofort geprueft und dann an '
            f"OpenFang uebergeben; er erscheint in keiner Antwort und in keinem Protokoll.</p>"
            f'<form method="post">'
            f'<input name="wert" type="password" autocomplete="off" autofocus>'
            f"<button>Eintragen</button></form>")


def ergebnisseite(ok: bool, referenz: str, hinweis: str) -> str:
    ref = _html.escape(referenz)
    if ok:
        return (f"{_KOPF}<h1>Uebernommen</h1><p><code>{ref}</code> ist geprueft und bei "
                f"OpenFang. Du kannst dieses Fenster schliessen.</p>")
    return (f"{_KOPF}<h1>Nicht uebernommen</h1>"
            f"<p><code>{ref}</code> wurde vom Anbieter abgelehnt: "
            f"<code>{_html.escape(hinweis)}</code>. Der Wert wurde nicht uebergeben.</p>"
            f"<p>Der Agent kann eine neue Eingabe anfordern.</p>")


def entgegennehmen(token: str, wert: str, schreiber) -> tuple[int, str]:
    """Verbraucht das Token und reicht den Wert an `schreiber` weiter.

    Ein unbekanntes, verbrauchtes oder abgelaufenes Token fuehrt zu 404 --
    OHNE den Wert anzufassen.
    """
    a = anfragen.verbrauchen(token)
    if a is None:
        return 404, f"{_KOPF}<h1>Link ungueltig</h1><p>Abgelaufen oder schon benutzt.</p>"
    ergebnis = schreiber(projekt=a.projekt, plugin=a.plugin, referenz=a.referenz,
                         art=a.art, wert=wert, ziel=a.ziel)
    if ergebnis.get("ok"):
        return 200, ergebnisseite(True, a.referenz, "")
    hinweis = str(ergebnis.get("status", ergebnis.get("fehler", "unbekannt")))
    return 200, ergebnisseite(False, a.referenz, hinweis)
