import { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";
import { BASE } from "../lib/api.js";
import { getSession } from "../lib/auth.js";

const POLL_MS = 15000;
const ASTANA = [51.16, 71.47];

/**
 * Компактная live-карта курьеров филиала — встраивается в экран сборщика.
 * Сворачивается, чтобы не занимать место.
 */
export default function CourierMiniMap() {
  const branchCode = getSession()?.branch_code || "";
  const [open, setOpen] = useState(true);
  const [couriers, setCouriers] = useState([]);
  const elRef = useRef(null);
  const mapRef = useRef(null);
  const markersRef = useRef({});
  const hasL = typeof window !== "undefined" && !!window.L;

  // init / teardown карты при разворачивании
  useEffect(() => {
    if (!open || !hasL || !elRef.current || mapRef.current) return;
    const L = window.L;
    const map = L.map(elRef.current, { attributionControl: false }).setView(ASTANA, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    mapRef.current = map;
    // карта в скрытом контейнере иногда требует пересчёта размеров
    setTimeout(() => map.invalidateSize(), 50);
    return () => {
      map.remove();
      mapRef.current = null;
      markersRef.current = {};
    };
  }, [open, hasL]);

  // опрос позиций
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetch(
          `${BASE}/admin/couriers-locations?branch_code=${encodeURIComponent(branchCode)}`,
          { headers: { Accept: "application/json" } }
        );
        const data = await res.json();
        if (!alive) return;
        setCouriers(Array.isArray(data) ? data : []);
        const map = mapRef.current;
        const L = window.L;
        if (map && L) {
          const bounds = [];
          for (const c of data) {
            if (c.lat == null || c.lon == null) continue;
            bounds.push([c.lat, c.lon]);
            const text = `${c.name || c.courier_code} · ${c.minutes_ago ?? "?"} мин`;
            if (markersRef.current[c.courier_code]) {
              markersRef.current[c.courier_code].setLatLng([c.lat, c.lon]).setPopupContent(text);
            } else {
              markersRef.current[c.courier_code] = L.marker([c.lat, c.lon]).addTo(map).bindPopup(text);
            }
          }
          if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
        }
      } catch {
        /* тихо */
      }
    }
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [branchCode, open]);

  return (
    <section className="minimap">
      <button type="button" className="minimap__head" onClick={() => setOpen((v) => !v)}>
        <span className="minimap__label">
          <Icon name="map" size={18} />
          Курьеры на карте
        </span>
        <span className="minimap__count">{couriers.length}</span>
        <span className="minimap__chev">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        hasL ? (
          <div ref={elRef} className="minimap__canvas" />
        ) : (
          <div className="minimap__fallback">
            {couriers.length === 0 && <span className="muted">Нет данных о позициях курьеров</span>}
            {couriers.map((c) => (
              <div className="minimap__row" key={c.courier_code}>
                <b>{c.name || c.courier_code}</b>
                <span className="muted"> · {c.minutes_ago ?? "?"} мин назад</span>
              </div>
            ))}
          </div>
        )
      )}
    </section>
  );
}
