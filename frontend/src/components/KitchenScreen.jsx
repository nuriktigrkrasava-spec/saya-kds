import { useEffect, useRef } from "react";
import ScreenHeader from "./ScreenHeader.jsx";
import KitchenItemRow from "./KitchenItemRow.jsx";
import StopListBanner from "./StopListBanner.jsx";
import PrepHint from "./PrepHint.jsx";
import AudioGuard from "./AudioGuard.jsx";
import Icon from "./Icon.jsx";
import PreorderShelf from "./PreorderShelf.jsx";
import { useOrders } from "../lib/orders.js";
import { getOperatorName } from "../lib/auth.js";
import { channelBadge } from "../lib/orderType.js";
import { splitPreorders, isPreorder, dueLabel } from "../lib/preorder.js";
import {
  useTick,
  formatElapsed,
  parseServerTime,
  overdueLevel,
  orderUrgency,
} from "../lib/time.js";
import { playNewOrder, playHandoff } from "../lib/sound.js";

// How long a freshly arrived card keeps its attention flash (ms).
const NEW_FLASH_MS = 6000;
// How often to repeat the "передавай заказ" reminder while any order is stuck.
// Plays, then stays quiet for this interval, then plays again — an insistent
// nag, not a continuous blare.
const HANDOFF_REMIND_MS = 10000;

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
  const relevant = orders
    .filter((o) => !o.delivered)
    .map((o) => ({
      ...o,
      _items: (o.items || []).filter((it) => stations.includes(it.station)),
    }))
    .filter((o) => o._items.length > 0)
    // hide orders where every relevant item is ready (kitchen done)
    .filter((o) => o._items.some((it) => !it.ready));

  // Предзаказ на вечер не должен лезть в очередь: он висит на «полке» внизу и
  // входит в работу сам за час до срока (preorder_held снимает бэкенд).
  const { live, parked } = splitPreorders(relevant);

  // Smart queue: most urgent first (overdue, then burning, then by age).
  // useTick re-renders every second, so this re-sorts as time passes.
  const visibleOrders = live.sort(
    (a, b) => orderUrgency(b, stations) - orderUrgency(a, stations)
  );

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

  // --- handoff reminder ("передавай заказ") --------------------------------
  // While ANY visible order has an item that is accepted-but-not-ready AND
  // overdue (the cook is sitting on it instead of passing it to assembly),
  // nag with playHandoff() every ~10s: play, ~10s pause, play again, until the
  // order is made ready. This folds in (replaces) the old 60s overdue re-alarm
  // so there's a single coherent reminder rather than two competing sounds.
  // Driven by useTick(1000), which re-renders this component every second.
  const lastHandoffMs = useRef(0); // last time we played; 0 === not nagging

  useEffect(() => {
    const now = Date.now();

    // Заказ «застрял», если есть неготовая позиция и он просрочен по времени появления.
    const stuck = visibleOrders.some((o) => {
      const c = parseServerTime(o.created_at);
      if (c == null) return false;
      const hasUnready = o._items.some((it) => !it.ready);
      return hasUnready && overdueLevel(now - c) === "overdue";
    });

    if (!stuck) {
      // Nothing stuck: stop nagging and reset so the next stuck order beeps
      // immediately rather than waiting out a stale interval.
      lastHandoffMs.current = 0;
      return;
    }

    // Stuck. Play on first crossing (lastHandoffMs === 0) and then at most
    // once per HANDOFF_REMIND_MS — never more often than every ~10s.
    if (now - lastHandoffMs.current >= HANDOFF_REMIND_MS) {
      lastHandoffMs.current = now;
      playHandoff();
    }
  });

  return (
    <div className="screen" style={{ "--accent": accent }}>
      <AudioGuard />
      <ScreenHeader title={title} accent={accent} connected={connected}>
        <span className="screen-header__count" title="Заказов в работе">
          {visibleOrders.length}
        </span>
      </ScreenHeader>

      <main className="screen-body">
        <StopListBanner />
        <PrepHint stations={stations} />
        {loading && <p className="muted">Загрузка заказов…</p>}
        {error && <p className="err-banner">Ошибка связи: {error}</p>}
        {!loading && !error && visibleOrders.length === 0 && (
          <p className="empty">Активных позиций нет</p>
        )}

        <div className="cards-grid">
          {visibleOrders.map((o) => {
            const createdAt = parseServerTime(o.created_at);
            const sinceOrder =
              createdAt != null ? formatElapsed(Date.now() - createdAt) : null;

            const isNew = newIds.current.has(o.id);
            const hasUnready = o._items.some((it) => !it.ready);
            const orderLevel =
              createdAt != null && hasUnready
                ? overdueLevel(Date.now() - createdAt)
                : "ok";
            const cardOverdue = orderLevel === "overdue";
            const cardWarn = orderLevel === "warn";
            const badge = channelBadge(o);

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
                  <span className="order-num">{o.number}</span>
                  <div className="order-card__meta-head">
                    {sinceOrder && (
                      <span
                        className={`order-since order-since--${orderLevel}`}
                        title="Время с момента заказа"
                      >
                        <Icon name="clock" size={16} />
                        {sinceOrder}
                      </span>
                    )}
                    {cardOverdue && (
                      <span className="chip chip--danger">
                        <Icon name="alert" size={16} />
                        просрочка
                      </span>
                    )}
                    {cardWarn && !cardOverdue && (
                      <span className="chip chip--warn">
                        <Icon name="fire" size={16} />
                        горит
                      </span>
                    )}
                  </div>
                </div>

                <div className="order-card__tags">
                  {badge && (
                    <span className="chip chip--neutral" title="Тип заказа">
                      <Icon name={badge.icon} size={16} />
                      {badge.text}
                    </span>
                  )}
                  {isPreorder(o) && dueLabel(o) && (
                    <span className="chip chip--preorder" title="Заказ ко времени">
                      <Icon name="preorder" size={16} />
                      Предзаказ {dueLabel(o)}
                    </span>
                  )}
                  {o.customer && <span className="order-cust">{o.customer}</span>}
                </div>

                {o.comment && (
                  <div className="order-note">
                    <Icon name="note" size={18} />
                    <span>{o.comment}</span>
                  </div>
                )}
                <div className="order-card__items">
                  {o._items.map((it) => (
                    <KitchenItemRow
                      key={it.id}
                      item={it}
                      name={name}
                      orderCreatedAt={o.created_at}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>

        <PreorderShelf orders={parked} itemsOf={(o) => o._items} />
      </main>
    </div>
  );
}
