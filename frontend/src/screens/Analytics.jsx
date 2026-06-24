import { useEffect, useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line,
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

export default function Analytics() {
  const orgId = getOrgId();
  const [range, setRange] = useState(presetRange(7));
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!orgId) return;
    setLoading(true);
    setError(null);
    api
      .stageStats(orgId, range.from, range.to)
      .then((data) => {
        setStats(data);
      })
      .catch((e) => {
        setError(e.message || String(e));
        setStats(null);
      })
      .finally(() => setLoading(false));
  }, [orgId, range.from, range.to]);

  const stageData = useMemo(
    () =>
      (stats?.stage_aggregates?.stages || []).map((s) => ({
        label: s.label,
        avg: Math.round((s.avg || 0) / 60),
        p95: Math.round((s.p95 || 0) / 60),
      })),
    [stats]
  );

  const slaRate = stats ? Math.round((stats.sla?.rate || 0) * 100) : null;

  return (
    <div className="admin">
      <div className="admin__inner">
        <Link to="/" className="admin__back">
          ← На главную
        </Link>

        <h1 className="admin__title">Этапы и журнал</h1>

        {/* Пикер периода */}
        <div className="analytics__presets" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 24 }}>
          <button
            className="btn btn--sm"
            onClick={() => setRange(presetRange(1))}
          >
            Сегодня
          </button>
          <button
            className="btn btn--sm"
            onClick={() => setRange(presetRange(7))}
          >
            7 дней
          </button>
          <button
            className="btn btn--sm"
            onClick={() => setRange(presetRange(30))}
          >
            30 дней
          </button>
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

        {loading && <p className="admin__state">Загрузка…</p>}
        {error && <p className="admin__state admin__state--err">Ошибка: {error}</p>}

        {/* Панель «Где застревают» */}
        <section
          className="admin__table-wrap"
          style={{ padding: 24, marginBottom: 24 }}
        >
          <h2 className="admin__section-title">Где застревают (минуты)</h2>
          {stageData.length === 0 && !loading ? (
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

        {/* Панель «SLA» */}
        <section
          className="admin__table-wrap"
          style={{ padding: 24, marginBottom: 24 }}
        >
          <h2 className="admin__section-title">SLA-просрочки</h2>
          {stats ? (
            <>
              <p
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  margin: "0 0 16px",
                  color: (stats.sla?.rate || 0) > 0.2 ? "#dc2626" : "#16a34a",
                }}
              >
                {slaRate}% просрочено
                <span
                  style={{ fontSize: 15, fontWeight: 400, color: "#6b7280", marginLeft: 10 }}
                >
                  ({stats.sla?.breached || 0}/{stats.sla?.total || 0})
                </span>
              </p>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart
                  data={stats.sla?.by_day || []}
                  margin={{ top: 8, right: 16, left: 0, bottom: 8 }}
                >
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Line
                    type="monotone"
                    dataKey="breached"
                    name="просрочки"
                    stroke="#dc2626"
                    strokeWidth={2}
                    dot={{ r: 3 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </>
          ) : (
            !loading && <p className="admin__state">Нет данных за период</p>
          )}
        </section>
      </div>
    </div>
  );
}
