import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { BASE } from "../lib/api.js";
import { getOrgId } from "../lib/auth.js";
import "../stationmap.css";

const ZONES = [
  { value: "rolls", label: "Роллы" },
  { value: "pizza", label: "Пицца" },
  { value: "hot", label: "Горячее" },
  { value: "other", label: "Прочее" },
];

export default function StationMap() {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  // product_id -> станция (черновик правок)
  const [draft, setDraft] = useState({});
  // Фильтр по названию (меню большое — ~1149 блюд).
  const [query, setQuery] = useState("");

  const showToast = useCallback((msg, ok = true) => {
    setToast({ msg, ok });
    window.clearTimeout(showToast._t);
    showToast._t = window.setTimeout(() => setToast(null), 3500);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${BASE}/admin/stations?org_id=${getOrgId()}`, {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
      const json = await res.json();
      setGroups(json);
      // Инициализируем черновик текущими значениями.
      const init = {};
      for (const g of json) {
        for (const p of g.products) init[p.product_id] = p.station;
      }
      setDraft(init);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function changeZone(productId, station) {
    setDraft((d) => ({ ...d, [productId]: station }));
  }

  async function autoMap() {
    setBusy(true);
    setToast(null);
    try {
      const res = await fetch(`${BASE}/admin/stations/auto?org_id=${getOrgId()}`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
      const json = await res.json();
      showToast(`Размечено ${json.mapped_products} блюд`);
      await load();
    } catch (e) {
      showToast(`Ошибка: ${e.message || e}`, false);
    } finally {
      setBusy(false);
    }
  }

  function changedItems() {
    const out = [];
    for (const g of groups) {
      for (const p of g.products) {
        const next = draft[p.product_id];
        if (next != null && next !== p.station) {
          out.push({ product_id: p.product_id, station: next, name: p.name });
        }
      }
    }
    return out;
  }

  async function save() {
    const items = changedItems();
    if (items.length === 0) {
      showToast("Нет изменений");
      return;
    }
    setBusy(true);
    setToast(null);
    try {
      const res = await fetch(`${BASE}/admin/stations?org_id=${getOrgId()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ items }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
      const json = await res.json();
      showToast(`Сохранено ${json.updated} блюд`);
      await load();
    } catch (e) {
      showToast(`Ошибка: ${e.message || e}`, false);
    } finally {
      setBusy(false);
    }
  }

  const pendingCount = changedItems().length;

  const q = query.trim().toLowerCase();
  const visibleGroups = q
    ? groups
        .map((g) => ({
          ...g,
          products: g.products.filter((p) =>
            (p.name || "").toLowerCase().includes(q),
          ),
        }))
        .filter((g) => g.products.length > 0)
    : groups;

  return (
    <div className="stationmap">
      <div className="stationmap__inner">
        <Link to="/" className="stationmap__back">
          ← На главную
        </Link>
        <h1 className="stationmap__title">Разметка зон (меню)</h1>
        <p className="stationmap__hint">
          Привяжите блюда из меню iiko к зонам кухни, чтобы заказы попадали на
          нужные экраны.
        </p>

        <div className="stationmap__toolbar">
          <button
            type="button"
            className="sm-btn sm-btn--primary"
            onClick={autoMap}
            disabled={busy}
          >
            {busy ? "Выполняется…" : "Авторазметка из меню iiko"}
          </button>
          <button
            type="button"
            className="sm-btn"
            onClick={save}
            disabled={busy || pendingCount === 0}
          >
            Сохранить{pendingCount > 0 ? ` (${pendingCount})` : ""}
          </button>
        </div>

        <input
          type="search"
          className="sm-search"
          placeholder="Поиск блюда по названию…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        {toast && (
          <p className={`sm-toast ${toast.ok ? "sm-toast--ok" : "sm-toast--err"}`}>
            {toast.msg}
          </p>
        )}

        {loading && <p className="stationmap__state">Загрузка…</p>}
        {error && !loading && (
          <p className="stationmap__state stationmap__state--err">
            Ошибка: {error}
          </p>
        )}

        {!loading && !error && groups.length === 0 && (
          <p className="stationmap__state">
            Меню пустое. Запустите авторазметку.
          </p>
        )}

        {!loading && !error && q && visibleGroups.length === 0 && (
          <p className="stationmap__state">Ничего не найдено.</p>
        )}

        {!loading &&
          !error &&
          visibleGroups.map((g) => (
            <section key={g.group} className="sm-group">
              <h2 className="sm-group__title">{g.group}</h2>
              <div className="sm-list">
                {g.products.map((p) => {
                  const current = draft[p.product_id] ?? p.station;
                  const dirty = current !== p.station;
                  return (
                    <div
                      key={p.product_id}
                      className={`sm-row${dirty ? " sm-row--dirty" : ""}`}
                    >
                      <span className="sm-row__name">{p.name}</span>
                      <select
                        className="sm-row__select"
                        value={current}
                        onChange={(e) =>
                          changeZone(p.product_id, e.target.value)
                        }
                        disabled={busy}
                      >
                        {ZONES.map((z) => (
                          <option key={z.value} value={z.value}>
                            {z.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
      </div>
    </div>
  );
}
