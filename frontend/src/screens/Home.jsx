import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";

const DEVICES = [
  {
    to: "/rolls",
    title: "Кухня «Роллы / Горячее»",
    desc: "Приём и готовность позиций",
    color: "#16a34a",
    emoji: "🍣",
  },
  {
    to: "/pizza",
    title: "Кухня «Пицца»",
    desc: "Приём и готовность пиццы",
    color: "#ea580c",
    emoji: "🍕",
  },
  {
    to: "/assembler",
    title: "Сборщик",
    desc: "Проверка и передача курьеру",
    color: "#0ea5e9",
    emoji: "📦",
  },
  {
    to: "/courier",
    title: "Курьер",
    desc: "Доставка заказов",
    color: "#7c3aed",
    emoji: "🛵",
  },
];

export default function Home() {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

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
        <h1 className="home__title">Saya KDS</h1>
        <p className="home__subtitle">Выберите устройство</p>

        <div className="home__grid">
          {DEVICES.map((d) => (
            <Link
              key={d.to}
              to={d.to}
              className="device-card"
              style={{ "--accent": d.color }}
            >
              <span className="device-card__emoji" aria-hidden>
                {d.emoji}
              </span>
              <span className="device-card__title">{d.title}</span>
              <span className="device-card__desc">{d.desc}</span>
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
          <p className="home__admin-link">
            <Link to="/admin">Аналитика (админ)</Link>
            {" · "}
            <Link to="/branches">Филиалы и сотрудники</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
