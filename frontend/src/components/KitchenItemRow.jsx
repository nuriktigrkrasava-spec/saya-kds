import { useState } from "react";
import { api } from "../lib/api.js";
import Icon from "./Icon.jsx";
import { zoneColor, zoneLabel } from "../lib/zones.js";
import {
  formatElapsed,
  overdueLevel,
  cookProgress,
  parseServerTime,
} from "../lib/time.js";

export default function KitchenItemRow({ item, name, orderCreatedAt }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const color = zoneColor(item.station);

  // Шага «Принял» нет: таймер идёт от появления заказа (created_at) и до «Готово».
  let timerText = null;
  let level = "ok";
  let fill = 0;
  let overdue = false;
  const cooking = !item.ready;
  if (cooking) {
    const base = parseServerTime(orderCreatedAt) ?? Date.now();
    const elapsed = Date.now() - base;
    timerText = formatElapsed(elapsed);
    level = overdueLevel(elapsed);
    fill = cookProgress(elapsed);
    overdue = level === "overdue";
  }

  async function ready() {
    setBusy(true);
    setErr(null);
    try {
      await api.readyItem(item.id, name || "—");
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const cls = [
    "kitem",
    item.ready ? "kitem--ready" : "",
    cooking ? "kitem--cooking" : "",
    overdue ? "kitem--overdue" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} style={{ "--zone": color }}>
      <div className="kitem__main">
        <span
          className="kitem__dot"
          style={{ background: color }}
          title={zoneLabel(item.station)}
        />
        <span className="kitem__name">
          {item.name}
          <span className="kitem__amount"> ×{item.amount}</span>
          {item.comment && (
            <span className="kitem__note">
              <Icon name="note" size={15} />
              {item.comment}
            </span>
          )}
          {(item.modifiers || []).length > 0 && (
            // Состав сета: раньше повар читал его с бумажного чека, теперь он
            // здесь. Отдельных отметок готовности у вложенных позиций нет —
            // готовность ставится по блюду целиком.
            <span className="kitem__mods">
              {(item.modifiers || []).map((m, i) => (
                <span key={i} className="kitem__mod">
                  {m.name}
                  {m.amount > 1 && <b> ×{m.amount}</b>}
                </span>
              ))}
            </span>
          )}
        </span>

        <div className="kitem__action">
          {item.ready ? (
            <span className="badge badge--ready">
              <Icon name="check" size={16} />
              Готово
            </span>
          ) : (
            <>
              <span className={`timer timer--${level}`}>{timerText}</span>
              <button className="btn btn--ready" onClick={ready} disabled={busy}>
                Готово
              </button>
            </>
          )}
        </div>
      </div>

      {cooking && (
        <div
          className={`cookbar cookbar--${level}`}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fill * 100)}
        >
          <span className="cookbar__fill" style={{ width: `${fill * 100}%` }} />
        </div>
      )}

      {err && <div className="kitem__err">{err}</div>}
    </div>
  );
}
