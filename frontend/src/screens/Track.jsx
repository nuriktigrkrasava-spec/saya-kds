import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { BASE } from "../lib/api.js";
import "../track.css";

const POLL_MS = 10000;

function fmtTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export default function Track() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading"); // loading | ok | notfound | error
  const mapRef = useRef(null);
  const markerRef = useRef(null);
  const [hasMap] = useState(typeof window !== "undefined" && !!window.L);

  // Опрос статуса каждые ~10с.
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const res = await fetch(`${BASE}/api/track/${encodeURIComponent(id)}`, {
          headers: { Accept: "application/json" },
        });
        if (res.status === 404) {
          if (alive) setState("notfound");
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        if (!alive) return;
        setData(body);
        setState("ok");
      } catch {
        if (alive) setState((s) => (s === "ok" ? "ok" : "error"));
      }
    }
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [id]);

  const courier = data?.courier || null;

  // Карта Leaflet с точкой курьера (только пока он в пути).
  useEffect(() => {
    const L = window.L;
    if (!L || !courier) return;
    const pos = [courier.lat, courier.lon];
    const popup = `Курьер · ${courier.minutes_ago ?? "?"} мин назад`;

    if (!mapRef.current) {
      const map = L.map("track-map", { zoomControl: false }).setView(pos, 14);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap",
        maxZoom: 19,
      }).addTo(map);
      mapRef.current = map;
    }
    const map = mapRef.current;
    if (markerRef.current) {
      markerRef.current.setLatLng(pos).setPopupContent(popup);
    } else {
      markerRef.current = L.marker(pos).addTo(map).bindPopup(popup);
    }
    map.setView(pos, map.getZoom() || 14);
  }, [courier]);

  // Снести карту, когда курьер пропал (доставлен / не в пути).
  useEffect(() => {
    if (!courier && mapRef.current) {
      mapRef.current.remove();
      mapRef.current = null;
      markerRef.current = null;
    }
  }, [courier]);

  if (state === "loading") {
    return (
      <div className="trk trk--center">
        <div className="trk__spinner" aria-hidden />
        <p className="trk__muted">Загружаем статус заказа…</p>
      </div>
    );
  }

  if (state === "notfound") {
    return (
      <div className="trk trk--center">
        <div className="trk__big">🔍</div>
        <h1 className="trk__title">Заказ не найден</h1>
        <p className="trk__muted">Проверьте ссылку — возможно, она устарела.</p>
      </div>
    );
  }

  if (state === "error" && !data) {
    return (
      <div className="trk trk--center">
        <div className="trk__big">⚠️</div>
        <h1 className="trk__title">Не удалось загрузить</h1>
        <p className="trk__muted">Пробуем снова…</p>
      </div>
    );
  }

  const stages = data?.stages || [];
  // Текущая стадия — последняя выполненная.
  let currentIdx = -1;
  stages.forEach((st, i) => {
    if (st.done) currentIdx = i;
  });

  return (
    <div className="trk">
      <header className="trk__head">
        <div className="trk__brand">Saya KDS</div>
        <h1 className="trk__order">Заказ №{data.number}</h1>
        {data.address && <p className="trk__addr">{data.address}</p>}
      </header>

      <ol className="trk__timeline">
        {stages.map((st, i) => {
          const isCurrent = i === currentIdx;
          const cls = [
            "trk__stage",
            st.done ? "is-done" : "",
            isCurrent ? "is-current" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <li className={cls} key={st.key}>
              <span className="trk__dot">{st.done ? "✓" : ""}</span>
              <div className="trk__stagebody">
                <span className="trk__label">{st.label}</span>
                {st.at && <span className="trk__at">{fmtTime(st.at)}</span>}
              </div>
            </li>
          );
        })}
      </ol>

      {courier && (
        <section className="trk__courier">
          <h2 className="trk__chead">Курьер в пути 🛵</h2>
          {hasMap ? (
            <div id="track-map" className="trk__map" />
          ) : (
            <p className="trk__muted">
              Курьер в пути · обновлено {courier.minutes_ago ?? "?"} мин назад
              <br />
              {courier.lat?.toFixed(5)}, {courier.lon?.toFixed(5)}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
