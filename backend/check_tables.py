import sqlite3

c = sqlite3.connect("storage/_mig_check.db")
rows = c.execute("select name from sqlite_master where type='table' order by name").fetchall()
names = [r[0] for r in rows]
print("table_count", len(names))
print(names)
