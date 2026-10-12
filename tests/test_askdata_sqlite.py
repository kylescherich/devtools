"""Tests for SQLite database inspection."""
import unittest
import tempfile
import time
import hashlib
import sqlite3
from pathlib import Path

from askdata.sqliteinspect import inspect_sqlite, open_readonly, InspectError
from tests.askdata_fixtures import fk_db, empty_db, hostile_db, not_a_db


class TestSQLiteInspect(unittest.TestCase):
    """Test SQLite inspection functionality."""
    
    def _compute_sha256(self, path):
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()
    
    def test_fk_db_structure(self):
        """Test foreign key database structure and relationships."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertIn("tables", result)
            
            # Check we have tables and a view
            table_names = [t["name"] for t in result["tables"]]
            self.assertIn("customers", table_names)
            self.assertIn("orders", table_names)
            self.assertIn("big_orders", table_names)
            
            # Check types
            tables_by_name = {t["name"]: t for t in result["tables"]}
            self.assertEqual(tables_by_name["customers"]["type"], "table")
            self.assertEqual(tables_by_name["orders"]["type"], "table")
            self.assertEqual(tables_by_name["big_orders"]["type"], "view")
            
            # Check orders row count
            self.assertEqual(tables_by_name["orders"]["row_count"], 300)
            
            # Check customers columns
            customers = tables_by_name["customers"]
            cols_by_name = {c["name"]: c for c in customers["columns"]}
            
            self.assertTrue(cols_by_name["id"]["pk"])
            self.assertTrue(cols_by_name["name"]["notnull"])
            
            # Check foreign key
            orders = tables_by_name["orders"]
            self.assertIn("foreign_keys", orders)
            fks = orders["foreign_keys"]
            self.assertEqual(len(fks), 1)
            self.assertEqual(fks[0]["from"], "customer_id")
            self.assertEqual(fks[0]["table"], "customers")
            self.assertEqual(fks[0]["to"], "id")
            
            # Check relationships
            self.assertIn("relationships", result)
            expected_rel = ["orders.customer_id", "customers.id"]
            self.assertIn(expected_rel, result["relationships"])
            
            # Check index
            self.assertIn("indexes", orders)
            self.assertIn("idx_orders_customer", orders["indexes"])
            
            # Check amount column has numeric stats
            amount_col = next(c for c in orders["columns"] if c["name"] == "amount")
            self.assertIn("min", amount_col)
            self.assertIn("max", amount_col)
            self.assertLessEqual(amount_col["min"], amount_col["max"])
            
            # Check placed column is detected as date
            placed_col = next(c for c in orders["columns"] if c["name"] == "placed")
            self.assertEqual(placed_col["type"], "date")
            self.assertIn("min", placed_col)
            self.assertIn("max", placed_col)
            # Should be ISO format dates
            self.assertTrue(placed_col["min"].startswith("2024-"))
            self.assertTrue(placed_col["max"].startswith("2024-"))
            
            # Check view has columns
            big_orders = tables_by_name["big_orders"]
            self.assertGreater(len(big_orders["columns"]), 0)
    
    def test_empty_db(self):
        """Test empty database with no tables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = empty_db(tmpdir)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            
            # Should have empty tables list
            self.assertEqual(result["tables"], [])
            
            # Should have warning
            self.assertIn("warnings", result)
            self.assertIn("no tables", result["warnings"])
    
    def test_hostile_db_names_and_values(self):
        """Test that hostile names and values round-trip exactly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = hostile_db(tmpdir)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            # Check table name round-trips exactly
            table = result["tables"][0]
            self.assertEqual(table["name"], 't"<script>')
            
            # Check column name round-trips exactly
            col = table["columns"][0]
            self.assertEqual(col["name"], 'c"<b>')
            
            # Check hostile value appears in samples
            self.assertIn("samples", col)
            self.assertIn('<img src=x onerror=alert(1)>', col["samples"])
    
    def test_max_rows_sampling(self):
        """Test that max_rows limit triggers sampling."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            result = inspect_sqlite(path, max_rows=100)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            
            # Find orders table
            orders = next(t for t in result["tables"] if t["name"] == "orders")
            
            # Should be sampled
            self.assertTrue(orders["sampled"])
            
            # Row count should still be exact
            self.assertEqual(orders["row_count"], 300)
            
            # Should have warning
            self.assertIn("orders: sampled first 100 rows", result["warnings"])
    
    def test_deadline_timeout(self):
        """Test that deadline causes timeout without exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            
            # Set deadline in the past
            deadline = time.monotonic() - 1
            result = inspect_sqlite(path, deadline=deadline)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            
            # Should have timeout warning
            self.assertIn("warnings", result)
            self.assertTrue(
                any("timeout" in w for w in result["warnings"]),
                f"Expected timeout warning, got: {result['warnings']}"
            )
    
    def test_symlink_rejected(self):
        """Test that symlinks are rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create a real database
            real_path = fk_db(tmpdir)
            
            # Create a symlink to it
            link_path = tmpdir / "link.db"
            link_path.symlink_to(real_path)
            
            # Should raise InspectError
            with self.assertRaises(InspectError) as cm:
                inspect_sqlite(link_path)
            
            self.assertIn("symlink", str(cm.exception).lower())
    
    def test_not_a_database(self):
        """Test that non-database files are rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = not_a_db(tmpdir)
            
            # Should raise InspectError
            with self.assertRaises(InspectError) as cm:
                inspect_sqlite(path)
            
            self.assertIn("not a SQLite database", str(cm.exception))
    
    def test_ro_insert_denied(self):
        """Test that INSERT is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("INSERT INTO customers (name, email) VALUES ('test', 'test@example.com')")
            
            conn.close()
    
    def test_ro_create_table_denied(self):
        """Test that CREATE TABLE is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("CREATE TABLE test (id INTEGER)")
            
            conn.close()
    
    def test_ro_attach_denied(self):
        """Test that ATTACH is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("ATTACH ':memory:' AS m")
            
            conn.close()
    
    def test_ro_load_extension_denied(self):
        """Test that load_extension function is denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("SELECT load_extension('x')")
            
            conn.close()
    
    def test_ro_pragma_journal_mode_denied(self):
        """Test that PRAGMA journal_mode is denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("PRAGMA journal_mode=wal")
            
            conn.close()
    
    def test_ro_pragma_query_only_denied(self):
        """Test that changing query_only is denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("PRAGMA query_only=0")
            
            conn.close()
    
    def test_ro_files_unchanged(self):
        """Test that database files are unchanged after inspection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create all database fixtures
            fk_path = fk_db(tmpdir)
            empty_path = empty_db(tmpdir)
            hostile_path = hostile_db(tmpdir)
            
            # Compute hashes before
            hashes_before = {
                "fk": self._compute_sha256(fk_path),
                "empty": self._compute_sha256(empty_path),
                "hostile": self._compute_sha256(hostile_path)
            }
            
            # Run inspections
            inspect_sqlite(fk_path)
            inspect_sqlite(fk_path, max_rows=50)
            inspect_sqlite(empty_path)
            inspect_sqlite(hostile_path)
            
            # Compute hashes after
            hashes_after = {
                "fk": self._compute_sha256(fk_path),
                "empty": self._compute_sha256(empty_path),
                "hostile": self._compute_sha256(hostile_path)
            }
            
            # Hashes should be identical
            self.assertEqual(hashes_before, hashes_after)
            
            # Check no WAL/journal/shm files were created
            db_files = list(tmpdir.glob("*.db"))
            all_files = list(tmpdir.iterdir())
            
            # Should only have .db files, no -wal, -journal, or -shm
            for f in all_files:
                self.assertTrue(
                    f.suffix == ".db",
                    f"Unexpected file created: {f.name}"
                )


if __name__ == '__main__':
    unittest.main()
