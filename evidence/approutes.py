"""前端**路由前缀**（`basePath`）与端口的**唯一真源**。

## 为什么必须有这个模块（2026-10-10 事故）

`apps/{lawyer,admin,im}/next.config.js` 自 **2026-10-01 14:42** 起配了 `basePath`
（`/lawyer` · `/admin` · `/im`，注释写明是为 `https://bot.tomneil.asia/<app>` 的
nginx 前缀转发），即应用整体挂在**前缀**下：

| 路径 | 码 | 路径 | 码 |
|---|---|---|---|
| `/` · `/login` · `/cases` | **404** | `<前缀>` · `<前缀>/login` · `<前缀>/cases` | **200** |

而当时的 `run_browser_job.py` 就绪探针与 15 条门禁探针**全都按无前缀写**
（`grep -rn "basePath" evidence/*.py` ⇒ **0 命中**）⇒ 每一条 `goto` 都 404
⇒ `browser-all` / `browser-admin` **自 10-01 起必然 exit 2**。

🚨 **它静默了 9 天**，两个放大因素缺一不可：
  ① 本仓**非 git 仓库** ⇒ `ci.yml` **永不触发**；
  ② 这两个 job **只在 main / 手动**跑，PR 上不跑（靠源码级地板兜底 —— 见 README 坑 74）。
⇒ 教训：**「有个 job 会跑它」≠「每个 PR 都会跑它」**；而**跨端配置改了、探针没跟**
   这类漂移，只有**跑一次**才看得见。

## 🚨 本模块最容易用错的一点：`Origin` 头**不能带路径**

`app_origin()` 与 `app_base()` / `app_url()` 是**两个不同的东西**：

- **`app_origin(app)`** —— 给 CORS 的 `Origin` 请求头用。**只到端口**。
  写 `Origin: http://localhost:<port>/admin` 是**错的**：`Origin` 的语法是
  `scheme://host[:port]`，**不含 path**；带上路径会让后端的 CORS 白名单**匹配不上**，
  症状是「服务明明起着却跨源被拒」——**而探针会把它读成产品缺陷**。
- **`app_base(app)` / `app_url(app, path)`** —— 给**页面导航**用，**必须带前缀**。

⚠️ `envprobe.ADMIN` 历史上**同时**承担了这两个角色（既当 `Origin` 又拼 `/login`），
本次只把「拼页面 URL」的那部分改成 `app_url()`，**常量本身保持只到端口**。

## 判据

`evidence/verify_base_path_consistency.py` 会拿本模块的 `APP_BASE_PATH`
去和四份 `next.config.js` 里的 `basePath` **逐端比对**，并拦截门禁探针里
「直接写 `localhost:300<端>/...`」这种**绕过本模块**的写法。
⇒ 所以本模块**不是**可以随手改的常量表：改它必须同时改 `next.config.js`。
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: 四端端口。与 `run_browser_job.APP_PORTS` / `ci.yml` 必须一致。
APP_PORT: dict[str, int] = {
    "web": 3000,
    "lawyer": 3001,
    "admin": 3002,
    "im": 3003,
}

#: 四端 `next.config.js` 的 `basePath`。**空串 = 无前缀**。
#: 🚨 改这里必须同时改 `apps/<app>/next.config.js`，否则判据会红（这是**故意**的）。
APP_BASE_PATH: dict[str, str] = {
    "web": "",
    "lawyer": "/lawyer",
    "admin": "/admin",
    "im": "/im",
}

_PORT_APP: dict[int, str] = {port: app for app, port in APP_PORT.items()}


def app_origin(app: str) -> str:
    """CORS `Origin` 头的值：**只到端口，绝不带 `basePath`**（见模块 docstring）。

    >>> app_origin("admin")
    'http://localhost:3002'
    """
    return f"http://localhost:{APP_PORT[app]}"


def app_base(app: str) -> str:
    """页面导航用的**基址**：`http://localhost:<端口><前缀>`，**无尾斜杠**。

    刻意不返回尾斜杠：调用方普遍写 `f"{base}{path}"` 且 `path` 自带前导 `/`
    （如 `f"{base}/login"`）⇒ 返回尾斜杠会拼出 `//login`。

    >>> app_base("admin")
    'http://localhost:3002/admin'
    >>> app_base("web")
    'http://localhost:3000'
    """
    return f"http://localhost:{APP_PORT[app]}{APP_BASE_PATH[app]}"


def base_for_port(port: int) -> str:
    """`app_base()` 的**按端口**版本 —— 给 `--port` 类探针（如 im 的 3003）。

    未知端口 ⇒ **前缀按空串处理**（保持改动前的行为，不让它意外崩）。
    ⚠️ 但这会让「端口写错」退化成 404 而不是报错 ⇒ 新写探针**优先用 `app_base(app)`**。
    """
    return f"http://localhost:{port}{APP_BASE_PATH.get(_PORT_APP.get(port, ""), "")}"


def app_url(app: str, path: str = "/") -> str:
    """带前缀的**完整页面 URL**。`path` 可省前导 `/`。

    >>> app_url("admin", "/login")
    'http://localhost:3002/admin/login'
    >>> app_url("web", "/qa")
    'http://localhost:3000/qa'
    """
    if not path.startswith("/"):
        path = "/" + path
    return f"{app_base(app)}{path}"


def url_for_port(port: int, path: str = "/") -> str:
    """`app_url()` 的**按端口**版本（`--port` 类探针用）。"""
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_for_port(port)}{path}"


def base_path_for_port(port: int) -> str:
    """该端口的 `basePath` 前缀（`""` = 无前缀）。**给拼 DOM 选择器用**。

    🚨 为什么不能省：`basePath` 落地后，`next/link` 渲染出的 `href` 是
    **带前缀**的（`/im/cases/123`）。探针里那种
    `a[href^="/cases/"]` 的选择器会**一个都匹配不到** ⇒ 探针报「环境问题」，
    而页面其实**好好的**（2026-10-10 实测 3 条探针栽在这上面）。
    """
    return APP_BASE_PATH.get(_PORT_APP.get(port, ""), "")


def base_path_of_base(base: str) -> str:
    """从 `app_base()` / `base_for_port()` 造出的**基址**里取出 `basePath` 部分。

    给那些「手里只有 `base` 字符串、没有端口」的探针用。

    >>> base_path_of_base("http://localhost:3003/im")
    '/im'
    >>> base_path_of_base("http://localhost:3000")
    ''
    """
    return urlsplit(base).path.rstrip("/")


def strip_prefix(prefix: str, path: str) -> str:
    """把 `prefix` 从 `path` 开头剥掉（`prefix` 为空 ⇒ 原样返回）。

    ⚠️ 剥完**保证**以 `/` 开头：`path == prefix` 时返回 `"/"`（应用根），
       而不是空串 —— 否则调用方拼 URL 会少一个斜杠。
    """
    if prefix and path.startswith(prefix):
        rest = path[len(prefix):]
        return rest if rest.startswith("/") else "/" + rest
    return path


def strip_base_path(port: int, path: str) -> str:
    """把页面路径里的 `basePath` 剥掉 ⇒ 得到「**应用内路径**」。

    🚨 断言应当基于**应用内路径**，而不是浏览器里那个带前缀的 `location.pathname`：
        `verify_im_tabs` 曾断言「点「咨询」后路径 == `/chat`」，而 `basePath` 落地后
        它**正确地**变成了 `/im/chat` ⇒ 探针把**对的**报成「**产品缺陷 4 条**」。
        剥前缀后断言与前缀**解耦**：前缀以后再改也不会假红。

    ⚠️ 注意 `Next` 的 `usePathname()` **本来就剥掉 `basePath`** ——
       所以「产品侧的激活态推导」是对的，错的只有「探针读 `location.pathname`」这一侧。
    """
    return strip_prefix(base_path_for_port(port), path)
