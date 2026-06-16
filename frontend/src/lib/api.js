import { getOrgId } from "./auth.js";

export const BASE = "http://localhost:8000";
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

// Convenience API calls matching the backend contract.
export const api = {
  getOrders: () => get(`/orders?org_id=${getOrgId()}`),
  seed: () => post(`/debug/seed?org_id=${getOrgId()}`),
  login: (pin) => post("/auth/login", { pin }),
  acceptItem: (itemId, by) => post(`/items/${itemId}/accept`, { by }),
  readyItem: (itemId, by) => post(`/items/${itemId}/ready`, { by }),
  checkItem: (itemId, by) => post(`/items/${itemId}/check`, { by }),
  assembleOrder: (orderId, by) => post(`/orders/${orderId}/assemble`, { by }),
  takeOrder: (orderId, by) => post(`/orders/${orderId}/take`, { by }),
  deliverOrder: (orderId, by) => post(`/orders/${orderId}/delivered`, { by }),
};
