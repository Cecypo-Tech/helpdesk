// Applying a `helpdesk:whatsapp-message` event without refetching.
//
// The event used to be `{ticket, is_incoming}` and every open client answered
// it by refetching the whole thread, the ticket info and the first page of the
// conversation list — per message, per agent. It now carries the stored row
// (see wa._fw_event_payload), so the list can move one row and the thread can
// append one bubble. Refetching is kept as reconciliation, not as the path.
//
// Framework-free so it can be unit tested; the components own the reactivity.

export interface WaMessageEvent {
  ticket: string;
  is_incoming: boolean;
  /** "insert": fired the moment a row is stored. "ingest": fired by the job once a ticket link exists. */
  origin?: "insert" | "ingest";
  phone?: string;
  name?: string;
  type?: "Incoming" | "Outgoing";
  content_type?: string;
  message?: string;
  attach?: string;
  status?: string;
  creation?: string;
  profile_name?: string;
  message_id?: string;
  reply_to_message_id?: string;
  is_reply?: number;
  sender_full_name?: string;
}

export interface ConversationRow {
  phone: string;
  last_message: string;
  last_message_time: string;
  last_direction: string;
  [key: string]: any;
}

/** An event carrying the row itself, as opposed to the legacy `{ticket, is_incoming}` shape. */
export function hasRow(ev: WaMessageEvent): ev is WaMessageEvent & { name: string; phone: string } {
  return Boolean(ev && ev.name && ev.phone);
}

/** The preview the list shows — same fallback the server uses. */
export function previewFor(ev: WaMessageEvent): string {
  return ev.message || `[${ev.content_type || "text"}]`;
}

/**
 * Move the event's conversation to the top with the new last message.
 *
 * Returns `handled: false` when the phone is not in the list — the caller
 * should refetch the first page, since the row may be new or on a page that
 * was never loaded. Reactions are handled (ignored): a thumbs-up is not the
 * last thing said and the server leaves it out of the ranking too.
 */
export function applyEventToConversationList(
  list: ConversationRow[],
  ev: WaMessageEvent
): { list: ConversationRow[]; handled: boolean } {
  if (!hasRow(ev)) return { list, handled: false };
  if (ev.content_type === "reaction") return { list, handled: true };

  const idx = list.findIndex((c) => c.phone === ev.phone);
  if (idx === -1) return { list, handled: false };

  const row = list[idx];
  // The "ingest" event repeats a row the "insert" event already applied; an
  // older event (out-of-order delivery) must not roll the preview back.
  if (row.last_message_time && ev.creation && ev.creation < row.last_message_time) {
    return { list, handled: true };
  }

  const updated: ConversationRow = {
    ...row,
    last_message: previewFor(ev),
    last_message_time: ev.creation || row.last_message_time,
    last_direction: ev.type || (ev.is_incoming ? "Incoming" : "Outgoing"),
  };
  const rest = list.filter((_, i) => i !== idx);
  return { list: [updated, ...rest], handled: true };
}

/** The row shape WhatsAppBubble renders, built from the event. */
export function rowFromEvent(ev: WaMessageEvent & { name: string }): Record<string, any> {
  const type = ev.type || (ev.is_incoming ? "Incoming" : "Outgoing");
  return {
    name: ev.name,
    creation: ev.creation || new Date().toISOString(),
    type,
    direction: type,
    message: ev.message || "",
    content_type: ev.content_type || "text",
    attach: ev.attach || "",
    media_url: ev.attach || "",
    status: ev.status || "",
    profile_name: ev.profile_name || "",
    message_id: ev.message_id || "",
    reply_to_message_id: ev.reply_to_message_id || "",
    is_reply: ev.is_reply || 0,
    sender_full_name: ev.sender_full_name || "",
    reference_name: ev.ticket || "",
    edit_history: [],
    is_edited: 0,
    edit_unrecoverable: 0,
    is_deleted: 0,
  };
}

/**
 * Insert or update one message in a chronologically ordered thread.
 *
 * Matches on `name`, then on `message_id` so an optimistic bubble that has been
 * resolved to its real identity is updated rather than duplicated. Fields the
 * event does not carry are kept from the existing row.
 */
export function upsertMessage(
  rows: Record<string, any>[],
  ev: WaMessageEvent
): Record<string, any>[] {
  if (!hasRow(ev)) return rows;
  const incoming = rowFromEvent(ev);
  const idx = rows.findIndex(
    (m) => m.name === incoming.name || (incoming.message_id && m.message_id === incoming.message_id)
  );
  if (idx !== -1) {
    const merged = { ...rows[idx] };
    for (const [key, value] of Object.entries(incoming)) {
      // An empty attach on the "insert" event must not blank one the row has.
      if (value === "" || value === 0) continue;
      merged[key] = value;
    }
    merged.name = incoming.name;
    delete merged._optimistic;
    const next = rows.slice();
    next[idx] = merged;
    return next;
  }
  const next = [...rows, incoming];
  next.sort((a, b) => String(a.creation).localeCompare(String(b.creation)));
  return next;
}
