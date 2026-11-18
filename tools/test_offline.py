"""Run Aqua's deterministic tests with sockets and process launches blocked.

This runner never contacts Ollama or opens applications. Live, execution-disabled
model checks are a separate opt-in command in smoke_tools.py.
"""

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test*.py", help="unittest discovery filename pattern")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    tests = Path(__file__).resolve().parents[1] / "tests"
    # Running a file under tools/ otherwise omits the repository root, which
    # the diagnostic-script regression tests import as the tools namespace.
    sys.path.insert(0, str(tests.parent))
    blocked = AssertionError("Automated tests must not use real sockets or launch processes.")
    with patch.object(socket, "create_connection", side_effect=blocked), \
            patch.object(socket.socket, "connect", side_effect=blocked), \
            patch.object(socket.socket, "connect_ex", side_effect=blocked), \
            patch.object(socket, "getaddrinfo", side_effect=blocked), \
            patch.object(subprocess, "Popen", side_effect=blocked):
        suite = unittest.defaultTestLoader.discover(str(tests), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
