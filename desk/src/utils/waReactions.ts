// Folding WhatsApp reaction rows into per-message badges.
//
// Reactions arrive as standalone message rows rather than as a field on the
// message they target, and both integrations append rather than overwrite: a
// replaced reaction is a new row, and a *cleared* one is a row with an empty
// body. WhatsApp's own model is one reaction per sender per message, latest
// wins — so the rendered state is only correct after folding the whole history
// per (target, sender).

export interface WaReactionRow {
  content_type?: string;
  reply_to_message_id?: string;
  message?: string;
  type?: string;
  creation?: string;
  sender_jid?: string;
  jid?: string;
  sender_full_name?: string;
  sender_name?: string;
  profile_name?: string;
}

export interface FoldedReaction {
  emoji: string;
  type: string;
  sender: string;
}

// Every agent reacts through the same WhatsApp line, so WhatsApp itself sees one
// sender for all outgoing reactions — collapsing them to a single key is what
// makes "clear my reaction" resolve to the right row.
const OWN = "__me__";

function senderKeyOf(row: WaReactionRow): string {
  if (row.type === "Outgoing") return OWN;
  // WABA rows carry no sender_jid; in a 1:1 chat the jid identifies the customer.
  return row.sender_jid || row.jid || "__them__";
}

function senderNameOf(row: WaReactionRow): string {
  // `sender_full_name` joins on the row's owner, which is Administrator for
  // anything inbound — so it only names the sender on outgoing rows.
  if (row.type === "Outgoing") {
    return row.sender_full_name || row.sender_name || "You";
  }
  return row.profile_name || row.sender_name || "Customer";
}

function isNewer(row: WaReactionRow, than: WaReactionRow): boolean {
  const a = new Date(row.creation || 0).getTime();
  const b = new Date(than.creation || 0).getTime();
  // Ties resolve to the later row in iteration order, which is creation-ordered.
  return a >= b;
}

/**
 * Fold reaction rows into a map of target message_id → badges to render.
 * Rows may be in any order and may include non-reaction messages.
 */
export function foldReactions(
  rows: WaReactionRow[]
): Record<string, FoldedReaction[]> {
  const latest: Record<string, Record<string, WaReactionRow>> = {};

  for (const row of rows ?? []) {
    if (row.content_type !== "reaction" || !row.reply_to_message_id) continue;
    const target = row.reply_to_message_id;
    const key = senderKeyOf(row);
    const bucket = (latest[target] ||= {});
    const prev = bucket[key];
    if (!prev || isNewer(row, prev)) bucket[key] = row;
  }

  const map: Record<string, FoldedReaction[]> = {};
  for (const target of Object.keys(latest)) {
    const badges = Object.values(latest[target])
      // An empty body is a removal, and it supersedes the emoji before it.
      .filter((row) => !!row.message)
      .map((row) => ({
        emoji: row.message as string,
        type: row.type || "Incoming",
        sender: senderNameOf(row),
      }));
    if (badges.length) map[target] = badges;
  }
  return map;
}
