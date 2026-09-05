import { describe, expect, it } from "vitest";

import {
  applyResolve,
  makePendingBubble,
  markRetrying,
  mergeThread,
  removePending,
  upsertPending,
} from "../waOptimistic";

const base = [
  { name: "a", creation: "2026-09-05 09:00:00.000000", message: "first", message_id: "wamid.a", type: "Incoming" },
];

function bubble(name = "temp-1", message = "on my way") {
  const b = makePendingBubble({ name, content_type: "text", message, sender_full_name: "Kush" });
  b.creation = "2026-09-05 10:00:00.000000";
  return b;
}

describe("makePendingBubble", () => {
  it("renders as an outgoing message that is still sending", () => {
    const b = makePendingBubble({
      name: "temp-9",
      content_type: "image",
      message: "caption",
      attach: "blob:x",
      reply_to_message_id: "wamid.q",
    });
    expect(b).toMatchObject({
      _optimistic: true,
      type: "Outgoing",
      status: "Pending",
      attach: "blob:x",
      media_url: "blob:x",
      is_reply: 1,
      reply_to_message_id: "wamid.q",
    });
  });
});

describe("upsertPending", () => {
  it("replaces a bubble of the same name so held text can grow", () => {
    const one = upsertPending([], bubble("t", "first"));
    const two = upsertPending(one, bubble("t", "first\nsecond"));
    expect(two).toHaveLength(1);
    expect(two[0].message).toBe("first\nsecond");
  });
});

describe("mergeThread", () => {
  it("appends pending bubbles after the fetched rows", () => {
    const merged = mergeThread(base, [bubble()]);
    expect(merged.map((m) => m.name)).toEqual(["a", "temp-1"]);
  });

  it("returns the base untouched when nothing is pending", () => {
    expect(mergeThread(base, [])).toBe(base);
  });

  it("drops a pending bubble the fetched rows already cover", () => {
    const stored = [...base, { name: "temp-1", creation: "x" }];
    expect(mergeThread(stored, [bubble()])).toBe(stored);
  });
});

describe("applyResolve", () => {
  it("moves a stored message into the thread under its real name", () => {
    const { base: b2, pending } = applyResolve(base, [bubble()], {
      name: "temp-1",
      realName: "real-1",
      message_id: "",
      status: "Success",
    });
    expect(pending).toEqual([]);
    expect(b2.map((m) => m.name)).toEqual(["a", "real-1"]);
    expect(b2[1]._optimistic).toBeUndefined();
    expect(b2[1].status).toBe("Success");
  });

  it("does not duplicate a row the realtime event already delivered", () => {
    const already = [...base, { name: "real-1", creation: "2026-09-05 10:00:00.100000", status: "sent" }];
    const { base: b2, pending } = applyResolve(already, [bubble()], {
      name: "temp-1",
      realName: "real-1",
      message_id: "",
      status: "Success",
    });
    expect(pending).toEqual([]);
    expect(b2).toBe(already);
  });

  it("keeps a failed send pending, marked Failed", () => {
    const { base: b2, pending } = applyResolve(base, [bubble()], {
      name: "temp-1",
      realName: "",
      message_id: "",
      status: "Failed",
    });
    expect(b2).toBe(base);
    expect(pending).toHaveLength(1);
    expect(pending[0].status).toBe("Failed");
    expect(pending[0]._optimistic).toBe(true);
  });

  it("ignores a payload for a bubble it does not hold", () => {
    const out = applyResolve(base, [], { name: "nope", realName: "r", message_id: "", status: "Success" });
    expect(out.base).toBe(base);
  });
});

describe("markRetrying / removePending", () => {
  it("flips a failed bubble back to Pending", () => {
    const failed = [{ ...bubble(), status: "Failed" }];
    expect(markRetrying(failed, "temp-1")[0].status).toBe("Pending");
  });

  it("removes by name", () => {
    expect(removePending([bubble("x"), bubble("y")], "x").map((p) => p.name)).toEqual(["y"]);
  });
});
