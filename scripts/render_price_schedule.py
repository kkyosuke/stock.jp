"""Render the macOS launchd price-collector definition inside private state."""

from __future__ import annotations

import argparse
import plistlib
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "operations/private/com.stockjp.price-collector.plist"


def render_schedule(
    *, root: Path = PROJECT_ROOT, output: Path | None = None
) -> dict[str, str]:
    template_path = root / "operations/templates/com.stockjp.price-collector.plist"
    destination = output or (root / "operations/private/com.stockjp.price-collector.plist")
    destination = destination if destination.is_absolute() else root / destination
    private = (root / "operations/private").resolve()
    resolved = destination.resolve()
    if not resolved.is_relative_to(private):
        raise ValueError("schedule output must stay under operations/private")
    rendered = template_path.read_text(encoding="utf-8").replace(
        "__PROJECT_ROOT__", str(root.resolve())
    )
    plistlib.loads(rendered.encode("utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as target:
        target.write(rendered)
        temporary = Path(target.name)
    temporary.chmod(0o600)
    temporary.replace(destination)
    return {
        "status": "RENDERED_NOT_INSTALLED",
        "path": destination.relative_to(root).as_posix(),
        "next_step": (
            "after merging into the permanent checkout, copy this file to "
            "~/Library/LaunchAgents and load it with launchctl"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(render_schedule(output=args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
