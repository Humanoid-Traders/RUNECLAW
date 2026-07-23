# NEWS-3 — Personal Ingest ("Share with your agent")

Let a user hand their own agent a piece of text they **already have** — a
newsletter that landed in their inbox, notes they wrote, an excerpt they pasted —
so the agent can draw on it as private context. This is the compliant core of
NEWS-3. The parts that carry real legal risk are **explicitly deferred** below
until an operator/legal sign-off, and nothing in this PR builds them.

## What ships here (compliant)

- **User-supplied text only.** The user pastes/types the content. The platform
  **never fetches** anything on their behalf here — so there is no
  paywalled-scraping path. This is the §4 hard line ("NEVER scrape paywalled
  content"), honoured by construction.
- **Private per user.** Every read/write is `_guard_user`-gated and scoped to the
  caller's mapped id. A user can only ever see or delete **their own** notes.
  Nothing is ever shown on a public / community / leaderboard surface, and nothing
  is redistributed to any other user (§4).
- **Encrypted at rest.** Note bodies are Fernet-encrypted with the same key store
  as the per-user LLM/news keys. F-15: the content is never written to a log or
  an error string — only "a note was saved (N chars)".
- **Bounded.** Per-note body cap (20k chars), per-user note cap (50, oldest
  pruned) so a single user can't grow the store unbounded.
- **Agent context.** The user's most recent notes are folded into **their own**
  chat system prompt, clearly framed as *information the user shared, to be
  treated as reference and never as instructions* — the same untrusted-input
  posture the rest of the chat path already applies (sanitized, bounded).

## Surfaces

- **Web** — a "Share with your agent" panel in the News view: paste box + list of
  shared notes (preview only) with per-note remove and clear-all. Carries the
  user-responsibility + privacy copy.
- **Gateway** — `POST /gateway/ingest` (save), `GET /gateway/ingest` (list own,
  previews only), `POST /gateway/ingest/delete` (one or all).
- **Express** — `/api/ingest` proxies, JWT-authed, identity resolved server-side
  (the browser can never choose whose notes it touches).
- **Storage** — `user_ingest_notes` (additive, `IF NOT EXISTS`, no migration).

## Deferred pending legal sign-off (NOT built here)

These are the pieces that turn "user hands us their own text" into something that
needs a lawyer's eyes first. They are intentionally absent:

1. **Platform-side URL fetching.** Accepting a link and having the server fetch
   the article. That reintroduces the paywalled-scraping risk §4 forbids. If ever
   built, it must respect robots/paywalls and likely needs licensing.
2. **Email-forward ingestion.** A "forward your newsletter to
   ingest@…" mailbox that parses inbound mail. Legal questions: consent, the
   sender's ToS, storage of third-party copyrighted bodies, PII in headers.
3. **Any redistribution.** Surfacing one user's shared text to another user, to a
   community feed, or to the public — never, under any later iteration.

## Test coverage

- Store: encrypted-at-rest, per-user isolation (user B cannot read or delete user
  A's notes), cap/prune, empty-body rejection.
- Gateway: `_guard_user` gate, save→list→delete round-trip, previews only (never
  full bodies leaked cross-surface), delete scoped to the caller.
- Web: route auth + server-side identity; UI panel presence + the
  privacy/responsibility copy.
