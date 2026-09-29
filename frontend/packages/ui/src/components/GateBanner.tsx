"use client";

import React from "react";
import { cn } from "../lib/cn";

export interface GateBannerProps {
  /** 触发 413 的原文。**组件绝不修改它**——只用来生成 .txt。 */
  text: string;
  /** 后端 413 错误体的 `field`（用于文案「哪个字段超了」）。 */
  field?: string | null;
  /** 后端 413 错误体的 `max_length`（字符上限）。 */
  maxLength?: number | null;
  /** 后端 413 错误体的 `guidance`，恒为 `upload_or_async`。 */
  guidance?: string | null;
  /** 生成的 .txt 文件名（不含扩展名）。 */
  fileName?: string;
  /** ① 一键存 .txt 上传：把原文封装成 File 交给调用方去发起上传。 */
  onSaveAsTxt?: (file: File) => void;
  /** ② 选本地文件：由调用方打开文件选择器。 */
  onPickFile?: () => void;
  onDismiss?: () => void;
  className?: string;
}

/**
 * 超限决策门（B1）：全局 413 时渲染的**非阻断**横幅。
 *
 * ## 硬约束：**零丢失**
 *
 * 本组件**绝不清空**用户已输入的内容——它只读取 `text` 用于生成 .txt。
 * 「超限即丢内容」是信任崩塌的头号来源（瑞思），也是本组件存在的理由。
 *
 * ## 为什么是 info（中性引导）而不是 danger（错误）
 *
 * 413 不是「你做错了」，而是「这条路走不通，换一条」。用 danger 会让用户
 * 以为提交失败、从而重新粘贴一遍——反而增加流失。
 *
 * ## v1 只做两个选项（2026-09-23 裁定）
 *
 * - ① 一键存 .txt 上传
 * - ② 选本地文件
 *
 * 第三选项「折叠原文继续问答」已裁定**不进 v1**（进停车区），待埋点验证后再定。
 * ⚠️ 但「关页后草稿恢复」是**独立的零丢失验收点**，由调用方负责，不随 ③ 一起砍。
 */
export const GateBanner: React.FC<GateBannerProps> = ({
  text,
  field,
  maxLength,
  guidance,
  fileName,
  onSaveAsTxt,
  onPickFile,
  onDismiss,
  className,
}) => {
  const handleSaveAsTxt = () => {
    if (!onSaveAsTxt) return;
    const base = (fileName || field || "合同原文").replace(/\.txt$/i, "");
    // 原文原样封装：**不 trim**（前后空白属于原文，裁剪会改变字符下标，
    // 与合同审查「定位逐字一致」的契约冲突）。
    const file = new File([text], `${base}.txt`, { type: "text/plain;charset=utf-8" });
    onSaveAsTxt(file);
  };

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col gap-3 rounded-r3 border p-4",
        "bg-[rgb(var(--gate-banner-bg))] border-[rgb(var(--gate-banner-border))]",
        className
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-body-sm font-semibold text-[rgb(var(--gate-banner-fg))]">
            内容超出在线处理上限，原文已保留
          </p>
          <p className="mt-1 text-caption text-ink-600">
            {maxLength ? `单次可处理 ${maxLength} 字` : "超出处理上限"}
            {field ? `（字段：${field}）` : ""}
            。系统**不会截断**你的文本——截断会让审查结论基于残缺内容，这比拒绝更危险。
          </p>
          {guidance === "upload_or_async" && (
            <p className="mt-1 text-caption text-ink-500">改为文件上传即可继续，处理完成会通知你。</p>
          )}
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="关闭提示"
            className="tap-ghost -mr-1 -mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-r1 text-ink-400 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {onSaveAsTxt && (
          <button
            type="button"
            onClick={handleSaveAsTxt}
            className="rounded-r2 bg-[rgb(var(--solid-brand))] px-3 py-2 text-label font-medium text-white transition-opacity duration-fast hover:opacity-90"
          >
            存为 .txt 并上传
          </button>
        )}
        {onPickFile && (
          <button
            type="button"
            onClick={onPickFile}
            className="rounded-r2 border border-line bg-surface px-3 py-2 text-label font-medium text-ink-700 transition-colors duration-fast hover:bg-surface-hover"
          >
            选择本地文件
          </button>
        )}
      </div>
    </div>
  );
};

GateBanner.displayName = "GateBanner";
