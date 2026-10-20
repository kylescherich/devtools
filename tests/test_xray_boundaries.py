"""repo-xray --cycles / --boundaries (BRIEF-DEVWS-035). Synthetic repos only."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import xray_boundary_fixtures as fx


REPO_ROOT = Path(__file__).parent.parent


class BoundaryTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.target = self.tmp / "target"
        self.cwd = self.tmp / "cwd"
        self.target.mkdir()
        self.cwd.mkdir()

    def repo(self, files, pyproject=None):
        fx.write_repo(self.target, files)
        if pyproject is not None:
            (self.target / "pyproject.toml").write_text(pyproject, encoding="ascii")
        return self.target

    def run_cli(self, *args, target=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        target = self.target if target is None else target
        return subprocess.run(
            [sys.executable, "-m", "repo_xray", str(target)] + list(args),
            cwd=self.cwd, capture_output=True, text=True, env=env)

    def run_json(self, *args):
        result = self.run_cli(*args, "--format", "json")
        self.assertNotEqual(result.stdout, "", result.stderr)
        return result, json.loads(result.stdout)

    def assert_output(self, result):
        self.assertNotEqual(result.stdout + result.stderr, "")


class TestCycles(BoundaryTestCase):
    def test_two_module_cycle(self):
        self.repo(fx.CYCLE2)
        result = self.run_cli("--cycles")
        self.assert_output(result)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Cycles: 1\n", result.stdout)
        self.assertIn("1. pkg.alpha -> pkg.beta -> pkg.alpha", result.stdout)
        self.assertIn("pkg.alpha -> pkg.beta  pkg/alpha.py:2", result.stdout)
        self.assertIn("pkg.beta -> pkg.alpha  pkg/beta.py:2", result.stdout)

    def test_three_module_cycle_canonical(self):
        self.repo(fx.CYCLE3)
        result = self.run_cli("--cycles")
        self.assert_output(result)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Cycles: 1\n", result.stdout)
        self.assertIn("1. pkg.a -> pkg.b -> pkg.c -> pkg.a", result.stdout)
        self.assertNotIn("pkg.b -> pkg.c -> pkg.a -> pkg.b", result.stdout)
        self.assertNotIn("pkg.c -> pkg.a -> pkg.b -> pkg.c", result.stdout)

    def test_cycle_listed_once(self):
        self.repo(fx.TWO_CYCLES)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["modules"] for c in data["cycles"]],
                         [["pkg.x", "pkg.y"], ["pkg.a", "pkg.b", "pkg.c"]])

    def test_shortest_loop_chosen(self):
        self.repo(fx.SHORTEST)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["modules"] for c in data["cycles"]],
                         [["pkg.a", "pkg.b"], ["pkg.a", "pkg.b", "pkg.c"]])

    def test_relative_imports_resolved(self):
        self.repo(fx.RELATIVE)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(data["cycles"]), 1)
        cycle = data["cycles"][0]
        self.assertEqual(cycle["modules"],
                         ["pkg.sub.one", "pkg.sub.two", "pkg.sub.three", "pkg.top"])
        self.assertEqual([(e["path"], e["line"]) for e in cycle["edges"]],
                         [("pkg/sub/one.py", 1), ("pkg/sub/two.py", 1),
                          ("pkg/sub/three.py", 1), ("pkg/top.py", 1)])

    def test_from_import_module_vs_name(self):
        self.repo(fx.FROM_NAME)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(data["cycles"]), 1)
        edges = [(e["from"], e["to"], e["path"]) for e in data["cycles"][0]["edges"]]
        self.assertEqual(edges, [("pkg", "pkg.mod", "pkg/__init__.py"),
                                 ("pkg.mod", "pkg", "pkg/mod.py")])

    def test_type_checking_excluded_and_listed(self):
        self.repo(fx.TYPE_CHECKING)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["modules"] for c in data["cycles"]], [["pkg.d", "pkg.e"]])
        self.assertEqual(
            [(e["from"], e["to"], e["path"], e["line"]) for e in data["type_checking"]],
            [("pkg.b", "pkg.a", "pkg/b.py", 4), ("pkg.b", "pkg.c", "pkg/b.py", 6)])

    def test_type_checking_listed_in_text(self):
        self.repo(fx.TYPE_CHECKING)
        result = self.run_cli("--cycles")
        self.assert_output(result)
        self.assertIn("TYPE_CHECKING imports (not counted): 2", result.stdout)
        self.assertIn("pkg/b.py:4  pkg.b -> pkg.a", result.stdout)

    def test_function_level_import_counts(self):
        self.repo(fx.FUNCTION_LEVEL)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["modules"] for c in data["cycles"]], [["pkg.a", "pkg.b"]])
        self.assertEqual(data["cycles"][0]["edges"][0]["line"], 2)

    def test_try_import_counts(self):
        self.repo(fx.TRY_IMPORT)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["modules"] for c in data["cycles"]], [["pkg.a", "pkg.b"]])

    def test_stdlib_name_not_internal(self):
        self.repo(fx.STDLIB_NAME)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c["modules"] for c in data["cycles"]], [["a", "b"]])

    def test_no_cycles_exit_0(self):
        self.repo(fx.NO_CYCLES)
        result = self.run_cli("--cycles")
        self.assert_output(result)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Cycles: 0\n  none\n", result.stdout)

    def test_markdown_format(self):
        self.repo(fx.CYCLE2)
        result = self.run_cli("--cycles", "--format", "md")
        self.assert_output(result)
        self.assertEqual(result.returncode, 1)
        self.assertIn("## Cycles (1)", result.stdout)
        self.assertIn("1. pkg.alpha -> pkg.beta -> pkg.alpha", result.stdout)

    def test_json_schema(self):
        self.repo(fx.NO_CYCLES)
        result, data = self.run_json("--cycles")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["schema"], "repo-xray-boundaries/1")
        self.assertEqual(data["cycles"], [])
        self.assertIsNone(data["independent"])


class TestIndependent(BoundaryTestCase):
    def test_violation_from_flag(self):
        self.repo(fx.VIOLATION)
        result = self.run_cli("--boundaries", "--independent", "pkg_a,pkg_b")
        self.assert_output(result)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Violations: 1\n", result.stdout)
        self.assertIn("pkg_a/core.py:2  pkg_a.core -> pkg_b.api", result.stdout)

    def test_clean_exit_0(self):
        self.repo(fx.CLEAN)
        result, data = self.run_json("--boundaries", "--independent", "pkg_a,pkg_b")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["independent"]["violations"], [])

    def test_unlisted_may_import_listed(self):
        self.repo(fx.UNLISTED)
        result, data = self.run_json("--boundaries", "--independent", "pkg_a,pkg_b")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["independent"]["violations"], [])

    def test_package_may_import_itself(self):
        self.repo(fx.SELF_IMPORT)
        result, data = self.run_json("--boundaries", "--independent", "pkg_a,pkg_b")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["independent"]["violations"], [])

    def test_type_checking_not_a_violation(self):
        self.repo(fx.TYPE_CHECKING_RULE)
        result, data = self.run_json("--boundaries", "--independent", "pkg_a,pkg_b")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["independent"]["violations"], [])

    def test_config_from_pyproject(self):
        self.repo(fx.VIOLATION, pyproject=fx.PYPROJECT_OK)
        result, data = self.run_json("--boundaries")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data["independent"]["packages"], ["pkg_a", "pkg_b"])
        self.assertEqual([(v["from"], v["to"], v["path"], v["line"])
                          for v in data["independent"]["violations"]],
                         [("pkg_a.core", "pkg_b.api", "pkg_a/core.py", 2)])

    def test_flag_overrides_pyproject(self):
        self.repo(fx.VIOLATION, pyproject=fx.PYPROJECT_OTHER)
        result, data = self.run_json("--boundaries", "--independent", "pkg_a,pkg_b")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data["independent"]["packages"], ["pkg_a", "pkg_b"])
        self.assertEqual(len(data["independent"]["violations"]), 1)

    def test_not_found_listed(self):
        self.repo(fx.CLEAN)
        result, data = self.run_json("--boundaries", "--independent", "pkg_a,pkg_b,nopkg")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["independent"]["not_found"], ["nopkg"])

    def test_malformed_pyproject_exit_2_no_content(self):
        self.repo(fx.CLEAN, pyproject=fx.PYPROJECT_MALFORMED)
        result = self.run_cli("--boundaries")
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("pyproject.toml", result.stderr)
        self.assertNotIn(fx.SENTINEL, result.stdout + result.stderr)
        self.assertNotIn("line 1", result.stderr)
        self.assertNotIn("independent =", result.stderr)

    def test_bad_value_exit_2_no_content(self):
        self.repo(fx.CLEAN, pyproject=fx.PYPROJECT_BAD_VALUE)
        result = self.run_cli("--boundaries")
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("pyproject.toml", result.stderr)
        self.assertNotIn(fx.SENTINEL, result.stdout + result.stderr)

    def test_no_config_exit_2(self):
        self.repo(fx.CLEAN)
        result = self.run_cli("--boundaries")
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--independent", result.stderr)

    def test_nested_names_usage_error(self):
        self.repo(fx.CLEAN)
        result = self.run_cli("--boundaries", "--independent", "pkg_a,pkg_a.core")
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)


class TestCheckModeCli(BoundaryTestCase):
    def test_independent_without_boundaries_exit_2(self):
        self.repo(fx.CLEAN)
        result = self.run_cli("--independent", "pkg_a,pkg_b")
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_html_format_in_check_mode_exit_2(self):
        self.repo(fx.CYCLE2)
        result = self.run_cli("--cycles", "--format", "html")
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_output_in_check_mode_exit_2(self):
        self.repo(fx.CYCLE2)
        result = self.run_cli("--cycles", "--output", str(self.cwd / "out.txt"))
        self.assert_output(result)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_unreadable_target_exit_4(self):
        result = self.run_cli("--cycles", target=self.tmp / "missing")
        self.assert_output(result)
        self.assertEqual(result.returncode, 4, result.stdout)
        self.assertIn("repo-xray:", result.stderr)

    def test_report_mode_ignores_tool_table(self):
        self.repo(fx.CYCLE2, pyproject=fx.PYPROJECT_MALFORMED)
        result = self.run_cli()
        self.assert_output(result)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.cwd / "target-xray.html"
        self.assertTrue(out.is_file())
        self.assertNotEqual(out.read_text(encoding="utf-8"), "")

    def test_target_unchanged(self):
        self.repo(fx.VIOLATION, pyproject=fx.PYPROJECT_OK)

        def digest():
            items = []
            for path in sorted(self.target.rglob("*")):
                if path.is_file():
                    items.append((path.relative_to(self.target).as_posix(),
                                  hashlib.sha256(path.read_bytes()).hexdigest()))
            return items

        before = digest()
        self.assertNotEqual(before, [])
        result = self.run_cli("--cycles", "--boundaries")
        self.assert_output(result)
        self.assertEqual(digest(), before)


if __name__ == "__main__":
    unittest.main()
