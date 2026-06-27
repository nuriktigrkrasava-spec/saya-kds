import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BASE } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import { zoneColor, zoneIcon, zoneLabel } from "../lib/zones.js";
import "../admin.css";

// Кухонные зоны (rolls=Япония по просьбе заказчика).
const ZLABEL = { pizza: "Пицца", rolls: "Япония", hot: "Горячее", other: "Прочее" };
const zlabel = (z) => ZLABEL[z] || zoneLabel(z);

// Этапы «пути заказа» (от кухни до клиента).
const STAGE_META = {
  queue: { label: "Очередь", color: "#94a3b8" },
  cooking: { label: "Готовка", color: "#e8552d" },
  assembly: { label: "Сборка", color: "#0ea5e9" },
  pickup_wait: { label: "Ожидание", color: "#f59e0b" },
  delivery: { label: "Доставка", color: "#7c3aed" },
};
const JOURNEY_KEYS = ["queue", "cooking", "assembly", "pickup_wait", "delivery"];

function sec2min(s) {
  return s != null ? Math.round(s / 60) : "—";
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
  const [stages, setStages] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const org = getOrgId();
        const [a, s] = await Promise.all([
          fetch(`${BASE}/admin/analytics?org_id=${org}`, { headers: { Accept: "application/json" } }),
          fetch(`${BASE}/admin/stage-stats?org_id=${org}`, { headers: { Accept: "application/json" } }),
        ]);
        if (!a.ok) throw new Error(`HTTP ${a.status} ${a.statusText}`.trim());
        const json = await a.json();
        const stageJson = s.ok ? await s.json() : null;
        if (!cancelled) { setData(json); setStages(stageJson); }
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // Полоса «путь заказа» из stage-stats (за последние 7 дней).
  const journey = useMemo(() => {
    const byKey = Object.fromEntries(
      (stages?.stage_aggregates?.stages || []).map((s) => [s.key, s]));
    const segs = JOURNEY_KEYS.map((k) => ({
      key: k, ...STAGE_META[k],
      avg: byKey[k]?.avg || 0, has: (byKey[k]?.avg || 0) > 0,
    }));
    const total = segs.reduce((sum, x) => sum + x.avg, 0);
    const bd = stages?.bottleneck_distribution || {};
    let neckKey = null, neckV = 0;
    for (const k of JOURNEY_KEYS) if ((bd[k] || 0) > neckV) { neckV = bd[k]; neckKey = k; }
    return { segs, total, hasAny: segs.some((s) => s.has),
             neckLabel: STAGE_META[neckKey]?.label, neckCount: neckV };
  }, [stages]);

  const maxZoneAvg = useMemo(
    () => Math.max(1, ...((data?.stations || []).map((s) => s.avg_prep_seconds || 0))),
    [data]
  );

  return (
    <div className="admin">
      <div className="admin__inner">
        <Link to="/" className="admin__back">← На главную</Link>
        <h1 className="admin__title">Аналитика</h1>

        {loading && <p className="admin__state">Загрузка…</p>}
        {error && !loading && (
          <p className="admin__state admin__state--err">Ошибка: {error}</p>
        )}

        {data && !loading && !error && (
          <>
            {/* верхние показатели */}
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
              <div className="stat-card stat-card--accent">
                <span className="stat-card__label">Среднее время доставки</span>
                <span className="stat-card__value">{fmtSeconds(data.delivery.avg_delivery_seconds)}</span>
                <span className="stat-card__sub">{data.delivery.delivered_count} доставок</span>
              </div>
            </div>

            {/* путь заказа (шкала) */}
            <section className="admin__table-wrap journey-card">
              <h2 className="admin__section-title">Путь заказа · в среднем <small className="admin__hint">за 7 дней</small></h2>
              {!journey.hasAny ? (
                <p className="admin__state">Нет данных за период</p>
              ) : (
                <>
                  <div className="journey__bar">
                    {journey.segs.map((s) => (
                      <div key={s.key}
                           className={"journey__seg" + (s.has ? "" : " journey__seg--empty")}
                           style={{ flexGrow: s.has ? s.avg : 0.18, background: s.has ? s.color : undefined }}
                           title={s.has ? `${s.label}: ${sec2min(s.avg)} мин` : `${s.label}: нет данных`} />
                    ))}
                    <div className="journey__total">
                      <span className="journey__total-n">{sec2min(journey.total)}</span>
                      <span className="journey__total-u">мин до клиента</span>
                    </div>
                  </div>
                  <div className="journey__legend">
                    {journey.segs.map((s) => (
                      <span key={s.key} className={"journey__leg" + (s.has ? "" : " journey__leg--empty")}>
                        <span className="journey__dot" style={{ background: s.has ? s.color : "#cbd5e1" }} />
                        {s.label}<b>{s.has ? `${sec2min(s.avg)}м` : "—"}</b>
                      </span>
                    ))}
                  </div>
                  {journey.neckLabel && (
                    <p className="journey__neck">
                      Чаще всего тормозит: <b>{journey.neckLabel}</b> · {journey.neckCount} зак.
                    </p>
                  )}
                </>
              )}
            </section>

            {/* кухня по зонам — бары */}
            <section className="admin__table-wrap zbars-card">
              <h2 className="admin__section-title">Кухня по зонам · среднее время готовки</h2>
              {(data.stations || []).length === 0 && <p className="admin__state">Нет данных</p>}
              {(data.stations || []).map((st) => (
                <div key={st.station} className="zbar">
                  <div className="zbar__row" style={{ cursor: "default" }}>
                    <span className="zbar__name">
                      <span className="zbar__ic">{zoneIcon(st.station)}</span>{zlabel(st.station)}
                    </span>
                    <span className="zbar__track">
                      <span className="zbar__fill"
                            style={{ width: `${((st.avg_prep_seconds || 0) / maxZoneAvg) * 100}%`, background: zoneColor(st.station) }} />
                    </span>
                    <span className="zbar__val">{sec2min(st.avg_prep_seconds)}<small>мин</small></span>
                    <span className="zbar__cnt">{st.items_ready}/{st.items_total}</span>
                  </div>
                </div>
              ))}
            </section>

            {/* сотрудники */}
            <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
              <h2 className="admin__section-title">Сотрудники</h2>
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Имя</th>
                    <th className="num">Готово позиций</th>
                    <th className="num">Ср. время готовки</th>
                  </tr>
                </thead>
                <tbody>
                  {data.employees.length === 0 && (
                    <tr><td colSpan={3} className="admin-table__empty">Нет данных</td></tr>
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
            </section>
          </>
        )}
      </div>
    </div>
  );
}
