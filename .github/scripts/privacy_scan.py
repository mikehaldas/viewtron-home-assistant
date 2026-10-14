#!/usr/bin/env python3
"""Scan a pull request for privacy issues.

The check looks at added diff lines (``git diff base...head``), the pull
request title and body from the GitHub event payload, and commit messages
from ``git log``. It is stdlib-only so the workflow does not install
dependencies.

A confidential-term hit is printed as ``[confidential term]``. The matched
text is not stored on the finding and is not written to the log.

``PRIVACY_SCAN_TERMS`` and ``PRIVACY_SCAN_PLATES`` are repository secrets.
Each line is a SHA-256 hex digest of a lowercase term. An unset or empty
secret skips only that digest check.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RULE_TERM = "confidential-term"
RULE_IP = "private-ip"
_plate_rule = "-".join(("license", "plate"))
RULE_PLATE = _plate_rule
CONFIDENTIAL_MASK = "[confidential term]"

ALLOW_FILENAME = "privacy-scan-allow.txt"
_approved_name = "privacy-scan-plates-approved.txt"
APPROVED_FILENAME = _approved_name

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SEPARATORS_RE = re.compile(r"[\s\-_]+")


class GitError(RuntimeError):
    """A git command failed."""


class Finding:
    """One rule hit. The matched text is intentionally not stored."""

    def __init__(self, path: str, line: int, rule: str) -> None:
        self.path = path
        self.line = line
        self.rule = rule

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return (self.path, self.line, self.rule) == (other.path, other.line, other.rule)

    def __repr__(self) -> str:
        return f"Finding({self.path!r}, {self.line!r}, {self.rule!r})"


class ScanContext:
    def __init__(
        self,
        term_hashes: set[str],
        plate_hashes: set[str],
        allow_nets: list[ipaddress.IPv4Network],
        approved: set[str],
    ) -> None:
        self.term_hashes = term_hashes
        self.plate_hashes = plate_hashes
        self.allow_nets = allow_nets
        self.approved = approved


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def warn(message: str) -> None:
    print(f"::warning::{message}", file=sys.stderr)


def _compile_patterns() -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """Build matchers without embedding private addresses or plate samples."""
    octet = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])"
    ipv4 = re.compile(rf"(?<![0-9.])(?:{octet}\.){{3}}{octet}(?![0-9.])")
    # "plate" may be followed by an uppercase letter (plateNumber) but not by
    # a lowercase letter, so "template" is not a mention.
    mention = re.compile(
        r"(?i)(?<![A-Za-z])plates?(?![a-z])"
        r"|(?<![A-Za-z])(?:lpr|anpr)(?![A-Za-z])"
        r"|(?<![A-Za-z])licen[cs]es?[\s\-_]+plates?(?![A-Za-z])"
    )
    quoted = re.compile(r"""['"]([A-Z0-9]{4,8})['"]""")
    tags = "licensePlateNumber|plateNumber|carPlate|licensePlate|plate"
    xml_value = re.compile(
        rf"(?i)<(?P<tag>{tags})\b[^>]*>\s*"
        rf"(?:<!\[CDATA\[(?P<cdata>.*?)\]\]>|(?P<text>[^<]*))"
    )
    return ipv4, mention, quoted, xml_value


_quad_re, _MENTION_RE, _QUOTED_RE, _XML_VALUE_RE = _compile_patterns()

# Names accepted as a JSON key or a Python keyword argument.
_FIELD_KEYS = ("license_plate", "plate_number", "plateNumber", "plate")

_TECH_TOKENS = frozenset(
    {
        "V1",
        "V2",
        "IPV4",
        "IPV6",
        "H264",
        "H265",
        "MP4",
        "MP3",
        "RFC1918",
        "HTTP2",
        "HTTP1",
    }
)


def private_networks() -> tuple[ipaddress.IPv4Network, ...]:
    def network(octets: tuple[int, int, int, int], prefix: int) -> ipaddress.IPv4Network:
        packed = 0
        for octet in octets:
            packed = (packed << 8) | octet
        net = ipaddress.ip_network((packed, prefix))
        if not isinstance(net, ipaddress.IPv4Network):
            raise RuntimeError("expected an IPv4 network")
        return net

    return (
        network((10, 0, 0, 0), 8),
        network((172, 16, 0, 0), 12),
        network((192, 168, 0, 0), 16),
        network((169, 254, 0, 0), 16),
    )


_PRIVATE_NETS = private_networks()


def parse_hash_secret(value: str | None) -> tuple[str, set[str]]:
    """Return ``(status, hashes)``.

    Status is ``missing`` when the variable is unset or blank, ``ok`` when
    every content line is a SHA-256 hex digest, and ``invalid`` otherwise.
    """
    if value is None or value.strip() == "":
        return "missing", set()
    hashes: set[str] = set()
    invalid = False
    for raw in value.lstrip("\ufeff").splitlines():
        item = raw.strip().lower()
        if not item or item.startswith("#"):
            continue
        if _HASH_RE.fullmatch(item):
            hashes.add(item)
        else:
            invalid = True
    if invalid or not hashes:
        return "invalid", set()
    return "ok", hashes


def ngram_candidates(text: str) -> list[str]:
    """Lowercase word n-grams of 1 to 3 words, plus separator-stripped forms."""
    lowered = text.lower()
    matches = list(_WORD_RE.finditer(lowered))
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            found.append(value)

    for start in range(len(matches)):
        for size in range(1, 4):
            end = start + size
            if end > len(matches):
                break
            words = [matches[index].group(0) for index in range(start, end)]
            raw = lowered[matches[start].start() : matches[end - 1].end()]
            stripped = _SEPARATORS_RE.sub("", raw)
            add(raw)
            add(" ".join(words))
            add("".join(words))
            add(stripped)
            add("-".join(words))
            add("_".join(words))
    return found


def normalize_plate(value: str) -> str:
    return _SEPARATORS_RE.sub("", value).upper()


def load_allow_list(path: Path) -> list[ipaddress.IPv4Network]:
    if not path.is_file():
        return []
    nets: list[ipaddress.IPv4Network] = []
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), 1):
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        token = entry if "/" in entry else entry + "/32"
        try:
            net = ipaddress.ip_network(token, strict=False)
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: invalid address or network") from exc
        if not isinstance(net, ipaddress.IPv4Network):
            raise ValueError(f"{path}:{lineno}: only IPv4 addresses are supported")
        nets.append(net)
    return nets


def load_approved_plates(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    approved: set[str] = set()
    text = path.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), 1):
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        norm = normalize_plate(entry)
        if not norm.isalnum():
            raise ValueError(f"{path}:{lineno}: invalid approved plate")
        approved.add(norm)
    return approved


def _ip_allowed(addr: ipaddress.IPv4Address, nets: list[ipaddress.IPv4Network]) -> bool:
    return any(addr in net for net in nets)


def find_private_ips(text: str, allow_nets: list[ipaddress.IPv4Network]) -> list[str]:
    found: list[str] = []
    for match in _quad_re.finditer(text):
        literal = match.group(0)
        try:
            addr = ipaddress.ip_address(literal)
        except ValueError:
            continue
        if not isinstance(addr, ipaddress.IPv4Address):
            continue
        if not any(addr in net for net in _PRIVATE_NETS):
            continue
        if _ip_allowed(addr, allow_nets):
            continue
        found.append(literal)
    return found


def _line_mentions_plate(text: str) -> bool:
    return _MENTION_RE.search(text) is not None


def _is_tech_token(value: str) -> bool:
    compact = normalize_plate(value)
    if compact in _TECH_TOKENS:
        return True
    if re.fullmatch(r"V\d{1,2}", compact):
        return True
    if re.fullmatch(r"H26[45]", compact):
        return True
    if re.fullmatch(r"RFC\d+", compact):
        return True
    if re.fullmatch(r"HTTP\d+", compact):
        return True
    if re.fullmatch(r"(?:MP[34]|IPV[46])", compact):
        return True
    raw = value.strip()
    if re.fullmatch(r"(?:IP|LPR|NVR)-[A-Z0-9].*", raw, re.IGNORECASE):
        return True
    return False


def _is_hex_or_uuid(value: str) -> bool:
    text = value.strip()
    if re.fullmatch(r"0[xX][0-9A-Fa-f]+", text):
        return True
    uuid = (
        r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
        r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    )
    if re.fullmatch(r"\{?" + uuid + r"\}?", text):
        return True
    # Longer than a plate token, so this does not drop short plate text
    # that happens to use the letters A through F.
    if re.fullmatch(r"[0-9A-Fa-f]{9,}", text):
        return True
    return False


def _excluded_plate(value: str) -> bool:
    return _is_tech_token(value) or _is_hex_or_uuid(value)


def _usable_field_value(value: str) -> str | None:
    raw = value.strip()
    if not raw or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-_]*", raw):
        return None
    norm = normalize_plate(raw)
    if not (2 <= len(norm) <= 8) or not any(char.isalpha() for char in norm):
        return None
    if _excluded_plate(raw):
        return None
    return norm


def _field_key_values(text: str) -> list[str]:
    keys = "|".join(_FIELD_KEYS)
    pattern = re.compile(
        rf"(?i)(?<![A-Za-z0-9_])(?P<qkey>['\"]?)(?P<key>{keys})(?P=qkey)"
        rf"\s*(?:(?<![!<>=])=(?!=)|:(?!:))\s*"
        rf"(?P<qval>['\"])(?P<value>[^'\"]*)(?P=qval)"
    )
    found: list[str] = []
    for match in pattern.finditer(text):
        usable = _usable_field_value(match.group("value"))
        if usable:
            found.append(usable)
    return found


def _xml_field_values(text: str) -> list[str]:
    found: list[str] = []
    for match in _XML_VALUE_RE.finditer(text):
        raw = match.group("cdata")
        if raw is None:
            raw = match.group("text") or ""
        usable = _usable_field_value(raw)
        if usable:
            found.append(usable)
    return found


def _quoted_plate_literals(text: str) -> list[str]:
    if not _line_mentions_plate(text):
        return []
    found: list[str] = []
    for match in _QUOTED_RE.finditer(text):
        token = match.group(1)
        if not any(char.isalpha() for char in token):
            continue
        if not any(char.isdigit() for char in token):
            continue
        if _excluded_plate(token):
            continue
        before = text[max(0, match.start() - 4) : match.start()]
        if re.search(r"(?:IP|LPR|NVR)-$", before, re.IGNORECASE):
            continue
        found.append(token)
    return found


def heuristic_plates(text: str) -> list[str]:
    """Plate text from a plate field, or a quoted literal in plate context."""
    return _xml_field_values(text) + _field_key_values(text) + _quoted_plate_literals(text)


def scan_line(path: str, lineno: int, text: str, ctx: ScanContext) -> list[Finding]:
    findings: list[Finding] = []
    if ctx.term_hashes:
        for candidate in ngram_candidates(text):
            if sha256_hex(candidate) in ctx.term_hashes:
                findings.append(Finding(path, lineno, RULE_TERM))
                break

    for _addr in find_private_ips(text, ctx.allow_nets):
        findings.append(Finding(path, lineno, RULE_IP))

    flagged: set[str] = set()
    for token in heuristic_plates(text):
        norm = normalize_plate(token)
        if norm in ctx.approved or norm in flagged:
            continue
        flagged.add(norm)
        findings.append(Finding(path, lineno, RULE_PLATE))
    if ctx.plate_hashes:
        for candidate in ngram_candidates(text):
            if sha256_hex(candidate) not in ctx.plate_hashes:
                continue
            norm = normalize_plate(candidate)
            if not norm or norm in ctx.approved or norm in flagged:
                continue
            flagged.add(norm)
            findings.append(Finding(path, lineno, RULE_PLATE))
    return findings


def format_finding(finding: Finding) -> str:
    if finding.rule == RULE_TERM:
        message = f"{RULE_TERM} {CONFIDENTIAL_MASK}"
    else:
        message = finding.rule
    return f"{finding.path}:{finding.line}: {message}"


def summarize(findings: list[Finding]) -> list[str]:
    counts: dict[tuple[str, str], int] = {}
    for finding in findings:
        key = (finding.rule, finding.path)
        counts[key] = counts.get(key, 0) + 1
    lines = []
    for rule, path in sorted(counts):
        lines.append(f"{rule}\t{path}\t{counts[(rule, path)]}")
    return lines


def _diff_path(raw: str) -> str | None:
    name = raw.strip()
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1]
    if name == "/dev/null":
        return None
    if name.startswith("b/"):
        return name[2:]
    return name


def parse_added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Return ``(path, new_line, text)`` for added lines only."""
    rows: list[tuple[str, int, str]] = []
    path: str | None = None
    new_line: int | None = None
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
            new_line = None
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match else None
            continue
        if not in_hunk:
            if line.startswith("+++ "):
                path = _diff_path(line[4:])
            continue
        if new_line is None or path is None:
            continue
        if line.startswith("+"):
            rows.append((path, new_line, line[1:]))
            new_line += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue
        else:
            new_line += 1
    return rows


def iter_event(event: object) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    if not isinstance(event, dict):
        return rows
    pull = event.get("pull_request")
    if not isinstance(pull, dict):
        pull = {}
    title = pull.get("title") or ""
    if isinstance(title, str) and title:
        rows.append(("pr-title", 1, title))
    body = pull.get("body") or ""
    if isinstance(body, str):
        for lineno, line in enumerate(body.splitlines(), 1):
            rows.append(("pr-body", lineno, line))
    commits = event.get("commits")
    if isinstance(commits, list):
        for commit in commits:
            if not isinstance(commit, dict):
                continue
            sha = str(commit.get("id") or commit.get("sha") or "unknown")
            message = commit.get("message") or ""
            if not isinstance(message, str) or message == "":
                continue
            short = sha[:7]
            for lineno, line in enumerate(message.splitlines(), 1):
                rows.append((f"commit/{short}", lineno, line))
    return rows


def git_bytes(repo: Path, args: list[str]) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(err or "git {0} failed".format(" ".join(args)))
    return proc.stdout


def git_text(repo: Path, args: list[str]) -> str:
    return git_bytes(repo, args).decode("utf-8", errors="replace")


def iter_head_files(repo: Path) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    listing = git_text(repo, ["ls-files", "-z"])
    for rel in listing.split("\0"):
        if not rel:
            continue
        try:
            data = git_bytes(repo, ["show", f"HEAD:{rel}"])
        except GitError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            rows.append((rel, lineno, line))
    return rows


def iter_commit_log(repo: Path, base: str, head: str) -> list[tuple[str, int, str]]:
    out = git_text(
        repo,
        ["log", "--reverse", "--format=%H%x1e%B%x1e", f"{base}..{head}"],
    )
    return _parse_commit_log(out)


def iter_head_commit(repo: Path) -> list[tuple[str, int, str]]:
    sha = git_text(repo, ["rev-parse", "HEAD"]).strip()
    body = git_text(repo, ["log", "-1", "--format=%B", "HEAD"])
    rows: list[tuple[str, int, str]] = []
    short = sha[:7]
    for lineno, line in enumerate(body.splitlines(), 1):
        rows.append((f"commit/{short}", lineno, line))
    return rows


def _parse_commit_log(out: str) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    parts = out.split("\x1e")
    index = 0
    while index + 1 < len(parts):
        sha = parts[index].strip()
        body = parts[index + 1]
        index += 2
        if not sha:
            continue
        # A newline git inserts between commits stays on the next hash.
        sha = sha.splitlines()[-1].strip()
        if not sha:
            continue
        short = sha[:7]
        for lineno, line in enumerate(body.splitlines(), 1):
            rows.append((f"commit/{short}", lineno, line))
    return rows


def load_event_file(path: str) -> list[tuple[str, int, str]]:
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, json.JSONDecodeError):
        warn(
            "could not read the GitHub event payload; skipping the title and body"
        )
        return []
    return iter_event(event)


def _dedupe(rows: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    seen: set[tuple[str, int, str]] = set()
    unique: list[tuple[str, int, str]] = []
    for row in rows:
        if row in seen:
            continue
        seen.add(row)
        unique.append(row)
    return unique


def collect_records(args: argparse.Namespace, env: dict[str, str]) -> list[tuple[str, int, str]]:
    repo = Path(args.repo)
    rows: list[tuple[str, int, str]] = []
    if args.worktree:
        rows.extend(iter_head_files(repo))
        rows.extend(iter_head_commit(repo))
        return rows
    if not args.base:
        raise GitError("--base is required (or pass --worktree)")
    diff = git_text(
        repo,
        [
            "diff",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            f"{args.base}...{args.head}",
        ],
    )
    rows.extend(parse_added_lines(diff))
    rows.extend(iter_commit_log(repo, args.base, args.head))
    event_path = args.event or env.get("GITHUB_EVENT_PATH") or ""
    if event_path:
        rows.extend(load_event_file(event_path))
    return _dedupe(rows)


def emit(finding: Finding) -> None:
    message = format_finding(finding)
    print(message)
    path = finding.path
    detail = message.split(": ", 1)[-1]
    if path.startswith("pr-") or path.startswith("commit/"):
        print(f"::error::{message}", file=sys.stderr)
        return
    print(
        f"::error file={path},line={finding.line}::{detail}",
        file=sys.stderr,
    )


def run_scan(args: argparse.Namespace, env: dict[str, str]) -> int:
    repo = Path(args.repo)
    allow_path = Path(args.allow) if args.allow else repo / ".github" / ALLOW_FILENAME
    approved_path = (
        Path(args.approved) if args.approved else repo / ".github" / APPROVED_FILENAME
    )
    try:
        allow_nets = load_allow_list(allow_path)
        approved = load_approved_plates(approved_path)
    except ValueError as exc:
        print(f"privacy scan: {exc}", file=sys.stderr)
        return 2

    term_status, term_hashes = parse_hash_secret(env.get("PRIVACY_SCAN_TERMS"))
    plate_status, plate_hashes = parse_hash_secret(env.get("PRIVACY_SCAN_PLATES"))
    if term_status == "missing":
        warn("PRIVACY_SCAN_TERMS is not set; skipping confidential-term rule")
        term_hashes = set()
    elif term_status == "invalid":
        print(
            "privacy scan: PRIVACY_SCAN_TERMS must be SHA-256 hex digests, one per line",
            file=sys.stderr,
        )
        return 2
    if plate_status == "missing":
        warn("PRIVACY_SCAN_PLATES is not set; skipping the hashed digest check")
        plate_hashes = set()
    elif plate_status == "invalid":
        print(
            "privacy scan: PRIVACY_SCAN_PLATES must be SHA-256 hex digests, one per line",
            file=sys.stderr,
        )
        return 2

    try:
        records = collect_records(args, env)
    except GitError as exc:
        print(f"privacy scan: {exc}", file=sys.stderr)
        return 2

    ctx = ScanContext(term_hashes, plate_hashes, allow_nets, approved)
    findings: list[Finding] = []
    for path, lineno, text in records:
        findings.extend(scan_line(path, lineno, text, ctx))

    for finding in findings:
        emit(finding)
    print("counts by rule and file:")
    summary = summarize(findings)
    if summary:
        print("\n".join(summary))
    else:
        print("(none)")
    if findings:
        print(
            f"privacy scan failed: {len(findings)} finding(s)",
            file=sys.stderr,
        )
        return 1
    print("privacy scan passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan a pull request for privacy issues.")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--base", help="Diff and log base commit")
    parser.add_argument("--head", default="HEAD", help="Diff and log head commit")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="Scan tracked files at HEAD plus the HEAD commit message",
    )
    parser.add_argument("--event", help="GitHub event JSON path")
    parser.add_argument("--allow", help="Private-address opt-out file")
    parser.add_argument("--approved", help="Approved plate file")
    return parser


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    return run_scan(args, env)


if __name__ == "__main__":
    sys.exit(main())
