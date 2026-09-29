"""内容安全审核（P0-13）。

法律依据：《生成式人工智能服务管理暂行办法》（2023-08-15 施行）
- **第四条**：不得生成煽动颠覆国家政权、危害国家安全、宣扬恐怖主义/极端主义、
  民族仇恨、暴力、淫秽色情，以及虚假有害信息等法律行政法规禁止的内容
- **第十四条**：发现违法内容的，应当及时采取**停止生成、停止传输、消除**等处置措施，
  并向有关主管部门报告；发现使用者利用服务从事违法活动的，应采取**警示、限制功能、
  暂停或终止服务**等措施，**保存有关记录**，并向有关主管部门报告
- **第十五条**：建立健全投诉、举报机制
- **第十七条**：具有舆论属性或社会动员能力的，应开展安全评估并履行**算法备案**

第十四条的四个动作词直接对应本模块的设计：
| 法条要求 | 实现 |
|----------|------|
| 停止生成 | 输入审核命中 → 拒绝调用模型 |
| 停止传输 | 输出审核命中 → 不推送该片段（含 SSE 流式） |
| 消除 | 落库前清洗；已生成内容标记 `blocked` |
| 保存有关记录 | `ModerationRecord` 表 + 审计日志 |
| 向主管部门报告 | `report_to_authority()` 钩子（对接监管接口） |
"""
from __future__ import annotations

import base64
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from loguru import logger

from app.config import settings
from app.core.errors import AppError, ErrorCode


# ═══════════════════════════════════════════════════════════
# 分级与处置
# ═══════════════════════════════════════════════════════════
class ModerationLevel(str, Enum):
    """审核严重度。分级决定处置动作——不是所有命中都该拒绝服务。"""

    PASS = "PASS"          # 未命中
    REVIEW = "REVIEW"      # 疑似：放行但打标，进入人工抽检队列
    BLOCK = "BLOCK"        # 明确违法：拒绝生成 / 停止传输
    ESCALATE = "ESCALATE"  # 高风险：拒绝 + 限制该用户功能 + 强制上报


class ModerationAction(str, Enum):
    """处置动作（对应《暂行办法》第十四条）。"""

    ALLOW = "ALLOW"            # 放行
    ALLOW_WITH_FLAG = "ALLOW_WITH_FLAG"  # 放行但留痕待抽检
    STOP_GENERATION = "STOP_GENERATION"  # 停止生成（输入命中）
    STOP_TRANSMISSION = "STOP_TRANSMISSION"  # 停止传输（输出命中）
    RESTRICT_USER = "RESTRICT_USER"  # 限制功能（+ 保存记录 + 上报）


class ModerationCategory(str, Enum):
    """违法内容类别（《暂行办法》第四条（一）+ 第九条网络信息内容生态治理）。"""

    POLITICAL = "POLITICAL"          # 危害国家安全 / 颠覆政权 / 分裂国家
    TERRORISM = "TERRORISM"          # 恐怖主义、极端主义
    ETHNIC_HATRED = "ETHNIC_HATRED"  # 民族仇恨、民族歧视
    VIOLENCE = "VIOLENCE"            # 暴力、血腥
    PORNOGRAPHY = "PORNOGRAPHY"      # 淫秽色情
    ILLEGAL_SERVICE = "ILLEGAL_SERVICE"  # 违法服务（代写论文、伪造证件、非法集资…）
    FRAUD = "FRAUD"                  # 诈骗、虚假有害信息
    DISCRIMINATION = "DISCRIMINATION"  # 第四条（二）歧视
    PRIVACY = "PRIVACY"              # 第四条（四）侵害隐私 / 个人信息

    # —— 法律行业特有（不是法定类目，但属执业红线）——
    PRIVILEGE_BYPASS = "PRIVILEGE_BYPASS"    # 诱导 AI 出具正式法律意见 / 冒充律师
    CASE_MANIPULATION = "CASE_MANIPULATION"  # 伪造证据、串供、行贿等违法执业


#: 严重度排序，用于「取最严重命中」
_SEVERITY: dict[ModerationLevel, int] = {
    ModerationLevel.PASS: 0,
    ModerationLevel.REVIEW: 1,
    ModerationLevel.BLOCK: 2,
    ModerationLevel.ESCALATE: 3,
}

#: 各严重度对应的处置动作
_ACTION_FOR_LEVEL: dict[ModerationLevel, ModerationAction] = {
    ModerationLevel.PASS: ModerationAction.ALLOW,
    ModerationLevel.REVIEW: ModerationAction.ALLOW_WITH_FLAG,
    ModerationLevel.BLOCK: ModerationAction.STOP_GENERATION,
    ModerationLevel.ESCALATE: ModerationAction.RESTRICT_USER,
}


# ═══════════════════════════════════════════════════════════
# 词库
# ═══════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Term:
    """一条违禁词规则。

    `pattern` 可以是子串（literal）或正则（regex）。正则用于处理
    「间隔符号插入」这类绕过（如「顛-覆-国-家」）。
    """

    text: str
    category: ModerationCategory
    level: ModerationLevel
    regex: bool = False
    note: str = ""


#: 内置词库。
#:
#: ⚠️ 生产环境必须由法务/合规团队用**全量**词库替换——这里只给出
#: 结构完整、覆盖每个类别的**最小可运行集**，用于打通链路与回归测试。
#: 真实词库通常 10 万+ 条，应以独立文件/数据库表交付，并支持热更新
#: （见 `load_terms_from_file`）。
_BUILTIN_TERMS: tuple[Term, ...] = (
    # —— 政治安全（最高级别：ESCALATE）——
    Term("颠覆国家政权", ModerationCategory.POLITICAL, ModerationLevel.ESCALATE),
    Term("推翻社会主义制度", ModerationCategory.POLITICAL, ModerationLevel.ESCALATE),
    Term("煽动分裂国家", ModerationCategory.POLITICAL, ModerationLevel.ESCALATE),
    Term("破坏国家统一", ModerationCategory.POLITICAL, ModerationLevel.ESCALATE),
    Term("危害国家安全", ModerationCategory.POLITICAL, ModerationLevel.ESCALATE),
    Term("颠覆政权", ModerationCategory.POLITICAL, ModerationLevel.ESCALATE),
    # —— 恐怖主义 / 极端主义 ——
    Term("恐怖主义", ModerationCategory.TERRORISM, ModerationLevel.ESCALATE),
    Term("极端主义", ModerationCategory.TERRORISM, ModerationLevel.ESCALATE),
    Term("制造爆炸物", ModerationCategory.TERRORISM, ModerationLevel.ESCALATE),
    Term("制作炸弹", ModerationCategory.TERRORISM, ModerationLevel.ESCALATE),
    # —— 民族仇恨 ——
    Term("煽动民族仇恨", ModerationCategory.ETHNIC_HATRED, ModerationLevel.ESCALATE),
    Term("民族歧视", ModerationCategory.ETHNIC_HATRED, ModerationLevel.BLOCK),
    # —— 暴力 ——
    Term("杀人方法", ModerationCategory.VIOLENCE, ModerationLevel.BLOCK),
    Term("虐待致死", ModerationCategory.VIOLENCE, ModerationLevel.BLOCK),
    # —— 淫秽色情 ——
    Term("淫秽色情", ModerationCategory.PORNOGRAPHY, ModerationLevel.BLOCK),
    Term("制作色情", ModerationCategory.PORNOGRAPHY, ModerationLevel.BLOCK),
    # —— 违法服务 ——
    Term("伪造身份证", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    Term("伪造公章", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    Term("办假证", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    Term("代写学位论文", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    Term("非法集资方案", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    Term("洗钱方法", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    Term("逃税方案", ModerationCategory.ILLEGAL_SERVICE, ModerationLevel.BLOCK),
    # —— 诈骗 / 虚假有害信息 ——
    Term("诈骗话术", ModerationCategory.FRAUD, ModerationLevel.BLOCK),
    Term("电信诈骗", ModerationCategory.FRAUD, ModerationLevel.BLOCK),
    # —— 歧视（第四条（二））——
    Term("地域歧视", ModerationCategory.DISCRIMINATION, ModerationLevel.REVIEW),
    Term("性别歧视", ModerationCategory.DISCRIMINATION, ModerationLevel.REVIEW),
    # —— 隐私（第四条（四））——
    Term("人肉搜索", ModerationCategory.PRIVACY, ModerationLevel.BLOCK),
    Term("开盒", ModerationCategory.PRIVACY, ModerationLevel.REVIEW),
    # —— 法律执业红线 ——
    Term("伪造证据", ModerationCategory.CASE_MANIPULATION, ModerationLevel.ESCALATE),
    Term("串供", ModerationCategory.CASE_MANIPULATION, ModerationLevel.ESCALATE),
    Term("贿赂法官", ModerationCategory.CASE_MANIPULATION, ModerationLevel.ESCALATE),
    Term("伪造借条", ModerationCategory.CASE_MANIPULATION, ModerationLevel.BLOCK),
    # —— 诱导越权（产品风控：AI 不得冒充律师出具正式意见）——
    Term(
        r"(帮我|替我|给我)(出具|签发|盖章).{0,6}(正式)?(法律意见书|律师函)",
        ModerationCategory.PRIVILEGE_BYPASS,
        ModerationLevel.REVIEW,
        regex=True,
        note="AI 不得以律师名义出具正式文书",
    ),
)


#: 间隔符：绕过者常用「顛-覆-国-家」「顛*覆*国*家」「顛 覆 国 家」
_SEPARATOR_CHARS = " \t\r\n-_*·.,，。、|/\\~!！?？@#$%^&+="
_SEP_RE = re.compile(f"[{re.escape(_SEPARATOR_CHARS)}]+")


# ═══════════════════════════════════════════════════════════
# 命中结果
# ═══════════════════════════════════════════════════════════
@dataclass
class Hit:
    category: ModerationCategory
    level: ModerationLevel
    matched_text: str
    note: str = ""
    #: 命中的归一化形态（用于排查绕过）
    via: str = "literal"

    def as_dict(self) -> dict:
        """⚠️ 只输出**脱敏**信息：不回传原始命中文本。

        原文进审计/记录表会形成二次违规存储，且可能被前端回显给用户。
        """
        return {
            "category": self.category.value,
            "level": self.level.value,
            "note": self.note,
            "via": self.via,
            "matched_hash": _hash8(self.matched_text),
            "matched_len": len(self.matched_text),
        }


@dataclass
class ModerationResult:
    """一次审核的完整结论。"""

    level: ModerationLevel = ModerationLevel.PASS
    action: ModerationAction = ModerationAction.ALLOW
    hits: list[Hit] = field(default_factory=list)
    #: 归一化后的文本摘要（脱敏，供排查）
    normalized_digest: str = ""
    #: 输入 / 输出 侧
    side: str = "input"
    #: 是否降级放行（审核后端不可用时的 fail-open / fail-closed 决策）
    degraded: bool = False

    @property
    def blocked(self) -> bool:
        """是否需要阻止（停止生成 / 停止传输）。"""
        return self.level in (ModerationLevel.BLOCK, ModerationLevel.ESCALATE)

    @property
    def needs_manual_review(self) -> bool:
        return self.level == ModerationLevel.REVIEW

    def top_category(self) -> Optional[ModerationCategory]:
        return self.hits[0].category if self.hits else None

    def as_dict(self) -> dict:
        return {
            "level": self.level.value,
            "action": self.action.value,
            "side": self.side,
            "blocked": self.blocked,
            "degraded": self.degraded,
            "categories": sorted({h.category.value for h in self.hits}),
            "hit_count": len(self.hits),
            "hits": [h.as_dict() for h in self.hits],
            "digest": self.normalized_digest,
        }

    def reason_for_user(self) -> str:
        """面向终端用户的拒答说明（不暴露词库细节，避免被反向调优绕过）。"""
        if not self.blocked:
            return ""
        return (
            "抱歉，该请求可能涉及法律法规禁止生成的内容，已停止生成。"
            "如您认为存在误判，可通过「投诉举报」入口反馈，我们将及时核实处理。"
        )


def _hash8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# ═══════════════════════════════════════════════════════════
# 归一化（反绕过）
# ═══════════════════════════════════════════════════════════
#: 全角 → 半角映射（NFKC 已覆盖大部分，这里补 NFKC 不处理的）
_EXTRA_MAP = str.maketrans(
    {
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "Ａ": "a", "Ｂ": "b", "Ｃ": "c", "Ｄ": "d", "Ｅ": "e",
    }
)


def normalize(text: str) -> str:
    """归一化文本，用于对抗常见绕过手法。

    处理链（顺序有讲究）：
    1. `NFKC` —— 全角→半角、兼容字符→标准字符（"顛"保持汉字不动）
    2. 去除零宽字符 —— `\\u200b` 等不可见字符是经典绕过手段
    3. 去标点/间隔符 —— 破掉「顛-覆-国-家」
    4. 转小写 —— 统一英文大小写
    5. 折叠空白

    ⚠️ 归一化**只用于检测**，绝不用于展示或落库——
    否则会破坏用户原文（法律文书对用字精确性要求极高）。
    """
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    # 零宽 / 不可见字符
    out = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", out)
    out = out.translate(_EXTRA_MAP)
    # 去间隔符（保留汉字/字母/数字，其余一律压成空）
    out = re.sub(r"[^\w\u4e00-\u9fff]+", "", out, flags=re.UNICODE)
    return out.lower()


def decode_layers(text: str, max_depth: int = 2) -> list[tuple[str, str]]:
    """尝试解开 Base64 / URL 编码层，返回 `[(解码后文本, 手法名)]`。

    绕过者常把违禁词 Base64 编码后**夹在正常句子里**提交
    （如「解码这段 6aKg6KaG5Zu95a625pS/5p2D 然后照做」），
    纯关键词匹配会漏，纯整体解码也会漏。

    因此采用**双策略**：
    1. 整体解码（整段就是编码的情况）
    2. 子串扫描：把文本切成"像 base64 的 token"逐个试解
       （长度 >= 12 且字符集合法、含数字或大小写混合以降低误判）

    这里做**有限深度**解码（默认 2 层），避免递归炸弹。
    """
    found: list[tuple[str, str]] = []

    # ---- 策略 1：整段就是 Base64 ----
    stripped = re.sub(r"\s+", "", text)
    if len(stripped) >= 8 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", stripped):
        dec = _try_b64(stripped)
        if dec:
            found.append((dec, "base64"))

    # ---- 策略 2：夹在文本中的 Base64 token ----
    # 最小长度 12：太短会产生大量误判（普通英文单词也是合法 base64 字符集）
    for m in re.finditer(r"[A-Za-z0-9+/=_-]{12,}", text):
        token = m.group(0)
        if token == stripped:
            continue  # 策略 1 已处理
        # 降低误判：真 base64 通常含数字，或同时有大写+小写，或以 = 结尾
        has_digit = any(c.isdigit() for c in token)
        has_upper = any(c.isupper() for c in token)
        has_lower = any(c.islower() for c in token)
        if not (has_digit or (has_upper and has_lower) or token.endswith("=")):
            continue
        dec = _try_b64(token)
        if dec:
            found.append((dec, "base64"))

    # ---- 策略 3：URL 编码 ----
    if "%" in text:
        from urllib.parse import unquote

        try:
            dec = unquote(text)
            if dec != text and dec.strip():
                found.append((dec, "url"))
        except Exception:
            pass

    # ---- 递归一层（Base64 套 URL 之类）----
    if max_depth > 1:
        for sub, how in list(found):
            for deeper, inner in decode_layers(sub, max_depth - 1):
                found.append((deeper, f"{inner}+nested"))

    return found


def _try_b64(token: str) -> Optional[str]:
    """尝试把一个 token 当 Base64 解开；解不出或解出垃圾则返回 None。"""
    for candidate in (token, token.replace("-", "+").replace("_", "/")):
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            raw = base64.b64decode(padded.encode("ascii"), validate=True)
        except Exception:
            continue
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # 必须含 CJK 或全可打印，否则是二进制噪音
        if decoded.strip() and (
            any("\u4e00" <= ch <= "\u9fff" for ch in decoded) or decoded.isprintable()
        ):
            return decoded
    return None


# ═══════════════════════════════════════════════════════════
# 审核引擎
# ═══════════════════════════════════════════════════════════
class ContentModerator:
    """可插拔内容审核器。

    后端策略（`settings.MODERATION_BACKEND`）：
    - `auto`（默认）：有外部 API Key 用外部，否则用内置词库
    - `builtin`：仅本地词库（零网络依赖，可离线部署）
    - `external`：仅外部审核 API（**生产推荐**，词库更全、有模型能力）
    - `none`：完全关闭（**仅限测试**，生产启动即失败）

    设计上遵循与 `ModelRouter` 一致的哲学：**生产环境不静默降级**。
    外部审核不可用时，按 `MODERATION_FAIL_MODE` 决定 fail-open / fail-closed。
    """

    def __init__(
        self,
        *,
        terms: Optional[Iterable[Term]] = None,
        backend: Optional[str] = None,
    ) -> None:
        self.terms: tuple[Term, ...] = tuple(terms) if terms is not None else _BUILTIN_TERMS
        self.backend = backend or settings.MODERATION_BACKEND
        # 预编译正则（词库可能很大，避免每次调用重复编译）
        self._regex_terms: list[tuple[re.Pattern[str], Term]] = [
            (re.compile(t.text, re.IGNORECASE), t) for t in self.terms if t.regex
        ]
        self._literal_terms: list[Term] = [t for t in self.terms if not t.regex]
        # 归一化后的字面词，用于比对归一化文本
        self._normalized_literals: list[tuple[str, Term]] = [
            (normalize(t.text), t) for t in self._literal_terms if normalize(t.text)
        ]

    # ---------------- 主入口 ----------------
    async def check(self, text: str, *, side: str = "input") -> ModerationResult:
        """审核一段文本。`side` 为 `input` / `output`，影响处置动作语义。"""
        if not settings.MODERATION_ENABLED:
            return ModerationResult(side=side)

        if not text or not text.strip():
            return ModerationResult(side=side)

        hits = self._check_builtin(text)

        # 外部后端（若启用）：合并结果
        if self._use_external():
            try:
                ext = await self._check_external(text)
                hits.extend(ext)
            except Exception as exc:
                logger.warning(f"外部内容审核调用失败：{exc}")
                if settings.MODERATION_FAIL_CLOSED:
                    # fail-closed：宁可拒绝，也不放行未审核内容
                    return ModerationResult(
                        level=ModerationLevel.REVIEW,
                        action=ModerationAction.STOP_GENERATION
                        if side == "input"
                        else ModerationAction.STOP_TRANSMISSION,
                        hits=[],
                        side=side,
                        degraded=True,
                    )
                logger.warning("内容审核降级放行（MODERATION_FAIL_CLOSED=false）")

        return self._aggregate(hits, text, side)

    def check_sync(self, text: str, *, side: str = "input") -> ModerationResult:
        """同步版：仅走内置词库（供单测与非异步上下文使用）。"""
        if not settings.MODERATION_ENABLED:
            return ModerationResult(side=side)
        return self._aggregate(self._check_builtin(text), text, side)

    # ---------------- 内置词库检测 ----------------
    def _check_builtin(self, text: str) -> list[Hit]:
        hits: list[Hit] = []
        norm = normalize(text)

        # 1) 字面词：原文直接命中
        for term in self._literal_terms:
            if term.text in text or term.text.lower() in text.lower():
                hits.append(
                    Hit(term.category, term.level, term.text, term.note, via="literal")
                )

        # 2) 字面词：归一化后命中（破间隔符/全角/零宽）
        for norm_term, term in self._normalized_literals:
            if norm_term in norm:
                if not any(h.matched_text == term.text for h in hits):
                    hits.append(
                        Hit(term.category, term.level, term.text, term.note, via="normalized")
                    )

        # 3) 正则规则：原文 + 归一化文本都试
        for pattern, term in self._regex_terms:
            m = pattern.search(text) or pattern.search(norm)
            if m:
                hits.append(
                    Hit(term.category, term.level, m.group(0), term.note, via="regex")
                )

        # 4) 解码层：Base64 / URL
        for decoded, how in decode_layers(text):
            if decoded == text:
                continue
            dec_norm = normalize(decoded)
            for norm_term, term in self._normalized_literals:
                if norm_term in dec_norm:
                    if not any(h.matched_text == term.text for h in hits):
                        hits.append(
                            Hit(term.category, term.level, term.text, term.note, via=how)
                        )
            for pattern, term in self._regex_terms:
                if pattern.search(decoded) or pattern.search(dec_norm):
                    if not any(h.matched_text == term.text for h in hits):
                        hits.append(
                            Hit(term.category, term.level, term.text, term.note, via=how)
                        )

        return hits

    # ---------------- 外部后端 ----------------
    def _use_external(self) -> bool:
        if self.backend == "builtin" or self.backend == "none":
            return False
        if self.backend == "external":
            return True
        # auto：有 Key 才用
        return bool(settings.MODERATION_API_KEY)

    async def _check_external(self, text: str) -> list[Hit]:
        """调用外部内容审核 API（阿里云绿网 / 腾讯天御 / 自建）。

        采用与 LLM Provider 一致的 OpenAI-compatible 风格约定，
        便于替换供应商。返回格式约定：
        `{"result": {"suggestion": "pass|review|block", "label": "...", "score": 0.9}}`
        """
        import httpx

        if not settings.MODERATION_API_KEY:
            raise RuntimeError("MODERATION_API_KEY 未配置")

        headers = {
            "Authorization": f"Bearer {settings.MODERATION_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {"content": text, "service": settings.MODERATION_SERVICE}
        async with httpx.AsyncClient(timeout=settings.MODERATION_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{settings.MODERATION_BASE_URL.rstrip('/')}/text/scan",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        result = data.get("result") or data.get("data") or {}
        suggestion = str(result.get("suggestion") or result.get("label") or "pass").lower()
        label = str(result.get("label") or result.get("category") or "")

        level_map = {
            "pass": ModerationLevel.PASS,
            "review": ModerationLevel.REVIEW,
            "block": ModerationLevel.BLOCK,
            "risky": ModerationLevel.BLOCK,
            "reject": ModerationLevel.ESCALATE,
        }
        level = level_map.get(suggestion, ModerationLevel.PASS)
        if level == ModerationLevel.PASS:
            return []

        category = _map_external_label(label)
        return [Hit(category, level, label or suggestion, note="external", via="external")]

    # ---------------- 汇总 ----------------
    def _aggregate(self, hits: list[Hit], text: str, side: str) -> ModerationResult:
        if not hits:
            return ModerationResult(
                level=ModerationLevel.PASS,
                action=ModerationAction.ALLOW,
                side=side,
                normalized_digest=_hash8(normalize(text)),
            )

        # 去重（按 category+matched_text），再按严重度降序
        seen: set[tuple[str, str]] = set()
        uniq: list[Hit] = []
        for h in hits:
            key = (h.category.value, h.matched_text)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(h)
        uniq.sort(key=lambda h: -_SEVERITY[h.level])

        top = uniq[0].level
        action = _ACTION_FOR_LEVEL[top]
        # 输出侧命中：动作语义必须是「停止传输」而非「停止生成」。
        # 第十四条的两个动作分属不同场景——输入侧拦截（不让违规内容进模型）
        # 与输出侧拦截（不让违规内容到达用户）在语义与后续处置上是两件事。
        # 即使 ESCALATE 级（需额外限制用户），输出侧的首要动作仍是停止传输。
        if side == "output" and action in (
            ModerationAction.STOP_GENERATION,
            ModerationAction.RESTRICT_USER,
        ):
            action = ModerationAction.STOP_TRANSMISSION

        return ModerationResult(
            level=top,
            action=action,
            hits=uniq,
            side=side,
            normalized_digest=_hash8(normalize(text)),
        )


def _map_external_label(label: str) -> ModerationCategory:
    """把外部供应商的 label 映射到内部分类。未识别归入 FRAUD（保守）。"""
    low = (label or "").lower()
    if any(k in low for k in ("politics", "political", "porn", "abuse", "terror")):
        if "terror" in low:
            return ModerationCategory.TERRORISM
        if "porn" in low:
            return ModerationCategory.PORNOGRAPHY
        if "politics" in low or "political" in low:
            return ModerationCategory.POLITICAL
    if "violence" in low or "blood" in low:
        return ModerationCategory.VIOLENCE
    if "fraud" in low or "contraband" in low or "illegal" in low:
        return ModerationCategory.ILLEGAL_SERVICE
    if "discrimin" in low:
        return ModerationCategory.DISCRIMINATION
    if "privacy" in low:
        return ModerationCategory.PRIVACY
    return ModerationCategory.FRAUD


# ═══════════════════════════════════════════════════════════
# 流式输出审核（SSE 增量片段）
# ═══════════════════════════════════════════════════════════
class StreamModerator:
    """SSE 流式输出审核器。

    **难点**：流式输出是逐片段推送的，违禁词可能**跨片段**被切开
    （如 `<piece1>颠覆</piece1><piece2>国家政权</piece2>`）。
    只审核单个片段必然漏检。

    解法：维护一个**滑动窗口缓冲**，每次新片段到达时审核
    `buffer[-OVERLAP:] + piece`，命中则停止整个流的传输。
    `OVERLAP` 取最长违禁词长度，保证跨片段词不可能被漏检。
    """

    #: 最长内置词长度（用于确定窗口大小）
    _MAX_TERM_LEN = max((len(t.text) for t in _BUILTIN_TERMS if not t.regex), default=8)

    def __init__(self, moderator: ContentModerator, *, overlap: Optional[int] = None) -> None:
        self.moderator = moderator
        self.overlap = overlap if overlap is not None else self._MAX_TERM_LEN * 2
        self.head = ""  # 已推送内容的前缀（仅保留窗口长度，避免内存膨胀）
        self.blocked_result: Optional[ModerationResult] = None

    def prepare(self, piece: str) -> tuple[bool, Optional[ModerationResult]]:
        """审核下一个待推送片段。

        返回 `(allow, result)`：`allow=False` 表示**必须停止传输**。
        调用方在拿到 `allow=True` 后再把 `piece` 写入 `committed()`。
        """
        buffer = self.head + piece
        result = self.moderator.check_sync(buffer, side="output")
        if result.blocked:
            self.blocked_result = result
            return False, result
        return True, result

    def committed(self, piece: str) -> None:
        """确认该片段已成功推送，推进窗口。"""
        self.head = (self.head + piece)[-self.overlap :]

    def full_text_digest(self) -> str:
        return _hash8(self.head)


# ═══════════════════════════════════════════════════════════
# 异常与对外接口
# ═══════════════════════════════════════════════════════════
class ContentBlockedError(AppError):
    """内容被拦截。HTTP 422（语义：请求可解析但内容不可处理）。"""

    status_code = 422
    code = ErrorCode.CONTENT_BLOCKED

    def __init__(self, result: ModerationResult) -> None:
        super().__init__(
            result.reason_for_user(),
            details={
                "categories": sorted({h.category.value for h in result.hits}),
                "action": result.action.value,
            },
        )
        self.result = result


def get_moderator() -> ContentModerator:
    """获取审核器（可按需扩展为带缓存的单例）。"""
    return ContentModerator()


async def report_to_authority(result: ModerationResult, *, subject: str, period: str) -> bool:
    """向主管部门报告的钩子（《暂行办法》第十四条）。

    真实实现需要对接网信办违法和不良信息举报中心 / 属地网信办接口。
    这里返回 False 表示"未配置监管上报通道"，由上层把事件置为
    `pending_report` 状态，人工兜底上报——**绝不能静默丢弃**。
    """
    if not settings.MODERATION_REPORT_ENABLED or not settings.MODERATION_REPORT_URL:
        logger.warning(
            f"内容违规事件需上报但未配置监管通道: subject={subject} "
            f"categories={[h.category.value for h in result.hits]}"
        )
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                settings.MODERATION_REPORT_URL,
                json={"subject": subject, "period": period, "detail": result.as_dict()},
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error(f"监管上报失败（需人工补报）: {exc}")
        return False


# ═══════════════════════════════════════════════════════════
# 词库热加载
# ═══════════════════════════════════════════════════════════
def load_terms_from_file(path: str) -> list[Term]:
    """从文件热加载词库。

    格式：每行 `类别|级别|是否正则(0/1)|词条`，`#` 开头为注释。
    生产环境应由合规团队维护此文件（或落库 + 后台管理界面），
    并支持不重启服务热更新。
    """
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        logger.warning(f"词库文件不存在，沿用内置词库: {path}")
        return list(_BUILTIN_TERMS)

    terms: list[Term] = []
    for lineno, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) != 4:
            logger.warning(f"词库第 {lineno} 行格式错误，已跳过: {line[:40]}")
            continue
        cat, lvl, is_re, text = parts
        try:
            terms.append(
                Term(
                    text=text,
                    category=ModerationCategory(cat),
                    level=ModerationLevel(lvl),
                    regex=is_re == "1",
                )
            )
        except ValueError as exc:
            logger.warning(f"词库第 {lineno} 行枚举无效，已跳过: {exc}")
    if not terms:
        logger.warning(f"词库文件为空，沿用内置词库: {path}")
        return list(_BUILTIN_TERMS)
    logger.info(f"已加载外部词库 {len(terms)} 条: {path}")
    return terms
