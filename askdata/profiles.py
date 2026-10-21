"""Source profiles for the askdata query deny-list (DEC-427, DEC-431).

A profile is recognised by shape. It classifies every TEXT and BLOB column
it knows as "allow" (a safe group key) or "deny". In a profiled source, a
column that is not listed is denied unless its declared type has numeric
affinity (default deny). Loaded tables (CSV, JSONL) have no declared types,
so every unlisted column of a profiled loaded table is denied.

Loaded profiles have one table, keyed "*": its name is the source NAME.
"""
ALLOW, DENY = "allow", "deny"

ARORA_V2 = {
    "failures": {"url": DENY, "error": ALLOW, "at": ALLOW},
    "images": {"url": DENY, "site": ALLOW, "sha256": DENY, "content_type": ALLOW,
               "bytes_z": DENY, "fetched_at": ALLOW},
    "records": {"fetched_at": ALLOW, "published_at": ALLOW, "published_precision": ALLOW,
                "title": DENY, "content_hash": DENY, "html_z": DENY, "text_z": DENY},
    "refetch_queue": {"due_at": ALLOW, "reason": ALLOW},
    "runs": {"kind": ALLOW, "started_at": ALLOW, "finished_at": ALLOW, "summary_json": DENY},
    "sightings": {"fetched_at": ALLOW},
    "transcripts": {"vimeo_id": DENY, "source": ALLOW, "text": DENY},
    "urls": {"site": ALLOW, "url": DENY, "slug": DENY, "first_seen_at": ALLOW,
             "last_seen_at": ALLOW},
}

JOB_SCAN_HEADER = ["State", "City", "Employer", "Job Title", "Setting", "Schedule", "Pay",
                   "Posted", "Notes", "Apply link", "Status", "My Notes"]

JOB_SCAN = {"*": {
    "State": ALLOW, "City": ALLOW, "Employer": ALLOW, "Job Title": DENY, "Setting": ALLOW,
    "Schedule": ALLOW, "Pay": DENY, "Posted": ALLOW, "Notes": DENY, "Apply link": DENY,
    "Status": ALLOW, "My Notes": DENY,
    "_file": ALLOW, "_file_date": ALLOW, "job_title_h": ALLOW,
}}

GATE_KEYS = ["action", "decision", "mode", "principal", "reason", "resource", "rule_id",
             "span_id", "surface", "trace_id", "ts"]

GATE_DECISIONS = {"*": {
    "action": ALLOW, "decision": ALLOW, "mode": ALLOW, "principal": ALLOW, "reason": ALLOW,
    "resource": ALLOW, "rule_id": ALLOW, "span_id": DENY, "surface": ALLOW,
    "trace_id": DENY, "ts": ALLOW, "_file": ALLOW, "_file_date": ALLOW,
}}

PROFILES = {"arora_v2": ARORA_V2, "job_scan": JOB_SCAN, "gate_decisions": GATE_DECISIONS}

_FOLDED = {
    name: {table.casefold(): {col.casefold(): v for col, v in cols.items()}
           for table, cols in tables.items()}
    for name, tables in PROFILES.items()
}


def explicit(profile, table, column):
    """The listed verdict for (table, column) in profile, or None."""
    tables = _FOLDED.get(profile, {})
    return tables.get(table.casefold(), {}).get(column.casefold())


def _numeric_affinity(declared):
    """SQLite affinity rules: INTEGER, REAL and NUMERIC are numeric."""
    t = (declared or "").upper()
    if "INT" in t:
        return True
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return False
    if "BLOB" in t or not t:
        return False
    return True


def verdict(profile, table, column, declared=""):
    """allow/deny for a column of a profiled source (default deny)."""
    found = explicit(profile, table, column)
    if found is not None:
        return found
    return ALLOW if _numeric_affinity(declared) else DENY


def detect_sqlite(table_names):
    names = {t.casefold() for t in table_names}
    return "arora_v2" if {"records", "urls", "runs"} <= names else None


def detect_csv(header):
    # Superset match: an added column cannot take a file out of its profile.
    names = {h.strip().casefold() for h in header}
    return "job_scan" if {h.casefold() for h in JOB_SCAN_HEADER} <= names else None


def detect_jsonl(keys):
    names = {k.casefold() for k in keys}
    return "gate_decisions" if set(GATE_KEYS) <= names else None
