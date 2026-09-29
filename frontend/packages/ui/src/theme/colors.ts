/**
 * 律小智主题色板 v2「墨与纸」
 * ----------------------------------------------------------------------------
 * 与 `packages/ui/src/tokens.css` 一一对应。CSS 变量用于样式，本文件提供 JS 侧的
 * 字面量色值，供图表、Canvas、内联样式、导出 PDF/Word 等无法使用 CSS 变量的场景。
 *
 * ⚠️ 本文件修复了一个历史缺陷：旧版 `themeColors` 的键名与色值完全错位
 *    （`emerald` 实际存的是靛蓝、`teal` 是青、`cyan` 是紫），导致
 *    `<KpiCard color="emerald" />` 渲染出蓝色。现按语义重新组织。
 *
 * 命名约定：数字表示层级强度，不是固定明度。深色模式见 `paletteDark`。
 */

/* ------------------------------------------------------------------ 类型 */

export interface ColorScale {
  50: string;
  100: string;
  200: string;
  300: string;
  400: string;
  500: string;
  600: string;
  700: string;
}

export interface NeutralScale extends ColorScale {
  800: string;
  900: string;
  950: string;
}

export interface Palette {
  ink: NeutralScale;
  brand: NeutralScale;
  ai: ColorScale;
  verified: ColorScale;
  pending: ColorScale;
  danger: ColorScale;
  gold: ColorScale;
  info: ColorScale;
}

/* -------------------------------------------------------------- 浅色模式 */

export const palette: Palette = {
  /** 中性层：替代 slate。数字 = 层级强度 */
  ink: {
    50: "#F7F8F9",
    100: "#EFF1F3",
    200: "#E2E5E9",
    300: "#CBD1D8",
    400: "#9AA3AE",
    500: "#6B7480",
    600: "#4A525C",
    700: "#343B44",
    800: "#21262D",
    900: "#14181D",
    950: "#0B0E12",
  },
  /** 品牌墨蓝：主按钮、激活导航、链接 */
  brand: {
    50: "#EEF3FB",
    100: "#D9E4F7",
    200: "#B6C9EE",
    300: "#8AA8E0",
    400: "#5A80CB",
    500: "#3660B0",
    600: "#274C93",
    700: "#1E3C77",
    800: "#16305F",
    900: "#102547",
    950: "#0A1830",
  },
  /** AI 生成：禁止作装饰色使用 */
  ai: {
    50: "#F4F2FD",
    100: "#E8E4FA",
    200: "#D0C7F3",
    300: "#B0A2E8",
    400: "#8A76DA",
    500: "#6E56CF",
    600: "#5B45B0",
    700: "#48368C",
  },
  /** 律师已确认 */
  verified: {
    50: "#EDF8F4",
    100: "#D6EFE6",
    200: "#B2E0D0",
    300: "#85CAB2",
    400: "#3EA884",
    500: "#1E8E6E",
    600: "#167055",
    700: "#125C46",
  },
  /** 待复核 / 风险 / 待同步 */
  pending: {
    50: "#FDF6E9",
    100: "#FAECD0",
    200: "#F0D6A0",
    300: "#E2BC70",
    400: "#C89C3E",
    500: "#B4791F",
    600: "#8F5F14",
    700: "#704A10",
  },
  /** 高风险 / 违约 / 待办角标 */
  danger: {
    50: "#FDF0EE",
    100: "#FADED9",
    200: "#F2BAB1",
    300: "#E68C80",
    400: "#D2584A",
    500: "#C0392B",
    600: "#A62F23",
    700: "#86251C",
  },
  /** 点缀金：S 级、引用序号、变量高亮。全局面积 ≤ 3% */
  gold: {
    50: "#FBF7EF",
    100: "#F6EEDE",
    200: "#EBDCBA",
    300: "#DCC494",
    400: "#C4A66C",
    500: "#B08A4F",
    600: "#8F6E3C",
    700: "#70562E",
  },
  /** 中性信息提示（偏青，与品牌蓝区分） */
  info: {
    50: "#EFF5FB",
    100: "#DBEAF6",
    200: "#B8D4EB",
    300: "#8CB6DC",
    400: "#528EC5",
    500: "#2A6FB0",
    600: "#225C93",
    700: "#1B4A76",
  },
};

/* -------------------------------------------------------------- 深色模式 */

export const paletteDark: Palette = {
  ink: {
    50: "#0B0E12",
    100: "#1A1F26",
    200: "#232931",
    300: "#2E353F",
    400: "#7E8795",
    500: "#9AA3AE",
    600: "#B4BCC6",
    700: "#CDD3DA",
    800: "#E3E7EC",
    900: "#F2F4F6",
    950: "#0B0E12",
  },
  brand: {
    50: "#10203A",
    100: "#16305F",
    200: "#1E3C77",
    300: "#274C93",
    400: "#6E94D6",
    500: "#4E77C4",
    600: "#3A63B0",
    700: "#274C93",
    800: "#16305F",
    900: "#102547",
    950: "#0A1830",
  },
  ai: {
    50: "#201A36",
    100: "#2A2246",
    200: "#382E5C",
    300: "#4C3E7C",
    400: "#8A76DA",
    500: "#9480E2",
    600: "#B0A0EE",
    700: "#C8BEF6",
  },
  verified: {
    50: "#122821",
    100: "#18352B",
    200: "#204639",
    300: "#2B5C4A",
    400: "#3A9676",
    500: "#34B48D",
    600: "#52C4A0",
    700: "#82D6B8",
  },
  pending: {
    50: "#2C220E",
    100: "#3C2D11",
    200: "#523D16",
    300: "#6E521E",
    400: "#92702C",
    500: "#C89130",
    600: "#DAA84E",
    700: "#E8C480",
  },
  danger: {
    50: "#2E1613",
    100: "#3E1C18",
    200: "#562620",
    300: "#76342C",
    400: "#A8443A",
    500: "#D65448",
    600: "#E87A6E",
    700: "#F09E94",
  },
  gold: {
    50: "#2A2316",
    100: "#3A2F1C",
    200: "#4E3F26",
    300: "#685432",
    400: "#8C7044",
    500: "#C49E62",
    600: "#D8B680",
    700: "#E6CCA0",
  },
  info: {
    50: "#102232",
    100: "#162E44",
    200: "#20405E",
    300: "#2C5880",
    400: "#4A82BA",
    500: "#5C9ED6",
    600: "#84BAE6",
    700: "#ACD2F0",
  },
};

/* ---------------------------------------------------------------- 语义角色 */

export const semanticColors = {
  /** 主色 */
  primary: palette.brand,
  brand: palette.brand,
  /** 链接与强调文本（深色模式下改用 paletteDark.brand[400]） */
  link: "#274C93",
  linkHover: "#1E3C77",
  linkDark: "#8AA8E0",

  /** 责任边界三态 */
  ai: palette.ai,
  verified: palette.verified,
  pending: palette.pending,
  /** 高风险 */
  danger: palette.danger,
  /** 点缀金 */
  gold: palette.gold,
  /** 中性信息 */
  info: palette.info,
  /** 中性层 */
  neutral: palette.ink,

  surface: {
    page: "#F7F8F9",
    card: "#FFFFFF",
    subtle: "#EFF1F3",
    hover: "#E2E5E9",
    sunken: "#E2E5E9",
    sidebar: "#0A1830",
  },
  border: {
    subtle: "#E2E5E9",
    DEFAULT: "#E2E5E9",
    strong: "#CBD1D8",
    focus: "#5A80CB",
  },
  text: {
    primary: "#14181D",
    secondary: "#343B44",
    tertiary: "#4A525C",
    muted: "#6B7480",
    faint: "#9AA3AE",
    inverse: "#FFFFFF",
    onSidebar: "#FFFFFF",
    onSidebarMuted: "#94A6C4",
  },
  status: {
    success: palette.verified[600],
    warning: palette.pending[600],
    error: palette.danger[500],
    info: palette.info[600],
  },
} as const;

/**
 * 责任边界三态定义（规范第 05 节）。
 * 颜色不可独立承载语义，必须「颜色 + 文字 + 图标」三重编码，
 * 因此这里把三者打包在一起，供 ProvenanceBadge 与导出模块统一取用。
 */
export const provenanceStates = {
  ai: {
    id: "ai",
    label: "AI 生成",
    icon: "✦",
    color: palette.ai[500],
    bg: palette.ai[50],
    borderStyle: "dashed" as const,
  },
  verified: {
    id: "verified",
    label: "律师已确认",
    icon: "✓",
    color: palette.verified[600],
    bg: palette.verified[50],
    borderStyle: "solid" as const,
  },
  pending: {
    id: "pending",
    label: "待复核",
    icon: "⏱",
    color: palette.pending[600],
    bg: palette.pending[50],
    borderStyle: "solid" as const,
  },
} as const;

export type ProvenanceState = keyof typeof provenanceStates;

/** 案件等级 -> 配色（S 金 / A 朱砂 / B 墨蓝 / C 中性）。字母 + 颜色双通道编码。 */
export const caseGradeColors: Record<string, string> = {
  S: palette.gold[500],
  A: palette.danger[500],
  B: palette.brand[600],
  C: palette.ink[400],
};

/**
 * @deprecated v2 已废弃，仅为兼容保留。
 * 旧版键名与色值完全错位（emerald=靛蓝 / teal=青 / cyan=紫），
 * 本对象已把键名指向正确色系。新代码请使用 `palette` 或 `semanticColors`。
 */
export const themeColors = {
  emerald: palette.verified,
  teal: palette.info,
  cyan: palette.ai,
  dark: palette.ink,
  amber: palette.pending,
};
