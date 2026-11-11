"""Ermittlung der 'unklar'-Anbieter Schritt fuer Schritt, mit Notiz jeder
verletzten OpenFang-Regel, danach Registrierungsversuch (gelockert).

Gibt keine client_id, kein Secret und keine Seitentexte aus, nur Klassen,
Hosts und HTTP-Status. Aufruf:
  python -I ermittlung_locker.py <recherche.json> <abgleich-README.md> <aus.json> [--redirect-https BASIS] [id ...]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20
UA = "OpenFang-Pruefung/1.0"


def http(method: str, url: str, body: dict | None = None, accept: str = "application/json"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: ANN002
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.headers), r.read(200_000)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(200_000)
        except Exception:  # noqa: BLE001
            raw = b""
        return e.code, dict(e.headers or {}), raw
    except Exception as e:  # noqa: BLE001
        return None, {}, type(e).__name__.encode()


def get_json(url: str):
    st, _, raw = http("GET", url)
    if st != 200:
        return None, st
    try:
        return json.loads(raw), st
    except Exception:  # noqa: BLE001
        return None, st


def host(u: str) -> str:
    return (urllib.parse.urlsplit(u).hostname or "").lower()


def norm(u: str) -> str:
    return u.rstrip("/").lower()


def registrable(h: str) -> str:
    parts = h.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def pruefe(iid: str, url: str, redirect_basis: str | None) -> dict:
    out: dict = {"id": iid, "server_url": url, "regeln_verletzt": []}
    probe = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "OpenFang", "version": "0"}}}
    st, hdr, _ = http("POST", url, probe, "application/json, text/event-stream")
    out["initialize_status"] = st
    if st in (301, 302, 303, 307, 308):
        out["weiterleitung_nach"] = host(hdr.get("Location", "") or hdr.get("location", ""))
        out["regeln_verletzt"].append("weiterleitung")
    www = " ".join(v for k, v in hdr.items() if k.lower() == "www-authenticate")
    m = re.search(r'resource_metadata="([^"]+)"', www)
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    prm = None
    kandidaten = []
    if m:
        kandidaten.append(m.group(1))
        if host(m.group(1)) != host(url):
            out["regeln_verletzt"].append("schutzbeschreibung fremder host")
    if path:
        kandidaten.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    kandidaten.append(f"{origin}/.well-known/oauth-protected-resource")
    for k in kandidaten:
        prm, _ = get_json(k)
        if prm:
            out["schutzbeschreibung"] = k.replace(origin, "") or k
            break
    if not prm:
        out["regeln_verletzt"].append("schutzbeschreibung nicht lesbar")
        out["klasse"] = "keine_schutzbeschreibung"
        return out
    res = str(prm.get("resource", ""))
    out["resource_gemeldet"] = res
    if norm(res) not in (norm(url), norm(origin)):
        out["regeln_verletzt"].append("resource passt nicht")
    aus = prm.get("authorization_servers") or []
    if not aus:
        out["klasse"] = "kein_anmeldeserver"
        return out
    as_url = str(aus[0]).rstrip("/")
    out["anmeldeserver"] = as_url
    meta = None
    pu = urllib.parse.urlsplit(as_url)
    as_origin = f"{pu.scheme}://{pu.netloc}"
    as_path = pu.path.rstrip("/")
    for k in (f"{as_url}/.well-known/oauth-authorization-server",
              f"{as_url}/.well-known/openid-configuration",
              f"{as_origin}/.well-known/oauth-authorization-server{as_path}",
              f"{as_origin}/.well-known/openid-configuration{as_path}"):
        meta, _ = get_json(k)
        if meta:
            if not k.startswith(as_url + "/.well-known"):
                out["regeln_verletzt"].append("anmeldeserver-metadaten nur mit pfad-einschub")
            break
    if not meta:
        out["regeln_verletzt"].append("anmeldeserver-metadaten nicht lesbar")
        out["klasse"] = "keine_metadaten"
        return out
    iss = str(meta.get("issuer", "")).rstrip("/")
    if iss != as_url:
        out["regeln_verletzt"].append("issuer passt nicht")
        out["issuer"] = iss
    ih = host(iss or as_url)
    eps = {k: meta.get(k) for k in ("authorization_endpoint", "token_endpoint",
                                     "registration_endpoint", "revocation_endpoint") if meta.get(k)}
    fremd = {k: host(v) for k, v in eps.items() if not (host(v) == ih or host(v).endswith("." + ih))}
    if fremd:
        out["regeln_verletzt"].append("fremder host")
        out["fremde_hosts"] = fremd
        out["gleiche_registrierbare_domain"] = all(registrable(h) == registrable(ih) for h in fremd.values())
    out["s256"] = "S256" in (meta.get("code_challenge_methods_supported") or [])
    reg = meta.get("registration_endpoint")
    if not reg:
        out["klasse"] = "ohne_selbstregistrierung"
        return out
    redirect = (f"{redirect_basis.rstrip('/')}/api/integrations/{iid}/oauth/callback"
                if redirect_basis else f"http://127.0.0.1:4200/api/integrations/{iid}/oauth/callback")
    body = {"client_name": "OpenFang VibeMind", "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}
    sc = meta.get("scopes_supported") or []
    if "offline_access" in sc:
        body["scope"] = "offline_access"
    st, _, raw = http("POST", reg, body)
    out["registrierung_status"] = st
    if st in (200, 201):
        try:
            hat_id = bool(json.loads(raw).get("client_id"))
        except Exception:  # noqa: BLE001
            hat_id = False
        out["klasse"] = "registrierung_ok" if hat_id else "registrierung_ohne_id"
    else:
        try:
            err = json.loads(raw)
            out["registrierung_fehler"] = str(err.get("error", ""))[:60]
            out["registrierung_text"] = str(err.get("error_description", ""))[:160]
        except Exception:  # noqa: BLE001
            pass
        out["klasse"] = "registrierung_abgelehnt"
    return out


def main() -> None:
    rech, readme, aus = sys.argv[1:4]
    rest = sys.argv[4:]
    redirect_basis = None
    if rest and rest[0] == "--redirect-https":
        redirect_basis = rest[1]
        rest = rest[2:]
    urls = {e["name"]: e.get("server_url", "") for e in json.load(open(rech, encoding="utf-8"))}
    if rest:
        ids = rest
    else:
        ids = [m.group(1) for m in re.finditer(r"^\| ([a-z0-9-]+) \| [a-z]+ \| unklar \|",
                                                open(readme, encoding="utf-8").read(), re.M)]
    ergebnisse = []
    for iid in ids:
        url = urls.get(iid, "")
        if not url:
            ergebnisse.append({"id": iid, "klasse": "ohne_url"})
            continue
        r = pruefe(iid, url, redirect_basis)
        ergebnisse.append(r)
        print(f"{iid:28} {r.get('klasse',''):26} {','.join(r['regeln_verletzt'])}", flush=True)
    json.dump(ergebnisse, open(aus, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
