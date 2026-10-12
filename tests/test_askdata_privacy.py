"""Tests for privacy defaults in askdata."""
import re
import tempfile
import unittest
from pathlib import Path

from askdata.cli import main
from askdata.report import render_terminal, render_md, render_html
from tests.askdata_fixtures import privacy_csv, privacy_db


class TestPrivacy(unittest.TestCase):
    """Test privacy defaults."""
    
    def test_privacy_csv_terminal_no_samples(self):
        """Privacy CSV terminal output without --samples hides sensitive data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = privacy_csv(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            # Assert non-empty
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            
            # Should have "Samples: off"
            self.assertIn("Samples: off", stdout)
            
            # Should NOT contain sensitive data
            self.assertNotIn("@example.com", stdout)
            self.assertNotIn("ghp_", stdout)
            self.assertNotIn("sk-", stdout)
            
            # Should NOT contain any 16-digit runs
            self.assertIsNone(re.search(r'\d{16}', stdout))
    
    def test_privacy_csv_terminal_with_samples(self):
        """Privacy CSV terminal output with --samples shows sensitive data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = privacy_csv(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path), "--samples"])
            
            # Assert non-empty
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            
            # Should have "Samples: on"
            self.assertIn("Samples: on", stdout)
            
            # Should contain sensitive data
            self.assertIn("@example.com", stdout)
            self.assertIn("ghp_", stdout)
            self.assertIn("sk-", stdout)
            
            # Should contain 16-digit runs
            self.assertIsNotNone(re.search(r'\d{16}', stdout))
    
    def test_privacy_csv_html_no_samples(self):
        """Privacy CSV HTML output without --samples hides sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(html_content, "")
                
                # Should have "Samples: off"
                self.assertIn("Samples:</strong> off", html_content)
                
                # Should NOT contain sensitive data
                self.assertNotIn("@example.com", html_content)
                self.assertNotIn("ghp_", html_content)
                self.assertNotIn("sk-", html_content)
                
                # Should NOT contain any 16-digit runs
                self.assertIsNone(re.search(r'\d{16}', html_content))
    
    def test_privacy_csv_html_with_samples(self):
        """Privacy CSV HTML output with --samples shows sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path), "--samples"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(html_content, "")
                
                # Should have "Samples: on"
                self.assertIn("Samples:</strong> on", html_content)
                
                # Should contain sensitive data
                self.assertIn("@example.com", html_content)
                self.assertIn("ghp_", html_content)
                self.assertIn("sk-", html_content)
                
                # Should contain 16-digit runs
                self.assertIsNotNone(re.search(r'\d{16}', html_content))
    
    def test_privacy_csv_md_no_samples(self):
        """Privacy CSV Markdown output without --samples hides sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_csv(input_dir)
                output_path = Path(output_dir) / "report.md"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path), "--format", "md"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(md_content, "")
                
                # Should have "Samples: off"
                self.assertIn("**Samples:** off", md_content)
                
                # Should NOT contain sensitive data
                self.assertNotIn("@example.com", md_content)
                self.assertNotIn("ghp_", md_content)
                self.assertNotIn("sk-", md_content)
                
                # Should NOT contain any 16-digit runs
                self.assertIsNone(re.search(r'\d{16}', md_content))
    
    def test_privacy_csv_md_with_samples(self):
        """Privacy CSV Markdown output with --samples shows sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_csv(input_dir)
                output_path = Path(output_dir) / "report.md"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path), 
                    "--format", "md", "--samples"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(md_content, "")
                
                # Should have "Samples: on"
                self.assertIn("**Samples:** on", md_content)
                
                # Should contain sensitive data
                self.assertIn("@example.com", md_content)
                self.assertIn("ghp_", md_content)
                self.assertIn("sk-", md_content)
                
                # Should contain 16-digit runs
                self.assertIsNotNone(re.search(r'\d{16}', md_content))
    
    def test_privacy_db_terminal_no_samples(self):
        """Privacy DB terminal output without --samples hides sensitive data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = privacy_db(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            # Assert non-empty
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            
            # Should have "Samples: off"
            self.assertIn("Samples: off", stdout)
            
            # Should NOT contain sensitive data
            self.assertNotIn("@example.com", stdout)
            self.assertNotIn("ghp_", stdout)
            self.assertNotIn("sk-", stdout)
            
            # Should NOT contain any 16-digit runs
            self.assertIsNone(re.search(r'\d{16}', stdout))
    
    def test_privacy_db_terminal_with_samples(self):
        """Privacy DB terminal output with --samples shows sensitive data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = privacy_db(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path), "--samples"])
            
            # Assert non-empty
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            
            # Should have "Samples: on"
            self.assertIn("Samples: on", stdout)
            
            # Should contain sensitive data
            self.assertIn("@example.com", stdout)
            self.assertIn("ghp_", stdout)
            self.assertIn("sk-", stdout)
            
            # Should contain 16-digit runs
            self.assertIsNotNone(re.search(r'\d{16}', stdout))
    
    def test_privacy_db_html_no_samples(self):
        """Privacy DB HTML output without --samples hides sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_db(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(html_content, "")
                
                # Should have "Samples: off"
                self.assertIn("Samples:</strong> off", html_content)
                
                # Should NOT contain sensitive data
                self.assertNotIn("@example.com", html_content)
                self.assertNotIn("ghp_", html_content)
                self.assertNotIn("sk-", html_content)
                
                # Should NOT contain any 16-digit runs
                self.assertIsNone(re.search(r'\d{16}', html_content))
    
    def test_privacy_db_html_with_samples(self):
        """Privacy DB HTML output with --samples shows sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_db(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path), "--samples"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(html_content, "")
                
                # Should have "Samples: on"
                self.assertIn("Samples:</strong> on", html_content)
                
                # Should contain sensitive data
                self.assertIn("@example.com", html_content)
                self.assertIn("ghp_", html_content)
                self.assertIn("sk-", html_content)
                
                # Should contain 16-digit runs
                self.assertIsNotNone(re.search(r'\d{16}', html_content))
    
    def test_privacy_db_md_no_samples(self):
        """Privacy DB Markdown output without --samples hides sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_db(input_dir)
                output_path = Path(output_dir) / "report.md"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path), "--format", "md"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(md_content, "")
                
                # Should have "Samples: off"
                self.assertIn("**Samples:** off", md_content)
                
                # Should NOT contain sensitive data
                self.assertNotIn("@example.com", md_content)
                self.assertNotIn("ghp_", md_content)
                self.assertNotIn("sk-", md_content)
                
                # Should NOT contain any 16-digit runs
                self.assertIsNone(re.search(r'\d{16}', md_content))
    
    def test_privacy_db_md_with_samples(self):
        """Privacy DB Markdown output with --samples shows sensitive data."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = privacy_db(input_dir)
                output_path = Path(output_dir) / "report.md"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path), 
                    "--format", "md", "--samples"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                
                # Assert non-empty
                self.assertNotEqual(md_content, "")
                
                # Should have "Samples: on"
                self.assertIn("**Samples:** on", md_content)
                
                # Should contain sensitive data
                self.assertIn("@example.com", md_content)
                self.assertIn("ghp_", md_content)
                self.assertIn("sk-", md_content)
                
                # Should contain 16-digit runs
                self.assertIsNotNone(re.search(r'\d{16}', md_content))
    
    def test_error_object_renders_terminal(self):
        """Object with error key renders in terminal format."""
        model = {
            "kind": "sqlite",
            "name": "test.db",
            "size_bytes": 1024,
            "samples_enabled": False,
            "tables": [
                {
                    "name": "bad_table",
                    "type": "table",
                    "error": "could not read table",
                    "row_count": None,
                    "sampled": False,
                    "foreign_keys": [],
                    "indexes": [],
                    "columns": []
                }
            ],
            "relationships": [],
            "warnings": []
        }
        
        output = render_terminal(model)
        
        # Assert non-empty
        self.assertNotEqual(output, "")
        
        # Should contain error message
        self.assertIn("Error: could not read table", output)
        self.assertIn("bad_table", output)
    
    def test_error_object_renders_md(self):
        """Object with error key renders in Markdown format."""
        model = {
            "kind": "sqlite",
            "name": "test.db",
            "size_bytes": 1024,
            "samples_enabled": False,
            "tables": [
                {
                    "name": "bad_table",
                    "type": "view",
                    "error": "SELECT failed",
                    "row_count": None,
                    "sampled": False,
                    "foreign_keys": [],
                    "indexes": [],
                    "columns": []
                }
            ],
            "relationships": [],
            "warnings": []
        }
        
        output = render_md(model)
        
        # Assert non-empty
        self.assertNotEqual(output, "")
        
        # Should contain error message
        self.assertIn("**Error:** SELECT failed", output)
        self.assertIn("bad_table", output)
    
    def test_error_object_renders_html(self):
        """Object with error key renders in HTML format."""
        model = {
            "kind": "sqlite",
            "name": "test.db",
            "size_bytes": 1024,
            "samples_enabled": False,
            "tables": [
                {
                    "name": "bad_table",
                    "type": "table",
                    "error": "authorization denied",
                    "row_count": None,
                    "sampled": False,
                    "foreign_keys": [],
                    "indexes": [],
                    "columns": []
                }
            ],
            "relationships": [],
            "warnings": []
        }
        
        output = render_html(model)
        
        # Assert non-empty
        self.assertNotEqual(output, "")
        
        # Should contain error message (HTML escaped)
        self.assertIn("<strong>Error:</strong> authorization denied", output)
        self.assertIn("bad_table", output)
    
    def _capture_main(self, args):
        """Run main() with captured stdout/stderr."""
        import contextlib
        import io
        
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        with contextlib.redirect_stdout(stdout_capture):
            with contextlib.redirect_stderr(stderr_capture):
                exit_code = main(args)
        
        return exit_code, stdout_capture.getvalue(), stderr_capture.getvalue()


if __name__ == '__main__':
    unittest.main()
