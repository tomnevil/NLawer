"use client";

import React from "react";
import { usePathname, useRouter } from "next/navigation";
import { Spinner, TabBar, useAuthGuard, type TabBarItem } from "@nlaw/ui";

/**
 * im（客户端）的受保护外壳。
 *
 * 【为什么这里不用 AppLayout / AppShell】
 * 概念图 06 把 IM 定为**满屏三栏**：288px 会话列表 + 正文 + 320px 案件上下文。
 * 而 AppShell 在 ≥1024px 时会常驻一条 240px 墨色侧栏，两者叠加后
 * 1280px 屏幕上留给正文的只有 432px——一段法条原文都放不下。
 * IM 的导航本来就是会话列表本身，再套一层侧栏是纯装饰性开销。
 *
 * 代价是拿不到 AppShell 里的应用切换器，所以四条一级路由各自挂一个独立的
 * `AppSwitcher`（`chat/page.tsx` 左栏头部 · `me/page.tsx`「切换到其他端」区块 ·
 * `cases/page.tsx` 与根页 `page.tsx` 头部）—— **这是跨端切换，不是导航出口**。
 *
 * ⚠️ **B2a 的「导航出口」是另一回事**：`verify_breakpoints.py` 的 B2a 只认
 * `a[href]`（且**自链不算**），而 `AppSwitcher` 是 `<button>` + 下拉、菜单项
 * 只在点开后渲染 ⇒ **判据看不见它**（实测：给 `/cases` 加完它仍判 rc=1，
 * `页内链接=['cases']`）。im 四条路由的出口**逐页不同**：
 * `/chat` 靠 250/288px 常驻左列（B2b 豁免）· `/` 靠快捷服务卡片 ·
 * `/me` 靠「我的咨询」数据格 `href="/chat"` · `/cases` 靠头部「发起咨询」`href="/chat#new"`。
 *
 * 此前 `/cases` 的唯一出口在**空态**里（`shown.length === 0` 时的「发起咨询」）
 * ⇒ **有案件的客户反而被困**，且判据结论会随种子数据变化。
 *
 * 【但移动端需要 AppShell 的 TabBar】
 * 规范 §8.4：**客户端 4 项 Tab**（工作台 / 咨询 / 我的案件 / 我的）。
 * `TabBar` 组件本身自带 `lg:hidden`，所以可以单独引进来——
 * 不需要为了拿到它而套上整条桌面侧栏。
 *
 * 鉴权与会话恢复仍走共享的 `useAuthGuard`，逻辑与另外三端完全一致。
 */

/**
 * 底部 4 项 Tab（规范 §8.4）。
 *
 * ⚠️ `id` 必须等于**一级路径段**（根路径用 `ROOT_ID`）。
 * 激活态是按 `pathname.split("/").filter(Boolean)[0] ?? ROOT_ID` 推出来的，
 * 改 `id` 不改路径（或反之）会让高亮**静默失效**：没有报错，只是不见了。
 */
const TABS: TabBarItem[] = [
  { id: "home", label: "工作台", href: "/" },
  { id: "chat", label: "咨询", href: "/chat" },
  { id: "cases", label: "我的案件", href: "/cases" },
  { id: "me", label: "我的", href: "/me" },
];

const ROOT_ID = "home";

export default function ImAppLayout({ children }: { children: React.ReactNode }) {
  const { loading } = useAuthGuard("/login");
  const pathname = usePathname();
  const router = useRouter();

  const activeId = pathname.split("/").filter(Boolean)[0] ?? ROOT_ID;

  // 会话恢复中给一个稳定占位，避免先渲染空三栏再跳登录造成闪烁
  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center bg-ink-50">
        <Spinner size="md" label="正在恢复会话" />
      </div>
    );
  }

  // h-dvh + overflow-hidden：IM 是三栏各自滚动的应用，页面本身不滚动。
  // 用 dvh 而非 vh，避免移动端地址栏收起时底部输入框被裁掉。
  //
  // 【安全区为什么这么分：底部归 TabBar，其余归根容器】
  // 三栏的顶部与左右**都**贴着视口边缘（会话列表头、对话头、消息输入区），
  // 逐栏加 padding 要写三遍、横屏时左右还各要一遍，加在根容器上一次到位。
  //
  // ⚠️ **底部是例外，必须单独处理**：`TabBar` 内部已经加了
  // `paddingBottom: var(--safe-bottom)`，根容器若**也**加一次，
  // 刘海屏上底部会多出整整一个安全区高度的空白（iPhone 实测 34px）。
  // ⇒ 根容器不加 `paddingBottom`，改由内容区按断点让位：
  //    · 移动端（< 1024px，TabBar 可见）：`--actionbar-bottom` = tabbar-h + safe-bottom
  //    · 桌面端（≥ 1024px，TabBar 被 `lg:hidden` 隐藏）：只剩 `--safe-bottom`
  // 两个令牌都已存在于 `tokens.css`，不新增。
  //
  // 底色用 `bg-surface` 而非 `bg-ink-50`：安全区那几条会被染成
  // 相邻两栏表头（都是 `bg-surface`）的颜色，看上去是表头自然延伸到边缘，
  // 而不是露出一圈浅灰的「底」。
  return (
    <div
      className="flex h-dvh flex-col overflow-hidden bg-surface font-sans text-ink-900"
      style={{
        paddingTop: "var(--safe-top)",
        paddingLeft: "var(--safe-left)",
        paddingRight: "var(--safe-right)",
      }}
    >
      <div className="min-h-0 flex-1 pb-[var(--actionbar-bottom)] lg:pb-[var(--safe-bottom)]">
        {children}
      </div>

      {/* TabBar 是 `fixed` 的，不参与上面的 flex 布局，所以内容区靠 pb 让位 */}
      <TabBar
        items={TABS}
        activeId={activeId}
        onSelect={(item) => {
          if (item.href) router.push(item.href);
        }}
      />
    </div>
  );
}
