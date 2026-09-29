"use client";

import { LoginShell, type DemoAccount } from "@nlaw/ui";

/** 演示账号（与 backend/app/seed/data.py 保持一致）。 */
const DEMO: DemoAccount[] = [
  { username: "ent_admin", password: "Ent@12345", label: "企业管理员", group: "企业端" },
  { username: "ent_user", password: "Ent@12345", label: "企业员工", group: "企业端" },
  { username: "client", password: "Client@12345", label: "个人用户", group: "个人端" },
];

export default function LoginPage() {
  return (
    <LoginShell
      appName="法务助手"
      tagline="个人与企业的法务智能工作台。合同审查、合规体检、智能问答，每一次判断都有据可查。"
      demoAccounts={DEMO}
      redirectTo="/qa"
      footer="本内容由 AI 生成，仅供参考，不构成正式法律意见。"
    />
  );
}
