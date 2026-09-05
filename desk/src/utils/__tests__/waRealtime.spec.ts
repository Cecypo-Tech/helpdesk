import { describe, expect, it } from "vitest";

import {
  applyEventToConversationList,
  hasRow,
  rowFromEvent,
  upsertMessage,
  type ConversationRow,
  type WaMessageEvent,
} from "../waRealtime";

function event(overrides: Partial<WaMessageEvent> = {}): WaMessageEvent {
  return {
    ticket: "",
    is_incoming: true,
    origin: "insert",
    phone: "254700000001",
    name: "msg-1",
    type: "Incoming",
    content_type: "text",
    message: "hello there",
    attach: "",
    status: "",
    creation: "2026-09-05 10:00:00.000000",
    profile_name: "Asha",
    message_id: "wamid.1",
    reply_to_message_id: "",
    is_reply: 0,
    sender_full_name: "",
    ...overrides,
  };
}

function row(phone: string, time: string, message = "older"): ConversationRow {
  return { phone, last_message: message, last_message_time: time, last_direction: "Outgoing" };
}

describe("hasRow", () => {
  it("distinguishes the legacy {ticket, is_incoming} shape", () => {
    expect(hasRow({ ticket: "12", is_incoming: true })).toBe(false);
    expect(hasRow(event())).toBe(true);
  });
});

describe("applyEventToConversationList", () => {
  const list = [
    row("254700000009", "2026-09-05 09:59:00.000000"),
    row("254700000001", "2026-09-05 09:00:00.000000"),
  ];

  it("moves the conversation to the top with the new preview", () => {
    const { list: next, handled } = applyEventToConversationList(list, event());
    expect(handled).toBe(true);
    expect(next.map((c) => c.phone)).toEqual(["254700000001", "254700000009"]);
    expect(next[0]).toMatchObject({
      last_message: "hello there",
      last_message_time: "2026-09-05 10:00:00.000000",
      last_direction: "Incoming",
    });
  });

  it("does not mutate the list it was given", () => {
    applyEventToConversationList(list, event());
    expect(list[0].phone).toBe("254700000009");
    expect(list[1].last_message).toBe("older");
  });

  it("asks for a refetch when the phone is not loaded", () => {
    const { handled } = applyEventToConversationList(list, event({ phone: "254799999999" }));
    expect(handled).toBe(false);
  });

  it("asks for a refetch on the legacy event shape", () => {
    const { handled } = applyEventToConversationList(list, { ticket: "1", is_incoming: true });
    expect(handled).toBe(false);
  });

  it("ignores reactions without refetching", () => {
    const { list: next, handled } = applyEventToConversationList(
      list,
      event({ content_type: "reaction", message: "👍" })
    );
    expect(handled).toBe(true);
    expect(next).toBe(list);
  });

  it("uses the server's fallback preview for media without a caption", () => {
    const { list: next } = applyEventToConversationList(
      list,
      event({ content_type: "image", message: "" })
    );
    expect(next[0].last_message).toBe("[image]");
  });

  it("never rolls the preview back to an older message", () => {
    const fresh = [row("254700000001", "2026-09-05 11:00:00.000000", "newest")];
    const { list: next } = applyEventToConversationList(fresh, event());
    expect(next[0].last_message).toBe("newest");
  });
});

describe("upsertMessage", () => {
  const thread = [
    { name: "a", creation: "2026-09-05 09:00:00.000000", message: "first", message_id: "wamid.a" },
  ];

  it("appends a new message in chronological position", () => {
    const next = upsertMessage(thread, event());
    expect(next.map((m) => m.name)).toEqual(["a", "msg-1"]);
    expect(next[1]).toMatchObject({ type: "Incoming", direction: "Incoming", media_url: "" });
  });

  it("sorts an event older than the tail into place", () => {
    const next = upsertMessage(thread, event({ creation: "2026-09-05 08:00:00.000000" }));
    expect(next.map((m) => m.name)).toEqual(["msg-1", "a"]);
  });

  it("updates an existing row by name without blanking fields the event lacks", () => {
    const withMedia = [
      { name: "msg-1", creation: "x", content_type: "image", attach: "/files/pic.jpg", status: "received" },
    ];
    const next = upsertMessage(withMedia, event({ content_type: "image", attach: "", status: "" }));
    expect(next).toHaveLength(1);
    expect(next[0].attach).toBe("/files/pic.jpg");
    expect(next[0].status).toBe("received");
  });

  it("resolves an optimistic bubble by message_id instead of duplicating it", () => {
    const pending = [
      { name: "temp-1", _optimistic: true, message_id: "wamid.1", creation: "x", status: "Pending" },
    ];
    const next = upsertMessage(pending, event({ status: "sent" }));
    expect(next).toHaveLength(1);
    expect(next[0].name).toBe("msg-1");
    expect(next[0].status).toBe("sent");
    expect(next[0]._optimistic).toBeUndefined();
  });

  it("leaves the thread alone for the legacy event shape", () => {
    expect(upsertMessage(thread, { ticket: "1", is_incoming: true })).toBe(thread);
  });
});

describe("rowFromEvent", () => {
  it("fills the fields WhatsAppBubble reads", () => {
    const r = rowFromEvent(event({ name: "n", is_incoming: false, type: "Outgoing", sender_full_name: "Kush" }));
    expect(r).toMatchObject({
      name: "n",
      type: "Outgoing",
      direction: "Outgoing",
      sender_full_name: "Kush",
      edit_history: [],
      is_deleted: 0,
    });
  });
});
