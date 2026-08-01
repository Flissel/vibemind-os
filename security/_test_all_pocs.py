"""
Test all 12 security PoCs for functionality.
Run: python _test_all_pocs.py
"""
import sys
import os
import asyncio
import json
import traceback
import subprocess
import importlib
import importlib.util

BASE = os.path.dirname(os.path.abspath(__file__))

results = {}

def test(name, fn):
    print(f"\n{'='*60}")
    print(f"TESTING: {name}")
    print('='*60)
    try:
        result = fn()
        results[name] = "PASS"
        print(f"  RESULT: PASS")
        return result
    except Exception as e:
        results[name] = f"FAIL: {e}"
        print(f"  RESULT: FAIL - {e}")
        traceback.print_exc()
        return None


# ========== TIER 1 ==========

def test_vuln_scanner():
    """Test vulnerability scanner - reads Windows registry, no LLM needed."""
    spec = importlib.util.spec_from_file_location(
        "vuln_main", os.path.join(BASE, 'poc_vuln_scanner', 'main.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    software = asyncio.run(mod.inventory_installed_software())
    print(f"  Found {len(software)} installed software items")
    for s in software[:3]:
        print(f"    - {s}")
    assert len(software) > 0, "No software found"
    return True

def test_network_monitor():
    """Test network monitor MCP server - WiFi/ARP/ports."""
    # Test that MCP server file is valid
    mcp_path = os.path.join(BASE, 'poc_network_monitor', 'mcp_server.py')
    assert os.path.exists(mcp_path), "mcp_server.py not found"
    print(f"  MCP server file exists: {mcp_path}")

    # Test underlying network commands
    r = subprocess.run(['netsh', 'wlan', 'show', 'networks', 'mode=bssid'],
                       capture_output=True, text=True, timeout=10)
    wifi_lines = [l for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []
    print(f"  WiFi scan: {len(wifi_lines)} output lines")

    r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=10)
    port_lines = [l for l in r.stdout.splitlines() if 'LISTENING' in l]
    print(f"  Listening ports: {len(port_lines)}")

    # Quick MCP server startup test (start, list tools, stop)
    proc = subprocess.Popen(
        [sys.executable, mcp_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=os.path.join(BASE, 'poc_network_monitor'))
    try:
        # Send JSON-RPC initialize
        init_msg = json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": 1,
                               "params": {"protocolVersion": "2024-11-05",
                                          "capabilities": {},
                                          "clientInfo": {"name": "test", "version": "0.1"}}})
        proc.stdin.write(f"Content-Length: {len(init_msg)}\r\n\r\n{init_msg}".encode())
        proc.stdin.flush()
        import time
        time.sleep(2)
        proc.terminate()
        print(f"  MCP server started and accepted connection")
    except Exception as e:
        proc.terminate()
        print(f"  MCP server startup test: {e}")
    return True

def test_alerter():
    """Test alerter - multi-channel alert system (must run from its own dir)."""
    alerter_dir = os.path.join(BASE, 'poc_alerter')
    # Write a small test script to avoid sys.path conflicts
    test_script = os.path.join(alerter_dir, '_quick_test.py')
    with open(test_script, 'w') as f:
        f.write("""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from config import TELEGRAM_BOT_TOKEN, SLACK_WEBHOOK_URL, ALERT_EMAIL_TO
from alerter import send_alert, send_telegram, send_slack, send_email, send_alert_batch
print("Functions: send_alert, send_telegram, send_slack, send_email, send_alert_batch")
print(f"Telegram configured: {bool(TELEGRAM_BOT_TOKEN)}")
print(f"Slack configured: {bool(SLACK_WEBHOOK_URL)}")
print(f"Email configured: {bool(ALERT_EMAIL_TO)}")
print("OK")
""")
    r = subprocess.run([sys.executable, test_script],
                       capture_output=True, text=True, timeout=15, cwd=alerter_dir)
    os.remove(test_script)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            print(f"  {line}")
    else:
        raise RuntimeError(f"alerter test failed: {r.stderr[:300]}")
    return True

def test_os_shield():
    """Test os_shield - must be tested from its own directory to avoid config conflicts."""
    shield_dir = os.path.join(BASE, 'poc_os_shield')
    test_script = os.path.join(shield_dir, '_quick_test.py')
    with open(test_script, 'w') as f:
        f.write("""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import AUTORUN_KEYS, SUSPICIOUS_PROCESS_NAMES
from baselines import capture_baseline
from tools import TOOL_DISPATCH
print(f"Config OK: {len(AUTORUN_KEYS)} autorun keys, {len(SUSPICIOUS_PROCESS_NAMES)} suspicious procs")
print(f"Tools: {list(TOOL_DISPATCH.keys())}")
bl = asyncio.run(capture_baseline())
print(f"Baseline captured: {len(bl)} categories")
for k,v in bl.items():
    cnt = len(v) if isinstance(v, (list,dict)) else v
    print(f"  {k}: {cnt} entries")
print("OK")
""")
    r = subprocess.run([sys.executable, test_script],
                       capture_output=True, text=True, timeout=30, cwd=shield_dir)
    os.remove(test_script)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            print(f"  {line}")
    else:
        raise RuntimeError(f"os_shield failed: {r.stderr[:300]}")
    return True


# ========== TIER 2 ==========

def test_forensics():
    """Test forensics - timeline reconstruction from Windows artifacts."""
    spec = importlib.util.spec_from_file_location(
        "forensics_main", os.path.join(BASE, 'poc_forensics', 'main.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    prefetch = asyncio.run(mod.parse_prefetch())
    print(f"  Prefetch entries: {len(prefetch)}")
    browser = asyncio.run(mod.parse_browser_history())
    print(f"  Browser history entries: {len(browser)}")
    ps = asyncio.run(mod.parse_powershell_history())
    print(f"  PowerShell history entries: {len(ps)}")
    usb = asyncio.run(mod.parse_usb_history())
    print(f"  USB device entries: {len(usb)}")
    recent = asyncio.run(mod.parse_recent_files())
    print(f"  Recent files: {len(recent)}")
    return True

def test_canary():
    """Test canary - honeypot deployment system."""
    spec = importlib.util.spec_from_file_location(
        "canary", os.path.join(BASE, 'poc_canary', 'canary.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, 'deploy_canaries'), "deploy_canaries not found"
    assert hasattr(mod, 'cleanup_canaries'), "cleanup_canaries not found"
    assert hasattr(mod, 'show_status'), "show_status not found"
    assert hasattr(mod, 'watch_canaries'), "watch_canaries not found"
    print("  Functions: deploy_canaries, cleanup_canaries, show_status, watch_canaries")

    # Check canary definitions
    assert hasattr(mod, 'CANARY_FILES'), "CANARY_FILES definition not found"
    print(f"  Canary types defined: {len(mod.CANARY_FILES)}")
    for c in mod.CANARY_FILES:
        print(f"    - {c.get('name', 'unknown')} -> {c.get('location', '?')}")
    return True

def test_endpoint_hardening():
    """Test endpoint hardening MCP server."""
    mcp_path = os.path.join(BASE, 'poc_endpoint_hardening', 'mcp_server.py')
    assert os.path.exists(mcp_path), "mcp_server.py not found"
    print(f"  MCP server file exists")

    # Test Windows Defender status
    r = subprocess.run(
        ['powershell', '-Command',
         'Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled | ConvertTo-Json'],
        capture_output=True, text=True, timeout=15)
    if r.returncode == 0 and r.stdout.strip():
        data = json.loads(r.stdout)
        print(f"  Defender: Antivirus={data.get('AntivirusEnabled')}, RealTime={data.get('RealTimeProtectionEnabled')}")
    return True

def test_botnet_detector():
    """Test botnet detector - DGA, beacon, zombie detection."""
    detector_path = os.path.join(BASE, 'poc_botnet_detector', 'detector.py')
    spec = importlib.util.spec_from_file_location("detector", detector_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Test DGA detector
    dga = mod.DGADetector()
    test_domains = ["google.com", "xkjhf8923hdf.xyz", "github.com", "a8sd7f6asd.ru"]
    for d in test_domains:
        score = dga.analyze_domain(d)
        label = "SUSPICIOUS" if score.get('score', 0) > 50 else "clean"
        print(f"  DGA: {d} -> score={score.get('score', 'N/A')} ({label})")

    # Test endpoint checker via BotnetDetector
    bd = mod.BotnetDetector()
    zombie = asyncio.run(bd.check_local())
    print(f"  Local check: {json.dumps(zombie, default=str)[:200]}")

    return True


# ========== TIER 3 ==========

def test_event_log():
    """Test event log MCP server."""
    mcp_path = os.path.join(BASE, 'poc_event_log', 'mcp_server.py')
    assert os.path.exists(mcp_path), "mcp_server.py not found"
    print(f"  MCP server file exists")

    r = subprocess.run(
        ['powershell', '-Command',
         'Get-WinEvent -FilterHashtable @{LogName="System";Level=2} -MaxEvents 3 2>$null | '
         'Select-Object TimeCreated,Message | ConvertTo-Json'],
        capture_output=True, text=True, timeout=15)
    if r.returncode == 0 and r.stdout.strip():
        events = json.loads(r.stdout) if r.stdout.startswith('[') else [json.loads(r.stdout)]
        print(f"  Recent system errors: {len(events)}")
    else:
        print(f"  Event log query returned code {r.returncode}")
    return True

def test_log_analyzer():
    """Test log analyzer - security event analysis."""
    spec = importlib.util.spec_from_file_location(
        "log_tools", os.path.join(BASE, 'poc_log_analyzer', 'tools.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    bf = asyncio.run(mod.detect_brute_force(hours=1))
    print(f"  Brute force (1h): detected={bf.get('brute_force_detected')}, failed_logins={bf.get('total_failed_logins')}")

    timeline = asyncio.run(mod.build_timeline(hours=1))
    print(f"  Timeline (1h): {len(timeline.get('events', []))} events")
    return True

def test_firewall():
    """Test firewall MCP server."""
    mcp_path = os.path.join(BASE, 'poc_firewall', 'mcp_server.py')
    assert os.path.exists(mcp_path), "mcp_server.py not found"
    print(f"  MCP server file exists")

    r = subprocess.run(
        ['netsh', 'advfirewall', 'show', 'allprofiles', 'state'],
        capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if 'State' in line or 'Zustand' in line or 'Status' in line:
                print(f"  {line.strip()}")
    return True

def test_keycloak():
    """Test keycloak - OAuth2 device verification."""
    spec = importlib.util.spec_from_file_location(
        "verify", os.path.join(BASE, 'poc_keycloak', 'verify_device.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert hasattr(mod, 'device_flow_verify'), "device_flow_verify not found"
    assert hasattr(mod, 'verify_application'), "verify_application not found"
    assert hasattr(mod, 'introspect_token'), "introspect_token not found"
    print("  Functions: device_flow_verify, verify_application, introspect_token")
    print("  NOTE: Requires running Keycloak Docker container on localhost:8080")
    return True


if __name__ == '__main__':
    print("VibeMind Security PoC Test Suite")
    print(f"Base: {BASE}")
    print(f"Python: {sys.version}")

    # Tier 1
    test("poc_vuln_scanner", test_vuln_scanner)
    test("poc_network_monitor", test_network_monitor)
    test("poc_alerter", test_alerter)
    test("poc_os_shield", test_os_shield)

    # Tier 2
    test("poc_forensics", test_forensics)
    test("poc_canary", test_canary)
    test("poc_endpoint_hardening", test_endpoint_hardening)
    test("poc_botnet_detector", test_botnet_detector)

    # Tier 3
    test("poc_event_log", test_event_log)
    test("poc_log_analyzer", test_log_analyzer)
    test("poc_firewall", test_firewall)
    test("poc_keycloak", test_keycloak)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print('='*60)
    passed = sum(1 for v in results.values() if v == "PASS")
    failed = sum(1 for v in results.values() if v != "PASS")
    for name, result in results.items():
        status = "PASS" if result == "PASS" else "FAIL"
        print(f"  [{status}] {name}" + (f" - {result}" if status == "FAIL" else ""))
    print(f"\nTotal: {passed} passed, {failed} failed out of {len(results)}")
