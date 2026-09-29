import re, pathlib

p = pathlib.Path("alembic/versions/031c74c6de97_initial_schema.py")
s = p.read_text(encoding="utf-8")

# 1) embedding 列按方言分支：Postgres 用 Vector(1536)，其它（含 SQLite）用 Text
old_embed = "sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),"
new_embed = (
    "sa.Column('embedding',"
    " Vector(1536) if op.get_bind().dialect.name == 'postgresql' else sa.Text(),"
    " nullable=True),"
)
assert old_embed in s, "embedding line not found"
s = s.replace(old_embed, new_embed)

# 2) 所有外键改为 use_alter=True（建表后由 ALTER 添加，避免 FK 顺序/循环依赖问题）
s = re.sub(r"sa\.ForeignKeyConstraint\(([^)]*)\)", r"sa.ForeignKeyConstraint(\1, use_alter=True)", s)

p.write_text(s, encoding="utf-8")
print("patched migration: embedding dialect-aware + use_alter on all FKs")
