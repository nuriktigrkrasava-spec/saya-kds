import Icon from "./Icon.jsx";
import { dueLabel, minutesUntilDue } from "../lib/preorder.js";

/**
 * «Полка» предзаказов: заказы ко времени, которые ещё не пора готовить.
 *
 * Заказчик 09.08.2026: предзаказ на 16:00 попадал в очередь в момент приёма и
 * повар начинал его сразу. Здесь такие заказы просто висят — без таймеров,
 * кнопок и звука, — а за час до срока бэкенд снимает preorder_held и карточка
 * сама переезжает наверх, в рабочую очередь.
 *
 * @param {object[]} orders   придержанные заказы (preorder_held)
 * @param {(o:object)=>object[]} itemsOf  какие позиции показывать в карточке
 */
export default function PreorderShelf({ orders, itemsOf = (o) => o.items || [] }) {
  if (!orders || orders.length === 0) return null;

  return (
    <section className="preshelf" aria-label="Предзаказы">
      <div className="preshelf__head">
        <Icon name="preorder" size={20} />
        <h2 className="preshelf__title">Предзаказы</h2>
        <span className="preshelf__count">{orders.length}</span>
        <span className="preshelf__hint">войдут в работу автоматически</span>
      </div>

      <div className="preshelf__grid">
        {orders.map((o) => {
          const items = itemsOf(o);
          const mins = minutesUntilDue(o);
          const soon = mins <= 90;
          return (
            <article key={o.id} className="precard">
              <div className="precard__head">
                <span className="precard__num">{o.number}</span>
                <span className={`chip chip--preorder${soon ? " chip--preorder-soon" : ""}`}>
                  <Icon name="preorder" size={16} />
                  {dueLabel(o) || "время не указано"}
                </span>
              </div>
              {o.customer && <div className="precard__cust">{o.customer}</div>}
              <ul className="precard__items">
                {items.map((it) => (
                  <li key={it.id}>
                    {it.name}
                    <span className="precard__amount">×{it.amount}</span>
                  </li>
                ))}
              </ul>
              {o.comment && (
                <div className="precard__note">
                  <Icon name="note" size={16} />
                  <span>{o.comment}</span>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
