import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";
import "../tg.css";

const BOT = "@finansistwire_bot";
const POLL_MS = 15000;
const PAD_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "del", "0", "ok"];

function routeUrl(lat, lon) {
  // 2GIS route search to the destination coordinates (lon,lat order).
  return `https://2gis.ru/routeSearch/rsType/car/to/${lon},${lat}`;
}

export default function TgCourier() {
  const tgRef = useRef(typeof window !== "undefined" ? window.Telegram?.WebApp : null);
  const tg = tgRef.current;
  const initData = tg?.initData || "";

  // phase: "loading" | "outside" | "pin" | "orders"
  const [phase, setPhase] = useState("loading");
  const [identity, setIdentity] = useState(null); // { name, branch_code, courier_code, ... }
  const [authErr, setAuthErr] = useState("");

  // PIN pad state
  const [pin, setPin] = useState("");
  const [pinErr, setPinErr] = useState("");
  const [pinBusy, setPinBusy] = useState(false);

  // orders state
  const [orders, setOrders] = useState([]);
  const [ordersErr, setOrdersErr] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [actingId, setActingId] = useState(null);

  // --- Telegram init + theme --------------------------------------------------
  useEffect(() => {
    if (!tg) return;
    try {
      tg.ready();
      tg.expand();
      const tp = tg.themeParams || {};
      const root = document.documentElement;
      if (tp.bg_color) root.style.setProperty("--tg-bg", tp.bg_color);
      if (tp.text_color) root.style.setProperty("--tg-text", tp.text_color);
      if (tp.hint_color) root.style.setProperty("--tg-hint", tp.hint_color);
      if (tp.button_color) root.style.setProperty("--tg-accent", tp.button_color);
      if (tp.button_text_color)
        root.style.setProperty("--tg-accent-text", tp.button_text_color);
      if (tp.secondary_bg_color)
        root.style.setProperty("--tg-card", tp.secondary_bg_color);
    } catch {
      /* ignore */
    }
  }, [tg]);

  const loadOrders = useCallback(
    async (courierCode) => {
      if (!courierCode) return;
      setRefreshing(true);
      setOrdersErr("");
      try {
        const list = await api.tgMyOrders(courierCode);
        setOrders(Array.isArray(list) ? list : []);
      } catch (e) {
        setOrdersErr(e.message || String(e));
      } finally {
        setRefreshing(false);
      }
    },
    []
  );

  // --- auth on mount ----------------------------------------------------------
  useEffect(() => {
    if (!initData) {
      setPhase("outside");
      return;
    }
    let cancelled = false;
    api
      .tgAuth(initData)
      .then((resp) => {
        if (cancelled) return;
        if (resp && resp.linked) {
          setIdentity(resp);
          setPhase("orders");
          loadOrders(resp.courier_code);
        } else {
          setPhase("pin");
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setAuthErr(e.message || String(e));
        setPhase("outside");
      });
    return () => {
      cancelled = true;
    };
  }, [initData, loadOrders]);

  // --- poll my-orders ---------------------------------------------------------
  useEffect(() => {
    if (phase !== "orders" || !identity?.courier_code) return;
    const t = setInterval(() => loadOrders(identity.courier_code), POLL_MS);
    return () => clearInterval(t);
  }, [phase, identity, loadOrders]);

  // --- геолокация: на смене курьер шлёт позицию, иначе его нельзя назначить ----
  const [geo, setGeo] = useState("выкл");
  const sendLocation = useCallback(
    (announce = false) => {
      if (!initData || !navigator.geolocation) {
        setGeo("недоступно");
        if (announce) alert("Это устройство не отдаёт геолокацию.");
        return;
      }
      setGeo("определяем…");
      navigator.geolocation.getCurrentPosition(
        async (pos) => {
          try {
            await api.tgLocation(initData, pos.coords.latitude, pos.coords.longitude);
            setGeo("вкл ✅ (на смене)");
          } catch {
            setGeo("ошибка отправки");
          }
        },
        () => {
          setGeo("нет доступа");
          if (announce)
            alert(
              "Разреши доступ к геолокации (в Telegram/настройках телефона), иначе тебе нельзя назначить заказы."
            );
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 }
      );
    },
    [initData]
  );

  useEffect(() => {
    if (phase !== "orders") return;
    sendLocation();
    const t = setInterval(() => sendLocation(), 20000);
    return () => clearInterval(t);
  }, [phase, sendLocation]);

  // --- PIN pad ----------------------------------------------------------------
  function press(key) {
    setPinErr("");
    if (key === "del") {
      setPin((p) => p.slice(0, -1));
      return;
    }
    if (key === "ok") {
      submitPin();
      return;
    }
    if (pin.length >= 12) return;
    setPin((p) => p + key);
  }

  async function submitPin() {
    if (!pin || pinBusy) return;
    setPinBusy(true);
    setPinErr("");
    try {
      const resp = await api.tgLink(initData, pin);
      setIdentity(resp);
      setPin("");
      setPhase("orders");
      loadOrders(resp.courier_code);
    } catch (e) {
      const msg = String(e.message || e);
      setPinErr(msg.includes("401") ? "Неверный PIN" : msg);
      setPin("");
    } finally {
      setPinBusy(false);
    }
  }

  // --- order actions ----------------------------------------------------------
  function buildRoute(order) {
    const url = routeUrl(order.lat, order.lon);
    if (tg?.openLink) tg.openLink(url);
    else window.open(url, "_blank");
  }

  async function markDelivered(order) {
    setActingId(order.id);
    setOrdersErr("");
    try {
      await api.deliverOrder(order.id, identity?.name || "курьер");
      await loadOrders(identity?.courier_code);
    } catch (e) {
      setOrdersErr(e.message || String(e));
    } finally {
      setActingId(null);
    }
  }

  async function logout() {
    try {
      if (initData) await api.tgUnlink(initData);
    } catch {
      /* всё равно выходим */
    }
    setIdentity(null);
    setOrders([]);
    setPin("");
    setPhase(initData ? "pin" : "outside");
  }

  // --- render -----------------------------------------------------------------
  if (phase === "loading") {
    return (
      <div className="tg">
        <div className="tg__center">
          <p className="tg__muted">Загрузка…</p>
        </div>
      </div>
    );
  }

  if (phase === "outside") {
    return (
      <div className="tg">
        <div className="tg__center">
          <div className="tg__logo" aria-hidden>
            🛵
          </div>
          <h1 className="tg__title">Saya · Курьер</h1>
          <p className="tg__muted">
            Откройте через Telegram-бота {BOT}
          </p>
          {authErr && <p className="tg__err">{authErr}</p>}
        </div>
      </div>
    );
  }

  if (phase === "pin") {
    return (
      <div className="tg">
        <div className="tg__center">
          <h1 className="tg__title">Привязка курьера</h1>
          <p className="tg__muted">Введите свой PIN-код</p>

          <div className="tg__pin-display" aria-label="PIN">
            {pin.length === 0 ? (
              <span className="tg__pin-placeholder">····</span>
            ) : (
              pin.split("").map((_, i) => <span key={i} className="tg__pin-dot" />)
            )}
          </div>

          <p className={`tg__err ${pinErr ? "tg__err--show" : ""}`}>{pinErr || " "}</p>

          <div className="tg__pad">
            {PAD_KEYS.map((k) => {
              if (k === "del") {
                return (
                  <button
                    key={k}
                    type="button"
                    className="tg__key tg__key--del"
                    onClick={() => press(k)}
                    aria-label="Удалить"
                  >
                    ⌫
                  </button>
                );
              }
              if (k === "ok") {
                return (
                  <button
                    key={k}
                    type="button"
                    className="tg__key tg__key--ok"
                    onClick={() => press(k)}
                    disabled={pinBusy || !pin}
                  >
                    {pinBusy ? "…" : "Войти"}
                  </button>
                );
              }
              return (
                <button
                  key={k}
                  type="button"
                  className="tg__key"
                  onClick={() => press(k)}
                >
                  {k}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // phase === "orders"
  return (
    <div className="tg">
      <header className="tg__header">
        <span className="tg__header-title">
          Курьер · {identity?.name || ""}
          <span className="tg__geo"> · 📍 {geo}</span>
        </span>
        <button
          type="button"
          className="tg__refresh"
          onClick={() => loadOrders(identity?.courier_code)}
          disabled={refreshing}
          aria-label="Обновить"
        >
          {refreshing ? "…" : "↻"}
        </button>
        <button
          type="button"
          className="tg__refresh"
          onClick={logout}
          aria-label="Выйти"
          title="Выйти / сменить курьера"
          style={{ marginLeft: 8 }}
        >
          Выйти
        </button>
      </header>

      <main className="tg__body">
        <div
          style={{
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
            padding: "10px 12px", marginBottom: 10, borderRadius: 12,
            background: geo.startsWith("вкл") ? "#e8f7ee" : "#fff4e5",
          }}
        >
          <span style={{ fontSize: 14 }}>📍 Геолокация: <b>{geo}</b></span>
          <button
            type="button"
            className="tg-btn tg-btn--route"
            style={{ marginLeft: "auto", padding: "8px 14px" }}
            onClick={() => sendLocation(true)}
          >
            Включить геолокацию
          </button>
        </div>

        {ordersErr && <p className="tg__err tg__err--show">{ordersErr}</p>}

        {orders.length === 0 ? (
          <div className="tg__empty">
            <div className="tg__logo" aria-hidden>
              ✅
            </div>
            <p className="tg__muted">Нет назначенных заказов</p>
          </div>
        ) : (
          <div className="tg__list">
            {orders.map((o) => (
              <section key={o.id} className="tg-card">
                <div className="tg-card__head">
                  <span className="tg-card__num">№ {o.number}</span>
                  {o.customer && (
                    <span className="tg-card__cust">{o.customer}</span>
                  )}
                </div>
                {o.address && <div className="tg-card__addr">📍 {o.address}</div>}
                {o.phone && (
                  <a className="tg-card__phone" href={`tel:${o.phone}`}>
                    📞 {o.phone}
                  </a>
                )}
                {Array.isArray(o.items) && o.items.length > 0 && (
                  <ul className="tg-card__items">
                    {o.items.map((it, i) => (
                      <li key={it.id ?? i}>
                        {it.name}
                        {it.amount != null && (
                          <span className="tg-card__amount"> ×{it.amount}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="tg-card__actions">
                  <button
                    type="button"
                    className="tg-btn tg-btn--route"
                    onClick={() => buildRoute(o)}
                    disabled={o.lat == null || o.lon == null}
                  >
                    🗺 Построить маршрут
                  </button>
                  <button
                    type="button"
                    className="tg-btn tg-btn--done"
                    onClick={() => markDelivered(o)}
                    disabled={actingId === o.id}
                  >
                    {actingId === o.id ? "…" : "✓ Доставлено"}
                  </button>
                </div>
              </section>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
