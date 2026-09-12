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
                f'<p>Fuer <code>{ref}</code> sollst du dich hier beim Anbieter '
                f"anmelden, ohne dass der Token je angezeigt wird. Der Klick "
                f'auf "Anmeldung starten" unten fuehrt durch die Anmeldung '
                f"beim Anbieter; der beschaffte Token geht danach direkt an "
                f"OpenFang und erscheint in keiner Antwort dieses Fensters. "
                f"Der Link gilt fuer genau einen Versuch -- bei einem Abbruch "
                f"oder Fehlschlag kann der Agent eine neue Eingabe anfordern.</p>"
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


def oauth_entgegennehmen(token: str, beschaffer, schreiber) -> tuple[int, str]:
    """Wie `entgegennehmen`, nur dass der Wert nicht aus dem Formular kommt,
    sondern aus dem OAuth-Fluss des Anbieters.

    `beschaffer(mcp_url) -> (name, token)` ist die Naht zum Provisioner.
    Eine Ausnahme daraus wird NICHT durchgereicht: ihre Nachricht koennte
    den Token enthalten.
    """
    a = anfragen.verbrauchen(token)
    if a is None:
        return 404, f"{_KOPF}<h1>Link ungueltig</h1><p>Abgelaufen oder schon benutzt.</p>"
    try:
        _name, wert = beschaffer(a.ziel)
    except BaseException:  # noqa: BLE001 -- die Nachricht koennte den Token tragen.
        # NICHT `except Exception`: der echte Provisioner
        # (provision-oauth-token.py) meldet erwartete Fehlschlaege
        # (Discovery/Registrierung/Callback/kein access_token) per
        # `raise SystemExit(...)` -- und `SystemExit` ist KEIN
        # `Exception`-Subtyp (`BaseException` direkt), faellt also durch
        # ein blosses `except Exception` und wuerde unbehandelt bis in den
        # ASGI-Stack durchschlagen. Belegt: ein `discover_resource_metadata`-
        # Fehlschlag gegen eine unbekannte Domain reproduziert das (s.
        # Task-6-Bericht, Abschnitt Mutation-Checks).
        return 200, ergebnisseite(False, a.referenz, "Anmeldung abgebrochen oder fehlgeschlagen")
    ergebnis = schreiber(projekt=a.projekt, plugin=a.plugin, referenz=a.referenz,
                         art=a.art, wert=wert, ziel=a.ziel)
    if ergebnis.get("ok"):
        return 200, ergebnisseite(True, a.referenz, "")
    return 200, ergebnisseite(False, a.referenz,
                              str(ergebnis.get("status", "unbekannt")))
