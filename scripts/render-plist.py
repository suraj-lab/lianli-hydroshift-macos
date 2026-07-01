#!/usr/bin/env python3
"""Render launchd plist templates for this checkout."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

LABEL = "com.suraj.lianli-hydroshift"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("template")
    parser.add_argument("output")
    parser.add_argument("--project-dir", default=None,
                        help="path to the project root (default: parent of scripts/)")
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()

    project = Path(args.project_dir).resolve() if args.project_dir else Path(__file__).resolve().parents[1]
    python = project / ".venv" / "bin" / "python"
    config = Path.home() / ".config" / "lianli-hydroshift" / "config.json"
    log_dir = Path(args.log_dir).expanduser() if args.log_dir else Path.home() / "Library" / "Logs" / "lianli-hydroshift"
    log_dir.mkdir(parents=True, exist_ok=True)

    text = Path(args.template).read_text(encoding="utf-8")
    replacements = {
        "@PROJECT_DIR@": str(project),
        "@PYTHON@": str(python),
        "@CONFIG@": str(config),
        "@STDOUT_LOG@": str(log_dir / f"{LABEL}.log"),
        "@STDERR_LOG@": str(log_dir / f"{LABEL}.err"),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
