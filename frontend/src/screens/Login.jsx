import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { saveSession } from "../lib/auth.js";
import "../login.css";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "del", "0", "ok"];

export default function Login() {
  const navigate = useNavigate();
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function press(key) {
    setError("");
    if (key === "del") {
      setPin((p) => p.slice(0, -1));
      return;
    }
    if (key === "ok") {
      submit();
      return;
    }
    if (pin.length >= 12) return;
    setPin((p) => p + key);
  }

  async function submit() {
    if (!pin || busy) return;
    setBusy(true);
    setError("");
    try {
      const resp = await api.login(pin);
      saveSession(resp);
      navigate("/", { replace: true });
    } catch {
      setError("Неверный PIN");
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login__card">
        <h1 className="login__title">Вход</h1>
        <p className="login__subtitle">Введите свой PIN-код</p>

        <div className="login__display" aria-label="PIN">
          {pin.length === 0 ? (
            <span className="login__placeholder">····</span>
          ) : (
            pin.split("").map((_, i) => (
              <span key={i} className="login__dot" />
            ))
          )}
        </div>

        <p className={`login__error ${error ? "login__error--show" : ""}`}>
          {error || " "}
        </p>

        <div className="login__pad">
          {KEYS.map((k) => {
            if (k === "del") {
              return (
                <button
                  key={k}
                  type="button"
                  className="login__key login__key--del"
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
                  className="login__key login__key--ok"
                  onClick={() => press(k)}
                  disabled={busy || !pin}
                >
                  {busy ? "…" : "Войти"}
                </button>
              );
            }
            return (
              <button
                key={k}
                type="button"
                className="login__key"
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
