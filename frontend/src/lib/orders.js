import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { joinZones, socket } from "./socket.js";

// Терминальные статусы iiko: заказ закрыт/удалён/отменён — убираем с экрана.
// iiko шлёт по одному заказу несколько вебхуков (New->Bill->Closed), поэтому
// без явного удаления upsert возвращал закрытую карточку обратно.
const TERMINAL_STATUSES = new Set(["Closed", "Deleted", "Cancelled"]);

/**
 * useOrders — loads orders once on mount, subscribes to "order:update",
 * upserts by id, and keeps them in state. Joins the given zones (and re-joins
 * on reconnect).
 *
 * @param {string[]} zones  zones to join over socket.io
 * @param {(order)=>boolean} [filterFn] optional filter applied to the result
 */
export function useOrders(zones, filterFn) {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [connected, setConnected] = useState(socket.connected);
  const zonesKey = (zones || []).join(",");

  useEffect(() => {
    let alive = true;

    setLoading(true);
    api
      .getOrders()
      .then((data) => {
        if (!alive) return;
        setOrders(Array.isArray(data) ? data : []);
        setError(null);
      })
      .catch((e) => {
        if (alive) setError(e.message || String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    const onUpdate = (payload) => {
      if (!payload || payload.id == null) return;
      setOrders((prev) => {
        const idx = prev.findIndex((o) => o.id === payload.id);
        // Заказ закрыт/удалён в iiko — убрать с экрана, не воскрешать. iiko шлёт
        // несколько вебхуков на заказ (New->Bill->Closed); без этого upsert
        // возвращал закрытую карточку обратно.
        if (TERMINAL_STATUSES.has(payload.status)) {
          return idx === -1 ? prev : prev.filter((o) => o.id !== payload.id);
        }
        if (idx === -1) return [...prev, payload];
        const next = prev.slice();
        next[idx] = payload;
        return next;
      });
    };

    const onConnect = () => setConnected(true);
    const onDisconnect = () => setConnected(false);

    socket.on("order:update", onUpdate);
    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);

    const leave = joinZones(zones || []);

    return () => {
      alive = false;
      socket.off("order:update", onUpdate);
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      leave();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zonesKey]);

  const filterRef = useRef(filterFn);
  filterRef.current = filterFn;

  const list = filterRef.current ? orders.filter(filterRef.current) : orders;

  return { orders: list, loading, error, connected };
}
