import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BASE, ORG_ID } from "../lib/api.js";
import "../admin.css";

const STATION_LABELS = {
  rolls: "Роллы",
  pizza: "Пицца",
  hot: "Горячее",
  other: "Прочее",
};

function stationLabel(station) {
  return STATION_LABELS[station] || STATION_LABELS.other;
}

function fmtSeconds(seconds) {
  if (seconds == null) return "—";
  const total = Math.max(0, Math.round(seconds));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

export default function Admin() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${BASE}/admin/analytics?org_id=${ORG_ID}`, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
        }
        const json = await res.json();
        if (!cancelled) setData(json);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="admin">
      <div className="admin__inner">
        <Link to="/" className="admin__back">
          ← На главную
        </Link>
        <h1 className="admin__title">Аналитика</h1>

        {loading && <p className="admin__state">Загрузка…</p>}
        {error && !loading && (
          <p className="admin__state admin__state--err">Ошибка: {error}</p>
        )}

        {data && !loading && !error && (
          <>
            <div className="admin__cards">
              <div className="stat-card">
                <span className="stat-card__label">Всего заказов</span>
                <span className="stat-card__value">{data.totals.orders}</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__label">Доставлено</span>
                <span className="stat-card__value">{data.totals.delivered}</span>
              </div>
              <div className="stat-card">
                <span className="stat-card__label">В работе</span>
                <span className="stat-card__value">{data.totals.in_progress}</span>
              </div>
            </div>

            <section className="admin__section">
              <h2 className="admin__section-title">Сотрудники</h2>
              <div className="admin__table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Имя</th>
                      <th className="num">Готово позиций</th>
                      <th className="num">Среднее время готовки</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.employees.length === 0 && (
                      <tr>
                        <td colSpan={3} className="admin-table__empty">
                          Нет данных
                        </td>
                      </tr>
                    )}
                    {data.employees.map((e) => (
                      <tr key={e.name}>
                        <td>{e.name}</td>
                        <td className="num">{e.items_done}</td>
                        <td className="num">{fmtSeconds(e.avg_prep_seconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Зоны</h2>
              <div className="admin__table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Зона</th>
                      <th className="num">Позиций</th>
                      <th className="num">Готово</th>
                      <th className="num">Ср. время</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.stations.length === 0 && (
                      <tr>
                        <td colSpan={4} className="admin-table__empty">
                          Нет данных
                        </td>
                      </tr>
                    )}
                    {data.stations.map((st) => (
                      <tr key={st.station}>
                        <td>{stationLabel(st.station)}</td>
                        <td className="num">{st.items_total}</td>
                        <td className="num">{st.items_ready}</td>
                        <td className="num">{fmtSeconds(st.avg_prep_seconds)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Доставка</h2>
              <div className="admin__cards">
                <div className="stat-card">
                  <span className="stat-card__label">Доставлено заказов</span>
                  <span className="stat-card__value">
                    {data.delivery.delivered_count}
                  </span>
                </div>
                <div className="stat-card">
                  <span className="stat-card__label">Среднее время доставки</span>
                  <span className="stat-card__value">
                    {fmtSeconds(data.delivery.avg_delivery_seconds)}
                  </span>
                </div>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
