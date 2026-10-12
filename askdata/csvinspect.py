"""CSV file inspection and analysis."""
import csv
import os
import random
import re
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median

# Reservoir size for approximate median calculation
RESERVOIR_SIZE = 100_000


def inspect_csv(path, max_bytes=500_000_000, max_rows=1_000_000, deadline=None, samples=False):
    """Inspect a CSV file and return analysis results.
    
    Args:
        path: Path to the CSV file
        max_bytes: Maximum file size in bytes (default 500MB)
        max_rows: Maximum number of data rows to process (default 1M)
        deadline: Optional time.monotonic() value to stop processing
        samples: Whether to collect sample values and top values (default False)
        
    Returns:
        dict with keys: kind, name, size_bytes, encoding, delimiter, has_header,
        row_count, rows_analyzed, malformed_skipped, physical_rows, physical_rows_exact,
        sampled, samples_enabled, columns, malformed, warnings
    """
    path = Path(path)
    size_bytes = path.stat().st_size
    
    # Try UTF-8 first, fallback to cp1252 on any UnicodeDecodeError
    encoding = "utf-8"
    try:
        return _inspect_csv_with_encoding(path, size_bytes, encoding, max_bytes, max_rows, deadline, samples)
    except UnicodeDecodeError:
        encoding = "cp1252"
        return _inspect_csv_with_encoding(path, size_bytes, encoding, max_bytes, max_rows, deadline, 
                                         samples, encoding_fallback=True)


def _inspect_csv_with_encoding(path, size_bytes, encoding, max_bytes, max_rows, deadline, samples, encoding_fallback=False):
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
            has_header = True
    
    # Process the file
    with open(path, 'r', encoding=encoding if encoding == 'cp1252' else 'utf-8-sig', newline='') as f:
        reader = csv.reader(f, delimiter=delimiter)
        
        # Read first row
        try:
            first_row = next(reader)
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
                "rows_analyzed": 0,
                "malformed_skipped": 0,
                "physical_rows": 0,
                "physical_rows_exact": True,
                "sampled": False,
                "samples_enabled": samples,
                "columns": [],
                "malformed": {"count": 0, "first_lines": []},
                "warnings": warnings + ["empty file"]
            }
        
        # Determine header and num_cols
        if has_header:
            header = first_row
            num_cols = len(header)
            
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
        else:
            # Headerless: use col1, col2, ... colN
            num_cols = len(first_row)
            header = [f"col{i+1}" for i in range(num_cols)]
        
        # Initialize column statistics with streaming stats
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
                # Streaming numeric stats
                "numeric_count": 0,
                "numeric_min": None,
                "numeric_max": None,
                "numeric_mean": 0.0,
                "numeric_m2": 0.0,  # For Welford's algorithm
                "numeric_reservoir": [],
                "numeric_reservoir_rng": random.Random(0),
                "numeric_reservoir_seen": 0,
                # Streaming date stats
                "date_min": None,
                "date_max": None,
            }
            for name in header
        }
        
        malformed_count = 0
        malformed_lines = []
        rows_analyzed = 0
        physical_rows = 0
        timed_out = False
        row_limited = False
        counting_only = False
        physical_rows_exact = True
        
        # Process first row if headerless
        if not has_header:
            # Check for malformed
            if len(first_row) != num_cols:
                malformed_count += 1
                if len(malformed_lines) < 3:
                    malformed_lines.append(reader.line_num)
                physical_rows += 1
            else:
                rows_analyzed += 1
                physical_rows += 1
                
                # Process each column value
                for col_name, value in zip(header, first_row):
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
                    
                    # Collect samples (first 5 distinct) only if enabled
                    if samples and len(col_data["samples"]) < 5 and value not in [s[:80] for s in col_data["samples"]]:
                        col_data["samples"].append(value[:80])
                    
                    # Type detection and streaming stats
                    _test_types_and_stats(value.strip(), col_data)
        
        for row in reader:
            # Check deadline every 10,000 rows (including 0)
            if not timed_out and physical_rows % 10000 == 0:
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    physical_rows_exact = False
                    warnings.append(f"timeout after {rows_analyzed} rows; statistics are partial")
                    break
            
            # Check for malformed rows
            if len(row) != num_cols:
                malformed_count += 1
                if len(malformed_lines) < 3:
                    malformed_lines.append(reader.line_num)
                physical_rows += 1
                continue
            
            physical_rows += 1
            
            # Check max_rows - if we've hit the limit, switch to counting only
            if rows_analyzed >= max_rows:
                if not row_limited:
                    row_limited = True
                    counting_only = True
                    if not size_warning_added:
                        warnings.append(f"sampled: first {max_rows} rows only")
                # Continue counting physical rows
                continue
            
            rows_analyzed += 1
            
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
                
                # Collect samples (first 5 distinct) only if enabled
                if samples and len(col_data["samples"]) < 5 and value not in [s[:80] for s in col_data["samples"]]:
                    col_data["samples"].append(value[:80])
                
                # Type detection and streaming stats
                _test_types_and_stats(value.strip(), col_data)
        
        # Check if we sampled
        sampled = timed_out or row_limited or size_warning_added
        
        # Mark distinct_exact as False if sampled
        if sampled:
            for col_data in columns_data.values():
                col_data["distinct_exact"] = False
        
        # Add malformed warning
        if malformed_count > 0:
            warnings.append(f"{malformed_count} malformed rows (first lines: {malformed_lines})")
        
        # Build final columns list
        columns = []
        for col_name in header:
            col_data = columns_data[col_name]
            col_result = _analyze_column(col_data, rows_analyzed, warnings, samples)
            columns.append(col_result)
        
        return {
            "kind": "csv",
            "name": path.name,
            "size_bytes": size_bytes,
            "encoding": encoding,
            "delimiter": delimiter,
            "has_header": has_header,
            "row_count": rows_analyzed,  # For backward compatibility
            "rows_analyzed": rows_analyzed,
            "malformed_skipped": malformed_count,
            "physical_rows": physical_rows,
            "physical_rows_exact": physical_rows_exact,
            "sampled": sampled,
            "samples_enabled": samples,
            "columns": columns,
            "malformed": {
                "count": malformed_count,
                "first_lines": malformed_lines
            },
            "warnings": warnings
        }


def _test_types_and_stats(value, col_data):
    """Test value against different types, update counts, and streaming stats.
    
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
            num_val = float(value)
            _update_numeric_stats(col_data, num_val)
        except ValueError:
            pass
    
    # Float test
    try:
        f = float(value)
        if not (f != f or f == float('inf') or f == float('-inf')):  # finite check
            col_data["float_count"] += 1
            if not re.fullmatch(r'[+-]?\d+', value, re.ASCII):
                _update_numeric_stats(col_data, f)
    except ValueError:
        pass
    
    # Date test
    if _is_date(value):
        col_data["date_count"] += 1
        parsed = _parse_date(value)
        if parsed:
            if col_data["date_min"] is None or parsed < col_data["date_min"]:
                col_data["date_min"] = parsed
            if col_data["date_max"] is None or parsed > col_data["date_max"]:
                col_data["date_max"] = parsed


def _update_numeric_stats(col_data, value):
    """Update streaming numeric statistics using Welford's algorithm and reservoir sampling.
    
    Args:
        col_data: Column data dictionary to update
        value: Numeric value to add
    """
    # Update count
    col_data["numeric_count"] += 1
    n = col_data["numeric_count"]
    
    # Update min/max
    if col_data["numeric_min"] is None or value < col_data["numeric_min"]:
        col_data["numeric_min"] = value
    if col_data["numeric_max"] is None or value > col_data["numeric_max"]:
        col_data["numeric_max"] = value
    
    # Update mean using Welford's algorithm
    delta = value - col_data["numeric_mean"]
    col_data["numeric_mean"] += delta / n
    delta2 = value - col_data["numeric_mean"]
    col_data["numeric_m2"] += delta * delta2
    
    # Reservoir sampling for median
    col_data["numeric_reservoir_seen"] += 1
    if len(col_data["numeric_reservoir"]) < RESERVOIR_SIZE:
        col_data["numeric_reservoir"].append(value)
    else:
        # Replace with decreasing probability
        j = col_data["numeric_reservoir_rng"].randint(0, col_data["numeric_reservoir_seen"] - 1)
        if j < RESERVOIR_SIZE:
            col_data["numeric_reservoir"][j] = value


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


def _analyze_column(col_data, total_rows, warnings, samples):
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
            "samples": [] if samples else [],
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
    
    # Calculate numeric stats from streaming data
    numeric = None
    if final_type in ("int", "float") and col_data["numeric_count"] > 0:
        reservoir = col_data["numeric_reservoir"]
        median_approx = col_data["numeric_reservoir_seen"] > RESERVOIR_SIZE
        
        if reservoir:
            numeric = {
                "min": col_data["numeric_min"],
                "max": col_data["numeric_max"],
                "mean": col_data["numeric_mean"],
                "median": median(reservoir),
                "median_approx": median_approx
            }
    
    # Calculate date stats from streaming data
    dates = None
    if final_type == "date" and col_data["date_min"] is not None:
        dates = {
            "min": col_data["date_min"].isoformat(),
            "max": col_data["date_max"].isoformat()
        }
    
    # Calculate top values
    distinct = len(col_data["value_counter"]) if col_data["distinct_exact"] else len(col_data["value_counter"])
    top = None
    if samples and (col_data["distinct_exact"] and distinct <= 50 and 
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
        "samples": col_data["samples"] if samples else [],
        "numeric": numeric,
        "dates": dates,
        "top": top
    }
