import { describe, expect, it } from "vitest";

import { formatSidebarTime, previewText, senderLabel } from "../waSidebar";

describe("formatSidebarTime", () => {
  const now = new Date(2026, 10, 22, 9, 0); // Sun 22 Nov 2026, 09:00 local

  it("shows the clock time for today", () => {
    expect(formatSidebarTime(new Date(2026, 10, 22, 8, 5).toISOString(), now)).toMatch(/08:05|8:05/);
  });

  it("calls last night 'Yesterday', not a clock time", () => {
    // 10 hours ago but a different calendar day — the old 24h diff said "today".
    expect(formatSidebarTime(new Date(2026, 10, 21, 23, 0).toISOString(), now)).toBe("Yesterday");
  });

  it("uses the weekday within the week", () => {
    expect(formatSidebarTime(new Date(2026, 10, 18, 12, 0).toISOString(), now)).toBe(
      new Date(2026, 10, 18).toLocaleDateString(undefined, { weekday: "short" })
    );
  });

  it("uses an unambiguous day and month name, adding the year only across years", () => {
    const sameYear = formatSidebarTime(new Date(2026, 5, 8, 12, 0).toISOString(), now);
    expect(sameYear).toMatch(/8/);
    expect(sameYear).toMatch(/Jun/);
    expect(sameYear).not.toMatch(/26/);
    expect(formatSidebarTime(new Date(2025, 5, 8, 12, 0).toISOString(), now)).toMatch(/25/);
  });

  it("is empty for a missing time", () => {
    expect(formatSidebarTime("", now)).toBe("");
  });
});

describe("previewText", () => {
  it("collapses newlines and runs of spaces", () => {
    expect(previewText("hello\n\n  there\tyou", "text")).toBe("hello there you");
  });

  it("strips WhatsApp bold/italic/strike markers", () => {
    expect(previewText("**Series Updates** _now_ ~old~", "text")).toBe("Series Updates now old");
  });

  it("keeps underscores and asterisks inside words", () => {
    expect(previewText("file_name_v2 and 5*3", "text")).toBe("file_name_v2 and 5*3");
  });

  it("names media without a caption", () => {
    expect(previewText("", "image")).toBe("📷 Photo");
    expect(previewText("", "video")).toBe("🎥 Video");
    expect(previewText("", "audio")).toBe("🎤 Voice note");
    expect(previewText("", "document")).toBe("📄 Document");
    expect(previewText("", "sticker")).toBe("Sticker");
  });

  it("prefixes a caption with the media icon", () => {
    expect(previewText("the invoice", "document")).toBe("📄 the invoice");
  });

  it("never shows [text] for an empty message", () => {
    expect(previewText("", "text")).toBe("Message");
    expect(previewText("", "")).toBe("Message");
  });
});

describe("senderLabel", () => {
  const me = "mary@acme.test";

  it("says You for my own outgoing message", () => {
    expect(senderLabel({ direction: "Outgoing", senderName: "Mary Agent", senderUser: me, me })).toBe("You");
  });

  it("names the teammate by first name", () => {
    expect(
      senderLabel({ direction: "Outgoing", senderName: "Richa Patel", senderUser: "richa@acme.test", me })
    ).toBe("Richa");
  });

  it("labels handset-sent messages as Phone, not Administrator", () => {
    expect(
      senderLabel({ direction: "Outgoing", senderName: "(via phone)", senderUser: "Administrator", me })
    ).toBe("Phone");
  });

  it("falls back to Agent when an outgoing row has no name", () => {
    expect(senderLabel({ direction: "Outgoing", senderName: "", senderUser: "", me })).toBe("Agent");
  });

  it("names the group member who wrote last", () => {
    expect(
      senderLabel({ direction: "Incoming", senderName: "Malanga Okoth", senderUser: "Guest", me, isGroup: true })
    ).toBe("Malanga");
  });

  it("leaves a 1:1 customer message unlabelled — the row title already names them", () => {
    expect(senderLabel({ direction: "Incoming", senderName: "Malanga", senderUser: "Guest", me })).toBe("");
  });
});
