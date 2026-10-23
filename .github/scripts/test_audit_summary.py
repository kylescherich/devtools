"""Tests for audit_summary.py (BRIEF-DEVWS-046, DEC-490).

Hand-written pip-audit JSON fixtures, written to temp dirs. Every test asserts
non-empty output before checking content (DEC-071).
Run: python -I -m unittest discover -s .github/scripts -p 'test_*.py' -v
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import audit_summary

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "audit_summary.py")
TODAY = "2026-10-22"

IGNORE_HEADER = (
    "# pip-audit ignore list (BRIEF-DEVWS-046, DEC-490). One entry per line:\n"
    "# <advisory id> | <reason> | <expires YYYY-MM-DD> | <DEC-NNN>\n"
    "# Phase 1 (report-only): entries are shown, never enforced. Phase 2 needs a Hub DEC.\n"
)

NO_VULNS = {
    "dependencies": [
        {"name": "certifi", "version": "2026.7.1", "vulns": []},
        {"name": "idna", "version": "3.10", "vulns": []},
    ],
    "fixes": [],
}

TWO_PKGS_THREE_ADVISORIES = {
    "dependencies": [
        {
            "name": "jinja2",
            "version": "2.10",
            "vulns": [
                {
                    "id": "PYSEC-2019-217",
                    "fix_versions": ["2.10.1"],
                    "aliases": ["CVE-2019-10906", "GHSA-462w-v97r-4m45"],
                    "description": "sandbox escape",
                },
                {
                    "id": "GHSA-h5c8-rqwp-cp95",
                    "fix_versions": ["3.1.3"],
                    "aliases": ["CVE-2024-22195"],
                    "description": "xss",
                },
            ],
        },
        {
            "name": "oldlib",
            "version": "0.1",
            "vulns": [
                {
                    "id": "PYSEC-2099-1",
                    "fix_versions": [],
                    "aliases": [],
                    "description": "no fix yet",
                },
            ],
        },
        {"name": "idna", "version": "3.10", "vulns": []},
    ],
    "fixes": [],
}

WITH_SKIPPED = {
    "dependencies": [
        {"name": "idna", "version": "3.10", "vulns": []},
        {
            "name": "privpkg",
            "skip_reason": "Dependency not found on PyPI and could not be audited: privpkg (1.0)",
        },
    ],
    "fixes": [],
}


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.ignore = self.write("audit-ignore.txt", IGNORE_HEADER)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def write_json(self, data, name="audit.json"):
        return self.write(name, json.dumps(data))

    def run_main(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = audit_summary.main(list(argv))
        out = buf.getvalue()
        self.assertTrue(out.strip(), "formatter printed nothing")
        return code, out


class TestHeader(Case):
    def test_header_fields(self):
        code, out = self.run_main(
            self.write_json(NO_VULNS), self.ignore, "--exit-code", "0",
            "--pip-audit-version", "pip-audit 2.10.1", "--service", "pypi",
        )
        self.assertEqual(code, 0)
        self.assertIn("pip-audit 2.10.1", out)
        self.assertIn("service: pypi", out)
        self.assertIn("Phase 1: report-only (DEC-490)", out)


class TestNoVulns(Case):
    def test_no_vulns(self):
        code, out = self.run_main(self.write_json(NO_VULNS), self.ignore, "--exit-code", "0")
        self.assertEqual(code, 0)
        self.assertIn("packages audited: 2", out)
        self.assertIn("vulnerable: 0", out)
        self.assertIn("advisories: 0", out)
        self.assertIn("skipped: 0", out)
        self.assertIn("No known vulnerabilities found.", out)
        self.assertNotIn("could not run", out)


class TestFindings(Case):
    def test_two_packages_three_advisories(self):
        code, out = self.run_main(
            self.write_json(TWO_PKGS_THREE_ADVISORIES), self.ignore, "--exit-code", "1"
        )
        self.assertEqual(code, 0)
        self.assertIn("packages audited: 3", out)
        self.assertIn("vulnerable: 2", out)
        self.assertIn("advisories: 3", out)
        self.assertIn("| package | installed | advisory id | aliases | fixed in |", out)
        self.assertIn(
            "| jinja2 | 2.10 | PYSEC-2019-217 | CVE-2019-10906, GHSA-462w-v97r-4m45 | 2.10.1 |", out
        )
        self.assertIn("| jinja2 | 2.10 | GHSA-h5c8-rqwp-cp95 | CVE-2024-22195 | 3.1.3 |", out)
        self.assertNotIn("No known vulnerabilities found.", out)
        self.assertNotIn("could not run", out)

    def test_no_fix_version_is_none(self):
        code, out = self.run_main(
            self.write_json(TWO_PKGS_THREE_ADVISORIES), self.ignore, "--exit-code", "1"
        )
        self.assertEqual(code, 0)
        self.assertIn("| oldlib | 0.1 | PYSEC-2099-1 | none | none |", out)

    def test_pipe_in_value_is_escaped(self):
        data = {"dependencies": [{"name": "a|b", "version": "1", "vulns": [
            {"id": "X-1", "fix_versions": ["2"], "aliases": []}]}]}
        code, out = self.run_main(self.write_json(data), self.ignore, "--exit-code", "1")
        self.assertEqual(code, 0)
        self.assertIn("| a\\|b | 1 | X-1 | none | 2 |", out)


DUPLICATED = {
    "dependencies": [
        {
            "name": "urllib3",
            "version": "1.24.1",
            "vulns": [
                {"id": "PYSEC-2019-133", "fix_versions": ["1.24.2"], "aliases": ["CVE-2019-11324"]},
                {"id": "GHSA-mh33-7rrq-662w", "fix_versions": ["1.24.2"], "aliases": []},
                {"id": "PYSEC-2019-133", "fix_versions": ["1.24.2"], "aliases": ["GHSA-mh33-7rrq-662w"]},
            ],
        },
        {
            "name": "urllib3",
            "version": "1.24.1",
            "vulns": [
                {"id": "GHSA-mh33-7rrq-662w", "fix_versions": ["1.24.3"], "aliases": ["CVE-2019-11324"]},
            ],
        },
    ],
    "fixes": [],
}


class TestDuplicates(Case):
    def test_each_advisory_once_and_counted_once(self):
        code, out = self.run_main(self.write_json(DUPLICATED), self.ignore, "--exit-code", "1")
        self.assertEqual(code, 0)
        self.assertIn("packages audited: 1", out)
        self.assertIn("vulnerable: 1", out)
        self.assertIn("advisories: 2", out)
        self.assertEqual(out.count("| urllib3 | 1.24.1 | PYSEC-2019-133 |"), 1)
        self.assertEqual(out.count("| urllib3 | 1.24.1 | GHSA-mh33-7rrq-662w |"), 1)

    def test_duplicates_merge_aliases_and_fix_versions(self):
        code, out = self.run_main(self.write_json(DUPLICATED), self.ignore, "--exit-code", "1")
        self.assertEqual(code, 0)
        self.assertIn(
            "| urllib3 | 1.24.1 | PYSEC-2019-133 | CVE-2019-11324, GHSA-mh33-7rrq-662w | 1.24.2 |", out
        )
        self.assertIn("| urllib3 | 1.24.1 | GHSA-mh33-7rrq-662w | CVE-2019-11324 | 1.24.2, 1.24.3 |", out)


class TestSkipped(Case):
    def test_skipped_listed(self):
        code, out = self.run_main(self.write_json(WITH_SKIPPED), self.ignore, "--exit-code", "0")
        self.assertEqual(code, 0)
        self.assertIn("packages audited: 1", out)
        self.assertIn("skipped: 1", out)
        self.assertIn("## Skipped packages", out)
        self.assertIn("- privpkg: Dependency not found on PyPI", out)


class TestCouldNotRun(Case):
    def test_missing_json(self):
        code, out = self.run_main(os.path.join(self.tmp, "nope.json"), self.ignore, "--exit-code", "2")
        self.assertEqual(code, 0)
        self.assertIn("audit could not run (exit 2)", out)
        self.assertIn("Phase 1: report-only (DEC-490)", out)

    def test_empty_json(self):
        code, out = self.run_main(self.write("audit.json", ""), self.ignore, "--exit-code", "1")
        self.assertEqual(code, 0)
        self.assertIn("audit could not run (exit 1)", out)

    def test_invalid_json(self):
        code, out = self.run_main(self.write("audit.json", "{not json"), self.ignore)
        self.assertEqual(code, 0)
        self.assertIn("audit could not run (exit unknown)", out)

    def test_wrong_shape_json(self):
        code, out = self.run_main(self.write("audit.json", "[1, 2]"), self.ignore, "--exit-code", "0")
        self.assertEqual(code, 0)
        self.assertIn("audit could not run (exit 0)", out)

    def test_error_exit_with_valid_json(self):
        code, out = self.run_main(self.write_json(NO_VULNS), self.ignore, "--exit-code", "2")
        self.assertEqual(code, 0)
        self.assertIn("audit could not run (exit 2)", out)
        self.assertNotIn("No known vulnerabilities found.", out)


class TestEmptySet(Case):
    def test_empty_set(self):
        code, out = self.run_main(os.path.join(self.tmp, "nope.json"), self.ignore, "--empty-set")
        self.assertEqual(code, 0)
        self.assertIn("audited set is empty (project has no dependencies); nothing to audit, not an error", out)
        self.assertIn("packages audited: 0", out)
        self.assertNotIn("could not run", out)


class TestIgnoreList(Case):
    def ignore_with(self, *lines):
        return self.write("audit-ignore.txt", IGNORE_HEADER + "".join(l + "\n" for l in lines))

    def test_header_only_has_no_entries(self):
        code, out = self.run_main(self.write_json(NO_VULNS), self.ignore, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertIn("## audit-ignore.txt", out)
        self.assertIn("- no entries", out)
        self.assertNotIn("warning", out)

    def test_valid_entry(self):
        ign = self.ignore_with("PYSEC-2019-217 | sandbox not used | 2027-01-31 | DEC-500")
        code, out = self.run_main(self.write_json(NO_VULNS), ign, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertIn("- PYSEC-2019-217 | sandbox not used | expires 2027-01-31 | DEC-500", out)
        self.assertNotIn("warning", out)

    def test_malformed_entry(self):
        ign = self.ignore_with("GHSA-xxxx-yyyy-zzzz | no expiry here")
        code, out = self.run_main(self.write_json(NO_VULNS), ign, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertIn("**warning: malformed**", out)
        self.assertIn("line 4", out)

    def test_bad_date_and_bad_dec_are_malformed(self):
        ign = self.ignore_with(
            "PYSEC-1 | reason | 2027-13-01 | DEC-500",
            "PYSEC-2 | reason | 2027-01-01 | DEC500",
        )
        code, out = self.run_main(self.write_json(NO_VULNS), ign, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertEqual(out.count("**warning: malformed**"), 2)

    def test_expired_entry(self):
        ign = self.ignore_with("PYSEC-2019-217 | old waiver | 2026-10-21 | DEC-480")
        code, out = self.run_main(self.write_json(NO_VULNS), ign, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertIn("**warning: expired**", out)
        self.assertIn("PYSEC-2019-217", out)

    def test_expires_today_is_not_expired(self):
        ign = self.ignore_with("PYSEC-2019-217 | waiver | 2026-10-22 | DEC-480")
        code, out = self.run_main(self.write_json(NO_VULNS), ign, "--today", TODAY)
        self.assertEqual(code, 0)
        self.assertNotIn("warning", out)

    def test_missing_ignore_file_is_warning(self):
        code, out = self.run_main(
            self.write_json(NO_VULNS), os.path.join(self.tmp, "absent.txt"), "--today", TODAY
        )
        self.assertEqual(code, 0)
        self.assertIn("**warning: ignore file not found**", out)


class TestExitCodeAlwaysZero(Case):
    def test_subprocess_exit_zero_for_every_case(self):
        cases = [
            [self.write_json(NO_VULNS, "a.json"), self.ignore, "--exit-code", "0"],
            [self.write_json(TWO_PKGS_THREE_ADVISORIES, "b.json"), self.ignore, "--exit-code", "1"],
            [self.write_json(WITH_SKIPPED, "c.json"), self.ignore, "--exit-code", "0"],
            [os.path.join(self.tmp, "missing.json"), self.ignore, "--exit-code", "2"],
            [self.write("d.json", "{bad"), self.write("i.txt", "garbage line\n"), "--today", TODAY],
            [self.write("e.json", ""), os.path.join(self.tmp, "absent.txt")],
            [self.write_json(NO_VULNS, "f.json"), self.ignore, "--exit-code", "not-a-number"],
        ]
        for argv in cases:
            with self.subTest(argv=argv[0]):
                proc = subprocess.run(
                    [sys.executable, "-I", SCRIPT, *argv],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertTrue(proc.stdout.strip(), "formatter printed nothing")
                self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
