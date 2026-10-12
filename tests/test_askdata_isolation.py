import ast
import unittest
from pathlib import Path

PACKAGES = ("repo_xray", "askdata", "jsondiff")


def _imported_modules(py_file):
    """Return every module name imported anywhere in py_file."""
    with open(py_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(py_file))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestAskdataIsolation(unittest.TestCase):
    def test_no_cross_imports(self):
        """Verify repo_xray, askdata and jsondiff never import each other."""
        repo_root = Path(__file__).parent.parent

        for package in PACKAGES:
            files = list((repo_root / package).rglob("*.py"))
            self.assertGreater(len(files), 0, f"No {package} files found")
            others = [p for p in PACKAGES if p != package]
            for py_file in files:
                for module in _imported_modules(py_file):
                    for other in others:
                        self.assertNotIn(other, module,
                                         f"{py_file} imports {other}")
