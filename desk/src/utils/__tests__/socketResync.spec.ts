import { describe, expect, it, vi } from "vitest";

import { watchResync } from "../socketResync";

function fakeSocket() {
  const handlers: Record<string, Array<(...a: any[]) => void>> = {};
  return {
    on(event: string, h: (...a: any[]) => void) {
      (handlers[event] ||= []).push(h);
    },
    off(event: string, h: (...a: any[]) => void) {
      handlers[event] = (handlers[event] || []).filter((x) => x !== h);
    },
    emit(event: string) {
      for (const h of handlers[event] || []) h();
    },
    count(event: string) {
      return (handlers[event] || []).length;
    },
  };
}

function fakeDocument(state = "visible") {
  const handlers: Array<() => void> = [];
  return {
    visibilityState: state,
    addEventListener(_: string, h: () => void) {
      handlers.push(h);
    },
    removeEventListener(_: string, h: () => void) {
      const i = handlers.indexOf(h);
      if (i !== -1) handlers.splice(i, 1);
    },
    change(state: string) {
      this.visibilityState = state;
      for (const h of handlers) h();
    },
    count() {
      return handlers.length;
    },
  };
}

describe("watchResync", () => {
  it("does not fire on the initial connect", () => {
    const socket = fakeSocket();
    const resync = vi.fn();
    watchResync(socket, resync, fakeDocument());
    socket.emit("connect");
    expect(resync).not.toHaveBeenCalled();
  });

  it("fires once per reconnect after a disconnect", () => {
    const socket = fakeSocket();
    const resync = vi.fn();
    watchResync(socket, resync, fakeDocument());
    socket.emit("connect");
    socket.emit("disconnect");
    socket.emit("connect");
    socket.emit("connect");
    expect(resync).toHaveBeenCalledTimes(1);
    socket.emit("disconnect");
    socket.emit("connect");
    expect(resync).toHaveBeenCalledTimes(2);
  });

  it("fires when the tab becomes visible, not when it hides", () => {
    const socket = fakeSocket();
    const doc = fakeDocument();
    const resync = vi.fn();
    watchResync(socket, resync, doc);
    doc.change("hidden");
    expect(resync).not.toHaveBeenCalled();
    doc.change("visible");
    expect(resync).toHaveBeenCalledTimes(1);
  });

  it("dispose removes every listener", () => {
    const socket = fakeSocket();
    const doc = fakeDocument();
    const resync = vi.fn();
    const handle = watchResync(socket, resync, doc);
    handle.dispose();
    expect(socket.count("connect")).toBe(0);
    expect(socket.count("disconnect")).toBe(0);
    expect(doc.count()).toBe(0);
    socket.emit("disconnect");
    socket.emit("connect");
    doc.change("visible");
    expect(resync).not.toHaveBeenCalled();
  });

  it("works without a document", () => {
    const socket = fakeSocket();
    const resync = vi.fn();
    const handle = watchResync(socket, resync, null);
    socket.emit("disconnect");
    socket.emit("connect");
    expect(resync).toHaveBeenCalledTimes(1);
    handle.dispose();
  });
});
