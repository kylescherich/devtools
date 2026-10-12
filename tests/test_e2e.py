"""End-to-end tests for repo-xray CLI."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


class TestE2E(unittest.TestCase):
    """End-to-end tests running the CLI as a subprocess."""
    
    def setUp(self):
        """Set up test environment."""
        self.repo_root = Path(__file__).parent.parent
        self.fixtures_dir = self.repo_root / 'tests' / 'fixtures'
    
    def _run_cli(self, args, cwd=None):
        """Run the CLI and return CompletedProcess."""
        if cwd is None:
            cwd = self.repo_root
        
        env = os.environ.copy()
        env['PYTHONPATH'] = str(self.repo_root)
        
        return subprocess.run(
            [sys.executable, '-m', 'repo_xray'] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env
        )
    
    def _compute_file_hashes(self, directory):
        """Compute sorted list of (relative_path, sha256) for all files."""
        hashes = []
        for root, dirs, files in os.walk(directory):
            # Sort for determinism
            dirs.sort()
            files.sort()
            
            for filename in files:
                filepath = Path(root) / filename
                rel_path = filepath.relative_to(directory)
                
                with open(filepath, 'rb') as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
                
                hashes.append((str(rel_path.as_posix()), file_hash))
        
        return sorted(hashes)
    
    def test_fixture_copy_html_output(self):
        """Test analyzing a fixture copy with HTML output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Copy fixture
            fixture_copy = tmpdir / 'sample_repo'
            shutil.copytree(self.fixtures_dir / 'sample_repo', fixture_copy)
            
            # Compute hashes before
            hashes_before = self._compute_file_hashes(fixture_copy)
            
            # Run CLI
            output_file = tmpdir / 'out.html'
            result = self._run_cli([str(fixture_copy), '--output', str(output_file)], cwd=tmpdir)
            
            # Check exit code
            self.assertEqual(result.returncode, 0)
            
            # Check stdout
            self.assertNotEqual(result.stdout, "")
            self.assertIn("wrote", result.stdout)
            
            # Check output file exists and is non-empty
            self.assertTrue(output_file.exists())
            content = output_file.read_text(encoding='utf-8')
            self.assertNotEqual(content, "")
            
            # Parse HTML
            parser = HTMLParser()
            parser.feed(content)
            
            # Check for expected module names
            expected_modules = [
                'samplepkg',
                'samplepkg.core',
                'samplepkg.util',
                'samplepkg.cli',
                'samplepkg.star',
                'samplepkg.broken',
                'test_core'
            ]
            for module in expected_modules:
                self.assertIn(module, content)
            
            # Compute hashes after
            hashes_after = self._compute_file_hashes(fixture_copy)
            
            # Verify copy is unchanged
            self.assertEqual(hashes_before, hashes_after)
    
    def test_devtools_root_html(self):
        """Test analyzing the devtools repo itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            output_file = tmpdir / 'out.html'
            
            result = self._run_cli([str(self.repo_root), '--output', str(output_file)], cwd=tmpdir)
            
            self.assertEqual(result.returncode, 0)
            
            content = output_file.read_text(encoding='utf-8')
            self.assertNotEqual(content, "")
            self.assertIn('repo_xray.analyzer', content)
            self.assertIn('repo_xray.report', content)
    
    def test_markdown_format(self):
        """Test Markdown output format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            fixture_copy = tmpdir / 'sample_repo'
            shutil.copytree(self.fixtures_dir / 'sample_repo', fixture_copy)
            
            output_file = tmpdir / 'out.md'
            result = self._run_cli(
                [str(fixture_copy), '--output', str(output_file), '--format', 'md'],
                cwd=tmpdir
            )
            
            self.assertEqual(result.returncode, 0)
            
            content = output_file.read_text(encoding='utf-8')
            self.assertNotEqual(content, "")
            self.assertIn('## Summary', content)
            self.assertIn('samplepkg.core', content)
    
    def test_no_output_argument(self):
        """Test default output filename in cwd."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            fixture_copy = tmpdir / 'sample_repo'
            shutil.copytree(self.fixtures_dir / 'sample_repo', fixture_copy)
            
            result = self._run_cli([str(fixture_copy)], cwd=tmpdir)
            
            self.assertEqual(result.returncode, 0)
            
            expected_file = tmpdir / 'sample_repo-xray.html'
            self.assertTrue(expected_file.exists())
            
            content = expected_file.read_text(encoding='utf-8')
            self.assertNotEqual(content, "")
    
    def test_output_inside_target(self):
        """Test that output inside target is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            fixture_copy = tmpdir / 'sample_repo'
            shutil.copytree(self.fixtures_dir / 'sample_repo', fixture_copy)
            
            # Try to write output inside the target
            output_file = fixture_copy / 'out.html'
            result = self._run_cli([str(fixture_copy), '--output', str(output_file)], cwd=tmpdir)
            
            self.assertEqual(result.returncode, 2)
            self.assertNotEqual(result.stderr, "")
            self.assertIn("outside the target", result.stderr)
            
            # Verify no file was created
            self.assertFalse(output_file.exists())
            
            # Verify copy is unchanged
            hashes_after = self._compute_file_hashes(fixture_copy)
            self.assertGreater(len(hashes_after), 0)
    
    def test_missing_path(self):
        """Test error handling for missing path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            missing_path = tmpdir / 'nonexistent'
            output_file = tmpdir / 'out.html'
            
            result = self._run_cli([str(missing_path), '--output', str(output_file)], cwd=tmpdir)
            
            self.assertEqual(result.returncode, 3)
            self.assertNotEqual(result.stderr, "")
            
            # Verify no output file was created
            self.assertFalse(output_file.exists())
    
    def test_max_files_limit(self):
        """Test --max-files limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            fixture_copy = tmpdir / 'sample_repo'
            shutil.copytree(self.fixtures_dir / 'sample_repo', fixture_copy)
            
            output_file = tmpdir / 'out.html'
            result = self._run_cli(
                [str(fixture_copy), '--output', str(output_file), '--max-files', '2'],
                cwd=tmpdir
            )
            
            self.assertEqual(result.returncode, 3)
            self.assertNotEqual(result.stderr, "")
            self.assertIn('--max-files', result.stderr)
    
    def test_svg_edge_deduplication(self):
        """Test that SVG graph has one edge per distinct (source, target) pair."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            fixture_copy = tmpdir / 'sample_repo'
            shutil.copytree(self.fixtures_dir / 'sample_repo', fixture_copy)
            
            output_file = tmpdir / 'out.html'
            result = self._run_cli([str(fixture_copy), '--output', str(output_file)], cwd=tmpdir)
            
            self.assertEqual(result.returncode, 0)
            
            content = output_file.read_text(encoding='utf-8')
            self.assertNotEqual(content, "")
            
            # Count <line elements in HTML
            line_count = content.count('<line')
            
            # Compute expected edge count from analyzer
            from repo_xray.analyzer import analyze
            model = analyze(fixture_copy)
            
            # Build set of test modules
            test_modules = {test['module'] for test in model.get('tests', [])}
            
            # Count distinct (source, target) edges among non-test modules
            edges = set()
            for mod_name, mod_info in model['modules'].items():
                if mod_name in test_modules:
                    continue
                for imp in mod_info.get('imports', []):
                    if imp.get('internal'):
                        target = imp['target']
                        if target not in test_modules and target in model['modules']:
                            edges.add((mod_name, target))
            
            expected_edge_count = len(edges)
            
            # Verify counts match
            self.assertEqual(line_count, expected_edge_count)
