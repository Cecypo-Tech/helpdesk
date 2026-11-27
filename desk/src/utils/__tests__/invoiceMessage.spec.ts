import { describe, expect, it } from "vitest";

import { invoiceMessage } from "../invoiceMessage";

const inv = (name: string, date: string, amt: number, overdue = 0) => ({
  name,
  posting_date: date,
  outstanding_amount: amt,
  days_overdue: overdue,
});

describe("invoiceMessage", () => {
  const snap = {
    currency: "KES",
    customer_name: "ACME Ltd",
    outstanding_total: 1994,
    overdue_total: 834,
    invoices: [
      inv("INV-3", "2026-11-13", 1160),
      inv("INV-1", "2026-07-07", 306, 113),
      inv("INV-2", "2026-07-17", 528, 103),
    ],
  };

  it("lists unpaid invoices oldest first, marks overdue, totals", () => {
    expect(invoiceMessage(snap)).toBe(
      [
        "ACME Ltd – outstanding invoices",
        "INV-1 · 07 Jul · KES 306 · overdue",
        "INV-2 · 17 Jul · KES 528 · overdue",
        "INV-3 · 13 Nov · KES 1,160",
        "Total KES 1,994 (KES 834 overdue)",
      ].join("\n")
    );
  });

  it("skips paid rows and caps at 10 with a +N more line", () => {
    const many = Array.from({ length: 13 }, (_, i) =>
      inv(`INV-${String(i).padStart(2, "0")}`, `2026-08-${String(i + 1).padStart(2, "0")}`, 100)
    );
    const text = invoiceMessage({ ...snap, overdue_total: 0, invoices: [...many, inv("PAID", "2026-01-01", 0)] });
    expect(text).not.toContain("PAID");
    expect(text).toContain("INV-09");
    expect(text).not.toContain("INV-10");
    expect(text).toContain("+3 more");
    expect(text.split("\n").pop()).toBe("Total KES 1,994");
  });
});
