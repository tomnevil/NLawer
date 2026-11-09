"""初始迁移的 embedding 列类型选择：缺 pgvector 时给出根因错误，不抛 TypeError。"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "031c74c6de97_initial_schema.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("nlawer_initial_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self):
        return self._value


class _FakeBind:
    def __init__(self, dialect: str, installed: int = 0) -> None:
        self.dialect = SimpleNamespace(name=dialect)
        self._installed = installed

    def execute(self, _stmt):  # noqa: ANN001
        return _FakeResult(self._installed)


def test_sqlite_uses_text(monkeypatch):
    mod = _load_migration_module()
    monkeypatch.setattr(mod.op, "get_bind", lambda: _FakeBind("sqlite"))
    assert isinstance(mod._embedding_column_type(), sa.Text)


def test_postgres_without_extension_uses_text(monkeypatch):
    mod = _load_migration_module()
    monkeypatch.setattr(mod.op, "get_bind", lambda: _FakeBind("postgresql", installed=0))
    assert isinstance(mod._embedding_column_type(), sa.Text)


def test_postgres_extension_without_python_package_raises_actionable_error(monkeypatch):
    mod = _load_migration_module()
    monkeypatch.setattr(mod.op, "get_bind", lambda: _FakeBind("postgresql", installed=1))
    monkeypatch.setattr(mod, "Vector", None)

    with pytest.raises(RuntimeError) as exc_info:
        mod._embedding_column_type()

    message = str(exc_info.value)
    assert "pgvector" in message
    assert "pip install" in message


def test_postgres_with_extension_and_package_returns_vector(monkeypatch):
    mod = _load_migration_module()
    monkeypatch.setattr(mod.op, "get_bind", lambda: _FakeBind("postgresql", installed=1))
    column_type = mod._embedding_column_type()
    assert getattr(column_type, "dim", None) == 1536
