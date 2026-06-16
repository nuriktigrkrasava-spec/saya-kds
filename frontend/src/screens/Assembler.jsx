import { useState } from "react";
import ScreenHeader from "../components/ScreenHeader.jsx";
import { useOrders } from "../lib/orders.js";
import { useName } from "../lib/useName.js";
import { api } from "../lib/api.js";
import { zoneColor, zoneLabel } from "../lib/zones.js";

const ACCENT = "#0ea5e9";
const ZONE_ORDER = ["rolls", "pizza", "hot", "other"];

function groupByZone(items) {
  const groups = {};
  for (const it of items || []) {
    const z = it.station || "other";
    (groups[z] = groups[z] || []).push(it);
  }
  return ZONE_ORDER.filter((z) => groups[z]).map((z) => ({ zone: z, items: groups[z] }));
}

function AssemblerCard({ order, name }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const items = order.items || [];
  const allReady = items.length > 0 && items.every((it) => it.ready);
  const allChecked = items.length > 0 && items.every((it) => it.is_checked);
  const canAssemble = allReady && allChecked && !order.assembled;

  async function check(item) {
    setErr(null);
    try {
      await api.checkItem(item.id, name || "—");
    } catch (e) {
      setErr(e.message || String(e));
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

  const groups = groupByZone(items);

  return (
    <section className={`order-card ${order.assembled ? "order-card--done" : ""}`}>
      <div className="order-card__head">
        <span className="order-num">№ {order.number}</span>
        {order.customer && <span className="order-cust">{order.customer}</span>}
      </div>

      {groups.map((g) => (
        <div key={g.zone} className="asm-group">
          <div className="asm-group__title">
            <span className="kitem__dot" style={{ background: zoneColor(g.zone) }} />
            {zoneLabel(g.zone)}
          </div>
          {g.items.map((it) => (
            <label
              key={it.id}
              className={`asm-item ${it.is_checked ? "asm-item--checked" : ""}`}
            >
              <input
                type="checkbox"
                checked={!!it.is_checked}
                disabled={!it.ready || order.assembled}
                onChange={() => check(it)}
              />
              <span className="asm-item__name">
                {it.name}
                <span className="kitem__amount"> ×{it.amount}</span>
              </span>
              <span className={`ready-flag ${it.ready ? "ready-flag--on" : ""}`}>
                {it.ready ? "✓ готово" : "ждём…"}
              </span>
            </label>
          ))}
        </div>
      ))}

      {err && <div className="kitem__err">{err}</div>}

      {order.assembled ? (
        <div className="badge badge--ready asm-handed">Передан курьеру</div>
      ) : (
        <button
          className="btn btn--big btn--assemble"
          onClick={assemble}
          disabled={!canAssemble || busy}
        >
          {busy ? "…" : "Передать курьеру"}
        </button>
      )}
    </section>
  );
}

export default function Assembler() {
  const [name, setName] = useName();
  const { orders, loading, error, connected } = useOrders(["assembler"]);

  const visible = orders
    .filter((o) => !o.delivered && (o.items || []).length > 0)
    .sort((a, b) => Number(a.assembled) - Number(b.assembled) || (a.number || 0) - (b.number || 0));

  return (
    <div className="screen" style={{ "--accent": ACCENT }}>
      <ScreenHeader
        title="Сборщик"
        accent={ACCENT}
        name={name}
        onNameChange={setName}
        connected={connected}
      />
      <main className="screen-body">
        {loading && <p className="muted">Загрузка заказов…</p>}
        {error && <p className="err-banner">Ошибка связи: {error}</p>}
        {!loading && !error && visible.length === 0 && (
          <p className="empty">Нет заказов для сборки 🎉</p>
        )}
        <div className="cards-grid">
          {visible.map((o) => (
            <AssemblerCard key={o.id} order={o} name={name} />
          ))}
        </div>
      </main>
    </div>
  );
}
