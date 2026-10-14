import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BASE, authenticatedFetch } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import "../demand.css";

const ASTANA = [51.16, 71.47];

// Радиус кружка растёт с числом заказов, но ограничен сверху.
function radiusFor(count) {
  return Math.min(6 + count * 3, 40);
}

// Цвет: мало заказов — зелёный, средне — оранжевый, много — красный.
function colorFor(count, max) {
  if (max <= 1) return "#16a34a";
  const r = count / max;
  if (r >= 0.66) return "#dc2626"; // красный
  if (r >= 0.33) return "#f59e0b"; // оранжевый
  return "#16a34a"; // зелёный
}

export default function DemandMap() {
  const mapRef = useRef(null);
  const [points, setPoints] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [hasMap] = useState(typeof window !== "undefined" && !!window.L);

  // Загрузка данных спроса.
  useEffect(() => {
    let alive = true;
    async function load() {
      setLoading(true);
      try {
        const res = await authenticatedFetch(
          `${BASE}/admin/demand?org_id=${encodeURIComponent(getOrgId())}`,
          { headers: { Accept: "application/json" } }
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!alive) return;
        setPoints(Array.isArray(data) ? data : []);
        setErr("");
      } catch (e) {
        if (alive) setErr(e.message || String(e));
      } finally {
        if (alive) setLoading(false);
      }
    }
    load();
    return () => {
      alive = false;
    };
  }, []);

  // Инициализация Leaflet-карты (один раз).
  useEffect(() => {
    const L = window.L;
    if (!L) return;
    const map = L.map("demand-map").setView(ASTANA, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [hasMap]);

  // Отрисовка кружков спроса при изменении данных.
  useEffect(() => {
    const L = window.L;
    const map = mapRef.current;
    if (!L || !map) return;

    const layer = L.layerGroup().addTo(map);
    const max = points.reduce((m, p) => Math.max(m, p.count || 0), 0);
    const bounds = [];
    for (const p of points) {
      if (p.lat == null || p.lon == null) continue;
      bounds.push([p.lat, p.lon]);
      const color = colorFor(p.count, max);
      L.circleMarker([p.lat, p.lon], {
        radius: radiusFor(p.count),
        color,
        fillColor: color,
        fillOpacity: 0.5,
        weight: 1,
      })
        .addTo(layer)
        .bindPopup(`${p.count} заказов · ${p.sample_address || "адрес неизвестен"}`);
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });

    return () => {
      layer.remove();
    };
  }, [points]);

  const totalOrders = points.reduce((s, p) => s + (p.count || 0), 0);

  return (
    <div className="demand">
      <header className="demand__top">
        <Link to="/" className="demand__back">← Назад</Link>
        <h1 className="demand__title">Карта спроса</h1>
        <span className="demand__count">{totalOrders} заказов</span>
      </header>

      {loading && <p className="demand__muted">Загрузка…</p>}
      {err && <p className="demand__err">Ошибка: {err}</p>}

      {!loading && !err && points.length === 0 && (
        <p className="demand__muted">Нет заказов с координатами</p>
      )}

      {hasMap ? (
        <>
          <div id="demand-map" className="demand__canvas" />
          {points.length > 0 && (
            <div className="demand__legend">
              <span className="demand__legend-title">Заказов в точке:</span>
              <span className="demand__chip demand__chip--low">мало</span>
              <span className="demand__chip demand__chip--mid">средне</span>
              <span className="demand__chip demand__chip--high">много</span>
            </div>
          )}
        </>
      ) : (
        <div className="demand__fallback">
          <p className="demand__muted">Карта недоступна — список по адресам:</p>
          {points.map((p, i) => (
            <div className="demand__row" key={`${p.lat},${p.lon},${i}`}>
              <span className="demand__addr">
                {p.sample_address || `${p.lat}, ${p.lon}`}
              </span>
              <span className="demand__n">{p.count} заказов</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
