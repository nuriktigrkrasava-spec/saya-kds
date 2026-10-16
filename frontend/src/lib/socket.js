import { io } from "socket.io-client";
import { BASE } from "./api.js";
import { getOrgId, getSession, isAuthed } from "./auth.js";

// Singleton socket connected to the backend (default Socket.IO path).
export const socket = io(BASE, {
  autoConnect: false,
  auth: (callback) => callback({ token: getSession()?.token || "" }),
  transports: ["websocket", "polling"],
});

/**
 * Join the given zones for the demo org. Re-joins automatically on every
 * (re)connect so a dropped connection doesn't silently stop updates.
 * Returns a cleanup function that removes the connect handler.
 */
export function joinZones(zones) {
  const list = Array.isArray(zones) ? zones : [zones];

  const doJoin = () => {
    const org_id = getOrgId();
    for (const zone of list) {
      socket.emit("join", { org_id, zone });
    }
  };

  if (isAuthed() && !socket.connected) socket.connect();
  if (socket.connected) doJoin();
  socket.on("connect", doJoin);

  return () => {
    socket.off("connect", doJoin);
  };
}

window.addEventListener("saya-session-changed", () => {
  socket.disconnect();
  if (isAuthed()) socket.connect();
});
