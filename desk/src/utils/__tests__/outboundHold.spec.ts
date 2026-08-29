import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OutboundHold, type HeldPayload } from "../outboundHold";

const TICKET = "TKT-1";

describe("OutboundHold", () => {
  let sent: HeldPayload[];

  function makeHold(holdMs = 5000) {
    sent = [];
    return new OutboundHold({ holdMs, onFlush: (p) => sent.push(p) });
  }

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends one message when the window closes", () => {
    const hold = makeHold();
    hold.append("first", TICKET);

    expect(sent).toEqual([]);
    vi.advanceTimersByTime(5000);
    expect(sent).toEqual([{ ticket: TICKET, message: "first", reply_to_message_id: "" }]);
  });

  it("merges messages typed inside the window into a single send", () => {
    const hold = makeHold();
    hold.append("checking now", TICKET);
    vi.advanceTimersByTime(2000);
    hold.append("give me a second", TICKET);

    vi.advanceTimersByTime(3000);
    expect(sent).toEqual([
      { ticket: TICKET, message: "checking now\ngive me a second", reply_to_message_id: "" },
    ]);
  });

  // The window opens on the first message and closes holdMs later, full stop.
  // Resetting on each append would let a chatty agent hold a customer's reply
  // open indefinitely; a fixed window keeps worst-case latency predictable.
  it("does not extend the window when more text is appended", () => {
    const hold = makeHold();
    hold.append("one", TICKET);
    vi.advanceTimersByTime(4000);
    hold.append("two", TICKET);

    vi.advanceTimersByTime(1000);
    expect(sent).toHaveLength(1);
    expect(sent[0].message).toBe("one\ntwo");
  });

  it("starts a fresh window for text typed after a flush", () => {
    const hold = makeHold();
    hold.append("first", TICKET);
    vi.advanceTimersByTime(5000);
    hold.append("second", TICKET);
    vi.advanceTimersByTime(5000);

    expect(sent.map((s) => s.message)).toEqual(["first", "second"]);
  });

  it("sends immediately when flushed by hand", () => {
    const hold = makeHold();
    hold.append("urgent", TICKET);
    hold.flush();

    expect(sent).toEqual([{ ticket: TICKET, message: "urgent", reply_to_message_id: "" }]);

    // The pending timer must not fire a second copy.
    vi.advanceTimersByTime(5000);
    expect(sent).toHaveLength(1);
  });

  it("is a no-op when flushed with nothing held", () => {
    const hold = makeHold();
    hold.flush();
    expect(sent).toEqual([]);
  });

  // A hold of zero is the kill switch: the setting can disable the whole
  // feature without a deploy, and sends must go straight out.
  it("sends synchronously when the hold is disabled", () => {
    const hold = makeHold(0);
    hold.append("straight through", TICKET);
    expect(sent).toEqual([
      { ticket: TICKET, message: "straight through", reply_to_message_id: "" },
    ]);
  });

  it("keeps the quote target of the first message in the window", () => {
    const hold = makeHold();
    hold.append("part one", TICKET, "wamid.AAA");
    hold.append("part two", TICKET, "wamid.BBB");
    hold.flush();

    expect(sent).toEqual([
      { ticket: TICKET, message: "part one\npart two", reply_to_message_id: "wamid.AAA" },
    ]);
  });

  it("drops held text on cancel without sending it", () => {
    const hold = makeHold();
    hold.append("never mind", TICKET);
    hold.cancel();

    vi.advanceTimersByTime(5000);
    expect(sent).toEqual([]);
    expect(hold.pending).toBe("");
  });

  it("exposes what is held so the composer can show it", () => {
    const hold = makeHold();
    expect(hold.isHolding).toBe(false);

    hold.append("visible", TICKET);
    expect(hold.pending).toBe("visible");
    expect(hold.isHolding).toBe(true);

    hold.flush();
    expect(hold.pending).toBe("");
    expect(hold.isHolding).toBe(false);
  });

  it("reports a deadline so the composer can count down", () => {
    const hold = makeHold();
    expect(hold.deadline).toBeNull();

    const before = Date.now();
    hold.append("tick", TICKET);
    expect(hold.deadline).toBe(before + 5000);
  });

  it("ignores blank text", () => {
    const hold = makeHold();
    hold.append("   ", TICKET);
    expect(hold.isHolding).toBe(false);
    vi.advanceTimersByTime(5000);
    expect(sent).toEqual([]);
  });

  // Route changes and unmounts call destroy(); anything still held belongs to
  // the customer, so it must go out rather than vanish with the component.
  it("flushes held text on destroy", () => {
    const hold = makeHold();
    hold.append("in flight", TICKET);
    hold.destroy();

    expect(sent).toEqual([{ ticket: TICKET, message: "in flight", reply_to_message_id: "" }]);
  });

  // The chat tab is mounted without a :key, so Vue can reuse the component
  // across a ticket switch and just swap the prop. If the ticket were read when
  // the timer fires rather than captured on append, held text would be
  // delivered to whichever conversation the agent moved to — a message sent to
  // the wrong customer.
  it("sends held text to the ticket it was typed against", () => {
    const hold = makeHold();
    hold.append("for the first customer", "TKT-1");
    hold.append("for the second customer", "TKT-2");

    // Switching conversations closes the open window rather than merging.
    expect(sent).toEqual([
      {
        ticket: "TKT-1",
        message: "for the first customer",
        reply_to_message_id: "",
      },
    ]);

    vi.advanceTimersByTime(5000);
    expect(sent[1]).toEqual({
      ticket: "TKT-2",
      message: "for the second customer",
      reply_to_message_id: "",
    });
  });

  it("applies a new hold duration to the next window", () => {
    const hold = makeHold();
    hold.setHoldMs(1000);
    hold.append("quick", TICKET);

    vi.advanceTimersByTime(1000);
    expect(sent).toHaveLength(1);
  });
});
