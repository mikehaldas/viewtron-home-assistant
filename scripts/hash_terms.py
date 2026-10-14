#!/usr/bin/env python3
"""Print SHA-256 hashes of lowercase privacy-scan terms.

Read one term per line on stdin. Blank lines and lines that start with
``#`` are ignored. Print one hex digest per term and nothing else.

    python scripts/hash_terms.py < terms.txt

Paste the hashes into the PRIVACY_SCAN_TERMS or PRIVACY_SCAN_PLATES
repository secret. Keep the plaintext file off the repository.
"""

from __future__ import annotations

import hashlib
import sys


def hash_term(term: str) -> str:
    normalized = term.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def main() -> int:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sys.stdout.write(hash_term(stripped) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
