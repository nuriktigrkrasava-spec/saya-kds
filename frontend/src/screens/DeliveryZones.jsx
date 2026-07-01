import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";

const ASTANA = [51.16, 71.47];
const COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"];

export default function DeliveryZones() {
  const mapRef = useRef(null);
  const drawnRef = useRef(null); // L.FeatureGroup с полигонами
  const zonesLenRef = useRef(0); // актуальная длина для индекса цвета внутри обработчика
  const [zones, setZones] = useState([]);
  const [err, setErr] = useState("");
  const hasMap = typeof window !== "undefined" && !!window.L;

  async function reload() {
    try {
      setZones(await api.deliveryZones(getOrgId()));
    } catch (e) {
      setErr(e.message || String(e));
    }
  }
  useEffect(() => {
    reload();
  }, []);

  // держим актуальную длину для индекса цвета внутри обработчика
  useEffect(() => {
    zonesLenRef.current = zones.length;
  }, [zones]);

  // init map + draw control
  useEffect(() => {
    const L = window.L;
    if (!L) return;
    const map = L.map("zones-map").setView(ASTANA, 11);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map);
    const drawn = new L.FeatureGroup().addTo(map);
    drawnRef.current = drawn;
    const ctrl = new L.Control.Draw({
      edit: { featureGroup: drawn },
      draw: {
        polygon: true,
        marker: false,
        polyline: false,
        circle: false,
        rectangle: false,
        circlemarker: false,
      },
    });
    map.addControl(ctrl);

    map.on(L.Draw.Event.CREATED, async (e) => {
      const latlngs = e.layer.getLatLngs()[0].map((p) => [p.lat, p.lng]);
      const color = COLORS[zonesLenRef.current % COLORS.length];
      const name = window.prompt("Название района:", "Район") || "Район";
      await api.createDeliveryZone({ org_id: getOrgId(), name, polygon: latlngs, color });
      await reload();
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      drawnRef.current = null;
    };
  }, [hasMap]);

  // перерисовываем сохранённые полигоны при изменении списка
  useEffect(() => {
    const L = window.L;
    const drawn = drawnRef.current;
    if (!L || !drawn) return;
    drawn.clearLayers();
    for (const z of zones) {
      if (!z.polygon || z.polygon.length < 3) continue;
      L.polygon(z.polygon, { color: z.color })
        .bindTooltip(z.name, { permanent: true })
        .addTo(drawn);
    }
  }, [zones]);

  async function remove(id) {
    if (!window.confirm("Удалить зону?")) return;
    await api.deleteDeliveryZone(id, getOrgId());
    await reload();
  }

  return (
    <div className="admin">
      <header style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <Link to="/analytics">← Назад</Link>
        <h1>Зоны доставки города</h1>
      </header>
      {err && <p style={{ color: "#dc2626" }}>Ошибка: {err}</p>}
      {!hasMap && <p>Карта недоступна (Leaflet не загрузился).</p>}
      <div id="zones-map" style={{ height: "60vh", width: "100%", borderRadius: 8 }} />
      <ul>
        {zones.map((z) => (
          <li key={z.id} style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span
              style={{
                width: 12,
                height: 12,
                background: z.color,
                display: "inline-block",
                borderRadius: 3,
              }}
            />
            {z.name} ({z.polygon?.length || 0} точек)
            <button onClick={() => remove(z.id)}>Удалить</button>
          </li>
        ))}
      </ul>
    </div>
  );
}
