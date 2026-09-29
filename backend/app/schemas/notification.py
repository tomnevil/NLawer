"""通知读路径 DTO（P0-15）。

写入侧（`notification_service.notify`）自项目初期即存在，但**从未有读取侧**：
`is_read` / `read_at` 两个字段在全库范围内没有任何赋值点，前端铃铛是硬编码
常亮的死按钮。本模块补齐读取侧的请求/响应契约。
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    """单条通知。

    `is_read` 在库中是 `Integer(0/1)`（历史建模如此，SQLite/PG 双方言可用），
    此处由 Pydantic 统一收敛为 `bool`，避免前端拿到 0/1 还需自行判断。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    content: Optional[str] = None
    #: 关联业务对象，前端据此决定点击跳转目标
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    payload: Optional[dict[str, Any]] = None
    is_read: bool = False
    read_at: Optional[str] = None
    created_at: Optional[datetime] = None


class UnreadCountOut(BaseModel):
    """未读数。

    `total` 为当前用户全部未读；`by_type` 供前端做分类角标
    （例如律师端「待复核」Tab 单独显示数量）；`latest_id` 供轮询时
    廉价判断「有没有新通知」，避免每 30 秒无谓重渲染整个列表
    （移动端会打断用户滚动位置）。
    """

    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    latest_id: int = 0


class ReadBatchIn(BaseModel):
    """批量标记已读。

    `ids` 上限 200：与 `pagination.COUNT_CAP` 同口径，避免一次请求
    构造出无界 `IN (...)` 语句。留空表示「全部标记已读」。
    """

    ids: list[int] = Field(default_factory=list, max_length=200)
    #: 仅标记指定类型（与 `ids` 互斥，`ids` 非空时忽略）
    type: Optional[str] = None


class ReadResultOut(BaseModel):
    """标记已读的结果。

    回传 `updated` 是幂等性的可观测证据：重复调用同一批 id，
    第二次 `updated` 必须为 0，而不是报错。
    """

    updated: int = 0
    unread_total: int = 0
