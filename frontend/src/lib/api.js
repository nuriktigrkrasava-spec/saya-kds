import { getOrgId } from "./auth.js";

// В Vite-деве (порт 5173) ходим на локальный backend :8000.
// Когда SPA раздаётся самим backend (ngrok/Render) — тот же origin (относительные пути),
// чтобы работало и в Telegram-мини-аппе, и в проде.
export const BASE =
  typeof window !== "undefined" && window.location.port === "5173"
    ? "http://localhost:8000"
    : "";
export const ORG_ID = "demo";

async function handle(res) {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${res.statusText} ${text}`.trim());
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export async function get(path) {
  const res = await fetch(`${BASE}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return handle(res);
}

export async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  return handle(res);
}

// In the Telegram mini-app the SAME origin serves both API and app, so use
// same-origin relative fetches when running inside Telegram, else fall back to
// the dev BASE.
export function tgBase() {
  if (typeof window !== "undefined" && window.Telegram?.WebApp?.initData) {
    return window.location.origin;
  }
  return BASE;
}

async function tgGet(path) {
  const res = await fetch(`${tgBase()}${path}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return handle(res);
}

async function tgPost(path, body) {
  const res = await fetch(`${tgBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  return handle(res);
}

// Convenience API calls matching the backend contract.
export const api = {
  getOrders: () => get(`/orders?org_id=${getOrgId()}`),
  seed: () => post(`/debug/seed?org_id=${getOrgId()}`),
  login: (pin) => post("/auth/login", { pin }),
  acceptItem: (itemId, by) => post(`/items/${itemId}/accept`, { by }),
  readyItem: (itemId, by) => post(`/items/${itemId}/ready`, { by }),
  checkItem: (itemId, by) => post(`/items/${itemId}/check`, { by }),
  revertItem: (itemId, by) => post(`/items/${itemId}/revert`, { by }),
  assembleOrder: (orderId, by) => post(`/orders/${orderId}/assemble`, { by }),
  takeOrder: (orderId, by) => post(`/orders/${orderId}/take`, { by }),
  deliverOrder: (orderId, by) => post(`/orders/${orderId}/delivered`, { by }),

  // Assembler assigns a courier (also marks the order assembled).
  branchCouriers: (branchCode) =>
    get(`/branches/couriers?branch_code=${encodeURIComponent(branchCode)}`),
  assignCourier: (orderId, courier_code, courier_name, by) =>
    post(`/orders/${orderId}/assign-courier`, { courier_code, courier_name, by }),

  // Telegram mini-app courier helpers (same-origin in Telegram).
  tgAuth: (initData) => tgPost("/tg/auth", { initData }),
  tgLink: (initData, pin) => tgPost("/tg/link", { initData, pin }),
  tgMyOrders: (courier_code) =>
    tgGet(`/tg/my-orders?courier_code=${encodeURIComponent(courier_code)}`),
  tgLocation: (initData, lat, lon) => tgPost("/tg/location", { initData, lat, lon }),
  tgUnlink: (initData) => tgPost("/tg/unlink", { initData }),
};
