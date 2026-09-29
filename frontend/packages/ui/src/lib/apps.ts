/**
 * 四端应用切换器的共享配置。
 *
 * 四个应用是独立的 Next 进程（开发端口见各 `apps/<app>/package.json` 的
 * `dev`/`start` 脚本，与 `docker-compose.yml` 的 `CORS_ORIGINS` 一致），
 * 因此切换只能走**整页跳转**，不能用客户端路由。
 *
 * 地址通过 `NEXT_PUBLIC_APP_*_URL` 覆盖；默认值是本地开发端口，
 * 生产部署时在构建期注入即可。
 */

export interface AppLink {
  id: string;
  label: string;
  href: string;
  description?: string;
}

export const APP_IDS = ["web", "lawyer", "admin", "im"] as const;
export type AppId = (typeof APP_IDS)[number];

export const APP_META: Record<AppId, { label: string; description: string; defaultUrl: string }> = {
  web: { label: "法务助手", description: "个人与企业法务", defaultUrl: "http://localhost:3000" },
  lawyer: { label: "律所工作台", description: "案件协作与办案", defaultUrl: "http://localhost:3001" },
  admin: { label: "运营后台", description: "平台治理", defaultUrl: "http://localhost:3002" },
  im: { label: "即时沟通", description: "律师与客户会话", defaultUrl: "http://localhost:3003" },
};

export interface AppUrlOverrides {
  web?: string;
  lawyer?: string;
  admin?: string;
  im?: string;
}

/**
 * 构造 `AppShell` 的 `apps` 参数。
 *
 * 传入各端 `process.env.NEXT_PUBLIC_APP_*_URL`（**必须写静态访问**，
 * 否则 Next 不会在客户端包里内联替换）。
 */
export function buildAppLinks(overrides: AppUrlOverrides = {}): AppLink[] {
  return APP_IDS.map((id) => ({
    id,
    label: APP_META[id].label,
    description: APP_META[id].description,
    href: overrides[id]?.trim() || APP_META[id].defaultUrl,
  }));
}

/** 取当前应用之外的其它应用（用于「切换到」菜单）。 */
export function otherAppLinks(current: AppId, overrides: AppUrlOverrides = {}): AppLink[] {
  return buildAppLinks(overrides).filter((a) => a.id !== current);
}
