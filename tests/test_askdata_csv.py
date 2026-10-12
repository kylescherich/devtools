"""Tests for CSV inspection."""
import hashlib
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open

from askdata.csvinspect import inspect_csv
from tests.askdata_fixtures import (
    clean_csv, messy_csv, empty_csv, header_only_csv,
    semicolon_csv, mixed_csv
)


class TestCSVInspect(unittest.TestCase):
    """Test CSV inspection functionality."""
    
    def _compute_sha256(self, path):
        """Compute SHA256 hash of a file."""
        sha = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(8192):
                sha.update(chunk)
        return sha.hexdigest()
    
    def test_clean_csv_types_and_stats(self):
        """Test clean CSV with proper types and statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            result = inspect_csv(path, samples=True)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            # Basic structure
            self.assertEqual(result["kind"], "csv")
            self.assertEqual(result["name"], "clean.csv")
            self.assertEqual(result["row_count"], 200)
            self.assertFalse(result["sampled"])
            
            # Find columns by name
            cols = {col["name"]: col for col in result["columns"]}
            
            # Check id column (int)
            self.assertEqual(cols["id"]["type"], "int")
            
            # Check price column (float) with min/max
            self.assertEqual(cols["price"]["type"], "float")
            self.assertIsNotNone(cols["price"]["numeric"])
            self.assertAlmostEqual(cols["price"]["numeric"]["min"], 10.5, places=1)
            self.assertAlmostEqual(cols["price"]["numeric"]["max"], 10.5 + 199 * 0.1, places=1)
            
            # Check created column (date) with min/max
            self.assertEqual(cols["created"]["type"], "date")
            self.assertIsNotNone(cols["created"]["dates"])
            self.assertIn("2024-01-15", cols["created"]["dates"]["min"])
            self.assertIn("2024-12-15", cols["created"]["dates"]["max"])
            
            # Check active column (bool)
            self.assertEqual(cols["active"]["type"], "bool")
            
            # Check samples <= 5
            for col in result["columns"]:
                self.assertLessEqual(len(col["samples"]), 5)
            
            # Check token uniqueness in JSON
            json_str = json.dumps(result)
            tokens = set(re.findall(r'tok-\d+', json_str))
            # Should have 1 to 5 distinct tokens
            self.assertGreaterEqual(len(tokens), 1)
            self.assertLessEqual(len(tokens), 5)
    
    def test_messy_csv_issues(self):
        """Test messy CSV with malformed rows, blank header, encoding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = messy_csv(tmpdir)
            result = inspect_csv(path, samples=True)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            # Check malformed rows
            self.assertEqual(result["malformed"]["count"], 2)
            self.assertEqual(result["malformed"]["first_lines"], [5, 9])
            
            # Check blank header was renamed
            cols = {col["name"]: col for col in result["columns"]}
            self.assertIn("column_3", cols)
            self.assertIn("blank header name replaced", " ".join(result["warnings"]))
            
            # Check encoding fallback
            self.assertEqual(result["encoding"], "cp1252")
            self.assertIn("encoding fallback", " ".join(result["warnings"]))
            
            # Check quoted value appears in samples of column_3
            col3_samples = cols["column_3"]["samples"]
            found_quoted = any("a, b" in str(s) for s in col3_samples)
            self.assertTrue(found_quoted, f"Expected 'a, b' in samples: {col3_samples}")
    
    def test_empty_csv(self):
        """Test empty CSV file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = empty_csv(tmpdir)
            result = inspect_csv(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            self.assertEqual(result["row_count"], 0)
            self.assertEqual(len(result["columns"]), 0)
            self.assertIn("empty file", result["warnings"])
    
    def test_header_only_csv(self):
        """Test CSV with only header."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = header_only_csv(tmpdir)
            result = inspect_csv(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            self.assertEqual(result["row_count"], 0)
            self.assertEqual(len(result["columns"]), 3)
            
            # All columns should have zero counts
            for col in result["columns"]:
                self.assertEqual(col["missing"], 0)
                self.assertEqual(col["distinct"], 0)
    
    def test_semicolon_delimiter(self):
        """Test semicolon delimiter detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = semicolon_csv(tmpdir)
            result = inspect_csv(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            self.assertEqual(result["delimiter"], ";")
            self.assertEqual(result["row_count"], 2)
    
    def test_mixed_type_below_threshold(self):
        """Test mixed column with 94% ints -> type mixed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = mixed_csv(tmpdir, 0.94)
            result = inspect_csv(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            col = result["columns"][0]
            self.assertEqual(col["type"], "mixed")
            self.assertEqual(col["candidate"], "int")
            self.assertIn("94% int", " ".join(result["warnings"]))
    
    def test_mixed_type_above_threshold(self):
        """Test mixed column with 96% ints -> type int."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = mixed_csv(tmpdir, 0.96)
            result = inspect_csv(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            col = result["columns"][0]
            self.assertEqual(col["type"], "int")
    
    def test_max_rows_limit(self):
        """Test max_rows limit causes sampling."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            result = inspect_csv(path, max_rows=50)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            self.assertEqual(result["row_count"], 50)
            self.assertTrue(result["sampled"])
            self.assertIn("sampled", " ".join(result["warnings"]))
    
    def test_deadline_timeout(self):
        """Test deadline causes timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            # Set deadline in the past
            result = inspect_csv(path, deadline=time.monotonic() - 1)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            self.assertTrue(result["sampled"])
            self.assertIn("timeout", " ".join(result["warnings"]))
    
    def test_fixtures_unchanged(self):
        """Test that inspect_csv doesn't modify input files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fixtures = [
                clean_csv(tmpdir),
                messy_csv(tmpdir),
                empty_csv(tmpdir),
                header_only_csv(tmpdir),
                semicolon_csv(tmpdir),
                mixed_csv(tmpdir, 0.5)
            ]
            
            # Compute hashes before
            hashes_before = {path: self._compute_sha256(path) for path in fixtures}
            
            # Run inspect_csv on each
            for path in fixtures:
                inspect_csv(path)
            
            # Compute hashes after
            hashes_after = {path: self._compute_sha256(path) for path in fixtures}
            
            # Compare
            self.assertEqual(hashes_before, hashes_after)
    
    def test_only_read_mode(self):
        """Test that builtins.open is only called with read mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = clean_csv(tmpdir)
            
            original_open = open
            open_calls = []
            
            def tracking_open(file, mode='r', *args, **kwargs):
                open_calls.append((str(file), mode))
                return original_open(file, mode, *args, **kwargs)
            
            with patch('builtins.open', tracking_open):
                inspect_csv(path)
            
            # Check all opens of the input file
            input_opens = [call for call in open_calls if str(path) in call[0]]
            
            for file, mode in input_opens:
                # Mode should not contain write/append/exclusive flags
                self.assertNotIn('w', mode, f"Write mode detected: {mode}")
                self.assertNotIn('a', mode, f"Append mode detected: {mode}")
                self.assertNotIn('+', mode, f"Update mode detected: {mode}")
                self.assertNotIn('x', mode, f"Exclusive mode detected: {mode}")
                # Should be read mode
                self.assertIn('r', mode, f"Expected read mode, got: {mode}")
    
    def test_encoding_fallback_late_bad_byte(self):
        """Test encoding fallback when bad byte appears after 64KB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "late_bad.csv"
            
            # Write header and 70,000+ bytes of ASCII rows, then a row with café
            with open(path, 'wb') as f:
                f.write(b'col1,col2\n')
                # Write enough rows to exceed 64KB
                for i in range(3000):
                    f.write(f'{i},value{i}\n'.encode('ascii'))
                # Now write a row with a bad UTF-8 byte (café in latin-1)
                f.write(b'9999,caf\xe9\n')
            
            result = inspect_csv(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result), 0)
            
            # Should have encoding cp1252
            self.assertEqual(result["encoding"], "cp1252")
            
            # Should have encoding fallback warning
            self.assertIn("encoding fallback", " ".join(result["warnings"]))
            
            # Should have processed rows (no exception)
            self.assertGreater(result["row_count"], 3000)


if __name__ == '__main__':
    unittest.main()
