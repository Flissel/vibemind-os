#!/usr/bin/env python3
"""Erzeugt Integrationsvorlagen und die Abgleichsliste fuer den OpenAI-Katalog.

Liest die Recherche (`integrations/abgleich/recherche.json`, mit `pruefung`),
den gepinnten Katalog und die mitgelieferten OpenFang-Vorlagen. Schreibt je
einrichtbarem Anbieter eine Datei `<ziel>/<name>.toml` und die Abgleichsliste
`<ziel>/abgleich/README.md`.

Regeln (Teil 4 der Plugin-System-Reihe):
- Nur offizielle Server: Domain-Pruefung vor jeder Vorlage.
- Bestehende Dateien (github, vercel, alles mit verified = true oder ohne
  verified-Feld) werden nie ueberschrieben.
- Zweiter Lauf aendert nichts (kein Zeitstempel ausser `datum` der Pruefung).
- Keine Geheimnisse: Vorlagen tragen nur Tresor-Namen.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

# Zweistufige Endungen, bei denen die registrierbare Domain drei Labels hat.
ZWEISTUFIGE_ENDUNGEN = frozenset(
    {"co.uk", "com.au", "co.jp", "com.br", "co.in"}
)

# Bekannte Anbieter-Domainwechsel: Recherche-Name -> Domains, die zum selben
# Anbieter gehoeren (Server- und Quell-Domain muessen beide enthalten sein).
# Quellen auf GitHub binden an den Anbieter-Org-Pfad ("github.com/<org>",
# kleingeschrieben), nie an github.com insgesamt.
ANBIETER_DOMAINS: dict[str, frozenset[str]] = {
    # Hersteller-GitHub-Org cbinsights
    "cb-insights": frozenset({"cbinsights.com", "github.com/cbinsights"}),
    # Cube Cloud laeuft auf cubecloud.dev, Doku auf cube.dev
    "cube": frozenset({"cubecloud.dev", "cube.dev"}),
    # Docket: Server docketai.com, Hilfe docket.io
    "docket": frozenset({"docketai.com", "docket.io"}),
    # Help Scout: API-Host helpscout.net, Doku helpscout.com
    "help-scout": frozenset({"helpscout.net", "helpscout.com"}),
    # HighLevel: API-Host leadconnectorhq.com, Doku gohighlevel.com
    "highlevel": frozenset({"leadconnectorhq.com", "gohighlevel.com"}),
    # Neon: Umbenennung neon.tech -> neon.com
    "neon-postgres": frozenset({"neon.tech", "neon.com"}),
    # Omni: Server omniapp.co, Doku omni.co
    "omni-analytics": frozenset({"omniapp.co", "omni.co"}),
    # Responsive: frueherer Name RFPIO (rfpio.com)
    "responsive": frozenset({"rfpio.com", "responsive.io"}),
    # SkyWatch: skywatch.co und skywatch.com
    "skywatch": frozenset({"skywatch.co", "skywatch.com"}),
    # Hersteller-GitHub-Org Teamwork
    "teamwork-com": frozenset({"teamwork.com", "github.com/teamwork"}),
}

# Dateien, die nie angefasst werden.
GESCHUETZT = frozenset({"github", "vercel"})

ID_MUSTER = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

KATEGORIEN = frozenset(
    {
        "devtools", "productivity", "communication", "data", "cloud",
        "ai", "sales", "marketing", "finance", "design",
    }
)

ICON: dict[str, str] = {
    "devtools": "🛠️",
    "productivity": "📋",
    "communication": "💬",
    "data": "📊",
    "cloud": "☁️",
    "ai": "🤖",
    "sales": "🤝",
    "marketing": "📣",
    "finance": "💶",
    "design": "🎨",
}

# Kategorie je Recherche-Name; Rest = productivity.
KATEGORIE: dict[str, str] = {
    "actively": "sales",
    "aiera": "finance",
    "airtable": "productivity",
    "alation": "data",
    "alpaca": "finance",
    "amplitude": "data",
    "apollo": "sales",
    "asana": "productivity",
    "atlassian-rovo": "productivity",
    "attio": "sales",
    "base44": "devtools",
    "binance": "finance",
    "biorender": "design",
    "box": "productivity",
    "brand24": "marketing",
    "brex": "finance",
    "brighthire": "sales",
    "build-ios-apps": "devtools",
    "calendly": "productivity",
    "canva": "design",
    "carta-crm": "finance",
    "catalyst-by-zoho": "cloud",
    "cb-insights": "data",
    "channel99": "marketing",
    "chronograph-gp": "finance",
    "chronograph-lp": "finance",
    "circleback": "productivity",
    "clay": "sales",
    "clickup": "productivity",
    "close": "sales",
    "cloudflare": "cloud",
    "cloudinary": "design",
    "codex-security": "devtools",
    "cogedim": "productivity",
    "common-room": "sales",
    "conductor": "marketing",
    "convex": "devtools",
    "coupler-io": "data",
    "coveo": "data",
    "cube": "data",
    "daloopa": "finance",
    "datadog": "devtools",
    "datasite": "finance",
    "deepnote": "data",
    "demandbase": "sales",
    "digitalocean": "cloud",
    "dnb-finance-analytics": "finance",
    "docket": "sales",
    "docusign": "productivity",
    "domotz-preview": "cloud",
    "dovetail": "productivity",
    "dow-jones-factiva": "data",
    "egnyte": "productivity",
    "factset": "finance",
    "fal": "ai",
    "figma": "design",
    "finn": "productivity",
    "fireflies": "productivity",
    "fiscal-ai": "finance",
    "fyxer": "productivity",
    "glean": "productivity",
    "gmail": "communication",
    "google-calendar": "productivity",
    "google-drive": "productivity",
    "govtribe": "sales",
    "granola": "productivity",
    "happenstance": "sales",
    "hebbia": "finance",
    "help-scout": "communication",
    "hex": "data",
    "heygen": "design",
    "hg-insights": "sales",
    "highlevel": "marketing",
    "hostinger": "cloud",
    "hubspot": "sales",
    "hugging-face": "ai",
    "intercom": "communication",
    "jam": "devtools",
    "keybid-puls": "sales",
    "linear": "devtools",
    "lovable": "devtools",
    "lseg": "finance",
    "marcopolo": "data",
    "mem": "productivity",
    "meticulate": "finance",
    "midpage": "productivity",
    "mixpanel": "data",
    "monday-com": "productivity",
    "moody-s": "finance",
    "morningstar": "finance",
    "motherduck": "data",
    "mt-newswires": "finance",
    "myregistry-com": "productivity",
    "neon-postgres": "cloud",
    "netlify": "cloud",
    "network-solutions": "cloud",
    "notion": "productivity",
    "omni-analytics": "data",
    "openai-developers": "ai",
    "otter-ai": "productivity",
    "outlook-calendar": "productivity",
    "outlook-email": "communication",
    "outreach": "sales",
    "particl-market-research": "data",
    "picsart": "design",
    "pipedrive": "sales",
    "pitchbook": "finance",
    "policynote": "data",
    "posthog": "data",
    "pylon": "communication",
    "quartr": "finance",
    "quickbooks": "finance",
    "quicknode": "cloud",
    "ranked-ai": "marketing",
    "razorpay": "finance",
    "read-ai": "productivity",
    "readwise": "productivity",
    "replayio": "devtools",
    "replit": "devtools",
    "responsive": "sales",
    "rox": "sales",
    "s-p": "finance",
    "scite": "ai",
    "semrush": "marketing",
    "sendgrid": "communication",
    "setu-bharat-connect-billpay": "finance",
    "sharepoint": "productivity",
    "shopify": "sales",
    "shutterstock": "design",
    "signnow": "productivity",
    "similarweb": "marketing",
    "skywatch": "data",
    "slack": "communication",
    "statsig": "data",
    "streak": "sales",
    "stripe": "finance",
    "supabase": "cloud",
    "superhuman": "communication",
    "taxdown": "finance",
    "teams": "communication",
    "teamwork-com": "productivity",
    "third-bridge": "finance",
    "thoughtspot": "data",
    "tinman-ai": "ai",
    "united-rentals": "productivity",
    "vantage": "cloud",
    "waldo": "devtools",
    "weatherpromise": "data",
    "windsor-ai": "marketing",
    "wix": "marketing",
    "yepcode": "devtools",
    "zoho": "productivity",
    "zoom": "communication",
    "zoominfo": "sales",
}

STICHWORTE: dict[str, list[str]] = {
    "aiera": ["finanzdaten", "analysten"],
    "amplitude": ["analytics", "produktdaten"],
    "apollo": ["crm", "kontakte"],
    "atlassian-rovo": ["projekte", "dokumente"],
    "attio": ["crm", "kontakte"],
    "base44": ["app-bau", "ki"],
    "calendly": ["kalender", "termine"],
    "carta-crm": ["crm", "investoren"],
    "cb-insights": ["marktdaten", "analysen"],
    "circleback": ["meeting", "notizen"],
    "clickup": ["projekte", "aufgaben"],
    "cloudflare": ["hosting", "netzwerk"],
    "common-room": ["crm", "community"],
    "cube": ["analytics", "datenmodell"],
    "daloopa": ["finanzdaten", "analysten"],
    "deepnote": ["notebooks", "analytics"],
    "docket": ["vertrieb-wissen", "dokumente"],
    "egnyte": ["dokumente", "dateien"],
    "factset": ["finanzdaten", "marktdaten"],
    "fal": ["ki", "bilder"],
    "fireflies": ["meeting", "notizen"],
    "fiscal-ai": ["finanzdaten", "aktien"],
    "granola": ["meeting", "notizen"],
    "help-scout": ["support", "tickets"],
    "hex": ["analytics", "notebooks"],
    "heygen": ["video", "design"],
    "highlevel": ["crm", "automatisierung"],
    "hostinger": ["hosting", "domains"],
    "hugging-face": ["ki", "modelle"],
    "intercom": ["support", "chat"],
    "jam": ["fehlerberichte", "debugging"],
    "linear": ["tickets", "projekte"],
    "lovable": ["app-bau", "ki"],
    "marcopolo": ["datenbank", "suche"],
    "midpage": ["recht", "recherche"],
    "motherduck": ["datenbank", "analytics"],
    "neon-postgres": ["datenbank", "postgres"],
    "netlify": ["hosting", "webseiten"],
    "notion": ["notizen", "dokumente"],
    "omni-analytics": ["analytics", "berichte"],
    "openai-developers": ["dokumentation", "ki"],
    "outreach": ["crm", "vertriebsmails"],
    "particl-market-research": ["marktforschung", "analytics"],
    "picsart": ["design", "bilder"],
    "posthog": ["analytics", "produktdaten"],
    "pylon": ["support", "tickets"],
    "quartr": ["finanzdaten", "quartalszahlen"],
    "quicknode": ["blockchain", "knoten"],
    "ranked-ai": ["seo", "werbung"],
    "readwise": ["notizen", "lesen"],
    "replit": ["entwicklung", "hosting"],
    "responsive": ["ausschreibungen", "antworten"],
    "scite": ["recherche", "studien"],
    "signnow": ["unterschriften", "dokumente"],
    "skywatch": ["satellitenbilder", "geodaten"],
    "statsig": ["experimente", "analytics"],
    "supabase": ["datenbank", "backend"],
    "superhuman": ["mail", "postfach"],
    "teamwork-com": ["projekte", "aufgaben"],
    "windsor-ai": ["marketing-daten", "analytics"],
    "wix": ["webseiten", "shop"],
    "yepcode": ["automatisierung", "skripte"],
}

TAG_JE_KATEGORIE: dict[str, str] = {
    "devtools": "entwicklung",
    "productivity": "produktivitaet",
    "communication": "kommunikation",
    "data": "daten",
    "cloud": "cloud",
    "ai": "ki",
    "sales": "vertrieb",
    "marketing": "marketing",
    "finance": "finanzen",
    "design": "design",
}


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def registrierbare_domain(host: str) -> str:
    """Letzte zwei Labels, drei bei zweistufigen Endungen (co.uk, ...)."""
    host = host.strip().lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    labels = [l for l in host.split(".") if l]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in ZWEISTUFIGE_ENDUNGEN:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_ok(zeile: dict[str, Any]) -> bool:
    """Server-Domain gleich Quell-Domain oder beide in ANBIETER_DOMAINS[name]."""
    server = registrierbare_domain(_host(str(zeile.get("server_url", ""))))
    quelle_url = str(zeile.get("quelle", ""))
    quelle = registrierbare_domain(_host(quelle_url))
    if quelle == "github.com":
        org = urlparse(quelle_url).path.strip("/").split("/")[0].lower()
        quelle = f"github.com/{org}" if org else quelle
    if not server or not quelle:
        return False
    if server == quelle:
        return True
    erlaubt = ANBIETER_DOMAINS.get(str(zeile.get("name", "")), frozenset())
    return server in erlaubt and quelle in erlaubt


def toml_str(wert: str) -> str:
    """TOML-Basisstring: `\\`, `"` und Steuerzeichen escapen."""
    aus = ['"']
    for ch in wert:
        if ch == "\\":
            aus.append("\\\\")
        elif ch == '"':
            aus.append('\\"')
        elif ch == "\n":
            aus.append("\\n")
        elif ch == "\r":
            aus.append("\\r")
        elif ch == "\t":
            aus.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            aus.append(f"\\u{ord(ch):04x}")
        else:
            aus.append(ch)
    aus.append('"')
    return "".join(aus)


def _einzeilig(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()


def tresor_name(name: str) -> str:
    return "INTEGRATION_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_KEY"


def _kategorie(name: str) -> str:
    return KATEGORIE.get(name, "productivity")


def _tags(name: str, kategorie: str) -> list[str]:
    """Kategorie-Wort zuerst, dann bis zu zwei Stichworte; nie die id."""
    tags: list[str] = []
    for t in [TAG_JE_KATEGORIE.get(kategorie, kategorie), *STICHWORTE.get(name, [])]:
        if t and t != name and t not in tags:
            tags.append(t)
    return tags[:3]


# ---------------------------------------------------------------------------
# Vorlage
# ---------------------------------------------------------------------------

def vorlage_toml(zeile: dict[str, Any]) -> str:
    """TOML-Text der Vorlage fuer eine Recherche-Zeile mit `einrichtbar_*`."""
    name = str(zeile["name"])
    anzeige = str(zeile.get("anzeigename") or name)
    pruefung = zeile.get("pruefung") or {}
    ergebnis = pruefung.get("ergebnis", "")
    kategorie = _kategorie(name)
    transport = zeile.get("transport") or "http"
    url = str(zeile["server_url"])

    z: list[str] = []
    z.append(
        "# Erzeugt von scripts/katalog_vorlagen.py - Quelle: "
        f"{_einzeilig(str(zeile.get('quelle', '')))} - geprueft: "
        f"{_einzeilig(str(pruefung.get('datum', '')))}"
    )
    z.append(f"id = {toml_str(name)}")
    z.append(f"name = {toml_str(anzeige)}")
    z.append(
        "description = "
        + toml_str(f"{anzeige} ueber den offiziellen Remote-MCP-Server")
    )
    z.append(f"category = {toml_str(kategorie)}")
    z.append(f"icon = {toml_str(ICON.get(kategorie, ICON['productivity']))}")
    z.append(
        "tags = [" + ", ".join(toml_str(t) for t in _tags(name, kategorie)) + "]"
    )
    z.append("read_only_tools = []")
    z.append("")
    z.append("[transport]")
    z.append(f"type = {toml_str(transport)}")
    z.append(f"url = {toml_str(url)}")
    z.append("")

    if ergebnis == "einrichtbar_oauth":
        scopes = (
            ["offline_access"]
            if "offline_access" in (pruefung.get("scopes_supported") or [])
            else []
        )
        z.append("[auth]")
        z.append('type = "oauth"')
        z.append("scopes = [" + ", ".join(toml_str(s) for s in scopes) + "]")
        z.append("")
    elif ergebnis == "einrichtbar_schluessel":
        env = tresor_name(name)
        header = str(zeile.get("header_name") or "Authorization")
        fmt = str(zeile.get("header_format") or "Bearer {credential}")
        z.append("[[auth_headers]]")
        z.append(f"name = {toml_str(header)}")
        z.append(f"format = {toml_str(fmt)}")
        z.append(f"credential = {toml_str(env)}")
        z.append("")
        z.append("[[required_env]]")
        z.append(f"name = {toml_str(env)}")
        z.append(f"label = {toml_str(anzeige + ' API-Schluessel')}")
        z.append(
            "help = "
            + toml_str(
                "Schluessel auf der Seite des Anbieters anlegen; "
                "er wird im Tresor abgelegt"
            )
        )
        z.append("is_secret = true")
        link = str(zeile.get("schluessel_link") or "")
        if link:
            z.append(f"get_url = {toml_str(link)}")
        z.append("")
    # einrichtbar_ohne_anmeldung: weder [auth] noch auth_headers

    z.append("[catalog]")
    z.append(f"replaces_openai_plugin = {toml_str(name)}")
    z.append('license = "proprietary-service"')
    z.append('admission = "admitted"')
    z.append("verified = false")
    return "\n".join(z) + "\n"


# ---------------------------------------------------------------------------
# Abgleich
# ---------------------------------------------------------------------------

def _eintraege(katalog: Any) -> list[dict[str, Any]]:
    if isinstance(katalog, dict):
        katalog = katalog.get("entries", [])
    return list(katalog)


def _sorte(eintrag: dict[str, Any]) -> str:
    arten = {
        c.get("component", {}).get("kind") for c in eintrag.get("components", [])
    }
    if "mcp" in arten:
        return "mcp"
    if "app" in arten:
        return "app"
    return "skills"


def _ids_mitgeliefert(mitgeliefert: Any) -> set[str]:
    if mitgeliefert is None:
        return set()
    if isinstance(mitgeliefert, (str, Path)):
        p = Path(mitgeliefert)
        return {f.stem for f in p.glob("*.toml")} if p.is_dir() else set()
    return set(mitgeliefert)


def abgleich(
    recherche: Iterable[dict[str, Any]],
    katalog: Any,
    mitgeliefert: Any,
    bestand: Iterable[str] = (),
) -> list[dict[str, str]]:
    """Eine Zeile je Katalogeintrag, nach Name sortiert."""
    zeilen_je_name = {str(z["name"]): z for z in recherche}
    ids_mit = _ids_mitgeliefert(mitgeliefert)
    fest = set(bestand) | GESCHUETZT
    aus: list[dict[str, str]] = []

    for eintrag in _eintraege(katalog):
        name = str(eintrag["name"])
        sorte = _sorte(eintrag)
        zeile = zeilen_je_name.get(name)
        quelle = str(zeile.get("quelle", "")) if zeile else ""
        pruefung = (zeile or {}).get("pruefung") or {}
        datum = str(pruefung.get("datum", ""))

        def eintrag_aus(ergebnis: str, grund: str) -> dict[str, str]:
            return {
                "name": name, "sorte": sorte, "ergebnis": ergebnis,
                "grund": grund, "quelle": quelle, "datum": datum,
            }

        if name in fest:
            aus.append(eintrag_aus("vorlage_bestand", "Vorlage liegt schon vor"))
            continue
        if sorte == "skills":
            aus.append(eintrag_aus("skills_teil_5", "nur Skills/Befehle/Agenten"))
            continue
        if zeile is None:
            aus.append(
                eintrag_aus("kein_offizieller_server", "nicht recherchiert")
            )
            continue

        url = str(zeile.get("server_url", ""))
        probe = str(pruefung.get("ergebnis", ""))
        klasse = str(pruefung.get("klasse", ""))
        einrichtbar = probe.startswith("einrichtbar_") and "{" not in url

        if not einrichtbar and name in ids_mit:
            aus.append(
                eintrag_aus("vorlage_mitgeliefert", "mitgelieferte Vorlage bleibt")
            )
            continue
        if not url:
            aus.append(
                eintrag_aus(
                    "kein_offizieller_server",
                    _einzeilig(str(zeile.get("notiz", ""))) or "kein Server gefunden",
                )
            )
            continue
        if "{" in url:
            aus.append(
                eintrag_aus("adresse_je_kunde", klasse or "adresse_je_kunde")
            )
            continue
        if not probe:
            aus.append(eintrag_aus("unklar", "nicht geprueft"))
            continue
        if not einrichtbar:
            ergebnis = probe if probe in {
                "braucht_registrierung", "nicht_erreichbar", "unklar",
                "adresse_je_kunde",
            } else "unklar"
            aus.append(eintrag_aus(ergebnis, klasse))
            continue
        if not ID_MUSTER.match(name):
            aus.append(eintrag_aus("id_ungueltig", "id passt nicht zum Format"))
            continue
        if not domain_ok(zeile):
            aus.append(
                eintrag_aus(
                    "domain_pruefen",
                    f"Server {registrierbare_domain(_host(url))} "
                    f"gegen Quelle {registrierbare_domain(_host(quelle))}",
                )
            )
            continue
        aus.append(eintrag_aus("vorlage", probe))

    aus.sort(key=lambda r: r["name"])
    return aus


def _zelle(text: str) -> str:
    return _einzeilig(text).replace("|", "\\|")


def abgleich_markdown(zeilen: list[dict[str, str]]) -> str:
    zaehler = Counter(z["ergebnis"] for z in zeilen)
    z: list[str] = [
        "# Abgleich OpenAI-Katalog gegen VibeMind-Vorlagen",
        "",
        "Erzeugt von `scripts/katalog_vorlagen.py` aus `recherche.json` und dem "
        "gepinnten Katalog. Nicht von Hand bearbeiten.",
        "",
        f"Eintraege gesamt: {len(zeilen)}",
        "",
        "| Ergebnis | Anzahl |",
        "|---|---|",
    ]
    for ergebnis in sorted(zaehler):
        z.append(f"| {ergebnis} | {zaehler[ergebnis]} |")
    z += [
        "",
        "| Name | Sorte | Ergebnis | Grund/Klasse | Quelle | Pruefdatum |",
        "|---|---|---|---|---|---|",
    ]
    for r in zeilen:
        z.append(
            "| " + " | ".join(
                _zelle(r[k])
                for k in ("name", "sorte", "ergebnis", "grund", "quelle", "datum")
            ) + " |"
        )
    return "\n".join(z) + "\n"


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------

def _bestehende(ziel: Path) -> set[str]:
    """Dateien, die nie ueberschrieben werden: verified = true, ohne verified
    oder nicht lesbar."""
    fest: set[str] = set()
    for f in ziel.glob("*.toml"):
        try:
            daten = tomllib.loads(f.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            fest.add(f.stem)
            continue
        katalog = daten.get("catalog")
        if not isinstance(katalog, dict) or katalog.get("verified") is not False:
            fest.add(f.stem)
    return fest


def lauf(
    recherche_pfad: Path, katalog_pfad: Path, mitgeliefert: Path, ziel: Path
) -> tuple[list[dict[str, str]], list[str]]:
    recherche = json.loads(recherche_pfad.read_text(encoding="utf-8"))
    katalog = json.loads(katalog_pfad.read_text(encoding="utf-8"))
    ziel.mkdir(parents=True, exist_ok=True)
    zeilen = abgleich(recherche, katalog, mitgeliefert, bestand=_bestehende(ziel))
    je_name = {str(r["name"]): r for r in recherche}
    fehler: list[str] = []
    for r in zeilen:
        if r["ergebnis"] == "id_ungueltig":
            fehler.append(f"{r['name']}: id passt nicht zu ^[a-z0-9][a-z0-9-]{{0,62}}$")
        elif r["ergebnis"] == "vorlage":
            text = vorlage_toml(je_name[r["name"]])
            with open(ziel / f"{r['name']}.toml", "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    (ziel / "abgleich").mkdir(parents=True, exist_ok=True)
    with open(ziel / "abgleich" / "README.md", "w", encoding="utf-8", newline="\n") as f:
        f.write(abgleich_markdown(zeilen))
    return zeilen, fehler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recherche", type=Path, required=True)
    ap.add_argument("--katalog", type=Path, required=True)
    ap.add_argument("--mitgeliefert", type=Path, required=True)
    ap.add_argument("--ziel", type=Path, required=True)
    a = ap.parse_args(argv)
    zeilen, fehler = lauf(a.recherche, a.katalog, a.mitgeliefert, a.ziel)
    for ergebnis, n in sorted(Counter(z["ergebnis"] for z in zeilen).items()):
        print(f"{ergebnis}: {n}")
    for f in fehler:
        print(f"FEHLER {f}", file=sys.stderr)
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
