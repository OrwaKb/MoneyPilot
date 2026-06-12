# MoneyPilot browsable chat sessions (2026-06-12)

**Why.** The advisor used to be one endless `chat_history` thread. This adds
named, browsable conversations with memory scoped to the active chat.

**Model.** A new `conversations(id, title, created_at)` table; `chat_history`
gains `conversation_id INTEGER REFERENCES conversations(id)`. Conversations are
created **lazily** — `advisor.chat()` with `conversation_id=None` opens one,
titled from the first message (first line, whitespace-collapsed, 48 chars max,
`…` if truncated). Continuations pass the returned id.

**Context window.** The advisor's "memory" is per-conversation: history sent to
Claude is `recent_chat(conn, 20, cid)[:-1]` — the last 20 messages of the
*active* chat only, never the whole history.

**Listing.** `list_conversations` LEFT JOINs `chat_history`, returning
`id, title, created_at, last_ts (MAX(ts) or created_at), msg_count`, ordered by
last activity DESC (tie-break `MAX(chat id)` so same-second touches still sort
by true recency).

**Delete.** Hard delete — `delete_conversation` drops the messages then the row
(chats aren't money; nothing to soft-keep). UI confirms in two steps.

**Migration v1→v2 (first real schema migration).** `SCHEMA_VERSION=2`. Fresh
DBs are born v2 (the `INSERT OR IGNORE` stamp writes '2', migration is a no-op).
Legacy files: `init_db` runs `_migrate()` after schema/seed/stamp — if
`schema_version < 2`, it `ALTER TABLE`s in `conversation_id` (when missing),
and if any chat rows exist creates one `Earlier conversation`
(created_at = oldest message ts) and assigns every orphan row to it, then bumps
the version. Idempotent: the version gate makes a second `init_db` a no-op.

**Backups.** `_EXPORT_TABLES` lists `conversations` immediately before
`chat_history` (FK parent first); the reversed delete in `import_json` then
removes children before parents — conversations and their messages ride
export/import intact.
