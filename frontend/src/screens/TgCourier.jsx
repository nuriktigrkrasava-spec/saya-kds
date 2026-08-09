import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";
import "../tg.css";

const BOT = "@finansistwire_bot";
const POLL_MS = 15000;

const MONEY = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });

function formatMoney(sum) {
  return `${MONEY.format(sum)} ₸`;
}

function routeUrl(order) {
  // /geo/{lon},{lat} надёжно открывает точку в нужном городе (автоопределение по
  // координатам), откуда курьер строит маршрут «Проезд». Формат routeSearch на web
  // редиректит на Москву и маршрут не строит, поэтому не используем.
  if (order?.lat != null && order?.lon != null) {
    return `https://2gis.ru/geo/${order.lon},${order.lat}`;
  }
  // Заказы с сайта приезжают без координат — ищем по самому адресу: 2ГИС
  // распознаёт его нормально, это и делали курьеры руками.
  const query = (order?.address || "").trim();
  return query ? `https://2gis.ru/astana/search/${encodeURIComponent(query)}` : null;
}

export default function TgCourier() {
  const tgRef = useRef(typeof window !== "undefined" ? window.Telegram?.WebApp : null);
  const tg = tgRef.current;
  const initData = tg?.initData || "";

  // phase: "loading" | "outside" | "pick" | "orders"
  const [phase, setPhase] = useState("loading");
  const [identity, setIdentity] = useState(null); // { name, branch_code, courier_code, ... }
  const [authErr, setAuthErr] = useState("");

  // pick: список открытых сегодня явок iiko — тап по своему имени = вход
  const [shift, setShift] = useState([]);
  const [shiftErr, setShiftErr] = useState("");
  const [shiftBusy, setShiftBusy] = useState(false);
  const [claimBusyId, setClaimBusyId] = useState(null);
  const [confirmEmp, setConfirmEmp] = useState(null); // занято — подтверждение

  // orders state
  const [orders, setOrders] = useState([]);
  const [ordersErr, setOrdersErr] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  // вкладки: мои заказы / все доставки филиала (только просмотр)
  const [tab, setTab] = useState("my"); // "my" | "all"
  const [allOrders, setAllOrders] = useState([]);
  const [ccPhone, setCcPhone] = useState(""); // общий телефон колл-центра
  const [detailOrder, setDetailOrder] = useState(null); // деталка по тапу
  const [actingId, setActingId] = useState(null);
  const [paymentOrder, setPaymentOrder] = useState(null); // order awaiting payment-kind pick
  const [paymentTypesList, setPaymentTypesList] = useState([]); // real payment types for overlay
  const [paymentTypesLoading, setPaymentTypesLoading] = useState(false);
  const [closeInfo, setCloseInfo] = useState(null); // сколько осталось получить, из iiko

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

  const loadAll = useCallback(async (orgId) => {
    if (!orgId) return;
    setRefreshing(true);
    setOrdersErr("");
    try {
      const resp = await api.tgDeliveries(orgId);
      setAllOrders(Array.isArray(resp?.orders) ? resp.orders : []);
      setCcPhone(resp?.call_center_phone || "");
    } catch (e) {
      setOrdersErr(e.message || String(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

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
          setPhase("pick");
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

  // --- poll active tab --------------------------------------------------------
  useEffect(() => {
    if (phase !== "orders" || !identity) return;
    const tick = () =>
      tab === "all"
        ? loadAll(identity.org_id)
        : loadOrders(identity.courier_code);
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => clearInterval(t);
  }, [phase, identity, tab, loadOrders, loadAll]);

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

  // Телефон колл-центра нужен и на вкладке «Мои» — подтягиваем один раз при входе.
  useEffect(() => {
    if (phase === "orders" && identity?.org_id) loadAll(identity.org_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, identity?.org_id]);

  // --- вход по явке: список имён ------------------------------------------------
  const loadShift = useCallback(async () => {
    setShiftBusy(true);
    setShiftErr("");
    try {
      const list = await api.tgShiftEmployees();
      setShift(Array.isArray(list) ? list : []);
    } catch (e) {
      setShiftErr(String(e.message || e).includes("503")
        ? "iiko недоступен, попробуй позже"
        : e.message || String(e));
    } finally {
      setShiftBusy(false);
    }
  }, []);

  // Список явок обновляем сам, пока курьер стоит на экране выбора: смену ему
  // открывают на кассе прямо в этот момент, и ждать, пока он догадается нажать
  // «Обновить», незачем (09.08.2026: Мейіржану открыли смену в 18:37, а он
  // смотрел в список, загруженный минутой раньше).
  useEffect(() => {
    if (phase !== "pick") return;
    loadShift();
    const t = setInterval(loadShift, 30000);
    return () => clearInterval(t);
  }, [phase, loadShift]);

  async function claim(emp) {
    if (claimBusyId) return;
    setConfirmEmp(null);
    setClaimBusyId(emp.employee_id);
    setShiftErr("");
    try {
      const resp = await api.tgClaim(initData, emp.employee_id);
      setIdentity(resp);
      setPhase("orders");
      loadOrders(resp.courier_code);
    } catch (e) {
      const msg = String(e.message || e);
      setShiftErr(msg.includes("409")
        ? "Явка уже закрыта — обнови список"
        : msg.includes("503") ? "iiko недоступен, попробуй позже" : msg);
      loadShift();
    } finally {
      setClaimBusyId(null);
    }
  }

  // --- order actions ----------------------------------------------------------
  function buildRoute(order) {
    const url = routeUrl(order);
    if (!url) return;
    if (tg?.openLink) tg.openLink(url);
    else window.open(url, "_blank");
  }

  function startDeliver(order) {
    setPaymentOrder(order);
    setPaymentTypesList([]);
    setCloseInfo(null);
    setPaymentTypesLoading(true);
    // Правда об оплате — только в iiko и только сейчас. Тип из заказа может
    // оказаться бонусной частью сплита: заказ №36 от 09.08.2026 показывал
    // «БАЛЛЫ» и ими же закрылся, хотя клиент платил онлайн. Поэтому спрашиваем
    // бэкенд: сколько осталось получить и нужен ли вообще выбор типа.
    api
      .closeInfo(order.id)
      .then(async (info) => {
        setCloseInfo(info);
        if (info?.needs_choice && order.org_id) {
          const list = await api.paymentTypes(order.org_id).catch(() => []);
          setPaymentTypesList(Array.isArray(list) ? list : []);
        }
      })
      .catch(() => setCloseInfo(null))
      .finally(() => setPaymentTypesLoading(false));
  }

  function cancelDeliver() {
    setPaymentOrder(null);
    setCloseInfo(null);
  }

  async function confirmDeliver(paymentKind, paymentTypeId) {
    const order = paymentOrder;
    setPaymentOrder(null);
    setPaymentTypesList([]);
    setCloseInfo(null);
    setActingId(order.id);
    setOrdersErr("");
    try {
      await api.deliverOrder(order.id, identity?.name || "курьер", paymentKind, paymentTypeId);
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
    setPhase(initData ? "pick" : "outside");
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

  if (phase === "pick") {
    return (
      <div className="tg">
        <div className="tg__center tg__center--top">
          <h1 className="tg__title">Кто ты?</h1>
          <p className="tg__muted">Открытые сегодня явки · тапни своё имя</p>
          <p className={`tg__err ${shiftErr ? "tg__err--show" : ""}`}>{shiftErr || " "}</p>

          {shiftBusy && shift.length === 0 ? (
            <p className="tg__muted">Загрузка…</p>
          ) : shift.length === 0 ? (
            <p className="tg__muted">
              Никого нет в списке. Нет твоего имени? Попроси кассира открыть
              смену в iiko, затем обнови.
            </p>
          ) : (
            <div className="tg__emplist">
              {shift.map((e) => (
                <button
                  key={e.employee_id}
                  type="button"
                  className="tg__emp"
                  disabled={claimBusyId !== null}
                  onClick={() => (e.busy ? setConfirmEmp(e) : claim(e))}
                >
                  <span className="tg__emp-name">
                    {claimBusyId === e.employee_id ? "…" : e.name}
                  </span>
                  <span className="tg__emp-meta">
                    {e.role_label}
                    {e.busy && <span className="tg__emp-busy"> · уже вошли</span>}
                  </span>
                </button>
              ))}
            </div>
          )}

          <button
            type="button"
            className="tg-btn tg-btn--ghost tg__emplist-refresh"
            onClick={loadShift}
            disabled={shiftBusy}
          >
            {shiftBusy ? "…" : "↻ Обновить список"}
          </button>
          <p className="tg__hint tg__hint--pick">
            Нет твоего имени — попроси кассира открыть смену в iiko.
          </p>
        </div>

        {confirmEmp && (
          <div className="tg-payment-overlay" role="dialog" aria-modal="true">
            <div className="tg-payment-sheet">
              <p className="tg-payment-sheet__title">Под именем «{confirmEmp.name}» уже вошли</p>
              <p className="tg-payment-sheet__sub">
                Если это не ты — выбери своё имя, иначе статистика доставок перепутается.
              </p>
              <div className="tg-payment-sheet__btns">
                <button type="button" className="tg-btn tg-btn--primary"
                        onClick={() => claim(confirmEmp)}>
                  Это я, войти
                </button>
              </div>
              <button type="button"
                      className="tg-btn tg-btn--ghost tg-payment-sheet__cancel"
                      onClick={() => setConfirmEmp(null)}>
                Отмена
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  // phase === "orders"
  const geoOn = geo.startsWith("вкл");
  // Выбор типа оплаты нужен, только если с клиента реально осталось получить
  // деньги. Если iiko не ответил (closeInfo=null) — падаем на прежнее правило:
  // тип в заказе есть => закрываем одной кнопкой, нет => спрашиваем.
  const needsChoice = closeInfo
    ? Boolean(closeInfo.needs_choice)
    : Boolean(paymentOrder && !paymentOrder.payment_type_name);
  return (
    <div className="tg">
      <header className="tg__header">
        <span className="tg__header-title">Курьер · {identity?.name || ""}</span>
        <div className="tg__header-actions">
          <button
            type="button"
            className="tg__icon-btn"
            onClick={() => loadOrders(identity?.courier_code)}
            disabled={refreshing}
            aria-label="Обновить"
          >
            {refreshing ? "…" : "↻"}
          </button>
          <button
            type="button"
            className="tg__icon-btn tg__logout"
            onClick={logout}
            aria-label="Выйти"
            title="Выйти / сменить курьера"
          >
            Выйти
          </button>
        </div>
      </header>

      <main className="tg__body">
        <div className={`tg__geo ${geoOn ? "tg__geo--on" : "tg__geo--off"}`}>
          <span className="tg__geo-text">
            📍 Геолокация: <b>{geo}</b>
          </span>
          <button
            type="button"
            className="tg__geo-btn"
            onClick={() => sendLocation(true)}
          >
            Включить геолокацию
          </button>
        </div>

        <div className="tg__hint">
          🛰 Чтобы тебя видели на карте <b>в фоне</b> (даже когда апп закрыт):
          в чате с ботом нажми <b>📎 → Геопозиция → Транслировать геопозицию</b>
          на 8 часов. Включи в начале смены.
        </div>

        {ordersErr && <p className="tg__err tg__err--show">{ordersErr}</p>}

        <div className="tg__tabs">
          <button
            type="button"
            className={`tg__tab ${tab === "my" ? "tg__tab--on" : ""}`}
            onClick={() => setTab("my")}
          >
            Мои · {orders.length}
          </button>
          <button
            type="button"
            className={`tg__tab ${tab === "all" ? "tg__tab--on" : ""}`}
            onClick={() => setTab("all")}
          >
            Ждут курьера · {allOrders.length}
          </button>
        </div>

        {tab === "all" ? (
          allOrders.length === 0 ? (
            <div className="tg__empty">
              <div className="tg__logo" aria-hidden>
                📭
              </div>
              <p className="tg__muted">Нет заказов, ждущих курьера</p>
            </div>
          ) : (
            <div className="tg__list">
              {allOrders.map((o) => (
                <button
                  key={o.id}
                  type="button"
                  className="tg-card tg-card--tap"
                  onClick={() => setDetailOrder(o)}
                >
                  <div className="tg-card__head">
                    <span className="tg-card__num">№ {o.number}</span>
                    {/* Готовность видно сразу: собранный заказ можно забирать
                        прямо сейчас, остальные ещё на кухне. */}
                    <span
                      className={`tg-card__status${o.assembled ? " tg-card__status--ready" : ""}`}
                    >
                      {o.assembled ? "✅ собран, ждёт курьера" : "👨‍🍳 готовится"}
                    </span>
                  </div>
                  <div className="tg-card__addr">
                    📍 {o.address || "Адрес не указан"}
                  </div>
                </button>
              ))}
            </div>
          )
        ) : orders.length === 0 ? (
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
                <div className="tg-card__addr">
                  📍 {o.address || "Адрес не указан"}
                </div>
                {o.phone && (
                  <a className="tg-card__phone" href={`tel:${o.phone}`}>
                    📞 {o.phone}
                  </a>
                )}
                {ccPhone && (
                  <a className="tg-card__phone" href={`tel:${ccPhone}`}>
                    ☎️ Колл-центр: {ccPhone}
                  </a>
                )}
                {o.comment && (
                  <div className="tg-card__note">📝 {o.comment}</div>
                )}
                {o.payment_type_name && (
                  <div className="tg-card__pay-badge">💳 Оплата: {o.payment_type_name}</div>
                )}
                {Array.isArray(o.items) && o.items.length > 0 && (
                  <ul className="tg-card__items">
                    {o.items.map((it, i) => (
                      <li key={it.id ?? i}>
                        {it.name}
                        {it.amount != null && (
                          <span className="tg-card__amount"> ×{it.amount}</span>
                        )}
                        {it.comment && (
                          <span className="tg-card__note tg-card__note--inline">
                            📝 {it.comment}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="tg-card__actions">
                  <button
                    type="button"
                    className="tg-btn tg-btn--primary"
                    onClick={() => buildRoute(o)}
                    disabled={!routeUrl(o)}
                  >
                    🗺 Маршрут
                  </button>
                  <button
                    type="button"
                    className="tg-btn tg-btn--success"
                    onClick={() => startDeliver(o)}
                    disabled={actingId === o.id}
                  >
                    {actingId === o.id ? "…" : "✓ Закрыть заказ"}
                  </button>
                </div>
              </section>
            ))}
          </div>
        )}
      </main>

      {detailOrder && (
        <div
          className="tg-payment-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={`Заказ № ${detailOrder.number}`}
          onClick={() => setDetailOrder(null)}
        >
          <div className="tg-payment-sheet" onClick={(e) => e.stopPropagation()}>
            <p className="tg-payment-sheet__title">Заказ № {detailOrder.number}</p>
            <p className="tg-payment-sheet__sub">
              {detailOrder.assigned_courier_name
                ? `🛵 Везёт: ${detailOrder.assigned_courier_name}`
                : "⏳ Курьер не назначен"}
            </p>
            {detailOrder.customer && (
              <p className="tg-detail__row">👤 {detailOrder.customer}</p>
            )}
            <p className="tg-detail__row">
              📍 {detailOrder.address || "Адрес не указан"}
            </p>
            {Array.isArray(detailOrder.items) && detailOrder.items.length > 0 && (
              <ul className="tg-card__items tg-detail__items">
                {detailOrder.items.map((it, i) => (
                  <li key={it.id ?? i}>
                    {it.name}
                    {it.amount != null && (
                      <span className="tg-card__amount"> ×{it.amount}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <div className="tg-payment-sheet__btns">
              {detailOrder.phone && (
                <a className="tg-btn tg-btn--primary" href={`tel:${detailOrder.phone}`}>
                  📞 Клиент: {detailOrder.phone}
                </a>
              )}
              {ccPhone && (
                <a className="tg-btn tg-btn--cash" href={`tel:${ccPhone}`}>
                  ☎️ Колл-центр: {ccPhone}
                </a>
              )}
              <button
                type="button"
                className="tg-btn tg-btn--card"
                onClick={() => buildRoute(detailOrder)}
                disabled={!routeUrl(detailOrder)}
              >
                🗺 Маршрут
              </button>
            </div>
            <button
              type="button"
              className="tg-btn tg-btn--ghost tg-payment-sheet__cancel"
              onClick={() => setDetailOrder(null)}
            >
              Закрыть
            </button>
          </div>
        </div>
      )}

      {paymentOrder && (
        <div className="tg-payment-overlay" role="dialog" aria-modal="true" aria-label="Закрыть заказ">
          <div className="tg-payment-sheet">
            <p className="tg-payment-sheet__title">
              {needsChoice ? "Чем оплатил клиент?" : "Закрыть заказ?"}
            </p>
            <p className="tg-payment-sheet__sub">Заказ № {paymentOrder.number}</p>
            {needsChoice && closeInfo?.due > 0 && (
              <p className="tg-payment-sheet__due">
                Получить с клиента: <b>{formatMoney(closeInfo.due)}</b>
              </p>
            )}
            {paymentTypesLoading ? (
              <p className="tg-payment-sheet__sub">Проверяем оплату…</p>
            ) : !needsChoice ? (
              // Заказ уже оплачен (онлайн, Kaspi, бонусы или их сумма) — просто
              // закрываем: доплачивать нечего, выбирать нечего.
              <>
                <p className="tg-payment-sheet__pay">
                  {closeInfo?.payment_type_name ? (
                    <>
                      Оплата: <b>{closeInfo.payment_type_name}</b>
                    </>
                  ) : (
                    <>Заказ оплачен полностью</>
                  )}
                </p>
                <div className="tg-payment-sheet__btns">
                  <button
                    type="button"
                    className="tg-btn tg-btn--primary"
                    onClick={() => confirmDeliver(null, null)}
                  >
                    Закрыть заказ
                  </button>
                </div>
              </>
            ) : paymentTypesList.length > 0 ? (
              <div className="tg-payment-sheet__btns tg-payment-sheet__btns--wrap">
                {paymentTypesList.map((pt) => {
                  const isDefault =
                    paymentOrder.payment_type_name &&
                    pt.name === paymentOrder.payment_type_name;
                  return (
                    <button
                      key={pt.id}
                      type="button"
                      className={`tg-btn tg-btn--pay-type${isDefault ? " tg-btn--pay-type--default" : ""}`}
                      onClick={() => confirmDeliver(pt.kind, pt.id)}
                    >
                      {pt.name}
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="tg-payment-sheet__btns">
                <button
                  type="button"
                  className="tg-btn tg-btn--cash"
                  onClick={() => confirmDeliver("Cash")}
                >
                  💵 Наличные
                </button>
                <button
                  type="button"
                  className="tg-btn tg-btn--card"
                  onClick={() => confirmDeliver("Card")}
                >
                  💳 Карта
                </button>
              </div>
            )}
            <button
              type="button"
              className="tg-btn tg-btn--ghost tg-payment-sheet__cancel"
              onClick={cancelDeliver}
            >
              Отмена
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
