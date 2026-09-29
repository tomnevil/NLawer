@echo off
rem 本地启动后端（8000）：环境变量把 DATABASE_URL 覆盖回 SQLite，
rem 无需 Docker/Postgres（.env 里的 5433 连不上是上次启动失败的根因）。
rem pydantic-settings 优先级：环境变量 > .env 文件，因此无需改 .env。
cd /d %~dp0
set DATABASE_URL=sqlite+aiosqlite:///./storage/nlawer.db
set DATABASE_URL_SYNC=sqlite:///./storage/nlawer.db
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > uvicorn_8000.out.log 2> uvicorn_8000.err.log
