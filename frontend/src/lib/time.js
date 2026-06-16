import { useEffect, useState } from "react";

// Re-render every second so running stopwatches update.
export function useTick(intervalMs = 1000) {
  const [, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

export function formatElapsed(ms) {
  if (ms == null || ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Returns "ok" | "warn" | "overdue" based on minutes since accepted.
export function overdueLevel(ms) {
  const min = ms / 60000;
  if (min >= 10) return "overdue";
  if (min >= 5) return "warn";
  return "ok";
}
