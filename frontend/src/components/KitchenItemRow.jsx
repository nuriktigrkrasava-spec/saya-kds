import { useState } from "react";
import { api } from "../lib/api.js";
import { zoneColor, zoneLabel } from "../lib/zones.js";
import {
  formatElapsed,
  overdueLevel,
  cookProgress,
  parseServerTime,
} from "../lib/time.js";

export default function KitchenItemRow({ item, name }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const color = zoneColor(item.station);

  // Server time drives the stopwatch so it is identical on every tablet and
  // survives reloads. Falls back to "now" only if accepted but timestamp absent.
  let timerText = null;
  let level = "ok";
  let fill = 0;
  let overdue = false;
  const cooking = item.accepted && !item.ready;
  if (cooking) {
    const acceptedAt = parseServerTime(item.accepted_at) ?? Date.now();
    const elapsed = Date.now() - acceptedAt;
    timerText = formatElapsed(elapsed);
    level = overdueLevel(elapsed);
    fill = cookProgress(elapsed);
    overdue = level === "overdue";
  }

  async function accept() {
    setBusy(true);
    setErr(null);
    try {
      await api.acceptItem(item.id, name || "—");
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
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
        </span>

        <div className="kitem__action">
          {item.ready ? (
            <span className="badge badge--ready">Готово ✓</span>
          ) : !item.accepted ? (
            <button className="btn btn--accept" onClick={accept} disabled={busy}>
              Принял
            </button>
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
          <span
            className="cookbar__fill"
            style={{ width: `${fill * 100}%` }}
          />
        </div>
      )}

      {err && <div className="kitem__err">{err}</div>}
    </div>
  );
}
