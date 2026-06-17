// Хранилище сессии сотрудника (вход по PIN-коду).
const KEY = "saya_session";

export function saveSession(data) {
  try {
    const session = {
      token: data.token,
      name: data.name,
      role: data.role,
      branch_code: data.branch_code,
      branch_name: data.branch_name,
      org_id: data.org_id,
    };
    localStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* ignore */
  }
}

export function getSession() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export function getOrgId() {
  const s = getSession();
  return (s && s.org_id) || "demo";
}

export function getOperatorName() {
  const s = getSession();
  return (s && s.name) || "";
}

export function isAuthed() {
  return !!getSession();
}

// ── Роли ────────────────────────────────────────────────────────────────────
export const MANAGER_ROLES = ["MN0", "Администратор", "УПРЭКСПО", "ADM", "ADM2"];

// Код роли -> экран, куда направить при входе.
const ROLE_ROUTE = {
  // роллы (экран «Роллы/Горячее» показывает и горячее)
  "СУШ": "/rolls", "СЭКСПО12": "/rolls", "СЭКСПО13": "/rolls", "СуШеф": "/rolls",
  "CO1": "/rolls", // повар (горячее) — тот же объединённый экран
  // пицца
  "ПИЦЦ": "/pizza", "ПЭКСПО13": "/pizza",
};

export function getRole() {
  const s = getSession();
  return (s && s.role) || "";
}

export function isManager() {
  return MANAGER_ROLES.includes(getRole());
}

export function routeForRole(role) {
  if (MANAGER_ROLES.includes(role)) return "/";
  return ROLE_ROUTE[role] || "/";
}
