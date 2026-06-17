import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BASE } from "../lib/api.js";
import "../branches.css";

export default function Branches() {
  const [branches, setBranches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${BASE}/rms/branches`, {
          headers: { Accept: "application/json" },
        });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status} ${res.statusText}`.trim());
        }
        const data = await res.json();
        if (!cancelled) setBranches(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const totalEmployees = useMemo(
    () =>
      branches.reduce(
        (sum, b) => sum + (b.employees ? b.employees.length : 0),
        0
      ),
    [branches]
  );

  const search = query.trim().toLowerCase();
  const searching = search.length > 0;

  // Список филиалов с отфильтрованными (при поиске) сотрудниками.
  const visible = useMemo(() => {
    if (!searching) {
      return branches.map((b) => ({ ...b, _shown: b.employees || [] }));
    }
    return branches
      .map((b) => {
        const shown = (b.employees || []).filter((e) =>
          (e.name || "").toLowerCase().includes(search)
        );
        return { ...b, _shown: shown };
      })
      .filter((b) => b._shown.length > 0);
  }, [branches, searching, search]);

  function toggle(key) {
    setOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function isExpanded(key) {
    // При поиске филиалы с совпадениями раскрываются автоматически.
    if (searching) return true;
    return !!open[key];
  }

  if (loading) {
    return (
      <div className="branches">
        <div className="branches__inner">
          <TopBar />
          <div className="branches__state">
            <div className="branches__spinner" aria-hidden />
            <p>Загружаем филиалы с сервера…</p>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="branches">
        <div className="branches__inner">
          <TopBar />
          <div className="branches__state branches__state--err">
            <p>Не удалось загрузить данные</p>
            <p className="branches__err-detail">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="branches">
      <div className="branches__inner">
        <TopBar />

        <p className="branches__summary">
          {branches.length} филиалов · {totalEmployees} сотрудников
        </p>

        <div className="branches__search">
          <input
            type="text"
            className="branches__search-input"
            placeholder="Поиск сотрудника по имени…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="branches__search-clear"
              onClick={() => setQuery("")}
              aria-label="Очистить"
            >
              ×
            </button>
          )}
        </div>

        {visible.length === 0 ? (
          <div className="branches__empty">Ничего не найдено</div>
        ) : (
          <div className="branches__list">
            {visible.map((b) => {
              const key = b.id || b.code || b.name;
              const expanded = isExpanded(key);
              const count = (b._shown || []).length;
              return (
                <div className="branch-card" key={key}>
                  <button
                    type="button"
                    className="branch-card__header"
                    onClick={() => toggle(key)}
                    aria-expanded={expanded}
                  >
                    <span
                      className={`branch-card__chevron ${
                        expanded ? "is-open" : ""
                      }`}
                      aria-hidden
                    >
                      ▸
                    </span>
                    <span className="branch-card__name">{b.name}</span>
                    <span className="branch-card__count">{count}</span>
                  </button>

                  {expanded && (
                    <div className="branch-card__body">
                      {count === 0 ? (
                        <div className="branch-card__empty">
                          Нет сотрудников
                        </div>
                      ) : (
                        b._shown.map((e, i) => (
                          <div className="emp-row" key={e.code || e.name || i}>
                            <span className="emp-row__name">{e.name}</span>
                            {e.code && (
                              <span className="emp-row__pin">PIN {e.code}</span>
                            )}
                            {e.role && (
                              <span className="emp-row__role">{e.role}</span>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function TopBar() {
  return (
    <div className="branches__top">
      <Link to="/" className="branches__back">
        ← Назад
      </Link>
      <h1 className="branches__title">Филиалы и сотрудники</h1>
    </div>
  );
}
