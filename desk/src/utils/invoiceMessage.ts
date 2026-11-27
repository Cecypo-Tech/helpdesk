// Short WhatsApp-sized summary of a customer's unpaid invoices, from powerpack's snapshot.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MAX_LINES = 10;

const amount = (cur: string, v: number) =>
  `${cur}${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(v || 0)}`;

// "2026-07-07" -> "07 Jul"; no Date() so the time zone can't shift the day.
const shortDate = (d: string) => {
  const [, m, day] = (d || "").split("-");
  return m ? `${day} ${MONTHS[Number(m) - 1]}` : d || "";
};

export function invoiceMessage(snap: any, name?: string): string {
  const cur = snap.currency ? `${snap.currency} ` : "";
  const unpaid = (snap.invoices || [])
    .filter((i: any) => i.outstanding_amount > 0)
    .sort((a: any, b: any) => (a.posting_date < b.posting_date ? -1 : 1));
  const lines = unpaid.slice(0, MAX_LINES).map(
    (i: any) =>
      `${i.name} · ${shortDate(i.posting_date)} · ${amount(cur, i.outstanding_amount)}` +
      (i.days_overdue > 0 ? " · overdue" : "")
  );
  if (unpaid.length > MAX_LINES) lines.push(`+${unpaid.length - MAX_LINES} more`);
  const overdue = snap.overdue_total > 0 ? ` (${amount(cur, snap.overdue_total)} overdue)` : "";
  return [
    `${name || snap.customer_name} – outstanding invoices`,
    ...lines,
    `Total ${amount(cur, snap.outstanding_total)}${overdue}`,
  ].join("\n");
}
