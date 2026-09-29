"""API 路由聚合（供 main.py 挂载 /api/v1）。"""
from app.api.v1 import api_router

__all__ = ["api_router"]
