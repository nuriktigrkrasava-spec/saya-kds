import { Link, useNavigate } from "react-router-dom";
import { getSession, clearSession } from "../lib/auth.js";

export default function ScreenHeader({ title, accent, connected, children }) {
  const navigate = useNavigate();
  const session = getSession();
  const identity = session
    ? [session.name, session.branch_name].filter(Boolean).join(" · ")
    : "";

  function logout() {
    clearSession();
    navigate("/login", { replace: true });
  }

  return (
    <header className="screen-header" style={accent ? { borderColor: accent } : undefined}>
      <div className="screen-header__left">
        <Link to="/" className="back-link" aria-label="На главную">
          ←
        </Link>
        <h1 className="screen-title">{title}</h1>
        <span
          className={`conn ${connected ? "conn--on" : "conn--off"}`}
          title={connected ? "Связь есть" : "Нет связи"}
        >
          <span className="conn__dot" />
          <span className="conn__label">
            {connected ? "Онлайн" : "Нет связи"}
          </span>
        </span>
      </div>
      <div className="screen-header__right">
        {identity && <span className="identity">{identity}</span>}
        <button type="button" className="btn btn--ghost logout-btn" onClick={logout}>
          Выйти
        </button>
        {children}
      </div>
    </header>
  );
}
