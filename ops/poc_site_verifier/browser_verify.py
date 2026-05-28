"""
Browser Verification Module
============================
Uses Playwright (headless Chromium) to verify scan findings in a real browser.
Only verifies HIGH/CRITICAL findings to keep runtime reasonable.

Called after the fast Python scan + auto-investigation, before LLM report generation.
Returns verified findings with screenshots as base64 evidence.
"""

import asyncio
import base64
import re
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright


async def browser_verify(url: str, scan_results: dict) -> dict:
    """
    Verify scan findings using a real headless browser.

    Args:
        url: Target URL
        scan_results: Dict containing all scan result dicts (paths_result, xss_result, etc.)

    Returns:
        Dict with verification results and screenshot evidence (base64).
    """
    result = {
        "verified_paths": [],
        "debunked_paths": [],
        "verified_xss": [],
        "debunked_xss": [],
        "verified_login": [],
        "screenshots": [],  # {name, base64_png, description}
        "summary": "",
    }

    print("  [BROWSER] Starting headless Chromium...", flush=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        # Block unnecessary resources for speed
        await context.route(
            re.compile(r"\.(png|jpg|jpeg|gif|svg|woff2?|ttf|eot|ico|mp4|webm)$"),
            lambda route: route.abort(),
        )

        page = await context.new_page()

        # --- 1. Screenshot the homepage as baseline ---
        print("  [BROWSER] Capturing homepage baseline...", flush=True)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)
            homepage_screenshot = await page.screenshot(full_page=False)
            homepage_title = await page.title()
            homepage_url = page.url
            result["screenshots"].append({
                "name": "homepage",
                "base64_png": base64.b64encode(homepage_screenshot).decode(),
                "description": f"Homepage: {homepage_title}",
            })
        except Exception as e:
            print(f"  [BROWSER] Homepage failed: {e}", flush=True)
            await browser.close()
            return result

        # --- 2. Verify exposed paths ---
        paths_result = scan_results.get("paths", {})
        found_paths = paths_result.get("found_paths", [])
        critical_paths = [p for p in found_paths if p.get("severity") in ("CRITICAL", "HIGH") and p.get("status") == 200]

        if critical_paths:
            print(f"  [BROWSER] Verifying {len(critical_paths)} exposed paths...", flush=True)

        for path_info in critical_paths[:10]:  # Cap at 10 to keep runtime sane
            path = path_info["path"]
            full_url = urljoin(url, path)
            try:
                resp = await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(500)

                status = resp.status if resp else 0
                current_title = await page.title()
                current_url = page.url
                body_text = await page.evaluate("() => document.body?.innerText?.substring(0, 1000) || ''")

                # Check if it's just the homepage again (SPA catch-all)
                is_same_as_homepage = (current_title == homepage_title and abs(len(body_text) - len(await page.evaluate("() => ''"))) < 100)

                # Content-specific validation
                is_real = True
                reason = ""

                if current_title == homepage_title:
                    # Probably SPA catch-all
                    is_real = False
                    reason = f"Same title as homepage ({homepage_title})"
                elif status == 404:
                    is_real = False
                    reason = "Server returned 404"
                elif "not found" in body_text.lower()[:200] or "page not found" in body_text.lower()[:200]:
                    is_real = False
                    reason = "Page shows 'not found' message"
                else:
                    # It's different from homepage — likely real
                    reason = f"Unique content (title: {current_title[:50]})"

                entry = {
                    "path": path,
                    "status": status,
                    "is_real": is_real,
                    "reason": reason,
                    "page_title": current_title[:100],
                }

                if is_real:
                    result["verified_paths"].append(entry)
                    # Take screenshot as evidence
                    screenshot = await page.screenshot(full_page=False)
                    result["screenshots"].append({
                        "name": f"path_{path.replace('/', '_').strip('_')}",
                        "base64_png": base64.b64encode(screenshot).decode(),
                        "description": f"VERIFIED: {path} — {reason}",
                    })
                    print(f"  [BROWSER]   REAL: {path} — {reason}", flush=True)
                else:
                    result["debunked_paths"].append(entry)
                    print(f"  [BROWSER]   FAKE: {path} — {reason}", flush=True)

            except Exception as e:
                result["debunked_paths"].append({
                    "path": path,
                    "is_real": False,
                    "reason": f"Browser error: {str(e)[:100]}",
                })

        # --- 3. Verify XSS reflections ---
        xss_result = scan_results.get("xss", {})
        reflections = xss_result.get("reflections_found", [])

        if reflections:
            print(f"  [BROWSER] Verifying {len(reflections)} XSS reflections...", flush=True)

        for refl in reflections[:5]:
            vector_url = refl.get("url", "")
            vector_name = refl.get("vector_name", "")
            if not vector_url:
                continue

            try:
                # Replace the canary with an actual XSS probe
                # Use a harmless payload that we can detect
                xss_marker = f"XSSPROBE{id(refl) % 9999}"
                xss_payload = f"<img src=x onerror=window.__xss_{xss_marker}=1>"

                # Build test URL with XSS payload
                parsed = urlparse(vector_url)
                # Replace canary value in query string
                test_url = re.sub(
                    r'XSSCANARY\d+',
                    xss_payload,
                    vector_url,
                )
                if test_url == vector_url:
                    # Canary wasn't in URL, try appending
                    test_url = vector_url

                # Navigate and check if payload executed
                js_errors = []
                page.on("pageerror", lambda err: js_errors.append(str(err)))

                await page.goto(test_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)

                # Check if our XSS marker was set
                xss_fired = await page.evaluate(f"() => window.__xss_{xss_marker} === 1")

                # Also check if the payload appears in the DOM unencoded
                body_html = await page.evaluate("() => document.body?.innerHTML?.substring(0, 5000) || ''")
                payload_in_dom = xss_payload in body_html
                encoded_in_dom = "&lt;img" in body_html and xss_marker in body_html

                if xss_fired:
                    severity = "CRITICAL"
                    reason = "XSS payload executed in browser"
                elif payload_in_dom:
                    severity = "HIGH"
                    reason = "Unencoded HTML injected into DOM (execution blocked by CSP or similar)"
                elif encoded_in_dom:
                    severity = "LOW"
                    reason = "Input reflected but HTML-encoded (safe)"
                else:
                    severity = None
                    reason = "Payload not reflected in rendered page"

                entry = {
                    "vector_name": vector_name,
                    "url": test_url[:200],
                    "xss_fired": xss_fired,
                    "payload_in_dom": payload_in_dom,
                    "encoded": encoded_in_dom,
                    "severity": severity,
                    "reason": reason,
                }

                if severity and severity in ("CRITICAL", "HIGH"):
                    result["verified_xss"].append(entry)
                    screenshot = await page.screenshot(full_page=False)
                    result["screenshots"].append({
                        "name": f"xss_{vector_name.replace(' ', '_')}",
                        "base64_png": base64.b64encode(screenshot).decode(),
                        "description": f"XSS VERIFIED: {vector_name} — {reason}",
                    })
                    print(f"  [BROWSER]   XSS CONFIRMED: {vector_name} — {reason}", flush=True)
                else:
                    result["debunked_xss"].append(entry)
                    print(f"  [BROWSER]   XSS FALSE POS: {vector_name} — {reason}", flush=True)

            except Exception as e:
                result["debunked_xss"].append({
                    "vector_name": vector_name,
                    "reason": f"Browser error: {str(e)[:100]}",
                })

        # --- 4. Verify login pages ---
        login_result = scan_results.get("login", {})
        if login_result.get("login_page_found"):
            login_url = login_result.get("login_url", "")
            if login_url:
                print(f"  [BROWSER] Verifying login page: {login_url}", flush=True)
                try:
                    await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(1000)

                    # Check for real login form elements
                    has_password_field = await page.evaluate(
                        "() => document.querySelector('input[type=password]') !== null"
                    )
                    has_username_field = await page.evaluate(
                        "() => document.querySelector('input[type=text], input[type=email], input[name*=user], input[name*=login], input[name*=email]') !== null"
                    )
                    has_submit = await page.evaluate(
                        "() => document.querySelector('button[type=submit], input[type=submit]') !== null"
                    )
                    current_title = await page.title()

                    is_real_login = has_password_field and (has_username_field or has_submit)

                    entry = {
                        "url": login_url,
                        "is_real": is_real_login,
                        "has_password_field": has_password_field,
                        "has_username_field": has_username_field,
                        "has_submit": has_submit,
                        "title": current_title[:100],
                    }
                    result["verified_login"].append(entry)

                    if is_real_login:
                        screenshot = await page.screenshot(full_page=False)
                        result["screenshots"].append({
                            "name": "login_page",
                            "base64_png": base64.b64encode(screenshot).decode(),
                            "description": f"Login page verified: {current_title}",
                        })
                        print(f"  [BROWSER]   LOGIN REAL: password field + form found", flush=True)
                    else:
                        print(f"  [BROWSER]   LOGIN FAKE: no real login form detected", flush=True)

                except Exception as e:
                    print(f"  [BROWSER]   Login check failed: {e}", flush=True)

        # --- 5. Check JS console for errors/secrets ---
        print("  [BROWSER] Checking JS console...", flush=True)
        try:
            console_messages = []
            page.on("console", lambda msg: console_messages.append({
                "type": msg.type,
                "text": msg.text[:200],
            }))
            await page.goto(url, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(2000)

            # Filter for interesting messages
            errors = [m for m in console_messages if m["type"] == "error"]
            warnings = [m for m in console_messages if m["type"] == "warning"]

            if errors:
                result["console_errors"] = errors[:10]

        except Exception:
            pass

        # --- Summary ---
        verified_count = len(result["verified_paths"]) + len(result["verified_xss"])
        debunked_count = len(result["debunked_paths"]) + len(result["debunked_xss"])
        total_screenshots = len(result["screenshots"])

        result["summary"] = (
            f"Browser verified {verified_count} findings as real, "
            f"debunked {debunked_count} as false positives. "
            f"{total_screenshots} screenshots captured as evidence."
        )

        print(f"  [BROWSER] Done: {verified_count} verified, {debunked_count} debunked, {total_screenshots} screenshots", flush=True)

        await browser.close()

    return result
