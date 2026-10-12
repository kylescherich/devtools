"""SQLite database inspection and analysis."""
import sqlite3
import urllib.parse
import time
from pathlib import Path


class InspectError(Exception):
    """Error during database inspection."""
    pass


def open_readonly(path):
    """Open a SQLite database in read-only mode with security restrictions.
    
    Args:
        path: Path to the database file
        
    Returns:
        sqlite3.Connection object configured for read-only access
        
    Raises:
        InspectError: If path is a symlink, not a file, or not a valid database
    """
    path = Path(path)
    
    # Refuse symlinks and non-files
    if path.is_symlink():
        raise InspectError("symlinks are not allowed")
    if not path.is_file():
        raise InspectError("path is not a file")
    
    # Build read-only URI
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro&immutable=1"
    
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError as e:
        raise InspectError("not a SQLite database") from e
    
    # Enable query-only mode
    conn.execute("PRAGMA query_only=1")
    
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
    
    return conn


def _quote_identifier(name):
    """Quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _is_date_value(value):
    """Check if a text value looks like a date using csvinspect logic."""
    from askdata.csvinspect import _is_date
    return _is_date(value)


def inspect_sqlite(path, max_rows=1_000_000, deadline=None):
    """Inspect a SQLite database and return analysis.
    
    Args:
        path: Path to the SQLite database file
        max_rows: Maximum rows to analyze per table (default 1,000,000)
        deadline: Optional time.monotonic() deadline for timeout
        
    Returns:
        Dictionary with database analysis including tables, relationships, and warnings
    """
    path = Path(path)
    result = {
        "kind": "sqlite",
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "tables": [],
        "relationships": [],
        "warnings": []
    }
    
    conn = None
    timed_out = False
    
    try:
        conn = open_readonly(path)
        
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
            try:
                table_info = _analyze_table(conn, obj_name, obj_type, max_rows, result["warnings"])
                result["tables"].append(table_info)
                
                # Collect foreign key relationships
                for fk in table_info.get("foreign_keys", []):
                    relationship = [
                        f"{obj_name}.{fk['from']}",
                        f"{fk['table']}.{fk['to']}"
                    ]
                    result["relationships"].append(relationship)
                    
            except sqlite3.OperationalError:
                # Timeout occurred
                timed_out = True
                break
        
        if timed_out:
            result["warnings"].append("timeout: statistics are partial")
            
    except sqlite3.OperationalError:
        # Timeout during initial queries
        result["warnings"].append("timeout: statistics are partial")
        
    finally:
        if conn:
            conn.close()
    
    return result


def _analyze_table(conn, obj_name, obj_type, max_rows, warnings):
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
    
    # Get foreign keys
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
    if foreign_keys:
        table_info["foreign_keys"] = foreign_keys
    
    # Get indexes
    cursor = conn.execute(f"PRAGMA index_list({quoted_name})")
    index_rows = cursor.fetchall()
    indexes = [row[1] for row in index_rows]  # row[1] is the index name
    if indexes:
        table_info["indexes"] = indexes
    
    # Analyze each column
    for col_idx, pragma_col in enumerate(pragma_columns):
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
        _analyze_column_stats(conn, src_query, col_name, sampled, col_info)
        
        table_info["columns"].append(col_info)
    
    return table_info


def _analyze_column_stats(conn, src_query, col_name, sampled, col_info):
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
        col_info["distinct"] = 0
        col_info["distinct_exact"] = not sampled
        col_info["samples"] = []
        return
    
    # Determine type based on typeof() distribution
    type_shares = {}
    for type_name, cnt in type_counts:
        share = cnt / total_non_missing
        type_shares[type_name] = share
    
    # Map SQLite types to our types
    int_share = type_shares.get("integer", 0)
    real_share = type_shares.get("real", 0)
    text_share = type_shares.get("text", 0)
    blob_share = type_shares.get("blob", 0)
    
    # Determine type
    if blob_share >= 0.95:
        col_info["type"] = "blob"
        col_info["candidate"] = "blob"
    elif int_share >= 0.95:
        col_info["type"] = "int"
        col_info["candidate"] = "int"
    elif int_share + real_share >= 0.95:
        col_info["type"] = "float"
        col_info["candidate"] = "float"
    elif text_share >= 0.95:
        # Check if it's a date column
        cursor = conn.execute(f"""
            SELECT {quoted_col} FROM ({src_query})
            WHERE {quoted_col} IS NOT NULL AND {quoted_col} != ''
              AND typeof({quoted_col}) = 'text'
            LIMIT 1000
        """)
        text_values = [row[0] for row in cursor.fetchall()]
        
        date_count = sum(1 for v in text_values if _is_date_value(str(v)))
        if text_values and date_count / len(text_values) >= 0.95:
            col_info["type"] = "date"
            col_info["candidate"] = "date"
        else:
            col_info["type"] = "text"
            col_info["candidate"] = "text"
    elif max(int_share, real_share, text_share, blob_share) >= 0.5:
        col_info["type"] = "mixed"
        col_info["candidate"] = None
    else:
        col_info["type"] = "text"
        col_info["candidate"] = None
    
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
    
    # Numeric statistics for int/float
    if col_info["type"] in ("int", "float"):
        cursor = conn.execute(f"""
            SELECT MIN({quoted_col}), MAX({quoted_col}), AVG({quoted_col})
            FROM ({src_query})
            WHERE typeof({quoted_col}) IN ('integer', 'real')
        """)
        min_val, max_val, mean_val = cursor.fetchone()
        if min_val is not None:
            col_info["min"] = min_val
            col_info["max"] = max_val
            col_info["mean"] = mean_val
            
            # Get median
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM ({src_query})
                WHERE typeof({quoted_col}) IN ('integer', 'real')
            """)
            numeric_count = cursor.fetchone()[0]
            
            if numeric_count > 0:
                median_offset = numeric_count // 2
                cursor = conn.execute(f"""
                    SELECT {quoted_col} FROM ({src_query})
                    WHERE typeof({quoted_col}) IN ('integer', 'real')
                    ORDER BY {quoted_col}
                    LIMIT 1 OFFSET {median_offset}
                """)
                median_row = cursor.fetchone()
                if median_row:
                    col_info["median"] = median_row[0]
    
    # Date statistics
    if col_info["type"] == "date":
        cursor = conn.execute(f"""
            SELECT MIN({quoted_col}), MAX({quoted_col})
            FROM ({src_query})
            WHERE typeof({quoted_col}) = 'text'
        """)
        min_val, max_val = cursor.fetchone()
        if min_val is not None:
            col_info["min"] = min_val
            col_info["max"] = max_val
    
    # Top values for text/bool/mixed when distinct <= 50
    if col_info["distinct"] <= 50 and col_info["type"] in ("text", "bool", "mixed"):
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
        if top_values:
            col_info["top"] = top_values
