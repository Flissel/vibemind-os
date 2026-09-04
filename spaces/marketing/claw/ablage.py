"""Ablage fuer erzeugte Beitraege — schreibt NUR nach /media-erzeugt.

DIE ZWEI ORDNER SIND EINE GRENZE, KEINE ORDNUNGSFRAGE. `/media` gehoert
dem Menschen: bei sales-claw als `./media:/media:ro` eingehaengt, also fuer
jeden Dienst schreibgeschuetzt — weder Werkzeugdienst noch Dispatcher
koennen dort je etwas anfassen. Maschinell Erzeugtes geht nach
`/media-erzeugt` (`sales-mcp/medien.py:45-49`). sales-claw liest BEIDE
Ordner, in dieser Rangfolge; was hier landet, findet der Vertrieb also,
ohne dass ein Agent je in die Ablage eines Menschen schreibt.

Dieses Modul kennt deshalb nur EINE Wurzel — die fuer Erzeugtes. Es gibt
hier bewusst keine Funktion, die `MEDIA_DIR` aufloest: was es nicht gibt,
kann auch kein spaeterer Aufrufer versehentlich benutzen.

ZWEI ORTE, EINE VARIABLE. sales-claw laeuft auf der VM, dieser Sidecar auf
dem Host — ein Beitrag, der nur auf dem Windows-Rechner landet, erreicht
den Vertrieb nie. Ist `MEDIA_ERZEUGT_SSH_HOST` gesetzt, reist die Datei
per SSH dorthin; ist sie leer, wird lokal geschrieben. Das Muster ist
woertlich das von `sync/_db.py`: gleiche Oberflaeche, Auswahl zur
Aufrufzeit, und das Umschalten ist durch Loeschen der Variable
vollstaendig rueckgaengig zu machen. Der INHALT reist auf stdin, nie auf
einer Kommandozeile — dort stuende er im Prozessverzeichnis der VM.

Die Namenspruefung folgt `medien.py:100-113`: geprueft wird, BEVOR
irgendetwas das Dateisystem beruehrt.
"""
import os
import re
import subprocess
import time

# Nur der Ordner fuer Erzeugtes. Ueberschreibbar fuer Tests und einen
# abweichenden Mount — aber es gibt keinen Weg, hier /media einzutragen,
# der nicht als solcher sichtbar waere.
ERZEUGT_VERZEICHNIS = "/media-erzeugt"

# Was ein Agent schreiben darf. Kein ps1, kein exe, kein sh: der Ordner wird
# von einem Menschen geoeffnet, und eine ausfuehrbare Datei aus einer
# Maschine hat darin nichts zu suchen.
# Textarten (fuer den Betreiber lesbar) und Binaerarten (was sales-claw
# anhaengen kann). Die Trennung ist keine Kosmetik: `medien.pruefe` laesst nur
# .pdf/.jpg/.jpeg/.png/.mp3/.ogg/.mp4/.ics durch — eine .md liegt im richtigen
# Ordner und ist trotzdem unanhaengbar (gemessen 04.09.2026).
ARTEN = {"md": ".md", "html": ".html", "txt": ".txt", "json": ".json",
         "pdf": ".pdf", "png": ".png", "jpg": ".jpg", "mp4": ".mp4"}

# Was sales-claw wirklich anhaengen kann (medien.py:57-79).
ANHAENGBAR = {".pdf", ".jpg", ".jpeg", ".png", ".mp3", ".ogg", ".mp4", ".ics"}

MAX_ZEICHEN = 2_000_000


SSH_ZEITLIMIT = 30


def ferner_rechner() -> str:
    """Der SSH-Host, wenn ferne Ablage gewollt ist — sonst leer."""
    return os.environ.get("MEDIA_ERZEUGT_SSH_HOST", "").strip()


def _ssh_schreiben(host: str, pfad: str, inhalt: str) -> None:
    """Legt `inhalt` als `pfad` auf `host` ab. Wirft bei Fehlschlag.

    Der Inhalt geht auf stdin. Auf der Kommandozeile steht nur der Pfad —
    und der ist ein gesaeuberter Name unter einem konfigurierten Ordner,
    kein Aufrufertext. Alles andere stuende im Prozessverzeichnis der VM
    und in deren Shell-Historie.
    """
    ordner = os.path.dirname(pfad)
    befehl = f"mkdir -p '{ordner}' && cat > '{pfad}'"
    nutzlast = inhalt if isinstance(inhalt, (bytes, bytearray)) \
        else inhalt.encode("utf-8")
    ergebnis = subprocess.run(
        ["ssh", host, befehl], input=nutzlast,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=SSH_ZEITLIMIT, check=False)
    if ergebnis.returncode != 0:
        meldung = ergebnis.stderr.decode("utf-8", "replace").strip()[:200]
        raise OSError(f"ssh {host}: {meldung or 'Rueckgabewert ' + str(ergebnis.returncode)}")


def wurzel() -> str:
    """Aufgeloester Ordner fuer Erzeugtes. Zur Aufrufzeit gelesen."""
    return os.path.realpath(os.environ.get("MEDIA_ERZEUGT_DIR",
                                           ERZEUGT_VERZEICHNIS))


def _name_ist_gut(name: str) -> bool:
    """Muster aus `medien.py`: gegen beide Trennzeichen, den Windows-
    Doppelpunkt und das NUL-Byte, bevor das Dateisystem beruehrt wird."""
    if not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or ":" in name:
        return False
    if "\x00" in name:
        # Wuerde sonst erst in realpath() als ValueError platzen — dieses
        # Modul verspricht aber eine Meldung, keine Ausnahme.
        return False
    if name.startswith("."):
        return False
    return name == os.path.basename(name)


def _saeubern(name: str) -> str:
    """Ein Titel wird zum Dateinamen: Kleinbuchstaben, Bindestriche."""
    kurz = re.sub(r"[^\wäöüßÄÖÜ.-]+", "-", name.strip().lower())
    return re.sub(r"-{2,}", "-", kurz).strip("-")[:80]


def ablegen(name, inhalt, art: str = "md") -> dict:
    """Schreibt `inhalt` nach /media-erzeugt. Wirft nie.

    `inhalt` ist Text ODER Bytes. Ein PDF durch eine Textkodierung zu
    schicken zerstoert es — deshalb reisen Bytes unveraendert, auch ueber
    SSH.

    Ueberschreibt NICHTS: liegt der Name schon da, bekommt der neue eine
    Zeitmarke. Ein Entwurf, der einen aelteren still ersetzt, ist ein
    verlorener Entwurf.
    """
    endung = ARTEN.get((art or "").strip().lower())
    if endung is None:
        return {"ok": False, "fehler":
                f"Art '{art}' ist nicht vorgesehen. Erlaubt: "
                + ", ".join(sorted(ARTEN))}
    binaer = isinstance(inhalt, (bytes, bytearray))
    if not inhalt or (not binaer and not inhalt.strip()):
        return {"ok": False, "fehler": "Ohne Inhalt wird nichts abgelegt."}
    if not binaer and len(inhalt) > MAX_ZEICHEN:
        return {"ok": False, "fehler":
                f"Inhalt zu gross ({len(inhalt)} Zeichen, erlaubt {MAX_ZEICHEN})."}

    roh = (name or "").strip()
    if roh.lower().endswith(endung):
        roh = roh[: -len(endung)]
    sauber = _saeubern(roh)
    if not sauber or not _name_ist_gut(sauber + endung):
        return {"ok": False, "fehler":
                f"'{name}' ergibt keinen brauchbaren Dateinamen."}

    dateiname = sauber + endung

    host = ferner_rechner()
    if host:
        # Ferner Ordner: NICHT realpath — der Pfad gehoert der VM, nicht
        # diesem Rechner. Der Schutz liegt vollstaendig im Namen, der oben
        # schon auf ein einziges Segment gesaeubert und geprueft wurde.
        ordner = os.environ.get("MEDIA_ERZEUGT_DIR", ERZEUGT_VERZEICHNIS).rstrip("/")
        ziel = f"{ordner}/{dateiname}"
        try:
            _ssh_schreiben(host, ziel, inhalt)
        except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
            return {"ok": False, "fehler": f"Ferne Ablage fehlgeschlagen "
                                           f"({type(e).__name__}: {e})"}
        return {"ok": True, "daten": {"pfad": f"{host}:{ziel}"}}

    ordner = wurzel()
    ziel = os.path.realpath(os.path.join(ordner, dateiname))
    # Der eigentliche Riegel: nach dem Aufloesen MUSS das Ziel unter der
    # Wurzel liegen. Ein Name, der das verletzt, kam gar nicht erst durch
    # `_name_ist_gut` — die Pruefung bleibt trotzdem, weil sie die einzige
    # ist, die auch Verweise (Symlinks) im Ordner selbst abfaengt.
    if not (ziel == ordner or ziel.startswith(ordner + os.sep)):
        return {"ok": False, "fehler": "Ziel liegt ausserhalb der Ablage."}

    try:
        os.makedirs(ordner, exist_ok=True)
        if os.path.exists(ziel):
            marke = time.strftime("%H%M%S")
            ziel = os.path.join(ordner, f"{sauber}-{marke}{endung}")
        if binaer:
            with open(ziel, "wb") as datei:
                datei.write(inhalt)
        else:
            with open(ziel, "w", encoding="utf-8", newline="\n") as datei:
                datei.write(inhalt)
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": f"Ablegen fehlgeschlagen "
                                       f"({type(e).__name__}: {e})"}
    return {"ok": True, "daten": {"pfad": ziel}}
