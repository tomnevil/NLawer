"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSession, type SessionUser } from "./useSession";

export interface UseAuthGuardResult {
  /** 会话恢复中，调用方应渲染占位而不是内容 */
  loading: boolean;
  user: SessionUser | null;
  logout: () => Promise<void>;
}

/**
 * 受保护页面的鉴权守卫：静默恢复会话 + 未登录跳转登录页 + 角色白名单（P2-9）。
 *
 * 为什么单独成钩子：`AppLayout`（四端标准骨架）与 IM 的三栏骨架都要这段
 * 逻辑，但两者的**布局形态完全不同**——IM 的导航就是会话列表本身，
 * 再套一层 240px 侧栏会挤掉正文栏宽度。把守卫抽出来，两种骨架各自
 * 决定怎么排布，逻辑只有一份。
 *
 * 跳转用 `router.replace` 而非 `push`：否则用户按返回键会弹回受保护页，
 * 触发「跳转 -> 未登录 -> 再跳转」的来回抖动。
 *
 * 钩子本身**不做**「已登录就跳走」的反向跳转——那属于登录页的职责，
 * 放在这里会让组件预览页之类的免鉴权页面也被重定向。
 *
 * ## 角色白名单（P2-9 跨端串号防线）
 *
 * 四端共享同一后端 origin 的 refresh Cookie：A 端登录后 B 端刷新会恢复出
 * A 端用户。`allowedRoles` 非空且恢复出的角色不在清单内时，强制服务端
 * 登出（清共享 Cookie）——共享 Cookie 下「只在本端本地登出」会形成
 * 踢出循环（下次挂载又恢复出同一个人），服务端吊销是唯一诚实解。
 */
export function useAuthGuard(
  loginPath = "/login",
  allowedRoles?: string[],
): UseAuthGuardResult {
  const router = useRouter();
  const { user, loading, unauthenticated, logout } = useSession();

  useEffect(() => {
    if (!loading && unauthenticated) router.replace(loginPath);
  }, [loading, unauthenticated, router, loginPath]);

  // 角色不符 -> 服务端吊销 -> unauthenticated 翻转 -> 上面的 replace 跳登录页。
  // allowedRoles 用 join(",") 做依赖键：调用方常写内联数组字面量，
  // 引用不稳定性会让本 effect 每次渲染都重启（键值稳定则幂等）。
  const allowedKey = allowedRoles?.join(",") ?? "";
  useEffect(() => {
    if (loading || !user || !allowedKey) return;
    const roles = allowedKey.split(",");
    if (!roles.includes(user.role ?? "")) void logout();
  }, [loading, user, allowedKey, logout]);

  return { loading, user, logout };
}
