"""CSV file inspection and analysis."""
import csv
import os
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median


def inspect_csv(path, max_bytes=500_000_000, max_rows=1_000_000, deadline=None):
    """Inspect a CSV file and return analysis results.
    
    Args:
        path: Path to the CSV file
        max_bytes: Maximum file size in bytes (default 500MB)
        max_rows: Maximum number of data rows to process (default 1M)
        deadline: Optional time.monotonic() value to stop processing
        
    Returns:
        dict with keys: kind, name, size_bytes, encoding, delimiter, has_header,
        row_count, sampled, columns, malformed, warnings
    """
    path = Path(path)
    size_bytes = path.stat().st_size
    
    # Try UTF-8 first, fallback to cp1252 on any UnicodeDecodeError
    encoding = "utf-8"
    try:
        return _inspect_csv_with_encoding(path, size_bytes, encoding, max_bytes, max_rows, deadline)
    except UnicodeDecodeError:
        encoding = "cp1252"
        return _inspect_csv_with_encoding(path, size_bytes, encoding, max_bytes, max_rows, deadline, 
                                         encoding_fallback=True)


def _inspect_csv_with_encoding(path, size_bytes, encoding, max_bytes, max_rows, deadline, encoding_fallback=False):
    """Internal function to inspect CSV with a specific encoding."""
    warnings = []
    
    if encoding_fallback:
        warnings.append("encoding fallback: UTF-8 failed, read as cp1252")
    
    # Check size guard
    size_warning_added = False
    if size_bytes > max_bytes:
        warnings.append(f"sampled: first {max_rows} rows only")
        size_warning_added = True
    
    # Read sample for dialect detection
    with open(path, 'r', encoding=encoding if encoding == 'cp1252' else 'utf-8-sig', newline='') as f:
        sample = f.read(65536)
    
    # Detect dialect
    delimiter = ','
    has_header = True
    
    if sample:
        try:
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(sample, delimiters=',;\t|')
            delimiter = dialect.delimiter
        except csv.Error:
            warnings.append("delimiter uncertain, assumed ','")
        
        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            warnings.append("header uncertain")
    
    # Process the file
    with open(path, 'r', encoding=encoding if encoding == 'cp1252' else 'utf-8-sig', newline='') as f:
        reader = csv.reader(f, delimiter=delimiter)
        
        # Read header
        try:
            header = next(reader)
        except StopIteration:
            # Empty file
            return {
                "kind": "csv",
                "name": path.name,
                "size_bytes": size_bytes,
                "encoding": encoding,
                "delimiter": delimiter,
                "has_header": has_header,
                "row_count": 0,
                "sampled": False,
                "columns": [],
                "malformed": {"count": 0, "first_lines": []},
                "warnings": warnings + ["empty file"]
            }
        
        # Fix blank and duplicate header names
        seen_names = {}
        fixed_header = []
        for i, name in enumerate(header):
            if not name.strip():
                name = f"column_{i+1}"
                warnings.append(f"blank header name replaced with {name}")
            
            if name in seen_names:
                seen_names[name] += 1
                name = f"{name}_{seen_names[name]}"
            else:
                seen_names[name] = 1
            
            fixed_header.append(name)
        
        header = fixed_header
        num_cols = len(header)
        
        # Initialize column statistics
        columns_data = {
            name: {
                "name": name,
                "non_missing": 0,
                "missing": 0,
                "value_counter": Counter(),
                "distinct_exact": True,
                "bool_count": 0,
                "int_count": 0,
                "float_count": 0,
                "date_count": 0,
                "samples": [],
                "numeric_values": [],
                "date_values": []
            }
            for name in header
        }
        
        malformed_count = 0
        malformed_lines = []
        row_count = 0
        timed_out = False
        row_limited = False
        
        # Check deadline before first row
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
        
        for row in reader:
            # Check deadline every 10,000 rows (including 0)
            if not timed_out and row_count % 10000 == 0:
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    warnings.append(f"timeout after {row_count} rows; statistics are partial")
                    break
            
            # Check max_rows - if we've hit the limit, check if there's another row
            if row_count >= max_rows:
                row_limited = True
                if not size_warning_added:
                    warnings.append(f"sampled: first {max_rows} rows only")
                break
            
            # Check for malformed rows
            if len(row) != num_cols:
                malformed_count += 1
                if len(malformed_lines) < 3:
                    malformed_lines.append(reader.line_num)
                continue
            
            row_count += 1
            
            # Process each column value
            for col_name, value in zip(header, row):
                col_data = columns_data[col_name]
                
                # Track missing values
                if not value.strip():
                    col_data["missing"] += 1
                    continue
                
                col_data["non_missing"] += 1
                
                # Track distinct values with Counter
                if col_data["distinct_exact"]:
                    if len(col_data["value_counter"]) < 100000:
                        col_data["value_counter"][value] += 1
                    else:
                        col_data["distinct_exact"] = False
                        if "distinct counts sampled" not in warnings:
                            warnings.append("distinct counts sampled")
                
                # Collect samples (first 5 distinct)
                if len(col_data["samples"]) < 5 and value not in [s[:80] for s in col_data["samples"]]:
                    col_data["samples"].append(value[:80])
                
                # Type detection
                _test_types(value.strip(), col_data)
        
        # Check if we sampled
        sampled = timed_out or row_limited
        
        # Add malformed warning
        if malformed_count > 0:
            warnings.append(f"{malformed_count} malformed rows (first lines: {malformed_lines})")
        
        # Build final columns list
        columns = []
        for col_name in header:
            col_data = columns_data[col_name]
            col_result = _analyze_column(col_data, row_count, warnings)
            columns.append(col_result)
        
        return {
            "kind": "csv",
            "name": path.name,
            "size_bytes": size_bytes,
            "encoding": encoding,
            "delimiter": delimiter,
            "has_header": has_header,
            "row_count": row_count,
            "sampled": sampled,
            "columns": columns,
            "malformed": {
                "count": malformed_count,
                "first_lines": malformed_lines
            },
            "warnings": warnings
        }


def _test_types(value, col_data):
    """Test value against different types and update counts.
    
    Args:
        value: The stripped value to test
        col_data: Column data dictionary to update
    """
    # Bool test
    if value.lower() in ('true', 'false', 'yes', 'no'):
        col_data["bool_count"] += 1
    
    # Int test - use regex for exact match
    if re.fullmatch(r'[+-]?\d+', value, re.ASCII):
        col_data["int_count"] += 1
        try:
            col_data["numeric_values"].append(float(value))
        except ValueError:
            pass
    
    # Float test
    try:
        f = float(value)
        if not (f != f or f == float('inf') or f == float('-inf')):  # finite check
            col_data["float_count"] += 1
            if not re.fullmatch(r'[+-]?\d+', value, re.ASCII):
                col_data["numeric_values"].append(f)
    except ValueError:
        pass
    
    # Date test
    if _is_date(value):
        col_data["date_count"] += 1
        col_data["date_values"].append(value)


def _is_date(value):
    """Check if value is a valid date."""
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        pass
    
    try:
        datetime.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        pass
    
    try:
        datetime.strptime(value, "%m/%d/%Y")
        return True
    except (ValueError, TypeError):
        pass
    
    return False


def _parse_date(value):
    """Parse a date value to a date object."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        pass
    
    try:
        return datetime.fromisoformat(value).date()
    except (ValueError, TypeError):
        pass
    
    try:
        return datetime.strptime(value, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        pass
    
    return None


def _analyze_column(col_data, total_rows, warnings):
    """Analyze column data and determine final type and statistics."""
    non_missing = col_data["non_missing"]
    
    if non_missing == 0:
        return {
            "name": col_data["name"],
            "type": "text",
            "candidate": None,
            "share": 0.0,
            "missing": col_data["missing"],
            "distinct": 0,
            "distinct_exact": col_data["distinct_exact"],
            "samples": [],
            "numeric": None,
            "dates": None,
            "top": None
        }
    
    # Calculate shares
    shares = {
        "bool": col_data["bool_count"] / non_missing,
        "int": col_data["int_count"] / non_missing,
        "float": col_data["float_count"] / non_missing,
        "date": col_data["date_count"] / non_missing
    }
    
    # Find best type (prefer bool > int > float > date on ties)
    best_type = max(shares.items(), key=lambda x: (x[1], ["bool", "int", "float", "date"].index(x[0]) * -1))
    candidate_type, share = best_type
    share = round(share, 3)
    
    # Determine final type and candidate
    if share >= 0.95:
        final_type = candidate_type
        candidate = candidate_type
    elif share >= 0.5:
        final_type = "mixed"
        candidate = candidate_type
        warnings.append(f"column {col_data['name']} mixed: {int(share*100)}% {candidate_type}")
    else:
        final_type = "text"
        candidate = None
    
    # Calculate numeric stats
    numeric = None
    if final_type in ("int", "float") and col_data["numeric_values"]:
        numeric = {
            "min": min(col_data["numeric_values"]),
            "max": max(col_data["numeric_values"]),
            "mean": mean(col_data["numeric_values"]),
            "median": median(col_data["numeric_values"])
        }
    
    # Calculate date stats
    dates = None
    if final_type == "date" and col_data["date_values"]:
        parsed_dates = [_parse_date(d) for d in col_data["date_values"]]
        parsed_dates = [d for d in parsed_dates if d is not None]
        if parsed_dates:
            dates = {
                "min": min(parsed_dates).isoformat(),
                "max": max(parsed_dates).isoformat()
            }
    
    # Calculate top values
    distinct = len(col_data["value_counter"]) if col_data["distinct_exact"] else len(col_data["value_counter"])
    top = None
    if (col_data["distinct_exact"] and distinct <= 50 and 
        final_type in ("text", "bool", "mixed")):
        top = [[value, count] for value, count in col_data["value_counter"].most_common(10)]
    
    return {
        "name": col_data["name"],
        "type": final_type,
        "candidate": candidate,
        "share": share,
        "missing": col_data["missing"],
        "distinct": distinct,
        "distinct_exact": col_data["distinct_exact"],
        "samples": col_data["samples"],
        "numeric": numeric,
        "dates": dates,
        "top": top
    }
