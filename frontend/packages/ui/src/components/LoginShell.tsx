"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { login, ApiError } from "@nlaw/sdk";
import { cn } from "../lib/cn";
import { SegmentedControl } from "./SegmentedControl";
import { BottomSheet } from "./mobile/BottomSheet";

export interface DemoAccount {
  username: string;
  password: string;
  label: string;
  /** 角色分组，用于顶部切换 */
  group: string;
}

/** 演示账号（与 backend/app/seed/data.py 保持一致）。 */
export const DEFAULT_DEMO_ACCOUNTS: DemoAccount[] = [
  { username: "firm_admin", password: "Firm@12345", label: "律所管理员", group: "律所端" },
  { username: "lawyer_wang", password: "Lawyer@12345", label: "执业律师", group: "律所端" },
  { username: "assistant", password: "Assistant@12345", label: "律师助理", group: "律所端" },
  { username: "client", password: "Client@12345", label: "客户", group: "客户端" },
  { username: "ent_admin", password: "Ent@12345", label: "企业管理员", group: "客户端" },
  { username: "admin", password: "Admin@12345", label: "平台管理员", group: "平台端" },
];

export type LoginMode = "password" | "phone";

export interface LoginShellProps {
  appName: string;
  /**
   * 演示账号。**是否展示由 `NEXT_PUBLIC_ENABLE_DEMO_ACCOUNTS` 统一控制**：
   * 仅当该变量为 `"true"` 时才渲染演示区——明文凭据绝不能默认打进生产
   * bundle（仓库审查 P1-1）。开发环境在各 app 的 .env.local 里开启。
   */
  demoAccounts?: DemoAccount[];
  /**
   * 启用的登录方式，默认仅密码。
   *
   * `"phone"` 需要后端提供短信下发与验证码校验端点（当前 `app/api/v1/auth.py`
   * 只有 register / login / refresh / logout / me），因此默认不启用——与其
   * 展示一个点了没反应的入口，不如等接口就绪后再打开。
   */
  modes?: LoginMode[];
  /** 验证码登录回调，仅在 modes 含 "phone" 时生效 */
  onPhoneLogin?: (phone: string, code: string) => Promise<void>;
  /** 请求验证码回调 */
  onRequestCode?: (phone: string) => Promise<void>;
  /** 品牌区副标题 */
  tagline?: string;
  /** 登录成功后的跳转路径，默认 "/" */
  redirectTo?: string;
  /** 表单底部补充内容（协议、帮助链接等） */
  footer?: React.ReactNode;
}

function ScaleIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden
    >
      <path d="M12 3v18M7 21h10M5 7h14M5 7l-3 7h6L5 7Zm14 0-3 7h6l-3-7Z" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z" />
    </svg>
  );
}

const fieldCls =
  "w-full min-h-tap rounded-r2 border border-line bg-surface px-3 text-body text-ink-900 " +
  "placeholder:text-ink-400 transition-colors duration-fast " +
  "focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20";

/**
 * 登录页骨架（四端共用）。
 *
 * 设计要点：
 * 1. **品牌区用墨色 + 香槟金，不再用渐变**。渐变在深色模式下会与页面底色
 *    打架，且让「法律产品」显得轻浮；墨底 + 一条金线更接近文书封面的气质。
 * 2. **演示账号按角色分组**。6 个账号平铺会让人不知道该点哪个；按端分组后
 *    一眼就能找到「我要以什么身份看」。
 * 3. **移动端把演示账号收进底部抽屉**。表单本身才是主角，账号列表在手机上
 *    会把它挤出首屏。
 */
export function LoginShell({
  appName,
  demoAccounts = DEFAULT_DEMO_ACCOUNTS,
  modes = ["password"],
  onPhoneLogin,
  onRequestCode,
  tagline = "法律科技双产品线：律所智能协作平台 + 个人与企业法务助手。",
  redirectTo = "/",
  footer,
}: LoginShellProps) {
  const router = useRouter();
  const [mode, setMode] = React.useState<LoginMode>("password");
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [phone, setPhone] = React.useState("");
  const [code, setCode] = React.useState("");
  const [countdown, setCountdown] = React.useState(0);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [accountsOpen, setAccountsOpen] = React.useState(false);

  const groups = React.useMemo(
    () => Array.from(new Set(demoAccounts.map((a) => a.group))),
    [demoAccounts]
  );
  const [activeGroup, setActiveGroup] = React.useState(groups[0] ?? "");
  const groupAccounts = demoAccounts.filter((a) => a.group === activeGroup);

  // 分组变化（或账号列表变化）时兜底，避免 activeGroup 落在不存在的分组上
  React.useEffect(() => {
    if (groups.length > 0 && !groups.includes(activeGroup)) setActiveGroup(groups[0]);
  }, [groups, activeGroup]);

  // 验证码倒计时
  React.useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  const doLogin = async (u: string, p: string) => {
    setUsername(u);
    setPassword(p);
    setLoading(true);
    setError("");
    try {
      // login() 内部会把 access 存进内存、refresh 由服务端写入 HttpOnly Cookie
      await login(u, p);
      router.push(redirectTo);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  const doPhoneLogin = async () => {
    if (!onPhoneLogin) return;
    setLoading(true);
    setError("");
    try {
      await onPhoneLogin(phone, code);
      router.push(redirectTo);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  const requestCode = async () => {
    if (!onRequestCode) return;
    setError("");
    try {
      await onRequestCode(phone);
      setCountdown(60);
    } catch (e) {
      setError(e instanceof Error ? e.message : "验证码发送失败");
    }
  };

  const phoneValid = /^1[3-9]\d{9}$/.test(phone);
  const isPhoneMode = mode === "phone";

  const errorBanner = error ? (
    <div role="alert" className="rounded-r2 border border-danger-500/30 bg-danger-500/10 px-3 py-2 text-body-sm text-danger-600">
      {error}
    </div>
  ) : null;

  return (
    <div className="flex min-h-screen items-stretch bg-ink-50">
      {/* ── 品牌区（仅桌面）────────────────────────────────────── */}
      <div className="relative hidden w-[44%] flex-col justify-between overflow-hidden bg-brand-950 p-12 text-white lg:flex">
        {/* 极淡的纸纹：两条 1px 斜线，避免大色块显得死板 */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.06]"
          style={{
            backgroundImage:
              "repeating-linear-gradient(115deg, #fff 0 1px, transparent 1px 22px)",
          }}
        />

        <div className="relative flex items-center gap-3">
          <ScaleIcon className="h-7 w-7 text-gold-400" />
          <span className="text-body-lg font-medium tracking-wide">律小智</span>
        </div>

        <div className="relative">
          <h1 className="font-serif text-[40px] leading-[1.25] tracking-tight">{appName}</h1>
          <div className="mt-6 h-px w-16 bg-gold-500" />
          <p className="mt-6 max-w-md text-body text-white/70">{tagline}</p>
          <p className="mt-3 max-w-md text-caption text-white/45">
            本内容由 AI 生成，仅供参考，不构成法律意见。
          </p>
        </div>

        <div className="relative text-caption text-white/40">© 2026 律小智 · NLawer</div>
      </div>

      {/* ── 表单区 ─────────────────────────────────────────────── */}
      <div className="flex w-full items-center justify-center px-5 py-10 lg:w-[56%] lg:px-8">
        <div className="w-full max-w-[380px] animate-fade-in">
          {/* 移动端品牌头 */}
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-r2 border border-gold-500/40 bg-brand-950 text-gold-400">
              <ScaleIcon className="h-5 w-5" />
            </span>
            <div>
              <p className="text-body font-medium text-ink-900">律小智</p>
              <p className="text-caption text-ink-500">{appName}</p>
            </div>
          </div>

          <h2 className="text-h2 text-ink-900">{isPhoneMode ? "手机号登录" : "登录"}</h2>
          <p className="mt-1 text-body-sm text-ink-500">
            {isPhoneMode ? "未注册的手机号将自动创建账号" : `使用${appName}工作台账号登录`}
          </p>

          {/* 登录方式切换：仅在启用多种方式时出现 */}
          {modes.length > 1 && (
            <SegmentedControl
              className="mt-5 w-full"
              fullWidth
              size="md"
              ariaLabel="登录方式"
              value={mode}
              onChange={(v) => {
                setMode(v as LoginMode);
                setError("");
              }}
              options={[
                { value: "password", label: "账号密码" },
                { value: "phone", label: "验证码" },
              ]}
            />
          )}

          <form
            className="mt-5 space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (isPhoneMode) void doPhoneLogin();
              else void doLogin(username, password);
            }}
          >
            {isPhoneMode ? (
              <>
                <div>
                  <label htmlFor="login-phone" className="mb-1.5 block text-label font-medium text-ink-700">
                    手机号
                  </label>
                  <input
                    id="login-phone"
                    type="tel"
                    inputMode="numeric"
                    autoComplete="tel"
                    enterKeyHint="next"
                    maxLength={11}
                    className={fieldCls}
                    value={phone}
                    onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
                    placeholder="请输入 11 位手机号"
                  />
                </div>

                <div>
                  <label htmlFor="login-code" className="mb-1.5 block text-label font-medium text-ink-700">
                    验证码
                  </label>
                  <div className="flex gap-2">
                    <input
                      id="login-code"
                      type="text"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      enterKeyHint="done"
                      maxLength={6}
                      className={cn(fieldCls, "num flex-1 tracking-[0.3em]")}
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                      placeholder="6 位数字"
                    />
                    <button
                      type="button"
                      onClick={() => void requestCode()}
                      disabled={!phoneValid || countdown > 0}
                      className={cn(
                        "min-h-tap shrink-0 whitespace-nowrap rounded-r2 border border-line px-3 text-body-sm font-medium",
                        "text-ink-700 transition-colors duration-fast hover:bg-surface-hover",
                        "disabled:cursor-not-allowed disabled:opacity-50"
                      )}
                    >
                      {countdown > 0 ? `${countdown}s` : "获取验证码"}
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <>
                <div>
                  <label htmlFor="login-username" className="mb-1.5 block text-label font-medium text-ink-700">
                    用户名
                  </label>
                  <input
                    id="login-username"
                    autoComplete="username"
                    enterKeyHint="next"
                    className={fieldCls}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="请输入用户名"
                  />
                </div>

                <div>
                  <label htmlFor="login-password" className="mb-1.5 block text-label font-medium text-ink-700">
                    密码
                  </label>
                  <div className="relative">
                    <input
                      id="login-password"
                      type={showPassword ? "text" : "password"}
                      autoComplete="current-password"
                      enterKeyHint="go"
                      className={cn(fieldCls, "pr-11")}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="请输入密码"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "隐藏密码" : "显示密码"}
                      className="tap-ghost absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-r2 text-ink-400 transition-colors duration-fast hover:text-ink-700"
                    >
                      {showPassword ? (
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3 3l18 18M10.6 10.6a2 2 0 002.8 2.8M9.4 5.6A9.6 9.6 0 0112 5c5 0 9 4.5 9 7a11 11 0 01-2.4 3.5M6.2 7.4C4.2 8.8 3 11 3 12c0 2.5 4 7 9 7a9.5 9.5 0 003.5-.7" />
                        </svg>
                      ) : (
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3 12c0-2.5 4-7 9-7s9 4.5 9 7-4 7-9 7-9-4.5-9-7z" />
                          <circle cx="12" cy="12" r="2.6" />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>
              </>
            )}

            {errorBanner}

            <button
              type="submit"
              disabled={loading || (isPhoneMode && (!phoneValid || code.length < 4))}
              className={cn(
                "flex w-full min-h-[44px] items-center justify-center rounded-r2 bg-brand-600 text-body font-medium text-white",
                "transition-colors duration-fast hover:bg-brand-700",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 focus-visible:ring-offset-2",
                "disabled:cursor-not-allowed disabled:opacity-55"
              )}
            >
              {loading ? "登录中…" : "登录"}
            </button>
          </form>

          {footer && <div className="mt-4 text-center text-caption text-ink-500">{footer}</div>}

          {/* ── 演示账号 ─────────────────────────────────────── */}
          {demoAccounts.length > 0 && (
            <>
              {/* 桌面：内联卡片 */}
              <div className="mt-7 hidden rounded-r3 border border-line bg-surface p-3.5 sm:block">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <span className="text-label font-medium text-ink-700">演示账号</span>
                  <span className="text-caption text-ink-400">点击即登录</span>
                </div>

                {groups.length > 1 && (
                  <SegmentedControl
                    className="mb-3 w-full"
                    fullWidth
                    size="sm"
                    ariaLabel="账号分组"
                    value={activeGroup}
                    onChange={setActiveGroup}
                    options={groups.map((g) => ({ value: g, label: g }))}
                  />
                )}

                <div className="grid grid-cols-2 gap-2">
                  {groupAccounts.map((a) => (
                    <button
                      key={a.username}
                      type="button"
                      disabled={loading}
                      onClick={() => void doLogin(a.username, a.password)}
                      className={cn(
                        "rounded-r2 border border-line px-2.5 py-2 text-left",
                        "transition-colors duration-fast hover:border-brand-500/40 hover:bg-brand-500/5",
                        "disabled:cursor-not-allowed disabled:opacity-50"
                      )}
                    >
                      <span className="block truncate text-body-sm font-medium text-ink-800">{a.label}</span>
                      <span className="num block truncate text-caption text-ink-500">{a.username}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* 移动端：收进底部抽屉 */}
              <button
                type="button"
                onClick={() => setAccountsOpen(true)}
                className="mt-6 flex w-full min-h-tap items-center justify-center gap-1.5 rounded-r2 border border-dashed border-line text-body-sm text-ink-600 transition-colors duration-fast hover:bg-surface-hover sm:hidden"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                使用演示账号登录
              </button>

              <BottomSheet
                isOpen={accountsOpen}
                onClose={() => setAccountsOpen(false)}
                title="演示账号"
                description="点击任意账号直接登录"
                heightRatio={0.62}
              >
                {groups.length > 1 && (
                  <SegmentedControl
                    className="mb-3 w-full"
                    fullWidth
                    size="md"
                    ariaLabel="账号分组"
                    value={activeGroup}
                    onChange={setActiveGroup}
                    options={groups.map((g) => ({ value: g, label: g }))}
                  />
                )}
                <div className="space-y-2">
                  {groupAccounts.map((a) => (
                    <button
                      key={a.username}
                      type="button"
                      disabled={loading}
                      onClick={() => {
                        setAccountsOpen(false);
                        void doLogin(a.username, a.password);
                      }}
                      className={cn(
                        "flex w-full min-h-tap items-center justify-between gap-3 rounded-r2 border border-line px-3 py-2.5 text-left",
                        "transition-colors duration-fast active:bg-surface-hover",
                        "disabled:cursor-not-allowed disabled:opacity-50"
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-body font-medium text-ink-800">{a.label}</span>
                        <span className="num block truncate text-caption text-ink-500">{a.username}</span>
                      </span>
                      <svg className="h-4 w-4 shrink-0 text-ink-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  ))}
                </div>
              </BottomSheet>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

LoginShell.displayName = "LoginShell";
