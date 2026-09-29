/**
 * @nlaw/sdk —— 统一 HTTP/SSE 客户端。
 * 对齐 AIECO packages/sdk 的「解包 {success,data,error}，错误抛 ApiError」契约，
 * 并内置 SSE 逐包读取器（按 data: {json}\n\n 切包）。
 *
 * ## 令牌承载策略（P0-7 / P0-11 修复）
 *
 * | 令牌 | 存放位置 | 有效期 | 为什么 |
 * |------|----------|--------|--------|
 * | access | **内存变量**（非 localStorage） | 30 分钟 | XSS 无法持久读取；刷新即失效 |
 * | refresh | **HttpOnly Cookie**（JS 完全读不到） | 7 天 | XSS 即使命中也无法窃取长效凭据 |
 * | csrf | 普通 Cookie（JS 可读） | 7 天 | 需回填到 `X-CSRF-Token` 头做双提交校验 |
 *
 * 早期实现把 access + refresh **都明文存 localStorage**，等于把 7 天的
 * 完整会话暴露给任何一次 XSS（第三方 npm 依赖、富文本渲染、Markdown 注入
 * 都可能触发）。现改为上述组合后，XSS 的最坏后果从「长期接管账号」降级
 * 为「当前标签页 30 分钟内被借用」。
 *
 * ## 为什么用内存而非 sessionStorage？
 * sessionStorage 同样可被 XSS 读取，只是不跨标签页。内存变量对 XSS 的
 * 读取门槛更高（需在同一执行上下文注入），且天然随页面关闭而清除。
 *
 * ## 租户视角（`X-Tenant-Id`）
 * 平台管理员专属能力，见 `tenantScope`。**它不是凭据**，因此可以持久化；
 * 但它决定「看到谁的数据」，登出必须清空。
 */

export const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE) ||
  "http://localhost:8000";

export interface ApiErrorDetail {
  code: string;
  message: string;
  details?: unknown;
}

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(message: string, code: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

/* ============================================================================
 * 413 内容过长（CONTENT_TOO_LARGE）—— 全局拦截
 * ----------------------------------------------------------------------------
 * ## 为什么必须在 SDK 这一层拦截
 *
 * 后端对超长文本返回 **413**（`code=CONTENT_TOO_LARGE`，见 `app/main.py` 的
 * `_too_long_to_413`），错误体里带 `field` / `max_length` / `guidance`。
 * 若让每个页面各自 try/catch，必然有页面漏掉 ⇒ 用户看到「点了没反应」
 * 或一条语焉不详的报错。统一在这里派发事件，各端订阅后渲染决策门（B1）。
 *
 * ## 为什么派发事件而不是直接弹 UI
 *
 * SDK 是**纯 HTTP 客户端**，不依赖 React / 组件库。派发事件让各端自选渲染方式，
 * 也避免「SDK 里塞 UI」这种层间越界（越界后组件库一改就要动 SDK）。
 *
 * ⚠️ 派发**不改变**错误语义：413 照常抛 `ApiError`，调用方原有的
 * catch 逻辑不受影响——决策门只是额外获得了一次「给条出路」的机会。
 * ========================================================================== */

export const CONTENT_TOO_LARGE_CODE = "CONTENT_TOO_LARGE";

/** 后端 413 错误体里与超限相关的字段（`app/main.py::_too_long_to_413`）。 */
export interface ContentTooLargeDetail {
  /** 触发 413 的字段名，如 `source_text`。 */
  field?: string | null;
  /** 该字段的字符上限。**前端计数必须用它**，不要另写一份常量。 */
  max_length?: number | null;
  /** 恒为 `upload_or_async`：引导改走文件上传 + 异步任务。 */
  guidance?: string | null;
}

export interface ContentTooLargeEvent {
  path: string;
  detail: ContentTooLargeDetail;
  message: string;
}

type ContentTooLargeHandler = (event: ContentTooLargeEvent) => void;

const _contentTooLargeHandlers = new Set<ContentTooLargeHandler>();

/** 订阅 413。**返回取消订阅函数**（便于在 useEffect 里清理）。 */
export function onContentTooLarge(handler: ContentTooLargeHandler): () => void {
  _contentTooLargeHandlers.add(handler);
  return () => {
    _contentTooLargeHandlers.delete(handler);
  };
}

/**
 * 判断一个错误是否为「内容过长」——调用方据此决定渲染决策门还是普通报错。
 * `status === 413` 与 `code === CONTENT_TOO_LARGE` 任一命中即可：
 * 中间层（网关/CDN）可能改写状态码，但不会改写业务错误码。
 */
export function isContentTooLarge(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 413 || err.code === CONTENT_TOO_LARGE_CODE);
}

function dispatchContentTooLarge(event: ContentTooLargeEvent): void {
  // 快照遍历：订阅者在回调里取消订阅也不会破坏本次遍历
  for (const handler of Array.from(_contentTooLargeHandlers)) {
    try {
      handler(event);
    } catch {
      // 单个订阅者抛错不能影响其它订阅者，更不能吞掉原始 413
    }
  }
}

/* ============================================================================
 * 401 会话过期 —— 全局广播（与 413 同一事件总线模式，P2-7）
 * ----------------------------------------------------------------------------
 * ## 为什么需要它
 *
 * 401 静默刷新失败（会话真死）此前只把错误抛给调用方：挂了 useAuthGuard
 * 的页面会跳登录，但**弹窗里、轮询里、未挂守卫的页面**只能各拿一条 401
 * toast，处理散落且体验不一致。
 *
 * ## 语义
 *
 * - 只在「刷新也救不回来」时派发（登录等 `_noRetry` 请求不派发——那不是
 *   会话过期，是凭据错误）。
 * - 派发**不改变**错误语义：401 照常抛 ApiError，调用方原有 catch 不受影响。
 * - useSession 订阅后统一置 unauthenticated，全应用同帧进入登录页。
 * ========================================================================== */

export interface SessionExpiredEvent {
  /** 触发会话过期的请求路径（诊断用）。 */
  path: string;
}

type SessionExpiredHandler = (event: SessionExpiredEvent) => void;

const _sessionExpiredHandlers = new Set<SessionExpiredHandler>();

/** 订阅会话过期事件。**返回取消订阅函数**（便于在 useEffect 里清理）。 */
export function onSessionExpired(handler: SessionExpiredHandler): () => void {
  _sessionExpiredHandlers.add(handler);
  return () => {
    _sessionExpiredHandlers.delete(handler);
  };
}

function dispatchSessionExpired(path: string): void {
  for (const handler of Array.from(_sessionExpiredHandlers)) {
    try {
      handler({ path });
    } catch {
      // 单个订阅者抛错不能影响其它订阅者，更不能吞掉原始 401
    }
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  token?: string | null;
  query?: Record<string, string | number | undefined>;
  signal?: AbortSignal;
  /**
   * 覆盖本次请求的租户视角。**默认不传**——绝大多数调用方应依赖
   * `tenantScope` 单例，逐请求传参只用于「必须落在固定租户」的少数场景。
   * 传 `null` 表示显式不带该头（回到账号自身租户）。
   */
  tenantId?: string | null;
  /** 内部使用：标记该请求已由刷新逻辑重试过，防止无限递归 */
  _retried?: boolean;
  /** 内部使用：禁止 401 自动刷新重试（登出等场景必须真正失败） */
  _noRetry?: boolean;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(API_BASE + path);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

/** 读取 Cookie（CSRF 令牌用；refresh 是 HttpOnly，这里读不到也不该读到）。 */
function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

export const CSRF_COOKIE_NAME = "nlaw_csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

/**
 * 令牌存储：**仅保存在内存**。
 *
 * 保留 `set/get/clear` 三个方法名，是为了让各页面调用点无需改动
 * （原先它们调 `tokenStore.set(access, refresh)`）。但语义已变：
 * `refresh` 参数被忽略——它现在由服务端写入 HttpOnly Cookie。
 */
export interface TokenStore {
  get: () => string | null;
  set: (access: string, refresh?: string) => void;
  clear: () => void;
}

let _accessToken: string | null = null;

export const tokenStore: TokenStore = {
  get: () => _accessToken,
  set: (access) => {
    // 注意：refresh 不再落地到任何可被 JS 读取的位置
    _accessToken = access || null;
  },
  clear: () => {
    _accessToken = null;
  },
};

/* ============================================================================
 * 租户视角（`X-Tenant-Id`）—— 平台管理员专属
 * ----------------------------------------------------------------------------
 * ## 为什么需要它
 *
 * 后端 `app/core/deps.py::get_tenant_context()` 对 `Role.PLATFORM_ADMIN`
 * 有一条特殊分支：允许通过 `X-Tenant-Id` 头部把租户上下文切换到目标租户；
 * **其余角色一律强制使用自身租户，该头部被完全忽略**。
 *
 * 而 admin 账号自身归属 `platform` 租户（`app/seed/data.py::DEMO_USERS`）。
 * `platform` 是**法规库 / 案例库 / 文书模板库的共享域**，真实业务数据
 * 全部落在 `firm_hlw`（`app/seed/business.py:64`）。
 *
 * ## ⚠️ 默认视角不是「全零」，是「平台演示数据」（实测更正）
 *
 * 曾据 `grep tenant_id app/seed/business.py | head -8` 只看到 `firm_hlw`，
 * 就写下「platform 是空租户、驾驶舱恒为 0」，并据此写过界面文案。
 * **端到端实测直接打翻**：`platform` 下有 **4 条 `PLT-DEMO-*` 案件**——
 * 同一文件后半段还有一个 `_seed_platform()`（`business.py:243`），
 * 注释原文「让默认登录的平台管理员也能看到非空驾驶舱」。
 *
 * 这比全零**更隐蔽**：全零会被当成「没数据」，4 条演示数据会被当成真实业务。
 * 界面文案必须明确写「演示数据，不代表任何真实业务租户」。
 * 实测脚本：`backend/verify_admin_tenant_scope.py`（15/15 通过，约 20 秒）。
 *
 * 切换租户视角是平台管理员看到指定租户真实数据的**唯一**途径，
 * 因为后端**没有任何**跨租户聚合端点（唯一例外是投诉举报模块，
 * 见 `app/api/v1/complaints.py`，它对平台管理员走全局查询）。
 *
 * ## 为什么是模块级单例，而不是逐请求参数
 *
 * 驾驶舱一次加载会并发十几个请求。把 `tenantId` 逐个透传既是噪音，
 * 也必然漏掉一两处，后果是「同一页面上半截是 A 租户、下半截是 B 租户」——
 * 一种看起来完全正常、但数字互相矛盾的静默错误。
 * 视角是**应用级**概念，与 `tokenStore` 同级。
 *
 * 四端是四个独立打包单元，模块级状态天然不会跨应用泄漏。
 *
 * ## 安全边界（三层，缺一不可）
 * 1. **服务端是唯一门禁**：非 `PLATFORM_ADMIN` 带这个头没有任何效果。
 * 2. **登出必须清空**（见 `logout()`）：否则残留视角会让下一个登录者
 *    带着前一个人的租户上下文发请求。
 * 3. **前端做形状校验**：只接受 `[A-Za-z0-9_-]{1,64}`，与后端
 *    `TenantMixin.tenant_id` 的 `String(64)` 对齐，顺带挡掉换行等非法头字符。
 * ========================================================================== */

export const TENANT_HEADER = "X-Tenant-Id";
/** sessionStorage 键名。用 session 而非 local：关闭标签页即复位，减少残留窗口。 */
export const TENANT_SCOPE_STORAGE_KEY = "nlaw_tenant_scope";
/** 与后端 `TenantMixin.tenant_id` 的 `String(64)` 对齐。 */
const TENANT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

function readPersistedTenantScope(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.sessionStorage.getItem(TENANT_SCOPE_STORAGE_KEY);
    return v && TENANT_ID_RE.test(v) ? v : null;
  } catch {
    // 隐私模式 / 存储被禁用：降级为「不持久化」，功能不受影响
    return null;
  }
}

let _tenantScope: string | null = readPersistedTenantScope();

function persistTenantScope(value: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (value) window.sessionStorage.setItem(TENANT_SCOPE_STORAGE_KEY, value);
    else window.sessionStorage.removeItem(TENANT_SCOPE_STORAGE_KEY);
  } catch {
    /* 存储不可用：内存中的值仍然生效 */
  }
}

export interface TenantScopeStore {
  /** 当前视角；`null` = 跟随账号自身租户 */
  get: () => string | null;
  /** 设置视角。返回 `false` 表示值非法、已忽略（调用方应据此提示）。 */
  set: (tenantId: string | null) => boolean;
  clear: () => void;
}

export const tenantScope: TenantScopeStore = {
  get: () => _tenantScope,
  set: (tenantId) => {
    const v = (tenantId ?? "").trim();
    if (v && !TENANT_ID_RE.test(v)) return false;
    _tenantScope = v || null;
    persistTenantScope(_tenantScope);
    return true;
  },
  clear: () => {
    _tenantScope = null;
    persistTenantScope(null);
  },
};

/** 并发刷新去重：多个请求同时 401 时只发一次 refresh。 */
let _refreshing: Promise<boolean> | null = null;
/** 上次刷新失败的时点（负缓存）：refresh Cookie 已死时避免连环打注定失败的请求。 */
let _refreshFailedAt = 0;
const REFRESH_RETRY_COOLDOWN_MS = 30_000;

async function refreshAccessToken(): Promise<boolean> {
  if (_refreshing) return _refreshing;
  // 失败负缓存：Cookie 过期/登出后，页面并发 N 个 401 会排队打 N 次注定
  // 失败的 refresh。冷却期内直接判死；重新登录成功时会复位（见 login）。
  if (_refreshFailedAt && Date.now() - _refreshFailedAt < REFRESH_RETRY_COOLDOWN_MS) {
    return false;
  }
  _refreshing = (async () => {
    try {
      const res = await fetch(buildUrl("/api/v1/auth/refresh"), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          // 双提交校验：HttpOnly refresh cookie 会被自动带上，CSRF 需手动回填
          [CSRF_HEADER_NAME]: readCookie(CSRF_COOKIE_NAME) ?? "",
        },
        // 兼容旧后端：Cookie 模式下 body 可省略
        body: JSON.stringify({}),
        credentials: "include",
      });
      if (!res.ok) return false;
      const json = await res.json();
      const access = json?.data?.access_token;
      if (!access) return false;
      _accessToken = access;
      return true;
    } catch {
      return false;
    } finally {
      _refreshing = null;
    }
  })();
  // 失败即写入负缓存时点（成功则保持 0）：落在唯一出口，避免多处 return 漏标
  const ok = await _refreshing;
  _refreshFailedAt = ok ? 0 : Date.now();
  return ok;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  /*
   * `FormData` 走 multipart：**不能**自己设 `Content-Type` ——
   * `boundary` 必须由浏览器生成，手写一个会让后端解不出字段（表现为「请求到了，文件却是空的」）。
   * 同理**不能** `JSON.stringify`（那会把 FormData 变成 `{}`）。
   * ⇒ `upload()` 正是靠这一条分支，复用本函数的鉴权 / 401 刷新 / 租户头 / CSRF / 错误解包。
   */
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  if (opts.body !== undefined && !isForm) headers["Content-Type"] = "application/json";

  const token = opts.token !== undefined ? opts.token : _accessToken;
  if (token) headers["Authorization"] = `Bearer ${token}`;

  /*
   * 租户视角。无条件回填是安全的——真正的门禁在服务端
   * （`get_tenant_context()` 只对 PLATFORM_ADMIN 认这个头）。
   * 前端这一层只负责「让管理员能表达他想看哪个租户」。
   */
  const tid = opts.tenantId !== undefined ? opts.tenantId : _tenantScope;
  if (tid) headers[TENANT_HEADER] = tid;

  /*
   * 默认方法由「是否带 body」推导，而不是一律 GET。
   *
   * 为什么必须这样：`fetch(url, { method: "GET", body })` 在浏览器里会**直接抛
   * TypeError**（"Request with GET/HEAD method cannot have body"），请求根本不会
   * 发出去。而 `authed(path, { body })` 这种写法极其自然，调用方很容易漏掉
   * `method: "POST"`——一旦漏掉，得到的不是 405 而是「点了没反应 + 控制台一条
   * TypeError」，排查成本很高。
   *
   * 实测该写法已造成 4 个页面 6 处写操作全部失效（文书生成三步、合规扫描、
   * 收入预测、知识文档新增）。带 body 的 GET 没有任何合法用途，
   * 因此这里把「有 body」直接判为 POST，从机制上消除这一类缺陷。
   */
  const method = opts.method ?? (opts.body !== undefined ? "POST" : "GET");
  // 写方法且未使用 Bearer 时，可能需要 CSRF 头（后端对 refresh 端点强制校验）。
  // 提前带上无副作用：后端仅在需要校验的路径上读取它。
  if (method !== "GET" && !token) {
    const csrf = readCookie(CSRF_COOKIE_NAME);
    if (csrf) headers[CSRF_HEADER_NAME] = csrf;
  }

  const res = await fetch(buildUrl(path, opts.query), {
    method,
    headers,
    body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
    signal: opts.signal,
    // 必须 include：否则跨域（前端 3000 → 后端 8000）不会发送/接收 Cookie
    credentials: "include",
  });

  // 访问令牌过期：自动刷新后重试一次（对调用方透明）
  // 注意 `_noRetry`：登出等「必须真正失败」的请求不能走刷新重试，
  // 否则会在登出时把令牌重新签发回来，登出形同虚设。
  if (res.status === 401 && !opts._retried && !opts._noRetry && !path.includes("/auth/refresh")) {
    const ok = await refreshAccessToken();
    if (ok) {
      return request<T>(path, { ...opts, _retried: true, token: _accessToken });
    }
    // 会话确实死了：连租户视角一起清掉。否则重新登录后，
    // 新用户会带着上一个人的租户上下文发出第一批请求。
    _accessToken = null;
    tenantScope.clear();
    // 广播会话过期（P2-7）：弹窗 / 轮询 / 未挂守卫的页面同步感知，
    // 不再各自拿到一条孤立的 401。错误仍照常上抛，语义不变。
    dispatchSessionExpired(path);
  }

  let json: any = null;
  try {
    json = await res.json();
  } catch {
    // 2xx 但解析不出 JSON：网关/代理截断或契约漂移，绝不能静默按 null 走下去
    throw new ApiError(
      res.ok ? "2xx 响应解析不出 JSON（疑似网关截断或契约漂移）" : "响应解析失败",
      res.ok ? "CONTRACT_MISMATCH" : "PARSE_ERROR",
      res.status,
    );
  }

  // 413：内容过长 —— 派发全局事件（B1 决策门的数据来源），随后照常抛错。
  // 放在抛错**之前**：派发只是一次「额外给条出路」的通知，不改变错误语义。
  if (res.status === 413) {
    const d = (json?.error ?? {}) as Partial<ContentTooLargeDetail> & { message?: string };
    dispatchContentTooLarge({
      path,
      detail: {
        field: d.field ?? null,
        max_length: d.max_length ?? null,
        guidance: d.guidance ?? null,
      },
      message: d.message ?? "内容超出处理上限",
    });
  }

  if (json && json.success === false) {
    const err = json.error as ApiErrorDetail;
    throw new ApiError(
      err?.message ?? "请求失败",
      err?.code ?? "UNKNOWN",
      res.status,
      err?.details,
    );
  }
  if (!res.ok) {
    throw new ApiError(json?.error?.message ?? "请求失败", json?.error?.code ?? "UNKNOWN", res.status);
  }
  return (json?.data ?? null) as T;
}

export const http = {
  get: <T>(p: string, o?: RequestOptions) => request<T>(p, { ...o, method: "GET" }),
  post: <T>(p: string, body?: unknown, o?: RequestOptions) =>
    request<T>(p, { ...o, method: "POST", body }),
  put: <T>(p: string, body?: unknown, o?: RequestOptions) =>
    request<T>(p, { ...o, method: "PUT", body }),
  patch: <T>(p: string, body?: unknown, o?: RequestOptions) =>
    request<T>(p, { ...o, method: "PATCH", body }),
  del: <T>(p: string, o?: RequestOptions) => request<T>(p, { ...o, method: "DELETE" }),
};

export type UploadPayload = FormData | Blob | Array<Blob>;

/** 类型守卫。写成函数而不是内联 `typeof FormData !== "undefined" && x instanceof FormData`：
 *  内联写法在 `else` 分支**收窄不掉 `FormData`**（`else` 还包含「`FormData` 不存在」这一种可能），
 *  于是 `form.append(name, item)` 会报 `FormData | Blob` 不可赋值 —— 被迫加 `as` 转型。 */
function isFormData(v: unknown): v is FormData {
  return typeof FormData !== "undefined" && v instanceof FormData;
}

export interface UploadOptions extends Omit<RequestOptions, "body" | "method"> {
  /** 表单字段名（传 `File` / `Blob` / 数组时用），默认 `"file"` */
  fieldName?: string;
  /** 追加的普通表单字段；`undefined` / `null` 会被跳过 */
  fields?: Record<string, string | number | boolean | undefined | null>;
}

/**
 * 文件上传（`multipart/form-data`）。
 *
 * **为什么必须单独有一个函数**：`request()` 对「带 body」的请求会设
 * `Content-Type: application/json` 并 `JSON.stringify` —— 对 `FormData` 这两步都会
 * **静默毁掉请求**（`boundary` 丢失、文件变成 `{}`，而后端只会说「文件是空的」）。
 * 而让三端各抄一份「鉴权 / 401 刷新 / 租户头 / CSRF / 错误解包」就是三份会漂移的实现。
 * ⇒ 本函数**只负责组装 `FormData`**，其余原样走 `request()`。
 *
 * ```ts
 * // 单文件（后端 `file: UploadFile = File(...)` 只收一个字段值 ⇒ 多张要循环调用）
 * await upload(`/evidence/cases/${caseId}`, captured.file);
 * // 自定义字段名 + 附加字段
 * await upload("/documents/upload", files, { fieldName: "files", fields: { source: "camera" } });
 * ```
 */
export function upload<T>(
  path: string,
  payload: UploadPayload,
  opts: UploadOptions = {},
): Promise<T> {
  const { fieldName = "file", fields, ...rest } = opts;

  let form: FormData;
  if (isFormData(payload)) {
    form = payload;
  } else {
    form = new FormData();
    for (const item of Array.isArray(payload) ? payload : [payload]) {
      // `Blob` 没有 `name`；不补文件名时后端只会收到字面量 "blob"
      const filename =
        typeof File !== "undefined" && item instanceof File ? item.name : undefined;
      if (filename === undefined) form.append(fieldName, item);
      else form.append(fieldName, item, filename);
    }
  }
  if (fields) {
    for (const [k, v] of Object.entries(fields)) {
      if (v !== undefined && v !== null) form.append(k, String(v));
    }
  }

  return request<T>(path, { ...rest, method: "POST", body: form });
}

/**
 * SSE 逐包读取器。后端以 `data: {json}\n\n` 推送，按双换行切包。
 *
 * 与 `request()` 对齐的三件事（P1-8）：
 * 1. **401 先静默刷新再重连一次**——access 30 分钟一过期，若不刷新，
 *    所有 AI 流式在下一次 REST 触发刷新前全部失败（对以流式输出为主的
 *    产品，这是最高频的失败路径）。
 * 2. **`try/finally` 释放 reader 并取消底层连接**——消费方提前 `break` /
 *    AbortSignal 触发时，不清理会让连接一直挂着直到进程回收（泄漏）。
 * 3. **流结束 flush 残留 buffer**——服务端未以空行收尾的最后一个事件
 *    原实现会被静默丢弃；单包解析失败也不再静默，留 `console.warn` 便于定位。
 */
export async function* streamSSE(
  path: string,
  body: unknown,
  opts: { token?: string | null; signal?: AbortSignal; tenantId?: string | null } = {},
): AsyncGenerator<any, void, unknown> {
  const buildHeaders = (token: string | null): Record<string, string> => {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      // 与 request() 对齐：SSE 的 POST 同样可能被 CSRF 双提交校验拦截
      [CSRF_HEADER_NAME]: readCookie(CSRF_COOKIE_NAME) ?? "",
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    // 与 request() 保持一致：SSE 若不带租户视角，会出现「列表是 A 租户、
    // 流式结果来自 B 租户」的错位。
    const tid = opts.tenantId !== undefined ? opts.tenantId : _tenantScope;
    if (tid) headers[TENANT_HEADER] = tid;
    return headers;
  };

  const open = (token: string | null) =>
    fetch(buildUrl(path), {
      method: "POST",
      headers: buildHeaders(token),
      body: JSON.stringify(body),
      signal: opts.signal,
      credentials: "include",
    });

  let token = opts.token !== undefined ? opts.token : _accessToken;
  let res = await open(token);
  if (res.status === 401 && (await refreshAccessToken())) {
    token = _accessToken;
    res = await open(token);
  }
  if (!res.ok || !res.body) {
    // 刷新也救不回来：广播会话过期（弹窗/轮询等未挂守卫的页面同步感知）
    if (res.status === 401) dispatchSessionExpired(path);
    throw new ApiError("流式连接失败", "SSE_ERROR", res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          yield JSON.parse(payload);
        } catch (e) {
          // 单包解析失败不再静默吞掉：至少留一条现场日志便于定位契约问题
          console.warn("[nlaw/sdk] SSE 事件解析失败", e, payload.slice(0, 120));
        }
      }
    }
    // 流结束：flush 解码器残留，并处理服务端未以空行收尾的最后一个事件
    buffer += decoder.decode();
    const line = buffer.split("\n").find((l) => l.startsWith("data:"));
    const payload = line?.slice(5).trim();
    if (payload) {
      try {
        yield JSON.parse(payload);
      } catch (e) {
        console.warn("[nlaw/sdk] SSE 尾包解析失败", e, payload.slice(0, 120));
      }
    }
  } finally {
    // 消费方提前 break / AbortSignal 触发时必须释放 reader 并取消底层连接，
    // 否则连接会一直挂着直到进程回收（泄漏）。
    try {
      await reader.cancel();
    } catch {
      // 流已被对端/中断关闭
    }
    reader.releaseLock();
  }
}

/** 携带当前 token 的请求便捷封装。 */
export function authed<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  // P2-8 快照漂移修复：不再在调用时刻快照 token。刷新与请求并发时，
  // 调用时刻的快照可能是旧值——这样的请求每个都要白挨一个 401 才能靠
  // 重试兜底。不传 token 让 request() 在发请求时读取模块级 _accessToken
  // （单一事实来源，永远是最新值）。
  return request<T>(path, opts);
}

/**
 * 登录：保存 access（内存），refresh 由服务端写 HttpOnly Cookie。
 *
 * 注意 `credentials: "include"` —— 跨域场景下不加这个，浏览器会**丢弃**
 * 响应里的 Set-Cookie，导致 refresh 静默失败（表现为「登录成功但一会儿就掉线」）。
 */
export async function login(username: string, password: string): Promise<any> {
  // token: null 确保不带旧的 Authorization 头；此时 request() 会自动回填 CSRF 头
  const data = await http.post<any>(
    "/api/v1/auth/login",
    { username, password },
    { token: null, _noRetry: true },
  );
  if (data?.access_token) {
    _accessToken = data.access_token;
    // 重新登录成功 ⇒ refresh 负缓存作废（此前的失败只说明旧 Cookie 已死）
    _refreshFailedAt = 0;
  }
  return data;
}

/** 登出：清内存令牌 + 让服务端清 Cookie。
 *
 * `_noRetry` 是关键：否则若 access 已过期，logout 会先触发 401 → 静默刷新 →
 * 重新签发令牌，用户点了「退出」却仍是登录态。
 */
export async function logout(): Promise<void> {
  try {
    await request("/api/v1/auth/logout", {
      method: "POST",
      token: _accessToken,
      body: {},
      _noRetry: true,
    });
  } catch {
    // 网络异常也继续清理本地状态
  } finally {
    // 无论服务端是否成功，本地内存令牌必须清空
    _accessToken = null;
    // 租户视角同样必须清空——它决定「看到谁的数据」，
    // 残留会让下一个登录者直接看到上一个管理员的租户上下文。
    tenantScope.clear();
  }
}

/**
 * 页面加载时恢复会话：用 HttpOnly Cookie 静默换一个 access 令牌。
 * 返回是否恢复成功（用于替代原先的 `if (!tokenStore.get()) redirect`）。
 */
export async function restoreSession(): Promise<boolean> {
  if (_accessToken) return true;
  return refreshAccessToken();
}
