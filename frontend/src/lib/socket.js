import { io } from "socket.io-client";
import { BASE, ORG_ID } from "./api.js";

// Singleton socket connected to the backend (default Socket.IO path).
export const socket = io(BASE, {
  autoConnect: true,
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
    for (const zone of list) {
      socket.emit("join", { org_id: ORG_ID, zone });
    }
  };

  if (socket.connected) doJoin();
  socket.on("connect", doJoin);

  return () => {
    socket.off("connect", doJoin);
  };
}
