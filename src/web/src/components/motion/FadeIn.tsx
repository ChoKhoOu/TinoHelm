"use client";

import { motion } from "framer-motion";

interface Props {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  delay?: number;
  duration?: number;
  direction?: "up" | "down" | "left" | "right" | "none";
  scale?: number;
}

export function FadeIn({ children, className, style, delay = 0, duration = 0.28, direction = "up", scale }: Props) {
  const offset = 12;
  const directionMap: Record<string, { x?: number; y?: number }> = {
    up: { y: offset },
    down: { y: -offset },
    left: { x: offset },
    right: { x: -offset },
    none: {},
  };

  return (
    <motion.div
      initial={{ opacity: 0, ...directionMap[direction], ...(scale !== undefined ? { scale } : {}) }}
      animate={{ opacity: 1, x: 0, y: 0, ...(scale !== undefined ? { scale: 1 } : {}) }}
      transition={{ duration, delay, ease: [0.16, 1, 0.3, 1] }}
      className={className}
      style={style}
    >
      {children}
    </motion.div>
  );
}
