"""Tests for repo_xray.analyzer."""

import json
import os
import shutil
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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


    def test_imports_in_functions_and_classes(self):
        """Test that imports inside functions and classes are captured."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create pkg/__init__.py
            pkg_dir = tmp_path / 'pkg'
            pkg_dir.mkdir()
            (pkg_dir / '__init__.py').write_text('')
            
            # Create pkg/a.py
            (pkg_dir / 'a.py').write_text('def helper(): pass\n')
            
            # Create pkg/b.py with imports inside function and class
            (pkg_dir / 'b.py').write_text(
                'def f():\n'
                '    from . import a\n'
                'class C:\n'
                '    def m(self):\n'
                '        import pkg.a\n'
            )
            
            result = analyze(tmp_path)
            
            # Assert result is non-empty
            self.assertTrue(result)
            self.assertGreater(result["files_scanned"], 0)
            
            # Check that pkg.b has an internal import of pkg.a
            self.assertIn("pkg.b", result["modules"])
            pkg_b = result["modules"]["pkg.b"]
            
            # Find the import with lineno 2
            found_import = None
            for imp in pkg_b["imports"]:
                if imp["lineno"] == 2:
                    found_import = imp
                    break
            
            self.assertIsNotNone(found_import)
            self.assertEqual(found_import["target"], "pkg.a")
            self.assertTrue(found_import["internal"])
    
    def test_skipped_symlink_large_and_syntax_error(self):
        """Test that symlinks, large files, and syntax errors are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create pkg/__init__.py
            pkg_dir = tmp_path / 'pkg'
            pkg_dir.mkdir()
            (pkg_dir / '__init__.py').write_text('')
            
            # Create pkg/ok.py
            (pkg_dir / 'ok.py').write_text('def ok(): pass\n')
            
            # Create pkg/big.py (300 bytes of comments)
            big_content = '# ' + 'x' * 297 + '\n'
            (pkg_dir / 'big.py').write_text(big_content)
            
            # Create pkg/bad.py with syntax error
            (pkg_dir / 'bad.py').write_text('def (:\n')
            
            # Try to create symlink (skip if not supported)
            symlink_created = False
            try:
                link_file = pkg_dir / 'link.py'
                os.symlink(pkg_dir / 'ok.py', link_file)
                symlink_created = True
            except (OSError, NotImplementedError):
                pass
            
            # Spy on open() to track which files are opened
            opened_files = []
            original_open = open
            
            def spy_open(file, *args, **kwargs):
                opened_files.append(str(file))
                return original_open(file, *args, **kwargs)
            
            with patch('builtins.open', spy_open):
                result = analyze(tmp_path, max_file_bytes=200)
            
            # Assert result is non-empty
            self.assertTrue(result)
            self.assertGreater(result["files_scanned"], 0)
            
            # Check skipped list
            skipped_dict = {item['path']: item['reason'] for item in result['skipped']}
            
            self.assertIn('pkg/big.py', skipped_dict)
            self.assertEqual(skipped_dict['pkg/big.py'], 'too_large')
            
            self.assertIn('pkg/bad.py', skipped_dict)
            self.assertEqual(skipped_dict['pkg/bad.py'], 'syntax_error')
            
            if symlink_created:
                self.assertIn('pkg/link.py', skipped_dict)
                self.assertEqual(skipped_dict['pkg/link.py'], 'symlink')
            
            # Check modules
            self.assertIn('pkg.ok', result['modules'])
            self.assertNotIn('pkg.big', result['modules'])
            self.assertNotIn('pkg.bad', result['modules'])
            self.assertNotIn('pkg.link', result['modules'])
            
            # Check that big.py was never opened
            big_py_path = str(pkg_dir / 'big.py')
            self.assertNotIn(big_py_path, opened_files)
    
    def test_reparse_point_directory_skipped(self):
        """Test that reparse point directories are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create pkg/__init__.py and pkg/ok.py
            pkg_dir = tmp_path / 'pkg'
            pkg_dir.mkdir()
            (pkg_dir / '__init__.py').write_text('')
            (pkg_dir / 'ok.py').write_text('def ok(): pass\n')
            
            # Create junc/x.py
            junc_dir = tmp_path / 'junc'
            junc_dir.mkdir()
            (junc_dir / 'x.py').write_text('def x(): pass\n')
            
            # Mock os.lstat to return reparse point for "junc"
            original_lstat = os.lstat
            
            def mock_lstat(path):
                result = original_lstat(path)
                if str(path).endswith('junc'):
                    # Return a namespace with reparse point attribute
                    return types.SimpleNamespace(
                        st_mode=stat.S_IFDIR | 0o755,
                        st_size=0,
                        st_file_attributes=getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
                    )
                return result
            
            with patch('repo_xray.analyzer.os.lstat', mock_lstat):
                result = analyze(tmp_path)
            
            # Assert result is non-empty
            self.assertTrue(result)
            self.assertGreater(result["files_scanned"], 0)
            
            # Check that x and junc.x are not modules
            self.assertNotIn('x', result['modules'])
            self.assertNotIn('junc.x', result['modules'])
            
            # Check skipped list contains junc
            skipped_dict = {item['path']: item['reason'] for item in result['skipped']}
            self.assertIn('junc', skipped_dict)
            self.assertEqual(skipped_dict['junc'], 'reparse_point')
            
            # Check pkg.ok is a module
            self.assertIn('pkg.ok', result['modules'])


    def test_try_import_not_counted_as_edge(self):
        """Test that imports inside try blocks are not counted as edges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create pkg/__init__.py
            pkg_dir = tmp_path / 'pkg'
            pkg_dir.mkdir()
            (pkg_dir / '__init__.py').write_text('')
            
            # Create pkg/x.py
            (pkg_dir / 'x.py').write_text('def y(): pass\n')
            
            # Create pkg/m.py with try import and regular import
            (pkg_dir / 'm.py').write_text(
                'try:\n'
                '    import pkg.x\n'
                'except ImportError:\n'
                '    pass\n'
                'from pkg.x import y\n'
            )
            
            result = analyze(tmp_path)
            
            # Assert result is non-empty
            self.assertTrue(result)
            self.assertGreater(result["files_scanned"], 0)
            
            # Check that pkg.m has exactly one import with target pkg.x at lineno 5
            self.assertIn("pkg.m", result["modules"])
            pkg_m = result["modules"]["pkg.m"]
            
            pkg_x_imports = [imp for imp in pkg_m["imports"] if imp["target"] == "pkg.x"]
            self.assertEqual(len(pkg_x_imports), 1)
            self.assertTrue(pkg_x_imports[0]["internal"])
            self.assertEqual(pkg_x_imports[0]["lineno"], 5)
            
            # Check try entries for pkg.m
            try_entries = [(u["detail"], u["lineno"]) for u in result["unresolved"] 
                          if u["module"] == "pkg.m" and u["kind"] == "try"]
            self.assertEqual(try_entries, [("pkg.x", 2)])
    
    def test_nested_try_single_entry(self):
        """Test that nested try blocks produce single entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            
            # Create pkg/__init__.py
            pkg_dir = tmp_path / 'pkg'
            pkg_dir.mkdir()
            (pkg_dir / '__init__.py').write_text('')
            
            # Create pkg/x.py
            (pkg_dir / 'x.py').write_text('def y(): pass\n')
            
            # Create pkg/n.py with nested try blocks
            (pkg_dir / 'n.py').write_text(
                'try:\n'
                '    try:\n'
                '        from pkg.x import y\n'
                '    except ImportError:\n'
                '        y = None\n'
                'except Exception:\n'
                '    y = None\n'
            )
            
            result = analyze(tmp_path)
            
            # Assert result is non-empty
            self.assertTrue(result)
            self.assertGreater(result["files_scanned"], 0)
            
            # Check that pkg.n has no import with target pkg.x
            self.assertIn("pkg.n", result["modules"])
            pkg_n = result["modules"]["pkg.n"]
            
            pkg_x_imports = [imp for imp in pkg_n["imports"] if imp["target"] == "pkg.x"]
            self.assertEqual(len(pkg_x_imports), 0)
            
            # Check exactly one try entry for pkg.n with detail "pkg.x" and lineno 3
            try_entries = [(u["detail"], u["lineno"]) for u in result["unresolved"] 
                          if u["module"] == "pkg.n" and u["kind"] == "try"]
            self.assertEqual(try_entries, [("pkg.x", 3)])


if __name__ == '__main__':
    unittest.main()
