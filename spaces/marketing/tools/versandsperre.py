"""Der Riegel vor Marketings eigenen Versendern (Betreiber-Entscheid 12.09.2026).

sales-claw ist seit diesem Tag der EINZIGE Versandweg
(Spec docs/superpowers/specs/2026-09-12-sales-claw-einziger-versandweg.md).
Marketing schreibt; zugestellt wird drueben, pro Kontakt, mit den Toren, die
dort haengen.

WARUM NICHT GELOESCHT, SONDERN GESPERRT
---------------------------------------
In `_send_paranoid.py` stecken dreizehn Gates — DKIM/SPF/DMARC-Ausrichtung,
Domain-Allowlist mit Unicode-Lookalike-Abwehr, Investor-Lockout,
Confirm-Token, Postfix-Loopback-Probe, Mailq-Audit — und in
`_send_telegram.py` dieselbe Struktur fuer chat_id samt Opt-in-Pruefung.
Das ist gute Arbeit, und sie wird wieder gebraucht: sales-claw hat keinen
Telegram-Dispatcher, und wer ihn baut, holt die Opt-in-Pruefung von hier
(Spec §6.1). Geloeschter Code kann das nicht.

WARUM DER RIEGEL SICH OEFFNEN LAESST
------------------------------------
`MARKETING_VERSAND_TROTZDEM=1` hebt ihn auf. Ein Schalter, der sich nicht
umlegen laesst, wird umgangen — jemand kommentiert die Zeile aus, und dann
weiss niemand mehr, dass es sie gab. Einer, der beim Umlegen laut wird,
wird gelesen: die Umgehung landet als Warnung im Log, mit Werkzeugnamen.

WARUM NUR LIVE
--------------
DRY_RUN und SHADOW stellen nichts zu — sie rechnen Empfaenger aus und
sprechen hoechstens mit Mailpit. Sie zu sperren wuerde Diagnose und
Testlaeufe kaputtmachen, ohne irgendetwas sicherer zu machen. Der Riegel
sitzt genau dort, wo eine Nachricht das Haus verlassen wuerde.
"""
from __future__ import annotations

import logging
import os

UMGEHUNG = "MARKETING_VERSAND_TROTZDEM"

_TEXT = (
    "{werkzeug}: Dieser Space versendet nicht mehr. Seit dem "
    "Betreiber-Entscheid vom 12.09.2026 stellt ausschliesslich sales-claw "
    "zu — pro Kontakt, mit gemeinsamer Verbotsliste, Loeschantrag, "
    "Privat-Flag, UWG-Pruefung und Kontakt-Freigabe. Der Weg dorthin ist "
    "ein Versandauftrag (POST /api/versandauftraege bzw. das Werkzeug "
    "versand_beauftragen); sales-claw macht daraus einen Entwurf, den ein "
    "Mensch freigibt. Muss dieser Versender doch laufen — etwa weil jemand "
    "gerade den fehlenden Telegram-Dispatcher baut —, dann mit "
    "{umgehung}=1; das wird protokolliert."
)


class VersandGesperrt(RuntimeError):
    """Ein Versuch, an sales-claw vorbei zu senden."""


def pruefen(werkzeug: str, log: logging.Logger | None = None) -> None:
    """Bricht ab, wenn `werkzeug` gerade wirklich zustellen wollte.

    Aufzurufen als erstes im LIVE-Zweig, vor jeder Konfiguration und jedem
    Netzkontakt — damit die Absage nicht davon abhaengt, ob SMTP gerade
    erreichbar ist.
    """
    if (os.environ.get(UMGEHUNG, "") or "").strip() in ("1", "true", "yes", "ja"):
        (log or logging.getLogger(__name__)).warning(
            "%s sendet trotz des Versand-Entscheids vom 12.09.2026 — %s ist "
            "gesetzt. sales-claw ist der vorgesehene Weg.", werkzeug, UMGEHUNG)
        return
    raise VersandGesperrt(_TEXT.format(werkzeug=werkzeug, umgehung=UMGEHUNG))
