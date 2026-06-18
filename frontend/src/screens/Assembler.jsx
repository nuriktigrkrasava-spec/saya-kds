import { useEffect, useRef, useState } from "react";
import ScreenHeader from "../components/ScreenHeader.jsx";
import CourierMiniMap from "../components/CourierMiniMap.jsx";
import StopListBanner from "../components/StopListBanner.jsx";
import { useOrders } from "../lib/orders.js";
import { getOperatorName, getSession } from "../lib/auth.js";
import { api } from "../lib/api.js";
import { zoneColor, zoneLabel } from "../lib/zones.js";
import { orderChannel, channelBadge, completeLabel } from "../lib/orderType.js";
import {
  useTick,
  formatElapsed,
  overdueLevel,
  cookProgress,
  parseServerTime,
  TARGET_MS,
} from "../lib/time.js";
import { playNewOrder, playOverdue } from "../lib/sound.js";

const ACCENT = "#0ea5e9";
const ZONE_ORDER = ["rolls", "pizza", "hot", "other"];

const NEW_FLASH_MS = 6000;
const OVERDUE_REALARM_MS = 60000;

function groupByZone(items) {
  const groups = {};
  for (const it of items || []) {
    const z = it.station || "other";
    (groups[z] = groups[z] || []).push(it);
  }
  return ZONE_ORDER.filter((z) => groups[z]).map((z) => ({ zone: z, items: groups[z] }));
}

function AssemblerCard({ order, name, isNew, couriers, couriersReady }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [courierCode, setCourierCode] = useState("");

  // Канал заказа решает, нужна ли курьерская панель.
  // Доставка (needsCourier) -> выбор курьера; зал/вынос/самовывоз/агрегатор -> «Выдан».
  const channel = orderChannel(order);

  const items = order.items || [];
  // Блюда зоны «прочее» (напитки/подарки/соусы) не готовятся на кухне.
  const needsReady = (it) => it.station !== "other";
  // Галочек больше нет: заказ можно отдать курьеру, когда все позиции готовы.
  const allReady = items.length > 0 && items.every((it) => it.ready || !needsReady(it));
  const canAssemble = allReady && !order.assembled;

  const courierList = couriers || [];
  const hasCouriers = courierList.length > 0;
  const activeCouriers = courierList.filter((c) => c.active);
  const activeCount = activeCouriers.length;
  // Поток не блокируется: если на смене никого нет — откат на обычную сборку.
  const hasActiveCouriers = activeCount > 0;
  // Name of the assigned courier, if any (from server payload).
  const assignedName =
    order.courier_name ||
    order.assigned_courier_name ||
    order.assigned?.courier_name ||
    (order.assigned?.name && order.assigned?.role === "courier"
      ? order.assigned.name
      : "") ||
    "";

  // Overall order timer from created_at -> progress toward the target window.
  const createdAt = parseServerTime(order.created_at);
  const elapsed = createdAt != null ? Date.now() - createdAt : 0;
  const level = order.assembled ? "ok" : overdueLevel(elapsed);
  const fill = cookProgress(elapsed);
  const timerText = createdAt != null ? formatElapsed(elapsed) : null;

  const cardOverdue = !order.assembled && level === "overdue";

  async function revert(item, e) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    setErr(null);
    try {
      await api.revertItem(item.id, name || "—");
    } catch (err) {
      setErr(err.message || String(err));
    }
  }

  async function assemble() {
    setBusy(true);
    setErr(null);
    try {
      await api.assembleOrder(order.id, name || "—");
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  // Некурьерские заказы (зал/вынос/самовывоз/агрегатор) закрываются здесь же:
  // помечаем собранным и сразу выданным — заказ уходит из списка сборщика.
  async function handout() {
    setBusy(true);
    setErr(null);
    try {
      await api.assembleOrder(order.id, name || "—");
      await api.deliverOrder(order.id, name || "—");
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function assign() {
    const courier = courierList.find((c) => c.code === courierCode);
    if (!courier) {
      setErr("Выберите курьера");
      return;
    }
    if (!courier.active) {
      setErr("Курьер не на смене");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.assignCourier(order.id, courier.code, courier.name, name || "—");
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const groups = groupByZone(items);

  const cls = [
    "order-card",
    order.assembled ? "order-card--done" : "",
    isNew ? "order-card--new" : "",
    cardOverdue ? "order-card--overdue" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section className={cls}>
      <div className="order-card__head">
        <span className="order-num">№ {order.number}</span>
        <div className="order-card__meta-head">
          {channelBadge(order) && (
            <span
              title="Тип заказа"
              style={{
                fontSize: "0.8em",
                fontWeight: 700,
                color: "#fff",
                background: "#2c3e50",
                borderRadius: "6px",
                padding: "2px 8px",
                whiteSpace: "nowrap",
              }}
            >
              {channelBadge(order)}
            </span>
          )}
          {timerText && (
            <span
              className={`order-since order-since--${level}`}
              title="Время с момента заказа"
            >
              ⏱ {timerText}
            </span>
          )}
          {order.customer && <span className="order-cust">{order.customer}</span>}
        </div>
      </div>

      {order.comment && <div className="order-note">📝 {order.comment}</div>}

      {!order.assembled && timerText && (
        <div
          className={`asmbar asmbar--${level}`}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fill * 100)}
          title={`Сборка идёт ${timerText}`}
        >
          <span className="asmbar__fill" style={{ width: `${fill * 100}%` }} />
        </div>
      )}

      {groups.map((g) => {
        const zReady = g.items.every((it) => it.ready);
        return (
          <div key={g.zone} className="asm-group">
            <div className="asm-group__title">
              <span className="kitem__dot" style={{ background: zoneColor(g.zone) }} />
              {zoneLabel(g.zone)}
              <span className={`asm-group__state ${zReady ? "asm-group__state--ready" : ""}`}>
                {zReady ? "✓ готово" : "ждём…"}
              </span>
            </div>
            {g.items.map((it) => (
              <div key={it.id} className="asm-item">
                <span className="asm-item__name">
                  {it.name}
                  <span className="kitem__amount"> ×{it.amount}</span>
                  {it.comment && <span className="kitem__note">📝 {it.comment}</span>}
                </span>
                <span className={`ready-flag ${(it.ready || !needsReady(it)) ? "ready-flag--on" : ""}`}>
                  {it.ready ? "✓ готово" : (!needsReady(it) ? "—" : "ждём…")}
                </span>
                {it.ready && needsReady(it) && !order.assembled && (
                  <button
                    type="button"
                    className="asm-revert"
                    title="Вернуть повару (не готово)"
                    onClick={(e) => revert(it, e)}
                  >
                    ↩ Вернуть
                  </button>
                )}
              </div>
            ))}
          </div>
        );
      })}

      {err && <div className="kitem__err">{err}</div>}

      {order.assembled ? (
        <div className="badge badge--ready asm-handed">
          {channel.needsCourier
            ? assignedName
              ? `Назначен: ${assignedName}`
              : "Передан курьеру"
            : completeLabel(channel)}
        </div>
      ) : !channel.needsCourier ? (
        // Зал / вынос / самовывоз / агрегатор: курьер не нужен — закрываем «Выдан».
        <button
          className="btn btn--big btn--assemble"
          onClick={handout}
          disabled={!canAssemble || busy}
        >
          {busy ? "…" : completeLabel(channel)}
        </button>
      ) : !couriersReady ? (
        <button className="btn btn--big btn--assemble" disabled>
          Загрузка курьеров…
        </button>
      ) : hasActiveCouriers ? (
        <div className="asm-courier">
          <div
            className="asm-courier__hint"
            style={{ fontWeight: 700, color: "#16a34a" }}
          >
            На смене: {activeCount}
          </div>
          <select
            className="asm-courier__select"
            value={courierCode}
            onChange={(e) => setCourierCode(e.target.value)}
            disabled={!canAssemble || busy}
          >
            <option value="">Выберите курьера…</option>
            {courierList.map((c) => (
              <option key={c.code} value={c.code} disabled={!c.active}>
                {c.active
                  ? `${c.name} · на смене`
                  : `${c.name} · не на смене`}
              </option>
            ))}
          </select>
          <button
            className="btn btn--big btn--assemble"
            onClick={assign}
            disabled={!canAssemble || busy || !courierCode}
          >
            {busy ? "…" : "Назначить курьера"}
          </button>
        </div>
      ) : (
        <>
          <div className="asm-courier__hint">
            {hasCouriers
              ? "Нет курьеров на смене"
              : "Нет курьеров в филиале"}
          </div>
          <button
            className="btn btn--big btn--assemble"
            onClick={assemble}
            disabled={!canAssemble || busy}
          >
            {busy ? "…" : "Передать курьеру"}
          </button>
        </>
      )}
    </section>
  );
}

export default function Assembler() {
  const name = getOperatorName();
  useTick(1000); // keep overall order timers / progress bars fresh

  const { orders, loading, error, connected } = useOrders(["assembler"]);

  // Load the branch couriers once per screen.
  const [couriers, setCouriers] = useState([]);
  const [couriersReady, setCouriersReady] = useState(false);

  useEffect(() => {
    const branchCode = getSession()?.branch_code;
    if (!branchCode) {
      setCouriersReady(true);
      return;
    }
    let cancelled = false;
    api
      .branchCouriers(branchCode)
      .then((list) => {
        if (!cancelled) setCouriers(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        if (!cancelled) setCouriers([]);
      })
      .finally(() => {
        if (!cancelled) setCouriersReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = orders
    .filter((o) => !o.delivered && (o.items || []).length > 0)
    .sort(
      (a, b) =>
        Number(a.assembled) - Number(b.assembled) || (a.number || 0) - (b.number || 0)
    );

  // --- new-order detection (same approach as KitchenScreen) ----------------
  const seenIds = useRef(null);
  const newIds = useRef(new Set());
  const flashTimers = useRef(new Map());

  // ids that are not yet assembled (those we want to alert/flash on arrival)
  const activeIds = visible.filter((o) => !o.assembled).map((o) => o.id);

  useEffect(() => {
    if (loading) return;
    if (seenIds.current === null) {
      seenIds.current = new Set(activeIds);
      return;
    }
    let gotNew = false;
    for (const id of activeIds) {
      if (!seenIds.current.has(id)) {
        gotNew = true;
        seenIds.current.add(id);
        newIds.current.add(id);
        const handle = setTimeout(() => {
          newIds.current.delete(id);
          flashTimers.current.delete(id);
        }, NEW_FLASH_MS);
        flashTimers.current.set(id, handle);
      }
    }
    if (gotNew) playNewOrder();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, activeIds.join(",")]);

  useEffect(() => {
    return () => {
      for (const h of flashTimers.current.values()) clearTimeout(h);
      flashTimers.current.clear();
    };
  }, []);

  // --- overdue alarm: order in progress past the target window -------------
  const overdueAlarmed = useRef(new Map());

  useEffect(() => {
    const now = Date.now();
    let shouldAlarm = false;
    const liveOverdue = new Set();

    for (const o of visible) {
      if (o.assembled) continue;
      const createdAt = parseServerTime(o.created_at);
      if (createdAt == null) continue;
      if (overdueLevel(now - createdAt) !== "overdue") continue;
      liveOverdue.add(o.id);
      const last = overdueAlarmed.current.get(o.id);
      if (last == null || now - last >= OVERDUE_REALARM_MS) {
        shouldAlarm = true;
        overdueAlarmed.current.set(o.id, now);
      }
    }
    for (const id of overdueAlarmed.current.keys()) {
      if (!liveOverdue.has(id)) overdueAlarmed.current.delete(id);
    }
    if (shouldAlarm) playOverdue();
  });

  return (
    <div className="screen" style={{ "--accent": ACCENT }}>
      <ScreenHeader title="Сборщик" accent={ACCENT} connected={connected} />
      <main className="screen-body">
        <StopListBanner />
        <CourierMiniMap />
        {loading && <p className="muted">Загрузка заказов…</p>}
        {error && <p className="err-banner">Ошибка связи: {error}</p>}
        {!loading && !error && visible.length === 0 && (
          <p className="empty">Нет заказов для сборки 🎉</p>
        )}
        <div className="cards-grid">
          {visible.map((o) => (
            <AssemblerCard
              key={o.id}
              order={o}
              name={name}
              isNew={newIds.current.has(o.id)}
              couriers={couriers}
              couriersReady={couriersReady}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
