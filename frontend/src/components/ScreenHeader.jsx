import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { getSession, clearSession } from "../lib/auth.js";
import { isSoundOn, setSoundOn, unlockAudio, playNewOrder } from "../lib/sound.js";

export default function ScreenHeader({ title, accent, connected, children }) {
  const navigate = useNavigate();
  const session = getSession();
  const identity = session
    ? [session.name, session.branch_name].filter(Boolean).join(" · ")
    : "";

  // Bell toggle. Starts from the persisted preference.
  const [soundOn, setSound] = useState(() => isSoundOn());

  function toggleSound() {
    if (soundOn) {
      setSoundOn(false);
      setSound(false);
    } else {
      // Enabling must happen inside this click so the browser unlocks audio.
      const ok = unlockAudio();
      setSoundOn(true);
      setSound(true);
      // Small confirmation chime so the user knows it works.
      if (ok) playNewOrder();
    }
  }

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
        <button
          type="button"
          className={`bell-btn ${soundOn ? "bell-btn--on" : "bell-btn--off"}`}
          onClick={toggleSound}
          aria-pressed={soundOn}
          title={soundOn ? "Звук включён" : "Включите звук, чтобы слышать оповещения"}
        >
          <span className="bell-btn__icon" aria-hidden="true">
            {soundOn ? "🔔" : "🔕"}
          </span>
          <span className="bell-btn__label">
            {soundOn ? "Звук" : "Включите звук"}
          </span>
        </button>
        {identity && <span className="identity">{identity}</span>}
        <button type="button" className="btn btn--ghost logout-btn" onClick={logout}>
          Выйти
        </button>
        {children}
      </div>
    </header>
  );
}
