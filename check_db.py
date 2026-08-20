import sqlite3

conn = sqlite3.connect("database.db")

tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()

print("Jadvallar:")
for table in tables:
    print(table[0])

conn.close()