"""Tests for the pull request privacy scan.

Dummy confidential terms in this file, such as acmecorp, are made up.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


scan = _load("privacy_scan_under_test", ROOT / ".github" / "scripts" / "privacy_scan.py")
hasher = _load("hash_terms_under_test", ROOT / "scripts" / "hash_terms.py")

TERM = "acmecorp"


def dotted(*parts):
    return ".".join(str(part) for part in parts)


def chunk(*parts):
    return "".join(parts)


def digests(text):
    return {scan.sha256_hex(candidate) for candidate in scan.ngram_candidates(text)}


def rules_for(text, ctx=None):
    if ctx is None:
        ctx = scan.ScanContext(set(), set(), [], set())
    return [item.rule for item in scan.scan_line("sample.txt", 1, text, ctx)]


def git(repo, *args):
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Privacy Scan Test",
            "GIT_AUTHOR_EMAIL": "privacy-scan@example.com",
            "GIT_COMMITTER_NAME": "Privacy Scan Test",
            "GIT_COMMITTER_EMAIL": "privacy-scan@example.com",
        }
    )
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout


def init_repo(path):
    path.mkdir()
    git(path, "init", "-b", "main")
    (path / "keep.txt").write_text("hello\n", encoding="utf-8")
    git(path, "add", "keep.txt")
    git(path, "commit", "-m", "base")
    return git(path, "rev-parse", "HEAD").strip()


class TestTerms:
    def test_hash_helper_matches_scanner(self):
        assert hasher.hash_term("  AcmeCorp  ") == scan.sha256_hex(TERM)

    def test_hash_helper_cli(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "hash_terms.py")],
            input="  AcmeCorp  \n\n# skip me\nacme corp\n",
            text=True,
            capture_output=True,
            check=True,
        )
        assert proc.stdout.splitlines() == [
            scan.sha256_hex(TERM),
            scan.sha256_hex("acme corp"),
        ]
        assert proc.stderr == ""

    def test_separator_forms_match_stripped_term(self):
        expected = scan.sha256_hex(TERM)
        for text in (
            TERM,
            "AcmeCorp",
            "ACMECORP",
            "acme-corp",
            "acme_corp",
            "acme corp",
            "vendor acme-corp today",
        ):
            assert expected in digests(text)

    def test_spaced_and_joined_forms(self):
        phrase = "north acme corp"
        assert scan.sha256_hex("north acme corp") in digests(phrase)
        assert scan.sha256_hex("northacmecorp") in digests(phrase)
        assert scan.sha256_hex("north-acme-corp") in digests(phrase)
        assert scan.sha256_hex("north_acme_corp") in digests(phrase)
        four = "please contact acme corp"
        assert scan.sha256_hex(four) not in digests(four)
        assert scan.sha256_hex("contact acme corp") in digests(four)

    def test_unrelated_words_do_not_match(self):
        expected = scan.sha256_hex(TERM)
        assert expected not in digests("acme")
        assert expected not in digests("corporate")

    def test_hit_is_masked_and_not_stored(self):
        ctx = scan.ScanContext({scan.sha256_hex(TERM)}, set(), [], set())
        findings = scan.scan_line("notes.txt", 3, "Hello AcmeCorp", ctx)
        assert findings == [scan.Finding("notes.txt", 3, scan.RULE_TERM)]
        rendered = scan.format_finding(findings[0])
        assert rendered == "notes.txt:3: " + scan.RULE_TERM + " " + scan.CONFIDENTIAL_MASK
        assert TERM not in rendered
        assert "AcmeCorp" not in rendered
        assert TERM not in repr(findings[0])
        assert all(TERM not in str(value) for value in vars(findings[0]).values())

    def test_secret_parsing(self):
        digest = scan.sha256_hex(TERM)
        status, hashes = scan.parse_hash_secret(digest.upper() + "\n# note\n")
        assert status == "ok"
        assert hashes == {digest}
        assert scan.parse_hash_secret(None) == ("missing", set())
        assert scan.parse_hash_secret("  \n") == ("missing", set())
        assert scan.parse_hash_secret("# comment only\n")[0] == "invalid"
        assert scan.parse_hash_secret(digest + "\n" + TERM + "\n")[0] == "invalid"


class TestAddresses:
    def test_private_boundaries(self):
        inside = (
            (10, 0, 0, 0),
            (10, 255, 255, 255),
            (172, 16, 0, 0),
            (172, 31, 255, 255),
            (192, 168, 0, 0),
            (192, 168, 255, 255),
            (169, 254, 0, 0),
            (169, 254, 255, 255),
        )
        for parts in inside:
            address = dotted(*parts)
            assert scan.find_private_ips(address, []) == [address]

    def test_public_and_other_ranges_are_ignored(self):
        outside = (
            (8, 8, 8, 8),
            (1, 1, 1, 1),
            (9, 255, 255, 255),
            (11, 0, 0, 1),
            (127, 0, 0, 1),
            (172, 15, 255, 255),
            (172, 32, 0, 0),
            (192, 167, 255, 255),
            (192, 169, 0, 1),
            (169, 253, 255, 255),
            (169, 255, 0, 1),
            (100, 64, 0, 1),
        )
        for parts in outside:
            assert scan.find_private_ips(dotted(*parts), []) == []

    def test_partial_and_trailing_dot_do_not_match(self):
        assert scan.find_private_ips(dotted(10, 1, 2), []) == []
        assert scan.find_private_ips(dotted(10, 1, 2, 3, 4), []) == []

    def test_two_addresses_on_one_line(self):
        first = dotted(10, 0, 0, 1)
        second = dotted(192, 168, 0, 2)
        found = scan.find_private_ips(first + " and " + second, [])
        assert found == [first, second]

    def test_allow_list_accepts_address_and_prefix(self, tmp_path):
        path = tmp_path / "allow.txt"
        listed = dotted(192, 168, 1, 9)
        prefix = dotted(10, 0, 0, 0) + "/8"
        path.write_text("# note\n" + listed + "\n" + prefix + "\n", encoding="utf-8")
        nets = scan.load_allow_list(path)
        assert scan.find_private_ips(listed, nets) == []
        assert scan.find_private_ips(dotted(10, 9, 8, 7), nets) == []
        other = dotted(192, 168, 2, 2)
        assert scan.find_private_ips(other, nets) == [other]

    def test_allow_comment_does_not_hide_an_address(self, tmp_path):
        path = tmp_path / "allow.txt"
        hidden = dotted(192, 168, 5, 5)
        path.write_text("# documented " + hidden + "\n", encoding="utf-8")
        nets = scan.load_allow_list(path)
        assert scan.find_private_ips(hidden, nets) == [hidden]

    def test_invalid_allow_entry(self, tmp_path):
        path = tmp_path / "allow.txt"
        path.write_text("not-an-address\n", encoding="utf-8")
        with pytest.raises(ValueError):
            scan.load_allow_list(path)


class TestPlates:
    def test_quoted_literal_needs_context_and_shape(self):
        four = chunk("AB", "12")
        eight = chunk("ABCD", "1234")
        nine = chunk("ABCD", "12345")
        short = chunk("A", "1")
        keyword = "plate"
        assert rules_for(keyword + " " + repr(four)) == [scan.RULE_PLATE]
        assert rules_for(keyword + " " + repr(eight)) == [scan.RULE_PLATE]
        assert rules_for(keyword + " " + repr(nine)) == []
        assert rules_for(keyword + " " + repr(short)) == []
        assert rules_for(keyword + " " + four) == []
        assert rules_for(repr(four)) == []
        assert rules_for(keyword + " " + repr(chunk("12", "345"))) == []
        assert rules_for(keyword + " " + repr(chunk("ab", "12"))) == []
        assert rules_for(keyword + " " + repr(chunk("ABCD"))) == []

    def test_context_is_plate_lpr_anpr_or_license_plate(self):
        token = repr(chunk("AB", "12", "CD"))
        for phrase in ("plate", "plates", "lpr", "anpr", "license plate", "LPR", "ANPR"):
            assert rules_for(phrase + " " + token) == [scan.RULE_PLATE]
        assert rules_for("license " + token) == []
        assert rules_for("template " + token) == []
        assert rules_for("The MIT License") == []
        assert rules_for("License :: OSI Approved :: MIT License") == []

    def test_method_call_with_quoted_literal(self):
        token = repr(chunk("AB", "12", "CD"))
        text = "event.get_plate_number() == " + token
        assert rules_for(text) == [scan.RULE_PLATE]

    def test_xml_and_key_values(self):
        value = chunk("AB", "12", "CD")
        for tag in ("plateNumber", "plate", "carPlate", "licensePlateNumber"):
            assert rules_for("<" + tag + ">" + value + "</" + tag + ">") == [scan.RULE_PLATE]
        wrapped = "<plateNumber><![CDATA[" + value + "]]></plateNumber>"
        assert rules_for(wrapped) == [scan.RULE_PLATE]
        assert rules_for("<plateNumber></plateNumber>") == []
        assert rules_for("<plateColor>" + value + "</plateColor>") == []
        assert rules_for("plate_number=" + repr(value)) == [scan.RULE_PLATE]
        assert rules_for('"plateNumber": ' + repr(value)) == [scan.RULE_PLATE]
        assert rules_for("license_plate=" + repr(value)) == [scan.RULE_PLATE]
        assert rules_for('{"plate": ' + repr(value) + "}") == [scan.RULE_PLATE]

    def test_tech_tokens_models_hex_and_uuid_are_ignored(self):
        keyword = "plate"
        for token in ("H264", "H265", "IPV4", "RFC1918", "HTTP2", "V1", "V2", "MP4"):
            assert rules_for(keyword + " " + repr(token)) == []
            assert rules_for("<plateNumber>" + token + "</plateNumber>") == []
        model = "LPR" + "-" + "IP4"
        assert rules_for("<plateNumber>" + model + "</plateNumber>") == []
        ident = "12345678-1234-5678-1234-123456789abc"
        assert rules_for("<plateNumber>" + ident + "</plateNumber>") == []
        prefixed = "0x" + "DEADBEEF"
        assert rules_for("<plateNumber>" + prefixed + "</plateNumber>") == []
        long_hex = "ABCDEF1234567890"
        assert rules_for("<plateNumber>" + long_hex + "</plateNumber>") == []
        kept = chunk("AB", "C1", "23")
        assert rules_for(keyword + " " + repr(kept)) == [scan.RULE_PLATE]

    def test_approved_spelling_passes(self):
        token = chunk("AB", "12")
        approved = {scan.normalize_plate(chunk("ab", "-", "12"))}
        ctx = scan.ScanContext(set(), set(), [], approved)
        assert rules_for("plate " + repr(token), ctx) == []
        assert rules_for("<plateNumber>" + token + "</plateNumber>", ctx) == []

    def test_hashed_plate_matches_and_does_not_double_count(self):
        token = chunk("AB", "12")
        digest = scan.sha256_hex(token.lower())
        ctx = scan.ScanContext(set(), {digest}, [], set())
        assert rules_for(token, ctx) == [scan.RULE_PLATE]
        lowered = chunk("ab", "12")
        assert rules_for("saw " + lowered, ctx) == [scan.RULE_PLATE]
        hyphenated = "id " + chunk("AB", "-", "12")
        assert rules_for(hyphenated, ctx) == [scan.RULE_PLATE]
        assert rules_for("plate " + repr(token), ctx) == [scan.RULE_PLATE]

    def test_approved_plate_suppresses_hash_hit(self):
        token = chunk("AB", "12")
        ctx = scan.ScanContext(
            set(),
            {scan.sha256_hex(token.lower())},
            [],
            {scan.normalize_plate(token)},
        )
        assert rules_for(token, ctx) == []

    def test_missing_plate_secret_still_runs_heuristic(self):
        token = chunk("CD", "34")
        assert rules_for("plate " + repr(token)) == [scan.RULE_PLATE]
        assert rules_for(token) == []


class TestDiffAndEvents:
    def test_added_lines_only_and_line_numbers(self):
        added = dotted(10, 8, 0, 2)
        removed = dotted(10, 8, 0, 1)
        context = dotted(10, 8, 0, 9)
        diff = "\n".join(
            [
                "diff --git a/cam.py b/cam.py",
                "--- a/cam.py",
                "+++ b/cam.py",
                "@@ -1,3 +1,3 @@",
                " keep",
                " " + context,
                "-old " + removed,
                "+new " + added,
                "\\ No newline at end of file",
                "diff --git a/old.txt b/new.txt",
                "--- /dev/null",
                "+++ b/new.txt",
                "@@ -0,0 +1,2 @@",
                "+one",
                "+two",
            ]
        )
        assert scan.parse_added_lines(diff) == [
            ("cam.py", 3, "new " + added),
            ("new.txt", 1, "one"),
            ("new.txt", 2, "two"),
        ]

    def test_deleted_file_has_no_added_lines(self):
        diff = "\n".join(
            [
                "diff --git a/gone.txt b/gone.txt",
                "--- a/gone.txt",
                "+++ /dev/null",
                "@@ -1 +0,0 @@",
                "-bye",
            ]
        )
        assert scan.parse_added_lines(diff) == []

    def test_commit_log_parser(self):
        first = "a" * 40
        second = "b" * 40
        blob = (
            first
            + "\x1emsg one\n\x1e\n"
            + second
            + "\x1emsg two\n\x1e"
        )
        assert scan._parse_commit_log(blob) == [
            ("commit/" + first[:7], 1, "msg one"),
            ("commit/" + second[:7], 1, "msg two"),
        ]

    def test_event_title_body_and_commits(self):
        marker = chunk("CD", "34")
        message = "saw plate " + repr(marker)
        event = {
            "pull_request": {"title": "hello", "body": "line one\nline two"},
            "commits": [{"id": "abc1234ffff", "message": message + "\nmore"}],
        }
        rows = scan.iter_event(event)
        assert rows[0] == ("pr-title", 1, "hello")
        assert rows[1] == ("pr-body", 1, "line one")
        assert rows[2] == ("pr-body", 2, "line two")
        assert rows[3][0] == "commit/abc1234"
        assert rows[3][2] == message
        assert rules_for(rows[3][2]) == [scan.RULE_PLATE]


class TestCommand:
    def test_invalid_secret_does_not_echo_plaintext(self, tmp_path, capsys):
        code = scan.main(
            ["--repo", str(tmp_path), "--base", "HEAD"],
            environ={"PRIVACY_SCAN_TERMS": TERM, "PRIVACY_SCAN_PLATES": ""},
        )
        captured = capsys.readouterr()
        assert code == 2
        assert TERM not in captured.out
        assert TERM not in captured.err

    def test_missing_secret_warns_and_skips_only_that_rule(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        init_repo(repo)
        (repo / "note.txt").write_text("vendor " + TERM + "\n", encoding="utf-8")
        git(repo, "add", "note.txt")
        git(repo, "commit", "-m", "note")
        head = git(repo, "rev-parse", "HEAD").strip()
        base = git(repo, "rev-parse", "HEAD~1").strip()
        code = scan.main(
            ["--repo", str(repo), "--base", base, "--head", head],
            environ={},
        )
        captured = capsys.readouterr()
        assert code == 0
        assert "PRIVACY_SCAN_TERMS is not set" in captured.err
        assert "PRIVACY_SCAN_PLATES is not set" in captured.err
        assert "privacy scan passed" in captured.out
        assert scan.RULE_TERM not in captured.out
        assert TERM not in captured.out

    def test_diff_title_body_and_commit_message(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        init_repo(repo)
        base = git(repo, "rev-parse", "HEAD").strip()
        address = dotted(10, 1, 2, 3)
        (repo / "cam.txt").write_text("keep\n" + address + "\n", encoding="utf-8")
        git(repo, "add", "cam.txt")
        marker = chunk("IB", "36", "NL")
        git(repo, "commit", "-m", "record plate " + repr(marker))
        head = git(repo, "rev-parse", "HEAD").strip()
        event = {
            "pull_request": {
                "title": "use " + address,
                "body": "vendor " + TERM + "\nsecond\n",
            }
        }
        event_path = tmp_path / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        code = scan.main(
            [
                "--repo",
                str(repo),
                "--base",
                base,
                "--head",
                head,
                "--event",
                str(event_path),
            ],
            environ={
                "PRIVACY_SCAN_TERMS": scan.sha256_hex(TERM),
                "PRIVACY_SCAN_PLATES": "",
            },
        )
        captured = capsys.readouterr()
        assert code == 1
        assert TERM not in captured.out
        assert TERM not in captured.err
        assert address not in captured.out
        assert marker not in captured.out
        assert "cam.txt:2: " + scan.RULE_IP in captured.out
        assert "pr-title:1: " + scan.RULE_IP in captured.out
        assert "pr-body:1: " + scan.RULE_TERM in captured.out
        assert scan.CONFIDENTIAL_MASK in captured.out
        assert "pr-body:2:" not in captured.out
        commit_hits = [
            line
            for line in captured.out.splitlines()
            if line.startswith("commit/") and scan.RULE_PLATE in line
        ]
        assert len(commit_hits) == 1
        assert commit_hits[0].split(":")[1] == "1"
        assert "counts by rule and file:" in captured.out
        assert "privacy scan failed:" in captured.err

    def test_worktree_scans_head(self, tmp_path, capsys):
        repo = tmp_path / "repo"
        init_repo(repo)
        address = dotted(169, 254, 1, 1)
        (repo / "lan.txt").write_text(address + "\n", encoding="utf-8")
        git(repo, "add", "lan.txt")
        git(repo, "commit", "-m", "add lan")
        code = scan.main(["--repo", str(repo), "--worktree"], environ={})
        captured = capsys.readouterr()
        assert code == 1
        assert "lan.txt:1: " + scan.RULE_IP in captured.out
        assert address not in captured.out


class TestRepositoryFiles:
    def test_only_the_approved_fake_plate_is_listed(self):
        approved_name = ".github/privacy-scan-plates-approved.txt"
        allow = ROOT / ".github" / "privacy-scan-allow.txt"
        approved = ROOT / approved_name
        assert scan.load_allow_list(allow) == []
        assert scan.load_approved_plates(approved) == {"IB36NL"}
        text = approved.read_text(encoding="utf-8")
        assert "Mike must approve each entry" in text
        assert "approved by Mike 2026-10-14" in text

    def test_workflow_covers_every_branch(self):
        text = (ROOT / ".github" / "workflows" / "privacy-scan.yml").read_text(
            encoding="utf-8"
        )
        assert "pull_request:" in text
        assert '"**"' in text
        assert "PRIVACY_SCAN_TERMS" in text
        assert "PRIVACY_SCAN_PLATES" in text
        assert "privacy_scan.py" in text
        assert '--base "$BASE_SHA" --head "$HEAD_SHA"' in text

    def test_added_sources_are_clean(self):
        approved_name = ".github/privacy-scan-plates-approved.txt"
        ctx = scan.ScanContext(
            set(),
            set(),
            scan.load_allow_list(ROOT / ".github" / "privacy-scan-allow.txt"),
            scan.load_approved_plates(ROOT / approved_name),
        )
        rels = [
            ".github/scripts/privacy_scan.py",
            ".github/workflows/privacy-scan.yml",
            "scripts/hash_terms.py",
            ".github/privacy-scan-allow.txt",
            ".github/privacy-scan-plates-approved.txt",
            "tests/test_privacy_scan.py",
        ]
        findings = []
        for rel in rels:
            for lineno, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                findings.extend(scan.scan_line(rel, lineno, line, ctx))
        assert findings == []

    def test_readme_section_is_clean(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("## Privacy scan\n")
        end = readme.index("\n## ", start + 1)
        ctx = scan.ScanContext(set(), set(), [], set())
        findings = []
        for lineno, line in enumerate(readme[start:end].splitlines(), 1):
            findings.extend(scan.scan_line("README.md", lineno, line, ctx))
        assert findings == []
