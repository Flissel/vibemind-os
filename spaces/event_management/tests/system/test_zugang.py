"""Tests fuer Zugang (Einladung, Passwort, Login, Sitzung) und Mailer (echte Postgres-DB)."""
from __future__ import annotations

import hashlib
import logging
import re
import ssl
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from spaces.event_management.system import mailer, zugang
from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden

BASIS = "https://events.example.org/"
PW = "ein-langes-passwort-1"
GEHEIM = b"testgeheimnis-nur-fuer-tests-0123456789"
FEHLER = "Einladung ungültig oder abgelaufen"


@pytest.fixture(autouse=True)
def _sperre_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, pw: str | None = None, aktiv: bool = True) -> UUID:
    h = zugang.passwort_hash(pw) if pw is not None else None
    return admin.execute(
        "insert into event.person (mail, name, passwort_hash, aktiv) values (%s, %s, %s, %s)"
        " returning id",
        (mail, "Person " + mail, h, aktiv),
    ).fetchone()["id"]


def _leitung(admin: Any, firma: UUID) -> Kontext:
    p = _person(admin, f"leitung-{uuid4().hex[:8]}@example.org")
    admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (p, firma))
    return Kontext(person_id=p, betreiber=False, rollen={firma: "leitung"})


def _token(m: mailer.SpeicherMailer) -> str:
    treffer = re.search(r"/einladung/([A-Za-z0-9_-]+)", m.gesendet[-1].text)
    assert treffer is not None
    return treffer.group(1)


def _einladen(db_app: Any, admin: Any, mail: str = "anna@example.org",
              rolle: str = "mitglied") -> tuple[UUID, mailer.SpeicherMailer, str]:
    f = _firma(admin, f"f-{uuid4().hex[:8]}")
    m = mailer.SpeicherMailer()
    zugang.einladen(db_app(), _leitung(admin, f), m, mail, "Anna Beispiel", f, rolle, BASIS)
    return f, m, _token(m)


class _Stolperdraht:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"SQL-Zugriff vor der Rechtepruefung: {name}")


# --- Passwort --------------------------------------------------------------------


def test_passwort_hash_format_und_kaputte_hashes() -> None:
    h = zugang.passwort_hash(PW)
    teile = h.split("$")
    assert teile[:4] == ["scrypt", str(2**14), "8", "1"] and len(teile) == 6
    assert zugang.passwort_ok(PW, h)
    assert not zugang.passwort_ok(PW + "x", h)
    assert zugang.passwort_hash(PW) != h  # eigener Salt
    for kaputt in ["", "scrypt", "scrypt$x$8$1$aa$bb", "md5$1$2$3$aa$bb",
                   "scrypt$16384$8$1$!!!$bb", "scrypt$16384$8$1$$", h + "$mehr",
                   "scrypt$1073741824$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAA",
                   "scrypt$-1$8$1$AAAA$AAAA", None]:
        assert zugang.passwort_ok(PW, kaputt) is False  # type: ignore[arg-type]


# --- Einladung -------------------------------------------------------------------


def test_einladung_gleiche_mail_andere_schreibweise(db_admin: Any, db_app: Any) -> None:
    f1, m1, t1 = _einladen(db_app, db_admin, mail="Anna@Example.ORG")
    f2, m2, t2 = _einladen(db_app, db_admin, mail="  anna@example.org ", rolle="leitung")
    p1 = zugang.einladung_einloesen(db_app(), t1, PW, "Anna B.")
    p2 = zugang.einladung_einloesen(db_app(), t2, "", None)
    assert p1 == p2
    assert db_admin.execute(
        "select count(*) as n from event.person where lower(mail) = 'anna@example.org'"
    ).fetchone()["n"] == 1
    rollen = {r["firma_id"]: r["rolle"] for r in db_admin.execute(
        "select firma_id, rolle from event.mitgliedschaft where person_id = %s", (p1,))}
    assert rollen == {f1: "mitglied", f2: "leitung"}
    assert m1.gesendet[0].an == m2.gesendet[0].an == "anna@example.org"
    assert zugang.anmelden(db_app(), "ANNA@example.org", PW) == p1


def test_einladungsmail_deutsch_mit_link(db_admin: Any, db_app: Any) -> None:
    _, m, token = _einladen(db_app, db_admin)
    mail = m.gesendet[0]
    assert len(m.gesendet) == 1
    assert f"https://events.example.org/einladung/{token}" in mail.text
    assert "//einladung" not in mail.text
    assert "Einladung" in mail.betreff and "7 Tage" in mail.text
    assert token not in mail.betreff and len(token) >= 40


def test_token_nur_als_hash_gespeichert(db_admin: Any, db_app: Any) -> None:
    _, _, token = _einladen(db_app, db_admin)
    zeilen = db_admin.execute("select row_to_json(e)::text as z from event.einladung e").fetchall()
    assert len(zeilen) == 1 and token not in zeilen[0]["z"]
    row = db_admin.execute(
        "select token_hash, gueltig_bis - now() as dauer, eingeloest from event.einladung"
    ).fetchone()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["eingeloest"] is None
    assert abs(row["dauer"].total_seconds() - 7 * 86400) < 120


def test_token_einmalig_und_ablauf(db_admin: Any, db_app: Any) -> None:
    _, _, token = _einladen(db_app, db_admin)
    pid = zugang.einladung_einloesen(db_app(), token, PW, None)
    assert isinstance(pid, UUID)
    with pytest.raises(ValueError) as zweimal:
        zugang.einladung_einloesen(db_app(), token, PW, None)
    _, _, abgelaufen = _einladen(db_app, db_admin, mail="bert@example.org")
    db_admin.execute(
        "update event.einladung set gueltig_bis = now() - interval '1 second'"
        " where token_hash = %s", (hashlib.sha256(abgelaufen.encode()).hexdigest(),))
    with pytest.raises(ValueError) as alt:
        zugang.einladung_einloesen(db_app(), abgelaufen, PW, None)
    with pytest.raises(ValueError) as unbekannt:
        zugang.einladung_einloesen(db_app(), "gibt-es-nicht", PW, None)
    with pytest.raises(ValueError) as leer:
        zugang.einladung_einloesen(db_app(), "", PW, None)
    for e in (zweimal, alt, unbekannt, leer):
        assert str(e.value) == FEHLER
    assert db_admin.execute(
        "select passwort_hash from event.person where lower(mail) = 'bert@example.org'"
    ).fetchone()["passwort_hash"] is None


def test_einloesen_doppelt_im_wettlauf(
    db_admin: Any, db_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwischen Lesen und Schreiben loest jemand anderes ein: rowcount-Wache greift."""
    _, _, token = _einladen(db_app, db_admin)
    echt = zugang.passwort_hash

    def dazwischen(pw: str) -> str:
        db_admin.execute("update event.einladung set eingeloest = now()")
        return echt(pw)

    monkeypatch.setattr(zugang, "passwort_hash", dazwischen)
    with pytest.raises(ValueError, match=FEHLER):
        zugang.einladung_einloesen(db_app(), token, PW, "Neu")
    p = db_admin.execute("select passwort_hash, name from event.person"
                         " where lower(mail) = 'anna@example.org'").fetchone()
    assert p["passwort_hash"] is None and p["name"] == "Anna Beispiel"
    assert db_admin.execute(
        "select count(*) as n from event.mitgliedschaft m join event.person p"
        " on p.id = m.person_id where p.mail = 'anna@example.org'").fetchone()["n"] == 0


def test_kurzes_passwort_abgelehnt(db_admin: Any, db_app: Any) -> None:
    _, _, token = _einladen(db_app, db_admin)
    for schlecht in ["elf-zeichen", "", None]:
        with pytest.raises(ValueError) as e:
            zugang.einladung_einloesen(db_app(), token, schlecht, None)  # type: ignore[arg-type]
        assert "12" in str(e.value) and (not schlecht or schlecht not in str(e.value))
    # Die Einladung ist nicht verbraucht.
    assert zugang.einladung_einloesen(db_app(), token, "genau-zwoelf", "Anna") is not None


def test_einladung_aendert_vorhandenes_passwort_und_namen_nicht(
    db_admin: Any, db_app: Any
) -> None:
    """Sicherheits-Ruling: sonst koennte eine Leitung per Einladung ein Konto uebernehmen."""
    p = _person(db_admin, "carla@example.org", pw=PW)
    f0 = _firma(db_admin, "alt")
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (p, f0))
    vorher = db_admin.execute("select passwort_hash, name from event.person where id = %s",
                              (p,)).fetchone()
    f, _, token = _einladen(db_app, db_admin, mail="carla@example.org")
    assert zugang.einladung_einloesen(db_app(), token, "angreifer-passwort-1", "Fremd") == p
    nachher = db_admin.execute("select passwort_hash, name from event.person where id = %s",
                               (p,)).fetchone()
    assert nachher == vorher
    assert zugang.anmelden(db_app(), "carla@example.org", "angreifer-passwort-1") is None
    assert zugang.anmelden(db_app(), "carla@example.org", PW) == p
    # Bestehende Mitgliedschaft bleibt unveraendert (on conflict do nothing).
    f2, _, t2 = _einladen(db_app, db_admin, mail="dora@example.org", rolle="mitglied")
    d = db_admin.execute("select id from event.person where mail = 'dora@example.org'"
                         ).fetchone()["id"]
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (d, f2))
    zugang.einladung_einloesen(db_app(), t2, PW, None)
    assert db_admin.execute("select rolle from event.mitgliedschaft where person_id = %s",
                            (d,)).fetchone()["rolle"] == "leitung"
    assert f != f2


def test_einloesen_inaktive_person(db_admin: Any, db_app: Any) -> None:
    _, _, token = _einladen(db_app, db_admin)
    db_admin.execute("update event.person set aktiv = false where mail = 'anna@example.org'")
    with pytest.raises(ValueError) as e:
        zugang.einladung_einloesen(db_app(), token, PW, None)
    assert str(e.value) == FEHLER


def test_einladen_prueft_recht_vor_sql(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "recht")
    m = mailer.SpeicherMailer()
    mitglied = Kontext(person_id=uuid4(), betreiber=False, rollen={f: "mitglied"})
    with pytest.raises(KeinRecht):
        zugang.einladen(_Stolperdraht(), mitglied, m, "x@example.org", "X", f,  # type: ignore[arg-type]
                        "mitglied", BASIS)
    fremd = Kontext(person_id=uuid4(), betreiber=False, rollen={uuid4(): "leitung"})
    with pytest.raises(NichtGefunden):
        zugang.einladen(_Stolperdraht(), fremd, m, "x@example.org", "X", f,  # type: ignore[arg-type]
                        "mitglied", BASIS)
    leitung = Kontext(person_id=uuid4(), betreiber=False, rollen={f: "leitung"})
    with pytest.raises(ValueError):
        zugang.einladen(_Stolperdraht(), leitung, m, "x@example.org", "X", f,  # type: ignore[arg-type]
                        "betreiber", BASIS)
    assert m.gesendet == []


@pytest.mark.parametrize("mail", [
    "a@example.org, b@example.org", "a@example.org;b@example.org",
    "a@example.org\nBcc: b@example.org", "a@example.org\r\nBcc: b@example.org",
    "Anna <anna@example.org>", "anna", "a@b@example.org", "@example.org", "anna@",
    "a b@example.org", '"a,b"@example.org', "", "   ",
])
def test_einladen_nur_genau_eine_adresse(db_admin: Any, mail: str) -> None:
    f = _firma(db_admin, "empf")
    m = mailer.SpeicherMailer()
    leitung = Kontext(person_id=uuid4(), betreiber=False, rollen={f: "leitung"})
    with pytest.raises(ValueError, match="Mailadresse") as e:
        zugang.einladen(_Stolperdraht(), leitung, m, mail, "X", f,  # type: ignore[arg-type]
                        "mitglied", BASIS)
    assert "example.org" not in str(e.value)
    assert m.gesendet == []


def test_einladungsmail_name_einzeilig_und_gekuerzt(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "einzeilig")
    m = mailer.SpeicherMailer()
    name = "Anna\r\nBcc: x@example.org\tBeispiel\x0b\u2028" + "y" * 200
    zugang.einladen(db_app(), _leitung(db_admin, f), m, "anna@example.org", name, f,
                    "mitglied", BASIS)
    erste = m.gesendet[0].text.split("\n")[0]
    assert erste.startswith("Hallo Anna Bcc: x@example.org Beispiel y") and erste.endswith(",")
    anrede = erste[len("Hallo "):-1]
    assert len(anrede) <= 100 and anrede.isprintable()
    assert m.gesendet[0].an == "anna@example.org"


def test_mailfehler_wird_geloggt_und_weitergereicht(
    db_admin: Any, db_app: Any, caplog: pytest.LogCaptureFixture
) -> None:
    class KaputterMailer:
        def senden(self, an: str, betreff: str, text: str) -> None:
            raise OSError("Verbindung abgelehnt")

    caplog.set_level(logging.DEBUG)
    f = _firma(db_admin, "kaputt")
    with pytest.raises(OSError):
        zugang.einladen(db_app(), _leitung(db_admin, f), KaputterMailer(), "eva@example.org",
                        "Eva", f, "mitglied", BASIS)
    # Die Einladung ist committet (Mail erst nach Commit), das Log nennt nur IDs.
    assert db_admin.execute("select count(*) as n from event.einladung").fetchone()["n"] == 1
    fehler = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert fehler and str(f) in caplog.text
    assert "eva@example.org" not in caplog.text and "/einladung/" not in caplog.text


# --- Einladung ansehen (ohne Einloesen) -------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def test_einladung_info_gueltig_dann_eingeloest(db_admin: Any, db_app: Any) -> None:
    f, _, token = _einladen(db_app, db_admin)
    firma_name = db_admin.execute("select name from event.firma where id = %s",
                                  (f,)).fetchone()["name"]
    vorher = db_admin.execute("select row_to_json(e)::text as z from event.einladung e"
                              ).fetchall()
    info = zugang.einladung_info(db_app(), token)
    assert info == {"gueltig": True, "firma_name": firma_name, "braucht_passwort": True}
    # Nur lesen: die Einladung bleibt unveraendert und einloesbar.
    assert db_admin.execute("select row_to_json(e)::text as z from event.einladung e"
                            ).fetchall() == vorher
    zugang.einladung_einloesen(db_app(), token, PW, None)
    assert zugang.einladung_info(db_app(), token) == {"gueltig": False}


def test_einladung_info_person_mit_passwort(db_admin: Any, db_app: Any) -> None:
    _person(db_admin, "bert@example.org", pw=PW)
    _, _, token = _einladen(db_app, db_admin, mail="bert@example.org")
    info = zugang.einladung_info(db_app(), token)
    assert info["gueltig"] is True and info["braucht_passwort"] is False


def test_einladung_info_abgelaufen_oder_inaktiv(db_admin: Any, db_app: Any) -> None:
    _, _, abgelaufen = _einladen(db_app, db_admin)
    db_admin.execute("update event.einladung set gueltig_bis = now() - interval '1 second'"
                     " where token_hash = %s", (_hash(abgelaufen),))
    assert zugang.einladung_info(db_app(), abgelaufen) == {"gueltig": False}
    _, _, inaktiv = _einladen(db_app, db_admin, mail="carla@example.org")
    db_admin.execute("update event.person set aktiv = false where mail = 'carla@example.org'")
    assert zugang.einladung_info(db_app(), inaktiv) == {"gueltig": False}


@pytest.mark.parametrize("token", [
    "", "x", "unbekannt-" + "a" * 40, "\ud800", "a\x00b", "ä" * 5000, None, 123, b"bytes",
], ids=["leer", "kurz", "unbekannt", "surrogat", "nul", "lang", "none", "int", "bytes"])
def test_einladung_info_kaputte_tokens(db_admin: Any, db_app: Any, token: object) -> None:
    _einladen(db_app, db_admin)  # es gibt eine echte Einladung, sie passt nur nicht
    assert zugang.einladung_info(db_app(), token) == {"gueltig": False}  # type: ignore[arg-type]


# --- Anmelden --------------------------------------------------------------------


def test_anmelden_falsches_passwort_und_sperre(db_admin: Any, db_app: Any) -> None:
    p = _person(db_admin, "finn@example.org", pw=PW)
    _person(db_admin, "ohne-pw@example.org")
    _person(db_admin, "inaktiv@example.org", pw=PW, aktiv=False)
    conn = db_app()
    t0 = 1_000_000.0
    assert zugang.anmelden(conn, " Finn@Example.org ", PW, jetzt=t0) == p
    assert zugang.anmelden(conn, "finn@example.org", "falsch-falsch-1", jetzt=t0) is None
    assert zugang.anmelden(conn, "niemand@example.org", PW, jetzt=t0) is None
    assert zugang.anmelden(conn, "ohne-pw@example.org", "", jetzt=t0) is None
    assert zugang.anmelden(conn, "inaktiv@example.org", PW, jetzt=t0) is None
    # Erfolg setzt den Zaehler zurueck: 1 + 3 Fehlversuche, Erfolg, dann 5 -> gesperrt.
    for _ in range(3):
        assert zugang.anmelden(conn, "finn@example.org", "falsch", jetzt=t0) is None
    assert zugang.anmelden(conn, "finn@example.org", PW, jetzt=t0) == p
    for i in range(5):
        assert zugang.anmelden(conn, "finn@example.org", "falsch", jetzt=t0 + i) is None
    assert zugang.anmelden(conn, "FINN@example.org", PW, jetzt=t0 + 10) is None
    assert zugang.anmelden(conn, "finn@example.org", PW, jetzt=t0 + 15 * 60 - 1) is None
    # Andere Mail ist nicht betroffen.
    q = _person(db_admin, "gina@example.org", pw=PW)
    assert zugang.anmelden(conn, "gina@example.org", PW, jetzt=t0 + 10) == q
    # Nach Ablauf des Fensters (gerechnet ab dem ersten Fehlversuch) geht es wieder.
    assert zugang.anmelden(conn, "finn@example.org", PW, jetzt=t0 + 15 * 60 + 1) == p


def test_unbekannte_mail_rechnet_trotzdem_hash(
    db_admin: Any, db_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _person(db_admin, "ohne-pw@example.org")
    gesehen: list[str] = []
    echt = zugang.passwort_ok

    def spion(pw: str, h: str) -> bool:
        gesehen.append(h)
        return echt(pw, h)

    monkeypatch.setattr(zugang, "passwort_ok", spion)
    conn = db_app()
    assert zugang.anmelden(conn, "niemand@example.org", PW) is None
    assert zugang.anmelden(conn, "ohne-pw@example.org", PW) is None
    assert gesehen == [zugang._DUMMY_HASH, zugang._DUMMY_HASH]
    assert zugang._DUMMY_HASH.startswith(f"scrypt${2**14}$8$1$")


def test_sperre_auch_fuer_unbekannte_mail(db_app: Any) -> None:
    conn = db_app()
    for _ in range(5):
        assert zugang.anmelden(conn, "unbekannt@example.org", "x", jetzt=50.0) is None
    with zugang._sperre_lock:
        assert len(zugang._fehlversuche["unbekannt@example.org"]) == 5


# --- Cookie und CSRF -------------------------------------------------------------


def test_cookie_manipuliert_oder_abgelaufen(db_admin: Any, db_app: Any) -> None:
    p = _person(db_admin, "hans@example.org", pw=PW)
    conn = db_app()
    t0 = 2_000_000_000.0
    wert = zugang.cookie_ausstellen(p, 1, GEHEIM, jetzt=t0)
    pid, version, ablauf, sig = wert.split(".")
    assert (pid, version, ablauf) == (str(p), "1", str(int(t0) + 12 * 3600))
    assert "=" not in sig
    assert zugang.cookie_pruefen(wert, GEHEIM, conn, jetzt=t0 + 60) == p
    assert zugang.cookie_pruefen(wert, GEHEIM, conn, jetzt=t0 + 12 * 3600) is None
    assert zugang.cookie_pruefen(wert, b"anderes-geheimnis-0123456789abcdef", conn,
                                 jetzt=t0) is None
    andere = _person(db_admin, "ida@example.org")
    manipuliert = [
        f"{andere}.1.{ablauf}.{sig}",
        f"{pid}.2.{ablauf}.{sig}",
        f"{pid}.1.{int(ablauf) + 3600}.{sig}",
        f"{pid}.1.{ablauf}.{sig[:-1]}{'A' if sig[-1] != 'A' else 'B'}",
        f"{pid}.1.{ablauf}.",
        f"{pid}.1.{ablauf}.{sig}.x",
        "", "a.b", "a.b.c.d", "...", "ä.ö.ü.ß", f"{pid}.1.{ablauf}.ä",
    ]
    for w in manipuliert:
        assert zugang.cookie_pruefen(w, GEHEIM, conn, jetzt=t0) is None, w
    assert zugang.cookie_pruefen(None, GEHEIM, conn, jetzt=t0) is None  # type: ignore[arg-type]
    assert zugang.cookie_pruefen(wert, b"", conn, jetzt=t0) is None
    # Signiert, aber kaputter Inhalt: kein Fehler, nur None.
    for nutzlast in ["kein-uuid.1.99999999999", f"{p}.eins.99999999999", f"{p}.1.bald"]:
        kaputt = f"{nutzlast}.{zugang._signatur(GEHEIM, nutzlast)}"
        assert zugang.cookie_pruefen(kaputt, GEHEIM, conn, jetzt=t0) is None
    # Unbekannte Person und inaktive Person.
    fremd = zugang.cookie_ausstellen(uuid4(), 1, GEHEIM, jetzt=t0)
    assert zugang.cookie_pruefen(fremd, GEHEIM, conn, jetzt=t0) is None
    db_admin.execute("update event.person set aktiv = false where id = %s", (p,))
    assert zugang.cookie_pruefen(wert, GEHEIM, conn, jetzt=t0) is None
    with pytest.raises(ValueError):
        zugang.cookie_ausstellen(p, 1, b"")


def test_abmelden_ueberall_entwertet_cookie(db_admin: Any, db_app: Any) -> None:
    p = _person(db_admin, "jan@example.org", pw=PW)
    conn = db_app()
    alt = zugang.cookie_ausstellen(p, 1, GEHEIM)
    assert zugang.cookie_pruefen(alt, GEHEIM, conn) == p
    zugang.abmelden_ueberall(conn, p)
    assert zugang.cookie_pruefen(alt, GEHEIM, conn) is None
    assert db_admin.execute("select sitzungs_version from event.person where id = %s",
                            (p,)).fetchone()["sitzungs_version"] == 2
    neu = zugang.cookie_ausstellen(p, 2, GEHEIM)
    assert zugang.cookie_pruefen(neu, GEHEIM, conn) == p
    with pytest.raises(NichtGefunden):
        zugang.abmelden_ueberall(conn, uuid4())


def test_csrf() -> None:
    wert = zugang.cookie_ausstellen(uuid4(), 1, GEHEIM)
    t = zugang.csrf_token(wert, GEHEIM)
    assert t == zugang.csrf_token(wert, GEHEIM) and "=" not in t
    assert zugang.csrf_ok(wert, t, GEHEIM)
    assert not zugang.csrf_ok(wert, t[:-1], GEHEIM)
    assert not zugang.csrf_ok(wert + "x", t, GEHEIM)
    assert not zugang.csrf_ok(wert, t, b"anderes-geheimnis-0123456789abcdef")
    assert not zugang.csrf_ok(wert, zugang.csrf_token(wert + "x", GEHEIM), GEHEIM)
    for c, tok, g in [("", t, GEHEIM), (wert, "", GEHEIM), (wert, t, b""),
                      (None, t, GEHEIM), (wert, None, GEHEIM), (wert, "ä", GEHEIM)]:
        assert zugang.csrf_ok(c, tok, g) is False  # type: ignore[arg-type]


# --- Log ----------------------------------------------------------------------------


def test_kein_token_im_log(
    db_admin: Any, db_app: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    mail = "Kai@Example.org"
    f, m, token = _einladen(db_app, db_admin, mail=mail)
    p = zugang.einladung_einloesen(db_app(), token, PW, "Kai")
    conn = db_app()
    assert zugang.anmelden(conn, mail, PW) == p
    assert zugang.anmelden(conn, mail, "falsches-passwort-9") is None
    for _ in range(5):
        zugang.anmelden(conn, mail, "falsches-passwort-9")
    wert = zugang.cookie_ausstellen(p, 1, GEHEIM)
    assert zugang.cookie_pruefen(wert, GEHEIM, conn) == p
    assert zugang.cookie_pruefen(wert + "x", GEHEIM, conn) is None
    zugang.csrf_ok(wert, zugang.csrf_token(wert, GEHEIM), GEHEIM)
    zugang.abmelden_ueberall(conn, p)
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    assert any(r.name == zugang.__name__ for r in caplog.records)
    assert str(p) in text  # nur IDs
    for geheim in [token, PW, "falsches-passwort-9", mail, mail.lower(), wert,
                   wert.rsplit(".", 1)[1], "/einladung/", GEHEIM.decode()]:
        assert geheim not in text, geheim
    assert m.gesendet[0].an == mail.lower()


# --- Mailer -------------------------------------------------------------------------


_SMTP_ENV = {
    "EVENT_SMTP_HOST": "smtp.example.org",
    "EVENT_SMTP_PORT": "587",
    "EVENT_SMTP_USER": "versand@example.org",
    "EVENT_SMTP_ABSENDER": "Event-Management <versand@example.org>",
}


def _smtp_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    datei = tmp_path / "smtp_pw"
    datei.write_bytes(b"smtp-test-passwort\r\n")
    for k, v in _SMTP_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EVENT_SMTP_PASSWORT_FILE", str(datei))


def test_smtp_mailer_aus_umgebung(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _smtp_env(monkeypatch, tmp_path)
    m = mailer.SmtpMailer.aus_umgebung()
    assert m.host == "smtp.example.org" and m.port == 587
    assert "smtp-test-passwort" not in repr(m)
    for fehlt in [*_SMTP_ENV, "EVENT_SMTP_PASSWORT_FILE"]:
        monkeypatch.delenv(fehlt)
        with pytest.raises(ValueError) as e:
            mailer.SmtpMailer.aus_umgebung()
        assert fehlt in str(e.value) and "example.org" not in str(e.value)
        _smtp_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EVENT_SMTP_PORT", "kein-port")
    with pytest.raises(ValueError, match="EVENT_SMTP_PORT"):
        mailer.SmtpMailer.aus_umgebung()
    _smtp_env(monkeypatch, tmp_path)
    monkeypatch.setenv("EVENT_SMTP_PASSWORT_FILE", str(tmp_path / "gibt-es-nicht"))
    with pytest.raises(ValueError, match="EVENT_SMTP_PASSWORT_FILE") as e2:
        mailer.SmtpMailer.aus_umgebung()
    assert "gibt-es-nicht" not in str(e2.value)


def test_smtp_mailer_senden_mit_starttls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aufrufe: list[tuple[str, Any]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            aufrufe.append(("verbinden", (host, port)))

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *a: object) -> None:
            aufrufe.append(("ende", None))

        def starttls(self, context: ssl.SSLContext) -> None:
            aufrufe.append(("starttls", context))

        def login(self, user: str, passwort: str) -> None:
            aufrufe.append(("login", (user, passwort)))

        def send_message(self, msg: Any) -> None:
            aufrufe.append(("senden", msg))

    _smtp_env(monkeypatch, tmp_path)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mailer.SmtpMailer.aus_umgebung().senden("lea@example.org", "Betreff ä", "Hallo\nWelt")
    namen = [a[0] for a in aufrufe]
    assert namen == ["verbinden", "starttls", "login", "senden", "ende"]
    assert aufrufe[0][1] == ("smtp.example.org", 587)
    kontext = aufrufe[1][1]
    assert isinstance(kontext, ssl.SSLContext) and kontext.verify_mode == ssl.CERT_REQUIRED
    assert aufrufe[2][1] == ("versand@example.org", "smtp-test-passwort")
    msg = aufrufe[3][1]
    assert msg["To"] == "lea@example.org" and msg["Subject"] == "Betreff ä"
    assert msg["From"] == _SMTP_ENV["EVENT_SMTP_ABSENDER"]
    assert "Welt" in msg.get_content()


def test_speicher_mailer() -> None:
    m = mailer.SpeicherMailer()
    m.senden("a@example.org", "B", "T")
    assert [(x.an, x.betreff, x.text) for x in m.gesendet] == [("a@example.org", "B", "T")]
    ziel: mailer.Mailer = m
    assert ziel is m
