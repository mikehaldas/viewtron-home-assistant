#!/usr/bin/env bash
# Print a pip requirement for viewtron 1.4.0.
# Uses PyPI when 1.4.0 or newer is published. Until then, installs the
# SDK branch that adds plate direction, confidence, list, and vehicle fields.
set -euo pipefail

SDK_BRANCH="cursor/api-2.1-sdk-1.4.0-9bcc"
SDK_URL="git+https://github.com/mikehaldas/viewtron-python-sdk.git@${SDK_BRANCH}"

spec="$(python3 - "$SDK_URL" <<'PY'
import json
import sys
import urllib.request

git_url = sys.argv[1]
fallback = f"viewtron @ {git_url}"


def parse(version):
    parts = version.split(".")
    nums = []
    for part in parts:
        if not part.isdigit():
            return None
        nums.append(int(part))
    return tuple(nums)


try:
    with urllib.request.urlopen("https://pypi.org/pypi/viewtron/json", timeout=30) as resp:
        releases = json.load(resp)["releases"]
except Exception:
    print(fallback)
    raise SystemExit(0)

available = any((parse(version) or ()) >= (1, 4, 0) for version in releases)
print("viewtron>=1.4.0" if available else fallback)
PY
)"

if [[ "${1:-}" == "--install" ]]; then
  python3 -m pip install "$spec"
else
  printf '%s\n' "$spec"
fi
