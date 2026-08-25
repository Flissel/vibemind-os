from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spaces.learning.validation.golden_path import run_deterministic  # noqa: E402


FIXTURE = ROOT / "spaces/learning/tests/e2e/fixtures/golden-course/authority.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Learning MCP golden path")
    parser.add_argument(
        "--profile", choices=("deterministic", "live-provider"), default="deterministic"
    )
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.profile == "live-provider":
        parser.error(
            "live-provider requires a separately configured admitted OpenFang runtime; "
            "deterministic validation is not live-provider evidence"
        )
    selected = args.workdir or Path(tempfile.mkdtemp(prefix="learning-golden-"))
    report = run_deterministic(selected, fixture_path=FIXTURE)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
