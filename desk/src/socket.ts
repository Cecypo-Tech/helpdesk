import { io } from "socket.io-client";
import { socketio_port } from "../../../../sites/common_site_config.json";

// extend window object
declare global {
  interface Window {
    site_name: string;
  }
}

export function initSocket() {
  let host = window.location.hostname;
  let siteName = window.site_name || host;
  let port = window.location.port ? `:${socketio_port}` : "";
  let protocol = port ? "http" : "https";
  let url = `${protocol}://${host}${port}/${siteName}`;

  const socket = io(url, {
    withCredentials: true,
    // Never give up. With a finite cap a laptop that slept or a tunnel that
    // blipped left the desk permanently deaf: no new messages, no list
    // updates, until a hard refresh. Backoff is capped so recovery is prompt;
    // utils/socketResync refetches once the socket is back.
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelayMax: 10000,
  });

  return socket;
}

export const socket = initSocket();
