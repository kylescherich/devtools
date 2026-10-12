import ast
import unittest
from pathlib import Path


class TestAskdataIsolation(unittest.TestCase):
    def test_no_cross_imports(self):
        """Verify askdata and repo_xray don't import each other."""
        repo_root = Path(__file__).parent.parent
        
        askdata_files = list((repo_root / "askdata").rglob("*.py"))
        repo_xray_files = list((repo_root / "repo_xray").rglob("*.py"))
        
        # Ensure we found files
        self.assertGreater(len(askdata_files), 0, "No askdata files found")
        self.assertGreater(len(repo_xray_files), 0, "No repo_xray files found")
        
        # Check askdata doesn't import repo_xray
        for py_file in askdata_files:
            with open(py_file, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=str(py_file))
                except SyntaxError:
                    continue
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn("repo_xray", alias.name,
                                           f"{py_file} imports repo_xray")
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            self.assertNotIn("repo_xray", node.module,
                                           f"{py_file} imports from repo_xray")
        
        # Check repo_xray doesn't import askdata
        for py_file in repo_xray_files:
            with open(py_file, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=str(py_file))
                except SyntaxError:
                    continue
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn("askdata", alias.name,
                                           f"{py_file} imports askdata")
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            self.assertNotIn("askdata", node.module,
                                           f"{py_file} imports from askdata")
