// Refetch after the moments a realtime client silently falls behind.
//
// Socket events are the only thing that keeps a chat current, and there are two
// ordinary ways to miss them: the socket drops (laptop sleep, tunnel blip) and
// events emitted meanwhile are gone, or the tab is in the background and the
// browser throttles it. Neither announces itself. This registers one callback
// for both: a `connect` that follows a `disconnect`, and the tab becoming
// visible again.
//
// Framework-free: the socket is anything with `on`/`off`, the document is
// anything with `addEventListener` and `visibilityState`, so it unit tests
// without a DOM.

interface Emitter {
  on(event: string, handler: (...args: any[]) => void): any;
  off(event: string, handler: (...args: any[]) => void): any;
}

interface VisibilityDocument {
  visibilityState: string;
  addEventListener(event: string, handler: () => void): void;
  removeEventListener(event: string, handler: () => void): void;
}

export interface ResyncHandle {
  dispose(): void;
}

export function watchResync(
  socket: Emitter,
  onResync: () => void,
  doc: VisibilityDocument | null = typeof document !== "undefined" ? document : null
): ResyncHandle {
  let wasDisconnected = false;

  const onDisconnect = () => {
    wasDisconnected = true;
  };
  // The first `connect` is the initial handshake, not a recovery; nothing was
  // missed and the component has just fetched.
  const onConnect = () => {
    if (!wasDisconnected) return;
    wasDisconnected = false;
    onResync();
  };
  const onVisibility = () => {
    if (doc && doc.visibilityState === "visible") onResync();
  };

  socket.on("disconnect", onDisconnect);
  socket.on("connect", onConnect);
  doc?.addEventListener("visibilitychange", onVisibility);

  return {
    dispose() {
      socket.off("disconnect", onDisconnect);
      socket.off("connect", onConnect);
      doc?.removeEventListener("visibilitychange", onVisibility);
    },
  };
}
