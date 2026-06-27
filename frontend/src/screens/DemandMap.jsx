import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import "../demand.css";

const ASTANA = [51.16, 71.47];

// Заливка клетки по плотности: нет заказов — почти прозрачная, дальше
// зелёный → оранжевый → красный по доле от максимума. БЕЗ чисел — только цвет.
function zoneStyle(count, max) {
  if (!count) return { color: "#cbd5e1", fillColor: "#e2e8f0", fillOpacity: 0.12, weight: 1 };
  const r = max > 0 ? count / max : 0;
  const color = r >= 0.66 ? "#dc2626" : r >= 0.33 ? "#f59e0b" : "#16a34a";
  return { color, fillColor: color, fillOpacity: 0.18 + r * 0.42, weight: 1 };
}

export default function DemandMap() {
  const mapRef = useRef(null);
  const [zones, setZones] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [hasMap] = useState(typeof window !== "undefined" && !!window.L);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.demandZones(getOrgId());
      setZones(Array.isArray(data) ? data : []);
      setErr("");
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Пересоздать сетку зон и перерисовать.
  const regrid = useCallback(async () => {
    setBusy(true);
    try {
      await api.autoGridZones(getOrgId(), 6);
      await load();
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }, [load]);

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

  // Отрисовка зон-клеток при изменении данных.
  useEffect(() => {
    const L = window.L;
    const map = mapRef.current;
    if (!L || !map) return;

    const layer = L.layerGroup().addTo(map);
    const max = zones.reduce((m, z) => Math.max(m, z.count || 0), 0);
    const bounds = [];
    for (const z of zones) {
      const poly = z.polygon || [];
      if (poly.length < 3) continue;
      for (const pt of poly) bounds.push(pt);
      L.polygon(poly, zoneStyle(z.count || 0, max))
        .addTo(layer)
        .bindTooltip(z.name || "", { permanent: false, direction: "center" });
    }
    if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });

    return () => {
      layer.remove();
    };
  }, [zones]);

  const hasZones = zones.length > 0;

  return (
    <div className="demand">
      <header className="demand__top">
        <Link to="/" className="demand__back">← Назад</Link>
        <h1 className="demand__title">Карта спроса</h1>
        <button className="demand__regrid" onClick={regrid} disabled={busy}>
          {busy ? "Делю…" : "Авто-разбить на зоны"}
        </button>
      </header>

      {loading && <p className="demand__muted">Загрузка…</p>}
      {err && <p className="demand__err">Ошибка: {err}</p>}

      {!loading && !err && !hasZones && (
        <p className="demand__muted">
          Зоны ещё не созданы — нажми «Авто-разбить на зоны».
        </p>
      )}

      {hasMap ? (
        <>
          <div id="demand-map" className="demand__canvas" />
          {hasZones && (
            <div className="demand__legend">
              <span className="demand__legend-title">Спрос по зонам:</span>
              <span className="demand__chip demand__chip--low">слабый</span>
              <span className="demand__chip demand__chip--mid">средний</span>
              <span className="demand__chip demand__chip--high">высокий</span>
            </div>
          )}
        </>
      ) : (
        <div className="demand__fallback">
          <p className="demand__muted">Карта недоступна — список зон:</p>
          {zones.map((z) => (
            <div className="demand__row" key={z.id}>
              <span className="demand__addr">Зона {z.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
