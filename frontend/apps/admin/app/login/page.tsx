"use client";

import { LoginShell, type DemoAccount } from "@nlaw/ui";

/** 演示账号（与 backend/app/seed/data.py 保持一致）。 */
const DEMO: DemoAccount[] = [
  { username: "admin", password: "Admin@12345", label: "平台管理员", group: "平台端" },
  { username: "firm_admin", password: "Firm@12345", label: "律所管理员", group: "租户端" },
  { username: "ent_admin", password: "Ent@12345", label: "企业管理员", group: "租户端" },
];

export default function LoginPage() {
  return (
    <LoginShell
      appName="运营后台"
      tagline="平台治理与运营驾驶舱。租户、合规、审计与质量指标的统一入口。"
      demoAccounts={DEMO}
      redirectTo="/"
    />
  );
}
