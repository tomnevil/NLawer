"use client";

import React from "react";
import { Building2, Check, Globe2, Info, X } from "lucide-react";
import { tenantScope } from "@nlaw/sdk";
import { Badge, Button, Input, Spinner, useSession } from "@nlaw/ui";

/**
 * 平台管理员的「租户视角」切换条。
 *
 * ## 它解决的是哪一个问题
 *
 * 默认登录（`admin` → `platform` 租户）看到的是**平台演示数据**，
 * 不是任何真实业务租户的数据。三个域是这样分的：
 *
 * | 租户 | 内容 | 实测（`verify_admin_tenant_scope.py`） |
 * |---|---|---|
 * | `platform` | 法规库 / 案例库 / 文书模板库 + `PLT-DEMO-*` 演示案件 | 4 条案件 |
 * | `firm_hlw` | 律所真实业务 | 10 条案件 |
 * | `ent_acme` | 企业真实业务 | 0 条案件 |
 *
 * 后端 `cases` / `reviews` / `compliance` / `billing` 一律按 `ctx.tenant_id`
 * 过滤，**没有任何跨租户聚合端点**；而 `app/core/deps.py::get_tenant_context()`
 * 为 `PLATFORM_ADMIN` 留了 `X-Tenant-Id` 头部这条切换通道。本组件就是它的界面。
 *
 * 唯一的例外是投诉举报模块（`app/api/v1/complaints.py`），它对平台管理员
 * 走**跨租户全局查询**——所以「投诉举报」页在默认视角下就能看到全部租户的工单，
 * 且该页**刻意不受本切换条影响**。
 *
 * ## ⚠️ 一段被验证推翻的注释（留在这里防止重犯）
 *
 * 本组件初版写的是「`platform` 是共享域，**不含业务案件**，所以驾驶舱恒为 0」。
 * 端到端验证实测 `platform` 下有 **4 条** `PLT-DEMO-*` 案件 —— 断言直接失败。
 *
 * 错因：当初只跑了
 * `grep -n tenant_id app/seed/business.py | head -8`，看到 `line 64: tenant_id = "firm_hlw"`
 * 就下了结论，漏掉了文件后半段的 `_seed_platform()`（`business.py:243`），
 * 它的注释原文是「平台（platform）演示数据集：让默认登录的"平台管理员"也能看到非空驾驶舱」。
 *
 * **`head -N` 截断 `grep` 输出之后下的结论不算结论。**
 *
 * 于是正确的表述是：默认视角**非零**，但看到的是**演示数据**；
 * 切换视角的价值是「从演示数据到真实数据」，而不是「从零到有」。
 */
export function TenantScopeBar() {
  const { user, loading } = useSession();
  const [mounted, setMounted] = React.useState(false);
  const [current, setCurrent] = React.useState<string | null>(null);
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const [err, setErr] = React.useState("");
  const [switching, setSwitching] = React.useState(false);

  /*
   * 视角值来自 sessionStorage，服务端渲染时读不到。
   * 若直接在渲染期读取，SSR 得到 null、客户端得到 `firm_hlw`，
   * 会产生 hydration 不一致。因此统一在挂载后同步一次。
   */
  React.useEffect(() => {
    setCurrent(tenantScope.get());
    setMounted(true);
  }, []);

  /*
   * 切换视角用**整页重载**，而不是局部 refetch。
   *
   * 视角是模块级单例，改它不会让任何已挂载的 React 组件重新渲染，
   * 更不会取消已经在飞的请求。若只做局部 refetch，页面会出现
   * 「上半截是旧租户的响应、下半截是新租户的响应」的混合渲染——
   * 每一块都显示成功，数字却互相矛盾。这是比报错更难发现的一类错误。
   *
   * 重载是唯一能保证「新视角下不存在任何旧视角的在途/缓存响应」的做法。
   * 切换视角是低频运维动作，付出一次重载是划算的。
   */
  const apply = (value: string | null) => {
    if (!tenantScope.set(value)) {
      setErr("租户 ID 只能包含字母、数字、下划线、连字符，长度 1–64");
      return;
    }
    setErr("");
    setSwitching(true);
    window.location.reload();
  };

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // 未挂载或会话未就绪：占位以固定高度，避免布局跳动
  if (!mounted || loading) {
    return <div className="h-[54px] rounded-r2 border border-line bg-surface" aria-hidden />;
  }

  /*
   * 非平台管理员直接不渲染。
   *
   * 后端 `get_tenant_context()` 对非 PLATFORM_ADMIN **完全忽略**这个头部。
   * 一个点了没有任何效果的控件，比没有这个控件更糟——它会让用户以为
   * 「我切过去了」，然后对着一份没变的数据做判断。
   */
  if (user?.role !== "PLATFORM_ADMIN") return null;

  const scoped = current !== null;

  return (
    <div className="relative rounded-r2 border border-line bg-surface px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex items-center gap-1.5 text-body-sm font-medium text-ink-700">
          {scoped ? (
            <Building2 className="h-4 w-4 text-link" />
          ) : (
            <Globe2 className="h-4 w-4 text-ink-400" />
          )}
          租户视角
        </span>

        <Badge variant={scoped ? "primary" : "neutral"}>
          {scoped ? `租户 ${current}` : "平台本级"}
        </Badge>

        <p className="min-w-0 flex-1 text-caption text-ink-500">
          {scoped ? (
            <>以该租户的身份读取全部业务数据（案件 / 复核 / 合规 / 计费）。</>
          ) : (
            <>
              平台本级（<code className="num">platform</code>）是
              <strong className="font-medium text-ink-600">法规库、案例库、文书模板库</strong>
              的共享域，其下的案件是
              <strong className="font-medium text-ink-600">平台演示数据</strong>
              ，不代表任何真实业务租户——要看真实业务请切换到对应租户。
            </>
          )}
        </p>

        <Button
          variant="outline"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="dialog"
          disabled={switching}
        >
          {switching ? <Spinner size="sm" label={null} /> : null}
          切换视角
        </Button>
      </div>

      {open && (
        <>
          {/* 点击遮罩关闭。用独立元素而非 document 监听，避免与页面内其它点击处理打架 */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div
            role="dialog"
            aria-label="切换租户视角"
            className="absolute right-0 z-50 mt-2 w-[min(92vw,26rem)] rounded-r2 border border-line bg-surface shadow-s3"
          >
            <div className="flex items-center justify-between border-b border-line px-3.5 py-2.5">
              <h3 className="text-body-sm font-medium text-ink-900">切换租户视角</h3>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="tap-ghost -mr-1.5 text-ink-400 hover:text-ink-700"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3 p-3.5">
              {/* 平台本级 */}
              <button
                type="button"
                onClick={() => apply(null)}
                disabled={!scoped || switching}
                className="flex w-full items-start gap-2.5 rounded-r2 border border-line px-3 py-2.5 text-left transition-colors hover:bg-surface-subtle disabled:cursor-default disabled:opacity-60"
              >
                <Globe2 className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-body-sm font-medium text-ink-800">
                    平台本级
                    {!scoped && <Check className="h-3.5 w-3.5 text-link" />}
                  </span>
                  <span className="mt-0.5 block text-caption text-ink-500">
                    跟随账号自身租户（platform）。这里是共享域 + 平台演示数据，不是真实业务。
                  </span>
                </span>
              </button>

              {/* 指定租户 */}
              <div className="rounded-r2 border border-line px-3 py-2.5">
                <label
                  htmlFor="tenant-scope-input"
                  className="mb-1.5 block text-body-sm font-medium text-ink-800"
                >
                  指定租户
                </label>
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[10rem] flex-1">
                    <Input
                      id="tenant-scope-input"
                      value={draft}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        setErr("");
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && draft.trim()) apply(draft.trim());
                      }}
                      placeholder="如 firm_hlw"
                      error={err}
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => apply(draft.trim())}
                    disabled={!draft.trim() || switching}
                  >
                    切换
                  </Button>
                </div>
                <p className="mt-1.5 text-caption text-ink-400">
                  租户 ID 形如 <code className="num">firm_hlw</code>（律所）/
                  <code className="num">ent_acme</code>（企业）。
                </p>
              </div>

              {/*
                必须如实说明能力边界：后端没有租户列表端点。
                做成一个「看起来很完整」的下拉框，但里面只有硬编码的几个
                demo 租户，等于把「演示数据」伪装成「平台能力」。
              */}
              <p className="flex items-start gap-1.5 text-caption text-ink-400">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  后端<strong className="font-medium">未提供租户列表接口</strong>
                  ，因此这里是按 ID 直接输入，而不是下拉选择——下拉框里放硬编码的演示租户，
                  会让「平台能管理租户」这个错觉一直留在界面上。
                </span>
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
