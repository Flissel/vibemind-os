"""Mailversand: Protokoll, SMTP mit STARTTLS und Speicher-Attrappe fuer Tests."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class Mailer(Protocol):
    def senden(self, an: str, betreff: str, text: str) -> None: ...


@dataclass(frozen=True)
class GesendeteMail:
    an: str
    betreff: str
    text: str


class SpeicherMailer:
    """Testattrappe: merkt sich jede Mail in ``gesendet``."""

    def __init__(self) -> None:
        self.gesendet: list[GesendeteMail] = []

    def senden(self, an: str, betreff: str, text: str) -> None:
        self.gesendet.append(GesendeteMail(an, betreff, text))


def _pflicht(umgebung: Mapping[str, str], name: str) -> str:
    wert = umgebung.get(name, "").strip()
    if not wert:
        raise ValueError(f"{name} fehlt")
    return wert


class SmtpMailer:
    """SMTP mit STARTTLS (Standard-SSL-Kontext, Zertifikat wird geprueft)."""

    def __init__(
        self, host: str, port: int, user: str, passwort: str, absender: str,
        timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self._passwort = passwort
        self.absender = absender
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"SmtpMailer(host={self.host!r}, port={self.port})"

    @classmethod
    def aus_umgebung(cls, umgebung: Mapping[str, str] | None = None) -> SmtpMailer:
        """Liest EVENT_SMTP_*; Fehlermeldungen nennen nur den Variablennamen."""
        env = os.environ if umgebung is None else umgebung
        host = _pflicht(env, "EVENT_SMTP_HOST")
        port_text = _pflicht(env, "EVENT_SMTP_PORT")
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError("EVENT_SMTP_PORT ungueltig") from None
        if not 0 < port < 65536:
            raise ValueError("EVENT_SMTP_PORT ungueltig")
        user = _pflicht(env, "EVENT_SMTP_USER")
        absender = _pflicht(env, "EVENT_SMTP_ABSENDER")
        datei = _pflicht(env, "EVENT_SMTP_PASSWORT_FILE")
        try:
            # strip(): Dateien von Windows enden oft mit CRLF.
            passwort = Path(datei).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            raise ValueError("EVENT_SMTP_PASSWORT_FILE nicht lesbar") from None
        if not passwort:
            raise ValueError("EVENT_SMTP_PASSWORT_FILE ist leer")
        return cls(host, port, user, passwort, absender)

    def senden(self, an: str, betreff: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.absender
        msg["To"] = an
        msg["Subject"] = betreff
        msg.set_content(text)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.user, self._passwort)
            smtp.send_message(msg)
        log.info("Mail per SMTP gesendet")
