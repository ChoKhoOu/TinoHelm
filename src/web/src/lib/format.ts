export function formatDateTime(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace("T", " ").slice(0, 16);
}

export function formatDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace("T", " ").slice(0, 10);
}

export function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    return ts;
  }
}
