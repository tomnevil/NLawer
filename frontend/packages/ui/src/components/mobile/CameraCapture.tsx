"use client";

import React from "react";
import { cn } from "../../lib/cn";
import { Spinner } from "../Spinner";

export interface CapturedFile {
  id: string;
  /** 压缩后的文件，可直接上传 */
  file: File;
  /** 预览地址（组件卸载时自动回收） */
  previewUrl: string;
  width: number;
  height: number;
  /** 压缩后字节数 */
  size: number;
}

export interface CameraCaptureProps {
  value: CapturedFile[];
  onChange: (files: CapturedFile[]) => void;
  /** 最多允许张数，默认 9 */
  max?: number;
  /** 单张原图上限（MB），超出直接拒绝，默认 20 */
  maxSizeMB?: number;
  /** 压缩后长边像素上限，默认 1600 */
  maxDimension?: number;
  /** JPEG 质量，默认 0.82 */
  quality?: number;
  label?: string;
  hint?: string;
  disabled?: boolean;
  onError?: (message: string) => void;
  className?: string;
}

let seq = 0;
const nextId = () => `cap-${Date.now()}-${seq++}`;

/** 等比缩放并压缩为 JPEG；失败时回退原文件，绝不阻断上传。 */
async function compress(
  file: File,
  maxDimension: number,
  quality: number
): Promise<{ blob: Blob; width: number; height: number }> {
  const bitmapUrl = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("decode-failed"));
      el.src = bitmapUrl;
    });

    const scale = Math.min(1, maxDimension / Math.max(img.width, img.height));
    const width = Math.round(img.width * scale);
    const height = Math.round(img.height * scale);

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no-2d-context");
    // 白色底，避免 PNG 透明区域转 JPEG 后变黑
    ctx.fillStyle = "#FFFFFF";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", quality)
    );
    if (!blob) throw new Error("to-blob-failed");
    return { blob, width, height };
  } catch {
    return { blob: file, width: 0, height: 0 };
  } finally {
    URL.revokeObjectURL(bitmapUrl);
  }
}

/**
 * 拍照 / 选图上传（移动端证据采集入口）。
 *
 * 移动端办案的高频动作是「拍合同、拍发票、拍聊天记录」。两个关键取舍：
 * 1. **拍摄与相册分成两个按钮**。单按钮 + `capture` 属性在 iOS 上会把
 *    用户直接推进相机，想选已有照片反而更绕。
 * 2. **前端先压缩再上传**。手机原图动辄 5–10MB，弱网下上传必失败；
 *    长边压到 1600px 后通常 <400KB，肉眼几乎无差。
 *
 * 解码失败（如浏览器不支持的 HEIC）时回退原文件，保证「能传上去」
 * 优先于「传得小」。
 */
export const CameraCapture: React.FC<CameraCaptureProps> = ({
  value,
  onChange,
  max = 9,
  maxSizeMB = 20,
  maxDimension = 1600,
  quality = 0.82,
  label = "证据照片",
  hint,
  disabled = false,
  onError,
  className,
}) => {
  const [busy, setBusy] = React.useState(false);
  const cameraRef = React.useRef<HTMLInputElement>(null);
  const galleryRef = React.useRef<HTMLInputElement>(null);
  const groupId = React.useId();

  // 记录本组件创建的所有 URL，卸载时统一回收，避免内存泄漏
  const ownedUrls = React.useRef<Set<string>>(new Set());
  React.useEffect(
    () => () => {
      ownedUrls.current.forEach((url) => URL.revokeObjectURL(url));
      ownedUrls.current.clear();
    },
    []
  );

  const remaining = Math.max(0, max - value.length);
  const atLimit = remaining === 0;

  const handleFiles = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;

    const picked = Array.from(fileList).slice(0, remaining);
    if (fileList.length > remaining) {
      onError?.(`最多上传 ${max} 张，已自动截取前 ${remaining} 张`);
    }

    setBusy(true);
    const added: CapturedFile[] = [];

    for (const file of picked) {
      if (!file.type.startsWith("image/")) {
        onError?.(`「${file.name}」不是图片，已跳过`);
        continue;
      }
      if (file.size > maxSizeMB * 1024 * 1024) {
        onError?.(`「${file.name}」超过 ${maxSizeMB}MB，请压缩后再传`);
        continue;
      }

      const { blob, width, height } = await compress(file, maxDimension, quality);
      const compressed =
        blob === file
          ? file
          : new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });

      const previewUrl = URL.createObjectURL(compressed);
      ownedUrls.current.add(previewUrl);

      added.push({
        id: nextId(),
        file: compressed,
        previewUrl,
        width,
        height,
        size: compressed.size,
      });
    }

    setBusy(false);
    if (added.length > 0) onChange([...value, ...added]);
  };

  const remove = (id: string) => {
    const target = value.find((f) => f.id === id);
    if (target) {
      URL.revokeObjectURL(target.previewUrl);
      ownedUrls.current.delete(target.previewUrl);
    }
    onChange(value.filter((f) => f.id !== id));
  };

  const totalMB = value.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024;

  return (
    <div className={cn("space-y-2.5", className)}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-label font-medium text-ink-700">{label}</span>
        <span className="num text-caption text-ink-500">
          {value.length}/{max}
        </span>
        {value.length > 0 && (
          <span className="num text-caption text-ink-400">共 {totalMB.toFixed(1)}MB</span>
        )}
      </div>

      {hint && <p className="text-caption text-ink-500">{hint}</p>}

      {/* 缩略图网格 */}
      {value.length > 0 && (
        <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {value.map((f) => (
            <li key={f.id} className="group relative aspect-square">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={f.previewUrl}
                alt={f.file.name}
                className="h-full w-full rounded-r2 border border-line object-cover"
              />
              <button
                type="button"
                onClick={() => remove(f.id)}
                disabled={disabled}
                aria-label={`移除 ${f.file.name}`}
                className={cn(
                  "absolute -right-1.5 -top-1.5 flex h-6 w-6 items-center justify-center rounded-full",
                  "border border-line bg-surface text-ink-600 shadow-s1",
                  "transition-colors duration-fast hover:bg-danger-500 hover:text-white",
                  "disabled:cursor-not-allowed disabled:opacity-50"
                )}
              >
                <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.4} aria-hidden>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
              {f.width > 0 && (
                <span className="num pointer-events-none absolute bottom-1 left-1 rounded-[3px] bg-ink-950/70 px-1 text-caption leading-4 text-white">
                  {f.width}×{f.height}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* 采集入口 */}
      <div className="flex gap-2">
        <input
          ref={cameraRef}
          id={`${groupId}-camera`}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          disabled={disabled || atLimit || busy}
          onChange={(e) => {
            void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <input
          ref={galleryRef}
          id={`${groupId}-gallery`}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          disabled={disabled || atLimit || busy}
          onChange={(e) => {
            void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />

        <button
          type="button"
          onClick={() => cameraRef.current?.click()}
          disabled={disabled || atLimit || busy}
          className={cn(
            "inline-flex min-h-tap flex-1 items-center justify-center gap-1.5 rounded-r2 border border-line",
            "bg-surface text-body-sm font-medium text-ink-700",
            "transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900",
            "disabled:cursor-not-allowed disabled:opacity-50"
          )}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 9a2 2 0 012-2h1.5l1-1.5h7l1 1.5H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
            <circle cx="12" cy="13" r="3.2" />
          </svg>
          拍照
        </button>

        <button
          type="button"
          onClick={() => galleryRef.current?.click()}
          disabled={disabled || atLimit || busy}
          className={cn(
            "inline-flex min-h-tap flex-1 items-center justify-center gap-1.5 rounded-r2 border border-line",
            "bg-surface text-body-sm font-medium text-ink-700",
            "transition-colors duration-fast hover:bg-surface-hover hover:text-ink-900",
            "disabled:cursor-not-allowed disabled:opacity-50"
          )}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 5h16v14H4zM4 15l4-4 3 3 3.5-3.5L20 15" />
          </svg>
          相册
        </button>
      </div>

      {busy && (
        <p className="flex items-center gap-1.5 text-caption text-ink-500">
          <Spinner className="h-3.5 w-3.5" label={null} />
          正在压缩…
        </p>
      )}

      {atLimit && !busy && (
        <p className="text-caption text-ink-500">已达上限 {max} 张，如需继续请先移除</p>
      )}
    </div>
  );
};

CameraCapture.displayName = "CameraCapture";
