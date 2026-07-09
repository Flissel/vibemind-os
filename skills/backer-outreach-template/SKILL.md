---
name: backer-outreach-template
description: Render crowdfunding backer-outreach messages per channel (email, linkedin-dm, x-dm, discord-dm). Used by spaces/marketing/workers/batch_sender.py — V1 is placeholder substitution over the markdown templates in templates/; V2 will add LLM persona-adaptation. Trigger when composing or editing backer outreach copy.
---

# Backer-Outreach-Template

Renders the outreach message that carries a personal payment link to a
potential backer. One template per channel under `templates/<channel>.md`;
`email.md` is the fallback for unknown channels.

## Placeholders (V1 contract)

| Placeholder | Source |
|---|---|
| `{{recipient_name}}` | recipient_id local-part (before @) |
| `{{recipient_id}}` | ledger recipient_id |
| `{{approve_url}}` | unique payment link (payment-infra) |
| `{{amount}}` / `{{currency}}` | batch amount (server-side) |
| `{{message}}` | batch.message_template (usually the bubble description) |

## Rules

- The payment link is PERSONAL (one order per recipient) — never reuse
  a link across recipients, never shorten/obscure it.
- Amount is stated verbatim; no "choose your amount" wording in V1.
- Keep channel limits in mind: X-DM ~10k chars, Discord 2000; email free.
- V2 (planned): LLM pass that adapts tone per recipient/persona — the
  contract above stays stable.
