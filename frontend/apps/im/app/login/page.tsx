"use client";

import { LoginShell, type DemoAccount } from "@nlaw/ui";

/** 演示账号（与 backend/app/seed/data.py 保持一致）。 */
const DEMO: DemoAccount[] = [
  { username: "client", password: "Client@12345", label: "客户", group: "演示身份" },
  { username: "lawyer_wang", password: "Lawyer@12345", label: "律师", group: "演示身份" },
];

export default function LoginPage() {
  return (
    <LoginShell
      appName="即时咨询"
      tagline="律师与客户的实时沟通。会话与案件、委托自动关联，随时可回溯。"
      demoAccounts={DEMO}
      redirectTo="/"
    />
  );
}
