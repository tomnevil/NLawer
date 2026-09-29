"use client";

import React from "react";
import Link from "next/link";
import { LoginShell, SegmentedControl } from "@nlaw/ui";

/**
 * 登录页预览。
 *
 * 右上角悬浮开关用于对比两种形态：
 * - 仅账号密码（默认，与当前后端能力一致）
 * - 账号密码 + 手机验证码（后端就绪后由各端显式开启）
 */
export default function LoginPreviewPage() {
  const [withPhone, setWithPhone] = React.useState(false);

  return (
    <>
      <LoginShell
        appName="法务助手"
        tagline="法律科技双产品线：律所智能协作平台 + 个人与企业法务助手。让每一次法律判断都有据可查。"
        modes={withPhone ? ["password", "phone"] : ["password"]}
        onRequestCode={async (phone) => {
          // 预览用桩：真实实现应调用后端短信下发端点
          await new Promise((r) => setTimeout(r, 400));
          if (!phone) throw new Error("请输入手机号");
        }}
        onPhoneLogin={async () => {
          await new Promise((r) => setTimeout(r, 600));
          throw new Error("验证码登录需要后端支持，当前为预览桩");
        }}
        footer={
          <>
            登录即表示同意
            <a className="mx-1 text-link hover:underline" href="#">
              服务协议
            </a>
            与
            <a className="mx-1 text-link hover:underline" href="#">
              隐私政策
            </a>
          </>
        }
      />

      {/* 预览控制条 */}
      <div className="fixed right-4 top-4 z-toast flex items-center gap-2 rounded-r3 border border-line bg-surface/95 p-2 shadow-s2 backdrop-blur">
        <Link
          href="/components-preview"
          className="min-h-tap rounded-r2 px-2.5 text-label text-ink-600 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
        >
          ← 返回
        </Link>
        <SegmentedControl
          size="sm"
          value={withPhone ? "phone" : "password"}
          onChange={(v) => setWithPhone(v === "phone")}
          ariaLabel="登录方式预览"
          options={[
            { value: "password", label: "仅密码" },
            { value: "phone", label: "+ 验证码" },
          ]}
        />
      </div>
    </>
  );
}
