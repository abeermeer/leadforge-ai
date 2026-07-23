# Phase 2 — Live-Fire Runbook

Everything before this point is theory. Nothing in the test suite proves the
system works against reality: SendGrid, the AI providers, and the scrapers are
**all mocked**. This runbook is the sequence that turns "the tests pass" into
"a client can use it".

**This phase cannot be automated by the agent.** It needs a domain you own, DNS
records only you can set, real API keys, and messages sent to real people. Every
step below is yours to run. Record the results in the tables — the numbers are
the deliverable, not the checkmarks.

---

## 2.1 Sending infrastructure (do this first, it takes 24-48h to propagate)

Without these three records your mail lands in spam regardless of copy quality,
and no application change fixes it.

**Use a subdomain, never your corporate root.** If outreach burns the domain's
reputation, you want `out.trax9.com` burned — not `trax9.com`, which carries
your invoices and client mail.

| Record | Where | Value |
|---|---|---|
| **SPF** | DNS TXT on `out.trax9.com` | `v=spf1 include:sendgrid.net ~all` |
| **DKIM** | SendGrid → Settings → Sender Authentication → Authenticate Your Domain | CNAMEs SendGrid generates (use automated security) |
| **DMARC** | DNS TXT on `_dmarc.out.trax9.com` | `v=DMARC1; p=none; rua=mailto:dmarc@trax9.com` |

Start DMARC at `p=none` (monitor only). Move to `p=quarantine` after two clean
weeks of reports — going straight to `p=reject` while misconfigured silently
kills your own mail.

**Verify before sending anything:**

```bash
dig +short TXT out.trax9.com
dig +short TXT _dmarc.out.trax9.com
```

Then send one message to a Gmail address you own, open **Show original**, and
confirm `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`. Do not proceed until all three
pass.

**Warmup.** The platform ramps automatically (`WARMUP_START_CAP=10`,
`+5/day`). Do not raise it. A cold domain sending 200 messages on day one is
permanently burned — there is no recovery, you buy a new domain.

---

## 2.2 The 20-lead run

Configure in **Settings**: SendGrid key, `from_email` on the authenticated
subdomain, real postal address, daily cap **20**.

Run one campaign against real businesses, then fill this in from
`/api/metrics/summary` and your own inbox:

| Question | How to check | Result |
|---|---|---|
| Audit failure rate | count leads stuck in `auditing` vs `audited` | ___ % |
| Playwright crashes on real sites | worker logs for timeout/crash | ___ |
| Bounces suppress the lead | bounce a known-bad address, check `suppressions` | ☐ |
| Reply detection fires | reply from an **outside** mailbox | ☐ |
| **Sequence cancels on reply** | confirm no follow-up sends after the reply | ☐ |
| Inbox placement | Gmail / Outlook: Primary, Promotions, or Spam | ___ |
| Cost per lead | provider dashboard tokens ÷ leads | $___ |

**The reply-cancellation row is the one that embarrasses you in front of a
client.** Test it deliberately: reply to a message, then wait past the +3d
follow-up window (or manually run `run_due_sequences`) and confirm nothing goes
out.

Quick check that a bounce actually suppressed someone:

```bash
curl -s localhost:8000/api/metrics/health -b cookies.txt
```

## 2.3 Fix what breaks

Expect this list to be longer than you think. That is the entire point of doing
it before a client does it for you. Log each failure, fix, re-run.

## 2.4 The 100-lead run

Different failure modes appear only at volume:

- **Rate limits** — provider 429s under concurrency
- **Celery backlog** — the queue-depth alert fires above 500
- **The Redis counter race** in `sender.py` — hourly/daily counters are
  read-then-increment, not atomic, so parallel workers can overshoot the cap by
  a few messages. Acceptable at low volume; fix with `INCR`-based counters
  before high volume
- **DB connections** — SQLite will not survive this; move to Postgres

---

## Definition of done

- [ ] SPF + DKIM + DMARC all `PASS` on a real received message
- [ ] 100-lead campaign completed end to end
- [ ] **Bounce rate under 3%**
- [ ] Mail landing in inbox (not Promotions, not Spam)
- [ ] "How did last week go" answered from the Dashboard in under a minute
- [ ] A deletion request handled with one call to `POST /api/privacy/purge`
- [ ] Two consecutive weeks running without a code change

That last line is the real bar. Software is finished when it stops needing you.
