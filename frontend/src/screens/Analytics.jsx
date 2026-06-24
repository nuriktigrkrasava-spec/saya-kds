import { useEffect, useMemo, useState, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, PieChart, Pie, Cell, Legend,
} from "recharts";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { getOrgId } from "../lib/auth.js";
import "../admin.css";

function presetRange(days) {
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - (days - 1));
  const iso = (d) => d.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(to) };
}

function sec2min(s) {
  return s != null ? Math.round(s / 60) : "—";
}

const COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"];

const EVENT_TYPE_LABELS = {
  item_accept: "Принял",
  item_ready: "Готово",
  item_check: "Проверил",
  item_revert: "Возврат",
  order_assemble: "Собрал",
  take: "Забрал",
  delivered: "Доставлен",
  iiko_write: "Запись iiko",
};

const JOURNAL_TYPES = [
  "item_accept", "item_ready", "item_check", "item_revert",
  "order_assemble", "take", "delivered",
];

// ── Модальное окно тайм-лайна заказа ───────────────────────────────────────
function TimelineModal({ orgId, orderId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!orderId) return;
    setLoading(true);
    setError(null);
    api
      .orderTimeline(orgId, orderId)
      .then(setData)
      .catch((e) => setError(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [orgId, orderId]);

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)",
        zIndex: 999, display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "#fff", borderRadius: 16, padding: 28, maxWidth: 680,
          width: "100%", maxHeight: "90vh", overflowY: "auto",
          boxShadow: "0 8px 40px rgba(0,0,0,0.18)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>
            Тайм-лайн заказа {data ? `#${data.order.number}` : ""}
          </h2>
          <button
            onClick={onClose}
            style={{ border: "none", background: "none", fontSize: 22, cursor: "pointer", color: "#6b7280" }}
          >
            ✕
          </button>
        </div>

        {loading && <p className="admin__state">Загрузка…</p>}
        {error && <p className="admin__state admin__state--err">Ошибка: {error}</p>}

        {data && (
          <>
            {/* Этапы */}
            <h3 style={{ fontSize: 15, fontWeight: 600, color: "#374151", marginBottom: 8 }}>Этапы</h3>
            <table className="admin-table" style={{ marginBottom: 20 }}>
              <thead>
                <tr>
                  <th>Этап</th>
                  <th className="num">Минуты</th>
                  <th>Бутылочное горло</th>
                </tr>
              </thead>
              <tbody>
                {data.stages.map((s) => (
                  <tr key={s.key} style={s.bottleneck ? { background: "#fef9c3" } : {}}>
                    <td>{s.label}</td>
                    <td className="num">{s.seconds != null ? `${sec2min(s.seconds)} мин` : "—"}</td>
                    <td style={{ color: s.bottleneck ? "#b45309" : "#9ca3af", fontWeight: s.bottleneck ? 600 : 400 }}>
                      {s.bottleneck ? "да" : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* События */}
            <h3 style={{ fontSize: 15, fontWeight: 600, color: "#374151", marginBottom: 8 }}>События</h3>
            <div style={{ borderLeft: "3px solid #e5e7eb", paddingLeft: 16 }}>
              {data.events.map((ev, i) => (
                <div key={i} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 12, color: "#9ca3af" }}>
                    {ev.at ? new Date(ev.at).toLocaleString("ru-RU") : ""}
                  </div>
                  <div style={{ fontWeight: 600, color: "#1f2937" }}>
                    {EVENT_TYPE_LABELS[ev.type] || ev.type}
                    {ev.actor_name ? <span style={{ fontWeight: 400, color: "#6b7280" }}> — {ev.actor_name}</span> : ""}
                  </div>
                  {ev.payload?.name && (
                    <div style={{ fontSize: 13, color: "#6b7280" }}>{ev.payload.name}</div>
                  )}
                  {ev.payload?.item_name && (
                    <div style={{ fontSize: 13, color: "#6b7280" }}>{ev.payload.item_name}</div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Основной экран ───────────────────────────────────────────────────────────
export default function Analytics() {
  const orgId = getOrgId();
  const [range, setRange] = useState(presetRange(7));

  // stageStats (уже был)
  const [stats, setStats] = useState(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState(null);

  // kitchenZones
  const [zones, setZones] = useState(null);
  const [zonesLoading, setZonesLoading] = useState(false);
  const [expandedZone, setExpandedZone] = useState(null);

  // anomalies
  const [anomalies, setAnomalies] = useState(null);
  const [anomaliesLoading, setAnomaliesLoading] = useState(false);

  // journal
  const [journal, setJournal] = useState(null);
  const [journalLoading, setJournalLoading] = useState(false);
  const [jType, setJType] = useState("");
  const [jActor, setJActor] = useState("");
  const [jNumber, setJNumber] = useState("");

  // drill-down timeline
  const [timelineOrderId, setTimelineOrderId] = useState(null);

  // ── загрузка stageStats ─────────────────────────────────────────────────
  useEffect(() => {
    if (!orgId) return;
    setStatsLoading(true);
    setStatsError(null);
    api
      .stageStats(orgId, range.from, range.to)
      .then(setStats)
      .catch((e) => { setStatsError(e.message || String(e)); setStats(null); })
      .finally(() => setStatsLoading(false));
  }, [orgId, range.from, range.to]);

  // ── загрузка kitchenZones ───────────────────────────────────────────────
  useEffect(() => {
    if (!orgId) return;
    setZonesLoading(true);
    api
      .kitchenZones(orgId, range.from, range.to)
      .then(setZones)
      .catch(() => setZones(null))
      .finally(() => setZonesLoading(false));
  }, [orgId, range.from, range.to]);

  // ── загрузка anomalies ──────────────────────────────────────────────────
  useEffect(() => {
    if (!orgId) return;
    setAnomaliesLoading(true);
    api
      .anomalies(orgId)
      .then(setAnomalies)
      .catch(() => setAnomalies(null))
      .finally(() => setAnomaliesLoading(false));
  }, [orgId]);

  // ── загрузка journal ────────────────────────────────────────────────────
  const loadJournal = useCallback(() => {
    if (!orgId) return;
    setJournalLoading(true);
    const params = {};
    if (jType) params.type = jType;
    if (jActor) params.actor = jActor;
    if (jNumber) params.order_number = jNumber;
    api
      .journal(orgId, range.from, range.to, params)
      .then(setJournal)
      .catch(() => setJournal(null))
      .finally(() => setJournalLoading(false));
  }, [orgId, range.from, range.to, jType, jActor, jNumber]);

  useEffect(() => { loadJournal(); }, [loadJournal]);

  // ── производные данные ──────────────────────────────────────────────────
  const stageData = useMemo(
    () =>
      (stats?.stage_aggregates?.stages || []).map((s) => ({
        label: s.label,
        avg: sec2min(s.avg),
        p95: sec2min(s.p95),
      })),
    [stats]
  );

  const bottleneckData = useMemo(
    () =>
      Object.entries(stats?.bottleneck_distribution || {})
        .map(([key, value]) => ({ name: key, value }))
        .filter((d) => d.value > 0),
    [stats]
  );

  const slaRate = stats ? Math.round((stats.sla?.rate || 0) * 100) : null;

  const pickupLag = stats?.pickup_lag || null;

  const zonesBarData = useMemo(
    () =>
      (zones?.zones || []).map((z) => ({
        zone: z.zone,
        avg: sec2min(z.avg),
        median: sec2min(z.median),
        count: z.count,
      })),
    [zones]
  );

  return (
    <div className="admin">
      <div className="admin__inner">
        <Link to="/" className="admin__back">
          ← На главную
        </Link>

        <h1 className="admin__title">Аналитика</h1>

        {/* ── Пикер периода ── */}
        <div className="analytics__presets" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 24 }}>
          <button className="btn btn--sm" onClick={() => setRange(presetRange(1))}>Сегодня</button>
          <button className="btn btn--sm" onClick={() => setRange(presetRange(7))}>7 дней</button>
          <button className="btn btn--sm" onClick={() => setRange(presetRange(30))}>30 дней</button>
          <input
            type="date"
            value={range.from}
            onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
            style={{ padding: "4px 8px", borderRadius: 6, border: "1px solid #d1d5db" }}
          />
          <span style={{ alignSelf: "center", color: "#6b7280" }}>—</span>
          <input
            type="date"
            value={range.to}
            onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
            style={{ padding: "4px 8px", borderRadius: 6, border: "1px solid #d1d5db" }}
          />
        </div>

        {statsLoading && <p className="admin__state">Загрузка…</p>}
        {statsError && <p className="admin__state admin__state--err">Ошибка: {statsError}</p>}

        {/* ── Где застревают ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">Где застревают (минуты)</h2>
          {stageData.length === 0 && !statsLoading ? (
            <p className="admin__state">Нет данных за период</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={stageData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <XAxis dataKey="label" tick={{ fontSize: 13 }} />
                <YAxis unit=" мин" tick={{ fontSize: 12 }} />
                <Tooltip formatter={(v, name) => [`${v} мин`, name]} />
                <Bar dataKey="avg" name="среднее" fill="#2563eb" radius={[4, 4, 0, 0]} />
                <Bar dataKey="p95" name="p95" fill="#f59e0b" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>

        {/* ── SLA-просрочки ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">SLA-просрочки</h2>
          {stats ? (
            <>
              <p style={{
                fontSize: 28, fontWeight: 700, margin: "0 0 16px",
                color: (stats.sla?.rate || 0) > 0.2 ? "#dc2626" : "#16a34a",
              }}>
                {slaRate}% просрочено
                <span style={{ fontSize: 15, fontWeight: 400, color: "#6b7280", marginLeft: 10 }}>
                  ({stats.sla?.breached || 0}/{stats.sla?.total || 0})
                </span>
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={stats.sla?.by_day || []} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Line type="monotone" dataKey="breached" name="просрочки"
                    stroke="#dc2626" strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </ResponsiveContainer>
            </>
          ) : (
            !statsLoading && <p className="admin__state">Нет данных за период</p>
          )}
        </section>

        {/* ── Лаг готов→выехал ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">Лаг готов → выехал</h2>
          {pickupLag ? (
            <>
              <div className="admin__cards" style={{ marginBottom: 16 }}>
                {[
                  { label: "Среднее", value: `${sec2min(pickupLag.avg)} мин` },
                  { label: "Медиана", value: `${sec2min(pickupLag.median)} мин` },
                  { label: "p95", value: `${sec2min(pickupLag.p95)} мин` },
                ].map((c) => (
                  <div key={c.label} className="stat-card">
                    <div className="stat-card__label">{c.label}</div>
                    <div className="stat-card__value" style={{ fontSize: 26 }}>{c.value}</div>
                  </div>
                ))}
              </div>
              {pickupLag.worst && pickupLag.worst.length > 0 && (
                <>
                  <p style={{ fontWeight: 600, color: "#374151", marginBottom: 6 }}>Антирекорды</p>
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>Заказ</th>
                        <th className="num">Лаг (мин)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pickupLag.worst.map((w, i) => (
                        <tr key={i}>
                          <td>#{w.number}</td>
                          <td className="num">{sec2min(w.seconds)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          ) : (
            !statsLoading && <p className="admin__state">Нет данных за период</p>
          )}
        </section>

        {/* ── Бутылочные горла (Pie) ── */}
        {bottleneckData.length > 0 && (
          <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
            <h2 className="admin__section-title">Распределение бутылочных горл</h2>
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie data={bottleneckData} dataKey="value" nameKey="name" outerRadius={90} label>
                  {bottleneckData.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </section>
        )}

        {/* ── Кухня по зонам ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">Кухня по зонам</h2>
          {zonesLoading && <p className="admin__state">Загрузка…</p>}
          {!zonesLoading && zonesBarData.length === 0 && (
            <p className="admin__state">Нет данных за период</p>
          )}
          {zonesBarData.length > 0 && (
            <>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={zonesBarData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                  <XAxis dataKey="zone" tick={{ fontSize: 13 }} />
                  <YAxis unit=" мин" tick={{ fontSize: 12 }} />
                  <Tooltip formatter={(v, name) => [`${v} мин`, name]} />
                  <Bar dataKey="avg" name="среднее" fill="#2563eb" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="median" name="медиана" fill="#16a34a" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>

              <table className="admin-table" style={{ marginTop: 16 }}>
                <thead>
                  <tr>
                    <th>Зона</th>
                    <th className="num">Позиций</th>
                    <th className="num">Среднее (мин)</th>
                    <th className="num">Медиана (мин)</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {(zones?.zones || []).map((z) => (
                    <>
                      <tr
                        key={z.zone}
                        style={{ cursor: z.employees?.length ? "pointer" : "default" }}
                        onClick={() => setExpandedZone(expandedZone === z.zone ? null : z.zone)}
                      >
                        <td style={{ fontWeight: 600 }}>{z.zone}</td>
                        <td className="num">{z.count}</td>
                        <td className="num">{sec2min(z.avg)}</td>
                        <td className="num">{sec2min(z.median)}</td>
                        <td style={{ color: "#2563eb", fontSize: 13 }}>
                          {z.employees?.length > 0 ? (expandedZone === z.zone ? "▲" : "▼") : ""}
                        </td>
                      </tr>
                      {expandedZone === z.zone && z.employees && z.employees.map((emp) => (
                        <tr key={emp.name} style={{ background: "#f9fafb" }}>
                          <td style={{ paddingLeft: 32, color: "#6b7280" }}>{emp.name}</td>
                          <td className="num" style={{ color: "#6b7280" }}>{emp.count}</td>
                          <td className="num" style={{ color: "#6b7280" }}>{sec2min(emp.avg)}</td>
                          <td className="num"></td>
                          <td></td>
                        </tr>
                      ))}
                    </>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>

        {/* ── Аномалии ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">Аномалии (сегодня)</h2>
          {anomaliesLoading && <p className="admin__state">Загрузка…</p>}
          {anomalies && (
            <>
              <p style={{ fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                Слишком быстро (&lt;1 мин)
              </p>
              {anomalies.fast?.length === 0 ? (
                <p style={{ color: "#9ca3af", marginBottom: 16 }}>Нет</p>
              ) : (
                <table className="admin-table" style={{ marginBottom: 20 }}>
                  <thead>
                    <tr>
                      <th>Заказ</th>
                      <th>Сотрудник</th>
                      <th>Позиция</th>
                      <th className="num">Сек</th>
                      <th>Принял</th>
                    </tr>
                  </thead>
                  <tbody>
                    {anomalies.fast?.map((a, i) => (
                      <tr key={i} style={{ background: "#fef2f2" }}>
                        <td>#{a.order_number}</td>
                        <td>{a.name}</td>
                        <td>{a.item_name}</td>
                        <td className="num" style={{ color: "#dc2626", fontWeight: 700 }}>{a.prep_seconds}</td>
                        <td style={{ fontSize: 12, color: "#9ca3af" }}>
                          {a.accepted_at ? new Date(a.accepted_at).toLocaleTimeString("ru-RU") : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <p style={{ fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                Частые возвраты (≥2)
              </p>
              {anomalies.reverts?.length === 0 ? (
                <p style={{ color: "#9ca3af" }}>Нет</p>
              ) : (
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Заказ</th>
                      <th className="num">Возвратов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {anomalies.reverts?.map((r, i) => (
                      <tr key={i}>
                        <td>#{r.order_number}</td>
                        <td className="num" style={{ color: "#f59e0b", fontWeight: 700 }}>{r.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </section>

        {/* ── Журнал событий ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">Журнал событий</h2>

          {/* Фильтры */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
            <select
              value={jType}
              onChange={(e) => setJType(e.target.value)}
              style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #d1d5db", fontSize: 14 }}
            >
              <option value="">Все типы</option>
              {JOURNAL_TYPES.map((t) => (
                <option key={t} value={t}>{EVENT_TYPE_LABELS[t] || t}</option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Сотрудник"
              value={jActor}
              onChange={(e) => setJActor(e.target.value)}
              style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #d1d5db", fontSize: 14 }}
            />
            <input
              type="text"
              placeholder="Номер заказа"
              value={jNumber}
              onChange={(e) => setJNumber(e.target.value)}
              style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #d1d5db", fontSize: 14 }}
            />
          </div>

          {journalLoading && <p className="admin__state">Загрузка…</p>}
          {journal && (
            <>
              <p style={{ fontSize: 13, color: "#9ca3af", marginBottom: 8 }}>
                Всего: {journal.total}
              </p>
              {journal.rows?.length === 0 ? (
                <p className="admin__state">Нет событий</p>
              ) : (
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Время</th>
                      <th>Заказ</th>
                      <th>Тип</th>
                      <th>Сотрудник</th>
                      <th>Позиция</th>
                    </tr>
                  </thead>
                  <tbody>
                    {journal.rows?.map((row, i) => (
                      <tr
                        key={i}
                        style={{ cursor: row.order_id ? "pointer" : "default" }}
                        onClick={() => row.order_id && setTimelineOrderId(row.order_id)}
                        title={row.order_id ? "Открыть тайм-лайн заказа" : ""}
                      >
                        <td style={{ fontSize: 12, color: "#6b7280", whiteSpace: "nowrap" }}>
                          {row.at ? new Date(row.at).toLocaleString("ru-RU") : ""}
                        </td>
                        <td>
                          {row.order_id ? (
                            <span style={{ color: "#2563eb", fontWeight: 600 }}>#{row.order_number}</span>
                          ) : (
                            `#${row.order_number}`
                          )}
                        </td>
                        <td>{EVENT_TYPE_LABELS[row.type] || row.type}</td>
                        <td>{row.actor_name}</td>
                        <td style={{ color: "#6b7280" }}>{row.item_name}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </section>
      </div>

      {/* ── Drill-down модалка ── */}
      {timelineOrderId && (
        <TimelineModal
          orgId={orgId}
          orderId={timelineOrderId}
          onClose={() => setTimelineOrderId(null)}
        />
      )}
    </div>
  );
}
