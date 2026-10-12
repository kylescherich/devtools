"""Fixture helpers for askdata tests."""
from pathlib import Path


def _quote_sql_identifier(name):
    """Quote a SQL identifier by doubling internal quotes."""
    return '"' + name.replace('"', '""') + '"'


def clean_csv(directory):
    """Create a clean CSV with 200 rows of various types.
    
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / "clean.csv"
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write("id,price,created,active,name,token\n")
        for i in range(200):
            f.write(f"{i},{10.5 + i * 0.1},2024-{(i % 12) + 1:02d}-15,")
            f.write(f"{'true' if i % 2 == 0 else 'false'},")
            f.write(f"item_{i},tok-{i}\n")
    
    return path


def messy_csv(directory):
    """Create a messy CSV with various issues.
    
    - Blank 3rd header
    - Quoted value "a, b"
    - 2 ragged rows at csv lines 5 and 9
    - cp1252 byte for café
    
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / "messy.csv"
    
    with open(path, 'wb') as f:
        # Header with blank 3rd column
        f.write(b"col1,col2,,col4\n")
        # Line 2
        f.write(b"1,2,3,4\n")
        # Line 3 - quoted value with comma
        f.write(b'5,6,"a, b",8\n')
        # Line 4
        f.write(b"9,10,11,12\n")
        # Line 5 - ragged (too few columns)
        f.write(b"13,14\n")
        # Line 6
        f.write(b"15,16,17,18\n")
        # Line 7
        f.write(b"19,20,21,22\n")
        # Line 8 - cp1252 café
        f.write(b"23,24,caf\xe9,26\n")
        # Line 9 - ragged (too many columns)
        f.write(b"27,28,29,30,31\n")
        # Line 10
        f.write(b"32,33,34,35\n")
    
    return path


def empty_csv(directory):
    """Create an empty CSV file.
    
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / "empty.csv"
    
    with open(path, 'w', encoding='utf-8') as f:
        pass
    
    return path


def header_only_csv(directory):
    """Create a CSV with only a header row.
    
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / "header_only.csv"
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write("col1,col2,col3\n")
    
    return path


def semicolon_csv(directory):
    """Create a CSV with semicolon delimiter.
    
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / "semicolon.csv"
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write("a;b;c\n")
        f.write("1;2;3\n")
        f.write("4;5;6\n")
    
    return path


def mixed_csv(directory, int_share):
    """Create a CSV with mixed int/text column.
    
    Args:
        directory: Directory to create file in
        int_share: Fraction of rows that should be integers (0.0 to 1.0)
        
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / f"mixed_{int(int_share*100)}.csv"
    
    num_rows = 100
    num_ints = int(num_rows * int_share)
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write("mixed_col\n")
        for i in range(num_rows):
            if i < num_ints:
                f.write(f"{i}\n")
            else:
                f.write(f"word_{i}\n")
    
    return path


def fk_db(directory):
    """Create a SQLite database with foreign keys, indexes, and a view.
    
    Returns path to the created file.
    """
    import sqlite3
    from datetime import date, timedelta
    
    directory = Path(directory)
    path = directory / "fk.db"
    
    conn = sqlite3.connect(str(path))
    
    # Create customers table
    conn.execute("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT
        )
    """)
    
    # Insert 50 customers
    for i in range(50):
        conn.execute(
            "INSERT INTO customers (name, email) VALUES (?, ?)",
            (f"Customer {i}", f"customer{i}@example.com")
        )
    
    # Create orders table with foreign key
    conn.execute("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id),
            amount REAL,
            placed TEXT
        )
    """)
    
    # Create index
    conn.execute("CREATE INDEX idx_orders_customer ON orders(customer_id)")
    
    # Insert 300 orders with dates spread across 2024
    start_date = date(2024, 1, 1)
    for i in range(300):
        customer_id = i % 50
        amount = 10.0 + (i * 1.5) % 100
        days_offset = (i * 365) // 300
        order_date = start_date + timedelta(days=days_offset)
        
        conn.execute(
            "INSERT INTO orders (customer_id, amount, placed) VALUES (?, ?, ?)",
            (customer_id, amount, order_date.isoformat())
        )
    
    # Create view
    conn.execute("""
        CREATE VIEW big_orders AS
        SELECT * FROM orders WHERE amount > 50
    """)
    
    conn.commit()
    conn.close()
    
    return path


def wal_db(directory):
    """Create a SQLite database in WAL mode with an open writer.
    
    Returns tuple of (path, writer_connection).
    The caller must close the writer connection.
    """
    import sqlite3
    
    directory = Path(directory)
    path = directory / "wal.db"
    
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    
    # Insert 10 rows and commit
    for i in range(10):
        conn.execute("INSERT INTO t (v) VALUES (?)", (f"value_{i}",))
    conn.commit()
    
    # Return path and open connection
    return path, conn


def fts_db(directory):
    """Create a SQLite database with FTS5 table and regular table.
    
    Returns path to the created file.
    """
    import sqlite3
    
    directory = Path(directory)
    path = directory / "fts.db"
    
    conn = sqlite3.connect(str(path))
    
    # Create FTS5 table
    conn.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
    conn.execute("INSERT INTO docs (body) VALUES ('first document')")
    conn.execute("INSERT INTO docs (body) VALUES ('second document')")
    conn.execute("INSERT INTO docs (body) VALUES ('third document')")
    
    # Create regular table
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t (x) VALUES (42)")
    
    conn.commit()
    conn.close()
    
    return path


def hostile_view_db(directory):
    """Create a SQLite database with hostile views that try to execute dangerous operations.
    
    Returns path to the created file.
    """
    import sqlite3
    
    directory = Path(directory)
    path = directory / "hostile_view.db"
    
    conn = sqlite3.connect(str(path))
    
    # Create regular table
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t (x) VALUES (1)")
    
    # Create hostile views
    conn.execute("CREATE VIEW bad AS SELECT load_extension('x') AS y")
    conn.execute("CREATE VIEW bad2 AS SELECT writefile('/tmp/askdata-should-not-exist', 'a') AS y")
    
    conn.commit()
    conn.close()
    
    return path


def empty_db(directory):
    """Create a valid SQLite database with no tables.
    
    Returns path to the created file.
    """
    import sqlite3
    
    directory = Path(directory)
    path = directory / "empty.db"
    
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()
    
    return path


def hostile_db(directory):
    """Create a SQLite database with hostile names and values.
    
    Returns path to the created file.
    """
    import sqlite3
    
    directory = Path(directory)
    path = directory / "hostile.db"
    
    conn = sqlite3.connect(str(path))
    
    # Table with quotes and HTML in name
    table_name = 't"<script>'
    col_name = 'c"<b>'
    
    quoted_table = _quote_sql_identifier(table_name)
    quoted_col = _quote_sql_identifier(col_name)
    
    conn.execute(f'CREATE TABLE {quoted_table} ({quoted_col} TEXT)')
    
    # Insert hostile values
    conn.execute(
        f'INSERT INTO {quoted_table} ({quoted_col}) VALUES (?)',
        ('<img src=x onerror=alert(1)>',)
    )
    conn.execute(
        f'INSERT INTO {quoted_table} ({quoted_col}) VALUES (?)',
        ('normal value',)
    )
    conn.execute(
        f'INSERT INTO {quoted_table} ({quoted_col}) VALUES (?)',
        ('<script>alert("xss")</script>',)
    )
    
    conn.commit()
    conn.close()
    
    return path


def not_a_db(directory):
    """Create a text file with .db extension.
    
    Returns path to the created file.
    """
    directory = Path(directory)
    path = directory / "fake.db"
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write("This is not a SQLite database.\n")
        f.write("Just a plain text file.\n")
    
    return path


def mixed_db(directory, int_share):
    """Create a SQLite database with mixed int/text column.
    
    Args:
        directory: Directory to create file in
        int_share: Fraction of rows that should be integers (0.0 to 1.0)
        
    Returns path to the created file.
    """
    import sqlite3
    
    directory = Path(directory)
    path = directory / f"mixed_{int(int_share*100)}.db"
    
    conn = sqlite3.connect(str(path))
    
    # Create table with mixed column (no declared type)
    conn.execute("CREATE TABLE m (v)")
    
    # Insert 100 rows
    num_rows = 100
    num_ints = int(num_rows * int_share)
    
    for i in range(num_rows):
        if i < num_ints:
            conn.execute("INSERT INTO m (v) VALUES (?)", (i,))
        else:
            conn.execute("INSERT INTO m (v) VALUES (?)", (f"word_{i}",))
    
    conn.commit()
    conn.close()
    
    return path
