"""Zugang: Einladung, Passwort, Login und Sitzungs-Cookie.

Geheimnisse (Token, Passwort, Cookie, Mailadresse, Link) erscheinen nie in Logs oder
Fehlermeldungen; geloggt werden nur person_id und firma_id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from email.utils import getaddresses
from typing import Any
from uuid import UUID

import psycopg

from spaces.event_management.system import firmen, pruefung, rechte
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion
from spaces.event_management.system.mailer import Mailer

log = logging.getLogger(__name__)

_ROLLEN = ("leitung", "mitglied")
_EINLADUNG_TAGE = 7
_FEHLER_EINLADUNG = "Einladung ungültig oder abgelaufen"
_PASSWORT_MIN = 12
_ANREDE_MAX = 100

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32

_COOKIE_SEKUNDEN = 12 * 3600

_SPERRE_VERSUCHE = 5
_SPERRE_FENSTER = 15 * 60.0
_SPERRE_AUFRAEUMEN_AB = 10_000


# --- Passwort ---------------------------------------------------------------------


def _b64(daten: bytes) -> str:
    return base64.b64encode(daten).decode("ascii")


def passwort_hash(pw: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    h = hashlib.scrypt(
        pw.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_HASH_BYTES
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(h)}"


def passwort_ok(pw: str, h: str) -> bool:
    """Konstante-Zeit-Vergleich; ein kaputter Hash ergibt False, nie eine Exception."""
    if not isinstance(pw, str) or not isinstance(h, str):
        return False
    teile = h.split("$")
    if len(teile) != 6 or teile[0] != "scrypt":
        return False
    try:
        n, r, p = int(teile[1]), int(teile[2]), int(teile[3])
        salt = base64.b64decode(teile[4], validate=True)
        erwartet = base64.b64decode(teile[5], validate=True)
        if not salt or not erwartet:
            return False
        ist = hashlib.scrypt(
            pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(erwartet)
        )
    except (ValueError, TypeError, OverflowError, MemoryError):
        return False
    return hmac.compare_digest(ist, erwartet)


# Gegen-Hash fuer unbekannte Mail / fehlendes Passwort: gleiche Laufzeit wie ein echter.
_DUMMY_HASH = passwort_hash(secrets.token_urlsafe(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Einladung --------------------------------------------------------------------


def _empfaenger(mail: str) -> str:
    """Genau eine nackte addr-spec (kein Anzeigename, keine Liste, kein Zeilenumbruch),
    sonst ValueError ohne die Adresse."""
    adresse = mail.strip()
    lokal, _, domain = adresse.partition("@")
    if (adresse.count("@") != 1 or not lokal or not domain or "," in adresse
            or any(c.isspace() or not c.isprintable() for c in adresse)
            or getaddresses([adresse]) != [("", adresse)]):
        raise ValueError("Mailadresse ungültig")
    return adresse


def _anrede(name: str) -> str:
    """Personenname fuer den Mailtext: eine Zeile, ohne Steuerzeichen, hoechstens 100 Zeichen."""
    zeile = "".join(c if c.isprintable() else " " for c in name)
    return " ".join(zeile.split())[:_ANREDE_MAX]


def einladen(
    conn: psycopg.Connection[Any], k: Kontext, mailer: Mailer, mail: str, name: str,
    firma_id: UUID, rolle: str, basis_url: str,
) -> None:
    """Legt Person (falls neu) und Einladung an und schickt die Mail nach dem Commit."""
    rechte.pruefe(k, "einladen", firma_id)
    if rolle not in _ROLLEN:
        raise ValueError("Rolle ungueltig")
    mail = _empfaenger(pruefung.text("mail", mail))
    name = pruefung.text("name", name)
    person = firmen.person_finden_oder_anlegen(conn, mail, name)
    token = secrets.token_urlsafe(32)
    with transaktion(conn, k) as cur:
        cur.execute("select name from event.firma where id = %s", (firma_id,))
        firma = cur.fetchone()
        if firma is None:
            raise NichtGefunden("Firma nicht gefunden")
        cur.execute(
            "insert into event.einladung (person_id, firma_id, rolle, token_hash, gueltig_bis)"
            " values (%s, %s, %s, %s, now() + make_interval(days => %s))",
            (person["id"], firma_id, rolle, _token_hash(token), _EINLADUNG_TAGE),
        )
    log.info("Einladung angelegt: person_id=%s firma_id=%s", person["id"], firma_id)

    firma_name = " ".join(str(firma["name"]).split())
    link = f"{basis_url.rstrip('/')}/einladung/{token}"
    betreff = f"Einladung zu {firma_name} im Event-Management"
    text = (
        f"Hallo {_anrede(person['name'])},\n\n"
        f"Sie wurden zu „{firma_name}“ im Event-Management eingeladen (Rolle: {rolle}).\n\n"
        f"Über diesen Link nehmen Sie die Einladung an:\n{link}\n\n"
        f"Der Link ist {_EINLADUNG_TAGE} Tage gültig und funktioniert nur einmal.\n"
        "Wenn Sie diese Einladung nicht erwartet haben, ignorieren Sie die Mail einfach.\n"
    )
    try:
        mailer.senden(person["mail"], betreff, text)
    except Exception as e:
        # Ohne exc_info: SMTP-Fehler koennen die Empfaengeradresse enthalten.
        log.error(
            "Einladungsmail nicht gesendet (%s): person_id=%s firma_id=%s",
            type(e).__name__, person["id"], firma_id,
        )
        raise


def einladung_info(conn: psycopg.Connection[Any], token: str) -> dict[str, Any]:
    """Nur lesen: Firma und ob ein Passwort gesetzt werden muss.

    Gueltig heisst: bekannt, nicht abgelaufen, nicht eingeloest, Person aktiv. Sonst
    immer genau ``{"gueltig": False}`` (kein Orakel ueber den Grund); wirft nie bei
    kaputten Tokens (leer, kein Text, NUL, einzelne Surrogate).
    """
    ungueltig: dict[str, Any] = {"gueltig": False}
    if not isinstance(token, str) or not token:
        return ungueltig
    try:
        h = _token_hash(token)
    except UnicodeError:
        return ungueltig
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select e.firma_name, e.eingeloest, e.gueltig_bis > now() as gueltig, p.aktiv,"
            " event.hat_passwort(p.id) as hat_pw"
            " from event.einladung_holen(%s) e join event.person p on p.id = e.person_id",
            (h,),
        )
        e = cur.fetchone()
    if e is None or e["eingeloest"] is not None or not e["gueltig"] or not e["aktiv"]:
        return ungueltig
    return {"gueltig": True, "firma_name": e["firma_name"], "braucht_passwort": not e["hat_pw"]}


def einladung_einloesen(
    conn: psycopg.Connection[Any], token: str, passwort: str, name: str | None
) -> UUID:
    """Loest eine Einladung ein. Der gueltige Token ist das Recht (kein rechte.pruefe).

    Hat die Person schon ein Passwort, bleiben Passwort und Name unveraendert
    (sonst koennte eine einladende Leitung das Konto uebernehmen).
    """
    if not isinstance(token, str) or not token:
        raise ValueError(_FEHLER_EINLADUNG)
    name = pruefung.text_oder_none("name", name)
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select e.id, e.person_id, e.firma_id, e.rolle, e.eingeloest,"
            " e.gueltig_bis > now() as gueltig, p.aktiv, event.hat_passwort(p.id) as hat_pw"
            " from event.einladung_holen(%s) e join event.person p on p.id = e.person_id",
            (_token_hash(token),),
        )
        e = cur.fetchone()
    if e is None or e["eingeloest"] is not None or not e["gueltig"] or not e["aktiv"]:
        raise ValueError(_FEHLER_EINLADUNG)

    neuer_hash: str | None = None
    neuer_name: str | None = None
    if not e["hat_pw"]:
        if not isinstance(passwort, str) or len(passwort) < _PASSWORT_MIN:
            raise ValueError(f"Das Passwort braucht mindestens {_PASSWORT_MIN} Zeichen")
        neuer_hash = passwort_hash(passwort)
        if name is not None and name.strip():
            neuer_name = name.strip()

    person_id: UUID = e["person_id"]
    firma_id: UUID = e["firma_id"]
    k = Kontext(person_id=person_id, betreiber=False, rollen={firma_id: e["rolle"]})
    with transaktion(conn, k) as cur:
        cur.execute(
            "update event.einladung set eingeloest = now()"
            " where id = %s and eingeloest is null and gueltig_bis > now()",
            (e["id"],),
        )
        if cur.rowcount != 1:
            raise ValueError(_FEHLER_EINLADUNG)
        if neuer_hash is not None:
            # passwort_hash ist fuer event_app unlesbar (004): "nur wenn leer" prueft die
            # SD-Funktion, gebunden an die Person dieses Kontexts.
            cur.execute(
                "select event.passwort_setzen_wenn_leer(%s, %s, %s) as ok",
                (person_id, neuer_hash, neuer_name),
            )
            gesetzt = cur.fetchone()
            if gesetzt is None or not gesetzt["ok"]:
                raise ValueError(_FEHLER_EINLADUNG)
        cur.execute(
            "insert into event.mitgliedschaft (person_id, firma_id, rolle) values (%s, %s, %s)"
            " on conflict (person_id, firma_id) do nothing",
            (person_id, firma_id, e["rolle"]),
        )
    log.info("Einladung eingeloest: person_id=%s firma_id=%s", person_id, firma_id)
    return person_id


# --- Anmelden und Sperre ----------------------------------------------------------

_sperre_lock = threading.Lock()
_fehlversuche: dict[str, list[float]] = {}


def _sperre_zuruecksetzen() -> None:
    """Nur fuer Tests: vergisst alle Fehlversuche."""
    with _sperre_lock:
        _fehlversuche.clear()


def _versuch_reservieren(schluessel: str, jetzt: float) -> bool:
    """Zaehlt den Versuch vorab als Fehlversuch (auch bei Parallelitaet hoechstens 5).

    Gibt False zurueck, wenn die Mail im Fenster schon gesperrt ist.
    """
    grenze = jetzt - _SPERRE_FENSTER
    with _sperre_lock:
        if len(_fehlversuche) > _SPERRE_AUFRAEUMEN_AB:
            for s in list(_fehlversuche):
                frisch = [t for t in _fehlversuche[s] if t > grenze]
                if frisch:
                    _fehlversuche[s] = frisch
                else:
                    del _fehlversuche[s]
        versuche = [t for t in _fehlversuche.get(schluessel, []) if t > grenze]
        if len(versuche) >= _SPERRE_VERSUCHE:
            _fehlversuche[schluessel] = versuche
            return False
        versuche.append(jetzt)
        _fehlversuche[schluessel] = versuche
        return True


def anmelden(
    conn: psycopg.Connection[Any], mail: str, passwort: str, jetzt: float | None = None
) -> UUID | None:
    """Gibt die person_id zurueck oder None (falsch, unbekannt, inaktiv, gesperrt)."""
    jetzt = time.time() if jetzt is None else jetzt
    schluessel = mail.strip().lower() if isinstance(mail, str) else ""
    if not _versuch_reservieren(schluessel, jetzt):
        log.warning("Anmeldung gesperrt (zu viele Fehlversuche)")
        return None
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select id, passwort_hash, aktiv from event.anmeldung_holen(%s)", (schluessel,)
        )
        p = cur.fetchone()
    gespeichert = p["passwort_hash"] if p is not None else None
    # Auch ohne Person/Passwort wird ein Hash berechnet (gleiche Laufzeit).
    ok = passwort_ok(passwort, gespeichert or _DUMMY_HASH)
    if p is None or not gespeichert or not ok or not p["aktiv"]:
        log.info("Anmeldung fehlgeschlagen")
        return None
    with _sperre_lock:
        _fehlversuche.pop(schluessel, None)
    log.info("Anmeldung erfolgreich: person_id=%s", p["id"])
    return p["id"]


def abmelden_ueberall(conn: psycopg.Connection[Any], person_id: UUID) -> None:
    """Erhoeht sitzungs_version; alle bisherigen Cookies werden ungueltig."""
    k = Kontext(person_id=person_id, betreiber=False, rollen={})
    with transaktion(conn, k) as cur:
        cur.execute(
            "update event.person set sitzungs_version = sitzungs_version + 1 where id = %s",
            (person_id,),
        )
        if cur.rowcount != 1:
            raise NichtGefunden("Person nicht gefunden")
    log.info("Ueberall abgemeldet: person_id=%s", person_id)


# --- Cookie und CSRF --------------------------------------------------------------


def _hmac_b64(geheimnis: bytes, daten: bytes) -> str:
    digest = hmac.new(geheimnis, daten, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _signatur(geheimnis: bytes, nutzlast: str) -> str:
    return _hmac_b64(geheimnis, nutzlast.encode("utf-8"))


def cookie_ausstellen(
    person_id: UUID, sitzungs_version: int, geheimnis: bytes, jetzt: float | None = None
) -> str:
    """``<person_id>.<version>.<ablauf_epoch>.<sig>``, gueltig 12 Stunden."""
    if not geheimnis:
        raise ValueError("Geheimnis fehlt")
    jetzt = time.time() if jetzt is None else jetzt
    nutzlast = f"{UUID(str(person_id))}.{int(sitzungs_version)}.{int(jetzt) + _COOKIE_SEKUNDEN}"
    return f"{nutzlast}.{_signatur(geheimnis, nutzlast)}"


def cookie_pruefen(
    wert: str, geheimnis: bytes, conn: psycopg.Connection[Any], jetzt: float | None = None
) -> UUID | None:
    """person_id bei gueltiger Signatur, nicht abgelaufen, aktiv und passender Version."""
    jetzt = time.time() if jetzt is None else jetzt
    try:
        if not wert or not geheimnis:
            return None
        teile = wert.split(".")
        if len(teile) != 4:
            return None
        nutzlast = ".".join(teile[:3])
        if not hmac.compare_digest(
            _signatur(geheimnis, nutzlast).encode("ascii"), teile[3].encode("utf-8")
        ):
            return None
        person_id = UUID(teile[0])
        version = int(teile[1])
        ablauf = int(teile[2])
    except (ValueError, TypeError, AttributeError):
        return None
    if ablauf <= jetzt:
        return None
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "select aktiv, sitzungs_version from event.person where id = %s", (person_id,)
        )
        p = cur.fetchone()
    if p is None or not p["aktiv"] or p["sitzungs_version"] != version:
        return None
    return person_id


def csrf_token(cookie_wert: str, geheimnis: bytes) -> str:
    if not cookie_wert or not geheimnis:
        raise ValueError("Cookie oder Geheimnis fehlt")
    return _hmac_b64(geheimnis, b"csrf:" + cookie_wert.encode("utf-8"))


def csrf_ok(cookie_wert: str, token: str, geheimnis: bytes) -> bool:
    if not cookie_wert or not token or not geheimnis:
        return False
    try:
        erwartet = csrf_token(cookie_wert, geheimnis)
        return hmac.compare_digest(erwartet.encode("ascii"), token.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        return False
