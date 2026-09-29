"use client";

import React, { createContext, useContext, useState, useCallback } from "react";
import { cn } from "../lib/cn";

export interface ToastProps {
  id: string;
  type: "success" | "error" | "warning" | "info";
  title: string;
  message?: string;
  duration?: number;
}

interface ToastContextValue {
  toasts: ToastProps[];
  addToast: (toast: Omit<ToastProps, "id">) => void;
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastProps[]>([]);

  const addToast = useCallback((toast: Omit<ToastProps, "id">) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { ...toast, id }]);
    setTimeout(
      () => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      },
      toast.duration || 5000
    );
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </ToastContext.Provider>
  );
};

const ToastContainer: React.FC<{ toasts: ToastProps[]; onRemove: (id: string) => void }> = ({
  toasts,
  onRemove,
}) => {
  if (toasts.length === 0) return null;
  return (
    <div
      className={cn(
        // 左右边距必须带上安全区：`inset-x-3`（12px）在横屏刘海机上
        // （safe-left/right ≈ 40px）会让提示条有一截压在刘海下面，
        // 而「网络不可用」这类提示恰恰是用户最需要看清的。
        // 下划线是 Tailwind 对 `calc()` 里空格的转义写法。
        "fixed z-toast flex flex-col gap-2",
        "left-[calc(0.75rem_+_var(--safe-left))] right-[calc(0.75rem_+_var(--safe-right))]",
        "bottom-[calc(var(--tabbar-h)+var(--safe-bottom)+12px)]",
        "sm:left-auto sm:right-[calc(1rem_+_var(--safe-right))] sm:w-auto",
        "lg:bottom-[calc(20px+var(--safe-bottom))]"
      )}
    >
      {toasts.map((toast) => (
        <Toast key={toast.id} {...toast} onClose={() => onRemove(toast.id)} />
      ))}
    </div>
  );
};

const typeStyles: Record<string, { bg: string; border: string; icon: string }> = {
  success: { bg: "bg-verified-500/10", border: "border-verified-500/30", icon: "text-verified-600" },
  error: { bg: "bg-danger-500/10", border: "border-danger-500/30", icon: "text-danger-600" },
  warning: { bg: "bg-pending-500/10", border: "border-pending-500/30", icon: "text-pending-600" },
  info: { bg: "bg-info-500/10", border: "border-info-500/30", icon: "text-info-600" },
};

const Toast: React.FC<ToastProps & { onClose: () => void }> = ({ type, title, message, onClose }) => {
  const styles = typeStyles[type];
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-r3 border bg-surface/95 p-4 shadow-s2 backdrop-blur-xl",
        "animate-slide-in-right sm:min-w-[300px] sm:max-w-[400px]",
        styles.bg,
        styles.border
      )}
      role="status"
    >
      <span className={cn("shrink-0", styles.icon)}>
        {type === "success" && (
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        )}
        {type === "error" && (
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        )}
        {type === "warning" && (
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
        )}
        {type === "info" && (
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        )}
      </span>
      <div className="min-w-0 flex-1">
        <h4 className="text-body-sm font-medium text-ink-900">{title}</h4>
        {message && <p className="mt-0.5 text-label text-ink-500">{message}</p>}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="关闭"
        className="tap-ghost shrink-0 rounded-r1 p-1 text-ink-400 transition-colors duration-fast hover:bg-surface-hover hover:text-ink-700"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
};

Toast.displayName = "Toast";
