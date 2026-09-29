import pathlib

p = pathlib.Path("alembic/versions/031c74c6de97_initial_schema.py")
s = p.read_text(encoding="utf-8")
n = s.count(", , use_alter=True")
s = s.replace(", , use_alter=True)", ", use_alter=True)")
p.write_text(s, encoding="utf-8")
print("fixed double-comma FK constraints:", n)
