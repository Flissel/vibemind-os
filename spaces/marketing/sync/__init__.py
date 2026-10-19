"""Marketing sync — bi-directional sync between Supabase marketing.* and
~/.rowboat/knowledge/Marketing/People/ markdown files.

Modules:
  render_md   : pure-function Markdown renderer (Phase 2)
  _frontmatter: deterministic YAML frontmatter serialization
  _filename   : handle -> safe filename sanitization
  _queries    : SQL strings (centralised so trigger + render see same shape)
  _db         : thin psycopg/docker-exec wrapper for both worker contexts
  worker_imap_sync: Worker C, IMAP -> marketing.inbound_messages
  inbound_pretag  : Auto-Antwort-Erkennung fuer eingehende Mails

Der Abgleich DB <-> Markdown (Worker A/B, Outbox aus 004) ist seit 076
stillgelegt: er lief nie im Betrieb. Siehe README.md.
"""
