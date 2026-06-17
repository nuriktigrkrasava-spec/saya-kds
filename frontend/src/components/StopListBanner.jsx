import { useEffect, useState } from "react";
import { BASE } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import { socket } from "../lib/socket.js";

/**
 * Sticky-баннер стоп-листа: показывает позиции, которых нет в наличии.
 * Грузит /stoplist при монтировании и слушает socket "stoplist:update" для live-обновлений.
 * Если стоп-лист пуст — ничего не рендерит.
 */
export default function StopListBanner() {
  const [items, setItems] = useState([]);
  const orgId = getOrgId();

  useEffect(() => {
    let alive = true;

    fetch(`${BASE}/stoplist?org_id=${encodeURIComponent(orgId)}`, {
      headers: { Accept: "application/json" },
    })
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (alive && Array.isArray(data)) setItems(data);
      })
      .catch(() => {
        /* ignore */
      });

    const onUpdate = (payload) => {
      if (!payload || payload.org_id !== orgId) return;
      setItems(Array.isArray(payload.items) ? payload.items : []);
    };
    socket.on("stoplist:update", onUpdate);

    return () => {
      alive = false;
      socket.off("stoplist:update", onUpdate);
    };
  }, [orgId]);

  if (!items.length) return null;

  const names = items
    .map((i) => i.name || i.product_id)
    .filter(Boolean)
    .join(", ");

  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        background: "#b00020",
        color: "#fff",
        padding: "10px 16px",
        fontWeight: 700,
        fontSize: "1.05rem",
        borderRadius: 8,
        marginBottom: 12,
        boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
      }}
    >
      ⛔ Стоп-лист: {names}
    </div>
  );
}
