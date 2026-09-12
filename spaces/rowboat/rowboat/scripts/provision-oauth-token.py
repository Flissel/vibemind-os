#!/usr/bin/env python3
"""Provision an OAuth bearer token for an MCP server into an env file.

Automates every step of the MCP authorization flow EXCEPT the one that is
the account owner's by design -- the login/consent click in the browser:

  1. RFC 9728 protected-resource discovery on the MCP URL
  2. RFC 8414 / OIDC authorization-server metadata
  3. RFC 7591 dynamic client registration (public client, PKCE only)
  4. Authorization-code flow with PKCE (S256) and an RFC 8707 resource
     indicator, catching the redirect on a localhost callback
  5. Token exchange, then appending `<NAME>=<token>` to --out

The token value is never printed and never leaves this process except into
the --out file. The reference NAME is derived from the resource URL with the
same rule Rowboat's OpenFangCredentialResolver uses (OAUTH_BEARER_ + host
and path, uppercased, non-alphanumeric runs collapsed to one underscore), so
the file line is exactly what OpenFang's dotenv chain needs; allowlist the
same name in OPENFANG_ISSUABLE_CREDENTIALS.

Usage:
  python scripts/provision-oauth-token.py https://mcp.notion.com/mcp --out /path/secrets.env
  python scripts/provision-oauth-token.py https://mcp.linear.app/mcp --dry-run

Stdlib only. --dry-run stops after printing the authorization URL (proves
discovery + registration without opening a browser).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

CALLBACK_PORT = 8976
REDIRECT_URI = f"http://127.0.0.1:{CALLBACK_PORT}/callback"
USER_AGENT = "vibemind-openfang-provisioner/1.0"

# Some providers (Linear) chain through roots missing from older Python
# trust stores; prefer certifi when it is importable, else the default.
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()
OPENFANG_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def derive_name(resource_url: str) -> str:
    parsed = urllib.parse.urlparse(resource_url)
    stem = re.sub(r"[^A-Z0-9]+", "_", f"{parsed.netloc}{parsed.path}".upper()).strip("_")
    name = f"OAUTH_BEARER_{stem}"
    if not OPENFANG_REFERENCE.match(name):
        raise SystemExit(f"derived name {name!r} is not a valid OpenFang reference")
    return name


def get_json(url: str) -> dict | None:
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None


def discover_resource_metadata(mcp_url: str) -> dict:
    parsed = urllib.parse.urlparse(mcp_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{origin}/.well-known/oauth-protected-resource{parsed.path}",
        f"{origin}/.well-known/oauth-protected-resource",
    ]
    # An unauthenticated POST also advertises the metadata URL in
    # WWW-Authenticate (that is how the MCP spec says clients find it).
    probe = urllib.request.Request(
        mcp_url,
        data=b"{}",
        method="POST",
        headers={"content-type": "application/json", "accept": "application/json, text/event-stream", "user-agent": USER_AGENT},
    )
    try:
        urllib.request.urlopen(probe, timeout=15, context=SSL_CONTEXT)
    except urllib.error.HTTPError as error:
        challenge = error.headers.get("www-authenticate", "")
        match = re.search(r'resource_metadata="([^"]+)"', challenge)
        if match:
            candidates.insert(0, match.group(1))
    except urllib.error.URLError:
        pass
    for candidate in candidates:
        metadata = get_json(candidate)
        if metadata and metadata.get("authorization_servers"):
            return metadata
    raise SystemExit(f"no protected-resource metadata found for {mcp_url}")


def discover_authorization_server(issuer: str) -> dict:
    parsed = urllib.parse.urlparse(issuer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for candidate in (
        f"{origin}/.well-known/oauth-authorization-server{parsed.path}",
        f"{origin}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/openid-configuration{parsed.path}",
        f"{origin}/.well-known/openid-configuration",
    ):
        metadata = get_json(candidate)
        if metadata and metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
            return metadata
    raise SystemExit(f"no authorization-server metadata found for {issuer}")


def register_client(registration_endpoint: str) -> str:
    body = json.dumps({
        "client_name": "vibemind-openfang-provisioner",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }).encode("utf-8")
    request = urllib.request.Request(
        registration_endpoint, data=body, method="POST",
        headers={"content-type": "application/json", "accept": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=15, context=SSL_CONTEXT) as response:
        registered = json.loads(response.read().decode("utf-8"))
    client_id = registered.get("client_id")
    if not isinstance(client_id, str) or client_id == "":
        raise SystemExit("dynamic client registration returned no client_id")
    return client_id


def wait_for_callback(expected_state: str, timeout_seconds: int = 300) -> str:
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            state = (query.get("state") or [""])[0]
            if state != expected_state:
                self.send_response(400); self.end_headers()
                self.wfile.write(b"state mismatch - ignore this window")
                return
            if "error" in query:
                result["error"] = (query.get("error_description") or query["error"])[0]
            else:
                result["code"] = (query.get("code") or [""])[0]
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Token received - you can close this window.</h2>")
            done.set()

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout_seconds):
            raise SystemExit("timed out waiting for the browser callback")
    finally:
        server.shutdown()
    if "error" in result:
        raise SystemExit(f"authorization refused: {result['error']}")
    if not result.get("code"):
        raise SystemExit("callback carried no authorization code")
    return result["code"]


def _entdecken_und_registrieren(mcp_url: str) -> tuple[str, dict, str, str, str, str]:
    """Discovery (Schritte 1-2) + dynamische Registrierung (Schritt 3) +
    PKCE/state/authorize_url -- der Teil des Flusses, den `--dry-run` UND
    `token_holen` beide brauchen, darum hier einmal statt zweimal.

    Gibt (resource, server_metadata, client_id, verifier, state,
    authorize_url) zurueck. Kein Netzwerkzugriff nach dieser Funktion
    faengt noch einen Token ab -- das beginnt erst mit dem Browser-Login.
    """
    resource_metadata = discover_resource_metadata(mcp_url)
    resource = resource_metadata.get("resource", mcp_url)
    issuer = resource_metadata["authorization_servers"][0]
    print(f"resource:              {resource}")
    print(f"authorization server:  {issuer}")

    server_metadata = discover_authorization_server(issuer)
    registration_endpoint = server_metadata.get("registration_endpoint")
    if not registration_endpoint:
        raise SystemExit("authorization server offers no dynamic registration; create a client manually")
    client_id = register_client(registration_endpoint)
    print(f"registered client:     {client_id}")

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    authorize_url = f"{server_metadata['authorization_endpoint']}?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    })
    return resource, server_metadata, client_id, verifier, state, authorize_url


def token_holen_voll(mcp_url: str) -> tuple[str, dict]:
    """Beschafft einen OAuth-Bearer fuer `mcp_url` und gibt (referenzname,
    tokens) zurueck, wobei `tokens` die VOLLSTAENDIGE JSON-Antwort des
    Token-Endpunkts ist (u.a. `access_token`, ggf. `refresh_token`,
    `expires_in`, `scope`) -- schreibt NICHTS.

    Herausgezogen aus main(), damit das Eingabefenster denselben Fluss
    benutzt, statt ihn abzuschreiben. main() ruft diese Funktion und
    schreibt danach wie bisher in --out.

    GEHEIMHALTUNG (Fix-Runde 1, Befund 2): `tokens` traegt jetzt sichtbar
    Klartext (`access_token`, ggf. `refresh_token`). Diese Funktion druckt,
    loggt und meldet `tokens` NIE als Ganzes -- auch nicht in einer
    Fehlermeldung oder einem Traceback. Aufrufer duerfen daraus
    ausschliesslich `expires_in`, `scope` und ein abgeleitetes
    `refresh=yes|no` drucken, nie `tokens` selbst, `access_token` oder
    `refresh_token`.

    Druckt bewusst KEINEN `reference name:` mehr (Fix-Runde 1, Befund 3):
    das war vor `name = args.name or name` in main() und zeigte bei
    `--name` erst den abgeleiteten, dann den ueberschriebenen Namen. Eine
    Bibliotheksfunktion druckt keinen Namen -- main() tut das jetzt selbst,
    mit dem WIRKSAMEN Namen.
    """
    resource, server_metadata, client_id, verifier, state, authorize_url = (
        _entdecken_und_registrieren(mcp_url))
    name = derive_name(resource)

    print("opening the browser; log in and approve there ...")
    webbrowser.open(authorize_url)
    code = wait_for_callback(state)

    token_body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": resource,
    }).encode("utf-8")
    token_request = urllib.request.Request(
        server_metadata["token_endpoint"], data=token_body, method="POST",
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(token_request, timeout=30, context=SSL_CONTEXT) as response:
        tokens = json.loads(response.read().decode("utf-8"))
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or access_token == "":
        raise SystemExit("token endpoint returned no access_token")
    return name, tokens


def token_holen(mcp_url: str) -> tuple[str, str]:
    """Beschafft einen OAuth-Bearer fuer `mcp_url` und gibt
    (referenzname, token) zurueck -- schreibt NICHTS.

    Schmale Fassung von `token_holen_voll` fuer das Eingabefenster
    (`fenster.oauth_entgegennehmen` via `server.py`): Signatur bewusst
    UNVERAENDERT (Fix-Runde 1, Befund 2, ausdruecklich entschieden) -- ein
    groesseres Tupel oder eine Datenklasse zoege `fenster.py`, `server.py`
    und die oauth-Tests mit, und ein Datenklassen-`repr` waere eine neue
    Leck-Flaeche fuer den Token, fuer nichts.
    """
    name, tokens = token_holen_voll(mcp_url)
    return name, tokens["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mcp_url", help="the MCP server URL, e.g. https://mcp.notion.com/mcp")
    parser.add_argument("--out", help="env file to append <NAME>=<token> to (created 0600 if missing)")
    parser.add_argument("--name", help="override the derived reference name")
    parser.add_argument("--dry-run", action="store_true", help="stop after printing the authorization URL")
    args = parser.parse_args()

    if args.dry_run:
        resource, _server_metadata, _client_id, _verifier, _state, authorize_url = (
            _entdecken_und_registrieren(args.mcp_url))
        name = args.name or derive_name(resource)
        print(f"reference name:        {name}")
        print("dry run - authorization URL (not opened):")
        print(f"  {authorize_url}")
        return

    name, tokens = token_holen_voll(args.mcp_url)
    name = args.name or name
    print(f"reference name:        {name}")
    access_token = tokens["access_token"]

    lines = [f"{name}={access_token}\n"]
    if isinstance(tokens.get("refresh_token"), str) and tokens["refresh_token"]:
        lines.append(f"{name}_REFRESH={tokens['refresh_token']}\n")
    if args.out:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        descriptor = os.open(args.out, flags, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
        destination = args.out
    else:
        destination = "(no --out given; token DISCARDED - rerun with --out)"

    print(f"token issued:          expires_in={tokens.get('expires_in')} scope={tokens.get('scope')!r} refresh={'yes' if len(lines) > 1 else 'no'}")
    print(f"written to:            {destination}")
    print(f"allowlist with:        OPENFANG_ISSUABLE_CREDENTIALS={name}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
