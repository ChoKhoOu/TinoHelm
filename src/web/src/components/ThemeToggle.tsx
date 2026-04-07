"use client";

import { useState, useEffect } from "react";

export function ThemeToggle() {
  const [light, setLight] = useState(false);

  useEffect(() => {
    setLight(document.documentElement.classList.contains("light"));
  }, []);

  const toggle = () => {
    const next = !light;
    setLight(next);
    // Add transitioning class BEFORE theme change for uniform 280ms transition
    document.documentElement.classList.add("theme-transitioning");
    if (next) {
      document.documentElement.classList.add("light");
    } else {
      document.documentElement.classList.remove("light");
    }
    localStorage.setItem("theme", next ? "light" : "dark");
    // Remove after transition completes
    setTimeout(() => {
      document.documentElement.classList.remove("theme-transitioning");
    }, 300);
  };

  return (
    <button
      onClick={toggle}
      aria-label={light ? "Switch to dark mode" : "Switch to light mode"}
      className="w-7 h-7 rounded-full border bg-transparent text-muted-foreground cursor-pointer flex items-center justify-center hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all duration-150 text-[0.82rem]"
    >
      ◑
    </button>
  );
}
