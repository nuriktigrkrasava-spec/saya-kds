import { useState } from "react";
import { api } from "../lib/api.js";
import { zoneColor, zoneLabel } from "../lib/zones.js";
import { formatElapsed, overdueLevel } from "../lib/time.js";
import {
  markAccepted,
  getAcceptedAt,
  clearAccepted,
} from "../lib/acceptTimes.js";

export default function KitchenItemRow({ item, name }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const color = zoneColor(item.station);
  const acceptedAt = item.accepted ? getAcceptedAt(item.id) : null;

  let timerText = null;
  let level = "ok";
  if (item.accepted && !item.ready) {
    const base = acceptedAt || Date.now();
    const elapsed = Date.now() - base;
    timerText = formatElapsed(elapsed);
    level = overdueLevel(elapsed);
  }

  async function accept() {
    setBusy(true);
    setErr(null);
    try {
      await api.acceptItem(item.id, name || "—");
      markAccepted(item.id);
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
      clearAccepted(item.id);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`kitem ${item.ready ? "kitem--ready" : ""}`}>
      <span className="kitem__dot" style={{ background: color }} title={zoneLabel(item.station)} />
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
      {err && <div className="kitem__err">{err}</div>}
    </div>
  );
}
