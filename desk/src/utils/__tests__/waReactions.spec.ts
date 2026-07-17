import { describe, expect, it } from "vitest";

import { foldReactions, type WaReactionRow } from "../waReactions";

const TARGET = "msg-1";

// Rows as the API returns them: reactions are standalone messages, ordered by creation.
function reaction(over: Partial<WaReactionRow> = {}): WaReactionRow {
  return {
    content_type: "reaction",
    reply_to_message_id: TARGET,
    message: "👍",
    type: "Incoming",
    creation: "2026-07-17 10:00:00",
    sender_jid: "254700000001@s.whatsapp.net",
    ...over,
  };
}

describe("foldReactions", () => {
  it("renders a single reaction as one badge", () => {
    const map = foldReactions([reaction()]);
    expect(map[TARGET]).toEqual([
      { emoji: "👍", type: "Incoming", sender: "Customer" },
    ]);
  });

  it("ignores non-reaction rows", () => {
    const map = foldReactions([
      { content_type: "text", message: "hello", reply_to_message_id: TARGET },
      { content_type: "image", message: "", reply_to_message_id: TARGET },
    ]);
    expect(map).toEqual({});
  });

  it("ignores reactions with no target", () => {
    expect(foldReactions([reaction({ reply_to_message_id: "" })])).toEqual({});
  });

  // The bug: reacting 👍 then ❤️ used to render both badges.
  it("replaces a sender's earlier reaction instead of showing both", () => {
    const map = foldReactions([
      reaction({ message: "👍", creation: "2026-07-17 10:00:00" }),
      reaction({ message: "❤️", creation: "2026-07-17 10:00:05" }),
    ]);
    expect(map[TARGET]).toHaveLength(1);
    expect(map[TARGET][0].emoji).toBe("❤️");
  });

  // The bug: a cleared reaction left the superseded badge on screen forever.
  it("clears a reaction when a later empty row supersedes it", () => {
    const map = foldReactions([
      reaction({ message: "👍", creation: "2026-07-17 10:00:00" }),
      reaction({ message: "", creation: "2026-07-17 10:00:05" }),
    ]);
    expect(map[TARGET]).toBeUndefined();
  });

  it("keeps a reaction that was cleared and then re-added", () => {
    const map = foldReactions([
      reaction({ message: "👍", creation: "2026-07-17 10:00:00" }),
      reaction({ message: "", creation: "2026-07-17 10:00:05" }),
      reaction({ message: "🎉", creation: "2026-07-17 10:00:09" }),
    ]);
    expect(map[TARGET]).toHaveLength(1);
    expect(map[TARGET][0].emoji).toBe("🎉");
  });

  it("resolves latest by creation, not by array position", () => {
    const map = foldReactions([
      reaction({ message: "❤️", creation: "2026-07-17 10:00:05" }),
      reaction({ message: "👍", creation: "2026-07-17 10:00:00" }),
    ]);
    expect(map[TARGET][0].emoji).toBe("❤️");
  });

  it("keeps reactions from different senders side by side", () => {
    const map = foldReactions([
      reaction({ message: "👍", sender_jid: "a@s.whatsapp.net" }),
      reaction({ message: "❤️", sender_jid: "b@s.whatsapp.net" }),
    ]);
    expect(map[TARGET]).toHaveLength(2);
    expect(map[TARGET].map((r) => r.emoji)).toEqual(
      expect.arrayContaining(["👍", "❤️"])
    );
  });

  it("keeps the same emoji from two senders as two badges", () => {
    const map = foldReactions([
      reaction({ message: "👍", sender_jid: "a@s.whatsapp.net", profile_name: "Ann" }),
      reaction({ message: "👍", sender_jid: "b@s.whatsapp.net", profile_name: "Bob" }),
    ]);
    expect(map[TARGET]).toHaveLength(2);
    expect(map[TARGET].map((r) => r.sender).sort()).toEqual(["Ann", "Bob"]);
  });

  it("clearing one sender leaves the other sender's reaction", () => {
    const map = foldReactions([
      reaction({ message: "👍", sender_jid: "a@s.whatsapp.net", creation: "2026-07-17 10:00:00" }),
      reaction({ message: "❤️", sender_jid: "b@s.whatsapp.net", creation: "2026-07-17 10:00:01" }),
      reaction({ message: "", sender_jid: "a@s.whatsapp.net", creation: "2026-07-17 10:00:05" }),
    ]);
    expect(map[TARGET]).toHaveLength(1);
    expect(map[TARGET][0].emoji).toBe("❤️");
  });

  // All agents share one WhatsApp line, so WhatsApp sees a single outgoing sender.
  it("treats every outgoing reaction as the same sender", () => {
    const map = foldReactions([
      reaction({ message: "👍", type: "Outgoing", sender_jid: "", creation: "2026-07-17 10:00:00" }),
      reaction({ message: "❤️", type: "Outgoing", sender_jid: "", creation: "2026-07-17 10:00:05" }),
    ]);
    expect(map[TARGET]).toHaveLength(1);
    expect(map[TARGET][0]).toEqual({ emoji: "❤️", type: "Outgoing", sender: "You" });
  });

  it("keeps an outgoing and an incoming reaction apart", () => {
    const map = foldReactions([
      reaction({ message: "👍", type: "Outgoing", sender_jid: "" }),
      reaction({ message: "👍", type: "Incoming", sender_jid: "a@s.whatsapp.net" }),
    ]);
    expect(map[TARGET]).toHaveLength(2);
    expect(map[TARGET].filter((r) => r.type === "Outgoing")).toHaveLength(1);
  });

  it("names an outgoing reactor by their user full name", () => {
    const map = foldReactions([
      reaction({ type: "Outgoing", sender_jid: "", sender_full_name: "Ann Agent" }),
    ]);
    expect(map[TARGET][0].sender).toBe("Ann Agent");
  });

  // sender_full_name joins on the row's owner, which is Administrator for
  // inbound rows — using it on an incoming reaction would misname the customer.
  it("names an incoming reactor by profile name, never by the row owner", () => {
    const map = foldReactions([
      reaction({
        type: "Incoming",
        profile_name: "Jane Customer",
        sender_full_name: "Administrator",
      }),
    ]);
    expect(map[TARGET][0].sender).toBe("Jane Customer");
  });

  it("falls back to sender_name when profile name is absent", () => {
    const map = foldReactions([
      reaction({ type: "Incoming", profile_name: "", sender_name: "Jane" }),
    ]);
    expect(map[TARGET][0].sender).toBe("Jane");
  });

  // WABA rows carry no sender_jid; the jid identifies the customer in a 1:1 chat.
  it("falls back to jid when sender_jid is absent (WABA rows)", () => {
    const map = foldReactions([
      { content_type: "reaction", reply_to_message_id: TARGET, message: "👍",
        type: "Incoming", creation: "2026-07-17 10:00:00", jid: "254700000001@c.us" },
      { content_type: "reaction", reply_to_message_id: TARGET, message: "❤️",
        type: "Incoming", creation: "2026-07-17 10:00:05", jid: "254700000001@c.us" },
    ]);
    expect(map[TARGET]).toHaveLength(1);
    expect(map[TARGET][0].emoji).toBe("❤️");
  });

  it("folds each target message independently", () => {
    const map = foldReactions([
      reaction({ reply_to_message_id: "msg-a", message: "👍" }),
      reaction({ reply_to_message_id: "msg-b", message: "❤️" }),
    ]);
    expect(map["msg-a"][0].emoji).toBe("👍");
    expect(map["msg-b"][0].emoji).toBe("❤️");
  });

  it("handles empty and missing input", () => {
    expect(foldReactions([])).toEqual({});
    expect(foldReactions(undefined as unknown as WaReactionRow[])).toEqual({});
  });
});
