import { Link } from "react-router-dom";

export default function ScreenHeader({
  title,
  accent,
  name,
  onNameChange,
  connected,
  children,
}) {
  return (
    <header className="screen-header" style={accent ? { borderColor: accent } : undefined}>
      <div className="screen-header__left">
        <Link to="/" className="back-link" aria-label="На главную">
          ←
        </Link>
        <h1 className="screen-title">{title}</h1>
        <span
          className={`conn-dot ${connected ? "conn-dot--on" : "conn-dot--off"}`}
          title={connected ? "Подключено" : "Нет связи"}
        />
      </div>
      <div className="screen-header__right">
        {onNameChange && (
          <label className="name-field">
            <span>Я:</span>
            <input
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="Имя"
            />
          </label>
        )}
        {children}
      </div>
    </header>
  );
}
