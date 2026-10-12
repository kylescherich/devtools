"""Tests for SQLite database inspection."""
import statistics
import unittest
import unittest.mock
import tempfile
import time
import hashlib
import sqlite3
from pathlib import Path

from askdata.sqliteinspect import inspect_sqlite, open_readonly, InspectError
from tests.askdata_fixtures import (
    fk_db, empty_db, hostile_db, not_a_db, mixed_db,
    wal_db, fts_db, hostile_view_db
)


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
            self.assertIsNotNone(amount_col["numeric"])
            self.assertIn("min", amount_col["numeric"])
            self.assertIn("max", amount_col["numeric"])
            self.assertLessEqual(amount_col["numeric"]["min"], amount_col["numeric"]["max"])
            
            # Check placed column is detected as date
            placed_col = next(c for c in orders["columns"] if c["name"] == "placed")
            self.assertEqual(placed_col["type"], "date")
            self.assertIsNotNone(placed_col["dates"])
            self.assertIn("min", placed_col["dates"])
            self.assertIn("max", placed_col["dates"])
            # Should be ISO format dates
            self.assertTrue(placed_col["dates"]["min"].startswith("2024-"))
            self.assertTrue(placed_col["dates"]["max"].startswith("2024-"))
            
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
            result = inspect_sqlite(path, samples=True)
            
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
    
    def test_ro_uri_flags(self):
        """Test that open_readonly uses correct URI flags."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            
            # Capture the real connect function before patching
            real_connect = sqlite3.connect
            
            # Track calls
            calls = []
            
            def side_effect_wrapper(*args, **kwargs):
                calls.append((args, kwargs))
                return real_connect(*args, **kwargs)
            
            with unittest.mock.patch("askdata.sqliteinspect.sqlite3.connect", side_effect=side_effect_wrapper):
                conn = open_readonly(path)
                conn.close()
            
            # Assert exactly one connect call
            self.assertEqual(len(calls), 1)
            
            args, kwargs = calls[0]
            
            # First argument should start with "file:" and contain mode=ro and immutable=1
            self.assertTrue(args[0].startswith("file:"))
            self.assertIn("mode=ro", args[0])
            self.assertNotIn("immutable=1", args[0])
            
            # kwargs should have uri=True
            self.assertTrue(kwargs.get("uri"))
    
    def test_column_keys_complete(self):
        """Test that every column has exactly the required keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            required_keys = {
                "name", "declared_type", "notnull", "pk", "type", "candidate",
                "share", "missing", "distinct", "distinct_exact", "samples",
                "numeric", "dates", "top"
            }
            
            for table in result["tables"]:
                for col in table["columns"]:
                    col_keys = set(col.keys())
                    self.assertEqual(
                        col_keys, required_keys,
                        f"Column {table['name']}.{col['name']} has wrong keys: {col_keys}"
                    )
    
    def test_customers_no_foreign_keys(self):
        """Test that customers table has empty foreign_keys list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            
            tables_by_name = {t["name"]: t for t in result["tables"]}
            customers = tables_by_name["customers"]
            
            self.assertEqual(customers["foreign_keys"], [])
    
    def test_mixed_94_percent_int(self):
        """Test mixed column with 94% int shows mixed type with warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = mixed_db(tmpdir, 0.94)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            table = result["tables"][0]
            self.assertEqual(table["name"], "m")
            
            col = table["columns"][0]
            self.assertEqual(col["name"], "v")
            self.assertEqual(col["type"], "mixed")
            self.assertEqual(col["candidate"], "int")
            
            # Check warning
            self.assertIn("warnings", result)
            warning_found = any("m.v mixed: 94% int" in w for w in result["warnings"])
            self.assertTrue(warning_found, f"Expected warning not found in: {result['warnings']}")
    
    def test_mixed_96_percent_int(self):
        """Test mixed column with 96% int shows int type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = mixed_db(tmpdir, 0.96)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            table = result["tables"][0]
            col = table["columns"][0]
            
            self.assertEqual(col["type"], "int")
            self.assertEqual(col["candidate"], "int")
    
    def test_missing_path_raises_error(self):
        """Test that a missing path raises InspectError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "does_not_exist.db"
            
            with self.assertRaises(InspectError) as cm:
                inspect_sqlite(missing_path)
            
            self.assertIn("not a file", str(cm.exception))
    
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


    def test_wal_sees_concurrent_commit(self):
        """Test that a reader sees concurrent commits in WAL mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path, writer = wal_db(tmpdir)
            
            try:
                # First inspection should see 10 rows
                result1 = inspect_sqlite(path)
                self.assertIsNotNone(result1)
                tables_by_name = {t["name"]: t for t in result1["tables"]}
                self.assertEqual(tables_by_name["t"]["row_count"], 10)
                
                # Writer inserts 5 more rows and commits
                for i in range(10, 15):
                    writer.execute("INSERT INTO t (v) VALUES (?)", (f"value_{i}",))
                writer.commit()
                
                # Second inspection should see 15 rows
                result2 = inspect_sqlite(path)
                self.assertIsNotNone(result2)
                tables_by_name = {t["name"]: t for t in result2["tables"]}
                self.assertEqual(tables_by_name["t"]["row_count"], 15)
                
            finally:
                writer.close()
    
    def test_wal_side_files_unchanged(self):
        """Test that WAL side files are unchanged after inspection.
        
        Note: -shm holds SQLite reader marks and may change.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            path, writer = wal_db(tmpdir)
            
            try:
                # Compute hashes and sizes before
                db_hash_before = self._compute_sha256(path)
                wal_path = Path(str(path) + "-wal")
                wal_hash_before = self._compute_sha256(wal_path)
                shm_path = Path(str(path) + "-shm")
                shm_size_before = shm_path.stat().st_size
                listing_before = sorted([f.name for f in tmpdir.iterdir()])
                
                # Run inspection
                inspect_sqlite(path)
                
                # Compute hashes and sizes after
                db_hash_after = self._compute_sha256(path)
                wal_hash_after = self._compute_sha256(wal_path)
                shm_size_after = shm_path.stat().st_size
                listing_after = sorted([f.name for f in tmpdir.iterdir()])
                
                # Database and WAL should be unchanged
                self.assertEqual(db_hash_before, db_hash_after)
                self.assertEqual(wal_hash_before, wal_hash_after)
                # SHM size should be unchanged
                self.assertEqual(shm_size_before, shm_size_after)
                # Directory listing should be unchanged
                self.assertEqual(listing_before, listing_after)
                
            finally:
                writer.close()
    
    def test_wal_without_shm_refused(self):
        """Test that WAL database without -shm access is refused unless frozen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            path, writer = wal_db(tmpdir)
            writer.close()
            
            # Remove -shm file
            shm_path = Path(str(path) + "-shm")
            shm_path.unlink(missing_ok=True)
            
            listing_before = sorted([f.name for f in tmpdir.iterdir()])
            
            # Should raise InspectError mentioning --frozen
            with self.assertRaises(InspectError) as cm:
                inspect_sqlite(path)
            
            self.assertIn("--frozen", str(cm.exception))
            
            # Directory listing should be unchanged
            listing_after = sorted([f.name for f in tmpdir.iterdir()])
            self.assertEqual(listing_before, listing_after)
            
            # With frozen=True, should work and see 10 rows
            result = inspect_sqlite(path, frozen=True)
            self.assertIsNotNone(result)
            tables_by_name = {t["name"]: t for t in result["tables"]}
            self.assertEqual(tables_by_name["t"]["row_count"], 10)
            
            # Directory listing should still be unchanged
            listing_final = sorted([f.name for f in tmpdir.iterdir()])
            self.assertEqual(listing_before, listing_final)
    
    def test_default_uri_has_no_immutable(self):
        """Test that default URI does not include immutable=1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            
            real_connect = sqlite3.connect
            calls = []
            
            def side_effect_wrapper(*args, **kwargs):
                calls.append((args, kwargs))
                return real_connect(*args, **kwargs)
            
            with unittest.mock.patch("askdata.sqliteinspect.sqlite3.connect", side_effect=side_effect_wrapper):
                conn = open_readonly(path, frozen=False)
                conn.close()
            
            self.assertEqual(len(calls), 1)
            args, kwargs = calls[0]
            
            self.assertTrue(args[0].startswith("file:"))
            self.assertIn("mode=ro", args[0])
            self.assertNotIn("immutable=1", args[0])
            self.assertTrue(kwargs.get("uri"))
    
    def test_frozen_uri_has_immutable(self):
        """Test that frozen=True URI includes immutable=1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            
            real_connect = sqlite3.connect
            calls = []
            
            def side_effect_wrapper(*args, **kwargs):
                calls.append((args, kwargs))
                return real_connect(*args, **kwargs)
            
            with unittest.mock.patch("askdata.sqliteinspect.sqlite3.connect", side_effect=side_effect_wrapper):
                conn = open_readonly(path, frozen=True)
                conn.close()
            
            self.assertEqual(len(calls), 1)
            args, kwargs = calls[0]
            
            self.assertTrue(args[0].startswith("file:"))
            self.assertIn("mode=ro", args[0])
            self.assertIn("immutable=1", args[0])
            self.assertTrue(kwargs.get("uri"))
    
    def test_ro_update_denied(self):
        """Test that UPDATE is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE customers SET name = 'test' WHERE id = 1")
            
            conn.close()
    
    def test_ro_delete_denied(self):
        """Test that DELETE is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM customers WHERE id = 1")
            
            conn.close()
    
    def test_ro_drop_denied(self):
        """Test that DROP TABLE is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DROP TABLE customers")
            
            conn.close()
    
    def test_ro_vacuum_denied(self):
        """Test that VACUUM is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("VACUUM")
            
            conn.close()
    
    def test_ro_create_temp_denied(self):
        """Test that CREATE TEMP TABLE is denied in read-only mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("CREATE TEMP TABLE temp_test (id INTEGER)")
            
            conn.close()
    
    def test_ro_table_valued_pragma_denied(self):
        """Test that table-valued pragmas are denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            conn = open_readonly(path)
            
            # pragma_journal_mode with argument should be denied
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("PRAGMA journal_mode")
            
            # pragma_function_list should be denied
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("PRAGMA function_list")
            
            conn.close()
    
    def test_fts5_and_shadow_tables(self):
        """Test that FTS5 tables work and shadow tables are listed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fts_db(tmpdir)
            result = inspect_sqlite(path)
            
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            tables_by_name = {t["name"]: t for t in result["tables"]}
            
            # docs table should exist with 3 rows
            self.assertIn("docs", tables_by_name)
            self.assertEqual(tables_by_name["docs"]["row_count"], 3)
            
            # docs_data shadow table should be listed
            self.assertIn("docs_data", tables_by_name)
            
            # Should not have "could not be read" warning for docs
            warnings_text = " ".join(result["warnings"])
            self.assertNotIn("docs: could not be read", warnings_text)
    
    def test_hostile_view_fails_cleanly(self):
        """Test that hostile views fail cleanly without executing dangerous operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = hostile_view_db(tmpdir)
            result = inspect_sqlite(path)
            
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            tables_by_name = {t["name"]: t for t in result["tables"]}
            
            # t table should work fine with 1 row
            self.assertIn("t", tables_by_name)
            self.assertEqual(tables_by_name["t"]["row_count"], 1)
            self.assertNotIn("error", tables_by_name["t"])
            
            # bad and bad2 views should have errors
            self.assertIn("bad", tables_by_name)
            self.assertIn("error", tables_by_name["bad"])
            self.assertEqual(tables_by_name["bad"]["columns"], [])
            
            self.assertIn("bad2", tables_by_name)
            self.assertIn("error", tables_by_name["bad2"])
            self.assertEqual(tables_by_name["bad2"]["columns"], [])
            
            # Should have warnings for both
            warnings_text = " ".join(result["warnings"])
            self.assertIn("bad: could not be read", warnings_text)
            self.assertIn("bad2: could not be read", warnings_text)
            
            # Verify dangerous file was not created
            dangerous_path = Path("/tmp/askdata-should-not-exist")
            self.assertFalse(dangerous_path.exists())


    def test_order_keys(self):
        """Test that order keys are correctly determined."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from tests.askdata_fixtures import order_db
            path = order_db(tmpdir)
            result = inspect_sqlite(path)
            
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            tables_by_name = {t["name"]: t for t in result["tables"]}
            
            # r has rowid
            self.assertEqual(tables_by_name["r"]["order"], "rowid")
            
            # w is WITHOUT ROWID but has primary key
            self.assertEqual(tables_by_name["w"]["order"], "primary key")
            
            # rv is a view with no rowid or pk
            self.assertEqual(tables_by_name["rv"]["order"], "none")
    
    def test_order_affects_sampling(self):
        """Test that ordering affects which rows are sampled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from tests.askdata_fixtures import order_db
            path = order_db(tmpdir)
            result = inspect_sqlite(path, max_rows=10)
            
            self.assertIsNotNone(result)
            
            tables_by_name = {t["name"]: t for t in result["tables"]}
            
            # r is ordered by rowid, so first 10 rows should be id 1..10
            r = tables_by_name["r"]
            self.assertTrue(r["sampled"])
            v_col = next(c for c in r["columns"] if c["name"] == "v")
            self.assertIsNotNone(v_col["numeric"])
            self.assertEqual(v_col["numeric"]["min"], 1)
            self.assertEqual(v_col["numeric"]["max"], 10)
            
            # w is ordered by primary key k, so first 10 rows should be k000..k009 (v = 0..9)
            w = tables_by_name["w"]
            self.assertTrue(w["sampled"])
            v_col = next(c for c in w["columns"] if c["name"] == "v")
            self.assertIsNotNone(v_col["numeric"])
            self.assertEqual(v_col["numeric"]["min"], 0)
            self.assertEqual(v_col["numeric"]["max"], 9)
    
    def test_median_calculation(self):
        """Test that median is calculated correctly in SQL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            result = inspect_sqlite(path)
            
            self.assertIsNotNone(result)
            
            tables_by_name = {t["name"]: t for t in result["tables"]}
            orders = tables_by_name["orders"]
            
            amount_col = next(c for c in orders["columns"] if c["name"] == "amount")
            self.assertIsNotNone(amount_col["numeric"])
            
            # Calculate expected median using statistics module
            # We need to get all 300 amounts
            import sqlite3
            conn = sqlite3.connect(str(path))
            cursor = conn.execute("SELECT amount FROM orders WHERE amount IS NOT NULL ORDER BY amount")
            all_amounts = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            expected_median = statistics.median(all_amounts)
            
            # Check that our SQL-calculated median matches
            self.assertEqual(amount_col["numeric"]["median"], expected_median)
            self.assertFalse(amount_col["numeric"]["median_approx"])
    
    def test_order_in_terminal_report(self):
        """Test that order and rows analyzed appear in terminal report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from tests.askdata_fixtures import order_db
            from askdata.report import render_terminal
            
            path = order_db(tmpdir)
            result = inspect_sqlite(path, max_rows=10)
            
            output = render_terminal(result)
            
            self.assertIsNotNone(output)
            self.assertIn("Order: none (rows unordered)", output)
            self.assertIn("Rows analyzed: 10 of 100", output)
    
    def test_column_keys_complete_with_order(self):
        """Test that every column still has exactly the required keys after adding order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = fk_db(tmpdir)
            result = inspect_sqlite(path)
            
            # Assert non-empty
            self.assertIsNotNone(result)
            self.assertGreater(len(result["tables"]), 0)
            
            required_keys = {
                "name", "declared_type", "notnull", "pk", "type", "candidate",
                "share", "missing", "distinct", "distinct_exact", "samples",
                "numeric", "dates", "top"
            }
            
            for table in result["tables"]:
                for col in table["columns"]:
                    col_keys = set(col.keys())
                    self.assertEqual(
                        col_keys, required_keys,
                        f"Column {table['name']}.{col['name']} has wrong keys: {col_keys}"
                    )


if __name__ == '__main__':
    unittest.main()
