"""分页封装：统一 `?page=1&page_size=20` 与响应结构。

## ⚠️ 为什么 `total` 需要「有界计数」（本轮实测修复的性能缺陷）

直觉上「深分页（大 OFFSET）慢」，但**实测不成立**：本项目在 2 万行下
`OFFSET 19000` 反而比 `OFFSET 0` 更快（1.98ms vs 4.55ms），
原因是 `tenant_id` 索引已缩小工作集、且 `LIMIT` 会短路。**因此不要基于
"深分页慢"这一未经证实的假设去改 keyset 分页——收益为零。**

真正的缺陷在 **`COUNT(*)`**，因为**计数无法短路**：它必须扫描所有匹配行。
550,000 行实测（SQLite）：

| 查询 | 耗时 |
|------|------|
| 租户过滤 `COUNT` | 4.55 ms（走覆盖索引） |
| 租户 + 关键词 `LIKE` 的 `COUNT` | **114.95 ms** |
| 数据页本身（`LIKE` + `LIMIT 20`） | **0.53 ms** |

即 **计数比取数据贵 217 倍**，而用户等待的正是计数。

**为什么不能靠"少取几行"解决**：`LIKE '%kw%'` 前导通配无法用 B-Tree 索引，
只能逐行匹配；`LIMIT` 能让数据页在凑够 20 行后立刻停止，但 `COUNT` 必须走完全部。

**解决思路**：绝大多数场景下用户只需要知道"有多少页可翻"，
而非精确总数。因此改为**有界计数**——最多数 `COUNT_CAP` 行就停：

- 匹配行数 ≤ `COUNT_CAP` → 返回**精确值**（常见情况，如按租户过滤后本就只有几百条）
- 匹配行数 > `COUNT_CAP` → 返回 `COUNT_CAP` 并置 `total_is_lower_bound=True`，
  前端显示「200+」，`pages` 相应标记为下界

这样**精度只在"多到用户翻不完"时才降级**，而那种场景下精确值本身也无意义。
"""
from typing import Any, Dict, Generic, List, Optional, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

#: 有界计数的上限。超过即返回该值并标记为下界。
#: 取值依据：200 条 × 20/页 = 10 页，已远超用户实际翻页深度
#: （电商/内容类产品普遍观察到翻页集中在**前 3 页**）。
COUNT_CAP: int = 200


class PaginationParams:
    """通用分页参数依赖。"""

    def __init__(
        self,
        page: int = Query(1, ge=1, description="页码，从 1 开始"),
        page_size: int = Query(20, ge=1, le=200, description="每页条数，最大 200"),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class Page(BaseModel, Generic[T]):
    """分页响应体。"""

    items: List[T]
    total: int
    page: int
    page_size: int
    pages: int
    #: `total` 是否为下界（即实际匹配数 > COUNT_CAP）。
    #: 前端据此显示「200+」而非误导性的精确数字。
    total_is_lower_bound: bool = False

    @classmethod
    def build(
        cls,
        items: List[T],
        total: int,
        params: PaginationParams,
        *,
        total_is_lower_bound: bool = False,
    ) -> "Page[T]":
        pages = (total + params.page_size - 1) // params.page_size if total else 0
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=pages,
            total_is_lower_bound=total_is_lower_bound,
        )


async def count_bounded(
    db: AsyncSession, stmt: Any, cap: int = COUNT_CAP
) -> tuple[int, bool]:
    """有界计数：最多数 `cap` 行，返回 `(计数, 是否仅为下界)`。

    实现要点：用**子查询 + LIMIT** 让数据库在凑够 `cap` 行后停止扫描。

    ```sql
    SELECT count(*) FROM (SELECT ... FROM cases WHERE ... LIMIT 201) t
    ```

    取 `cap + 1` 是为了区分「恰好等于 cap」与「超过 cap」：
    数到 201 说明**确实还有更多**，返回 `(200, True)`；
    数到 200 说明**已经数完**，返回 `(200, False)`。若只取 200
    则两种情况无法区分，会把"正好 200 条"错报成下界。
    """
    bounded = select(func.count()).select_from(stmt.limit(cap + 1).subquery())
    counted = int((await db.execute(bounded)).scalar_one())
    if counted > cap:
        return cap, True
    return counted, False


def ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """成功响应体：{success, data}。"""
    payload: Dict[str, Any] = {"success": True, "data": data}
    payload.update(extra)
    return payload


def _paginate_meta(total: int, params: Optional[PaginationParams]) -> Dict[str, Any]:
    if params is None:
        return {}
    return {
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "pages": (total + params.page_size - 1) // params.page_size if total else 0,
    }
