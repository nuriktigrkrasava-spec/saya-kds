import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BASE } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import { ZONE_COLORS, ZONE_LABELS, ZONE_ICONS } from "../lib/zones.js";
import "../dashboard.css";

const ZONE_ORDER = ["rolls", "pizza", "hot", "other"];
const REFRESH_MS = 10000;

function fmtSeconds(seconds) {
  if (seconds == null) return "—";
  const total = Math.max(0, Math.round(seconds));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
  return res.json();
}

export default function Dashboard() {
  const [dashboard, setDashboard] = useState(null);
  const [leaderboard, setLeaderboard] = useState(null);
  const [forecast, setForecast] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const org = getOrgId();
      try {
        const [d, l, f] = await Promise.all([
          getJSON(`/admin/dashboard?org_id=${org}`),
          getJSON(`/admin/leaderboard?org_id=${org}`),
          getJSON(`/admin/forecast?org_id=${org}`),
        ]);
        if (cancelled) return;
        setDashboard(d);
        setLeaderboard(l);
        setForecast(f);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  function exportCsv() {
    window.open(`${BASE}/admin/report.csv?org_id=${getOrgId()}`, "_blank");
  }

  const tiles = dashboard
    ? [
        { label: "Активных заказов", value: dashboard.active_orders },
        { label: "Доставлено сегодня", value: dashboard.delivered_today },
        { label: "Ср. готовка", value: fmtSeconds(dashboard.avg_prep_today_seconds) },
        { label: "Ср. доставка", value: fmtSeconds(dashboard.avg_delivery_today_seconds) },
        { label: "Курьеров на смене", value: dashboard.on_shift_couriers },
      ]
    : [];

  // Для графика спроса берём максимум, чтобы масштабировать ширину полос.
  const forecastRows = (forecast || []).filter((h) => h.count > 0);
  const maxCount = forecastRows.reduce((m, h) => Math.max(m, h.count), 0) || 1;

  return (
    <div className="dash">
      <div className="dash__inner">
        <Link to="/" className="dash__back">
          ← На главную
        </Link>
        <div className="dash__head">
          <h1 className="dash__title">Сводка (сейчас)</h1>
          <button type="button" className="dash__csv" onClick={exportCsv}>
            Выгрузить CSV
          </button>
        </div>

        {loading && <p className="dash__state">Загрузка…</p>}
        {error && (
          <p className="dash__state dash__state--err">Ошибка: {error}</p>
        )}

        {dashboard && (
          <div className="dash__tiles">
            {tiles.map((t) => (
              <div className="dash-tile" key={t.label}>
                <span className="dash-tile__value">{t.value}</span>
                <span className="dash-tile__label">{t.label}</span>
              </div>
            ))}
          </div>
        )}

        {dashboard && (
          <section className="dash-card">
            <h2 className="dash-card__title">По зонам сейчас</h2>
            <div className="dash-zones">
              {ZONE_ORDER.map((z) => (
                <div
                  className="dash-zone"
                  key={z}
                  style={{ "--zone": ZONE_COLORS[z] }}
                >
                  <span className="dash-zone__icon" aria-hidden>
                    {ZONE_ICONS[z]}
                  </span>
                  <span className="dash-zone__count">
                    {dashboard.by_zone?.[z] ?? 0}
                  </span>
                  <span className="dash-zone__label">{ZONE_LABELS[z]}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {leaderboard && (
          <section className="dash-card">
            <h2 className="dash-card__title">Рейтинг поваров (сегодня)</h2>
            {leaderboard.length === 0 ? (
              <p className="dash__state">Сегодня данных нет</p>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Имя</th>
                    <th className="num">Готово</th>
                    <th className="num">Ср. время</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.map((row, i) => (
                    <tr key={`${row.name}-${i}`}>
                      <td>{row.name}</td>
                      <td className="num">{row.items_done}</td>
                      <td className="num">{fmtSeconds(row.avg_prep_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}

        {forecast && (
          <section className="dash-card">
            <h2 className="dash-card__title">Спрос по часам</h2>
            {forecastRows.length === 0 ? (
              <p className="dash__state">Данных нет</p>
            ) : (
              <div className="dash-bars">
                {forecastRows.map((h) => (
                  <div className="dash-bar" key={h.hour}>
                    <span className="dash-bar__hour">
                      {String(h.hour).padStart(2, "0")}:00
                    </span>
                    <span className="dash-bar__track">
                      <span
                        className="dash-bar__fill"
                        style={{ width: `${(h.count / maxCount) * 100}%` }}
                      />
                    </span>
                    <span className="dash-bar__count">{h.count}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
