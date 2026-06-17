import { useEffect, useState } from "react";

// Target cook time: the progress bar fills 0 -> 100% over this window.
export const TARGET_MS = 10 * 60 * 1000;

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

// Progress through the target cook time, capped 0..1 for the visual fill.
// (The timer text itself keeps counting past 100%.)
export function cookProgress(ms, target = TARGET_MS) {
  if (ms == null || ms < 0) ms = 0;
  return Math.min(1, ms / target);
}

// Parse a server ISO timestamp ("...Z") to epoch ms, or null.
export function parseServerTime(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) ? t : null;
}

// Boosts that force urgency tiers to dominate the age tiebreaker.
const OVERDUE_BOOST = 1e9;
const WARN_BOOST = 1e6;

// Numeric urgency score for an order (higher = more urgent). Pure: reads
// "now" via Date.now(). Considers only items whose station is in `stations`.
//
// Score = ageInSeconds (since created_at)
//       + OVERDUE_BOOST if any relevant accepted-not-ready item is overdue
//       + WARN_BOOST    if any relevant accepted-not-ready item is "warn"
//
// Missing timestamps contribute 0 boost; missing created_at => 0 age.
export function orderUrgency(order, stations) {
  if (!order) return 0;
  const now = Date.now();

  const createdAt = parseServerTime(order.created_at);
  const ageSeconds = createdAt != null ? Math.max(0, (now - createdAt) / 1000) : 0;

  const stationSet = Array.isArray(stations) ? stations : [];
  const items = Array.isArray(order.items) ? order.items : [];

  let hasOverdue = false;
  let hasWarn = false;

  for (const it of items) {
    if (!it || !stationSet.includes(it.station)) continue;
    if (!it.accepted || it.ready) continue;
    const acceptedAt = parseServerTime(it.accepted_at);
    if (acceptedAt == null) continue; // missing timestamp => no boost
    const level = overdueLevel(now - acceptedAt);
    if (level === "overdue") hasOverdue = true;
    else if (level === "warn") hasWarn = true;
  }

  let score = ageSeconds;
  if (hasOverdue) score += OVERDUE_BOOST;
  if (hasWarn) score += WARN_BOOST;
  return score;
}
