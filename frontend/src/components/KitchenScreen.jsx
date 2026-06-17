import { useEffect, useRef } from "react";
import ScreenHeader from "./ScreenHeader.jsx";
import KitchenItemRow from "./KitchenItemRow.jsx";
import { useOrders } from "../lib/orders.js";
import { getOperatorName } from "../lib/auth.js";
import {
  useTick,
  formatElapsed,
  parseServerTime,
  overdueLevel,
  orderUrgency,
} from "../lib/time.js";
import { playNewOrder, playOverdue } from "../lib/sound.js";

// How long a freshly arrived card keeps its attention flash (ms).
const NEW_FLASH_MS = 6000;
// Re-sound the overdue alarm at most this often per item while still overdue.
const OVERDUE_REALARM_MS = 60000;

/**
 * Shared kitchen station screen.
 * @param {string} title    header title
 * @param {string} accent   accent color
 * @param {string[]} zones  zones to join
 * @param {string[]} stations  item stations to show
 */
export default function KitchenScreen({ title, accent, zones, stations }) {
  const name = getOperatorName();
  useTick(1000); // keep running stopwatches fresh

  const { orders, loading, error, connected } = useOrders(zones);

  // Orders that have at least one item for these stations and aren't delivered.
  const visibleOrders = orders
    .filter((o) => !o.delivered)
    .map((o) => ({
      ...o,
      _items: (o.items || []).filter((it) => stations.includes(it.station)),
    }))
    .filter((o) => o._items.length > 0)
    // hide orders where every relevant item is ready (kitchen done)
    .filter((o) => o._items.some((it) => !it.ready))
    // Smart queue: most urgent first (overdue, then burning, then by age).
    // useTick re-renders every second, so this re-sorts as time passes.
    .sort((a, b) => orderUrgency(b, stations) - orderUrgency(a, stations));

  // --- new-order detection -------------------------------------------------
  // Track which order ids we've already seen so we only flash/beep genuinely
  // new ones. The first GET should not beep for the whole existing list.
  const seenIds = useRef(null); // null === not initialised yet
  const newIds = useRef(new Set()); // ids currently flashing
  const flashTimers = useRef(new Map()); // id -> timeout handle

  useEffect(() => {
    if (loading) return;
    const currentIds = visibleOrders.map((o) => o.id);

    if (seenIds.current === null) {
      // First settled load — seed the baseline, no alerts.
      seenIds.current = new Set(currentIds);
      return;
    }

    let gotNew = false;
    for (const id of currentIds) {
      if (!seenIds.current.has(id)) {
        gotNew = true;
        seenIds.current.add(id);
        newIds.current.add(id);
        // Clear the flash after a few seconds. useTick re-renders every 1s,
        // so the class drops off within ~1s of the flag clearing.
        const handle = setTimeout(() => {
          newIds.current.delete(id);
          flashTimers.current.delete(id);
        }, NEW_FLASH_MS);
        flashTimers.current.set(id, handle);
      }
    }
    if (gotNew) playNewOrder();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, visibleOrders.map((o) => o.id).join(",")]);

  useEffect(() => {
    return () => {
      for (const h of flashTimers.current.values()) clearTimeout(h);
      flashTimers.current.clear();
    };
  }, []);

  // --- overdue alarm -------------------------------------------------------
  // Remember the last time we sounded the alarm for each item id so we don't
  // beep on every 1s tick, but re-alarm roughly once a minute while overdue.
  const overdueAlarmed = useRef(new Map()); // itemId -> last alarm ms

  useEffect(() => {
    const now = Date.now();
    let shouldAlarm = false;
    const liveOverdue = new Set();

    for (const o of visibleOrders) {
      for (const it of o._items) {
        if (!it.accepted || it.ready) continue;
        const acceptedAt = parseServerTime(it.accepted_at) ?? now;
        if (overdueLevel(now - acceptedAt) !== "overdue") continue;
        liveOverdue.add(it.id);
        const last = overdueAlarmed.current.get(it.id);
        if (last == null || now - last >= OVERDUE_REALARM_MS) {
          shouldAlarm = true;
          overdueAlarmed.current.set(it.id, now);
        }
      }
    }

    // Drop items that are no longer overdue so they re-alarm if it recurs.
    for (const id of overdueAlarmed.current.keys()) {
      if (!liveOverdue.has(id)) overdueAlarmed.current.delete(id);
    }

    if (shouldAlarm) playOverdue();
  });

  return (
    <div className="screen" style={{ "--accent": accent }}>
      <ScreenHeader title={title} accent={accent} connected={connected} />

      <main className="screen-body">
        {loading && <p className="muted">Загрузка заказов…</p>}
        {error && <p className="err-banner">Ошибка связи: {error}</p>}
        {!loading && !error && visibleOrders.length === 0 && (
          <p className="empty">Нет активных позиций 🎉</p>
        )}

        <div className="cards-grid">
          {visibleOrders.map((o) => {
            const createdAt = parseServerTime(o.created_at);
            const sinceOrder =
              createdAt != null ? formatElapsed(Date.now() - createdAt) : null;

            const isNew = newIds.current.has(o.id);
            // card-level overdue: any accepted, not-ready item past 10 min
            const cardOverdue = o._items.some((it) => {
              if (!it.accepted || it.ready) return false;
              const a = parseServerTime(it.accepted_at) ?? Date.now();
              return overdueLevel(Date.now() - a) === "overdue";
            });
            // card-level warn: any accepted, not-ready item in the 5–10 min band
            const cardWarn =
              !cardOverdue &&
              o._items.some((it) => {
                if (!it.accepted || it.ready) return false;
                const a = parseServerTime(it.accepted_at);
                if (a == null) return false;
                return overdueLevel(Date.now() - a) === "warn";
              });

            const urgencyBadge = cardOverdue
              ? { text: "🔴 просрочка", bg: "#c0392b" }
              : cardWarn
              ? { text: "🟡 горит", bg: "#b8860b" }
              : null;

            const cls = [
              "order-card",
              isNew ? "order-card--new" : "",
              cardOverdue ? "order-card--overdue" : "",
            ]
              .filter(Boolean)
              .join(" ");

            return (
              <section key={o.id} className={cls}>
                <div className="order-card__head">
                  <span className="order-num">№ {o.number}</span>
                  <div className="order-card__meta-head">
                    {urgencyBadge && (
                      <span
                        title="Срочность заказа"
                        style={{
                          fontSize: "0.8em",
                          fontWeight: 700,
                          color: "#fff",
                          background: urgencyBadge.bg,
                          borderRadius: "6px",
                          padding: "2px 8px",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {urgencyBadge.text}
                      </span>
                    )}
                    {sinceOrder && (
                      <span className="order-since" title="Время с момента заказа">
                        ⏱ {sinceOrder}
                      </span>
                    )}
                    {o.customer && <span className="order-cust">{o.customer}</span>}
                  </div>
                </div>
                <div className="order-card__items">
                  {o._items.map((it) => (
                    <KitchenItemRow key={it.id} item={it} name={name} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </main>
    </div>
  );
}
