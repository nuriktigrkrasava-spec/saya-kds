import ScreenHeader from "./ScreenHeader.jsx";
import KitchenItemRow from "./KitchenItemRow.jsx";
import { useOrders } from "../lib/orders.js";
import { useName } from "../lib/useName.js";
import { useTick } from "../lib/time.js";

/**
 * Shared kitchen station screen.
 * @param {string} title    header title
 * @param {string} accent   accent color
 * @param {string[]} zones  zones to join
 * @param {string[]} stations  item stations to show
 */
export default function KitchenScreen({ title, accent, zones, stations }) {
  const [name, setName] = useName();
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
    .sort((a, b) => (a.number || 0) - (b.number || 0));

  return (
    <div className="screen" style={{ "--accent": accent }}>
      <ScreenHeader
        title={title}
        accent={accent}
        name={name}
        onNameChange={setName}
        connected={connected}
      />

      <main className="screen-body">
        {loading && <p className="muted">Загрузка заказов…</p>}
        {error && <p className="err-banner">Ошибка связи: {error}</p>}
        {!loading && !error && visibleOrders.length === 0 && (
          <p className="empty">Нет активных позиций 🎉</p>
        )}

        <div className="cards-grid">
          {visibleOrders.map((o) => (
            <section key={o.id} className="order-card">
              <div className="order-card__head">
                <span className="order-num">№ {o.number}</span>
                {o.customer && <span className="order-cust">{o.customer}</span>}
              </div>
              <div className="order-card__items">
                {o._items.map((it) => (
                  <KitchenItemRow key={it.id} item={it} name={name} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </main>
    </div>
  );
}
