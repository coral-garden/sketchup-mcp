"""Run the deterministic Python coverage suite with forbidden I/O guarded."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def reject_forbidden_io(event: str, arguments: tuple[object, ...]) -> None:
    """Fail if a coverage test opens a network endpoint or Ruby subprocess."""

    if event in {"socket.bind", "socket.connect"}:
        address = arguments[1]
        if isinstance(address, tuple):
            raise AssertionError(
                f"deterministic Python coverage test attempted network I/O: {address!r}"
            )
    if event == "subprocess.Popen":
        executable = Path(str(arguments[0])).name
        if executable.startswith(("ruby", "sketchup")):
            raise AssertionError(
                "deterministic Python coverage test attempted a Ruby or SketchUp "
                "subprocess"
            )


def reject_sleep(_seconds: float) -> None:
    raise AssertionError("deterministic Python coverage test attempted to sleep")


def main() -> int:
    os.environ["SKETCHUP_MCP_DETERMINISTIC_TESTS"] = "1"
    sys.addaudithook(reject_forbidden_io)
    time.sleep = reject_sleep
    suite = unittest.defaultTestLoader.discover(str(REPO_ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
