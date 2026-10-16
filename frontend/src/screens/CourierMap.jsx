import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BASE, authenticatedFetch } from "../lib/api.js";
import { getSession } from "../lib/auth.js";
import "../map.css";

const POLL_MS = 15000;
const ASTANA = [51.16, 71.47];

export default function CourierMap() {
  const branchCode = getSession()?.branch_code || "";
  const mapRef = useRef(null);
  const markersRef = useRef({}); // courier_code -> L.marker
  const [couriers, setCouriers] = useState([]);
  const [err, setErr] = useState("");
  const [hasMap, setHasMap] = useState(typeof window !== "undefined" && !!window.L);

  // init Leaflet map
  useEffect(() => {
    const L = window.L;
    if (!L) {
      setHasMap(false);
      return;
    }
    const map = L.map("courier-map").setView(ASTANA, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      markersRef.current = {};
    };
  }, []);

  // poll locations
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await authenticatedFetch(
          `${BASE}/admin/couriers-locations?branch_code=${encodeURIComponent(branchCode)}`,
          { headers: { Accept: "application/json" } }
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!alive) return;
        setCouriers(Array.isArray(data) ? data : []);
        setErr("");
        const map = mapRef.current;
        const L = window.L;
        if (map && L) {
          const bounds = [];
          for (const c of data) {
            if (c.lat == null || c.lon == null) continue;
            bounds.push([c.lat, c.lon]);
            const text = `${c.name || c.courier_code} · ${c.minutes_ago ?? "?"} мин назад`;
            if (markersRef.current[c.courier_code]) {
              markersRef.current[c.courier_code].setLatLng([c.lat, c.lon]).setPopupContent(text);
            } else {
              markersRef.current[c.courier_code] = L.marker([c.lat, c.lon])
                .addTo(map)
                .bindPopup(text);
            }
          }
          if (bounds.length) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
        }
      } catch (e) {
        if (alive) setErr(e.message || String(e));
      }
    }
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [branchCode]);

  return (
    <div className="cmap">
      <header className="cmap__top">
        <Link to="/" className="cmap__back">← Назад</Link>
        <h1 className="cmap__title">Курьеры на карте</h1>
        <span className="cmap__count">{couriers.length}</span>
      </header>

      {err && <p className="cmap__err">Ошибка: {err}</p>}

      {hasMap ? (
        <div id="courier-map" className="cmap__canvas" />
      ) : (
        <div className="cmap__fallback">
          <p className="cmap__muted">Карта недоступна — список курьеров:</p>
          {couriers.length === 0 && <p className="cmap__muted">Нет данных о позициях</p>}
          {couriers.map((c) => (
            <div className="cmap__row" key={c.courier_code}>
              <span className="cmap__name">{c.name || c.courier_code}</span>
              <span className="cmap__ago">{c.minutes_ago ?? "?"} мин назад</span>
              <a
                className="cmap__link"
                href={`https://2gis.ru/geo/${c.lon},${c.lat}`}
                target="_blank"
                rel="noreferrer"
              >
                на карте 2ГИС
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
