"use client";

import { LoginShell, type DemoAccount } from "@nlaw/ui";

/** 演示账号（与 backend/app/seed/data.py 保持一致）。 */
const DEMO: DemoAccount[] = [
  { username: "lawyer_wang", password: "Lawyer@12345", label: "高级合伙人（可 L3）", group: "律所端" },
  { username: "lawyer_li", password: "Lawyer@12345", label: "执业律师（L2）", group: "律所端" },
  { username: "firm_admin", password: "Firm@12345", label: "律所管理员", group: "律所端" },
  { username: "assistant", password: "Assistant@12345", label: "律师助理", group: "律所端" },
];

export default function LoginPage() {
  return (
    <LoginShell
      appName="律师工作台"
      tagline="律所智能协作平台。案件流转、证据管理、复核审批与 AI 辅助分析，责任边界清晰可溯。"
      demoAccounts={DEMO}
      redirectTo="/cases"
      footer="本内容由 AI 生成，仅供参考，不构成正式法律意见。"
    />
  );
}
