"""
Website Authenticity Verifier - Main Entry Point
==================================================
LLM-gesteuerte OSINT-Pruefung von Webseiten auf Echtheit.

Architektur (autogen-core + OpenFang tool calling):

  [User/CLI]
      |
      v
  [OrchestratorAgent]  <-- konfiguriertes OpenFang-Modell waehlt Tools
      |         |
      v         v
  [CheckerAgent]   [think() -> OpenFang reasoning]
  (WHOIS, SSL, DNS, HTTP, Wayback, Content, IP)
      |
      v
  [AnalyzerAgent]  <-- OpenFang bewertet alle Ergebnisse
      |
      v
  [ReporterAgent]  <-- OpenFang formatiert den Report
      |
      v
  [Final Report]

Nutzung (OpenFang ueber die zentrale VibeMind-Konfiguration):
  python verify.py https://example.com
  python verify.py https://example.com --checks whois,ssl,dns
"""

import asyncio
import sys
from urllib.parse import urlparse
from typing import Any

from autogen_core import AgentId, SingleThreadedAgentRuntime

from messages import VerifyTarget, AuthenticityReport
from orchestrator import OrchestratorAgent
from checker import CheckerAgent
from analyzer import AnalyzerAgent
from reporter import ReporterAgent


DEFAULT_CHECKS = "whois,ssl,dns,http,wayback,content,ip"
SITE_VERIFIER_ROLE = "security_analyzer"


def get_client(role: str) -> Any:
    """Resolve the configured client lazily at the runtime boundary."""
    from vibemind_shared import get_client as shared_get_client

    return shared_get_client(role)


def get_model(role: str) -> str:
    """Resolve the configured model lazily at the runtime boundary."""
    from vibemind_shared import get_model as shared_get_model

    return shared_get_model(role)


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return parsed.netloc or parsed.path.split("/")[0]


async def verify_site(url: str, checks: str = DEFAULT_CHECKS) -> AuthenticityReport:
    """Run the full verification pipeline."""

    llm_client = get_client(SITE_VERIFIER_ROLE)
    llm_model = get_model(SITE_VERIFIER_ROLE)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    domain = extract_domain(url)

    print("=" * 60)
    print("  WEBSITE AUTHENTICITY VERIFIER")
    print("  LLM-Driven OSINT Analysis")
    print("=" * 60)
    print(f"\n  Target:  {url}")
    print(f"  Domain:  {domain}")
    print(f"  Checks:  {checks}")
    print()

    # AutoGen runtime
    runtime = SingleThreadedAgentRuntime()

    # Register agents
    print("[SETUP] Registriere Agents...", flush=True)

    await OrchestratorAgent.register(
        runtime, "orchestrator",
        lambda: OrchestratorAgent(llm_client, llm_model),
    )
    await CheckerAgent.register(
        runtime, "checker_agent",
        lambda: CheckerAgent(),
    )
    await AnalyzerAgent.register(
        runtime, "analyzer_agent",
        lambda: AnalyzerAgent(llm_client, llm_model),
    )
    await ReporterAgent.register(
        runtime, "reporter_agent",
        lambda: ReporterAgent(llm_client, llm_model),
    )

    runtime.start()
    print("[SETUP] Agents bereit.\n", flush=True)

    # Send verification request to orchestrator
    try:
        report: AuthenticityReport = await runtime.send_message(
            VerifyTarget(
                url=url,
                domain=domain,
                check_types=checks,
            ),
            AgentId("orchestrator", "default"),
        )
    except BaseException:
        try:
            await runtime.stop()
        except BaseException:
            pass
        raise
    else:
        await runtime.stop()

    # Print final report
    print("\n")
    # Encode safely for Windows console
    safe_text = report.report_text.encode("ascii", errors="replace").decode("ascii")
    print(safe_text)
    print()

    # Exit code based on verdict
    verdict_colors = {
        "AUTHENTIC": "\033[92m",    # green
        "SUSPICIOUS": "\033[93m",   # yellow
        "FAKE": "\033[91m",         # red
        "INCONCLUSIVE": "\033[90m", # gray
    }
    reset = "\033[0m"
    color = verdict_colors.get(report.verdict, "")

    print(f"\n  {color}VERDICT: {report.verdict} (Confidence: {report.confidence}){reset}")
    print(f"  Findings: {report.finding_count} | Red Flags: {report.red_flag_count}")
    print()

    return report


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    url = sys.argv[1]
    checks = DEFAULT_CHECKS

    if "--checks" in sys.argv:
        idx = sys.argv.index("--checks")
        if idx + 1 < len(sys.argv):
            checks = sys.argv[idx + 1]

    report = await verify_site(url, checks)

    # Return exit code: 0=authentic, 1=suspicious/inconclusive, 2=fake
    if report.verdict == "AUTHENTIC":
        sys.exit(0)
    elif report.verdict == "FAKE":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
