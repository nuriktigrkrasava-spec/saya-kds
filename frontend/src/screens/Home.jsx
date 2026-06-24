import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { getSession, clearSession, isManager } from "../lib/auth.js";

const DEVICES = [
  {
    to: "/rolls",
    title: "Роллы / Горячее",
    desc: "Приём и готовность позиций",
    tag: "Станция кухни",
    color: "#16a34a",
    emoji: "🍣",
  },
  {
    to: "/pizza",
    title: "Пицца",
    desc: "Приём и готовность пиццы",
    tag: "Станция кухни",
    color: "#e8552d",
    emoji: "🍕",
  },
  {
    to: "/assembler",
    title: "Сборщик",
    desc: "Проверка и передача курьеру",
    tag: "Сборка",
    color: "#0ea5e9",
    emoji: "📦",
  },
];

export default function Home() {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const session = getSession();
  const identity = session
    ? [session.name, session.branch_name].filter(Boolean).join(" · ")
    : "";

  function logout() {
    clearSession();
    navigate("/login", { replace: true });
  }

  async function seed() {
    setBusy(true);
    setStatus(null);
    try {
      await api.seed();
      setStatus({ ok: true, msg: "Демо-заказы загружены" });
    } catch (e) {
      setStatus({ ok: false, msg: `Ошибка: ${e.message || e}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="home">
      <div className="home__inner">
        <header className="home__head">
          <span className="home__brand">Saya</span>
          <h1 className="home__title">Кухонный дисплей</h1>
          <p className="home__subtitle">Выберите станцию на этом устройстве</p>
          {identity && (
            <p className="home__identity">
              Вы вошли: {identity}
              <button type="button" className="home-link home__logout" onClick={logout}>
                Выйти
              </button>
            </p>
          )}
        </header>

        <div className="home__grid">
          {DEVICES.map((d) => (
            <Link
              key={d.to}
              to={d.to}
              className="device-card"
              style={{ "--accent": d.color }}
            >
              <span className="device-card__icon" aria-hidden>
                {d.emoji}
              </span>
              <span className="device-card__body">
                <span className="device-card__tag">{d.tag}</span>
                <span className="device-card__title">{d.title}</span>
                <span className="device-card__desc">{d.desc}</span>
              </span>
              <span className="device-card__go" aria-hidden>
                →
              </span>
            </Link>
          ))}
        </div>

        <div className="home__seed">
          <button className="btn btn--big btn--neutral" onClick={seed} disabled={busy}>
            {busy ? "Загрузка…" : "Загрузить демо-заказы"}
          </button>
          {status && (
            <p className={`seed-status ${status.ok ? "ok" : "err"}`}>{status.msg}</p>
          )}
          {isManager() && (
            <nav className="home__links">
              <Link to="/dashboard" className="home-link">
                Сводка (сейчас)
              </Link>
              <Link to="/admin" className="home-link">
                Аналитика
              </Link>
              <Link to="/stations" className="home-link">
                Разметка зон
              </Link>
              <Link to="/history" className="home-link">
                История сотрудников
              </Link>
              <Link to="/branches" className="home-link">
                Филиалы и сотрудники
              </Link>
              <Link to="/map" className="home-link">
                Карта курьеров
              </Link>
              <Link to="/demand" className="home-link">
                Карта спроса
              </Link>
              <Link to="/analytics" className="home-link">
                Этапы и журнал
              </Link>
            </nav>
          )}
        </div>
      </div>
    </div>
  );
}
