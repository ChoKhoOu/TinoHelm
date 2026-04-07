# QDS × shadcn — 代码架构指南

## 核心思路

```
globals.css (CSS variables)
    ↓ 映射
tailwind.config.ts (theme extend)
    ↓ 消费
shadcn 组件 (自动继承)
    ↓ 封装
QDS 业务组件 (stat-card, progress-bar, etc.)
```

一个地方改 token，全局生效。不需要在每个组件里硬编码颜色。

---

## 1. globals.css — 单一 Token 源

shadcn 默认用 HSL 格式的 CSS variables。我们把 QDS 的 hex token 转成 HSL 塞进去，
shadcn 的所有组件（Button, Card, Table, Dialog...）自动就是 QDS 风格。

```css
/* src/styles/globals.css */

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  /* ===== QDS Warm Minimal — Dark (default) ===== */
  :root {
    /* shadcn 标准变量 → 映射到 QDS token */
    --background: 40 4% 15%;           /* #262624 bg-s */
    --foreground: 43 8% 89%;           /* #E8E6E0 t0 */

    --card: 37 4% 18%;                 /* #302f2d bg-p */
    --card-foreground: 43 8% 89%;

    --popover: 40 6% 8%;               /* #141413 bg-in */
    --popover-foreground: 43 8% 89%;

    --primary: 17 61% 58%;             /* #D97857 acc */
    --primary-foreground: 0 0% 100%;

    --secondary: 40 3% 22%;            /* #3b3a37 bg-t */
    --secondary-foreground: 43 8% 89%;

    --muted: 40 3% 22%;               /* #3b3a37 */
    --muted-foreground: 40 4% 44%;    /* #73726C t2 */

    --accent: 17 61% 58%;             /* #D97857 */
    --accent-foreground: 0 0% 100%;

    --destructive: 0 99% 75%;         /* #FE8181 dan */
    --destructive-foreground: 0 0% 100%;

    --border: 40 3% 22%;              /* #3b3a37 bd */
    --input: 40 3% 22%;
    --ring: 17 61% 58%;               /* acc for focus ring */

    --radius: 0.75rem;                /* 12px = --r */

    /* ===== QDS 扩展变量 ===== */
    --qds-bg-inset: 60 4% 8%;         /* #141413 */
    --qds-success: 144 42% 37%;       /* #36884B */
    --qds-success-dim: 144 42% 37% / 0.12;
    --qds-danger: 0 99% 75%;          /* #FE8181 */
    --qds-danger-dim: 0 99% 75% / 0.12;
    --qds-info: 212 72% 71%;          /* #85B7EB */
    --qds-info-dim: 212 72% 71% / 0.12;
    --qds-warning: 39 94% 72%;        /* #FAC775 */
    --qds-warning-dim: 39 94% 72% / 0.12;

    --qds-t1: 43 3% 59%;              /* #9C9A92 */
    --qds-t2: 40 4% 44%;              /* #73726C */
    --qds-t3: 40 3% 36%;              /* #5F5E5A */
    --qds-border-hover: 40 3% 36%;    /* #5F5E5A bdh */

    --qds-shimmer: rgba(255, 255, 255, 0.35);

    /* Typography */
    --font-mono: 'IBM Plex Mono', monospace;
    --font-sans: 'IBM Plex Sans', sans-serif;
  }

  /* ===== QDS Warm Minimal — Light ===== */
  .light, [data-theme="light"] {
    --background: 43 18% 97%;         /* #faf9f5 */
    --foreground: 40 4% 10%;          /* #2C2C2A */

    --card: 43 14% 94%;               /* #f5f4ed */
    --card-foreground: 40 4% 10%;

    --popover: 43 25% 98%;            /* #fcfbf8 */
    --popover-foreground: 40 4% 10%;

    --primary: 17 61% 58%;            /* #D97857 same */
    --primary-foreground: 0 0% 100%;

    --secondary: 40 11% 88%;          /* #eae8e0 */
    --secondary-foreground: 40 4% 10%;

    --muted: 40 11% 88%;
    --muted-foreground: 40 4% 44%;

    --border: 37 10% 83%;             /* #dedbd3 */
    --input: 37 10% 83%;
    --ring: 17 61% 58%;

    --qds-danger: 0 60% 31%;          /* #8A2425 */
    --qds-danger-dim: 0 60% 31% / 0.08;
    --qds-info: 212 55% 40%;          /* #3266AD */
    --qds-info-dim: 212 55% 40% / 0.08;
    --qds-warning: 36 82% 28%;        /* #854F0B */

    --qds-t1: 40 4% 44%;
    --qds-t2: 43 3% 59%;
    --qds-t3: 40 5% 67%;
    --qds-border-hover: 37 7% 75%;

    --qds-shimmer: rgba(255, 255, 255, 0.7);
  }

  /* ===== Base resets ===== */
  * {
    @apply border-border;
  }
  body {
    @apply bg-background text-foreground;
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
  }
}
```

---

## 2. tailwind.config.ts — 映射 Token 到 Utility Classes

```typescript
// tailwind.config.ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],     // 用 class 控制，不用 prefers-color-scheme
  content: ["./src/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      /* ===== Colors — 直接引用 CSS variables ===== */
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        /* QDS 扩展 */
        qds: {
          inset: "hsl(var(--qds-bg-inset))",
          success: "hsl(var(--qds-success))",
          "success-dim": "hsl(var(--qds-success-dim))",
          danger: "hsl(var(--qds-danger))",
          "danger-dim": "hsl(var(--qds-danger-dim))",
          info: "hsl(var(--qds-info))",
          "info-dim": "hsl(var(--qds-info-dim))",
          warning: "hsl(var(--qds-warning))",
          "warning-dim": "hsl(var(--qds-warning-dim))",
          t1: "hsl(var(--qds-t1))",
          t2: "hsl(var(--qds-t2))",
          t3: "hsl(var(--qds-t3))",
        },
      },

      /* ===== Typography ===== */
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },

      /* ===== Border radius ===== */
      borderRadius: {
        lg: "var(--radius)",       // 12px
        md: "calc(var(--radius) - 4px)",  // 8px
        sm: "calc(var(--radius) - 6px)",  // 6px
      },

      /* ===== QDS 动效 ===== */
      transitionTimingFunction: {
        qds: "cubic-bezier(.16, 1, .3, 1)",  // --eo
      },
      transitionDuration: {
        qds: "280ms",        // 标准 enter
        "qds-fast": "150ms", // hover
        "qds-slow": "400ms", // panel expand
      },
      keyframes: {
        "qds-shimmer": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
        "qds-pulse-ring": {
          "0%": { transform: "scale(0.8)", opacity: "0.7" },
          "100%": { transform: "scale(2.2)", opacity: "0" },
        },
        "qds-tick-green": {
          "0%": { backgroundColor: "hsl(var(--qds-success-dim))" },
          "100%": { backgroundColor: "transparent" },
        },
        "qds-tick-red": {
          "0%": { backgroundColor: "hsl(var(--qds-danger-dim))" },
          "100%": { backgroundColor: "transparent" },
        },
        "qds-fade-up": {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "qds-slide-right": {
          "0%": { opacity: "0", transform: "translateX(30px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "qds-skeleton": {
          "0%": { transform: "translateX(-50%)" },
          "100%": { transform: "translateX(50%)" },
        },
      },
      animation: {
        "qds-shimmer": "qds-shimmer 2.5s ease-in-out infinite",
        "qds-pulse": "qds-pulse-ring 2s cubic-bezier(.16,1,.3,1) infinite",
        "qds-tick-g": "qds-tick-green 0.6s ease-out",
        "qds-tick-r": "qds-tick-red 0.6s ease-out",
        "qds-fade-up": "qds-fade-up 0.35s cubic-bezier(.16,1,.3,1)",
        "qds-slide-in": "qds-slide-right 0.35s cubic-bezier(.16,1,.3,1)",
        "qds-skeleton": "qds-skeleton 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
```

用的时候直接写 class：
```tsx
<div className="bg-card text-card-foreground rounded-lg border p-4 transition-colors duration-qds-fast hover:border-qds-t3">
  <span className="text-qds-success font-mono">+$12,480</span>
</div>
```

---

## 3. Chart.js 全局主题

```typescript
// src/lib/chart-theme.ts
import { useTheme } from "next-themes";

export function useChartTheme() {
  const { resolvedTheme } = useTheme();
  const isLight = resolvedTheme === "light";

  return {
    colors: {
      success: "#36884B",
      danger: isLight ? "#8A2425" : "#FE8181",
      accent: "#D97857",
      info: isLight ? "#3266AD" : "#85B7EB",
      warning: isLight ? "#854F0B" : "#FAC775",
      grid: isLight ? "rgba(0,0,0,.05)" : "rgba(255,255,255,.05)",
      tick: isLight ? "rgba(44,44,42,.4)" : "rgba(232,230,224,.4)",
      text: isLight ? "#2C2C2A" : "#E8E6E0",
      muted: isLight ? "rgba(0,0,0,.15)" : "rgba(255,255,255,.2)",
    },
    tooltip: {
      backgroundColor: isLight ? "#f5f4ed" : "#302f2d",
      titleColor: isLight ? "#2C2C2A" : "#E8E6E0",
      bodyColor: isLight ? "rgba(44,44,42,.4)" : "rgba(232,230,224,.4)",
      borderColor: isLight ? "#dedbd3" : "#3b3a37",
      borderWidth: 1,
      cornerRadius: 8,
      padding: 10,
      bodyFont: { family: "'IBM Plex Mono'" },
      titleFont: { family: "'IBM Plex Mono'", weight: "600" as const },
    },
    scales: {
      grid: { color: isLight ? "rgba(0,0,0,.05)" : "rgba(255,255,255,.05)", drawBorder: false },
      ticks: {
        color: isLight ? "rgba(44,44,42,.4)" : "rgba(232,230,224,.4)",
        font: { family: "'IBM Plex Mono'", size: 10 },
      },
    },
  };
}
```

---

## 4. QDS 业务组件

shadcn 的 Card, Table, Badge, Dialog 直接用（它们已经通过 CSS variables 继承了 QDS 配色）。
以下是 shadcn 不提供、需要自己封装的 QDS 特有组件：

### 4.1 StatCard

```tsx
// src/components/qds/stat-card.tsx
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  trend?: "up" | "down" | "neutral";
  help?: string;
  className?: string;
}

export function StatCard({ label, value, sub, trend, help, className }: StatCardProps) {
  return (
    <div className={cn(
      "rounded-lg border bg-card p-4 transition-colors duration-qds-fast hover:border-qds-t3",
      className
    )}>
      <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground flex items-center gap-1">
        {label}
        {help && <HelpTip text={help} />}
      </div>
      <div className={cn(
        "font-mono text-xl font-semibold mt-1",
        trend === "up" && "text-qds-success",
        trend === "down" && "text-qds-danger",
      )}>
        {value}
      </div>
      {sub && (
        <div className={cn(
          "font-mono text-[0.65rem] mt-0.5",
          trend === "up" && "text-qds-success",
          trend === "down" && "text-qds-danger",
          !trend && "text-muted-foreground",
        )}>
          {sub}
        </div>
      )}
    </div>
  );
}
```

### 4.2 ShimmerBar (进度条)

```tsx
// src/components/qds/shimmer-bar.tsx
import { cn } from "@/lib/utils";

interface ShimmerBarProps {
  progress: number;   // 0-100
  height?: "sm" | "md";  // 3px or 6px
  active?: boolean;   // shimmer animation
  variant?: "accent" | "success" | "danger";
}

export function ShimmerBar({ progress, height = "sm", active = true, variant = "accent" }: ShimmerBarProps) {
  const colors = {
    accent: "bg-primary",
    success: "bg-qds-success",
    danger: "bg-qds-danger",
  };

  return (
    <div className={cn(
      "w-full overflow-hidden rounded-full bg-secondary relative",
      height === "sm" ? "h-[3px]" : "h-1.5",
    )}>
      <div
        className={cn("h-full rounded-full transition-[width] duration-[1.5s] ease-qds", colors[variant])}
        style={{ width: `${progress}%` }}
      />
      {active && (
        <div className="absolute inset-0 animate-qds-shimmer">
          <div className="h-full w-full bg-gradient-to-r from-transparent via-[var(--qds-shimmer)] to-transparent" />
        </div>
      )}
    </div>
  );
}
```

### 4.3 HelpTip (? 图标)

```tsx
// src/components/qds/help-tip.tsx
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function HelpTip({ text }: { text: string }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border text-[0.55rem] text-qds-t3 cursor-help ml-1 transition-colors hover:text-qds-t1 hover:border-qds-t3 hover:bg-secondary">
            ?
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-[220px] text-xs">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
```

### 4.4 StatusBadge

```tsx
// src/components/qds/status-badge.tsx
import { cn } from "@/lib/utils";

type Status = "running" | "done" | "failed" | "queued";

const styles: Record<Status, string> = {
  running: "bg-primary/10 text-primary",
  done: "bg-qds-success-dim text-qds-success",
  failed: "bg-qds-danger-dim text-qds-danger",
  queued: "bg-secondary text-muted-foreground",
};

const labels: Record<Status, string> = {
  running: "Running",
  done: "✓ Done",
  failed: "✕ Failed",
  queued: "◦ Queued",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 font-mono text-[0.68rem] font-medium px-2.5 py-0.5 rounded-full",
      styles[status],
    )}>
      {status === "running" && <PulseRing />}
      {labels[status]}
    </span>
  );
}

function PulseRing() {
  return (
    <span className="relative w-1.5 h-1.5 rounded-full bg-primary">
      <span className="absolute inset-[-3px] rounded-full border-[1.5px] border-primary animate-qds-pulse opacity-0" />
    </span>
  );
}
```

### 4.5 TickFlash (价格闪烁)

```tsx
// src/hooks/use-tick-flash.ts
import { useRef, useEffect, useState } from "react";

export function useTickFlash(value: number) {
  const prevRef = useRef(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value !== prevRef.current) {
      setFlash(value > prevRef.current ? "up" : "down");
      prevRef.current = value;
      const t = setTimeout(() => setFlash(null), 600);
      return () => clearTimeout(t);
    }
  }, [value]);

  return flash;
}

// Usage in component:
// const flash = useTickFlash(markPrice);
// <td className={cn(flash === "up" && "animate-qds-tick-g", flash === "down" && "animate-qds-tick-r")}>
```

---

## 5. 页面布局约定

### 5.1 PageHeader 组件

```tsx
// src/components/qds/page-header.tsx
interface PageHeaderProps {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}

export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        <h1 className="text-lg font-bold">{title}</h1>
        {subtitle && <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}
```

### 5.2 SectionLabel

```tsx
// src/components/qds/section-label.tsx
export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3 font-mono text-[0.55rem] tracking-widest uppercase text-primary">
      {children}
      <div className="flex-1 h-px bg-border" />
    </div>
  );
}
```

---

## 6. 动效规范 (Tailwind classes)

| 动效 | Tailwind class | 场景 |
|------|---------------|------|
| Hover | `transition-colors duration-qds-fast` | 所有可交互元素 |
| Enter | `animate-qds-fade-up` | 页面 section 入场 |
| Slide in | `animate-qds-slide-in` | 详情页进入 |
| Shimmer | `animate-qds-shimmer` | 进度条 |
| Tick flash | `animate-qds-tick-g` / `animate-qds-tick-r` | 价格更新 |
| Skeleton | `animate-qds-skeleton` | 骨架屏 |
| Pulse | `animate-qds-pulse` | Running 状态指示 |
| Button hover | `hover:-translate-y-0.5 hover:shadow-md` | 按钮上浮 |
| Button active | `active:translate-y-px active:scale-[0.98]` | 按钮按下 |

**绝对不要用的：**
- `ease-in-out` (除 shimmer)
- `animate-spin` 做 loading（用 scale + fade）
- `transition-all`（性能差，用具体属性）
- 任何鼠标跟踪效果

---

## 7. 文件结构

```
src/
├── styles/
│   └── globals.css              ← QDS token (唯一真相源)
├── components/
│   ├── ui/                      ← shadcn 原生组件 (npx shadcn@latest add)
│   │   ├── button.tsx
│   │   ├── card.tsx
│   │   ├── table.tsx
│   │   ├── dialog.tsx
│   │   ├── tooltip.tsx
│   │   └── ...
│   ├── qds/                     ← QDS 特有组件
│   │   ├── stat-card.tsx
│   │   ├── shimmer-bar.tsx
│   │   ├── status-badge.tsx
│   │   ├── help-tip.tsx
│   │   ├── page-header.tsx
│   │   ├── section-label.tsx
│   │   ├── id-badge.tsx
│   │   ├── env-bar.tsx
│   │   └── tick-value.tsx
│   └── layout/
│       ├── app-shell.tsx         ← Sidebar + Topbar + Content + StatusBar
│       ├── sidebar.tsx
│       ├── topbar.tsx
│       └── status-bar.tsx
├── lib/
│   ├── chart-theme.ts           ← Chart.js 全局配色
│   └── utils.ts                 ← cn() helper
├── hooks/
│   ├── use-tick-flash.ts
│   └── use-theme.ts
└── app/
    ├── layout.tsx               ← AppShell wrapper
    ├── dashboard/
    ├── strategies/
    ├── trading/
    ├── backtests/
    ├── data/
    └── settings/
```

---

## 8. shadcn 组件要覆盖的样式

shadcn 安装后大部分组件已经用 CSS variables，只需微调几个：

```tsx
// Button — 加上 QDS 动效
// 修改 components/ui/button.tsx 的 variants:
const buttonVariants = cva(
  "... transition-all duration-qds-fast ease-qds active:translate-y-px active:scale-[0.98]",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:opacity-90 hover:-translate-y-0.5",
        destructive: "border border-qds-danger text-qds-danger hover:bg-qds-danger hover:text-white",
        outline: "border bg-transparent hover:bg-secondary hover:border-qds-t3",
        ghost: "hover:bg-secondary hover:text-foreground",
        warning: "border border-qds-warning text-qds-warning hover:bg-qds-warning hover:text-black",
      },
      // ...
    },
  }
);
```

```tsx
// Dialog — 加上 QDS 入场动效
// 修改 components/ui/dialog.tsx:
// DialogContent 加上:
className="... data-[state=open]:animate-qds-fade-up"
```

```tsx
// Table — 加上 hover 行高亮
// 修改 components/ui/table.tsx:
// TableRow 加上:
className="... transition-colors duration-qds-fast hover:bg-secondary"
```

---

## 总结

| 层级 | 文件 | 职责 |
|------|------|------|
| Token | `globals.css` | 颜色、字体、间距的唯一真相源 |
| 映射 | `tailwind.config.ts` | Token → Tailwind utility class |
| 基础组件 | `components/ui/` | shadcn 原生，自动继承 token |
| 业务组件 | `components/qds/` | StatCard / ShimmerBar / StatusBadge 等 |
| 布局 | `components/layout/` | AppShell / Sidebar / Topbar |
| 图表 | `lib/chart-theme.ts` | Chart.js 配色跟随主题切换 |
| 动效 | `tailwind.config.ts` | 统一的 keyframes + duration + easing |

改颜色？改 `globals.css` 一个地方。
改动效？改 `tailwind.config.ts` 一个地方。
加新组件？放 `components/qds/`，用已有的 token 和 utility class。
