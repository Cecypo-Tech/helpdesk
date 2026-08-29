// Briefly holding outgoing WABA text so an agent's chunked reply bills once.
//
// From 1 October 2026 Meta bills every business message sent inside the 24-hour
// service window, not just templates. Agents habitually split one thought across
// several bubbles — on a 30-day sample, 305 outgoing messages left within 30
// seconds of the previous one — and each of those bubbles is now its own charge.
//
// Holding the first message for a few seconds and appending anything typed
// during that window turns a burst into a single send. The window opens on the
// first message and closes holdMs later without extending, so the worst-case
// delay a customer sees is fixed rather than something a fast typist can stretch.
//
// Deliberately framework-free: the composer owns the display, this owns the
// timing. WABA only — the Evolution (WA Line) path is not billed by Meta, so
// there is no reason to add latency to it.

export interface HeldPayload {
  /**
   * The ticket the text was typed against, captured on append rather than read
   * at flush time. The chat tab is mounted without a :key, so Vue may reuse the
   * component across a ticket switch and simply swap the prop — reading the
   * ticket when the timer fires would deliver a held message to whichever
   * conversation the agent happened to move to.
   */
  ticket: string;
  message: string;
  reply_to_message_id: string;
}

export interface OutboundHoldOptions {
  holdMs: number;
  onFlush: (payload: HeldPayload) => void;
}

export class OutboundHold {
  private holdMs: number;
  private readonly onFlush: (payload: HeldPayload) => void;

  private parts: string[] = [];
  private ticket = "";
  private replyToMessageId = "";
  private timer: ReturnType<typeof setTimeout> | null = null;
  private deadlineAt: number | null = null;

  constructor(options: OutboundHoldOptions) {
    this.holdMs = options.holdMs;
    this.onFlush = options.onFlush;
  }

  /** Text the agent has sent that has not gone out yet. */
  get pending(): string {
    return this.parts.join("\n");
  }

  get isHolding(): boolean {
    return this.parts.length > 0;
  }

  /** Epoch ms the held text goes out, for the composer's countdown. */
  get deadline(): number | null {
    return this.deadlineAt;
  }

  /** Takes effect on the next window; a window already open keeps its deadline. */
  setHoldMs(ms: number): void {
    this.holdMs = ms;
  }

  append(text: string, ticket: string, replyToMessageId = ""): void {
    const trimmed = text.trim();
    if (!trimmed) return;

    // A hold of zero is the setting's kill switch — send straight through.
    if (this.holdMs <= 0) {
      this.onFlush({
        ticket,
        message: trimmed,
        reply_to_message_id: replyToMessageId,
      });
      return;
    }

    // Text belonging to a different conversation can never be merged into this
    // one, so a ticket switch closes the open window instead of extending it.
    if (this.parts.length && ticket !== this.ticket) this.flush();

    // The quote target belongs to the message that opened the window; a later
    // chunk is a continuation of that reply, not a reply to something else.
    if (!this.parts.length) {
      this.ticket = ticket;
      this.replyToMessageId = replyToMessageId;
    }
    this.parts.push(trimmed);

    if (this.timer === null) {
      this.deadlineAt = Date.now() + this.holdMs;
      this.timer = setTimeout(() => this.flush(), this.holdMs);
    }
  }

  /** Send whatever is held right now. */
  flush(): void {
    const message = this.pending;
    const ticket = this.ticket;
    const replyTo = this.replyToMessageId;
    this.reset();
    if (!message) return;
    this.onFlush({ ticket, message, reply_to_message_id: replyTo });
  }

  /** Drop held text without sending it. */
  cancel(): void {
    this.reset();
  }

  /**
   * Held text belongs to the customer, so tearing down the composer sends it
   * rather than discarding it.
   */
  destroy(): void {
    this.flush();
  }

  private reset(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.parts = [];
    this.ticket = "";
    this.replyToMessageId = "";
    this.deadlineAt = null;
  }
}
