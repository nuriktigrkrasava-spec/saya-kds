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
