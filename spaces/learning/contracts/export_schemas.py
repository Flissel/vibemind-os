from __future__ import annotations

import json
from pathlib import Path
from pydantic import BaseModel

from .mcp_models import EventEnvelopeV1, ToolRequestV1
from .outcomes import ToolResultV1, TruthReadbackV1


SCHEMAS: dict[str, type[BaseModel]] = {
    "event-envelope-v1.json": EventEnvelopeV1,
    "tool-request-v1.json": ToolRequestV1,
    "tool-result-v1.json": ToolResultV1,
    "truth-readback-v1.json": TruthReadbackV1,
}


def export_schemas(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        content = json.dumps(
            model.model_json_schema(),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        (output_dir / name).write_text(f"{content}\n", encoding="utf-8")


def main() -> int:
    output_dir = Path(__file__).resolve().parents[1] / "tests" / "contract" / "schemas"
    export_schemas(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
