#!/usr/bin/env python3
"""Build or check the reproducible SketchUp extension RBZ."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from extension_package import PackageError, build_package, check_package


REPO_ROOT = Path(__file__).resolve().parents[1]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="artifact directory (default: dist)",
    )
    parser.add_argument(
        "--check",
        type=Path,
        metavar="RBZ",
        help="validate an existing package instead of building",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        report = (
            check_package(REPO_ROOT, args.check)
            if args.check is not None
            else build_package(REPO_ROOT, args.output_dir)
        )
    except PackageError as error:
        print(f"Package check failed: {error}", file=sys.stderr)
        return 1

    action = "Validated" if args.check is not None else "Built"
    displayed_path = (
        report.path.relative_to(REPO_ROOT)
        if report.path.is_relative_to(REPO_ROOT)
        else report.path
    )
    print(f"{action} {displayed_path}")
    print(f"Version: {report.version}")
    print(f"SHA-256: {report.sha256}")
    print(f"Files: {len(report.files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
