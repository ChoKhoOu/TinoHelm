import { useRef, useEffect, useState } from "react";

/**
 * 价格闪烁 hook — 当数值变化时返回方向，用于 CSS 动画
 *
 * Usage:
 *   const flash = useTickFlash(price);
 *   <td className={cn(flash === "up" && "animate-qds-tick-g", flash === "down" && "animate-qds-tick-r")}>
 */
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
