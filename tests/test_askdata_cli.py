import contextlib
import hashlib
import html
import io
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from askdata.cli import main
from tests.askdata_fixtures import (
    clean_csv, messy_csv, empty_csv, header_only_csv, semicolon_csv,
    mixed_csv, fk_db, empty_db, hostile_db, not_a_db
)


class TestAskdataCLI(unittest.TestCase):
    def _compute_sha256(self, path):
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()
    
    def _capture_main(self, args):
        """Run main() with captured stdout/stderr."""
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        with contextlib.redirect_stdout(stdout_capture):
            with contextlib.redirect_stderr(stderr_capture):
                exit_code = main(args)
        
        return exit_code, stdout_capture.getvalue(), stderr_capture.getvalue()
    
    def test_no_arguments_shows_usage(self):
        exit_code, stdout, stderr = self._capture_main([])
        
        self.assertNotEqual(stderr, "")
        self.assertEqual(exit_code, 2)
        self.assertIn("usage: askdata", stderr)
    
    def test_inspect_help_shows_options(self):
        exit_code, stdout, stderr = self._capture_main(["inspect", "--help"])
        
        self.assertNotEqual(stdout, "")
        self.assertEqual(exit_code, 0)
        self.assertIn("--max-bytes", stdout)
        self.assertIn("--max-rows", stdout)
        self.assertIn("--timeout", stdout)
        self.assertIn("--force", stdout)
    
    def test_clean_csv_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            initial_hash = self._compute_sha256(path)
            initial_files = set(Path(tmpdir).iterdir())
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertIn("price", stdout)
            
            # Verify input unchanged
            self.assertEqual(initial_hash, self._compute_sha256(path))
            self.assertEqual(initial_files, set(Path(tmpdir).iterdir()))
    
    def test_clean_csv_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = clean_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                initial_hash = self._compute_sha256(input_path)
                initial_files = set(Path(input_dir).iterdir())
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertNotEqual(stdout, "")
                self.assertEqual(exit_code, 0)
                self.assertIn("wrote", stdout)
                self.assertTrue(output_path.exists())
                
                # Parse HTML and check for column names
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                # Verify it parses
                parser = HTMLParser()
                parser.feed(html_content)
                
                # Check for escaped column names
                for col_name in ["id", "price", "created", "active", "name", "token"]:
                    escaped = html.escape(col_name, quote=True)
                    self.assertIn(escaped, html_content)
                
                # Verify input unchanged
                self.assertEqual(initial_hash, self._compute_sha256(input_path))
                self.assertEqual(initial_files, set(Path(input_dir).iterdir()))
    
    def test_messy_csv_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = messy_csv(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_messy_csv_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = messy_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                initial_hash = self._compute_sha256(input_path)
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                parser = HTMLParser()
                parser.feed(html_content)
                
                for col_name in ["col1", "col2", "column_3", "col4"]:
                    escaped = html.escape(col_name, quote=True)
                    self.assertIn(escaped, html_content)
                
                self.assertEqual(initial_hash, self._compute_sha256(input_path))
    
    def test_empty_csv_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = empty_csv(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_empty_csv_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = empty_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
    
    def test_header_only_csv_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = header_only_csv(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_header_only_csv_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = header_only_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                for col_name in ["col1", "col2", "col3"]:
                    escaped = html.escape(col_name, quote=True)
                    self.assertIn(escaped, html_content)
    
    def test_semicolon_csv_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = semicolon_csv(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_semicolon_csv_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = semicolon_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                for col_name in ["a", "b", "c"]:
                    escaped = html.escape(col_name, quote=True)
                    self.assertIn(escaped, html_content)
    
    def test_mixed_csv_94_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = mixed_csv(tmpdir, 0.94)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_mixed_csv_94_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = mixed_csv(input_dir, 0.94)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                escaped = html.escape("mixed_col", quote=True)
                self.assertIn(escaped, html_content)
    
    def test_fk_db_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_fk_db_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = fk_db(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                parser = HTMLParser()
                parser.feed(html_content)
                
                # Check customers table columns
                for col_name in ["id", "name", "email"]:
                    escaped = html.escape(col_name, quote=True)
                    self.assertIn(escaped, html_content)
                
                # Check orders table columns
                for col_name in ["customer_id", "amount", "placed"]:
                    escaped = html.escape(col_name, quote=True)
                    self.assertIn(escaped, html_content)
    
    def test_fk_db_md_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = fk_db(input_dir)
                output_path = Path(output_dir) / "report.md"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path),
                    "--format", "md"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                
                self.assertIn("customers", md_content)
                self.assertIn("orders", md_content)
    
    def test_empty_db_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = empty_db(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_empty_db_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = empty_db(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
    
    def test_hostile_db_terminal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = hostile_db(tmpdir)
            initial_hash = self._compute_sha256(path)
            
            exit_code, stdout, stderr = self._capture_main(["inspect", str(path)])
            
            self.assertNotEqual(stdout, "")
            self.assertEqual(exit_code, 0)
            self.assertEqual(initial_hash, self._compute_sha256(path))
    
    def test_hostile_db_html_output(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = hostile_db(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
                
                with open(output_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
                
                # Verify hostile content is escaped
                escaped_table = html.escape('t"<script>', quote=True)
                escaped_col = html.escape('c"<b>', quote=True)
                self.assertIn(escaped_table, html_content)
                self.assertIn(escaped_col, html_content)
    
    def test_output_inside_input_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = clean_csv(tmpdir)
            output_path = Path(tmpdir) / "report.html"
            initial_files = set(Path(tmpdir).iterdir())
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(input_path), "--output", str(output_path)
            ])
            
            self.assertEqual(exit_code, 2)
            self.assertIn("inside input directory", stderr)
            # No new file created
            self.assertEqual(initial_files, set(Path(tmpdir).iterdir()))
    
    def test_output_exists_without_force(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = clean_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                # Create existing file
                output_path.write_text("existing content")
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path)
                ])
                
                self.assertEqual(exit_code, 2)
                self.assertIn("exists", stderr)
                # File unchanged
                self.assertEqual(output_path.read_text(), "existing content")
    
    def test_output_exists_with_force(self):
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = clean_csv(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                # Create existing file
                output_path.write_text("existing content")
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path),
                    "--force"
                ])
                
                self.assertEqual(exit_code, 0)
                # File overwritten
                self.assertNotEqual(output_path.read_text(), "existing content")
    
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "missing.csv"
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(missing_path)
            ])
            
            self.assertEqual(exit_code, 3)
            self.assertIn("not found", stderr)
    
    def test_directory_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exit_code, stdout, stderr = self._capture_main([
                "inspect", tmpdir
            ])
            
            self.assertEqual(exit_code, 3)
            self.assertIn("directory", stderr)
    
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_path = clean_csv(tmpdir)
            symlink_path = Path(tmpdir) / "link.csv"
            symlink_path.symlink_to(real_path)
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(symlink_path)
            ])
            
            self.assertEqual(exit_code, 3)
            self.assertIn("symlink", stderr)
    
    def test_not_a_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = not_a_db(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(path)
            ])
            
            self.assertEqual(exit_code, 3)
    
    def test_unsupported_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            xyz_path = Path(tmpdir) / "file.xyz"
            xyz_path.write_text("some content")
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(xyz_path)
            ])
            
            self.assertEqual(exit_code, 3)
            self.assertIn("unsupported format", stderr)
    
    def test_invalid_format_option(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(path), "--format", "pdf"
            ])
            
            self.assertEqual(exit_code, 2)
    
    def test_max_rows_sampling(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(path), "--max-rows", "50"
            ])
            
            self.assertEqual(exit_code, 0)
            self.assertIn("sampled", stdout)
    
    def test_timeout_partial_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(path), "--timeout", "0"
            ])
            
            self.assertEqual(exit_code, 0)
            self.assertIn("timeout", stdout)
    
    def test_all_inputs_unchanged(self):
        """Verify all input files are unchanged after processing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create all fixtures and compute hashes
            fixtures = [
                clean_csv(tmpdir),
                messy_csv(tmpdir),
                empty_csv(tmpdir),
                header_only_csv(tmpdir),
                semicolon_csv(tmpdir),
                mixed_csv(tmpdir, 0.94),
                fk_db(tmpdir),
                empty_db(tmpdir),
                hostile_db(tmpdir),
            ]
            
            initial_hashes = {path: self._compute_sha256(path) for path in fixtures}
            initial_files = set(Path(tmpdir).iterdir())
            
            # Process each fixture
            for path in fixtures:
                self._capture_main(["inspect", str(path)])
            
            # Verify all unchanged
            for path in fixtures:
                self.assertEqual(initial_hashes[path], self._compute_sha256(path))
            
            self.assertEqual(initial_files, set(Path(tmpdir).iterdir()))
    
    def test_subprocess_clean_csv(self):
        """Test running via subprocess."""
        repo_root = Path(__file__).parent.parent
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            
            result = subprocess.run(
                [sys.executable, "-m", "askdata", "inspect", str(path)],
                cwd=repo_root,
                capture_output=True,
                text=True
            )
            
            self.assertEqual(result.returncode, 0)
            self.assertIn("price", result.stdout)
    
    def test_frozen_flag_on_fk_db(self):
        """Test --frozen flag on regular database."""
        with tempfile.TemporaryDirectory() as input_dir:
            with tempfile.TemporaryDirectory() as output_dir:
                input_path = fk_db(input_dir)
                output_path = Path(output_dir) / "report.html"
                
                exit_code, stdout, stderr = self._capture_main([
                    "inspect", str(input_path), "--output", str(output_path),
                    "--frozen"
                ])
                
                self.assertEqual(exit_code, 0)
                self.assertTrue(output_path.exists())
    
    def test_wal_without_shm_error(self):
        """Test that WAL database without -shm gives exit code 3 with --frozen in stderr."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from tests.askdata_fixtures import wal_db
            path, writer = wal_db(tmpdir)
            writer.close()
            
            # Remove -shm file
            shm_path = Path(str(path) + "-shm")
            shm_path.unlink(missing_ok=True)
            
            exit_code, stdout, stderr = self._capture_main([
                "inspect", str(path)
            ])
            
            self.assertEqual(exit_code, 3)
            self.assertIn("--frozen", stderr)
