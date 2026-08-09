import { useEffect, useState } from "react";
import Icon from "./Icon.jsx";
import { audioHealthy, unlockAudio } from "../lib/sound.js";

// Как часто проверяем, жив ли звук. Планшет мог уснуть, браузер — приостановить
// AudioContext, повар — перезагрузить вкладку. Всё это чинится одним касанием.
const WATCHDOG_MS = 5000;

/**
 * Полноэкранная блокирующая плашка на случай мёртвого звука.
 *
 * Смысл: экран физически не может работать молча. Пока AudioContext не в
 * состоянии running, заказы под плашкой не видны и не нажимаются — единственное
 * доступное действие это коснуться экрана, что и разблокирует звук.
 */
export default function AudioGuard() {
  const [ok, setOk] = useState(() => audioHealthy());

  useEffect(() => {
    const id = setInterval(() => setOk(audioHealthy()), WATCHDOG_MS);
    return () => clearInterval(id);
  }, []);

  function fix() {
    unlockAudio();
    // resume() асинхронный — перечитываем состояние следующим тиком.
    setTimeout(() => setOk(audioHealthy()), 100);
  }

  if (ok) return null;

  return (
    <div className="audio-guard" onClick={fix} role="alert">
      <Icon name="mute" size={112} className="audio-guard__icon" />
      <div className="audio-guard__text">ЗВУК НЕ РАБОТАЕТ</div>
      <div className="audio-guard__hint">КОСНИСЬ ЭКРАНА</div>
    </div>
  );
}
