import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BASE, authenticatedFetch } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import "../history.css";

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

export default function EmployeeHistory() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await authenticatedFetch(
          `${BASE}/admin/employee-history?org_id=${getOrgId()}`,
          { headers: { Accept: "application/json" } },
        );
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

  const totalAnomalies = (data || []).reduce(
    (sum, e) => sum + (e.suspicious_count || 0),
    0,
  );

  return (
    <div className="history">
      <div className="history__inner">
        <Link to="/" className="history__back">
          ← На главную
        </Link>
        <h1 className="history__title">История сотрудников (сегодня)</h1>

        {loading && <p className="history__state">Загрузка…</p>}
        {error && !loading && (
          <p className="history__state history__state--err">Ошибка: {error}</p>
        )}

        {data && !loading && !error && (
          <>
            <div
              className={`history__summary${
                totalAnomalies > 0 ? " history__summary--alert" : ""
              }`}
            >
              {totalAnomalies > 0 && <span aria-hidden>⚠</span>}
              Аномалий: {totalAnomalies}
            </div>

            {data.length === 0 && (
              <p className="history__state">Сегодня данных нет</p>
            )}

            {data.map((emp) => (
              <section className="emp-card" key={emp.name}>
                <header className="emp-card__head">
                  <h2 className="emp-card__name">{emp.name}</h2>
                  <div className="emp-card__meta">
                    <span>
                      Позиций: <b>{emp.count}</b>
                    </span>
                    <span>
                      Ср. готовка: <b>{fmtSeconds(emp.avg_prep_seconds)}</b>
                    </span>
                    {emp.suspicious_count > 0 && (
                      <span className="emp-card__warn">
                        ⚠ Подозрительных: {emp.suspicious_count}
                      </span>
                    )}
                  </div>
                </header>

                <div className="history__table-wrap">
                  <table className="history-table">
                    <thead>
                      <tr>
                        <th>Заказ №</th>
                        <th>Блюдо</th>
                        <th>Зона</th>
                        <th className="num">Время готовки</th>
                      </tr>
                    </thead>
                    <tbody>
                      {emp.items.map((it, i) => (
                        <tr
                          key={`${it.order_number}-${it.item_name}-${i}`}
                          className={it.suspicious ? "row--suspicious" : ""}
                          title={
                            it.suspicious ? "Подозрительно быстро" : undefined
                          }
                        >
                          <td>{it.order_number}</td>
                          <td>{it.item_name}</td>
                          <td>{stationLabel(it.station)}</td>
                          <td className="num">
                            {it.suspicious && (
                              <span aria-hidden className="row__flag">
                                ⚠{" "}
                              </span>
                            )}
                            {fmtSeconds(it.prep_seconds)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
