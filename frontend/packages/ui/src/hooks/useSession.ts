"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { ApiError, authed, logout as sdkLogout, onSessionExpired, restoreSession } from "@nlaw/sdk";

/**
 * 后端 `UserBrief`（`app/schemas/auth.py`）。
 *
 * ⚠️ `tenant_id` 是**字符串**而非数字：后端 `TenantMixin.tenant_id` 是
 * `String(64)`，取值形如 `platform` / `firm_hlw` / `ent_acme`。
 * 早期这里误标为 `number | null`，虽然 TS 不做运行时校验、不会立刻报错，
 * 但它会诱导调用方写出 `tenant_id === 1` 这类**永远为假**的比较，
 * 是「租户判断静默失效」的温床。
 */
export interface SessionUser {
  id: number;
  username: string;
  full_name?: string | null;
  role?: string | null;
  tenant_id?: string | null;
  tenant_name?: string | null;
}

/** 后端 `app/core/rbac.py` 的 `Role` 枚举 → 中文标签。 */
export const ROLE_LABELS: Record<string, string> = {
  PLATFORM_ADMIN: "平台管理员",
  FIRM_ADMIN: "律所管理员",
  LAWYER: "律师",
  ASSISTANT: "律师助理",
  CLIENT: "客户",
  ENTERPRISE_ADMIN: "企业管理员",
  ENTERPRISE_USER: "企业员工",
};

export function roleLabel(role?: string | null): string | undefined {
  if (!role) return undefined;
  return ROLE_LABELS[role] ?? role;
}

/** 取展示名：优先姓名，其次用户名。 */
export function displayName(user: SessionUser | null): string | undefined {
  if (!user) return undefined;
  return user.full_name?.trim() || user.username;
}

export interface UseSessionResult {
  user: SessionUser | null;
  /** 首次会话恢复 + 用户信息拉取是否仍在进行 */
  loading: boolean;
  /**
   * 已确认未登录（静默换发失败，或 `/auth/me` 返回 401）。
   * 调用方应据此跳转登录页——钩子本身不做跳转，避免在不需要鉴权的
   * 页面（如登录页自身）里产生重定向循环。
   */
  unauthenticated: boolean;
  /** 重新拉取用户信息 */
  refresh: () => Promise<void>;
  /** 登出（清内存令牌 + 服务端 Cookie） */
  logout: () => Promise<void>;
}

/* ============================================================================
 * 会话状态是**应用级单例**，不是组件级状态
 * ----------------------------------------------------------------------------
 * 早期实现把状态放在 `useState` 里，于是「布局恢复一次 + 页面再问一次」就会
 * 打两次 `/auth/me`（web 首页实测如此）。会话是全应用唯一的事实，应该像
 * 令牌一样只有一份，因此改由模块级 store 持有，`useSyncExternalStore` 订阅。
 *
 * 带来的额外好处：任何组件调 `logout()` 后，所有订阅者（顶栏用户区、左栏
 * 用户区、页面主体）会在同一帧内一起变为未登录，不会出现「一处已登出、
 * 另一处还显示着用户名」的错位。
 * ========================================================================== */

interface SessionSnapshot {
  user: SessionUser | null;
  loading: boolean;
  unauthenticated: boolean;
}

/** 服务端渲染期间恒定返回同一对象，避免 hydration 不一致。 */
const SERVER_SNAPSHOT: SessionSnapshot = { user: null, loading: true, unauthenticated: false };

let snapshot: SessionSnapshot = SERVER_SNAPSHOT;
const listeners = new Set<() => void>();

/** 首屏恢复只做一次；后续刷新一律走 `refresh()`。 */
let started = false;
/** 并发去重：多个组件同时挂载时只发一轮请求。 */
let inflight: Promise<void> | null = null;
/**
 * 会话世代号（P1-2 守卫）。每次 `loadSession` 开始与 `logout()` 时递增；
 * 在途请求完成时若世代已变，其 emit 一律作废——否则「首屏恢复期间点退出，
 * 迟到的 /auth/me 会把刚清空的 user 又覆写回登录态」。
 */
let sessionGen = 0;

function emit(next: Partial<SessionSnapshot>): void {
  snapshot = { ...snapshot, ...next };
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): SessionSnapshot {
  return snapshot;
}

function getServerSnapshot(): SessionSnapshot {
  return SERVER_SNAPSHOT;
}

/**
 * 恢复会话：先用 HttpOnly refresh Cookie 静默换发 access 令牌，
 * 再拉 `/auth/me`。两个请求**串行**——并行会在换发完成前带着空令牌
 * 打 `/auth/me` 拿到 401，把一次正常恢复变成一次误判掉线。
 *
 * 401 一律走「清理本地状态 + 标记未登录」，不重试——重试只会把一个
 * 明确的鉴权失败拖成三倍延迟。
 */
async function loadSession(): Promise<void> {
  // 世代守卫（P1-2）：每次恢复开始递增；`logout()` 也递增以作废在途请求。
  const gen = ++sessionGen;
  const ok = await restoreSession();
  if (!ok) {
    if (gen === sessionGen) emit({ user: null, unauthenticated: true });
    return;
  }
  try {
    const me = await authed<SessionUser>("/api/v1/auth/me");
    // 首屏恢复期间用户点了退出：迟到的 /auth/me 不得把 user「复活」
    if (gen === sessionGen) emit({ user: me, unauthenticated: false });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      if (gen === sessionGen) emit({ user: null, unauthenticated: true });
      return;
    }
    // 网络异常不应把用户踢出登录态——保持现状，避免断网即掉线
  }
}

function ensureStarted(): Promise<void> {
  if (inflight) return inflight;
  inflight = loadSession().finally(() => {
    inflight = null;
    emit({ loading: false });
  });
  return inflight;
}

/**
 * 会话恢复与当前用户（应用级单例）。
 *
 * 所有调用方共享同一份状态与同一轮请求；`loading` 只在首次恢复与显式
 * `refresh()` 期间为 true。
 */
export function useSession(): UseSessionResult {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // 首屏触发一次恢复。放在 effect 里而不是模块顶层，是为了不在 SSR 期间
  // 发请求；`started` 保证即便 StrictMode 双跑 effect 也只发一轮。
  //
  // 注意：`started` 不能永久阻断重探活——否则登录成功回到受保护页时，
  // AppLayout 重新挂载，但单例里仍残留登录前的 `{unauthenticated:true}`，
  // 守卫会立刻把人弹回 /login（表现为「能登录却不跳转」）。
  // 因此只在「已确认登录（有 user）」时跳过；仍是未登录态时每次挂载都重新探活。
  useEffect(() => {
    if (started && snapshot.user) return;
    started = true;
    void ensureStarted();
  }, []);

  // 401 全局事件（P2-7）：任何请求 refresh 失败都广播到此，全应用同帧
  // 进入未登录态——包括弹窗、轮询等未挂 useAuthGuard 的角落。
  // 世代号 +1：作废在途 loadSession 的 emit，防止迟到的 /auth/me「复活」。
  useEffect(
    () =>
      onSessionExpired(() => {
        sessionGen += 1;
        emit({ user: null, unauthenticated: true, loading: false });
      }),
    [],
  );

  const refresh = useCallback(async () => {
    emit({ loading: true });
    try {
      await loadSession();
    } finally {
      emit({ loading: false });
    }
  }, []);

  const logout = useCallback(async () => {
    // 先作废在途 loadSession 的 emit（世代号 +1）：否则首屏恢复期间点退出，
    // 迟到的 /auth/me 会把刚清空的 user 又覆写回登录态（P1-2）
    sessionGen += 1;
    await sdkLogout();
    emit({ user: null, unauthenticated: true, loading: false });
  }, []);

  return {
    user: snap.user,
    loading: snap.loading,
    unauthenticated: snap.unauthenticated,
    refresh,
    logout,
  };
}
