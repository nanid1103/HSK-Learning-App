"""
Insert CSV vocabulary rows into the project's SQLite database (`database.db`).

Usage:
    python3 admin_scripts/csv_to_db.py vocab.csv

This assumes the DB has a table named `vocabulary` with columns roughly matching:
    id, hanzi, pinyin, meaning, hsk_level

Make a backup of your `database.db` before running.
"""

import sys
import sqlite3
import csv
import os

DB_PATH = os.environ.get('DATABASE_PATH', 'database.db')


def ensure_table_schema(conn):
    # Try to detect if table exists; if not, create a minimal table.
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vocabulary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hanzi TEXT NOT NULL,
        pinyin TEXT,
        meaning TEXT,
        hsk_level TEXT
    )
    """)
    conn.commit()


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 csv_to_db.py vocab.csv")
        sys.exit(1)

    csv_path = sys.argv[1]

    # Ensure parent directory exists (useful when DATABASE_PATH is on a mounted volume like /data)
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    ensure_table_schema(conn)

    inserted = 0
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            hanzi = row.get('hanzi') or row.get('word') or row.get('character')
            pinyin = row.get('pinyin', '')
            meaning = row.get('meaning', '')
            hsk = row.get('hsk_level') or row.get('level') or ''
            if not hanzi:
                print('Skipping row without hanzi:', row)
                continue
            conn.execute('INSERT INTO vocabulary (hanzi, pinyin, meaning, hsk_level) VALUES (?, ?, ?, ?)',
                         (hanzi, pinyin, meaning, hsk))
            inserted += 1
    conn.commit()
    conn.close()
    print(f'Inserted {inserted} rows into {DB_PATH}')


if __name__ == '__main__':
    main()
