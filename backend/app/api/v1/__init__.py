"""/api/v1 路由聚合。

各业务模块按阶段逐步挂载。新增模块时在此 include_router 即可。
"""
from fastapi import APIRouter

from app.api.v1 import (
    analyses,
    archives,
    audit_retention,
    auth,
    billing,
    cases,
    complaints,
    compliance,
    conversations,
    dispatches,
    documents,
    evidence,
    files,
    jobs,
    knowledge,
    notifications,
    qa,
    reviews,
    ws,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(jobs.router)
api_router.include_router(conversations.router)
api_router.include_router(cases.router)
api_router.include_router(dispatches.router)
api_router.include_router(analyses.router)
api_router.include_router(evidence.router)
api_router.include_router(reviews.router)
api_router.include_router(archives.router)
api_router.include_router(qa.router)
api_router.include_router(documents.router)
api_router.include_router(compliance.router)
api_router.include_router(knowledge.router)
api_router.include_router(billing.router)
api_router.include_router(files.router)
api_router.include_router(complaints.router)
api_router.include_router(audit_retention.router)
api_router.include_router(notifications.router)
api_router.include_router(ws.router)

__all__ = ["api_router"]
