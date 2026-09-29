/**
 * 律小智 Tailwind 预设 v2「墨与纸」
 * ----------------------------------------------------------------------------
 * 设计规范：deliverables/ui-design/design-spec.md
 * 令牌来源：packages/ui/src/tokens.css（唯一事实来源）
 *
 * 本预设做三件事：
 *   1. 暴露 v2 语义色名：ink / brand / ai / verified / pending / danger / gold / info
 *   2. 把存量色系名（slate / indigo / cyan / emerald / amber / red / violet …）
 *      整体重映射到语义令牌 —— 存量 38 个页面零改动即获得新配色，
 *      且深色模式由 CSS 变量自动翻转，不再需要任何 `.dark { !important }` 覆盖。
 *   3. 收敛圆角、字号、动效到规范刻度。
 *
 * 颜色写法统一为 `rgb(var(--x) / <alpha-value>)`，因此 `bg-brand-600/10`
 * 这类透明度修饰符可正常工作。
 */
import type { Config } from "tailwindcss";

/** 把 tokens.css 中的 RGB 三元组令牌包装成 Tailwind 颜色函数。 */
const tok = (name: string): string => `rgb(var(--${name}) / <alpha-value>)`;

const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950] as const;

/** 从 tokens.css 的色阶生成 Tailwind 颜色对象。 */
const scale = (prefix: string, steps: readonly number[] = STEPS): Record<string, string> =>
  Object.fromEntries(steps.map((s) => [String(s), tok(`${prefix}-${s}`)]));

/**
 * 把不足 11 档的语义色阶补齐为 Tailwind 标准档位，
 * 缺失档位回落到数值最接近的已定义档位（保证任意 `bg-x-900` 都能生成类）。
 */
const fullScale = (partial: Record<string, string>): Record<string, string> => {
  const defined = Object.keys(partial).map(Number);
  const out: Record<string, string> = {};
  for (const s of STEPS) {
    const exact = partial[String(s)];
    if (exact) {
      out[String(s)] = exact;
      continue;
    }
    const nearest = defined.reduce((a, b) => (Math.abs(b - s) < Math.abs(a - s) ? b : a), defined[0]);
    out[String(s)] = partial[String(nearest)];
  }
  return out;
};

/* ------------------------------------------------------------------ 语义色阶 */
const ink = scale("ink");
const brand = scale("brand");
const ai = fullScale(scale("ai", [50, 100, 200, 300, 400, 500, 600, 700]));
const verified = fullScale(scale("verified", [50, 100, 200, 300, 400, 500, 600, 700]));
const pending = fullScale(scale("pending", [50, 100, 200, 300, 400, 500, 600, 700]));
const danger = fullScale(scale("danger", [50, 100, 200, 300, 400, 500, 600, 700]));
const gold = fullScale(scale("gold", [50, 100, 200, 300, 400, 500, 600, 700]));
const info = fullScale(scale("info", [50, 100, 200, 300, 400, 500, 600, 700]));

const preset: Partial<Config> = {
  darkMode: "class",
  theme: {
    extend: {
      /* -------------------------------------------------------------- 颜色 */
      colors: {
        /* v2 语义色名 —— 新代码请优先使用这一组 */
        ink,
        brand,
        ai,
        verified,
        pending,
        danger,
        gold,
        info,

        /* 角色令牌：语义比色阶更明确 */
        surface: {
          DEFAULT: tok("surface-card"),
          card: tok("surface-card"),
          page: tok("surface-page"),
          subtle: tok("surface-subtle"),
          hover: tok("surface-hover"),
          sunken: tok("surface-sunken"),
        },
        line: {
          subtle: tok("border-subtle"),
          DEFAULT: tok("border-default"),
          strong: tok("border-strong"),
        },
        t1: tok("text-primary"),
        t2: tok("text-secondary"),
        t3: tok("text-tertiary"),
        tmuted: tok("text-muted"),
        tfaint: tok("text-faint"),
        link: {
          DEFAULT: tok("link"),
          hover: tok("link-hover"),
          muted: tok("link-muted"),
        },
        sidebar: {
          fg: tok("sidebar-fg"),
          muted: tok("sidebar-fg-muted"),
          dim: tok("sidebar-fg-dim"),
          border: tok("sidebar-border"),
          active: tok("sidebar-active-bg"),
          hover: tok("sidebar-hover-bg"),
          "badge-danger": tok("sidebar-badge-danger"),
          "badge-pending": tok("sidebar-badge-pending"),
        },

        /* 恒定实底：用于承载白色前景的实心块（AI 头像、用户气泡、实心徽章）。
         * 刻意**不加** `<alpha-value>`——这些颜色在深浅两种模式下都必须
         * 保持深色才能让白字达到 4.5:1，任何透明度修饰都会破坏这个前提。
         * 因此 `bg-solid-ai/50` 这类写法不生成类，属预期行为。 */
        solid: {
          brand: "rgb(var(--solid-brand))",
          ai: "rgb(var(--solid-ai))",
          info: "rgb(var(--solid-info))",
          verified: "rgb(var(--solid-verified))",
          pending: "rgb(var(--solid-pending))",
          danger: "rgb(var(--solid-danger))",
          gold: "rgb(var(--solid-gold))",
        },

        /* -------------------------------------------------- 存量色系重映射
         * 键名保持不变，色值整体指向 v2 令牌。这样存量页面无需改动，
         * 配色即自动收敛到「墨与纸」，且深色模式由变量驱动。
         * 阶段三页面重构完成后，这些别名可以逐步删除。 */
        // slate -> ink（数字表示层级强度；400 起整体下沉一档以提升对比度）
        slate: {
          ...ink,
          400: ink["500"],
          500: ink["600"],
          600: ink["700"],
          700: ink["800"],
          800: ink["900"],
          900: ink["900"],
        },
        indigo: brand, // 旧主色 #4F46E5 -> 墨蓝 #274C93
        cyan: info, // 旧辅色 -> 中性信息青
        teal: info,
        emerald: verified, // 旧绿 -> 律师已确认语义
        amber: pending, // 旧亮金 -> 待复核语义
        yellow: gold,
        orange: pending,
        red: danger, // 旧红 -> 高风险语义
        rose: danger,
        violet: ai, // 旧紫 -> AI 生成语义
        accent: info, // 旧 preset 键
      },

      /* ------------------------------------------------------------ 字体栈 */
      fontFamily: {
        sans: ["var(--font-sans)"],
        serif: ["var(--font-serif)"],
        mono: ["var(--font-mono)"],
      },

      /* ------------------------------------------------------------ 字号阶梯 */
      fontSize: {
        // 语义字号（规范第 03 节），新代码请优先使用
        caption: ["11px", { lineHeight: "1.5", fontWeight: "500" }],
        label: ["12px", { lineHeight: "1.5", fontWeight: "500" }],
        "body-sm": ["13px", { lineHeight: "1.65" }],
        body: ["14px", { lineHeight: "1.7" }],
        "body-lg": ["15px", { lineHeight: "1.85" }],
        h4: ["16px", { lineHeight: "1.5", fontWeight: "600" }],
        h3: ["18px", { lineHeight: "1.45", fontWeight: "600" }],
        h2: ["22px", { lineHeight: "1.4", fontWeight: "600", letterSpacing: "-0.01em" }],
        h1: ["30px", { lineHeight: "1.3", fontWeight: "600", letterSpacing: "-0.01em" }],
        display: ["38px", { lineHeight: "1.25", fontWeight: "600", letterSpacing: "-0.01em" }],
        // 存量档位：只调整行高以适配中文，不改字号，避免布局位移
        xs: ["12px", { lineHeight: "1.5" }],
        sm: ["14px", { lineHeight: "1.7" }],
        base: ["16px", { lineHeight: "1.75" }],
        lg: ["18px", { lineHeight: "1.45" }],
        xl: ["20px", { lineHeight: "1.5" }],
        "2xl": ["24px", { lineHeight: "1.4" }],
        "3xl": ["30px", { lineHeight: "1.3" }],
      },

      /* ------------------------------------------------------------ 圆角收敛 */
      borderRadius: {
        r1: "var(--r1)", // 4px 徽章、标签
        r2: "var(--r2)", // 6px 按钮、输入框
        r3: "var(--r3)", // 8px 卡片、面板
        r4: "var(--r4)", // 12px 模态、抽屉
        // 存量档位收敛：卡片 12->8px，模态 16/24->12px
        sm: "var(--r1)",
        DEFAULT: "var(--r2)",
        md: "var(--r2)",
        lg: "var(--r3)",
        xl: "var(--r3)",
        "2xl": "var(--r4)",
        "3xl": "var(--r4)",
      },

      /* ------------------------------------------------------------ 阴影三级 */
      boxShadow: {
        s1: "var(--s1)",
        s2: "var(--s2)",
        s3: "var(--s3)",
        focus: "0 0 0 3px var(--focus-ring)",
        // 存量键保留，改为墨蓝色调、降低强度（法律产品优先用边框而非阴影）
        "brand-sm": "0 1px 2px 0 rgba(39, 76, 147, 0.06)",
        "brand-md": "0 4px 12px -2px rgba(39, 76, 147, 0.1)",
        "brand-glow": "0 16px 40px -8px rgba(39, 76, 147, 0.18)",
      },

      /* ---------------------------------------------------------- 渐变收敛 */
      backgroundImage: {
        // 登录页品牌区专用：墨蓝深底，不再是靛蓝->青蓝的彩虹渐变
        "brand-gradient": "linear-gradient(160deg, rgb(var(--brand-800)) 0%, rgb(var(--brand-950)) 100%)",
        "ai-gradient": "linear-gradient(to right, rgb(var(--ai-500)), rgb(var(--ai-600)))",
      },

      /* -------------------------------------------------------------- 动效 */
      transitionDuration: {
        fast: "var(--dur-fast)",
        base: "var(--dur-base)",
        slow: "var(--dur-slow)",
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
        soft: "var(--ease-soft)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in-right": {
          "0%": { opacity: "0", transform: "translateX(24px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "pulse-soft": {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        /* 加载指示的旋转。**取代 Tailwind 内置的旋转类**（`animate-` + `spin`）——
         * 内置那把时长写死成 `1s linear infinite`、**不读 `--dur-*`** ⇒
         * `prefers-reduced-motion: reduce` 下照转不停（A4 拦的就是这类）。
         * 这里把时长/次数接到 `--dur-spin` / `--spin-iter`，reduce 下两者归零 ⇒ 静止。
         * ⚠️ 关键帧名**必须字面出现在 animation 值里**：Tailwind 靠 `value.includes(name)`
         *    决定要不要输出 `@keyframes`，写成 `var(--spin-name)` 会导致**关键帧不产出**。
         * ⚠️ 注释里不把被禁类名写全 —— A4 按源码文本扫、连注释一起扫（见 `Spinner.tsx`）。 */
        "spin-soft": {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
        "sheet-up": {
          "0%": { transform: "translateY(100%)" },
          "100%": { transform: "translateY(0)" },
        },
        "drawer-in": {
          "0%": { opacity: "0", transform: "translateX(16px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "panel-in-right": {
          "0%": { transform: "translateX(100%)" },
          "100%": { transform: "translateX(0)" },
        },
        "panel-in-left": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in var(--dur-base) var(--ease-out) both",
        "slide-in-right": "slide-in-right var(--dur-slow) var(--ease-soft) both",
        "pulse-soft": "pulse-soft var(--dur-pulse) ease-in-out var(--pulse-iter)",
        // 加载指示的**令牌化**旋转；内置的 `animate-` + `spin` 不经令牌，已在 A4 里禁用。
        "spin-soft": "spin-soft var(--dur-spin) linear var(--spin-iter)",
        "sheet-up": "sheet-up var(--dur-slow) var(--ease-soft) both",
        "drawer-in": "drawer-in var(--dur-slow) var(--ease-soft) both",
        "panel-in-right": "panel-in-right var(--dur-slow) var(--ease-soft) both",
        "panel-in-left": "panel-in-left var(--dur-slow) var(--ease-soft) both",
      },

      /* ------------------------------------------------------------ 断点与容器 */
      screens: {
        wide: "1600px", // 驾驶舱全宽档；compact/medium/expanded 复用 sm/lg/xl
      },
      maxWidth: {
        reading: "var(--content-reading)",
        workbench: "var(--content-workbench)",
        wide: "var(--content-wide)",
        citation: "var(--citation-panel-w)",
      },

      /* -------------------------------------------------------------- 层级 */
      zIndex: {
        topbar: "var(--z-topbar)",
        sidebar: "var(--z-sidebar)",
        tabbar: "var(--z-tabbar)",
        drawer: "var(--z-drawer)",
        sheet: "var(--z-sheet)",
        modal: "var(--z-modal)",
        toast: "var(--z-toast)",
      },

      /* -------------------------------------------------------------- 尺寸 */
      spacing: {
        sidebar: "var(--sidebar-w)",
        topbar: "var(--topbar-h)",
        tabbar: "var(--tabbar-h)",
        citation: "var(--citation-panel-w)",
        /* IM 三栏：会话列表栏 / 案件上下文栏（宽度随 1400px 断点自动收窄） */
        rail: "var(--conversation-rail-w)",
        panel: "var(--context-panel-w)",
        tap: "var(--tap)",
      },
    },
  },
};

export default preset;
