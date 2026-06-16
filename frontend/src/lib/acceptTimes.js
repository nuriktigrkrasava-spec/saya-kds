// Client-side stopwatch store: remembers when an item was accepted, since the
// backend payload exposes only the `accepted` boolean (no timestamp).
// Persisted to localStorage so a screen refresh keeps timers running.

const KEY = "saya_kds_accept_times";

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

function save(map) {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    /* ignore quota / private mode */
  }
}

export function markAccepted(itemId, ts = Date.now()) {
  const map = load();
  if (!map[itemId]) {
    map[itemId] = ts;
    save(map);
  }
  return map[itemId];
}

export function getAcceptedAt(itemId) {
  const map = load();
  return map[itemId] || null;
}

export function clearAccepted(itemId) {
  const map = load();
  if (map[itemId]) {
    delete map[itemId];
    save(map);
  }
}
