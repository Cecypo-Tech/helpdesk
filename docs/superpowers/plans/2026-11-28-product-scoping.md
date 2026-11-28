# Product scoping (2026-11-28)

Approved by the owner in chat ("yes").

1. Close rule: agent closes also need `hd_product` -> "Pick a product before closing". Same exemptions as work type.
2. `weekly_digest.guess_products()` hourly: open, non-reminder, >1h old, `product_guessed=0` tickets with no product.
   Customer's sole entitlement first; otherwise LLM picks one enabled HD Product by exact name, or none.
   Marks `product_guessed=1` only when the attempt completed (LLM errors retry).
3. Learned notes carry `product` (from `entitlement.resolve_product_for_ticket` at draft, editable). Approve writes
   `HD Ticket Embedding.product` (note's product, else the ticket's current product). Product edit on an Approved
   note re-embeds. `search_resolved_tickets(query, top_k, product=None)` returns only rows whose product equals the
   ticket's product; with no product known, only rows without one. The bot passes the resolved product.

Tests: test_work_types (product required), test_weekly_digest (guess_products), test_learn (product on note,
embedding, poll message), test_embeddings_scope (search filter).
