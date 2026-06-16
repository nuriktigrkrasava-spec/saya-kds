import { useState } from "react";

// Persisted operator name, used as the `by` field in API calls.
export function useName(key = "saya_kds_name", fallback = "") {
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem(key) || fallback;
    } catch {
      return fallback;
    }
  });

  const update = (v) => {
    setName(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      /* ignore */
    }
  };

  return [name, update];
}
