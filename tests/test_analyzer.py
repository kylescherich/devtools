"""Tests for repo_xray.analyzer."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from repo_xray.analyzer import analyze, TargetError, TooManyFiles


class TestAnalyzer(unittest.TestCase):
    """Test the analyzer module."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.fixture_path = Path(__file__).parent / 'fixtures' / 'sample_repo'
    
    def test_analyze_fixture(self):
        """Test analyzing the sample fixture repository."""
        result = analyze(self.fixture_path)
        
        # Assert result is non-empty
        self.assertTrue(result)
        self.assertGreater(result["files_scanned"], 0)
        
        # Check Engine class with methods
        core_module = result["modules"].get("samplepkg.core")
        self.assertIsNotNone(core_module)
        
        engine_class = None
        for cls in core_module["classes"]:
            if cls["name"] == "Engine":
                engine_class = cls
                break
        
        self.assertIsNotNone(engine_class)
        self.assertEqual(engine_class["docstring"], "Runs things.")
        
        # Check methods
        start_method = None
        stop_method = None
        for method in engine_class["methods"]:
            if method["name"] == "start":
                start_method = method
            elif method["name"] == "stop":
                stop_method = method
        
        self.assertIsNotNone(start_method)
        self.assertEqual(start_method["signature"], "(self, speed=…)")
        
        self.assertIsNotNone(stop_method)
        self.assertEqual(stop_method["signature"], "(self)")
    
    def test_build_signature(self):
        """Test build function signature."""
        result = analyze(self.fixture_path)
        self.assertTrue(result)
        
        core_module = result["modules"].get("samplepkg.core")
        self.assertIsNotNone(core_module)
        
        build_func = None
        for func in core_module["functions"]:
            if func["name"] == "build":
                build_func = func
                break
        
        self.assertIsNotNone(build_func)
        self.assertEqual(build_func["signature"], "(name, *args, retries=…, **kw)")
        self.assertEqual(build_func["docstring"], "Build it.")
    
    def test_imports(self):
        """Test import detection."""
        result = analyze(self.fixture_path)
        self.assertTrue(result)
        
        core_module = result["modules"].get("samplepkg.core")
        self.assertIsNotNone(core_module)
        
        # Check for samplepkg.util import (internal)
        util_import = None
        os_import = None
        for imp in core_module["imports"]:
            if imp["target"] == "samplepkg.util":
                util_import = imp
            elif imp["target"] == "os":
                os_import = imp
            # No target should end in ".helper"
            self.assertFalse(imp["target"].endswith(".helper"))
        
        self.assertIsNotNone(util_import)
        self.assertTrue(util_import["internal"])
        
        self.assertIsNotNone(os_import)
        self.assertFalse(os_import["internal"])
        
        # Every import with internal=True must have target in model["modules"]
        for imp in core_module["imports"]:
            if imp["internal"]:
                self.assertIn(imp["target"], result["modules"])
    
    def test_unresolved_imports(self):
        """Test unresolved import detection."""
        result = analyze(self.fixture_path)
        self.assertTrue(result)
        
        # Check for various kinds of unresolved imports
        kinds = {item["kind"] for item in result["unresolved"]}
        
        self.assertIn("star", kinds)
        self.assertIn("importlib", kinds)
        self.assertIn("__import__", kinds)
        self.assertIn("try", kinds)
        self.assertIn("relative", kinds)
    
    def test_no_secrets_in_output(self):
        """Test that secrets and config files are not included."""
        result = analyze(self.fixture_path)
        self.assertTrue(result)
        
        # Convert to JSON and check for forbidden strings
        result_json = json.dumps(result)
        
        self.assertNotIn("samplepkg.plugins", result_json)
        self.assertNotIn("do-not-read", result_json)
        self.assertNotIn("FAKE_TOKEN", result_json)
        self.assertNotIn(".env", result_json)
    
    def test_entry_points(self):
        """Test entry point detection."""
        result = analyze(self.fixture_path)
        self.assertTrue(result)
        
        # Check for console script
        console_script = None
        main_guard = None
        
        for ep in result["entry_points"]:
            if ep["kind"] == "console_script" and ep["name"] == "sample-run":
                console_script = ep
            elif ep["kind"] == "main_guard" and ep["module"] == "samplepkg.cli":
                main_guard = ep
        
        self.assertIsNotNone(console_script)
        self.assertEqual(console_script["module"], "samplepkg.cli")
        
        self.assertIsNotNone(main_guard)
    
    def test_test_inference(self):
        """Test test file detection and target inference."""
        result = analyze(self.fixture_path)
        self.assertTrue(result)
        
        # Find test_core test - should be "test_core" not "tests.test_core"
        test_core = None
        for test in result["tests"]:
            if test["module"] == "test_core":
                test_core = test
                break
        
        self.assertIsNotNone(test_core)
        self.assertTrue(test_core["inferred"])
        
        # Check inferred targets
        self.assertIn("samplepkg.core", test_core["inferred_targets"])
        self.assertIn("samplepkg.util", test_core["inferred_targets"])
    
    def test_symlink_not_scanned(self):
        """Test that symlinked files are not scanned."""
        # Create a temporary directory with a copy of the fixture
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Copy fixture
            shutil.copytree(self.fixture_path, tmp_path / 'repo')
            repo_path = tmp_path / 'repo'
            
            # Create a symlink to a Python file
            target_file = repo_path / 'samplepkg' / 'core.py'
            link_file = repo_path / 'samplepkg' / 'linked.py'
            os.symlink(target_file, link_file)
            
            result = analyze(repo_path)
            self.assertTrue(result)
            
            # Check that linked.py is not in modules
            self.assertNotIn("samplepkg.linked", result["modules"])
    
    def test_max_files_limit(self):
        """Test that TooManyFiles is raised when limit exceeded."""
        with self.assertRaises(TooManyFiles):
            analyze(self.fixture_path, max_files=2)
    
    def test_missing_path_raises_error(self):
        """Test that TargetError is raised for missing path."""
        with self.assertRaises(TargetError):
            analyze("/nonexistent/path/to/repo")
    
    def test_devtools_repo(self):
        """Test analyzing the devtools repository itself."""
        # Two directories above this test file
        devtools_root = Path(__file__).parent.parent
        result = analyze(devtools_root)
        
        # Assert result is non-empty
        self.assertTrue(result)
        self.assertGreater(result["files_scanned"], 0)
        
        # Should not have samplepkg.core (that's in fixtures)
        self.assertIn("samplepkg.core", result["modules"])
        
        # Should have repo_xray.analyzer
        self.assertIn("repo_xray.analyzer", result["modules"])
        
        # No module name should contain "fixtures"
        for module_name in result["modules"]:
            self.assertNotIn("fixtures", module_name)
        
        # Should have unresolved relative import in samplepkg.broken
        found_broken_relative = False
        for unresolved in result["unresolved"]:
            if unresolved["module"] == "samplepkg.broken" and unresolved["kind"] == "relative":
                found_broken_relative = True
                break
        self.assertTrue(found_broken_relative)
    
    def test_collision_resolution(self):
        """Test that files with same stem in different dirs get unique names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create a/x.py and b/x.py (no __init__.py)
            (tmp_path / 'a').mkdir()
            (tmp_path / 'a' / 'x.py').write_text('def foo(): pass\n')
            
            (tmp_path / 'b').mkdir()
            (tmp_path / 'b' / 'x.py').write_text('def bar(): pass\n')
            
            result = analyze(tmp_path)
            
            # Assert result is non-empty
            self.assertTrue(result)
            self.assertGreater(result["files_scanned"], 0)
            
            # Should have both a.x and b.x
            self.assertIn("a.x", result["modules"])
            self.assertIn("b.x", result["modules"])


if __name__ == '__main__':
    unittest.main()
