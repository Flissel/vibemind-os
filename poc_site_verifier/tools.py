"""
Website Authenticity Check Tools
=================================
Pure async functions that perform OSINT checks on a domain/URL.
Each returns a dict of findings. Used by OrchestratorAgent via OpenAI tool calling.
"""

import asyncio
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llm_client import get_model

from openai import AsyncOpenAI


# ================================================================
# STEALTH HTTP LAYER
# ================================================================
# Centralized request handling with browser-like fingerprinting,
# randomized delays, UA rotation, and WAF-aware backoff.

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

_ACCEPT_HEADERS = {
    "html": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "json": "application/json, text/plain, */*",
    "any": "*/*",
}

# Per-domain session state: tracks cookies and last request time
_domain_state: dict = {}
_global_lock = asyncio.Lock()


def _browser_headers(accept: str = "html", referer: str | None = None) -> dict:
    """Generate a full set of browser-like HTTP headers."""
    ua = random.choice(_USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": _ACCEPT_HEADERS.get(accept, _ACCEPT_HEADERS["any"]),
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers


async def _stealth_delay(domain: str, min_s: float = 0.3, max_s: float = 1.5):
    """Randomized inter-request delay per domain to avoid rate limiting."""
    now = time.monotonic()
    state = _domain_state.setdefault(domain, {"last_req": 0.0, "backoff": 0})

    elapsed = now - state["last_req"]
    base_delay = random.uniform(min_s, max_s)

    # Add exponential backoff if we've been rate-limited
    if state["backoff"] > 0:
        base_delay += min(2 ** state["backoff"], 30)

    remaining = base_delay - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)

    state["last_req"] = time.monotonic()


def _record_backoff(domain: str):
    """Increase backoff counter after a 403/429."""
    state = _domain_state.setdefault(domain, {"last_req": 0.0, "backoff": 0})
    state["backoff"] = min(state["backoff"] + 1, 5)


def _clear_backoff(domain: str):
    """Reset backoff on successful request."""
    state = _domain_state.setdefault(domain, {"last_req": 0.0, "backoff": 0})
    state["backoff"] = 0


async def stealth_request(
    url: str,
    method: str = "GET",
    accept: str = "html",
    referer: str | None = None,
    timeout: int = 15,
    data: bytes | None = None,
    extra_headers: dict | None = None,
    delay: bool = True,
    max_retries: int = 2,
) -> urllib.request.Request:
    """
    Central HTTP request function with stealth features.
    Returns the response object (from urlopen).
    Handles: UA rotation, browser headers, per-domain delay, WAF backoff + retry.
    """
    parsed = urlparse(url)
    domain = parsed.netloc

    for attempt in range(max_retries + 1):
        if delay:
            await _stealth_delay(domain)

        headers = _browser_headers(accept=accept, referer=referer)
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(url, method=method, headers=headers, data=data)

        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: urllib.request.urlopen(req, timeout=timeout)
            )
            _clear_backoff(domain)
            return resp
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503) and attempt < max_retries:
                _record_backoff(domain)
                wait = random.uniform(2, 5) * (attempt + 1)
                await asyncio.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < max_retries:
                await asyncio.sleep(random.uniform(1, 3))
                continue
            raise


async def stealth_fetch(url: str, **kwargs) -> str:
    """Convenience: fetch URL and return decoded body text. Handles gzip/br/deflate."""
    import gzip
    import zlib

    resp = await stealth_request(url, **kwargs)
    raw = resp.read()

    # Decompress based on Content-Encoding
    ce = (resp.headers.get("Content-Encoding") or "").lower()
    if ce == "gzip" or ce == "x-gzip":
        raw = gzip.decompress(raw)
    elif ce == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    elif ce == "br":
        try:
            import brotli
            raw = brotli.decompress(raw)
        except ImportError:
            pass  # brotli not installed, return raw

    encoding = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(encoding, errors="replace")


async def stealth_head(url: str, **kwargs) -> urllib.request.Request:
    """Convenience: HEAD request."""
    return await stealth_request(url, method="HEAD", **kwargs)


# ================================================================
# SOFT-404 / SPA CATCH-ALL DETECTION
# ================================================================
# Many SPAs return 200 + index.html for ANY path. This creates massive
# false positives in path discovery, XSS checks, API discovery, etc.
# Solution: fingerprint the homepage and a known-bogus path. If they
# match, the site has a catch-all. Then any response matching that
# fingerprint is a soft-404.

import hashlib

_baseline_cache: dict = {}  # domain -> {"homepage_hash": str, "bogus_hash": str, "is_catchall": bool}


async def _get_baseline(url: str) -> dict:
    """Get or compute the baseline fingerprint for a domain."""
    parsed = urlparse(url)
    domain = parsed.netloc
    base = f"{parsed.scheme}://{domain}"

    if domain in _baseline_cache:
        return _baseline_cache[domain]

    baseline = {"homepage_hash": None, "bogus_hash": None, "is_catchall": False}

    try:
        # Fetch homepage
        home_body = await stealth_fetch(base + "/", timeout=15, max_retries=1)
        baseline["homepage_hash"] = hashlib.md5(home_body.encode()).hexdigest()

        # Fetch a path that definitely doesn't exist
        bogus_body = await stealth_fetch(
            base + "/zz-nonexistent-path-xk8m3q7p2w/", timeout=10, max_retries=1
        )
        baseline["bogus_hash"] = hashlib.md5(bogus_body.encode()).hexdigest()

        # If homepage == bogus path, it's a catch-all SPA
        baseline["is_catchall"] = (baseline["homepage_hash"] == baseline["bogus_hash"])

    except Exception:
        pass

    _baseline_cache[domain] = baseline
    return baseline


async def is_soft_404(url: str, body: str) -> bool:
    """Check if a response body is actually a soft-404 (SPA catch-all returning homepage)."""
    baseline = await _get_baseline(url)
    if not baseline["is_catchall"]:
        return False

    body_hash = hashlib.md5(body.encode()).hexdigest()
    return body_hash == baseline["homepage_hash"]


async def is_real_endpoint(url: str, body: str, status: int = 200) -> bool:
    """
    Determine if a response is a real endpoint (not a soft-404).
    Returns True if the response is genuine content, False if it's a catch-all.
    """
    if status not in (200, 301, 302):
        return True  # non-200 responses are real signals
    return not await is_soft_404(url, body)


# ================================================================
# TOOL: whois_lookup
# ================================================================

async def whois_lookup(domain: str) -> dict:
    """Query WHOIS data for a domain. Returns registrant, dates, registrar."""
    result = {
        "domain": domain,
        "raw": "",
        "registrar": None,
        "creation_date": None,
        "expiry_date": None,
        "registrant_org": None,
        "registrant_country": None,
        "domain_age_days": None,
        "privacy_protected": False,
        "warning": None,
    }

    try:
        # Determine WHOIS server
        tld = domain.rsplit(".", 1)[-1]
        whois_servers = {
            "com": "whois.verisign-grs.com",
            "net": "whois.verisign-grs.com",
            "org": "whois.pir.org",
            "de": "whois.denic.de",
            "io": "whois.nic.io",
            "co": "whois.nic.co",
            "me": "whois.nic.me",
            "info": "whois.afilias.net",
        }
        server = whois_servers.get(tld, f"whois.nic.{tld}")

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(server, 43), timeout=10.0
        )
        writer.write(f"{domain}\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(8192), timeout=10.0)
        writer.close()
        await writer.wait_closed()

        text = raw.decode("utf-8", errors="replace")
        result["raw"] = text[:3000]

        # Parse common fields
        for line in text.splitlines():
            line_lower = line.lower().strip()
            if "registrar:" in line_lower:
                result["registrar"] = line.split(":", 1)[1].strip()
            elif "creation date:" in line_lower or "created:" in line_lower:
                result["creation_date"] = line.split(":", 1)[1].strip()
            elif "expir" in line_lower and "date:" in line_lower:
                result["expiry_date"] = line.split(":", 1)[1].strip()
            elif "registrant organization:" in line_lower:
                result["registrant_org"] = line.split(":", 1)[1].strip()
            elif "registrant country:" in line_lower:
                result["registrant_country"] = line.split(":", 1)[1].strip()

        # Check privacy protection
        privacy_keywords = ["privacy", "redacted", "withheld", "proxy", "whoisguard", "domains by proxy"]
        if any(kw in text.lower() for kw in privacy_keywords):
            result["privacy_protected"] = True

        # Calculate domain age
        if result["creation_date"]:
            try:
                for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d-%b-%Y"):
                    try:
                        created = datetime.strptime(result["creation_date"][:19], fmt)
                        age = (datetime.now() - created).days
                        result["domain_age_days"] = age
                        if age < 90:
                            result["warning"] = f"Domain is only {age} days old — very young!"
                        break
                    except ValueError:
                        continue
            except Exception:
                pass

    except Exception as e:
        result["warning"] = f"WHOIS lookup failed: {e}"

    return result


# ================================================================
# TOOL: check_ssl_cert
# ================================================================

async def check_ssl_cert(domain: str, port: int = 443) -> dict:
    """Check SSL/TLS certificate details for a domain."""
    result = {
        "domain": domain,
        "port": port,
        "tls_enabled": False,
        "tls_version": None,
        "cipher": None,
        "cert_subject_cn": None,
        "cert_issuer": None,
        "cert_not_before": None,
        "cert_not_after": None,
        "cert_expired": None,
        "cert_san": [],
        "cn_matches_domain": None,
        "self_signed": False,
        "warning": None,
    }

    try:
        ctx = ssl.create_default_context()
        # First try with verification
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(domain, port, ssl=ctx, server_hostname=domain),
                timeout=10.0,
            )
        except ssl.SSLCertVerificationError as e:
            result["warning"] = f"Certificate verification failed: {e}"
            # Retry without verification to get cert details
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(domain, port, ssl=ctx, server_hostname=domain),
                timeout=10.0,
            )

        result["tls_enabled"] = True
        ssl_obj = writer.transport.get_extra_info("ssl_object")

        if ssl_obj:
            result["tls_version"] = ssl_obj.version()
            cipher_info = ssl_obj.cipher()
            if cipher_info:
                result["cipher"] = cipher_info[0]

            cert = ssl_obj.getpeercert()
            if cert:
                # Subject CN
                subject = dict(x[0] for x in cert.get("subject", ()))
                result["cert_subject_cn"] = subject.get("commonName")

                # Issuer
                issuer = dict(x[0] for x in cert.get("issuer", ()))
                result["cert_issuer"] = issuer.get("organizationName") or issuer.get("commonName")

                # Dates
                result["cert_not_before"] = cert.get("notBefore")
                result["cert_not_after"] = cert.get("notAfter")

                # Check expiry
                if cert.get("notAfter"):
                    try:
                        expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                        result["cert_expired"] = expiry < datetime.now()
                    except ValueError:
                        pass

                # SAN entries
                san = cert.get("subjectAltName", ())
                result["cert_san"] = [entry[1] for entry in san if entry[0] == "DNS"]

                # CN match
                cn = result["cert_subject_cn"] or ""
                san_list = result["cert_san"]
                result["cn_matches_domain"] = (
                    domain == cn
                    or domain in san_list
                    or any(
                        s.startswith("*.") and domain.endswith(s[1:])
                        for s in [cn] + san_list
                    )
                )

                # Self-signed check
                if subject == issuer:
                    result["self_signed"] = True
                    result["warning"] = "Self-signed certificate detected!"

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        result["warning"] = f"SSL check failed: {e}"

    return result


# ================================================================
# TOOL: dns_records
# ================================================================

async def dns_records(domain: str, record_types: str = "A,MX,TXT") -> dict:
    """Query DNS records for a domain. Checks A, MX, TXT (SPF/DMARC/DKIM)."""
    result = {
        "domain": domain,
        "records": {},
        "spf_found": False,
        "dmarc_found": False,
        "spf_record": None,
        "dmarc_record": None,
        "mx_records": [],
        "a_records": [],
        "warning": None,
    }

    types_to_check = [t.strip().upper() for t in record_types.split(",")]

    for rtype in types_to_check:
        try:
            query_domain = domain

            proc = await asyncio.create_subprocess_exec(
                "nslookup", "-type=" + rtype, query_domain,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            output = stdout.decode("utf-8", errors="replace")
            result["records"][rtype] = output.strip()

            # Parse A records
            if rtype == "A":
                for line in output.splitlines():
                    if "Address:" in line and "." in line:
                        addr = line.split("Address:")[-1].strip()
                        if addr and not addr.startswith("#"):
                            result["a_records"].append(addr)

            # Parse MX records
            if rtype == "MX":
                for line in output.splitlines():
                    if "mail exchanger" in line.lower() or "MX" in line:
                        result["mx_records"].append(line.strip())

            # Parse TXT for SPF/DMARC
            if rtype == "TXT":
                if "v=spf1" in output:
                    result["spf_found"] = True
                    for line in output.splitlines():
                        if "v=spf1" in line:
                            result["spf_record"] = line.strip()
                if "v=DMARC1" in output:
                    result["dmarc_found"] = True
                    for line in output.splitlines():
                        if "v=DMARC1" in line:
                            result["dmarc_record"] = line.strip()

        except Exception as e:
            result["records"][rtype] = f"Error: {e}"

    # Also check DMARC specifically
    try:
        proc = await asyncio.create_subprocess_exec(
            "nslookup", "-type=TXT", f"_dmarc.{domain}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        dmarc_output = stdout.decode("utf-8", errors="replace")
        if "v=DMARC1" in dmarc_output:
            result["dmarc_found"] = True
            for line in dmarc_output.splitlines():
                if "v=DMARC1" in line:
                    result["dmarc_record"] = line.strip()
    except Exception:
        pass

    if not result["spf_found"]:
        result["warning"] = "No SPF record found — emails can be spoofed!"
    if not result["dmarc_found"]:
        w = result.get("warning") or ""
        result["warning"] = (w + " No DMARC record found.").strip()

    return result


# ================================================================
# TOOL: http_headers
# ================================================================

async def http_headers(url: str) -> dict:
    """Fetch HTTP headers and check for security headers."""
    result = {
        "url": url,
        "status_code": None,
        "server": None,
        "headers": {},
        "security_headers": {},
        "missing_security_headers": [],
        "redirect_chain": [],
        "warning": None,
    }

    SECURITY_HEADERS = [
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "X-XSS-Protection",
        "Referrer-Policy",
        "Permissions-Policy",
    ]

    try:
        import urllib.error

        hdrs = _browser_headers(accept="html")
        req = urllib.request.Request(url, method="HEAD", headers=hdrs)

        # Follow redirects manually to capture chain
        class RedirectHandler(urllib.request.HTTPRedirectHandler):
            def __init__(self):
                self.redirects = []
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                self.redirects.append({"code": code, "url": newurl})
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        handler = RedirectHandler()
        opener = urllib.request.build_opener(handler)
        await _stealth_delay(urlparse(url).netloc)

        resp = await asyncio.get_event_loop().run_in_executor(
            None, lambda: opener.open(req, timeout=15)
        )

        result["status_code"] = resp.status
        result["redirect_chain"] = handler.redirects
        result["headers"] = dict(resp.headers)
        result["server"] = resp.headers.get("Server")

        for header in SECURITY_HEADERS:
            val = resp.headers.get(header)
            if val:
                result["security_headers"][header] = val
            else:
                result["missing_security_headers"].append(header)

    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["warning"] = f"HTTP error: {e.code} {e.reason}"
    except Exception as e:
        result["warning"] = f"HTTP check failed: {e}"

    return result


# ================================================================
# TOOL: wayback_check
# ================================================================

async def wayback_check(url: str) -> dict:
    """Check Wayback Machine (archive.org) for archived snapshots of a URL."""
    result = {
        "url": url,
        "archived": False,
        "total_snapshots": 0,
        "first_snapshot": None,
        "latest_snapshot": None,
        "archive_age_days": None,
        "warning": None,
    }

    try:
        api_url = f"https://web.archive.org/wayback/available?url={url}"
        resp = await stealth_request(api_url, accept="json", timeout=15)
        data = json.loads(resp.read().decode())

        snapshots = data.get("archived_snapshots", {})
        closest = snapshots.get("closest")

        if closest:
            result["archived"] = True
            result["latest_snapshot"] = closest.get("url")

            ts = closest.get("timestamp", "")
            if len(ts) >= 8:
                snap_date = datetime.strptime(ts[:8], "%Y%m%d")
                result["latest_snapshot_date"] = snap_date.isoformat()

        # Get first snapshot via CDX
        cdx_url = f"https://web.archive.org/cdx/search/cdx?url={url}&output=json&limit=1&fl=timestamp"

        try:
            resp2 = await stealth_request(cdx_url, accept="json", timeout=15)
            cdx_data = json.loads(resp2.read().decode())
            if cdx_data and len(cdx_data) > 1:
                first_ts = cdx_data[1][0]
                if len(first_ts) >= 8:
                    first_date = datetime.strptime(first_ts[:8], "%Y%m%d")
                    result["first_snapshot"] = first_date.isoformat()
                    result["archive_age_days"] = (datetime.now() - first_date).days
        except Exception:
            pass

        if not result["archived"]:
            result["warning"] = "No Wayback Machine archives found — site may be very new or blocked."

    except Exception as e:
        result["warning"] = f"Wayback check failed: {e}"

    return result


# ================================================================
# TOOL: page_content_scan
# ================================================================

async def page_content_scan(url: str) -> dict:
    """Fetch page content and scan for impressum, privacy policy, external scripts, iframes."""
    result = {
        "url": url,
        "title": None,
        "has_impressum": False,
        "has_privacy_policy": False,
        "has_contact_info": False,
        "external_scripts": [],
        "external_iframes": [],
        "suspicious_patterns": [],
        "content_length": 0,
        "language_hints": [],
        "warning": None,
    }

    try:
        html = await stealth_fetch(url, timeout=20)
        result["content_length"] = len(html)
        html_lower = html.lower()

        # Title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            result["title"] = title_match.group(1).strip()[:200]

        # Impressum / Legal
        impressum_keywords = ["impressum", "imprint", "legal notice", "§ 5 tmg", "§5 tmg", "site notice"]
        result["has_impressum"] = any(kw in html_lower for kw in impressum_keywords)

        # Privacy
        privacy_keywords = ["datenschutz", "privacy policy", "privacy notice", "data protection", "dsgvo", "gdpr"]
        result["has_privacy_policy"] = any(kw in html_lower for kw in privacy_keywords)

        # Contact
        contact_patterns = [
            r"[\w.+-]+@[\w-]+\.[\w.]+",  # email
            r"tel[:\s]*[\+\d\s\-\(\)]{8,}",  # phone
            r"contact\s*us",
        ]
        for pat in contact_patterns:
            if re.search(pat, html_lower):
                result["has_contact_info"] = True
                break

        # External scripts
        for match in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE):
            src = match.group(1)
            if src.startswith("http") and urlparse(url).netloc not in src:
                result["external_scripts"].append(src[:200])

        # External iframes
        for match in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE):
            src = match.group(1)
            result["external_iframes"].append(src[:200])

        # Suspicious patterns
        suspicious = [
            ("cryptocurrency wallet", "Crypto wallet address found"),
            ("click here to verify", "Phishing-style CTA detected"),
            ("account suspended", "Scare tactic text"),
            ("send bitcoin", "Bitcoin solicitation"),
            ("urgent action required", "Urgency tactic"),
        ]
        for keyword, description in suspicious:
            if keyword in html_lower:
                result["suspicious_patterns"].append(description)

        # Language hints
        lang_match = re.search(r'<html[^>]+lang=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if lang_match:
            result["language_hints"].append(lang_match.group(1))

    except Exception as e:
        result["warning"] = f"Page scan failed: {e}"

    return result


# ================================================================
# TOOL: reverse_ip_lookup
# ================================================================

async def reverse_ip_lookup(domain: str) -> dict:
    """Resolve domain to IP and get basic hosting info via WHOIS on the IP."""
    result = {
        "domain": domain,
        "ip_address": None,
        "hosting_org": None,
        "hosting_country": None,
        "warning": None,
    }

    try:
        ip = await asyncio.get_event_loop().run_in_executor(
            None, lambda: socket.gethostbyname(domain)
        )
        result["ip_address"] = ip

        # WHOIS on IP
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("whois.arin.net", 43), timeout=10.0
        )
        writer.write(f"n + {ip}\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(4096), timeout=10.0)
        writer.close()
        await writer.wait_closed()

        text = raw.decode("utf-8", errors="replace")

        for line in text.splitlines():
            lower = line.lower().strip()
            if "orgname:" in lower:
                result["hosting_org"] = line.split(":", 1)[1].strip()
            elif "organization:" in lower:
                result["hosting_org"] = result["hosting_org"] or line.split(":", 1)[1].strip()
            elif "country:" in lower:
                result["hosting_country"] = line.split(":", 1)[1].strip()

    except Exception as e:
        result["warning"] = f"Reverse IP lookup failed: {e}"

    return result


# ================================================================
# TOOL: open_redirect_check
# ================================================================

async def open_redirect_check(url: str) -> dict:
    """Test for open redirect vulnerabilities on common parameters."""
    import urllib.error
    import urllib.parse

    result = {
        "url": url,
        "tests_run": 0,
        "redirects_found": [],
        "issues": [],
    }

    REDIRECT_TARGET = "https://evil-redirect-test.com"

    REDIRECT_PARAMS = [
        "redirect", "redirect_to", "url", "next", "return",
        "returnTo", "return_url", "goto", "destination", "redir",
        "redirect_uri", "continue", "target", "link", "out",
    ]

    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def __init__(self):
            self.redirected_to = None
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            self.redirected_to = newurl
            return None

    for param in REDIRECT_PARAMS:
        test_url = f"{base_url}/?{param}={urllib.parse.quote(REDIRECT_TARGET)}"
        result["tests_run"] += 1

        try:
            handler = NoRedirectHandler()
            opener = urllib.request.build_opener(handler)
            hdrs = _browser_headers()
            await _stealth_delay(parsed.netloc)

            try:
                resp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda u=test_url, o=opener: o.open(
                        urllib.request.Request(u, headers=hdrs),
                        timeout=10,
                    )
                )
                # Check if response body contains the redirect URL
                body = resp.read().decode("utf-8", errors="replace")
                if REDIRECT_TARGET in body:
                    result["redirects_found"].append({
                        "parameter": param,
                        "url": test_url,
                        "type": "url_in_body",
                    })
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307, 308):
                    location = e.headers.get("Location", "")
                    if REDIRECT_TARGET in location:
                        result["redirects_found"].append({
                            "parameter": param,
                            "url": test_url,
                            "type": "http_redirect",
                            "location": location,
                        })

            if handler.redirected_to and REDIRECT_TARGET in handler.redirected_to:
                result["redirects_found"].append({
                    "parameter": param,
                    "url": test_url,
                    "type": "http_redirect",
                    "location": handler.redirected_to,
                })

        except Exception:
            pass

    if result["redirects_found"]:
        params = list(set(r["parameter"] for r in result["redirects_found"]))
        result["issues"].append({
            "severity": "HIGH",
            "category": "Open Redirect",
            "title": f"Open redirect via parameter(s): {', '.join(params)}",
            "description": (
                f"The site redirects to arbitrary external URLs via parameter(s) {', '.join(params)}. "
                "Attackers use this for phishing: victim sees the trusted domain in the link "
                "but gets redirected to a fake login page."
            ),
            "fix": "Validate redirect URLs server-side. Only allow relative paths or whitelisted domains.",
        })

    return result


# ================================================================
# TOOL: http_methods_check
# ================================================================

async def http_methods_check(url: str) -> dict:
    """Test which HTTP methods are allowed (PUT, DELETE, TRACE, OPTIONS)."""
    import urllib.error

    result = {
        "url": url,
        "allowed_methods": [],
        "dangerous_methods": [],
        "issues": [],
    }

    METHODS_TO_TEST = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "HEAD"]
    DANGEROUS = {"PUT", "DELETE", "TRACE", "PATCH"}

    # First try OPTIONS to get Allow header
    try:
        resp = await stealth_request(url, method="OPTIONS", timeout=10)
        allow = resp.headers.get("Allow", "")
        if allow:
            result["allowed_methods"] = [m.strip() for m in allow.split(",")]
    except Exception:
        pass

    # Test each method individually
    for method in METHODS_TO_TEST:
        try:
            resp = await stealth_request(url, method=method, timeout=10)
            if method not in result["allowed_methods"]:
                result["allowed_methods"].append(method)
            if method in DANGEROUS:
                result["dangerous_methods"].append(method)
        except urllib.error.HTTPError as e:
            if e.code != 405:  # 405 = Method Not Allowed (expected)
                if method not in result["allowed_methods"]:
                    result["allowed_methods"].append(method)
                if method in DANGEROUS:
                    result["dangerous_methods"].append(method)
        except Exception:
            pass

    if "TRACE" in result["dangerous_methods"]:
        result["issues"].append({
            "severity": "MEDIUM",
            "category": "HTTP Methods",
            "title": "TRACE method enabled",
            "description": (
                "HTTP TRACE is enabled. This can be used for Cross-Site Tracing (XST) attacks "
                "to steal credentials from HTTP headers including cookies and auth tokens."
            ),
            "fix": "Disable TRACE method in web server config.",
            "nginx_fix": "if ($request_method = TRACE) { return 405; }",
        })

    if "PUT" in result["dangerous_methods"] or "DELETE" in result["dangerous_methods"]:
        methods = [m for m in ["PUT", "DELETE"] if m in result["dangerous_methods"]]
        result["issues"].append({
            "severity": "HIGH",
            "category": "HTTP Methods",
            "title": f"Dangerous HTTP methods enabled: {', '.join(methods)}",
            "description": (
                f"HTTP {', '.join(methods)} method(s) are accepted. "
                "PUT can upload files, DELETE can remove resources. "
                "Unless this is an intentional API, these should be disabled."
            ),
            "fix": f"Disable {', '.join(methods)} methods unless needed for API functionality.",
        })

    return result


# ================================================================
# TOOL: js_secrets_scanner
# ================================================================

async def js_secrets_scanner(url: str) -> dict:
    """Scan JavaScript files for exposed API keys, tokens, and secrets."""

    result = {
        "url": url,
        "js_files_scanned": 0,
        "secrets_found": [],
        "issues": [],
    }

    SECRET_PATTERNS = [
        ("AWS Access Key", r'AKIA[0-9A-Z]{16}'),
        ("AWS Secret Key", r'(?i)aws_secret_access_key[\s]*[=:][\s]*["\']?([A-Za-z0-9/+=]{40})'),
        ("Google API Key", r'AIza[0-9A-Za-z\-_]{35}'),
        ("Google OAuth", r'[0-9]+-[0-9A-Za-z_]{32}\.apps\.googleusercontent\.com'),
        ("GitHub Token", r'(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}'),
        ("Slack Token", r'xox[bpors]-[0-9a-zA-Z]{10,}'),
        ("Slack Webhook", r'https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+'),
        ("Private Key", r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----'),
        ("Stripe Key", r'(?:sk|pk)_(?:test|live)_[0-9a-zA-Z]{24,}'),
        ("Mailgun API", r'key-[0-9a-zA-Z]{32}'),
        ("Twilio", r'SK[0-9a-fA-F]{32}'),
        ("SendGrid", r'SG\.[0-9A-Za-z\-_]{22}\.[0-9A-Za-z\-_]{43}'),
        ("Firebase", r'(?i)firebase[a-z0-9_.-]*\.firebaseio\.com'),
        ("JWT Token", r'eyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+'),
        ("Basic Auth", r'(?i)(?:basic|bearer)\s+[A-Za-z0-9+/=]{20,}'),
        ("Password in URL", r'(?i)(?:password|passwd|pwd|secret)\s*[=:]\s*["\'][^"\']{4,}["\']'),
        ("API Key Generic", r'(?i)(?:api[_-]?key|apikey)\s*[=:]\s*["\'][A-Za-z0-9]{16,}["\']'),
        ("Database URL", r'(?:mysql|postgres|mongodb|redis)://[^\s"\'<>]+'),
        ("Internal IP", r'(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})'),
    ]

    # Fetch the page multiple times to defeat CDN bundle rotation
    # (SPAs like lovable.dev serve different JS bundle hashes per request)
    try:
        from urllib.parse import urljoin

        js_urls = set()
        all_inline_scripts = []
        seen_inline_hashes = set()

        FETCH_ROUNDS = 3
        for round_num in range(FETCH_ROUNDS):
            try:
                html = await stealth_fetch(url, timeout=15)

                # Find all script sources
                for match in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE):
                    src = match.group(1)
                    if not src.startswith("data:"):
                        full_url = urljoin(url, src)
                        js_urls.add(full_url)

                # Collect inline scripts (deduplicated by hash)
                for script_content in re.findall(r'<script[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
                    if len(script_content.strip()) > 10:
                        h = hashlib.md5(script_content.encode()).hexdigest()
                        if h not in seen_inline_hashes:
                            seen_inline_hashes.add(h)
                            all_inline_scripts.append(script_content)

            except Exception:
                pass

            if round_num < FETCH_ROUNDS - 1:
                await asyncio.sleep(random.uniform(0.5, 1.5))

        inline_scripts = all_inline_scripts
        seen_secrets = set()  # Deduplicate by (type, full_value)

        for i, script_content in enumerate(inline_scripts):
            if len(script_content.strip()) > 10:
                for secret_name, pattern in SECRET_PATTERNS:
                    matches = re.findall(pattern, script_content)
                    if matches:
                        for match in matches[:3]:
                            val = match if isinstance(match, str) else match[0] if match else ""
                            dedup_key = (secret_name, val)
                            if dedup_key not in seen_secrets:
                                seen_secrets.add(dedup_key)
                                result["secrets_found"].append({
                                    "type": secret_name,
                                    "location": f"inline_script_{i+1}",
                                    "value_preview": val[:20] + "..." if len(val) > 20 else val,
                                    "full_value": val,
                                })

        # Scan external JS files (limit to same-domain + CDN)
        parsed_url = re.match(r'https?://[^/]+', url).group(0)
        domain = urlparse(url).netloc
        # Include same-domain and common CDN patterns for the same site
        scan_js = [u for u in js_urls if domain in u or u.startswith(parsed_url)][:25]

        sem = asyncio.Semaphore(5)
        seen_secrets = set()  # Deduplicate by (type, full_value)

        async def scan_js_file(js_url):
            async with sem:
                try:
                    js_content = await stealth_fetch(js_url, timeout=10)

                    for secret_name, pattern in SECRET_PATTERNS:
                        matches = re.findall(pattern, js_content)
                        if matches:
                            for match in matches[:3]:
                                val = match if isinstance(match, str) else match[0] if match else ""
                                dedup_key = (secret_name, val)
                                if dedup_key not in seen_secrets:
                                    seen_secrets.add(dedup_key)
                                    result["secrets_found"].append({
                                        "type": secret_name,
                                        "location": js_url.split("/")[-1][:60],
                                        "value_preview": val[:20] + "..." if len(val) > 20 else val,
                                        "full_value": val,
                                    })
                except Exception:
                    pass

        tasks = [scan_js_file(u) for u in scan_js]
        await asyncio.gather(*tasks)

        result["js_files_scanned"] = len(scan_js) + len(inline_scripts)
        result["fetch_rounds"] = FETCH_ROUNDS
        result["unique_js_urls"] = len(js_urls)

    except Exception as e:
        result["issues"].append({
            "severity": "INFO",
            "category": "JS Secrets",
            "title": "JS scanning failed",
            "description": str(e),
        })

    # Generate issues
    if result["secrets_found"]:
        # Group by type
        types = list(set(s["type"] for s in result["secrets_found"]))
        critical_types = ["AWS Access Key", "AWS Secret Key", "Private Key",
                         "Stripe Key", "Database URL", "Password in URL",
                         "GitHub Token", "SendGrid"]
        high_types = ["Google API Key", "Slack Token", "JWT Token",
                     "Firebase", "API Key Generic", "Mailgun API"]

        has_critical = any(t in critical_types for t in types)
        has_high = any(t in high_types for t in types)

        severity = "CRITICAL" if has_critical else "HIGH" if has_high else "MEDIUM"

        result["issues"].append({
            "severity": severity,
            "category": "Exposed Secrets",
            "title": f"{len(result['secrets_found'])} secret(s) found in JavaScript: {', '.join(types[:5])}",
            "description": (
                f"Sensitive data found in JavaScript files: {', '.join(types)}. "
                "These credentials are visible to anyone viewing the page source. "
                "Attackers scan for these patterns automatically."
            ),
            "fix": (
                "Remove all secrets from client-side JavaScript. "
                "Use server-side API proxies instead of exposing API keys. "
                "Rotate any exposed credentials immediately."
            ),
        })

    # Internal IPs are lower severity
    internal_ips = [s for s in result["secrets_found"] if s["type"] == "Internal IP"]
    if internal_ips and not any(s["type"] != "Internal IP" for s in result["secrets_found"]):
        result["issues"] = [{
            "severity": "LOW",
            "category": "Information Disclosure",
            "title": f"Internal IP address(es) found in JavaScript",
            "description": "Internal network IP addresses are exposed in JavaScript files, revealing network topology.",
            "fix": "Remove internal IP references from client-side code.",
        }]

    return result


# ================================================================
# TOOL: email_spoofing_test
# ================================================================

async def email_spoofing_test(domain: str) -> dict:
    """Deep check of email security: SPF strictness, DMARC policy, DKIM."""
    result = {
        "domain": domain,
        "spf": {"found": False, "record": None, "strict": False, "issues": []},
        "dmarc": {"found": False, "record": None, "policy": None, "issues": []},
        "dkim": {"found": False, "selectors_checked": []},
        "spoofable": True,
        "issues": [],
    }

    # SPF deep check
    try:
        proc = await asyncio.create_subprocess_exec(
            "nslookup", "-type=TXT", domain,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode("utf-8", errors="replace")

        for line in output.splitlines():
            if "v=spf1" in line:
                result["spf"]["found"] = True
                result["spf"]["record"] = line.strip()

                if "-all" in line:
                    result["spf"]["strict"] = True
                elif "~all" in line:
                    result["spf"]["issues"].append("SPF uses ~all (softfail) instead of -all (hardfail). Spoofed emails may still be delivered.")
                elif "+all" in line:
                    result["spf"]["issues"].append("SPF uses +all — this allows ANYONE to send emails as this domain!")
                    result["issues"].append({
                        "severity": "CRITICAL",
                        "category": "Email Security",
                        "title": "SPF +all allows universal email spoofing",
                        "description": "SPF record contains +all, meaning any server is authorized to send email for this domain.",
                        "fix": "Change +all to -all in SPF record.",
                    })
                elif "?all" in line:
                    result["spf"]["issues"].append("SPF uses ?all (neutral) — provides no protection.")
                break
    except Exception:
        pass

    if not result["spf"]["found"]:
        result["issues"].append({
            "severity": "HIGH",
            "category": "Email Security",
            "title": "No SPF record found",
            "description": "Without SPF, anyone can send emails pretending to be from this domain.",
            "fix": f"Add SPF record: {domain}. IN TXT \"v=spf1 include:_spf.google.com -all\"",
        })

    # DMARC deep check
    try:
        proc = await asyncio.create_subprocess_exec(
            "nslookup", "-type=TXT", f"_dmarc.{domain}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode("utf-8", errors="replace")

        for line in output.splitlines():
            if "v=DMARC1" in line:
                result["dmarc"]["found"] = True
                result["dmarc"]["record"] = line.strip()

                if "p=none" in line:
                    result["dmarc"]["policy"] = "none"
                    result["dmarc"]["issues"].append("DMARC policy is 'none' — spoofed emails are not rejected, only reported.")
                    result["issues"].append({
                        "severity": "MEDIUM",
                        "category": "Email Security",
                        "title": "DMARC policy set to 'none' (monitoring only)",
                        "description": "DMARC is configured but only monitors — spoofed emails are delivered normally.",
                        "fix": "Change DMARC policy from p=none to p=quarantine or p=reject.",
                    })
                elif "p=quarantine" in line:
                    result["dmarc"]["policy"] = "quarantine"
                elif "p=reject" in line:
                    result["dmarc"]["policy"] = "reject"
                    result["spoofable"] = False
                break
    except Exception:
        pass

    if not result["dmarc"]["found"]:
        result["issues"].append({
            "severity": "HIGH",
            "category": "Email Security",
            "title": "No DMARC record found",
            "description": "Without DMARC, email receivers cannot verify if emails from this domain are legitimate.",
            "fix": f"Add DMARC record: _dmarc.{domain}. IN TXT \"v=DMARC1; p=reject; rua=mailto:dmarc@{domain}\"",
        })

    # DKIM check (common selectors)
    DKIM_SELECTORS = ["default", "google", "selector1", "selector2", "dkim", "mail", "k1", "s1", "s2"]

    for selector in DKIM_SELECTORS:
        try:
            proc = await asyncio.create_subprocess_exec(
                "nslookup", "-type=TXT", f"{selector}._domainkey.{domain}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            output = stdout.decode("utf-8", errors="replace")

            if "v=DKIM1" in output or "p=" in output:
                result["dkim"]["found"] = True
                result["dkim"]["selectors_checked"].append({
                    "selector": selector, "found": True,
                })
                break
            else:
                result["dkim"]["selectors_checked"].append({
                    "selector": selector, "found": False,
                })
        except Exception:
            pass

    if not result["dkim"]["found"]:
        result["issues"].append({
            "severity": "MEDIUM",
            "category": "Email Security",
            "title": "No DKIM record found (common selectors checked)",
            "description": "No DKIM signing detected. Emails cannot be cryptographically verified as authentic.",
            "fix": "Configure DKIM signing in your mail server and publish the public key in DNS.",
        })

    # Overall spoofability assessment
    if result["spf"]["found"] and result["spf"]["strict"] and result["dmarc"]["found"] and result["dmarc"]["policy"] == "reject":
        result["spoofable"] = False
    else:
        result["spoofable"] = True
        if not result["issues"]:
            result["issues"].append({
                "severity": "MEDIUM",
                "category": "Email Security",
                "title": "Email spoofing partially possible",
                "description": "Email security configuration is incomplete. Spoofed emails may be delivered.",
            })

    return result


# ================================================================
# TOOL: waf_detection
# ================================================================

async def waf_detection(url: str) -> dict:
    """Detect if a Web Application Firewall (WAF) is protecting the site."""
    import urllib.error

    result = {
        "url": url,
        "waf_detected": False,
        "waf_name": None,
        "evidence": [],
        "issues": [],
    }

    WAF_SIGNATURES = {
        "Cloudflare": ["cf-ray", "cf-cache-status", "__cfduid", "cloudflare"],
        "AWS WAF": ["x-amzn-requestid", "x-amz-cf-id", "awswaf"],
        "Akamai": ["akamai", "x-akamai"],
        "Sucuri": ["x-sucuri", "sucuri"],
        "Wordfence": ["wordfence"],
        "ModSecurity": ["mod_security", "modsecurity"],
        "F5 BIG-IP": ["bigipserver", "x-wa-info"],
        "Barracuda": ["barra_counter_session"],
        "Imperva/Incapsula": ["incap_ses", "x-cdn", "imperva"],
        "Fastly": ["x-fastly", "fastly"],
    }

    # Check 1: Normal request headers
    try:
        resp = await stealth_request(url, timeout=10)
        headers = {k.lower(): v for k, v in resp.headers.items()}
        headers_str = str(headers).lower()
        body = resp.read().decode("utf-8", errors="replace").lower()

        for waf_name, signatures in WAF_SIGNATURES.items():
            for sig in signatures:
                if sig in headers_str or sig in body:
                    result["waf_detected"] = True
                    result["waf_name"] = waf_name
                    result["evidence"].append(f"Signature '{sig}' found in headers/body")
                    break
            if result["waf_detected"]:
                break
    except Exception:
        pass

    # Check 2: Send malicious-looking request and see if blocked
    if not result["waf_detected"]:
        test_payloads = [
            "/?test=<script>alert(1)</script>",
            "/?id=1' OR 1=1--",
            "/?file=../../etc/passwd",
        ]

        for payload in test_payloads:
            try:
                test_url = url.rstrip("/") + payload
                resp = await stealth_request(test_url, timeout=10)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    body = e.read().decode("utf-8", errors="replace").lower()
                    for waf_name, signatures in WAF_SIGNATURES.items():
                        for sig in signatures:
                            if sig in body:
                                result["waf_detected"] = True
                                result["waf_name"] = waf_name
                                result["evidence"].append(f"403 block with '{sig}' signature on attack payload")
                                break
                        if result["waf_detected"]:
                            break

                    if not result["waf_detected"]:
                        result["waf_detected"] = True
                        result["waf_name"] = "Unknown WAF"
                        result["evidence"].append(f"403 block on attack payload: {payload}")
                    break
            except Exception:
                pass

    if not result["waf_detected"]:
        result["issues"].append({
            "severity": "MEDIUM",
            "category": "WAF",
            "title": "No Web Application Firewall detected",
            "description": (
                "No WAF was detected protecting this website. A WAF provides an additional "
                "layer of defense against SQL injection, XSS, and other web attacks."
            ),
            "fix": "Consider deploying a WAF (Cloudflare, AWS WAF, ModSecurity, Wordfence for WordPress).",
        })

    return result


# ================================================================
# TOOL: rate_limit_check
# ================================================================

async def rate_limit_check(url: str) -> dict:
    """Test if the site has rate limiting on key endpoints."""
    import urllib.error

    result = {
        "url": url,
        "endpoints_tested": [],
        "issues": [],
    }

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    ENDPOINTS = [
        ("/wp-login.php", "WordPress Login", 10),
        ("/xmlrpc.php", "WordPress XMLRPC", 5),
        ("/", "Main Page", 20),
    ]

    for path, name, request_count in ENDPOINTS:
        endpoint_url = base_url + path
        endpoint_result = {
            "endpoint": path,
            "name": name,
            "requests_sent": 0,
            "requests_succeeded": 0,
            "rate_limited": False,
            "rate_limit_code": None,
        }

        start = time.time()

        for i in range(request_count):
            try:
                resp = await stealth_request(endpoint_url, timeout=5, delay=False)
                resp.read()
                endpoint_result["requests_sent"] += 1
                endpoint_result["requests_succeeded"] += 1
            except urllib.error.HTTPError as e:
                endpoint_result["requests_sent"] += 1
                if e.code == 429:
                    endpoint_result["rate_limited"] = True
                    endpoint_result["rate_limit_code"] = 429
                    break
                elif e.code == 403 and i > 3:
                    endpoint_result["rate_limited"] = True
                    endpoint_result["rate_limit_code"] = 403
                    break
                else:
                    endpoint_result["requests_succeeded"] += 1
            except Exception:
                endpoint_result["requests_sent"] += 1
                break

        elapsed = time.time() - start
        endpoint_result["elapsed_seconds"] = round(elapsed, 2)
        result["endpoints_tested"].append(endpoint_result)

    # Generate issues
    login_endpoint = next((e for e in result["endpoints_tested"] if "login" in e["name"].lower()), None)
    if login_endpoint and not login_endpoint["rate_limited"]:
        result["issues"].append({
            "severity": "HIGH",
            "category": "Rate Limiting",
            "title": f"No rate limiting on {login_endpoint['name']}",
            "description": (
                f"Sent {login_endpoint['requests_sent']} requests to {login_endpoint['endpoint']} "
                f"in {login_endpoint['elapsed_seconds']}s without being rate-limited. "
                "Automated brute-force attacks can run unrestricted."
            ),
            "fix": "Implement rate limiting (fail2ban, WordPress plugin, or nginx limit_req).",
            "nginx_fix": "limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;\nlocation /wp-login.php { limit_req zone=login burst=3 nodelay; }",
        })

    xmlrpc_endpoint = next((e for e in result["endpoints_tested"] if "xmlrpc" in e["name"].lower()), None)
    if xmlrpc_endpoint and not xmlrpc_endpoint["rate_limited"] and xmlrpc_endpoint["requests_succeeded"] > 3:
        result["issues"].append({
            "severity": "HIGH",
            "category": "Rate Limiting",
            "title": "No rate limiting on XMLRPC",
            "description": (
                f"XMLRPC endpoint accepts rapid requests without throttling. "
                "Combined with system.multicall, thousands of login attempts per minute are possible."
            ),
            "fix": "Block XMLRPC or add rate limiting.",
            "nginx_fix": "location /xmlrpc.php { return 403; }",
        })

    return result


# ================================================================
# TOOL: dns_zone_transfer
# ================================================================

async def dns_zone_transfer(domain: str) -> dict:
    """Test if DNS zone transfer (AXFR) is possible — reveals all DNS records."""
    result = {
        "domain": domain,
        "nameservers": [],
        "zone_transfer_possible": False,
        "records_leaked": [],
        "issues": [],
    }

    # Get nameservers
    try:
        proc = await asyncio.create_subprocess_exec(
            "nslookup", "-type=NS", domain,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode("utf-8", errors="replace")

        import re
        for line in output.splitlines():
            ns_match = re.search(r'nameserver\s*=\s*(\S+)', line, re.IGNORECASE)
            if ns_match:
                ns = ns_match.group(1).rstrip(".")
                result["nameservers"].append(ns)
    except Exception:
        pass

    # Try zone transfer on each nameserver
    for ns in result["nameservers"][:3]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "nslookup", "-type=AXFR", domain, ns,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout.decode("utf-8", errors="replace")

            # If we get actual records (not just refused/failed), zone transfer worked
            record_count = sum(1 for line in output.splitlines()
                             if any(rt in line for rt in ["IN A ", "IN MX ", "IN CNAME ", "IN TXT "]))

            if record_count > 2:
                result["zone_transfer_possible"] = True
                result["records_leaked"] = output.splitlines()[:30]
                result["issues"].append({
                    "severity": "CRITICAL",
                    "category": "DNS",
                    "title": f"DNS Zone Transfer possible on {ns}",
                    "description": (
                        f"Nameserver {ns} allows zone transfer (AXFR). "
                        f"All DNS records ({record_count}+) can be downloaded, revealing "
                        "complete infrastructure: subdomains, mail servers, internal hostnames."
                    ),
                    "fix": f"Restrict zone transfers on {ns} to authorized secondary nameservers only.",
                })
                break

        except Exception:
            pass

    return result


# ================================================================
# TOOL: breach_check
# ================================================================

async def breach_check(domain: str) -> dict:
    """Check if email addresses from the domain appear in known data breaches."""
    import urllib.error

    result = {
        "domain": domain,
        "breach_data_available": False,
        "breaches_found": [],
        "email_patterns_checked": [],
        "issues": [],
    }

    # Check Have I Been Pwned API (public domain search)
    try:
        api_url = f"https://haveibeenpwned.com/api/v3/breaches?domain={domain}"
        resp = await stealth_request(
            api_url, accept="json", timeout=10,
            extra_headers={"hibp-api-key": ""},
        )
        body = resp.read().decode("utf-8", errors="replace")
        breaches = json.loads(body)

        if breaches:
            result["breach_data_available"] = True
            for breach in breaches[:10]:
                result["breaches_found"].append({
                    "name": breach.get("Name", "?"),
                    "date": breach.get("BreachDate", "?"),
                    "pwn_count": breach.get("PwnCount", 0),
                    "data_classes": breach.get("DataClasses", []),
                })

            total_pwned = sum(b.get("PwnCount", 0) for b in breaches)
            result["issues"].append({
                "severity": "HIGH",
                "category": "Data Breach",
                "title": f"Domain found in {len(breaches)} data breach(es)",
                "description": (
                    f"Email addresses from {domain} appear in {len(breaches)} known data breaches "
                    f"affecting approximately {total_pwned:,} accounts. "
                    f"Breaches: {', '.join(b.get('Name', '?') for b in breaches[:5])}. "
                    "Compromised credentials may be used for credential stuffing attacks."
                ),
                "fix": (
                    "Force password resets for all affected users. "
                    "Implement 2FA. "
                    "Monitor for credential stuffing attempts. "
                    "Check specific emails at haveibeenpwned.com."
                ),
            })

    except urllib.error.HTTPError as e:
        if e.code == 404:
            pass  # No breaches found — good
        elif e.code == 401:
            result["issues"].append({
                "severity": "INFO",
                "category": "Data Breach",
                "title": "Breach check requires API key",
                "description": "HIBP API requires a paid API key for domain searches. Manual check at haveibeenpwned.com recommended.",
            })
    except Exception as e:
        result["issues"].append({
            "severity": "INFO",
            "category": "Data Breach",
            "title": "Breach check failed",
            "description": str(e),
        })

    return result


# ================================================================
# TOOL: sqli_check
# ================================================================

async def sqli_check(url: str) -> dict:
    """Test for SQL Injection indicators on common parameters."""
    import urllib.error
    import urllib.parse

    result = {
        "url": url,
        "tests_run": 0,
        "potential_injections": [],
        "issues": [],
    }

    # SQL error patterns that indicate the query reached the database
    SQL_ERRORS = [
        ("mysql", r"you have an error in your sql syntax"),
        ("mysql", r"warning.*mysql"),
        ("mysql", r"unclosed quotation mark"),
        ("mysql", r"mysql_fetch"),
        ("mysql", r"mysqli?_"),
        ("postgres", r"pg_query"),
        ("postgres", r"postgresql.*error"),
        ("postgres", r"unterminated quoted string"),
        ("mssql", r"microsoft.*odbc.*sql"),
        ("mssql", r"microsoft.*ole.*db"),
        ("mssql", r"\[sql server\]"),
        ("sqlite", r"sqlite.*error"),
        ("sqlite", r"sqlite3\."),
        ("generic", r"sql syntax.*error"),
        ("generic", r"syntax error.*sql"),
        ("generic", r"unrecognized token"),
        ("generic", r"unexpected end of sql"),
        ("generic", r"database error"),
        ("generic", r"db error"),
        ("generic", r"query failed"),
        ("wordpress", r"wpdb->"),
        ("wordpress", r"wp_"),
        ("wordpress", r"table.*doesn.*exist"),
    ]

    # Parameters to test
    PARAMS_TO_TEST = ["s", "p", "id", "page", "cat", "tag", "author", "product_id", "post"]

    # Injection payloads (safe — only trigger error messages, no data extraction)
    PAYLOADS = [
        ("single_quote", "'"),
        ("double_quote", '"'),
        ("comment", "1'--"),
        ("or_true", "1' OR '1'='1"),
        ("sleep_test", "1' AND SLEEP(0)--"),  # 0 seconds = safe, just checks syntax
    ]

    import re
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    for param in PARAMS_TO_TEST:
        for payload_name, payload in PAYLOADS:
            test_url = f"{base_url}/?{param}={urllib.parse.quote(payload)}"
            result["tests_run"] += 1

            try:
                start_time = time.time()
                resp = await stealth_request(test_url, timeout=15)
                elapsed = time.time() - start_time
                body = resp.read().decode("utf-8", errors="replace").lower()

                # Check for SQL error messages
                for db_type, pattern in SQL_ERRORS:
                    if re.search(pattern, body, re.IGNORECASE):
                        finding = {
                            "parameter": param,
                            "payload": payload_name,
                            "url": test_url,
                            "db_type": db_type,
                            "error_pattern": pattern,
                            "response_time": round(elapsed, 2),
                        }
                        result["potential_injections"].append(finding)
                        break  # One match per test is enough

                # Check for suspiciously long response time (time-based blind SQLi)
                if elapsed > 8 and "sleep" in payload_name:
                    result["potential_injections"].append({
                        "parameter": param,
                        "payload": payload_name,
                        "url": test_url,
                        "db_type": "time_based",
                        "error_pattern": f"Response took {elapsed:.1f}s (possible time-based injection)",
                        "response_time": round(elapsed, 2),
                    })

            except urllib.error.HTTPError as e:
                # 500 Internal Server Error on SQL payload = very suspicious
                if e.code == 500:
                    result["potential_injections"].append({
                        "parameter": param,
                        "payload": payload_name,
                        "url": test_url,
                        "db_type": "error_based",
                        "error_pattern": f"HTTP 500 on SQL payload (server crashed on input)",
                        "response_time": 0,
                    })
            except Exception:
                pass

    # Also test WordPress-specific endpoints
    wp_endpoints = [
        f"{base_url}/wp-json/wp/v2/posts?per_page=1'",
        f"{base_url}/wp-json/wp/v2/pages?per_page=1'",
        f"{base_url}/?author=1'",
    ]

    for endpoint in wp_endpoints:
        result["tests_run"] += 1
        try:
            resp = await stealth_request(endpoint, timeout=10)
            body = resp.read().decode("utf-8", errors="replace").lower()

            for db_type, pattern in SQL_ERRORS:
                if re.search(pattern, body, re.IGNORECASE):
                    result["potential_injections"].append({
                        "parameter": "wp_endpoint",
                        "payload": "single_quote",
                        "url": endpoint,
                        "db_type": db_type,
                        "error_pattern": pattern,
                        "response_time": 0,
                    })
                    break
        except urllib.error.HTTPError as e:
            if e.code == 500:
                result["potential_injections"].append({
                    "parameter": "wp_endpoint",
                    "payload": "single_quote",
                    "url": endpoint,
                    "db_type": "error_based",
                    "error_pattern": f"HTTP 500 on WP endpoint with quote injection",
                    "response_time": 0,
                })
        except Exception:
            pass

    # Generate issues
    if result["potential_injections"]:
        # Deduplicate by parameter
        affected_params = list(set(pi["parameter"] for pi in result["potential_injections"]))
        db_types = list(set(pi["db_type"] for pi in result["potential_injections"]))

        error_based = [pi for pi in result["potential_injections"] if pi["db_type"] != "time_based"]
        time_based = [pi for pi in result["potential_injections"] if pi["db_type"] == "time_based"]

        if error_based:
            result["issues"].append({
                "severity": "CRITICAL",
                "category": "SQL Injection",
                "title": f"SQL error messages on parameters: {', '.join(affected_params)}",
                "description": (
                    f"SQL injection payloads triggered database error messages on parameter(s) "
                    f"{', '.join(affected_params)}. Database type: {', '.join(db_types)}. "
                    "This confirms that user input reaches the SQL query without proper sanitization. "
                    "An attacker can extract the entire database contents including user credentials."
                ),
                "fix": (
                    "Use parameterized queries (prepared statements) for ALL database queries. "
                    "In WordPress: always use $wpdb->prepare(). "
                    "Update all plugins to latest versions. "
                    "Disable detailed error messages in production (WP_DEBUG = false)."
                ),
            })

        if time_based:
            result["issues"].append({
                "severity": "HIGH",
                "category": "SQL Injection",
                "title": "Possible time-based blind SQL injection",
                "description": (
                    "Suspicious response time delays detected when sending SQL SLEEP payloads. "
                    "This may indicate blind SQL injection where the attacker can extract data "
                    "one character at a time by measuring response times."
                ),
                "fix": "Use parameterized queries. Implement query timeouts.",
            })
    else:
        result["issues"].append({
            "severity": "INFO",
            "category": "SQL Injection",
            "title": f"No SQL injection indicators found ({result['tests_run']} tests)",
            "description": "No SQL error messages or timing anomalies detected. Basic SQL injection appears mitigated.",
        })

    return result


# ================================================================
# TOOL: xss_reflection_check
# ================================================================

async def xss_reflection_check(url: str) -> dict:
    """Test if a site reflects user input in responses (XSS vector detection)."""
    import urllib.error

    result = {
        "url": url,
        "reflections_found": [],
        "forms": [],
        "total_vectors_tested": 0,
        "issues": [],
    }

    CANARY = "XSSCANARY" + str(hash(url) % 9999)

    TEST_VECTORS = [
        ("Search ?s=", "/?s=" + CANARY),
        ("Search ?q=", "/?q=" + CANARY),
        ("Search ?search=", "/?search=" + CANARY),
        ("Query ?query=", "/?query=" + CANARY),
        ("Page ?p=", "/?p=" + CANARY),
        ("ID ?id=", "/?id=" + CANARY),
        ("404 path reflection", "/" + CANARY),
        ("Redirect ?redirect_to=", "/?redirect_to=" + CANARY),
        ("Callback ?callback=", "/?callback=" + CANARY),
    ]

    result["total_vectors_tested"] = len(TEST_VECTORS)

    # Pre-compute baseline to detect SPA catch-all
    baseline = await _get_baseline(url)

    for name, path in TEST_VECTORS:
        test_url = url.rstrip("/") + path
        try:
            body = await stealth_fetch(test_url, timeout=10)

            # Soft-404 check: if the response is just the SPA homepage, the
            # canary appearing means the SPA framework puts URL fragments
            # into the DOM (e.g. meta tags, title) — not a real server-side reflection
            if baseline["is_catchall"]:
                body_hash = hashlib.md5(body.encode()).hexdigest()
                if body_hash == baseline["homepage_hash"]:
                    continue  # Identical to homepage, canary can't be reflected

            if CANARY in body:
                idx = body.index(CANARY)
                context = body[max(0, idx - 100):idx + len(CANARY) + 100]

                # Determine context
                import re
                in_script = "<script" in body[max(0, idx - 300):idx].lower()
                in_attr = bool(re.search(
                    r'(?:value|content|alt|title|href|src|action)=["\'][^"\']*' + CANARY,
                    context, re.IGNORECASE
                ))
                in_tag = bool(re.search(r'<[^>]*' + CANARY, context))

                # Check if HTML-encoded
                encoded_canary = CANARY.replace("<", "&lt;").replace(">", "&gt;")
                is_encoded = ("&lt;" in context or "&gt;" in context or "&#" in context)

                if in_script:
                    context_type = "inside_script"
                    severity = "CRITICAL"
                elif in_attr:
                    context_type = "inside_attribute"
                    severity = "HIGH" if not is_encoded else "MEDIUM"
                elif in_tag:
                    context_type = "inside_tag"
                    severity = "HIGH" if not is_encoded else "MEDIUM"
                else:
                    context_type = "plain_text"
                    severity = "MEDIUM" if not is_encoded else "LOW"

                reflection = {
                    "vector_name": name,
                    "url": test_url,
                    "context_type": context_type,
                    "is_html_encoded": is_encoded,
                    "surrounding_html": context.strip()[:200],
                    "severity": severity,
                }
                result["reflections_found"].append(reflection)

        except urllib.error.HTTPError:
            pass
        except Exception:
            pass

    # Analyze forms on the page
    try:
        html = await stealth_fetch(url, timeout=10)
        forms = re.findall(r'<form[^>]*>(.*?)</form>', html, re.IGNORECASE | re.DOTALL)

        for i, form_html in enumerate(forms):
            action_match = re.search(r'action=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
            method_match = re.search(r'method=["\']([^"\']*)["\']', form_html, re.IGNORECASE)

            text_inputs = re.findall(
                r'<(?:input[^>]+type=["\'](?:text|search|email|url|tel)["\'][^>]*|textarea[^>]*)>',
                form_html, re.IGNORECASE
            )

            if text_inputs:
                input_names = []
                for inp in text_inputs:
                    name_match = re.search(r'name=["\']([^"\']*)["\']', inp, re.IGNORECASE)
                    if name_match:
                        input_names.append(name_match.group(1))

                result["forms"].append({
                    "form_index": i + 1,
                    "action": action_match.group(1) if action_match else "(self)",
                    "method": (method_match.group(1).upper() if method_match else "GET"),
                    "text_input_count": len(text_inputs),
                    "input_names": input_names,
                })

    except Exception:
        pass

    # Generate issues
    if result["reflections_found"]:
        # Group by severity
        critical = [r for r in result["reflections_found"] if r["severity"] == "CRITICAL"]
        high = [r for r in result["reflections_found"] if r["severity"] == "HIGH"]
        encoded = [r for r in result["reflections_found"] if r["is_html_encoded"]]
        unencoded = [r for r in result["reflections_found"] if not r["is_html_encoded"]]

        if critical:
            result["issues"].append({
                "severity": "CRITICAL",
                "category": "XSS",
                "title": f"User input reflected inside <script> tag",
                "description": (
                    f"Input is reflected inside a script context at: "
                    f"{', '.join(r['vector_name'] for r in critical)}. "
                    "Direct JavaScript injection is likely possible."
                ),
                "fix": "Escape all user input in script contexts. Implement CSP header.",
            })
        elif unencoded:
            result["issues"].append({
                "severity": "HIGH",
                "category": "XSS",
                "title": f"User input reflected WITHOUT HTML encoding",
                "description": (
                    f"Input is reflected without encoding at: "
                    f"{', '.join(r['vector_name'] for r in unencoded)}. "
                    "HTML/JavaScript injection may be possible."
                ),
                "fix": "HTML-encode all user input before rendering. Add Content-Security-Policy header.",
            })
        elif encoded:
            result["issues"].append({
                "severity": "LOW",
                "category": "XSS",
                "title": f"User input reflected (HTML-encoded) in {len(encoded)} location(s)",
                "description": (
                    f"Input is reflected but HTML-encoded at: "
                    f"{', '.join(r['vector_name'] for r in encoded)}. "
                    "Encoding prevents direct XSS, but combined with outdated JavaScript "
                    "libraries (e.g. jQuery < 3.5) or DOM manipulation, bypass may be possible."
                ),
                "fix": "Update JavaScript libraries. Add Content-Security-Policy header as defense-in-depth.",
            })

    if result["forms"]:
        get_forms = [f for f in result["forms"] if f["method"] == "GET"]
        if get_forms:
            names = []
            for f in get_forms:
                names.extend(f["input_names"])
            if names:
                result["issues"].append({
                    "severity": "LOW",
                    "category": "XSS",
                    "title": f"GET forms with text inputs: {', '.join(names[:5])}",
                    "description": (
                        "Forms using GET method place user input in the URL, making reflected XSS "
                        "easier to exploit via crafted links."
                    ),
                    "fix": "Use POST method for forms where possible. Validate and encode all input.",
                })

    return result


# ================================================================
# TOOL: cms_version_detect
# ================================================================

async def cms_version_detect(url: str) -> dict:
    """Detect CMS, framework, and library versions from HTML and headers."""

    result = {
        "url": url,
        "cms": None,
        "cms_version": None,
        "php_version": None,
        "server_software": None,
        "javascript_libraries": [],
        "outdated_libraries": [],
        "wordpress_details": {},
        "issues": [],
    }

    try:
        resp = await stealth_request(url, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        headers = dict(resp.headers)

        # Server / PHP from headers
        result["server_software"] = headers.get("Server")
        php_header = headers.get("X-Powered-By", "")
        if "PHP" in php_header:
            result["php_version"] = php_header

        # --- WordPress Detection ---
        wp_indicators = [
            "/wp-content/", "/wp-includes/", "wp-json",
            "wordpress", "wp-login.php",
        ]
        if any(ind in html.lower() for ind in wp_indicators):
            result["cms"] = "WordPress"

            # WP version from meta generator
            gen_match = re.search(
                r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s*([\d.]+)',
                html, re.IGNORECASE
            )
            if gen_match:
                result["cms_version"] = gen_match.group(1)
                result["wordpress_details"]["version_source"] = "meta generator"

            # WP version from ?ver= params
            ver_matches = re.findall(r'\?ver=([\d.]+)', html)
            if ver_matches:
                # Highest version is likely WP version
                versions = sorted(set(ver_matches), reverse=True)
                result["wordpress_details"]["resource_versions"] = versions[:5]
                if not result["cms_version"] and versions:
                    result["cms_version"] = versions[0]
                    result["wordpress_details"]["version_source"] = "resource ver param"

            # WP REST API
            if "/wp-json/" in html or "wp-json" in html:
                result["wordpress_details"]["rest_api_exposed"] = True

            # WP Users enumeration endpoint
            try:
                users_body = await stealth_fetch(
                    url.rstrip("/") + "/wp-json/wp/v2/users",
                    accept="json", timeout=10,
                )
                if users_body.startswith("["):
                    import json as _json
                    users_data = _json.loads(users_body)
                    usernames = [u.get("slug", u.get("name", "?")) for u in users_data[:5]]
                    result["wordpress_details"]["exposed_users"] = usernames
                    result["issues"].append({
                        "severity": "HIGH",
                        "category": "Information Disclosure",
                        "title": f"WordPress user enumeration: {', '.join(usernames)}",
                        "description": (
                            "The WP REST API exposes usernames at /wp-json/wp/v2/users. "
                            "Attackers use these for targeted brute-force attacks."
                        ),
                        "fix": "Disable user enumeration: add_filter('rest_endpoints', function($endpoints) { unset($endpoints['/wp/v2/users']); return $endpoints; });",
                    })
            except Exception:
                result["wordpress_details"]["user_enum_blocked"] = True

            # WP XMLRPC (brute-force vector)
            try:
                xmlrpc_resp = await stealth_head(
                    url.rstrip("/") + "/xmlrpc.php", timeout=10,
                )
                if xmlrpc_resp.status == 200:
                    result["wordpress_details"]["xmlrpc_enabled"] = True
                    result["issues"].append({
                        "severity": "HIGH",
                        "category": "Authentication",
                        "title": "WordPress XMLRPC enabled",
                        "description": (
                            "xmlrpc.php is accessible. Attackers use system.multicall to try "
                            "thousands of passwords in a single request, bypassing rate limiting."
                        ),
                        "fix": "Disable XMLRPC: add_filter('xmlrpc_enabled', '__return_false'); or block in nginx: location /xmlrpc.php { return 403; }",
                        "nginx_fix": "location /xmlrpc.php { return 403; }",
                    })
            except Exception:
                result["wordpress_details"]["xmlrpc_blocked"] = True

        # --- JavaScript Libraries ---
        import re

        # jQuery
        jquery_matches = re.findall(
            r'jquery[.-]?([\d.]+)(?:\.min)?\.js', html, re.IGNORECASE
        )
        if jquery_matches:
            for ver in set(jquery_matches):
                result["javascript_libraries"].append({"name": "jQuery", "version": ver})
                # jQuery < 3.5.0 has known XSS vulnerabilities
                try:
                    major, minor = int(ver.split(".")[0]), int(ver.split(".")[1])
                    if major < 3 or (major == 3 and minor < 5):
                        result["outdated_libraries"].append({
                            "name": "jQuery",
                            "version": ver,
                            "latest": "3.7.x",
                            "risk": "Known XSS vulnerabilities (CVE-2020-11022, CVE-2020-11023)",
                        })
                        result["issues"].append({
                            "severity": "HIGH",
                            "category": "Vulnerable Library",
                            "title": f"Outdated jQuery {ver} (XSS vulnerable)",
                            "description": f"jQuery {ver} has known XSS vulnerabilities. Current version is 3.7.x.",
                            "fix": f"Update jQuery from {ver} to latest 3.7.x",
                        })
                except (ValueError, IndexError):
                    pass

        # Bootstrap
        bootstrap_matches = re.findall(
            r'bootstrap[.-]?([\d.]+)(?:\.min)?\.(?:js|css)', html, re.IGNORECASE
        )
        if bootstrap_matches:
            for ver in set(bootstrap_matches):
                result["javascript_libraries"].append({"name": "Bootstrap", "version": ver})

        # React
        react_match = re.search(r'react(?:\.production)?[.-]?([\d.]+)', html, re.IGNORECASE)
        if react_match:
            result["javascript_libraries"].append({"name": "React", "version": react_match.group(1)})

        # CMS version issues
        if result["cms"] == "WordPress" and result["cms_version"]:
            result["issues"].append({
                "severity": "MEDIUM",
                "category": "CMS",
                "title": f"WordPress version exposed: {result['cms_version']}",
                "description": "WordPress version is publicly visible. Attackers can look up known CVEs for this specific version.",
                "fix": "Remove version from meta generator: remove_action('wp_head', 'wp_generator');",
            })

    except Exception as e:
        result["issues"].append({
            "severity": "INFO",
            "category": "CMS Detection",
            "title": "CMS detection failed",
            "description": str(e),
        })

    return result


# ================================================================
# TOOL: login_security_check
# ================================================================

async def login_security_check(url: str) -> dict:
    """Check login page for security features: rate limiting, captcha, 2FA hints."""

    result = {
        "url": url,
        "login_page_found": False,
        "login_url": None,
        "has_captcha": False,
        "has_2fa_hint": False,
        "has_rate_limiting": False,
        "csrf_token_present": False,
        "autocomplete_password": None,
        "issues": [],
    }

    login_paths = ["/wp-login.php", "/admin/login", "/login", "/user/login", "/signin"]

    for path in login_paths:
        login_url = url.rstrip("/") + path
        try:
            resp = await stealth_request(login_url, timeout=10)

            if resp.status != 200:
                continue

            html = resp.read().decode("utf-8", errors="replace")

            # Check if it's actually a login form
            import re
            if not re.search(r'type=["\']password["\']', html, re.IGNORECASE):
                continue

            result["login_page_found"] = True
            result["login_url"] = login_url

            html_lower = html.lower()

            # Captcha
            captcha_indicators = [
                "recaptcha", "hcaptcha", "captcha", "turnstile",
                "g-recaptcha", "cf-turnstile", "challenge",
            ]
            result["has_captcha"] = any(ind in html_lower for ind in captcha_indicators)

            # 2FA hints
            twofa_indicators = [
                "two-factor", "2fa", "authenticator", "verification code",
                "otp", "one-time", "zweifaktor",
            ]
            result["has_2fa_hint"] = any(ind in html_lower for ind in twofa_indicators)

            # CSRF token
            csrf_indicators = [
                'name="_token"', 'name="csrf_token"', 'name="_csrf"',
                'name="csrfmiddlewaretoken"', "wp_nonce", "_wpnonce",
            ]
            result["csrf_token_present"] = any(ind in html_lower for ind in csrf_indicators)

            # Autocomplete on password
            pwd_match = re.search(
                r'<input[^>]*type=["\']password["\'][^>]*>', html, re.IGNORECASE
            )
            if pwd_match:
                pwd_tag = pwd_match.group(0).lower()
                if 'autocomplete="off"' in pwd_tag or "autocomplete='off'" in pwd_tag:
                    result["autocomplete_password"] = "off"
                else:
                    result["autocomplete_password"] = "on (default)"

            # Generate issues
            if not result["has_captcha"]:
                result["issues"].append({
                    "severity": "HIGH",
                    "category": "Authentication",
                    "title": f"Login page without CAPTCHA: {path}",
                    "description": (
                        "No CAPTCHA or challenge detected on the login form. "
                        "Automated brute-force attacks can try thousands of passwords per minute."
                    ),
                    "fix": "Add Google reCAPTCHA, hCaptcha, or Cloudflare Turnstile to the login form.",
                })

            if not result["has_2fa_hint"]:
                result["issues"].append({
                    "severity": "MEDIUM",
                    "category": "Authentication",
                    "title": "No two-factor authentication detected",
                    "description": (
                        "No indicators of 2FA/MFA found on the login page. "
                        "A compromised password gives immediate full access."
                    ),
                    "fix": "Implement 2FA (TOTP, WebAuthn, or SMS) for all admin accounts.",
                })

            if not result["csrf_token_present"]:
                result["issues"].append({
                    "severity": "MEDIUM",
                    "category": "Authentication",
                    "title": "No CSRF token on login form",
                    "description": "Login form appears to lack CSRF protection. Login CSRF attacks are possible.",
                    "fix": "Add CSRF token validation to the login form.",
                })

            # Found a login page, no need to check more
            break

        except Exception:
            continue

    if not result["login_page_found"]:
        # Not an issue, just means no standard login path
        pass

    return result


# ================================================================
# TOOL: subdomain_content_scan
# ================================================================

async def subdomain_content_scan(subdomains: str) -> dict:
    """Scan discovered subdomains for exposed panels, debug modes, and sensitive content."""

    subdomain_list = [s.strip() for s in subdomains.split(",") if s.strip()]

    result = {
        "scanned": [],
        "issues": [],
    }

    async def scan_subdomain(fqdn):
        entry = {
            "subdomain": fqdn,
            "reachable_http": False,
            "reachable_https": False,
            "title": None,
            "server": None,
            "status_code": None,
            "is_same_as_main": False,
            "findings": [],
        }

        for scheme in ["https", "http"]:
            sub_url = f"{scheme}://{fqdn}"
            try:
                resp = await stealth_request(sub_url, timeout=10)
                html = resp.read().decode("utf-8", errors="replace")
                headers = dict(resp.headers)

                if scheme == "https":
                    entry["reachable_https"] = True
                else:
                    entry["reachable_http"] = True

                entry["status_code"] = resp.status
                entry["server"] = headers.get("Server")

                import re
                title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
                if title_match:
                    entry["title"] = title_match.group(1).strip()[:200]

                html_lower = html.lower()

                # Check for debug/dev indicators
                debug_indicators = [
                    ("debug", "Debug mode indicator found"),
                    ("stack trace", "Stack trace visible"),
                    ("traceback", "Python traceback visible"),
                    ("exception", "Exception details visible"),
                    ("phpinfo", "PHP info page"),
                    ("xdebug", "Xdebug enabled"),
                    ("development mode", "Development mode active"),
                    ("staging", "Staging environment label"),
                    ("test environment", "Test environment label"),
                    ("not for production", "Non-production warning found"),
                ]
                for keyword, description in debug_indicators:
                    if keyword in html_lower:
                        entry["findings"].append(description)

                # Check for directory listing
                if "index of /" in html_lower or "parent directory" in html_lower:
                    entry["findings"].append("Directory listing enabled")

                # Check for default pages
                if "it works!" in html_lower or "welcome to nginx" in html_lower or "apache2 default" in html_lower:
                    entry["findings"].append("Default server page (unconfigured)")

                break  # If HTTPS works, no need to try HTTP

            except Exception:
                continue

        return entry

    tasks = [scan_subdomain(fqdn) for fqdn in subdomain_list]
    entries = await asyncio.gather(*tasks)

    for entry in entries:
        result["scanned"].append(entry)

        if entry["findings"]:
            for finding in entry["findings"]:
                sev = "HIGH" if any(kw in finding.lower() for kw in ["debug", "trace", "xdebug", "phpinfo"]) else "MEDIUM"
                result["issues"].append({
                    "severity": sev,
                    "category": "Subdomain Exposure",
                    "title": f"{entry['subdomain']}: {finding}",
                    "description": f"Subdomain {entry['subdomain']} shows: {finding}. This may expose internal information or provide attack vectors.",
                    "fix": f"Restrict access to {entry['subdomain']} via IP whitelist or authentication, or remove if unused.",
                })

        # Staging/dev/test reachable without auth
        fqdn_lower = entry["subdomain"].lower()
        if any(kw in fqdn_lower for kw in ["staging", "dev", "test", "beta", "demo"]):
            if entry["reachable_https"] or entry["reachable_http"]:
                result["issues"].append({
                    "severity": "HIGH",
                    "category": "Subdomain Exposure",
                    "title": f"{entry['subdomain']} is publicly accessible",
                    "description": (
                        f"Non-production subdomain {entry['subdomain']} is reachable without authentication. "
                        "Staging/dev environments often have weaker security, test credentials, or debug modes enabled."
                    ),
                    "fix": f"Restrict {entry['subdomain']} behind VPN, IP whitelist, or HTTP Basic Auth.",
                    "nginx_fix": f"# For {entry['subdomain']}\nauth_basic \"Restricted\";\nauth_basic_user_file /etc/nginx/.htpasswd;",
                })

    return result


# ================================================================
# TOOL: robots_sitemap_scan
# ================================================================

async def robots_sitemap_scan(url: str) -> dict:
    """Analyze robots.txt and sitemap.xml for exposed paths and misconfigurations."""
    import urllib.error
    from urllib.parse import urljoin

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    result = {
        "url": url,
        "robots_txt": {"found": False, "content": None, "disallowed_paths": [], "sitemaps": []},
        "sitemap": {"found": False, "urls_count": 0, "sample_urls": []},
        "exposed_sensitive_paths": [],
        "issues": [],
        "warnings": [],
    }

    SENSITIVE_PATTERNS = [
        "admin", "login", "dashboard", "wp-admin", "phpmyadmin",
        "cpanel", "webmail", "api", "graphql", "debug",
        "staging", "test", "dev", "backup", ".git", ".env",
        "config", "setup", "install", "database", "db",
        "secret", "private", "internal", "upload", "editor",
    ]

    # --- robots.txt ---
    try:
        robots_url = f"{base}/robots.txt"
        robots_content = await stealth_fetch(robots_url, timeout=10)
        result["robots_txt"]["found"] = True
        result["robots_txt"]["content"] = robots_content[:3000]

        for line in robots_content.splitlines():
            line = line.strip()
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    result["robots_txt"]["disallowed_paths"].append(path)
                    path_lower = path.lower()
                    for pattern in SENSITIVE_PATTERNS:
                        if pattern in path_lower:
                            result["exposed_sensitive_paths"].append({
                                "path": path,
                                "source": "robots.txt Disallow",
                                "concern": f"Sensitive path '{pattern}' exposed in robots.txt — attackers check this first",
                            })
                            break
            elif line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                # Fix: sitemap line has format "Sitemap: https://..."
                if not sitemap_url.startswith("http"):
                    sitemap_url = "https:" + sitemap_url
                result["robots_txt"]["sitemaps"].append(sitemap_url)

        if not result["robots_txt"]["disallowed_paths"]:
            result["issues"].append({
                "severity": "LOW",
                "category": "Information Disclosure",
                "title": "robots.txt has no Disallow rules",
                "description": "All paths are crawlable. Consider restricting admin/internal paths.",
            })

    except urllib.error.HTTPError as e:
        if e.code == 404:
            result["issues"].append({
                "severity": "INFO",
                "category": "Configuration",
                "title": "No robots.txt found",
                "description": "Missing robots.txt. Not a vulnerability but recommended for SEO and path control.",
            })
    except Exception as e:
        result["warnings"].append(f"robots.txt check failed: {e}")

    # --- sitemap.xml ---
    sitemap_urls_to_check = result["robots_txt"]["sitemaps"] or [f"{base}/sitemap.xml"]

    for sitemap_url in sitemap_urls_to_check[:3]:
        try:
            sitemap_content = await stealth_fetch(sitemap_url, timeout=10)
            result["sitemap"]["found"] = True

            import re
            urls_in_sitemap = re.findall(r"<loc>(.*?)</loc>", sitemap_content)
            result["sitemap"]["urls_count"] = len(urls_in_sitemap)
            result["sitemap"]["sample_urls"] = urls_in_sitemap[:20]

            for u in urls_in_sitemap:
                u_lower = u.lower()
                for pattern in SENSITIVE_PATTERNS:
                    if pattern in u_lower:
                        result["exposed_sensitive_paths"].append({
                            "path": u,
                            "source": "sitemap.xml",
                            "concern": f"Sensitive URL with '{pattern}' in sitemap — publicly indexed",
                        })
                        break

        except Exception:
            pass

    if result["exposed_sensitive_paths"]:
        result["issues"].append({
            "severity": "MEDIUM",
            "category": "Information Disclosure",
            "title": f"{len(result['exposed_sensitive_paths'])} sensitive paths exposed",
            "description": "Sensitive paths found in robots.txt or sitemap.xml. Attackers use these as reconnaissance targets.",
        })

    return result


# ================================================================
# TOOL: subdomain_enum
# ================================================================

async def subdomain_enum(domain: str) -> dict:
    """Enumerate common subdomains via DNS resolution."""
    result = {
        "domain": domain,
        "found_subdomains": [],
        "total_checked": 0,
        "total_found": 0,
        "issues": [],
        "warnings": [],
    }

    COMMON_SUBDOMAINS = [
        "www", "mail", "ftp", "localhost", "webmail", "smtp", "pop", "ns1", "ns2",
        "admin", "portal", "api", "dev", "staging", "test", "beta", "demo",
        "app", "m", "mobile", "cdn", "static", "media", "img", "images",
        "blog", "shop", "store", "cms", "crm", "erp",
        "vpn", "remote", "gateway", "proxy",
        "db", "database", "sql", "mysql", "postgres", "mongo", "redis",
        "git", "gitlab", "github", "jenkins", "ci", "cd", "deploy",
        "monitor", "grafana", "prometheus", "kibana", "elastic",
        "backup", "bak", "old", "legacy", "archive",
        "internal", "intranet", "extranet", "private",
        "owa", "exchange", "autodiscover", "cpanel", "whm",
        "status", "health", "docs", "wiki",
    ]

    RISKY_SUBDOMAINS = {
        "admin", "staging", "test", "dev", "beta", "demo",
        "db", "database", "sql", "mysql", "postgres", "mongo", "redis",
        "jenkins", "git", "gitlab", "backup", "bak", "old", "legacy",
        "internal", "intranet", "private", "cpanel", "whm", "phpmyadmin",
    }

    import socket

    async def check_subdomain(sub):
        fqdn = f"{sub}.{domain}"
        try:
            ip = await asyncio.get_event_loop().run_in_executor(
                None, lambda: socket.gethostbyname(fqdn)
            )
            return {"subdomain": fqdn, "ip": ip, "risky": sub in RISKY_SUBDOMAINS}
        except socket.gaierror:
            return None

    # Run checks with concurrency limit
    sem = asyncio.Semaphore(20)

    async def limited_check(sub):
        async with sem:
            return await check_subdomain(sub)

    tasks = [limited_check(sub) for sub in COMMON_SUBDOMAINS]
    results = await asyncio.gather(*tasks)

    result["total_checked"] = len(COMMON_SUBDOMAINS)

    for r in results:
        if r:
            result["found_subdomains"].append(r)

    result["total_found"] = len(result["found_subdomains"])

    risky = [s for s in result["found_subdomains"] if s["risky"]]
    if risky:
        names = ", ".join(s["subdomain"] for s in risky[:5])
        result["issues"].append({
            "severity": "HIGH",
            "category": "Attack Surface",
            "title": f"{len(risky)} risky subdomain(s) found",
            "description": f"Potentially sensitive subdomains are publicly resolvable: {names}. These may expose admin panels, databases, or development environments.",
        })

    return result


# ================================================================
# TOOL: cors_check
# ================================================================

async def cors_check(url: str) -> dict:
    """Test for CORS misconfigurations."""

    result = {
        "url": url,
        "cors_enabled": False,
        "allows_any_origin": False,
        "allows_credentials_with_wildcard": False,
        "reflects_origin": False,
        "allows_null_origin": False,
        "issues": [],
        "details": {},
    }

    test_origins = [
        "https://evil-attacker.com",
        "null",
        url.replace("https://", "http://"),  # HTTP version
    ]

    for origin in test_origins:
        try:
            resp = await stealth_request(
                url, timeout=10,
                extra_headers={"Origin": origin},
            )
            headers = dict(resp.headers)

            acao = headers.get("Access-Control-Allow-Origin", "")
            acac = headers.get("Access-Control-Allow-Credentials", "")

            if acao:
                result["cors_enabled"] = True
                result["details"][origin] = {"acao": acao, "acac": acac}

                if acao == "*":
                    result["allows_any_origin"] = True
                    if acac.lower() == "true":
                        result["allows_credentials_with_wildcard"] = True

                if acao == origin and origin == "https://evil-attacker.com":
                    result["reflects_origin"] = True

                if acao == "null" or (origin == "null" and acao):
                    result["allows_null_origin"] = True

        except Exception:
            pass

    # Generate issues
    if result["allows_credentials_with_wildcard"]:
        result["issues"].append({
            "severity": "CRITICAL",
            "category": "CORS",
            "title": "CORS allows credentials with wildcard origin",
            "description": "Access-Control-Allow-Origin: * combined with Access-Control-Allow-Credentials: true. Any website can make authenticated requests and steal data.",
            "fix": "Never combine wildcard origin with credentials. Whitelist specific trusted origins.",
        })

    if result["reflects_origin"]:
        result["issues"].append({
            "severity": "HIGH",
            "category": "CORS",
            "title": "CORS reflects arbitrary Origin header",
            "description": "Server reflects any Origin back in Access-Control-Allow-Origin. An attacker's site can make cross-origin requests as the victim.",
            "fix": "Implement an explicit whitelist of allowed origins instead of reflecting the Origin header.",
        })

    if result["allows_null_origin"]:
        result["issues"].append({
            "severity": "MEDIUM",
            "category": "CORS",
            "title": "CORS allows null Origin",
            "description": "Server accepts 'null' as a valid origin. Sandboxed iframes and redirects send 'null' origin, enabling certain attack scenarios.",
            "fix": "Do not allow 'null' as a valid origin in CORS configuration.",
        })

    return result


# ================================================================
# TOOL: port_scan
# ================================================================

async def port_scan(domain: str, ports: str = "common") -> dict:
    """Scan common ports on a domain to find exposed services."""
    result = {
        "domain": domain,
        "open_ports": [],
        "closed_ports": [],
        "total_scanned": 0,
        "issues": [],
        "warnings": [],
    }

    COMMON_PORTS = {
        21: ("FTP", "HIGH"),
        22: ("SSH", "MEDIUM"),
        23: ("Telnet", "CRITICAL"),
        25: ("SMTP", "MEDIUM"),
        53: ("DNS", "LOW"),
        80: ("HTTP", "INFO"),
        110: ("POP3", "MEDIUM"),
        143: ("IMAP", "MEDIUM"),
        443: ("HTTPS", "INFO"),
        445: ("SMB", "CRITICAL"),
        993: ("IMAPS", "INFO"),
        995: ("POP3S", "INFO"),
        1433: ("MSSQL", "CRITICAL"),
        1434: ("MSSQL Browser", "HIGH"),
        3306: ("MySQL", "CRITICAL"),
        3389: ("RDP", "CRITICAL"),
        5432: ("PostgreSQL", "CRITICAL"),
        5900: ("VNC", "CRITICAL"),
        6379: ("Redis", "CRITICAL"),
        8080: ("HTTP-Alt", "MEDIUM"),
        8443: ("HTTPS-Alt", "LOW"),
        8888: ("HTTP-Alt/Jupyter", "HIGH"),
        9090: ("Prometheus", "HIGH"),
        9200: ("Elasticsearch", "CRITICAL"),
        27017: ("MongoDB", "CRITICAL"),
    }

    if ports == "common":
        port_list = list(COMMON_PORTS.keys())
    else:
        port_list = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]

    result["total_scanned"] = len(port_list)

    sem = asyncio.Semaphore(30)

    async def check_port(port):
        async with sem:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(domain, port), timeout=3.0
                )
                # Try banner grab
                banner = None
                try:
                    data = await asyncio.wait_for(reader.read(1024), timeout=2.0)
                    if data:
                        banner = data.decode("utf-8", errors="replace").strip()[:200]
                except Exception:
                    pass
                writer.close()
                await writer.wait_closed()

                service, severity = COMMON_PORTS.get(port, ("Unknown", "MEDIUM"))
                return {"port": port, "state": "open", "service": service, "banner": banner, "severity": severity}
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                return {"port": port, "state": "closed"}

    tasks = [check_port(p) for p in port_list]
    results = await asyncio.gather(*tasks)

    for r in results:
        if r["state"] == "open":
            result["open_ports"].append(r)
        else:
            result["closed_ports"].append(r["port"])

    # Generate issues for dangerous open ports
    SAFE_PORTS = {80, 443, 993, 995}
    for op in result["open_ports"]:
        if op["port"] not in SAFE_PORTS:
            sev = op.get("severity", "MEDIUM")
            result["issues"].append({
                "severity": sev,
                "category": "Network Exposure",
                "title": f"Port {op['port']} ({op['service']}) is open",
                "description": f"Service {op['service']} on port {op['port']} is publicly accessible. Banner: {op.get('banner', 'none')}",
                "fix": f"Restrict port {op['port']} via firewall unless public access is required. Use VPN for admin services.",
            })

    return result


# ================================================================
# TOOL: path_discovery
# ================================================================

async def path_discovery(url: str) -> dict:
    """Check for common sensitive paths and files."""
    import urllib.error
    from urllib.parse import urljoin

    result = {
        "url": url,
        "found_paths": [],
        "checked_count": 0,
        "issues": [],
    }

    PATHS_TO_CHECK = [
        # Version control
        ("/.git/HEAD", "CRITICAL", "Git repository exposed — source code leak"),
        ("/.git/config", "CRITICAL", "Git config exposed — may contain credentials"),
        ("/.svn/entries", "CRITICAL", "SVN repository exposed"),
        # Environment / Config
        ("/.env", "CRITICAL", "Environment file exposed — likely contains secrets"),
        ("/.env.local", "CRITICAL", "Local env file exposed"),
        ("/.env.production", "CRITICAL", "Production env file exposed"),
        ("/config.php", "HIGH", "PHP config file exposed"),
        ("/wp-config.php", "CRITICAL", "WordPress config exposed — database credentials"),
        ("/web.config", "HIGH", "IIS web.config exposed"),
        # Backups
        ("/backup.sql", "CRITICAL", "Database backup file exposed"),
        ("/dump.sql", "CRITICAL", "Database dump exposed"),
        ("/database.sql", "CRITICAL", "Database file exposed"),
        ("/backup.zip", "HIGH", "Backup archive exposed"),
        ("/backup.tar.gz", "HIGH", "Backup archive exposed"),
        # Admin panels
        ("/admin", "MEDIUM", "Admin panel found"),
        ("/admin/login", "MEDIUM", "Admin login page found"),
        ("/wp-admin", "MEDIUM", "WordPress admin panel"),
        ("/wp-login.php", "MEDIUM", "WordPress login page"),
        ("/administrator", "MEDIUM", "Joomla admin panel"),
        ("/phpmyadmin", "HIGH", "phpMyAdmin database interface exposed"),
        ("/adminer.php", "HIGH", "Adminer database interface exposed"),
        # Debug / Info
        ("/phpinfo.php", "HIGH", "PHP info page — reveals server configuration"),
        ("/info.php", "HIGH", "PHP info page"),
        ("/server-status", "MEDIUM", "Apache server status exposed"),
        ("/server-info", "MEDIUM", "Apache server info exposed"),
        ("/.well-known/security.txt", "INFO", "Security.txt found (good practice)"),
        ("/debug", "HIGH", "Debug endpoint exposed"),
        ("/api/debug", "HIGH", "API debug endpoint"),
        # Common API paths
        ("/api", "INFO", "API endpoint found"),
        ("/api/v1", "INFO", "API v1 endpoint"),
        ("/graphql", "MEDIUM", "GraphQL endpoint — may allow introspection"),
        ("/swagger", "MEDIUM", "Swagger API docs exposed"),
        ("/api-docs", "MEDIUM", "API documentation exposed"),
        # Package files
        ("/package.json", "MEDIUM", "Node.js package.json exposed — reveals dependencies"),
        ("/composer.json", "MEDIUM", "PHP composer.json exposed"),
    ]

    # Pre-compute baseline to detect SPA catch-all
    baseline = await _get_baseline(url)

    sem = asyncio.Semaphore(5)

    async def check_path(path, severity, description):
        async with sem:
            full_url = urljoin(url, path)
            try:
                # Use GET (not HEAD) so we can verify the body isn't a soft-404
                body = await stealth_fetch(full_url, timeout=8, max_retries=1)

                # Soft-404 check: if body matches the SPA homepage, it's fake
                if baseline["is_catchall"]:
                    body_hash = hashlib.md5(body.encode()).hexdigest()
                    if body_hash == baseline["homepage_hash"]:
                        return None  # SPA catch-all — not a real path

                # Additional content validation for specific file types
                path_lower = path.lower()
                if path_lower.endswith((".sql", ".zip", ".tar.gz")):
                    # If these return HTML, it's not a real file
                    if "<html" in body[:200].lower():
                        return None
                if ".git/" in path_lower:
                    # Git HEAD should contain "ref:", config should contain "[core]"
                    if "ref:" not in body[:50] and "[core]" not in body[:200]:
                        return None
                if path_lower.endswith(".php"):
                    # PHP files returning the SPA HTML = not real
                    if "<html" in body[:200].lower() and "phpinfo" not in body.lower() and "wp-" not in body.lower():
                        return None

                return {
                    "path": path,
                    "status": 200,
                    "severity": severity,
                    "description": description,
                }
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    return {
                        "path": path,
                        "status": 403,
                        "severity": "LOW" if severity in ("CRITICAL", "HIGH") else "INFO",
                        "description": f"{description} (403 Forbidden — exists but restricted)",
                    }
                return None
            except Exception:
                pass
            return None

    tasks = [check_path(p, s, d) for p, s, d in PATHS_TO_CHECK]
    results = await asyncio.gather(*tasks)

    result["checked_count"] = len(PATHS_TO_CHECK)

    for r in results:
        if r:
            result["found_paths"].append(r)
            if r["severity"] in ("CRITICAL", "HIGH", "MEDIUM"):
                result["issues"].append({
                    "severity": r["severity"],
                    "category": "Path Exposure",
                    "title": f"{r['path']} accessible (HTTP {r['status']})",
                    "description": r["description"],
                    "fix": f"Block access to {r['path']} in your web server config or remove the file.",
                    "nginx_fix": f"location {r['path']} {{ return 404; }}",
                })

    # --- Phase 2: Read .env files and extract leaked secrets ---
    result["env_leaks"] = []
    env_paths = [p for p in result["found_paths"] if ".env" in p["path"] and p["status"] == 200]

    for env_entry in env_paths:
        env_url = urljoin(url, env_entry["path"])
        try:
            body = await stealth_fetch(env_url, timeout=10, max_retries=1)

            # Validate: must be a text .env file, not binary/HTML/compressed garbage
            # Check for non-printable characters (binary indicator)
            non_printable = sum(1 for c in body[:500] if ord(c) < 32 and c not in '\n\r\t')
            if non_printable > len(body[:500]) * 0.05:
                continue  # > 5% non-printable = binary, skip
            # Check it's not an HTML page (Cloudflare challenge, error page)
            body_stripped = body.strip().lower()
            if body_stripped.startswith("<!doctype") or body_stripped.startswith("<html") or "<head>" in body_stripped[:500]:
                continue  # HTML page, not a .env file
            # Must have at least one KEY=VALUE line with alphanumeric key
            if not re.search(r'^[A-Za-z_][A-Za-z0-9_]*\s*=', body, re.MULTILINE):
                continue  # No valid KEY=VALUE pattern found

            secrets = []
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    # Key must be a valid env var name
                    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                        continue
                    value = value.strip().strip("'\"")
                    # Classify sensitivity
                    key_upper = key.upper()
                    sensitive = False
                    secret_type = "Config Value"
                    if any(kw in key_upper for kw in (
                        "SECRET", "PASSWORD", "PASS", "TOKEN", "API_KEY", "APIKEY",
                        "AUTH", "PRIVATE", "CREDENTIAL", "DB_", "DATABASE",
                        "REDIS", "MONGO", "MYSQL", "POSTGRES", "SMTP",
                        "AWS_", "S3_", "STRIPE", "SENDGRID", "TWILIO",
                        "JWT", "ENCRYPT", "SIGNING", "MASTER",
                    )):
                        sensitive = True
                        secret_type = "Secret/Credential"
                    elif any(kw in key_upper for kw in (
                        "KEY", "URL", "URI", "HOST", "PORT", "ENDPOINT",
                        "DOMAIN", "BUCKET", "QUEUE", "WEBHOOK",
                    )):
                        sensitive = True
                        secret_type = "Infrastructure Config"

                    if value and len(value) > 1:
                        # Mask the value for the report (show first 6 + last 2 chars)
                        if len(value) > 12:
                            masked = value[:6] + "..." + value[-2:]
                        elif len(value) > 4:
                            masked = value[:3] + "..."
                        else:
                            masked = "***"

                        secrets.append({
                            "key": key,
                            "value_preview": masked,
                            "full_length": len(value),
                            "type": secret_type,
                            "sensitive": sensitive,
                        })

            if secrets:
                result["env_leaks"].append({
                    "file": env_entry["path"],
                    "secrets": secrets,
                    "total_vars": len(secrets),
                    "sensitive_vars": sum(1 for s in secrets if s["sensitive"]),
                })

                # Add a detailed issue
                sensitive_keys = [s["key"] for s in secrets if s["sensitive"]]
                all_keys = [s["key"] for s in secrets]
                result["issues"].append({
                    "severity": "CRITICAL",
                    "category": "Exposed Secrets",
                    "title": f"{env_entry['path']} leaked: {len(secrets)} variables ({sum(1 for s in secrets if s['sensitive'])} sensitive)",
                    "description": (
                        f"The environment file {env_entry['path']} is publicly readable and contains {len(secrets)} configuration values. "
                        f"Sensitive keys include: {', '.join(sensitive_keys[:10]) or 'none classified as sensitive'}. "
                        f"All keys: {', '.join(all_keys[:20])}."
                    ),
                    "fix": (
                        f"1. IMMEDIATELY block access to {env_entry['path']} via web server config. "
                        "2. Rotate ALL credentials found in this file. "
                        "3. Add .env* to .gitignore and web server deny rules. "
                        "4. Audit access logs for prior unauthorized access."
                    ),
                    "nginx_fix": f"location ~ /\\.env {{ return 404; }}",
                })

        except Exception:
            pass

    return result


# ================================================================
# TOOL: security_audit
# ================================================================

async def security_audit(url: str) -> dict:
    """Deep security audit: TLS config, headers, cookies, mixed content, open redirects."""
    result = {
        "url": url,
        "issues": [],
        "score": 100,  # start at 100, deduct for issues
        "tls_details": {},
        "header_analysis": {},
        "cookie_issues": [],
        "recommendations": [],
    }

    import urllib.error

    domain = urlparse(url).netloc

    # --- 1. TLS deep check ---
    try:
        import ssl
        ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(domain, 443, ssl=ctx, server_hostname=domain),
            timeout=10.0,
        )
        ssl_obj = writer.transport.get_extra_info("ssl_object")
        if ssl_obj:
            version = ssl_obj.version()
            cipher = ssl_obj.cipher()
            result["tls_details"] = {
                "version": version,
                "cipher_name": cipher[0] if cipher else None,
                "cipher_bits": cipher[2] if cipher else None,
            }

            # Check for weak TLS
            if version in ("TLSv1", "TLSv1.1"):
                result["issues"].append({
                    "severity": "HIGH",
                    "category": "TLS",
                    "title": f"Outdated TLS version: {version}",
                    "description": f"Server supports {version} which is deprecated and insecure.",
                    "fix": "Disable TLSv1.0 and TLSv1.1 in your web server config.",
                    "nginx_fix": "ssl_protocols TLSv1.2 TLSv1.3;",
                    "apache_fix": "SSLProtocol all -SSLv3 -TLSv1 -TLSv1.1",
                })
                result["score"] -= 20

            if cipher and cipher[2] and cipher[2] < 128:
                result["issues"].append({
                    "severity": "HIGH",
                    "category": "TLS",
                    "title": f"Weak cipher: {cipher[0]} ({cipher[2]} bits)",
                    "description": "Cipher strength below 128 bits is considered weak.",
                    "fix": "Configure strong cipher suites.",
                    "nginx_fix": "ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';",
                })
                result["score"] -= 15

        writer.close()
        await writer.wait_closed()
    except Exception as e:
        result["issues"].append({
            "severity": "CRITICAL",
            "category": "TLS",
            "title": "TLS connection failed",
            "description": str(e),
            "fix": "Ensure HTTPS is properly configured.",
        })
        result["score"] -= 30

    # --- 2. HTTP Security Headers deep analysis ---
    HEADER_CHECKS = {
        "Strict-Transport-Security": {
            "required": True,
            "severity": "HIGH",
            "description": "HSTS not set. Browsers can be tricked into HTTP connections (downgrade attack).",
            "fix": "Add HSTS header to enforce HTTPS.",
            "nginx_fix": "add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\" always;",
            "apache_fix": "Header always set Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\"",
            "deduction": 15,
        },
        "Content-Security-Policy": {
            "required": True,
            "severity": "HIGH",
            "description": "No CSP header. XSS attacks are not mitigated by the browser.",
            "fix": "Add a Content-Security-Policy header.",
            "nginx_fix": "add_header Content-Security-Policy \"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self';\" always;",
            "apache_fix": "Header always set Content-Security-Policy \"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'\"",
            "deduction": 15,
        },
        "X-Frame-Options": {
            "required": True,
            "severity": "MEDIUM",
            "description": "No X-Frame-Options. Site can be embedded in iframes (clickjacking risk).",
            "fix": "Add X-Frame-Options header.",
            "nginx_fix": "add_header X-Frame-Options \"SAMEORIGIN\" always;",
            "apache_fix": "Header always set X-Frame-Options \"SAMEORIGIN\"",
            "deduction": 10,
        },
        "X-Content-Type-Options": {
            "required": True,
            "severity": "MEDIUM",
            "description": "No X-Content-Type-Options. Browser may MIME-sniff responses into executable content.",
            "fix": "Add nosniff header.",
            "nginx_fix": "add_header X-Content-Type-Options \"nosniff\" always;",
            "apache_fix": "Header always set X-Content-Type-Options \"nosniff\"",
            "deduction": 5,
        },
        "Referrer-Policy": {
            "required": True,
            "severity": "LOW",
            "description": "No Referrer-Policy. Full URL including query params may leak to third parties.",
            "fix": "Add Referrer-Policy header.",
            "nginx_fix": "add_header Referrer-Policy \"strict-origin-when-cross-origin\" always;",
            "apache_fix": "Header always set Referrer-Policy \"strict-origin-when-cross-origin\"",
            "deduction": 5,
        },
        "Permissions-Policy": {
            "required": False,
            "severity": "LOW",
            "description": "No Permissions-Policy. Browser features (camera, mic, geolocation) not restricted.",
            "fix": "Add Permissions-Policy header.",
            "nginx_fix": "add_header Permissions-Policy \"camera=(), microphone=(), geolocation=()\" always;",
            "apache_fix": "Header always set Permissions-Policy \"camera=(), microphone=(), geolocation=()\"",
            "deduction": 5,
        },
        "X-XSS-Protection": {
            "required": False,
            "severity": "LOW",
            "description": "No X-XSS-Protection. Legacy XSS filter not enabled (modern browsers use CSP instead).",
            "fix": "Add X-XSS-Protection header (or rely on CSP).",
            "nginx_fix": "add_header X-XSS-Protection \"1; mode=block\" always;",
            "apache_fix": "Header always set X-XSS-Protection \"1; mode=block\"",
            "deduction": 3,
        },
    }

    try:
        resp = await stealth_request(url, timeout=15)
        headers = dict(resp.headers)

        for header_name, check in HEADER_CHECKS.items():
            value = headers.get(header_name)
            result["header_analysis"][header_name] = {
                "present": value is not None,
                "value": value,
            }

            if value is None and check["required"]:
                issue = {
                    "severity": check["severity"],
                    "category": "HTTP Header",
                    "title": f"Missing: {header_name}",
                    "description": check["description"],
                    "fix": check["fix"],
                    "nginx_fix": check.get("nginx_fix"),
                    "apache_fix": check.get("apache_fix"),
                }
                result["issues"].append(issue)
                result["score"] -= check["deduction"]
            elif value is None:
                result["recommendations"].append({
                    "header": header_name,
                    "description": check["description"],
                    "nginx_fix": check.get("nginx_fix"),
                    "apache_fix": check.get("apache_fix"),
                })

            # Check HSTS specifics
            if header_name == "Strict-Transport-Security" and value:
                if "includeSubDomains" not in value:
                    result["recommendations"].append({
                        "header": "HSTS",
                        "description": "HSTS missing includeSubDomains directive.",
                        "fix": "Add includeSubDomains to HSTS header.",
                    })
                if "preload" not in value:
                    result["recommendations"].append({
                        "header": "HSTS",
                        "description": "HSTS missing preload directive. Consider HSTS preload list.",
                        "fix": "Add preload and submit to hstspreload.org",
                    })

        # --- 3. Cookie analysis ---
        set_cookies = resp.headers.get_all("Set-Cookie") if hasattr(resp.headers, "get_all") else []
        if not set_cookies:
            raw_cookies = [v for k, v in resp.headers.items() if k.lower() == "set-cookie"]
            set_cookies = raw_cookies

        for cookie_str in set_cookies:
            cookie_lower = cookie_str.lower()
            issues = []
            if "secure" not in cookie_lower:
                issues.append("Missing Secure flag")
            if "httponly" not in cookie_lower:
                issues.append("Missing HttpOnly flag")
            if "samesite" not in cookie_lower:
                issues.append("Missing SameSite attribute")

            if issues:
                cookie_name = cookie_str.split("=")[0].strip()
                result["cookie_issues"].append({
                    "cookie": cookie_name,
                    "issues": issues,
                    "raw": cookie_str[:200],
                })
                result["score"] -= 3

        # --- 4. Server info leakage ---
        server = headers.get("Server", "")
        if server:
            # Check for version disclosure
            import re
            version_match = re.search(r"[\d]+\.[\d]+", server)
            if version_match:
                result["issues"].append({
                    "severity": "LOW",
                    "category": "Information Disclosure",
                    "title": f"Server version exposed: {server}",
                    "description": "Server header reveals software version, aiding attackers.",
                    "fix": "Hide server version.",
                    "nginx_fix": "server_tokens off;",
                    "apache_fix": "ServerTokens Prod\nServerSignature Off",
                })
                result["score"] -= 3

        # --- 5. X-Powered-By leakage ---
        powered_by = headers.get("X-Powered-By")
        if powered_by:
            result["issues"].append({
                "severity": "LOW",
                "category": "Information Disclosure",
                "title": f"X-Powered-By exposed: {powered_by}",
                "description": "Technology stack revealed, makes targeted attacks easier.",
                "fix": "Remove X-Powered-By header.",
                "nginx_fix": "proxy_hide_header X-Powered-By;",
                "apache_fix": "Header always unset X-Powered-By",
            })
            result["score"] -= 3

        # --- 6. HTTP to HTTPS redirect ---
        try:
            http_url = url.replace("https://", "http://")
            hdrs = _browser_headers()
            http_req = urllib.request.Request(http_url, method="HEAD", headers=hdrs)

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    self.redirect_url = newurl
                    return None

            handler = NoRedirect()
            opener = urllib.request.build_opener(handler)
            await _stealth_delay(domain)

            try:
                resp2 = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: opener.open(http_req, timeout=10)
                )
                # No redirect happened
                result["issues"].append({
                    "severity": "HIGH",
                    "category": "Transport Security",
                    "title": "HTTP does not redirect to HTTPS",
                    "description": "HTTP version of site is accessible without redirect to HTTPS.",
                    "fix": "Add HTTP to HTTPS redirect.",
                    "nginx_fix": "server { listen 80; server_name example.com; return 301 https://$host$request_uri; }",
                    "apache_fix": "RewriteEngine On\nRewriteCond %{HTTPS} off\nRewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]",
                })
                result["score"] -= 15
            except urllib.error.HTTPError:
                pass  # redirect or block = fine
        except Exception:
            pass

    except Exception as e:
        result["issues"].append({
            "severity": "MEDIUM",
            "category": "Connectivity",
            "title": "Could not fetch page for audit",
            "description": str(e),
        })

    # Clamp score
    result["score"] = max(0, result["score"])

    # Generate consolidated nginx/apache config
    nginx_lines = []
    apache_lines = []
    for issue in result["issues"]:
        if issue.get("nginx_fix"):
            nginx_lines.append(f"    {issue['nginx_fix']}")
        if issue.get("apache_fix"):
            apache_lines.append(f"    {issue['apache_fix']}")
    for rec in result["recommendations"]:
        if rec.get("nginx_fix"):
            nginx_lines.append(f"    {rec['nginx_fix']}")
        if rec.get("apache_fix"):
            apache_lines.append(f"    {rec['apache_fix']}")

    if nginx_lines:
        result["nginx_config_snippet"] = "# OS Shield Security Audit - Recommended nginx config\nserver {\n" + "\n".join(nginx_lines) + "\n}"
    if apache_lines:
        result["apache_config_snippet"] = "# OS Shield Security Audit - Recommended Apache config\n<IfModule mod_headers.c>\n" + "\n".join(apache_lines) + "\n</IfModule>"

    return result


# ================================================================
# TOOL: tls_cipher_suite_grading
# ================================================================

async def tls_cipher_suite_grading(domain: str, port: int = 443) -> dict:
    """Test multiple TLS versions and cipher suites. Grade the TLS configuration."""
    result = {
        "domain": domain,
        "supported_protocols": [],
        "grade": "?",
        "weak_ciphers_found": [],
        "deprecated_protocols": [],
        "supports_forward_secrecy": False,
        "issues": [],
    }

    WEAK_CIPHERS = {"RC4", "DES", "3DES", "NULL", "EXPORT", "MD5", "RC2", "IDEA", "SEED"}
    FS_KEYWORDS = {"ECDHE", "DHE", "ECDH"}

    tls_versions = [
        ("TLSv1.0", ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1),
        ("TLSv1.1", ssl.TLSVersion.TLSv1_1, ssl.TLSVersion.TLSv1_1),
        ("TLSv1.2", ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2),
        ("TLSv1.3", ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3),
    ]

    for version_name, min_ver, max_ver in tls_versions:
        entry = {"version": version_name, "supported": False, "cipher": None, "bits": None}
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = min_ver
            ctx.maximum_version = max_ver

            def _connect(d=domain, p=port, c=ctx):
                conn = c.wrap_socket(socket.socket(), server_hostname=d)
                conn.settimeout(10)
                conn.connect((d, p))
                cipher_info = conn.cipher()
                conn.close()
                return cipher_info

            cipher_info = await asyncio.get_event_loop().run_in_executor(None, _connect)
            if cipher_info:
                entry["supported"] = True
                entry["cipher"] = cipher_info[0]
                entry["bits"] = cipher_info[2]

                cipher_upper = cipher_info[0].upper()
                for weak in WEAK_CIPHERS:
                    if weak in cipher_upper:
                        result["weak_ciphers_found"].append(f"{cipher_info[0]} ({version_name})")
                        break

                for fs in FS_KEYWORDS:
                    if fs in cipher_upper:
                        result["supports_forward_secrecy"] = True
                        break

                if version_name in ("TLSv1.0", "TLSv1.1"):
                    result["deprecated_protocols"].append(version_name)

        except (ssl.SSLError, OSError, socket.timeout):
            entry["supported"] = False
        except Exception:
            entry["supported"] = False

        result["supported_protocols"].append(entry)

    # Compute grade
    supported = [p for p in result["supported_protocols"] if p["supported"]]
    supported_names = {p["version"] for p in supported}

    if not supported:
        result["grade"] = "F"
    elif result["weak_ciphers_found"]:
        result["grade"] = "F"
    elif "TLSv1.0" in supported_names:
        result["grade"] = "D"
    elif "TLSv1.1" in supported_names:
        result["grade"] = "C"
    elif supported_names == {"TLSv1.3"}:
        result["grade"] = "A+"
    elif supported_names == {"TLSv1.2", "TLSv1.3"} and result["supports_forward_secrecy"]:
        result["grade"] = "A+"
    elif "TLSv1.2" in supported_names and result["supports_forward_secrecy"]:
        result["grade"] = "A"
    elif "TLSv1.2" in supported_names:
        result["grade"] = "B"
    else:
        result["grade"] = "B"

    # Generate issues
    if result["grade"] == "F":
        result["issues"].append({
            "severity": "CRITICAL", "category": "TLS Configuration",
            "title": "TLS Grade F -- weak ciphers or no secure protocols",
            "description": f"Weak ciphers found: {', '.join(result['weak_ciphers_found']) or 'none'}. Attackers can decrypt traffic.",
            "fix": "Disable all weak ciphers and enable only TLSv1.2+ with strong cipher suites.",
        })
    elif result["grade"] in ("C", "D"):
        result["issues"].append({
            "severity": "HIGH", "category": "TLS Configuration",
            "title": f"TLS Grade {result['grade']} -- deprecated protocols: {', '.join(result['deprecated_protocols'])}",
            "description": "Deprecated TLS protocols are still supported. Known vulnerabilities: POODLE, BEAST.",
            "fix": "Disable TLSv1.0 and TLSv1.1. Only allow TLSv1.2 and TLSv1.3.",
        })
    elif result["grade"] == "B":
        result["issues"].append({
            "severity": "MEDIUM", "category": "TLS Configuration",
            "title": "TLS Grade B -- good but not optimal",
            "description": "TLS is acceptable but could be improved with forward secrecy and TLSv1.3.",
            "fix": "Enable ECDHE cipher suites for forward secrecy. Enable TLSv1.3.",
        })

    if result["deprecated_protocols"] and result["grade"] not in ("F", "C", "D"):
        result["issues"].append({
            "severity": "MEDIUM", "category": "TLS Configuration",
            "title": f"Deprecated TLS protocols still active: {', '.join(result['deprecated_protocols'])}",
            "description": "Deprecated versions should be disabled even when strong protocols are also available.",
            "fix": "Disable TLSv1.0 and TLSv1.1 in server configuration.",
        })

    return result


# ================================================================
# TOOL: cookie_security_audit
# ================================================================

async def cookie_security_audit(url: str) -> dict:
    """Comprehensive cookie security audit across multiple endpoints."""
    result = {
        "url": url,
        "cookies_found": [],
        "total_cookies": 0,
        "insecure_cookies": 0,
        "issues": [],
    }

    SESSION_PATTERNS = re.compile(
        r"(sess|session|sid|phpsessid|jsessionid|token|auth|jwt|csrf|xsrf|login|_id)",
        re.IGNORECASE
    )

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    paths_to_check = [
        parsed.path or "/",
        "/login", "/signin", "/admin", "/api", "/wp-login.php",
    ]

    seen_cookies = {}

    for path in paths_to_check:
        check_url = base + path
        try:
            resp = await stealth_request(check_url, timeout=10, max_retries=1)
            set_cookies = resp.headers.get_all("Set-Cookie") if hasattr(resp.headers, 'get_all') else []
            if not set_cookies:
                raw_headers = resp.getheaders() if hasattr(resp, 'getheaders') else []
                set_cookies = [v for k, v in raw_headers if k.lower() == "set-cookie"]

            for cookie_str in set_cookies:
                parts = cookie_str.split(";")
                name_val = parts[0].strip()
                name = name_val.split("=")[0].strip() if "=" in name_val else name_val

                if name in seen_cookies:
                    continue

                flags_lower = cookie_str.lower()
                cookie_info = {
                    "name": name,
                    "source_path": path,
                    "secure": "secure" in flags_lower,
                    "httponly": "httponly" in flags_lower,
                    "samesite": None,
                    "domain": None,
                    "max_age_seconds": None,
                    "is_session_cookie": bool(SESSION_PATTERNS.search(name)),
                    "issues": [],
                }

                for part in parts[1:]:
                    part_stripped = part.strip().lower()
                    if part_stripped.startswith("samesite="):
                        cookie_info["samesite"] = part.strip().split("=", 1)[1].strip()
                    elif part_stripped.startswith("domain="):
                        cookie_info["domain"] = part.strip().split("=", 1)[1].strip()
                    elif part_stripped.startswith("max-age="):
                        try:
                            cookie_info["max_age_seconds"] = int(part.strip().split("=", 1)[1])
                        except ValueError:
                            pass

                if not cookie_info["secure"]:
                    cookie_info["issues"].append("Missing Secure flag")
                if not cookie_info["httponly"]:
                    cookie_info["issues"].append("Missing HttpOnly flag")
                if not cookie_info["samesite"]:
                    cookie_info["issues"].append("Missing SameSite attribute")
                if cookie_info["max_age_seconds"] and cookie_info["max_age_seconds"] > 86400 * 365:
                    cookie_info["issues"].append(f"Excessively long lifetime ({cookie_info['max_age_seconds'] // 86400} days)")

                seen_cookies[name] = cookie_info

        except Exception:
            continue

    result["cookies_found"] = list(seen_cookies.values())
    result["total_cookies"] = len(seen_cookies)
    result["insecure_cookies"] = sum(1 for c in seen_cookies.values() if c["issues"])

    session_insecure = [c for c in seen_cookies.values() if c["is_session_cookie"] and c["issues"]]
    other_insecure = [c for c in seen_cookies.values() if not c["is_session_cookie"] and c["issues"]]

    if session_insecure:
        names = ", ".join(c["name"] for c in session_insecure[:5])
        all_issues = set()
        for c in session_insecure:
            all_issues.update(c["issues"])
        result["issues"].append({
            "severity": "HIGH", "category": "Cookie Security",
            "title": f"Session cookies with missing security flags: {names}",
            "description": f"Session cookies lack: {', '.join(all_issues)}. Allows session theft via XSS or MITM.",
            "fix": "Set Secure, HttpOnly, and SameSite=Strict on all session cookies.",
        })

    if other_insecure:
        result["issues"].append({
            "severity": "MEDIUM", "category": "Cookie Security",
            "title": f"{len(other_insecure)} non-session cookie(s) missing security flags",
            "description": "Non-session cookies are missing recommended security attributes.",
            "fix": "Apply Secure, HttpOnly, and SameSite attributes to all cookies.",
        })

    return result


# ================================================================
# TOOL: api_endpoint_discovery
# ================================================================

async def api_endpoint_discovery(url: str) -> dict:
    """Fuzz common API paths and detect exposed endpoints, docs, and auth requirements."""
    result = {
        "url": url,
        "discovered_endpoints": [],
        "open_endpoints": 0,
        "authenticated_endpoints": 0,
        "graphql_introspection": False,
        "wp_users_exposed": False,
        "issues": [],
    }

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    API_PATHS = [
        "/api", "/api/v1", "/api/v2", "/api/v3",
        "/api/users", "/api/user", "/api/me", "/api/profile",
        "/api/admin", "/api/config", "/api/settings",
        "/api/health", "/api/status", "/api/ping",
        "/api/docs", "/api/swagger", "/api/openapi.json", "/api/swagger.json",
        "/api/graphql", "/graphql",
        "/api/login", "/api/auth", "/api/token",
        "/api/upload", "/api/files", "/api/export",
        "/api/search",
        "/rest", "/rest/v1", "/rest/api",
        "/v1", "/v2",
        "/wp-json", "/wp-json/wp/v2", "/wp-json/wp/v2/users",
        "/jsonapi", "/_api",
    ]

    random.shuffle(API_PATHS)

    # Pre-compute baseline to detect SPA catch-all
    baseline = await _get_baseline(url)

    sem = asyncio.Semaphore(3)

    async def check_endpoint(path):
        async with sem:
            ep_url = base + path
            try:
                resp = await stealth_request(ep_url, accept="json", timeout=10, max_retries=1)
                status = resp.status
                ct = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", errors="replace")[:500]

                # Soft-404 check
                if baseline["is_catchall"] and status == 200:
                    full_body = body  # already truncated but enough for hash
                    # If Content-Type is html (not json), likely SPA catch-all
                    if "html" in ct.lower() and "json" not in ct.lower():
                        return None  # SPA serving HTML for an API path = fake

                is_json = "json" in ct.lower() or body.strip()[:1] in ("{", "[")
                requires_auth = status in (401, 403)
                data_exposed = status == 200 and is_json and len(body.strip()) > 10

                return {
                    "path": path,
                    "status_code": status,
                    "content_type": ct[:100],
                    "requires_auth": requires_auth,
                    "response_preview": body[:200],
                    "data_exposed": data_exposed,
                }
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return {
                        "path": path, "status_code": e.code, "content_type": "",
                        "requires_auth": True, "response_preview": "", "data_exposed": False,
                    }
                return None
            except Exception:
                return None

    tasks = [check_endpoint(p) for p in API_PATHS]
    ep_results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in ep_results:
        if r and isinstance(r, dict):
            result["discovered_endpoints"].append(r)
            if r["data_exposed"] and not r["requires_auth"]:
                result["open_endpoints"] += 1
            if r["requires_auth"]:
                result["authenticated_endpoints"] += 1

    # Check GraphQL introspection
    graphql_eps = [e for e in result["discovered_endpoints"] if "graphql" in e["path"] and e.get("data_exposed")]
    if graphql_eps:
        try:
            introspection_query = json.dumps({"query": "{ __schema { types { name } } }"}).encode()
            resp = await stealth_request(
                base + "/graphql", method="POST", accept="json", timeout=10,
                data=introspection_query,
                extra_headers={"Content-Type": "application/json"},
            )
            body = resp.read().decode("utf-8", errors="replace")
            if "__schema" in body or "__type" in body:
                result["graphql_introspection"] = True
        except Exception:
            pass

    # Check WP user enum
    wp_user_eps = [e for e in result["discovered_endpoints"]
                   if "wp-json/wp/v2/users" in e["path"] and e.get("data_exposed")]
    if wp_user_eps:
        preview = wp_user_eps[0].get("response_preview", "")
        if '"slug"' in preview or '"name"' in preview:
            result["wp_users_exposed"] = True

    # Generate issues
    if result["open_endpoints"] > 0:
        open_paths = [e["path"] for e in result["discovered_endpoints"] if e["data_exposed"] and not e["requires_auth"]]
        result["issues"].append({
            "severity": "HIGH", "category": "API Exposure",
            "title": f"{result['open_endpoints']} API endpoint(s) accessible without authentication",
            "description": f"Open endpoints: {', '.join(open_paths[:10])}. Data exposed without authentication.",
            "fix": "Implement authentication (API keys, OAuth, JWT) on all API endpoints.",
        })

    if result["graphql_introspection"]:
        result["issues"].append({
            "severity": "MEDIUM", "category": "API Exposure",
            "title": "GraphQL introspection enabled",
            "description": "GraphQL schema introspection reveals the entire API structure to attackers.",
            "fix": "Disable introspection in production.",
        })

    if result["wp_users_exposed"]:
        result["issues"].append({
            "severity": "HIGH", "category": "Information Disclosure",
            "title": "WordPress user enumeration via REST API",
            "description": "/wp-json/wp/v2/users leaks usernames for brute-force login attacks.",
            "fix": "Restrict the WP REST API users endpoint via plugin or filter.",
        })

    return result


# ================================================================
# TOOL: dependency_cve_scan
# ================================================================

async def dependency_cve_scan(url: str) -> dict:
    """Detect JavaScript libraries and check for known CVEs."""
    result = {
        "url": url,
        "libraries_detected": [],
        "vulnerabilities_found": [],
        "total_libraries": 0,
        "total_vulnerabilities": 0,
        "issues": [],
    }

    KNOWN_CVES = {
        "jquery": [
            {"below": "3.5.0", "cve": "CVE-2020-11022", "severity": "MEDIUM", "description": "XSS via jQuery.htmlPrefilter"},
            {"below": "3.0.0", "cve": "CVE-2015-9251", "severity": "MEDIUM", "description": "XSS in Ajax requests to untrusted domains"},
            {"below": "1.12.0", "cve": "CVE-2015-9251", "severity": "HIGH", "description": "Multiple XSS vulnerabilities"},
        ],
        "angular": [
            {"below": "1.6.9", "cve": "CVE-2019-10768", "severity": "HIGH", "description": "Prototype pollution in merge()"},
            {"below": "1.8.0", "cve": "CVE-2022-25869", "severity": "MEDIUM", "description": "XSS via xlink:href in SVG"},
        ],
        "angularjs": [
            {"below": "1.6.9", "cve": "CVE-2019-10768", "severity": "HIGH", "description": "Prototype pollution in merge()"},
        ],
        "lodash": [
            {"below": "4.17.21", "cve": "CVE-2021-23337", "severity": "HIGH", "description": "Command injection via template()"},
            {"below": "4.17.12", "cve": "CVE-2019-10744", "severity": "CRITICAL", "description": "Prototype pollution"},
        ],
        "bootstrap": [
            {"below": "4.3.1", "cve": "CVE-2019-8331", "severity": "MEDIUM", "description": "XSS in tooltip/popover data-template"},
            {"below": "3.4.1", "cve": "CVE-2019-8331", "severity": "MEDIUM", "description": "XSS in tooltip/popover"},
        ],
        "vue": [
            {"below": "2.5.17", "cve": "CVE-2018-11235", "severity": "MEDIUM", "description": "Potential XSS via template compilation"},
        ],
        "react-dom": [
            {"below": "16.4.2", "cve": "CVE-2018-6341", "severity": "MEDIUM", "description": "XSS via SSR attribute injection"},
        ],
        "moment": [
            {"below": "2.29.4", "cve": "CVE-2022-31129", "severity": "HIGH", "description": "ReDoS via crafted date string"},
            {"below": "2.19.3", "cve": "CVE-2017-18214", "severity": "HIGH", "description": "ReDoS vulnerability"},
        ],
        "handlebars": [
            {"below": "4.7.7", "cve": "CVE-2021-23369", "severity": "CRITICAL", "description": "Remote code execution via template"},
        ],
        "dompurify": [
            {"below": "2.3.6", "cve": "CVE-2022-25927", "severity": "MEDIUM", "description": "mXSS bypass"},
        ],
        "axios": [
            {"below": "1.6.0", "cve": "CVE-2023-45857", "severity": "MEDIUM", "description": "CSRF token leakage"},
        ],
    }

    VERSION_PATTERNS = [
        (r'jQuery\s*(?:JavaScript Library\s+)?v?(\d+\.\d+\.\d+)', "jquery"),
        (r'jquery[.-](\d+\.\d+\.\d+)', "jquery"),
        (r'jQuery\.fn\.jquery\s*=\s*["\'](\d+\.\d+\.\d+)', "jquery"),
        (r'Bootstrap\s+v?(\d+\.\d+\.\d+)', "bootstrap"),
        (r'bootstrap[.-](\d+\.\d+\.\d+)', "bootstrap"),
        (r'AngularJS\s+v?(\d+\.\d+\.\d+)', "angular"),
        (r'angular[.-](\d+\.\d+\.\d+)', "angular"),
        (r'Vue\.js\s+v?(\d+\.\d+\.\d+)', "vue"),
        (r'vue[.-](\d+\.\d+\.\d+)', "vue"),
        (r'React\s+v?(\d+\.\d+\.\d+)', "react-dom"),
        (r'react-dom[.-](\d+\.\d+\.\d+)', "react-dom"),
        (r'Lodash\s+v?(\d+\.\d+\.\d+)', "lodash"),
        (r'lodash[.-](\d+\.\d+\.\d+)', "lodash"),
        (r'moment[.-](\d+\.\d+\.\d+)', "moment"),
        (r'Handlebars\s+v?(\d+\.\d+\.\d+)', "handlebars"),
        (r'handlebars[.-](\d+\.\d+\.\d+)', "handlebars"),
        (r'DOMPurify\s+v?(\d+\.\d+\.\d+)', "dompurify"),
        (r'axios[/.-](\d+\.\d+\.\d+)', "axios"),
    ]

    def _semver_lt(version_str, threshold_str):
        try:
            v = [int(x) for x in version_str.split(".")[:3]]
            t = [int(x) for x in threshold_str.split(".")[:3]]
            while len(v) < 3: v.append(0)
            while len(t) < 3: t.append(0)
            return v < t
        except (ValueError, AttributeError):
            return False

    try:
        html = await stealth_fetch(url, timeout=15)
        js_files = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        parsed_url = urlparse(url)
        base = f"{parsed_url.scheme}://{parsed_url.netloc}"

        all_js_content = "\n".join(re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE))

        detected = {}
        for pattern, lib_name in VERSION_PATTERNS:
            m = re.search(pattern, all_js_content, re.IGNORECASE)
            if m and lib_name not in detected:
                detected[lib_name] = {"name": lib_name, "version": m.group(1), "source": "inline"}

        for js_src in js_files:
            for pattern, lib_name in VERSION_PATTERNS:
                m = re.search(pattern, js_src, re.IGNORECASE)
                if m and lib_name not in detected:
                    detected[lib_name] = {"name": lib_name, "version": m.group(1), "source": js_src[:100]}

        sem = asyncio.Semaphore(3)

        async def scan_js(js_url):
            async with sem:
                try:
                    if js_url.startswith("//"):
                        js_url = "https:" + js_url
                    elif js_url.startswith("/"):
                        js_url = base + js_url
                    elif not js_url.startswith("http"):
                        return
                    content = await stealth_fetch(js_url, timeout=10, max_retries=1)
                    snippet = content[:5000]
                    for pattern, lib_name in VERSION_PATTERNS:
                        m = re.search(pattern, snippet, re.IGNORECASE)
                        if m and lib_name not in detected:
                            detected[lib_name] = {"name": lib_name, "version": m.group(1), "source": js_url[:100]}
                except Exception:
                    pass

        await asyncio.gather(*[scan_js(f) for f in js_files[:10]], return_exceptions=True)

        result["libraries_detected"] = list(detected.values())
        result["total_libraries"] = len(detected)

        for lib_name, info in detected.items():
            version = info["version"]
            for cve_entry in KNOWN_CVES.get(lib_name, []):
                if _semver_lt(version, cve_entry["below"]):
                    result["vulnerabilities_found"].append({
                        "library": lib_name,
                        "version": version,
                        "cve": cve_entry["cve"],
                        "severity": cve_entry["severity"],
                        "description": cve_entry["description"],
                    })

        result["total_vulnerabilities"] = len(result["vulnerabilities_found"])

        if result["vulnerabilities_found"]:
            crit = [v for v in result["vulnerabilities_found"] if v["severity"] == "CRITICAL"]
            high = [v for v in result["vulnerabilities_found"] if v["severity"] == "HIGH"]
            medium = [v for v in result["vulnerabilities_found"] if v["severity"] == "MEDIUM"]

            if crit:
                libs = ", ".join(set(f"{v['library']} {v['version']} ({v['cve']})" for v in crit))
                result["issues"].append({
                    "severity": "CRITICAL", "category": "Vulnerable Dependencies",
                    "title": f"Critical CVEs in JavaScript libraries: {libs}",
                    "description": f"{len(crit)} critical vulnerability(ies). May allow RCE or severe data compromise.",
                    "fix": "Update affected libraries immediately.",
                })
            if high:
                libs = ", ".join(set(f"{v['library']} {v['version']} ({v['cve']})" for v in high))
                result["issues"].append({
                    "severity": "HIGH", "category": "Vulnerable Dependencies",
                    "title": f"High-severity CVEs: {libs}",
                    "description": f"{len(high)} high-severity vulnerability(ies). XSS, prototype pollution, or command injection possible.",
                    "fix": "Update affected libraries to patched versions.",
                })
            if medium:
                libs = ", ".join(set(f"{v['library']} {v['version']} ({v['cve']})" for v in medium))
                result["issues"].append({
                    "severity": "MEDIUM", "category": "Vulnerable Dependencies",
                    "title": f"Medium-severity CVEs: {libs}",
                    "description": f"{len(medium)} medium-severity vulnerability(ies) found.",
                    "fix": "Plan library updates in the next maintenance window.",
                })

    except Exception as e:
        result["issues"].append({
            "severity": "INFO", "category": "Vulnerable Dependencies",
            "title": "Dependency scan incomplete",
            "description": f"Could not fully scan dependencies: {e}",
        })

    return result


# ================================================================
# TOOL: subdomain_takeover_check
# ================================================================

async def subdomain_takeover_check(subdomains_data: list) -> dict:
    """Check if discovered subdomains have dangling CNAME records."""
    result = {
        "subdomains_checked": 0,
        "dangling_cnames": [],
        "issues": [],
    }

    DANGLING_SERVICES = {
        ".s3.amazonaws.com": "AWS S3", ".s3-website": "AWS S3 Website",
        ".herokuapp.com": "Heroku", ".herokudns.com": "Heroku",
        ".github.io": "GitHub Pages", ".ghost.io": "Ghost",
        ".myshopify.com": "Shopify", ".pantheonsite.io": "Pantheon",
        ".wordpress.com": "WordPress.com", ".surge.sh": "Surge.sh",
        ".bitbucket.io": "Bitbucket", ".azurewebsites.net": "Azure",
        ".cloudfront.net": "CloudFront", ".zendesk.com": "Zendesk",
        ".readme.io": "ReadMe", ".fastly.net": "Fastly",
        ".netlify.app": "Netlify", ".fly.dev": "Fly.io",
        ".vercel.app": "Vercel", ".render.onrender.com": "Render",
        ".unbouncepages.com": "Unbounce", ".statuspage.io": "Statuspage",
        ".uservoice.com": "UserVoice",
    }

    TAKEOVER_SIGNATURES = [
        "NoSuchBucket", "There isn't a GitHub Pages site here",
        "No such app", "no-such-app", "herokucdn.com/error-pages",
        "404 Blog is not found", "is not a registered InCloud URI",
        "Domain is not configured", "project not found",
        "The request could not be satisfied",
        "Repository not found", "Fastly error: unknown domain",
        "The specified bucket does not exist",
        "This UserVoice subdomain is currently available",
    ]

    sem = asyncio.Semaphore(5)

    async def check_subdomain(sub_info):
        fqdn = sub_info.get("subdomain", "")
        if not fqdn:
            return None

        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nslookup", "-type=CNAME", fqdn,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
                output = stdout.decode("utf-8", errors="replace")

                cname_target = None
                for line in output.splitlines():
                    if "canonical name" in line.lower() or "cname" in line.lower():
                        parts = line.split("=")
                        if len(parts) >= 2:
                            cname_target = parts[-1].strip().rstrip(".")
                            break

                if not cname_target:
                    return None

                service = None
                for pattern, svc_name in DANGLING_SERVICES.items():
                    if pattern in cname_target.lower():
                        service = svc_name
                        break

                if not service:
                    return None

                takeover_possible = False
                evidence = ""
                try:
                    body = await stealth_fetch(f"https://{fqdn}", timeout=10, max_retries=1)
                    for sig in TAKEOVER_SIGNATURES:
                        if sig.lower() in body.lower():
                            takeover_possible = True
                            evidence = sig
                            break
                except Exception:
                    try:
                        body = await stealth_fetch(f"http://{fqdn}", timeout=10, max_retries=1)
                        for sig in TAKEOVER_SIGNATURES:
                            if sig.lower() in body.lower():
                                takeover_possible = True
                                evidence = sig
                                break
                    except Exception:
                        takeover_possible = True
                        evidence = "Connection failed -- service likely deprovisioned"

                return {
                    "subdomain": fqdn,
                    "cname_target": cname_target,
                    "service": service,
                    "takeover_possible": takeover_possible,
                    "evidence": evidence,
                }

            except Exception:
                return None

    tasks = [check_subdomain(s) for s in subdomains_data]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    result["subdomains_checked"] = len(subdomains_data)

    for r in results_list:
        if r and isinstance(r, dict):
            result["dangling_cnames"].append(r)

    takeovers = [d for d in result["dangling_cnames"] if d["takeover_possible"]]
    dangling_only = [d for d in result["dangling_cnames"] if not d["takeover_possible"]]

    if takeovers:
        subs = ", ".join(d["subdomain"] for d in takeovers[:5])
        result["issues"].append({
            "severity": "CRITICAL", "category": "Subdomain Takeover",
            "title": f"Subdomain takeover possible: {subs}",
            "description": f"{len(takeovers)} subdomain(s) have dangling CNAMEs to deprovisioned services. Attacker can claim these.",
            "fix": "Remove dangling DNS CNAME records or reclaim the service.",
        })

    if dangling_only:
        subs = ", ".join(d["subdomain"] for d in dangling_only[:5])
        result["issues"].append({
            "severity": "MEDIUM", "category": "Subdomain Takeover",
            "title": f"Dangling CNAME records found: {subs}",
            "description": f"{len(dangling_only)} subdomain(s) point to external services. Not currently exploitable but should be monitored.",
            "fix": "Verify all CNAME targets are actively provisioned. Remove unused DNS records.",
        })

    return result


# ================================================================
# TOOL: secret_validator
# ================================================================

async def secret_validator(secrets: list, env_leaks: list = None) -> dict:
    """
    Validate if discovered secrets/API keys are actually live and usable.
    Tests each key against its respective service API.

    Args:
        secrets: list from js_secrets_scanner results (type, location, value_preview)
        env_leaks: list from path_discovery env_leaks (file, secrets[])

    Returns:
        Dict with validation results per key.
    """
    result = {
        "validated": [],
        "dead_or_fake": [],
        "inconclusive": [],
        "total_checked": 0,
        "live_count": 0,
        "issues": [],
    }

    all_keys = []

    # Collect JS secrets
    for s in (secrets or []):
        all_keys.append({
            "type": s.get("type", "Unknown"),
            "value": s.get("value_preview", "").rstrip("."),
            "full_value": s.get("full_value", s.get("value_preview", "").rstrip(".")),
            "source": f"JS: {s.get('location', '')}",
        })

    # Collect .env secrets
    for env in (env_leaks or []):
        for s in env.get("secrets", []):
            if s.get("sensitive"):
                all_keys.append({
                    "type": s.get("type", "Config Value"),
                    "value": s.get("full_value", s.get("value_preview", "")),
                    "full_value": s.get("full_value", ""),
                    "key_name": s.get("key", ""),
                    "source": f"ENV: {env.get('file', '')}",
                })

    result["total_checked"] = len(all_keys)

    for key_info in all_keys:
        key_type = key_info["type"].lower()
        value = key_info.get("full_value") or key_info.get("value", "")
        key_name = key_info.get("key_name", "")
        source = key_info["source"]

        validation = {
            "type": key_info["type"],
            "source": source,
            "key_name": key_name,
            "value_preview": value[:20] + "..." if len(value) > 20 else value,
            "status": "inconclusive",
            "reason": "",
            "risk_level": "UNKNOWN",
        }

        try:
            # --- Google API Key ---
            if "google" in key_type and "api" in key_type:
                if value.startswith("AIza") and len(value) >= 35:
                    # Test against Google Maps Geocoding API (free tier)
                    test_url = f"https://maps.googleapis.com/maps/api/geocode/json?address=test&key={value}"
                    try:
                        body = await stealth_fetch(test_url, accept="json", timeout=10, delay=False)
                        data = json.loads(body)
                        status = data.get("status", "")
                        if status == "OK" or status == "ZERO_RESULTS":
                            validation["status"] = "LIVE"
                            validation["reason"] = f"Google API key is active (Maps API returned: {status})"
                            validation["risk_level"] = "CRITICAL"
                        elif status == "REQUEST_DENIED":
                            error_msg = data.get("error_message", "")
                            if "not authorized" in error_msg.lower():
                                validation["status"] = "RESTRICTED"
                                validation["reason"] = f"Key exists but restricted to specific APIs: {error_msg[:100]}"
                                validation["risk_level"] = "MEDIUM"
                            elif "invalid" in error_msg.lower():
                                validation["status"] = "DEAD"
                                validation["reason"] = "Key is invalid/revoked"
                                validation["risk_level"] = "NONE"
                            else:
                                validation["status"] = "RESTRICTED"
                                validation["reason"] = f"Key denied: {error_msg[:100]}"
                                validation["risk_level"] = "LOW"
                        elif "OVER_QUERY_LIMIT" in status:
                            validation["status"] = "LIVE"
                            validation["reason"] = "Key is active but over quota"
                            validation["risk_level"] = "HIGH"
                        else:
                            validation["status"] = "inconclusive"
                            validation["reason"] = f"Unexpected response: {status}"
                    except Exception as e:
                        validation["reason"] = f"Validation request failed: {str(e)[:80]}"
                else:
                    validation["status"] = "INVALID_FORMAT"
                    validation["reason"] = "Does not match Google API key format (AIza...)"
                    validation["risk_level"] = "NONE"

            # --- Google OAuth Client ID ---
            elif "google" in key_type and "oauth" in key_type:
                if ".apps.googleusercontent.com" in value:
                    # OAuth client IDs are meant to be public, but we can check if the project is active
                    test_url = f"https://oauth2.googleapis.com/tokeninfo?id_token=invalid"
                    validation["status"] = "PUBLIC_KEY"
                    validation["reason"] = "OAuth Client IDs are designed to be public. Risk depends on OAuth flow configuration."
                    validation["risk_level"] = "LOW"
                else:
                    validation["status"] = "INVALID_FORMAT"
                    validation["reason"] = "Does not match Google OAuth Client ID format"
                    validation["risk_level"] = "NONE"

            # --- JWT Token ---
            elif "jwt" in key_type:
                if value.startswith("eyJ"):
                    import base64 as b64
                    try:
                        # Decode header and payload (no signature verification)
                        parts = value.split(".")
                        if len(parts) >= 2:
                            # Fix padding
                            header_b64 = parts[0] + "=" * (4 - len(parts[0]) % 4)
                            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
                            header = json.loads(b64.b64decode(header_b64))
                            payload = json.loads(b64.b64decode(payload_b64))

                            alg = header.get("alg", "?")
                            exp = payload.get("exp")
                            sub = payload.get("sub", "")
                            iss = payload.get("iss", "")

                            if exp:
                                from datetime import datetime, timezone
                                exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
                                now = datetime.now(timezone.utc)
                                if exp_dt < now:
                                    validation["status"] = "EXPIRED"
                                    validation["reason"] = f"JWT expired on {exp_dt.isoformat()}. Algorithm: {alg}, Issuer: {iss}"
                                    validation["risk_level"] = "LOW"
                                else:
                                    validation["status"] = "LIVE"
                                    validation["reason"] = f"JWT valid until {exp_dt.isoformat()}. Algorithm: {alg}, Issuer: {iss}, Sub: {sub}"
                                    validation["risk_level"] = "CRITICAL"
                            else:
                                validation["status"] = "LIVE"
                                validation["reason"] = f"JWT has no expiry (never expires!). Algorithm: {alg}, Issuer: {iss}"
                                validation["risk_level"] = "CRITICAL"

                            validation["jwt_details"] = {
                                "algorithm": alg,
                                "issuer": iss,
                                "subject": sub,
                                "expires": exp,
                                "claims": list(payload.keys())[:10],
                            }
                        else:
                            validation["status"] = "INVALID_FORMAT"
                            validation["reason"] = "JWT has fewer than 2 parts"
                            validation["risk_level"] = "NONE"
                    except Exception as e:
                        validation["status"] = "inconclusive"
                        validation["reason"] = f"JWT decode failed: {str(e)[:80]}"
                else:
                    validation["status"] = "INVALID_FORMAT"
                    validation["reason"] = "Does not match JWT format (eyJ...)"
                    validation["risk_level"] = "NONE"

            # --- AWS Access Key ---
            elif "aws" in key_type or (key_name and "AWS" in key_name.upper()):
                if value.startswith("AKIA") and len(value) == 20:
                    validation["status"] = "LIKELY_LIVE"
                    validation["reason"] = "Valid AWS Access Key ID format (AKIA..., 20 chars). Cannot verify without secret key."
                    validation["risk_level"] = "CRITICAL"
                elif value.startswith("ASIA"):
                    validation["status"] = "TEMPORARY"
                    validation["reason"] = "AWS temporary credential (ASIA...). May already be expired."
                    validation["risk_level"] = "HIGH"
                else:
                    validation["status"] = "inconclusive"
                    validation["reason"] = "Does not match standard AWS key format"

            # --- Stripe Key ---
            elif "stripe" in key_type or (key_name and "STRIPE" in key_name.upper()):
                if value.startswith("sk_live_"):
                    validation["status"] = "LIVE"
                    validation["reason"] = "Stripe LIVE secret key exposed! This grants full access to the Stripe account."
                    validation["risk_level"] = "CRITICAL"
                elif value.startswith("sk_test_"):
                    validation["status"] = "TEST_KEY"
                    validation["reason"] = "Stripe TEST key — no real financial risk, but should not be public."
                    validation["risk_level"] = "LOW"
                elif value.startswith("pk_live_") or value.startswith("pk_test_"):
                    validation["status"] = "PUBLIC_KEY"
                    validation["reason"] = "Stripe publishable key — designed to be public."
                    validation["risk_level"] = "NONE"
                else:
                    validation["status"] = "inconclusive"
                    validation["reason"] = "Stripe key format not recognized"

            # --- Generic API Key / Password in env ---
            elif key_name:
                key_upper = key_name.upper()
                if any(kw in key_upper for kw in ("PASSWORD", "PASS", "SECRET")):
                    if value in ("changeme", "password", "123456", "test", "example", "xxx", "TODO", ""):
                        validation["status"] = "PLACEHOLDER"
                        validation["reason"] = f"Value appears to be a placeholder: {value[:20]}"
                        validation["risk_level"] = "NONE"
                    else:
                        validation["status"] = "LIKELY_LIVE"
                        validation["reason"] = "Credential value looks real (not a known placeholder)"
                        validation["risk_level"] = "CRITICAL"
                elif any(kw in key_upper for kw in ("DB_", "DATABASE", "MYSQL", "POSTGRES", "REDIS", "MONGO")):
                    if "localhost" in value or "127.0.0.1" in value:
                        validation["status"] = "LOCAL_ONLY"
                        validation["reason"] = "Points to localhost — not exploitable remotely"
                        validation["risk_level"] = "LOW"
                    else:
                        validation["status"] = "LIKELY_LIVE"
                        validation["reason"] = "Database connection string pointing to remote host"
                        validation["risk_level"] = "CRITICAL"
                elif any(kw in key_upper for kw in ("SMTP", "SENDGRID", "TWILIO", "MAILGUN")):
                    validation["status"] = "LIKELY_LIVE"
                    validation["reason"] = "Service credential — cannot verify without making authenticated request"
                    validation["risk_level"] = "HIGH"
                else:
                    validation["status"] = "inconclusive"
                    validation["reason"] = "Cannot determine if key is active without service-specific testing"

            # --- Password in URL ---
            elif "password" in key_type:
                validation["status"] = "PATTERN_MATCH"
                validation["reason"] = "Password pattern detected in URL/code. May be a CSS selector or form field reference."
                validation["risk_level"] = "LOW"

            # --- Fallback ---
            else:
                validation["status"] = "inconclusive"
                validation["reason"] = f"No validator available for type: {key_info['type']}"

        except Exception as e:
            validation["reason"] = f"Validation error: {str(e)[:100]}"

        # Categorize
        if validation["status"] in ("LIVE", "LIKELY_LIVE"):
            result["validated"].append(validation)
            result["live_count"] += 1
        elif validation["status"] in ("DEAD", "INVALID_FORMAT", "PLACEHOLDER", "EXPIRED"):
            result["dead_or_fake"].append(validation)
        else:
            result["inconclusive"].append(validation)

    # Generate issues
    live_keys = [v for v in result["validated"] if v["risk_level"] == "CRITICAL"]
    high_keys = [v for v in result["validated"] if v["risk_level"] == "HIGH"]

    if live_keys:
        summary = "; ".join(f"{v['type']} ({v['source']})" for v in live_keys[:5])
        result["issues"].append({
            "severity": "CRITICAL",
            "category": "Live Exposed Secrets",
            "title": f"{len(live_keys)} LIVE secret(s) confirmed: {summary}",
            "description": (
                f"Validation confirmed {len(live_keys)} exposed secret(s) are actively usable. "
                "These keys respond to API calls or match known live credential patterns. "
                "Immediate rotation required."
            ),
            "fix": "1. Rotate all confirmed live keys immediately. 2. Revoke old keys. 3. Remove from source code. 4. Use environment variables or secret managers.",
        })

    if high_keys:
        summary = "; ".join(f"{v['type']} ({v['source']})" for v in high_keys[:5])
        result["issues"].append({
            "severity": "HIGH",
            "category": "Live Exposed Secrets",
            "title": f"{len(high_keys)} likely live secret(s): {summary}",
            "description": f"These credentials appear to be real based on format analysis but could not be fully verified.",
            "fix": "Rotate these credentials as a precaution and remove from public code.",
        })

    return result


# ================================================================
# TOOL: think (reasoning step)
# ================================================================

async def think(reasoning_prompt: str, llm_client: AsyncOpenAI) -> dict:
    """Use LLM to reason step-by-step about authenticity implications."""
    response = await llm_client.chat.completions.create(
        model=get_model("default", "poc_site_verifier"),
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior OSINT analyst evaluating website authenticity.\n\n"
                    "Think step-by-step about the findings presented.\n\n"
                    "Structure:\n"
                    "REASONING:\n- Step 1: ...\n- Step 2: ...\n\n"
                    "CONCLUSION: <one sentence>\n\n"
                    "VERDICT: <AUTHENTIC|SUSPICIOUS|FAKE|INCONCLUSIVE>\n"
                ),
            },
            {"role": "user", "content": reasoning_prompt},
        ],
    )

    text = response.choices[0].message.content.strip()
    reasoning = text
    conclusion = ""
    verdict = "INCONCLUSIVE"

    if "CONCLUSION:" in text:
        parts = text.split("CONCLUSION:")
        reasoning = parts[0].strip()
        remainder = parts[1].strip()
        if "VERDICT:" in remainder:
            conclusion_parts = remainder.split("VERDICT:")
            conclusion = conclusion_parts[0].strip()
            verdict = conclusion_parts[1].strip().split()[0] if conclusion_parts[1].strip() else "INCONCLUSIVE"
        else:
            conclusion = remainder

    return {
        "reasoning": reasoning,
        "conclusion": conclusion,
        "verdict": verdict,
    }


# ================================================================
# OPENAI TOOL DEFINITIONS (for function calling)
# ================================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "whois_lookup",
            "description": (
                "Query WHOIS data for a domain. Returns registrant info, creation/expiry dates, "
                "registrar, domain age, and whether privacy protection is active. "
                "Use this to determine domain legitimacy and ownership."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name (e.g. 'example.com')"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_ssl_cert",
            "description": (
                "Check SSL/TLS certificate for a domain. Returns TLS version, cipher, "
                "certificate issuer, validity dates, SAN entries, CN match, and self-signed status. "
                "Use this to verify HTTPS security."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name"},
                    "port": {"type": "integer", "description": "Port (default 443)"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dns_records",
            "description": (
                "Query DNS records for a domain. Checks A, MX, TXT records including "
                "SPF and DMARC. Use this to verify email security and DNS configuration."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name"},
                    "record_types": {
                        "type": "string",
                        "description": "Comma-separated record types (default: 'A,MX,TXT')",
                    },
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_headers",
            "description": (
                "Fetch HTTP response headers from a URL. Checks status code, server type, "
                "redirect chain, and security headers (HSTS, CSP, X-Frame-Options, etc). "
                "Use this to evaluate web server security posture."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL (e.g. 'https://example.com')"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wayback_check",
            "description": (
                "Check Internet Archive Wayback Machine for historical snapshots of a URL. "
                "Returns whether the site is archived, first/latest snapshots, total count, "
                "and archive age. Use this to verify site history and longevity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to check in Wayback Machine"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "page_content_scan",
            "description": (
                "Fetch and analyze a web page's HTML content. Checks for impressum/legal notice, "
                "privacy policy, contact info, external scripts/iframes, suspicious patterns "
                "(phishing, crypto, scam indicators), and page language. "
                "Use this to evaluate content legitimacy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to scan"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reverse_ip_lookup",
            "description": (
                "Resolve domain to IP and look up hosting provider info. Returns IP address, "
                "hosting organization, and country. Use this to check where the site is hosted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain name to resolve"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Reason step-by-step about authenticity implications of collected findings. "
                "Use this after gathering scan data to analyze what the results mean "
                "and form a verdict (AUTHENTIC / SUSPICIOUS / FAKE / INCONCLUSIVE)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning_prompt": {
                        "type": "string",
                        "description": "Detailed description of findings to reason about",
                    },
                },
                "required": ["reasoning_prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "security_audit",
            "description": (
                "Deep security audit of a website. Checks TLS configuration (version, ciphers), "
                "all HTTP security headers with severity ratings, cookie security flags, "
                "server version disclosure, HTTP-to-HTTPS redirect, and X-Powered-By leakage. "
                "Returns a security score (0-100), detailed issues with severity, "
                "and ready-to-use nginx/Apache config snippets to fix all issues. "
                "Use this AFTER the initial authenticity scan to provide actionable security recommendations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to audit (e.g. 'https://example.com')"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "robots_sitemap_scan",
            "description": (
                "Analyze robots.txt and sitemap.xml for exposed sensitive paths. "
                "Finds admin panels, API endpoints, database paths, and other sensitive URLs "
                "that are inadvertently disclosed. Use this for information disclosure assessment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL to check"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subdomain_enum",
            "description": (
                "Enumerate common subdomains via DNS resolution. Checks ~70 common subdomains "
                "(admin, staging, dev, api, db, etc.) and flags risky ones that expose attack surface. "
                "Use this to discover forgotten or exposed services."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain to enumerate subdomains for"},
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cors_check",
            "description": (
                "Test for CORS misconfigurations. Checks if the server reflects arbitrary origins, "
                "allows wildcard with credentials, or accepts null origin. "
                "CORS misconfigs can allow attackers to steal data cross-origin. "
                "Use this to verify API and web security."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to test CORS on"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "port_scan",
            "description": (
                "Scan common ports on a domain to find exposed services. Checks ~25 ports "
                "including databases (MySQL, PostgreSQL, MongoDB, Redis), admin services "
                "(SSH, RDP, VNC), and web services. Grabs banners where possible. "
                "Use this to assess network attack surface."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Domain to scan"},
                    "ports": {
                        "type": "string",
                        "description": "Comma-separated ports or 'common' for default set",
                    },
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "path_discovery",
            "description": (
                "Check for common sensitive paths and files on a web server. Tests ~35 paths "
                "including .git, .env, backup files, admin panels, phpMyAdmin, debug endpoints, "
                "API docs, and config files. Finds files that should not be publicly accessible. "
                "Use this to discover exposed sensitive resources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL to check paths on"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cms_version_detect",
            "description": (
                "Detect CMS type and version (WordPress, etc.), JavaScript library versions "
                "(jQuery, Bootstrap, React), PHP version, and server software. "
                "Checks WordPress-specific attack vectors: user enumeration via REST API, "
                "XMLRPC brute-force endpoint, exposed version numbers. "
                "Flags outdated libraries with known CVEs (e.g. jQuery < 3.5 XSS). "
                "Use this to identify vulnerable software components."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to analyze"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "login_security_check",
            "description": (
                "Analyze login pages for security features. Checks for CAPTCHA presence, "
                "2FA indicators, CSRF tokens, and password autocomplete settings. "
                "Detects brute-force exposure on wp-login.php, /admin/login, /login, etc. "
                "Use this to assess authentication security."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL of the site"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subdomain_content_scan",
            "description": (
                "Scan discovered subdomains for exposed debug pages, directory listings, "
                "stack traces, default server pages, and development/staging indicators. "
                "Pass subdomains as comma-separated FQDNs. "
                "Use this after subdomain_enum to analyze risky subdomains."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subdomains": {
                        "type": "string",
                        "description": "Comma-separated list of subdomains to scan (e.g. 'staging.example.com,dev.example.com')",
                    },
                },
                "required": ["subdomains"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "xss_reflection_check",
            "description": (
                "Test if a website reflects user input in responses, indicating XSS vulnerability potential. "
                "Tests 9 common injection vectors (search params, 404 pages, redirects, callbacks). "
                "Analyzes reflection context (script, attribute, tag, plaintext) and HTML encoding. "
                "Also scans for forms with text inputs as XSS vectors. "
                "Use this to assess Cross-Site Scripting risk, especially combined with outdated JavaScript libraries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL to test for XSS reflection"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sqli_check",
            "description": (
                "Test for SQL Injection vulnerabilities. Sends safe payloads (single quotes, "
                "comments, OR conditions) to common parameters and checks for SQL error messages "
                "in responses. Tests WordPress-specific endpoints. Detects error-based and "
                "time-based blind injection. Does NOT extract data — only detects if injection is possible. "
                "Use this to assess database security."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Base URL to test for SQL injection"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_redirect_check",
            "description": "Test for open redirect vulnerabilities on common URL parameters (redirect, next, return_url, etc). Attackers use open redirects for phishing.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_methods_check",
            "description": "Test which HTTP methods are allowed (PUT, DELETE, TRACE, OPTIONS). Flags dangerous methods that could allow file upload or cross-site tracing.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "js_secrets_scanner",
            "description": "Scan JavaScript files for exposed API keys, tokens, passwords, database URLs, and internal IPs. Checks both inline and external JS files for 19 secret patterns (AWS, Google, Stripe, GitHub, Slack, etc).",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email_spoofing_test",
            "description": "Deep email security check: SPF strictness (-all vs ~all), DMARC policy (none/quarantine/reject), DKIM presence. Determines if emails from this domain can be spoofed for phishing.",
            "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "waf_detection",
            "description": "Detect if a Web Application Firewall protects the site. Checks headers and sends attack payloads to trigger WAF blocks. Identifies Cloudflare, AWS WAF, Sucuri, ModSecurity, Wordfence, etc.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rate_limit_check",
            "description": "Test rate limiting on login and sensitive endpoints. Sends rapid requests to check if brute-force protection is active. Tests wp-login.php and xmlrpc.php.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dns_zone_transfer",
            "description": "Test if DNS zone transfer (AXFR) is possible. If successful, reveals ALL DNS records including internal subdomains, mail servers, and infrastructure details.",
            "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "breach_check",
            "description": "Check if email addresses from the domain appear in known data breaches via HaveIBeenPwned. Finds compromised credentials that could be used for credential stuffing attacks.",
            "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}, "required": ["domain"]},
        },
    },
]
