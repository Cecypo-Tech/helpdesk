// Formatting for a WhatsApp sidebar row, shared by the WA Line and WABA lists.
// Framework-free so it can be unit tested.

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** "14:02" today, "Yesterday", "Mon" within the week, then "8 Jun" / "8 Jun 25". Calendar days, not 24h windows. */
export function formatSidebarTime(ts: string, now: Date = new Date()): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (days <= 0) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "Yesterday";
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "2-digit" } : {}),
  });
}

/** Full date and time for the row's tooltip. */
export function fullTimestamp(ts: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const MEDIA: Record<string, [icon: string, label: string]> = {
  image: ["📷", "Photo"],
  video: ["🎥", "Video"],
  audio: ["🎤", "Voice note"],
  document: ["📄", "Document"],
  sticker: ["", "Sticker"],
};

/** One-line-friendly preview: whitespace collapsed, WhatsApp markers stripped, media named. */
export function previewText(message: string, contentType: string): string {
  const text = (message || "")
    // *bold* / **bold**, _italic_, ~strike~ — only when wrapping a word, so file_name and 5*3 survive.
    .replace(/(^|[\s(])([*_~]{1,2})(\S(?:.*?\S)?)\2(?=[\s.,!?:;)]|$)/g, "$1$3")
    .replace(/\s+/g, " ")
    .trim();
  const media = MEDIA[contentType];
  if (media) {
    const [icon, label] = media;
    if (!text) return icon ? `${icon} ${label}` : label;
    return icon ? `${icon} ${text}` : text;
  }
  return text || "Message";
}

/**
 * Who sent the last message, for a "Name:" prefix. Empty for an incoming 1:1
 * message — the row title already names the customer.
 *
 * The name comes from the stored sender name, not the record owner: messages
 * sent from the handset are owned by Administrator and named "(via phone)".
 */
export function senderLabel(opts: {
  direction: string;
  senderName?: string;
  senderUser?: string;
  me?: string;
  isGroup?: boolean;
}): string {
  const name = (opts.senderName || "").trim();
  const first = name.split(/\s+/)[0] || "";
  if (opts.direction === "Outgoing") {
    if (name === "(via phone)") return "Phone";
    if (opts.me && opts.senderUser === opts.me) return "You";
    return first || "Agent";
  }
  return opts.isGroup ? first : "";
}
