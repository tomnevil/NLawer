"use client";

import React, { useEffect } from "react";
import { onContentTooLarge } from "@nlaw/sdk";
import { useToast } from "./Toast";

export interface ContentTooLargeGateProps {
  /**
   * 只处理路径里包含这些片段的 413；不传 = 全部都处理。
   *
   * 各端通常应该传：一个端内可能有多种超限字段，但只有某几处是
   * 这个端真正关心的。不过**即便不匹配也不该静默**——SDK 抛出的
   * `ApiError` 依然会走到调用方自己的 catch，这里只是「额外给一次提示」。
   */
  pathIncludes?: string[];
  title?: string;
}

/**
 * 端级 413 兜底（B1 的全局那一半）。
 *
 * ## 它解决什么
 *
 * `packages/sdk` 已经把 413 派发成全局事件，但**没人订阅就等于没发生**：
 * 用户看到的是「点了没反应」或调用方自己 catch 出的一句干巴巴的报错。
 * 本组件挂在各端壳层，保证 413 **至少**会被明确告知一次。
 *
 * ## 它不解决什么
 *
 * 它**不渲染决策门**。决策门需要「超限的那段原文」才能提供
 * 「存为 .txt 上传」，而原文只有具体表单才知道
 * （见 web 合同审查页那种接法：把 `draft` 直接传给 `GateBanner`）。
 * 这里只给 toast —— 宁可少给一个选项，也不编造用户没输入过的内容。
 *
 * ⚠️ 因此各端若有长文本表单，**仍应**像 web 那样单独接 `GateBanner`；
 * 本组件是兜底，不是替代。
 */
export const ContentTooLargeGate: React.FC<ContentTooLargeGateProps> = ({
  pathIncludes,
  title = "内容超出处理上限",
}) => {
  const { addToast } = useToast();

  useEffect(() => {
    return onContentTooLarge((e) => {
      if (pathIncludes && pathIncludes.length > 0) {
        if (!pathIncludes.some((p) => e.path.includes(p))) return;
      }
      const max = e.detail.max_length;
      addToast({
        type: "warning",
        title,
        message: max
          ? `单次最多 ${max} 字，原文未丢失，请改用文件上传。`
          : "原文未丢失，请改用文件上传。",
      });
    });
  }, [addToast, pathIncludes, title]);

  return null;
};

ContentTooLargeGate.displayName = "ContentTooLargeGate";
