"""SQLite database inspection and analysis."""
import sqlite3
import urllib.parse
import time
import statistics
from pathlib import Path


class InspectError(Exception):
    """Error during database inspection."""
    pass


def open_readonly(path, frozen=False):
    """Open a SQLite database in read-only mode with security restrictions.
    
    Args:
        path: Path to the database file
        frozen: If True, add immutable=1 flag (for static copies only)
        
    Returns:
        sqlite3.Connection object configured for read-only access
        
    Raises:
        InspectError: If path is a symlink, not a file, or not a valid database
    """
    path = Path(path)
    
    # Refuse symlinks, missing paths, and non-files
    if not path.exists():
        raise InspectError("not a file")
    if path.is_symlink():
        raise InspectError("symlinks are not allowed")
    if not path.is_file():
        raise InspectError("not a file")
    
    # Read first 100 bytes to check for WAL mode
    try:
        with open(path, 'rb') as f:
            header = f.read(100)
    except (OSError, PermissionError) as e:
        raise InspectError("not a file") from e
    
    # Check if database is in WAL mode (bytes 18 and 19 both == 2)
    if len(header) >= 20 and header[18] == 2 and header[19] == 2:
        # WAL mode detected
        if not frozen:
            shm_path = Path(str(path) + "-shm")
            if not shm_path.exists():
                raise InspectError("WAL database without -shm access; copy it and use --frozen")
    
    # Build read-only URI
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro"
    if frozen:
        uri += "&immutable=1"
    
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        error_name = getattr(e, 'sqlite_errorname', '')
        if error_name == 'SQLITE_CANTOPEN' or error_name.startswith('SQLITE_READONLY'):
            raise InspectError("WAL database without -shm access; copy it and use --frozen") from e
        raise InspectError("not a SQLite database") from e
    
    # Enable query-only mode
    try:
        conn.execute("PRAGMA query_only=1")
    except sqlite3.Error as e:
        conn.close()
        error_name = getattr(e, 'sqlite_errorname', '')
        if error_name == 'SQLITE_CANTOPEN' or error_name.startswith('SQLITE_READONLY'):
            raise InspectError("WAL database without -shm access; copy it and use --frozen") from e
        raise InspectError("not a SQLite database") from e
    
    # Set up authorizer to restrict operations
    def authorizer(action, arg1, arg2, arg3, arg4):
        # Allow SELECT and READ operations
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        
        # Allow safe functions only
        if action == sqlite3.SQLITE_FUNCTION:
            func_name = (arg2 or "").lower()
            blocked_functions = {
                'load_extension', 'readfile', 'writefile', 'edit', 'fts3_tokenizer'
            }
            if func_name in blocked_functions:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        
        # Allow only safe pragmas
        if action == sqlite3.SQLITE_PRAGMA:
            pragma_name = (arg1 or "").lower()
            # data_version only allowed when arg2 is None (FTS5 needs it)
            if pragma_name == 'data_version' and arg2 is None:
                return sqlite3.SQLITE_OK
            # Other allowed pragmas can have arguments
            allowed_pragmas = {
                'table_info', 'table_xinfo', 'foreign_key_list', 
                'index_list', 'index_info'
            }
            if pragma_name in allowed_pragmas:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        
        # Deny everything else
        return sqlite3.SQLITE_DENY
    
    conn.set_authorizer(authorizer)
    
    # Verify it's a valid SQLite database
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlite3.Error as e:
        conn.close()
        error_name = getattr(e, 'sqlite_errorname', '')
        if error_name == 'SQLITE_CANTOPEN' or error_name.startswith('SQLITE_READONLY'):
            raise InspectError("WAL database without -shm access; copy it and use --frozen") from e
        raise InspectError("not a SQLite database") from e
    
    return conn


def _quote_identifier(name):
    """Quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _is_date_value(value):
    """Check if a text value looks like a date using csvinspect logic."""
    from askdata.csvinspect import _is_date
    return _is_date(value)


def inspect_sqlite(path, max_rows=1_000_000, deadline=None, frozen=False):
    """Inspect a SQLite database and return analysis.
    
    Args:
        path: Path to the SQLite database file
        max_rows: Maximum rows to analyze per table (default 1,000,000)
        deadline: Optional time.monotonic() deadline for timeout
        frozen: If True, treat database as unchanging (immutable=1)
        
    Returns:
        Dictionary with database analysis including tables, relationships, and warnings
    """
    path = Path(path)
    
    conn = None
    timed_out = False
    
    try:
        conn = open_readonly(path, frozen=frozen)
        
        result = {
            "kind": "sqlite",
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "tables": [],
            "relationships": [],
            "warnings": []
        }
        
        # Set up progress handler for deadline
        if deadline is not None:
            def progress_handler():
                if time.monotonic() > deadline:
                    return 1  # Abort
                return 0  # Continue
            conn.set_progress_handler(progress_handler, 10000)
        
        # Get all tables and views from sqlite_master
        cursor = conn.execute("""
            SELECT name, type 
            FROM sqlite_master 
            WHERE type IN ('table', 'view') 
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        objects = cursor.fetchall()
        
        if not objects:
            result["warnings"].append("no tables")
        
        for obj_name, obj_type in objects:
            # Check deadline before each object
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                break
            
            try:
                table_info = _analyze_table(conn, obj_name, obj_type, max_rows, deadline, result["warnings"])
                result["tables"].append(table_info)
                
                # Collect foreign key relationships
                for fk in table_info["foreign_keys"]:
                    relationship = [
                        f"{obj_name}.{fk['from']}",
                        f"{fk['table']}.{fk['to']}"
                    ]
                    result["relationships"].append(relationship)
                    
            except sqlite3.DatabaseError as e:
                # Check if this is a deadline timeout
                if deadline is not None and time.monotonic() > deadline:
                    timed_out = True
                    break
                # Otherwise, record the error and continue
                error_msg = str(e)
                table_info = {
                    "name": obj_name,
                    "type": obj_type,
                    "error": error_msg,
                    "columns": []
                }
                result["tables"].append(table_info)
                result["warnings"].append(f"{obj_name}: could not be read ({error_msg})")
        
        if timed_out and "timeout: statistics are partial" not in result["warnings"]:
            result["warnings"].append("timeout: statistics are partial")
            
    except sqlite3.OperationalError as e:
        # Only treat as timeout if deadline is set and passed
        if deadline is not None and time.monotonic() > deadline:
            if "timeout: statistics are partial" not in result["warnings"]:
                result["warnings"].append("timeout: statistics are partial")
        else:
            raise
        
    finally:
        if conn:
            conn.close()
    
    return result


def _analyze_table(conn, obj_name, obj_type, max_rows, deadline, warnings):
    """Analyze a single table or view."""
    quoted_name = _quote_identifier(obj_name)
    
    table_info = {
        "name": obj_name,
        "type": obj_type,
        "columns": []
    }
    
    # Get row count
    cursor = conn.execute(f"SELECT COUNT(*) FROM {quoted_name}")
    row_count = cursor.fetchone()[0]
    table_info["row_count"] = row_count
    
    # Determine if we need to sample
    sampled = row_count > max_rows
    table_info["sampled"] = sampled
    
    if sampled:
        warnings.append(f"{obj_name}: sampled first {max_rows} rows")
        src_query = f"SELECT * FROM {quoted_name} LIMIT {max_rows}"
    else:
        src_query = f"SELECT * FROM {quoted_name}"
    
    # Get column info from PRAGMA
    cursor = conn.execute(f"PRAGMA table_info({quoted_name})")
    pragma_columns = cursor.fetchall()
    
    # Get foreign keys (always include, even if empty)
    cursor = conn.execute(f"PRAGMA foreign_key_list({quoted_name})")
    fk_rows = cursor.fetchall()
    foreign_keys = []
    for fk_row in fk_rows:
        # fk_row: (id, seq, table, from, to, on_update, on_delete, match)
        foreign_keys.append({
            "from": fk_row[3],
            "table": fk_row[2],
            "to": fk_row[4]
        })
    table_info["foreign_keys"] = foreign_keys
    
    # Get indexes (always include, even if empty)
    cursor = conn.execute(f"PRAGMA index_list({quoted_name})")
    index_rows = cursor.fetchall()
    indexes = [row[1] for row in index_rows]  # row[1] is the index name
    table_info["indexes"] = indexes
    
    # Analyze each column
    for col_idx, pragma_col in enumerate(pragma_columns):
        # Check deadline before each column
        if deadline is not None and time.monotonic() > deadline:
            raise sqlite3.OperationalError("deadline exceeded")
        
        # pragma_col: (cid, name, type, notnull, dflt_value, pk)
        col_name = pragma_col[1]
        declared_type = pragma_col[2]
        notnull = bool(pragma_col[3])
        pk = bool(pragma_col[5])
        
        col_info = {
            "name": col_name,
            "declared_type": declared_type,
            "notnull": notnull,
            "pk": pk
        }
        
        # Analyze column statistics
        _analyze_column_stats(conn, src_query, col_name, sampled, obj_name, col_info, warnings)
        
        table_info["columns"].append(col_info)
    
    return table_info


def _analyze_column_stats(conn, src_query, col_name, sampled, obj_name, col_info, warnings):
    """Analyze statistics for a single column."""
    quoted_col = _quote_identifier(col_name)
    
    # Count missing values (NULL or empty string)
    cursor = conn.execute(f"""
        SELECT COUNT(*) FROM ({src_query})
        WHERE {quoted_col} IS NULL OR {quoted_col} = ''
    """)
    col_info["missing"] = cursor.fetchone()[0]
    
    # Get type distribution using typeof()
    cursor = conn.execute(f"""
        SELECT typeof({quoted_col}) as t, COUNT(*) as cnt
        FROM ({src_query})
        WHERE {quoted_col} IS NOT NULL AND {quoted_col} != ''
        GROUP BY t
    """)
    type_counts = cursor.fetchall()
    
    total_non_missing = sum(cnt for _, cnt in type_counts)
    
    if total_non_missing == 0:
        col_info["type"] = "text"
        col_info["candidate"] = None
        col_info["share"] = 0.0
        col_info["distinct"] = 0
        col_info["distinct_exact"] = not sampled
        col_info["samples"] = []
        col_info["numeric"] = None
        col_info["dates"] = None
        col_info["top"] = None
        return
    
    # Calculate shares over non-missing values
    type_shares = {}
    for type_name, cnt in type_counts:
        share = cnt / total_non_missing
        type_shares[type_name] = share
    
    # Map SQLite types to our types
    int_share = type_shares.get("integer", 0.0)
    real_share = type_shares.get("real", 0.0)
    text_share = type_shares.get("text", 0.0)
    blob_share = type_shares.get("blob", 0.0)
    
    float_share = int_share + real_share
    
    # Check date share for text values
    date_share = 0.0
    if text_share > 0:
        cursor = conn.execute(f"""
            SELECT {quoted_col} FROM ({src_query})
            WHERE {quoted_col} IS NOT NULL AND {quoted_col} != ''
              AND typeof({quoted_col}) = 'text'
            LIMIT 1000
        """)
        text_values = [row[0] for row in cursor.fetchall()]
        
        if text_values:
            date_count = sum(1 for v in text_values if _is_date_value(str(v)))
            date_fraction = date_count / len(text_values)
            date_share = text_share * date_fraction
    
    # Determine type and candidate using the new rules
    # First check >= 0.95 in order: int, float, date, blob
    if int_share >= 0.95:
        col_info["type"] = "int"
        col_info["candidate"] = "int"
        col_info["share"] = round(int_share, 3)
    elif float_share >= 0.95:
        col_info["type"] = "float"
        col_info["candidate"] = "float"
        col_info["share"] = round(float_share, 3)
    elif date_share >= 0.95:
        col_info["type"] = "date"
        col_info["candidate"] = "date"
        col_info["share"] = round(date_share, 3)
    elif blob_share >= 0.95:
        col_info["type"] = "blob"
        col_info["candidate"] = "blob"
        col_info["share"] = round(blob_share, 3)
    else:
        # Find highest share >= 0.5 (ties: int, float, date, blob order)
        shares_with_types = [
            (int_share, "int"),
            (float_share, "float"),
            (date_share, "date"),
            (blob_share, "blob")
        ]
        max_share, max_type = max(shares_with_types, key=lambda x: x[0])
        
        if max_share >= 0.5:
            col_info["type"] = "mixed"
            col_info["candidate"] = max_type
            col_info["share"] = round(max_share, 3)
            # Add warning
            percent = int(round(max_share * 100))
            warnings.append(f"column {obj_name}.{col_name} mixed: {percent}% {max_type}")
        else:
            col_info["type"] = "text"
            col_info["candidate"] = None
            col_info["share"] = round(text_share, 3)
    
    # Get distinct count
    cursor = conn.execute(f"""
        SELECT COUNT(DISTINCT {quoted_col}) FROM ({src_query})
        WHERE {quoted_col} IS NOT NULL AND {quoted_col} != ''
    """)
    col_info["distinct"] = cursor.fetchone()[0]
    col_info["distinct_exact"] = not sampled
    
    # Get samples (first 5 distinct non-null values)
    cursor = conn.execute(f"""
        SELECT DISTINCT {quoted_col} FROM ({src_query})
        WHERE {quoted_col} IS NOT NULL AND {quoted_col} != ''
        LIMIT 5
    """)
    samples = []
    for row in cursor.fetchall():
        value = row[0]
        if isinstance(value, bytes):
            samples.append(f"<blob {len(value)} bytes>")
        else:
            value_str = str(value)
            if len(value_str) > 80:
                value_str = value_str[:80]
            samples.append(value_str)
    col_info["samples"] = samples
    
    # Numeric statistics for int/float/mixed with numeric candidate
    if col_info["type"] in ("int", "float") or (col_info["type"] == "mixed" and col_info["candidate"] in ("int", "float")):
        cursor = conn.execute(f"""
            SELECT {quoted_col}
            FROM ({src_query})
            WHERE typeof({quoted_col}) IN ('integer', 'real')
            ORDER BY {quoted_col}
        """)
        numeric_values = [row[0] for row in cursor.fetchall()]
        
        if numeric_values:
            min_val = min(numeric_values)
            max_val = max(numeric_values)
            mean_val = sum(numeric_values) / len(numeric_values)
            median_val = statistics.median(numeric_values)
            
            col_info["numeric"] = {
                "min": min_val,
                "max": max_val,
                "mean": mean_val,
                "median": median_val
            }
        else:
            col_info["numeric"] = None
    else:
        col_info["numeric"] = None
    
    # Date statistics for date/mixed with date candidate
    if col_info["type"] == "date" or (col_info["type"] == "mixed" and col_info["candidate"] == "date"):
        cursor = conn.execute(f"""
            SELECT MIN({quoted_col}), MAX({quoted_col})
            FROM ({src_query})
            WHERE typeof({quoted_col}) = 'text'
        """)
        min_val, max_val = cursor.fetchone()
        if min_val is not None:
            col_info["dates"] = {
                "min": min_val,
                "max": max_val
            }
        else:
            col_info["dates"] = None
    else:
        col_info["dates"] = None
    
    # Top values for text/mixed when distinct <= 50
    if col_info["distinct"] <= 50 and col_info["type"] in ("text", "mixed"):
        cursor = conn.execute(f"""
            SELECT {quoted_col}, COUNT(*) as cnt
            FROM ({src_query})
            WHERE {quoted_col} IS NOT NULL AND {quoted_col} != ''
            GROUP BY {quoted_col}
            ORDER BY cnt DESC
            LIMIT 10
        """)
        top_values = []
        for value, count in cursor.fetchall():
            if isinstance(value, bytes):
                value_str = f"<blob {len(value)} bytes>"
            else:
                value_str = str(value)
                if len(value_str) > 80:
                    value_str = value_str[:80]
            top_values.append([value_str, count])
        col_info["top"] = top_values if top_values else None
    else:
        col_info["top"] = None
