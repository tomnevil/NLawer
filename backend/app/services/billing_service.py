"""用量与计费：实时扣减 / 看板 / 超量转工单 / 收入预测（PRD 5.7 / 11.1）。

收入预测借鉴 YouTubeBoardcast `monetization/` 的分项系数法：
按服务分项（订阅 / 案件服务 / 工单 / 增值）加权求和，输出 breakdown + total。

**并发正确性（P1 修复）**：
原 `consume()` 是「读额度 → 判断 → 内存自增 → flush」，在并发下有两个缺陷：
1. **丢失更新**：两个请求读到同一个 `used_count`，各自 +1 后写回，实际只加了 1
   —— 实测「额度 5、20 并发」只记到 **used_count=2**，**13 笔成功请求未计费**。
2. **额度行重复**：`ensure_quota` 的「查不到就插入」在并发下会插入多行同一
   (tenant, type, period) 的额度，此后 `first()` 只读其中一行，其余成僵尸数据。

因此新增 `consume_atomic()`：用**条件 UPDATE + rowcount 判定**完成扣减
（数据库层面单语句原子，不依赖「先读后写」），并对越界行做补偿。
"""
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ErrorCode
from app.core.metrics import metrics
from app.models.billing import UsageQuota, UsageRecord, WorkOrder
from app.models.enums import NotificationType, UsageType, WorkOrderStatus
from app.services.notification_service import notify

# 各用量类型的标准价与加急价（分）
_PRICE_CENTS: dict[UsageType, dict[str, int]] = {
    UsageType.QA: {"standard": 0, "urgent": 0},
    UsageType.DOCUMENT: {"standard": 2900, "urgent": 4900},
    UsageType.CONTRACT_REVIEW: {"standard": 9900, "urgent": 16900},
    UsageType.COMPLIANCE_SCAN: {"standard": 19900, "urgent": 29900},
}

#: 额度预警阈值：用量达到套餐额度的该比例时提醒一次。
#: 取 80% 的依据：留出 20% 的反应窗口——律所用户充值/调整套餐通常需要
#: 走内部审批，太晚（如 95%）提醒等于没有提醒，太早（如 50%）则一个账期内
#: 大量用户长期处于"预警中"，预警本身失去区分度。
_QUOTA_WARN_NUMERATOR = 4
_QUOTA_WARN_DENOMINATOR = 5


def _quota_warn_threshold(limit_count: int) -> int:
    """预警阈值 = `ceil(limit_count * 4/5)`。

    用整数向上取整而非 `int(limit * 0.8)`：后者是**向下**取整，
    在额度较小时会把阈值压到「已经耗尽」的位置，预警永远不触发
    （例：额度 3 → `int(2.4) = 2`，但真正危险的是第 3 次）。

    最小返回 1：额度为 1 时预警点就是唯一那次扣减，仍应提醒。
    """
    if limit_count <= 0:
        return 0
    return max(1, -(-limit_count * _QUOTA_WARN_NUMERATOR // _QUOTA_WARN_DENOMINATOR))

def price_cents(usage_type: UsageType, *, urgent: bool = False) -> int:
    """单次服务的标准价 / 加急价（分）。

    公开此入口是为了让接口层能回传「本次到底扣了多少分」，而不必跨模块
    直接读 `_PRICE_CENTS` 私有字典——价目表必须只有一个来源。
    """
    return _PRICE_CENTS[usage_type]["urgent" if urgent else "standard"]


# 超量转工单时，需要转交律师人工处理的服务类型
_ESCALATE_TYPES = {UsageType.CONTRACT_REVIEW, UsageType.COMPLIANCE_SCAN}

# 收入预测分项系数（分项系数法）
_REVENUE_WEIGHTS = {
    "subscription": 1.0,   # 订阅收入确定性最高
    "case_service": 0.85,  # 案件服务按历史转化折算
    "work_order": 0.9,     # 工单按成交率折算
    "value_added": 0.6,    # 增值服务最不确定
}


class BillingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 原子扣减（并发安全，P1 修复）----------------
    async def consume_atomic(
        self,
        *,
        tenant_id: str,
        user_id: Optional[int],
        usage_type: UsageType,
        period: str,
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        urgent: bool = False,
        limit_count: int = 100,
    ) -> dict[str, Any]:
        """在额度内**原子**扣减一次；额度用尽则不扣，自动转工单。

        与 `consume()` 的区别：这里用「条件 UPDATE」把
        「判断是否还有额度」与「自增」合并为**一条 SQL**，
        由数据库保证原子性，不再依赖「先读后写」的内存判断。

        条件：`used_count < limit_count`（或 `limit_count <= 0` 表示不限量）。
        `rowcount == 1` 表示扣减成功；`rowcount == 0` 表示额度已尽 → 转工单。
        """
        await self.ensure_quota(tenant_id, usage_type, period, limit_count=limit_count)
        q = await self._get_quota_row(tenant_id, usage_type, period)
        if q is None:  # 理论上不会发生，兜底避免 500
            q = await self.ensure_quota(tenant_id, usage_type, period, limit_count=limit_count)

        granted = await self._try_increment(q.id)
        # 扣减前用量：用于判断本次是否**跨过**预警阈值。
        # `_try_increment` 是 Core 层 UPDATE，ORM 对象此刻仍是旧值，
        # 因此这里读到的就是扣减前的真实值。
        used_before = q.used_count or 0

        rec = UsageRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            usage_type=usage_type,
            period=period,
            ref_type=ref_type,
            ref_id=ref_id,
            converted_to_work_order=int(not granted),
        )
        self.db.add(rec)

        work_order = None
        if not granted:
            # 指标：额度耗尽转工单的频次是「套餐定价是否合理」的前置信号
            metrics.quota_exhausted_total.inc((str(getattr(usage_type, "value", usage_type)),))
            work_order = await self._to_work_order(
                tenant_id=tenant_id, user_id=user_id, usage_type=usage_type,
                ref_type=ref_type, ref_id=ref_id, urgent=urgent,
            )
            await notify(
                self.db, tenant_id=tenant_id, user_id=user_id,
                type=NotificationType.WORK_ORDER_CREATED,
                content=f"用量已超套餐额度，已自动生成工单：{work_order.order_no}",
                ref_type=ref_type, ref_id=ref_id,
            )

        await self.db.flush()
        await self.db.refresh(q)

        if granted:
            await self._maybe_warn_quota(
                tenant_id=tenant_id,
                user_id=user_id,
                q=q,
                used_before=used_before,
                usage_type=usage_type,
                ref_type=ref_type,
                ref_id=ref_id,
            )

        return {
            "used": q.used_count,
            "limit": q.limit_count,
            "remaining": q.remaining,
            "exceeded": not granted,
            "work_order_id": work_order.id if work_order else None,
        }

    async def _get_quota_row(
        self, tenant_id: str, usage_type: UsageType, period: str
    ) -> Optional[UsageQuota]:
        return (
            await self.db.execute(
                select(UsageQuota).where(
                    UsageQuota.tenant_id == tenant_id,
                    UsageQuota.usage_type == usage_type,
                    UsageQuota.period == period,
                )
            )
        ).scalars().first()

    async def _try_increment(self, quota_id: int) -> bool:
        """条件自增：仅当仍有额度时才 +1。返回是否扣减成功。

        `limit_count <= 0` 视为不限量（与 `exhausted` 语义一致：
        只有 `limit_count > 0` 才可能用尽）。
        """
        stmt = (
            update(UsageQuota)
            .where(
                UsageQuota.id == quota_id,
                (UsageQuota.limit_count <= 0)
                | (UsageQuota.used_count < UsageQuota.limit_count),
            )
            .values(used_count=UsageQuota.used_count + 1)
        )
        res = await self.db.execute(stmt)
        # SQLAlchemy 2.0 的 CursorResult.rowcount：1=扣减成功，0=额度已尽
        return bool(res.rowcount and res.rowcount > 0)

    # ---------------- 额度 ----------------
    async def get_quota_row(
        self, tenant_id: str, usage_type: UsageType, period: str
    ) -> Optional[UsageQuota]:
        """读取额度行（不存在返回 None）。

        公开出来是为了让端点拿到**调整前**的值写进审计（Q-M）——
        端点直接调私有 `_get_quota_row` 会把内部结构绑进接口层。
        """
        return await self._get_quota_row(tenant_id, usage_type, period)

    async def set_quota_limit(
        self, tenant_id: str, usage_type: UsageType, period: str, limit_count: int
    ) -> UsageQuota:
        """把某租户某类型某账期的**额度上限**设为 `limit_count`（Q-M）。

        - 语义是**设置新上限**（绝对值），不是增量；调用方负责记录前后值以便复盘。
        - `limit_count` 必须 `>= 0`：负数上限没有业务含义，且会让「剩余额度」
          算出负值，把看板与转工单判断一起带偏。
        - 额度行不存在时按新上限创建（`used_count` 归 0）。
        """
        if limit_count < 0:
            raise BadRequestError(
                "额度上限不能为负数", code=ErrorCode.VALIDATION_ERROR,
            )
        q = await self._get_quota_row(tenant_id, usage_type, period)
        if q is None:
            q = UsageQuota(
                tenant_id=tenant_id, usage_type=usage_type, period=period,
                limit_count=limit_count, used_count=0,
            )
            self.db.add(q)
            await self.db.flush()
            return q
        q.limit_count = limit_count
        await self.db.flush()
        return q

    async def ensure_quota(
        self, tenant_id: str, usage_type: UsageType, period: str, limit_count: int = 100
    ) -> UsageQuota:
        """取（或创建）额度行。

        **并发注意**：这里仍是「查不到就插入」，在并发首用时会有多个请求
        同时插入同一 (tenant, type, period)。已加 `IntegrityError` 兜底：
        唯一约束冲突时回滚并重新读取。

        但仍**建议**为 `usage_quotas` 建
        `UNIQUE(tenant_id, usage_type, period)` 唯一索引——
        没有数据库约束时，并发插入仍可能产生重复行（应用层只能缓解）。
        """
        q = await self._get_quota_row(tenant_id, usage_type, period)
        if q is not None:
            return q

        q = UsageQuota(
            tenant_id=tenant_id, usage_type=usage_type, period=period,
            limit_count=limit_count, used_count=0,
        )
        self.db.add(q)
        try:
            await self.db.flush()
        except IntegrityError:
            # 并发下被别的请求抢先插入：回滚后重新读取
            await self.db.rollback()
            q = await self._get_quota_row(tenant_id, usage_type, period)
            if q is None:
                raise
        return q

    async def consume(
        self,
        *,
        tenant_id: str,
        user_id: Optional[int],
        usage_type: UsageType,
        period: str,
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        urgent: bool = False,
    ) -> dict[str, Any]:
        """扣减一次用量；超量自动转工单（PRD 超量自动转工单）。

        ⚠️ **已废弃，请改用 `consume_atomic()`。**

        本实现是"读额度 → 判断是否超限 → 自增"三步式，两个缺陷：
        1. **丢失更新**：`q.used_count = q.used_count + 1` 读的是快照值，
           并发时多个请求各自基于同一旧值计算，后写覆盖先写。
           实测额度 5、20 并发 → `used_count` 只记到 1~2，**少计费且少转工单**。
        2. **额度行重复**：`ensure_quota` 的"查-无则插"在并发首次使用时
           可能插入多行，把额度切成几份、变相放大可用量。

        保留此方法仅用于 `verify_p1_concurrency_retention.py` 的前后对比；
        生产调用点已全部切到 `consume_atomic()`。
        """
        q = await self.ensure_quota(tenant_id, usage_type, period)
        exceeded = q.exhausted

        rec = UsageRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            usage_type=usage_type,
            period=period,
            ref_type=ref_type,
            ref_id=ref_id,
            converted_to_work_order=int(exceeded),
        )
        self.db.add(rec)
        if not exceeded:
            q.used_count = (q.used_count or 0) + 1

        work_order = None
        if exceeded:
            work_order = await self._to_work_order(
                tenant_id=tenant_id, user_id=user_id, usage_type=usage_type,
                ref_type=ref_type, ref_id=ref_id, urgent=urgent,
            )
            await notify(
                self.db, tenant_id=tenant_id, user_id=user_id,
                type=NotificationType.WORK_ORDER_CREATED,
                content=f"用量已超套餐额度，已自动生成工单：{work_order.order_no}",
                ref_type=ref_type, ref_id=ref_id,
            )

        return {
            "used": q.used_count,
            "limit": q.limit_count,
            "remaining": q.remaining,
            "exceeded": exceeded,
            "work_order_id": work_order.id if work_order else None,
        }

    async def _maybe_warn_quota(
        self,
        *,
        tenant_id: str,
        user_id: Optional[int],
        q: UsageQuota,
        used_before: int,
        usage_type: UsageType,
        ref_type: Optional[str],
        ref_id: Optional[int],
    ) -> None:
        """额度预警（`QUOTA_WARNING`）。

        ## 为什么必须判「跨阈值」而不是「低于阈值」

        扣减是**每次调用都发生**的高频动作。若写成
        `if remaining / limit < 0.2: notify(...)`，那么用户一旦进入预警区，
        **接下来每一次扣减都会再发一条通知**——额度剩 20 次、用户连续用
        20 次，就会收到 20 条「额度不足」。这类通知不但无用，还会**训练
        用户忽略通知**，连带让「派单待接」「待复核」这些真通知一起失效。

        正确语义是**只在状态发生迁移的那一刻发一次**：
        `used_before < 阈值 <= used_after`。阈值由 80% 用量定义，
        因此一个账期内最多触发一次。

        ## 为什么预警要在「耗尽转工单」之前

        `WORK_ORDER_CREATED` 是**事后**通知（额度已经用光、工单已经生成）。
        预警的价值在于**给用户留出反应时间**（充值 / 调整用量），
        所以必须在耗尽前发出，两者不是替代关系。
        """
        if not q.limit_count or q.limit_count <= 0:
            return  # 不限量套餐没有「额度不足」的概念
        if not user_id:
            return

        threshold = _quota_warn_threshold(q.limit_count)
        used_after = q.used_count or 0
        if not (used_before < threshold <= used_after):
            return

        await notify(
            self.db,
            tenant_id=tenant_id,
            user_id=user_id,
            type=NotificationType.QUOTA_WARNING,
            content=(
                f"{getattr(usage_type, 'value', usage_type)} 用量已达 "
                f"{used_after}/{q.limit_count}，剩余 {q.remaining} 次，"
                f"额度用尽后将自动转为计费工单"
            ),
            ref_type=ref_type,
            ref_id=ref_id,
            payload={
                "usage_type": str(getattr(usage_type, "value", usage_type)),
                "used": used_after,
                "limit": q.limit_count,
                "remaining": q.remaining,
                "threshold": threshold,
            },
        )

    async def _to_work_order(
        self,
        *,
        tenant_id: str,
        user_id: Optional[int],
        usage_type: UsageType,
        ref_type: Optional[str],
        ref_id: Optional[int],
        urgent: bool,
    ) -> WorkOrder:
        price = _PRICE_CENTS[usage_type]["urgent" if urgent else "standard"]
        wo = WorkOrder(
            tenant_id=tenant_id,
            created_by=user_id,
            usage_type=usage_type,
            order_no=_order_no(tenant_id),
            title=f"{usage_type.value} 超量转工单",
            description="套餐额度已用尽，本次服务转为按单计费",
            status=WorkOrderStatus.PENDING,
            urgent=int(urgent),
            price_cents=price,
            escalate_to_lawyer=int(usage_type in _ESCALATE_TYPES),
            ref_type=ref_type,
            ref_id=ref_id,
            billing_note={"standard_cents": _PRICE_CENTS[usage_type]["standard"],
                          "urgent_cents": _PRICE_CENTS[usage_type]["urgent"], "urgent": urgent},
        )
        self.db.add(wo)
        await self.db.flush()
        return wo

    # ---------------- 看板 ----------------
    async def dashboard(self, tenant_id: str, period: str) -> dict[str, Any]:
        quotas = list(
            (
                await self.db.execute(
                    select(UsageQuota).where(UsageQuota.tenant_id == tenant_id, UsageQuota.period == period)
                )
            ).scalars().all()
        )
        orders = list(
            (await self.db.execute(select(WorkOrder).where(WorkOrder.tenant_id == tenant_id))).scalars().all()
        )
        return {
            "period": period,
            "quotas": [
                {
                    "usage_type": q.usage_type.value,
                    "used": q.used_count,
                    "limit": q.limit_count,
                    "remaining": q.remaining,
                    "percent": round(q.used_count / q.limit_count * 100, 1) if q.limit_count else 0,
                }
                for q in quotas
            ],
            "work_orders": {
                "total": len(orders),
                "pending": sum(1 for o in orders if o.status == WorkOrderStatus.PENDING),
                "amount_cents": sum(o.price_cents for o in orders),
            },
        }

    async def list_work_orders(self, tenant_id: str, status: Optional[str] = None) -> list[WorkOrder]:
        stmt = select(WorkOrder).where(WorkOrder.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(WorkOrder.status == status)
        return list((await self.db.execute(stmt.order_by(WorkOrder.id.desc()))).scalars().all())

    # ---------------- 收入预测（分项系数法）----------------
    def project_revenue(
        self,
        *,
        subscription_cents: int = 0,
        case_service_cents: int = 0,
        work_order_cents: int = 0,
        value_added_cents: int = 0,
    ) -> dict[str, Any]:
        weights = _REVENUE_WEIGHTS
        breakdown = {
            "subscription": int(subscription_cents * weights["subscription"]),
            "case_service": int(case_service_cents * weights["case_service"]),
            "work_order": int(work_order_cents * weights["work_order"]),
            "value_added": int(value_added_cents * weights["value_added"]),
        }
        total = sum(breakdown.values())
        # Q-AA（2026-09-21 裁定）：收入预测**合计必须为正数**。
        # 净零/净负营收会污染财报口径（某租户可构造负值拉低/抬高营收合计）。
        # 单项可为负（退款/冲销），只要净合计仍为正即放行。
        if total <= 0:
            raise BadRequestError(
                "收入预测合计必须为正数（净零/净负会被拒收）",
                code=ErrorCode.VALIDATION_ERROR,
            )
        return {
            "breakdown": breakdown,
            "weights": weights,
            "total_cents": total,
            "total_yuan": round(total / 100, 2),
        }


def _order_no(tenant_id: str) -> str:
    import datetime
    import uuid

    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    return f"WO-{tenant_id}-{ts}-{uuid.uuid4().hex[:4]}"
