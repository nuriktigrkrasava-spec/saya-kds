import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";

// Зона планшета -> зона прогноза. Пицца и горячее — одна бригада, один планшет.
const ZONE_BY_STATION = { rolls: "japan", pizza: "pizza_hot", hot: "pizza_hot" };

const POLL_MS = 5 * 60 * 1000;

/**
 * Подсказка сушисту за полчаса до наплыва: чего и сколько заготовить.
 *
 * Считается по продажам за месяц; состав сетов туда входит модификаторами,
 * поэтому роллы внутри сетов учтены. В спокойный час плашки нет — дёргать
 * повара просто так значит добиться, чтобы её перестали читать.
 */
// Посмотреть плашку, не дожидаясь часа пик: ?prep=19 — час, ?prep_day=1 —
// день недели (0 понедельник). Без них работает как обычно.
function previewFromUrl() {
  if (typeof window === "undefined") return null;
  const q = new URLSearchParams(window.location.search);
  const hour = q.get("prep");
  if (hour == null) return null;
  const day = q.get("prep_day");
  return { hour: Number(hour), weekday: day == null ? null : Number(day) };
}

export default function PrepHint({ stations }) {
  const [hint, setHint] = useState(null);
  const preview = previewFromUrl();
  const zone = ZONE_BY_STATION[(stations || [])[0]] || "japan";
  const orgId = getOrgId();

  useEffect(() => {
    if (!orgId) return;
    let alive = true;
    const load = () =>
      api
        .prepHint(orgId, zone, preview)
        .then((r) => alive && setHint(r?.hint || null))
        .catch(() => alive && setHint(null));
    load();
    const t = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, zone]);

  if (!hint) return null;

  return (
    <div className="prep-hint">
      <div className="prep-hint__head">
        Через полчаса наплыв · обычно к {String(hint.hour).padStart(2, "0")}:00
      </div>
      <div className="prep-hint__list">
        {hint.dishes.map((d) => (
          <span key={d.dish} className="prep-hint__dish">
            {d.dish} <b>~{d.amount}</b>
          </span>
        ))}
      </div>
      <div className="prep-hint__foot">Заготовьте заранее</div>
    </div>
  );
}
