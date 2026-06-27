import React, { useEffect, useMemo, useState, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, PieChart, Pie, Cell, Legend,
} from "recharts";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { getOrgId } from "../lib/auth.js";
import { zoneColor, zoneIcon, zoneLabel } from "../lib/zones.js";
import "../admin.css";

function presetRange(days) {
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - (days - 1));
  // Локальная дата (Y-M-D по местному времени, НЕ UTC): «Сегодня» должно означать
  // текущий местный день, а не UTC-день — иначе граница съезжает на смещение часового пояса.
  const iso = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return { from: iso(from), to: iso(to) };
}

function sec2min(s) {
  return s != null ? Math.round(s / 60) : "—";
}

const COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"];

// ── Нагрузка по зонам/часам (heatmap) ───────────────────────────────────────
const KZONE_LABEL = { pizza: "Пицца", rolls: "Япония", hot: "Горячее" };

// Этапы «пути заказа» (от кухни до клиента). iiko_close — внутреннее, не показываем.
const STAGE_META = {
  queue: { label: "Очередь", color: "#94a3b8" },
  cooking: { label: "Готовка", color: "#e8552d" },
  assembly: { label: "Сборка", color: "#0ea5e9" },
  pickup_wait: { label: "Ожидание", color: "#f59e0b" },
  delivery: { label: "Доставка", color: "#7c3aed" },
};
const JOURNEY_KEYS = ["queue", "cooking", "assembly", "pickup_wait", "delivery"];

function hexToRgb(hex) {
  const h = (hex || "#2563eb").replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

// Фон ячейки = цвет зоны с альфой, растущей по нагрузке; текст белеет на насыщенном.
function heatStyle(count, max, hex) {
  if (!count) return { background: "rgba(15,23,42,0.025)" };
  const t = Math.sqrt(count / max); // sqrt вытягивает слабую нагрузку из невидимости
  const a = 0.12 + t * 0.82;
  const { r, g, b } = hexToRgb(hex);
  return { background: `rgba(${r},${g},${b},${a.toFixed(3)})`, color: a > 0.58 ? "#fff" : "#0f172a" };
}

// Окно часов: от первого до последнего часа С ДАННЫМИ. Пустые будущие и ночные
// часы не рисуем — поэтому в отчёте за сегодня не видно ещё не наступивших часов.
function activeHourWindow(zones, cells, keyOf) {
  let lo = null, hi = null;
  for (const z of zones) {
    const row = cells[keyOf(z)] || {};
    for (const h of Object.keys(row)) {
      if (!row[h]?.count) continue;
      const n = Number(h);
      lo = lo == null ? n : Math.min(lo, n);
      hi = hi == null ? n : Math.max(hi, n);
    }
  }
  if (lo == null) return [];
  return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
}

const keyOfZone = (z) => (typeof z === "string" ? z : z.id == null ? "none" : String(z.id));

function HoursHeatTable({ title, data, rowLabel, rowColor, rowIcon }) {
  if (!data) return null;
  const { zones, cells, totals_by_zone } = data;
  const hours = activeHourWindow(zones, cells, keyOfZone);
  const nowHour = new Date().getHours();

  if (!hours.length) {
    return (
      <section className="card heatx-card">
        <div className="heatx-head"><h2>{title}</h2></div>
        <p className="heatx-empty">Пока нет данных за выбранный период.</p>
      </section>
    );
  }

  const max = Math.max(1, ...zones.flatMap((z) =>
    hours.map((h) => cells[keyOfZone(z)]?.[h]?.count || 0)));

  return (
    <section className="card heatx-card">
      <div className="heatx-head">
        <h2>{title}</h2>
        <span className="heatx-legend">объём&nbsp;·&nbsp;ср.&nbsp;мин</span>
      </div>
      <div className="heatx-scroll">
        <table className="heatx">
          <thead>
            <tr>
              <th className="heatx__corner" />
              {hours.map((h) => (
                <th key={h} className={"heatx__h" + (h === nowHour ? " heatx__h--now" : "")}>
                  {String(h).padStart(2, "0")}
                </th>
              ))}
              <th className="heatx__sum-h">Σ</th>
            </tr>
          </thead>
          <tbody>
            {zones.map((z) => {
              const k = keyOfZone(z);
              const tot = totals_by_zone[k] || { count: 0, avg_sec: null };
              const color = rowColor(z);
              const row = cells[k] || {};
              let peak = -1, peakV = 0;
              for (const h of hours) {
                const c = row[h]?.count || 0;
                if (c > peakV) { peakV = c; peak = h; }
              }
              return (
                <tr key={k}>
                  <th className="heatx__zone" scope="row">
                    <span className="heatx__zone-in">
                      <span className="heatx__dot" style={{ background: color }} />
                      {rowIcon ? <span className="heatx__icon">{rowIcon(z)}</span> : null}
                      <span className="heatx__name">{rowLabel(z)}</span>
                    </span>
                  </th>
                  {hours.map((h) => {
                    const c = row[h];
                    const cls = "heatx__cell" + (h === peak && peakV > 0 ? " heatx__cell--peak" : "");
                    const hh = String(h).padStart(2, "0");
                    return (
                      <td key={h} className={cls} style={heatStyle(c?.count, max, color)}
                          title={c ? `${hh}:00 — ${c.count} шт · ${sec2min(c.avg_sec)} мин` : `${hh}:00 — нет`}>
                        {c ? (
                          <><span className="heatx__n">{c.count}</span><span className="heatx__m">{sec2min(c.avg_sec)}</span></>
                        ) : null}
                      </td>
                    );
                  })}
                  <td className="heatx__sum">
                    <span className="heatx__n">{tot.count}</span>
                    <span className="heatx__m">{sec2min(tot.avg_sec)}м</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

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
  // Какой пресет периода активен (1|7|30), либо null если выбран свой диапазон —
  // нужно, чтобы подсветить нажатую кнопку.
  const [preset, setPreset] = useState(7);
  const applyPreset = (days) => { setPreset(days); setRange(presetRange(days)); };
  const setCustomDate = (field) => (e) => { setPreset(null); setRange((r) => ({ ...r, [field]: e.target.value })); };

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

  // zonal load heatmaps
  const [zoneLoadData, setZoneLoadData] = useState(null);
  const [courierLoadData, setCourierLoadData] = useState(null);

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

  // ── загрузка нагрузки по зонам/районам ──────────────────────────────────
  useEffect(() => {
    if (!orgId) return;
    api.zoneLoad(orgId, range.from, range.to).then(setZoneLoadData).catch(() => setZoneLoadData(null));
    api.courierZoneLoad(orgId, range.from, range.to).then(setCourierLoadData).catch(() => setCourierLoadData(null));
  }, [orgId, range.from, range.to]);

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

  // Полоса «путь заказа»: ВСЕ этапы (от кухни до клиента); этап без данных
  // показываем бледным «—», чтобы доставка/ожидание были видны, даже если в
  // выбранном периоде по ним ещё нет завершённых заказов.
  const journey = useMemo(() => {
    const byKey = Object.fromEntries(
      (stats?.stage_aggregates?.stages || []).map((s) => [s.key, s]));
    const segs = JOURNEY_KEYS.map((k) => ({
      key: k, ...STAGE_META[k],
      avg: byKey[k]?.avg || 0, p95: byKey[k]?.p95 || 0,
      has: (byKey[k]?.avg || 0) > 0,
    }));
    const total = segs.reduce((sum, x) => sum + x.avg, 0);
    const bd = stats?.bottleneck_distribution || {};
    let neckKey = null, neckV = 0;
    for (const k of JOURNEY_KEYS) if ((bd[k] || 0) > neckV) { neckV = bd[k]; neckKey = k; }
    return { segs, total, hasAny: segs.some((s) => s.has),
             neckKey, neckLabel: STAGE_META[neckKey]?.label, neckCount: neckV };
  }, [stats]);

  return (
    <div className="admin">
      <div className="admin__inner">
        <Link to="/" className="admin__back">
          ← На главную
        </Link>

        <h1 className="admin__title">Аналитика</h1>

        {/* ── Пикер периода ── */}
        <div className="analytics__presets">
          <button className={"seg-btn" + (preset === 1 ? " seg-btn--on" : "")}
                  aria-pressed={preset === 1} onClick={() => applyPreset(1)}>Сегодня</button>
          <button className={"seg-btn" + (preset === 7 ? " seg-btn--on" : "")}
                  aria-pressed={preset === 7} onClick={() => applyPreset(7)}>7 дней</button>
          <button className={"seg-btn" + (preset === 30 ? " seg-btn--on" : "")}
                  aria-pressed={preset === 30} onClick={() => applyPreset(30)}>30 дней</button>
          <span className="analytics__presets-sep" />
          <input type="date" className="analytics__date" value={range.from} onChange={setCustomDate("from")} />
          <span className="analytics__dash">—</span>
          <input type="date" className="analytics__date" value={range.to} onChange={setCustomDate("to")} />
        </div>

        {statsLoading && <p className="admin__state">Загрузка…</p>}
        {statsError && <p className="admin__state admin__state--err">Ошибка: {statsError}</p>}

        {/* ── Нагрузка по зонам/районам (heatmap) ── */}
        <HoursHeatTable
          title="Кухня · нагрузка по часам"
          data={zoneLoadData}
          rowLabel={(z) => KZONE_LABEL[z] || z}
          rowColor={(z) => zoneColor(z)}
          rowIcon={(z) => zoneIcon(z)} />
        <HoursHeatTable
          title="Курьер · нагрузка по районам"
          data={courierLoadData}
          rowLabel={(z) => z.name}
          rowColor={(z) => z.color || "#2563eb"}
          rowIcon={() => "📍"} />
        <div className="heatx-actions">
          <Link to="/delivery-zones" className="heatx-link">Настроить зоны города →</Link>
        </div>

        {/* ── Путь заказа (полоса этапов) ── */}
        <section className="admin__table-wrap journey-card">
          <h2 className="admin__section-title">Путь заказа · в среднем</h2>
          {!journey.hasAny && !statsLoading ? (
            <p className="admin__state">Нет данных за период</p>
          ) : (
            <>
              <div className="journey__bar">
                {journey.segs.map((s) => (
                  <div key={s.key}
                       className={"journey__seg" + (s.has ? "" : " journey__seg--empty")}
                       style={{ flexGrow: s.has ? s.avg : 0.18, background: s.has ? s.color : undefined }}
                       title={s.has ? `${s.label}: ${sec2min(s.avg)} мин (p95 ${sec2min(s.p95)})`
                                    : `${s.label}: нет данных за период`} />
                ))}
                <div className="journey__total">
                  <span className="journey__total-n">{sec2min(journey.total)}</span>
                  <span className="journey__total-u">мин до клиента</span>
                </div>
              </div>
              <div className="journey__legend">
                {journey.segs.map((s) => (
                  <span key={s.key}
                        className={"journey__leg" + (s.key === journey.neckKey ? " journey__leg--neck" : "")
                          + (s.has ? "" : " journey__leg--empty")}>
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

        {/* ── Кухня по зонам ── */}
        <section className="admin__table-wrap zbars-card">
          <h2 className="admin__section-title">Кухня по зонам · среднее время готовки</h2>
          {zonesLoading && <p className="admin__state">Загрузка…</p>}
          {!zonesLoading && (zones?.zones || []).length === 0 && (
            <p className="admin__state">Нет данных за период</p>
          )}
          {(() => {
            const zs = zones?.zones || [];
            const maxAvg = Math.max(1, ...zs.map((z) => z.avg || 0));
            return zs.map((z) => {
              const open = expandedZone === z.zone;
              const col = zoneColor(z.zone);
              const hasEmp = z.employees?.length > 0;
              return (
                <div key={z.zone} className="zbar">
                  <button type="button" className="zbar__row"
                          onClick={() => hasEmp && setExpandedZone(open ? null : z.zone)}>
                    <span className="zbar__name">
                      <span className="zbar__ic">{zoneIcon(z.zone)}</span>
                      {KZONE_LABEL[z.zone] || zoneLabel(z.zone)}
                    </span>
                    <span className="zbar__track">
                      <span className="zbar__fill" style={{ width: `${(z.avg / maxAvg) * 100}%`, background: col }} />
                    </span>
                    <span className="zbar__val">{sec2min(z.avg)}<small>мин</small></span>
                    <span className="zbar__cnt">{z.count} поз.</span>
                    <span className="zbar__chev">{hasEmp ? (open ? "▲" : "▼") : ""}</span>
                  </button>
                  {open && z.employees?.map((emp) => (
                    <div key={emp.name} className="zbar__emp">
                      <span>{emp.name}</span>
                      <span>{sec2min(emp.avg)} мин · {emp.count} поз.</span>
                    </div>
                  ))}
                </div>
              );
            });
          })()}
        </section>

        {/* ── Аномалии ── */}
        <section className="admin__table-wrap" style={{ padding: 24, marginBottom: 24 }}>
          <h2 className="admin__section-title">Аномалии (сегодня)</h2>
          {anomaliesLoading && <p className="admin__state">Загрузка…</p>}
          {anomalies && (
            <div className="anom-grid">
              <div className={"anom" + (anomalies.fast?.length ? " anom--bad" : " anom--ok")}>
                <div className="anom__head">
                  <span className="anom__title">Слишком быстро <small>&lt; 1 мин</small></span>
                  <span className="anom__badge">{anomalies.fast?.length || 0}</span>
                </div>
                {anomalies.fast?.length === 0 ? (
                  <p className="anom__ok">Всё чисто</p>
                ) : (
                  <ul className="anom__list">
                    {anomalies.fast.map((a, i) => (
                      <li key={i}>
                        <span>#{a.order_number} · {a.item_name}</span>
                        <span className="anom__num">{a.prep_seconds}с · {a.name}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className={"anom" + (anomalies.reverts?.length ? " anom--bad" : " anom--ok")}>
                <div className="anom__head">
                  <span className="anom__title">Частые возвраты <small>≥ 2</small></span>
                  <span className="anom__badge">{anomalies.reverts?.length || 0}</span>
                </div>
                {anomalies.reverts?.length === 0 ? (
                  <p className="anom__ok">Всё чисто</p>
                ) : (
                  <ul className="anom__list">
                    {anomalies.reverts.map((r, i) => (
                      <li key={i}>
                        <span>#{r.order_number}</span>
                        <span className="anom__num">{r.count} возвр.</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
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
