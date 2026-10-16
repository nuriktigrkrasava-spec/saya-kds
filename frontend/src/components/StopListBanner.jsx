import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { BASE, authenticatedFetch } from "../lib/api.js";
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

    authenticatedFetch(`${BASE}/stoplist?org_id=${encodeURIComponent(orgId)}`, {
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
    <div className="stoplist-banner" role="status">
      <Icon name="ban" size={20} />
      <span className="stoplist-banner__label">Стоп-лист</span>
      <span className="stoplist-banner__names">{names}</span>
    </div>
  );
}
