import { useState } from "react";
import ScreenHeader from "../components/ScreenHeader.jsx";
import { useOrders } from "../lib/orders.js";
import { getOperatorName } from "../lib/auth.js";
import { api } from "../lib/api.js";

const ACCENT = "#7c3aed";

function route2gisWeb(order) {
  if (order.lon == null || order.lat == null) return null;
  return `https://2gis.ru/routeSearch/rsType/car/to/${order.lon},${order.lat}`;
}

function route2gisApp(order) {
  if (order.lon == null || order.lat == null) return null;
  return `dgis://2gis.ru/routeSearch/rsType/car/to/${order.lon},${order.lat}`;
}

function OrderRow({ order, name, taken }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function call(fn) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const webLink = route2gisWeb(order);
  const appLink = route2gisApp(order);

  return (
    <section className="courier-card">
      <div className="courier-card__head">
        <span className="order-num">{order.number}</span>
      </div>
      <div className="courier-card__addr">{order.address || "Адрес не указан"}</div>
      <div className="courier-card__meta">
        {order.customer && <span>{order.customer}</span>}
        {order.phone && (
          <a className="phone-link" href={`tel:${order.phone}`}>
            {order.phone}
          </a>
        )}
      </div>

      {err && <div className="kitem__err">{err}</div>}

      {!taken ? (
        <button
          className="btn btn--big btn--take"
          onClick={() => call(() => api.takeOrder(order.id, name || "—"))}
          disabled={busy}
        >
          Взять
        </button>
      ) : (
        <div className="courier-card__actions">
          {webLink && (
            <button
              className="btn btn--big btn--route"
              onClick={() => window.open(webLink, "_blank")}
            >
              Построить маршрут
            </button>
          )}
          {appLink && (
            <button
              className="btn btn--ghost btn--route-app"
              onClick={() => window.open(appLink, "_self")}
            >
              Открыть в приложении
            </button>
          )}
          <button
            className="btn btn--big btn--delivered"
            onClick={() => call(() => api.deliverOrder(order.id, name || "—"))}
            disabled={busy}
          >
            Доставлено
          </button>
        </div>
      )}
    </section>
  );
}

export default function Courier() {
  const name = getOperatorName();
  const { orders, loading, error, connected } = useOrders(["courier"]);

  const available = orders
    .filter((o) => o.assembled && !o.delivered && !o.on_way)
    .sort((a, b) => (a.number || 0) - (b.number || 0));

  const taken = orders
    .filter((o) => o.assembled && !o.delivered && o.on_way)
    .sort((a, b) => (a.number || 0) - (b.number || 0));

  return (
    <div className="screen screen--phone" style={{ "--accent": ACCENT }}>
      <ScreenHeader title="Курьер" accent={ACCENT} connected={connected} />
      <main className="screen-body courier-body">
        {loading && <p className="muted">Загрузка заказов…</p>}
        {error && <p className="err-banner">Ошибка связи: {error}</p>}

        {taken.length > 0 && (
          <>
            <h2 className="section-title">Мои заказы</h2>
            {taken.length > 1 && (
              <p className="route-note">
                Бесплатная ссылка 2ГИС строит маршрут только до одной точки за
                раз — открывайте заказы по порядку.
              </p>
            )}
            {taken.map((o) => (
              <OrderRow key={o.id} order={o} name={name} taken />
            ))}
          </>
        )}

        <h2 className="section-title">Готовы к доставке</h2>
        {!loading && available.length === 0 && (
          <p className="empty">Свободных заказов нет</p>
        )}
        {available.map((o) => (
          <OrderRow key={o.id} order={o} name={name} taken={false} />
        ))}
      </main>
    </div>
  );
}
