// Pending bubbles for the WhatsApp Business (WABA) composer.
//
// The agent's message used to appear only once Meta had answered and the
// thread had refetched. A pending bubble goes in the moment they hit send and
// is resolved to the stored row's identity when the request returns — or kept,
// marked Failed with a Retry, when it does not.
//
// Pending bubbles live apart from the fetched thread so a reload between send
// and response cannot drop them; `mergeThread` folds them in for rendering and
// drops any that the fetched rows already cover.

export interface PendingBubble {
  name: string;
  _optimistic: true;
  type: "Outgoing";
  direction: "Outgoing";
  content_type: string;
  message: string;
  attach: string;
  media_url: string;
  status: string;
  message_id: string;
  creation: string;
  is_reply: number;
  reply_to_message_id: string;
  sender_full_name: string;
  profile_name: string;
  [key: string]: any;
}

export interface ResolvePayload {
  name: string;
  realName: string;
  message_id: string;
  status: string;
}

export function makePendingBubble(input: {
  name: string;
  content_type: string;
  message: string;
  attach?: string;
  reply_to_message_id?: string;
  sender_full_name?: string;
}): PendingBubble {
  return {
    name: input.name,
    _optimistic: true,
    type: "Outgoing",
    direction: "Outgoing",
    content_type: input.content_type,
    message: input.message,
    attach: input.attach || "",
    media_url: input.attach || "",
    status: "Pending",
    message_id: "",
    creation: new Date().toISOString(),
    is_reply: input.reply_to_message_id ? 1 : 0,
    reply_to_message_id: input.reply_to_message_id || "",
    sender_full_name: input.sender_full_name || "",
    profile_name: "",
  };
}

function byCreation(a: Record<string, any>, b: Record<string, any>): number {
  return String(a.creation).localeCompare(String(b.creation));
}

function coveredBy(rows: Record<string, any>[], bubble: Record<string, any>): boolean {
  return rows.some(
    (m) =>
      m.name === bubble.name ||
      (bubble.message_id && m.message_id === bubble.message_id)
  );
}

/** Insert or replace a pending bubble by name. */
export function upsertPending(pending: PendingBubble[], bubble: PendingBubble): PendingBubble[] {
  const idx = pending.findIndex((p) => p.name === bubble.name);
  if (idx === -1) return [...pending, bubble];
  const next = pending.slice();
  next[idx] = { ...pending[idx], ...bubble };
  return next;
}

export function removePending(pending: PendingBubble[], name: string): PendingBubble[] {
  return pending.filter((p) => p.name !== name);
}

/** The fetched thread plus whatever is still pending, in order, without doubles. */
export function mergeThread(
  base: Record<string, any>[],
  pending: PendingBubble[]
): Record<string, any>[] {
  const extra = pending.filter((p) => !coveredBy(base, p));
  if (!extra.length) return base;
  return [...base, ...extra].sort(byCreation);
}

/**
 * The send has returned. A stored row moves from `pending` into `base` under
 * its real name (unless the realtime event already put it there); a failed
 * send stays pending, marked Failed, so the bubble can offer Retry.
 */
export function applyResolve(
  base: Record<string, any>[],
  pending: PendingBubble[],
  payload: ResolvePayload
): { base: Record<string, any>[]; pending: PendingBubble[] } {
  const idx = pending.findIndex((p) => p.name === payload.name);
  if (idx === -1) return { base, pending };
  const bubble = pending[idx];

  if (String(payload.status).toLowerCase() === "failed" || !payload.realName) {
    const next = pending.slice();
    next[idx] = { ...bubble, status: "Failed" };
    return { base, pending: next };
  }

  const resolved: Record<string, any> = {
    ...bubble,
    name: payload.realName,
    message_id: payload.message_id || bubble.message_id,
    status: payload.status || "Success",
  };
  delete resolved._optimistic;

  const rest = removePending(pending, payload.name);
  if (coveredBy(base, resolved)) return { base, pending: rest };
  return { base: [...base, resolved].sort(byCreation), pending: rest };
}

/** Put a failed bubble back to Pending for another attempt. */
export function markRetrying(pending: PendingBubble[], name: string): PendingBubble[] {
  return pending.map((p) => (p.name === name ? { ...p, status: "Pending" } : p));
}
