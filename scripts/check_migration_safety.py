"""Block CI if migration contains unsafe DDL patterns."""
from __future__ import annotations

import pathlib
import re
import sys

UNSAFE = [
    (re.compile(r"ADD COLUMN .* NOT NULL(?! DEFAULT)", re.IGNORECASE),
     "Add NOT NULL column without DEFAULT — locks table on large tables"),
    (re.compile(r"DROP COLUMN", re.IGNORECASE),
     "DROP COLUMN — requires expand/contract release pattern"),
    (re.compile(r"ALTER COLUMN .* TYPE", re.IGNORECASE),
     "ALTER COLUMN TYPE — rewrites table"),
    (re.compile(r"CREATE INDEX(?! CONCURRENTLY)", re.IGNORECASE),
     "CREATE INDEX without CONCURRENTLY — blocks writes"),
    (re.compile(r"DROP INDEX(?! CONCURRENTLY)", re.IGNORECASE),
     "DROP INDEX without CONCURRENTLY"),
    (re.compile(r"LOCK TABLE", re.IGNORECASE),
     "Explicit LOCK TABLE"),
]

ROOT = pathlib.Path(__file__).resolve().parent.parent / "migrations" / "versions"


def main() -> int:
    fails = 0
    for path in sorted(ROOT.glob("*.py")):
        content = path.read_text()
        for pattern, msg in UNSAFE:
            if pattern.search(content):
                print(f"::error file={path}::{msg}")
                fails += 1
    if fails:
        print(f"\n{fails} unsafe migration pattern(s) found.")
        print("Override with annotation # safety:ok if intentional.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
