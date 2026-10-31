# Act now

The top of the page should answer one question for any set of tools: what should I do
next? Today "Act now" is a fixed list of four GitHub panels. This note proposes making it
generic: every plugin describes its items in a shared vocabulary, and one ranking turns
those descriptions into a short list of the best next actions, whatever the tools.

It builds on `plugin_rearchitecture.md` (plugins, items with a `subject`, the entity graph).

## What makes something worth doing now

Three kinds of item earn a place, matching how people triage by hand:

- **Quick wins**: done in minutes. Reply to a thread, approve a small PR, tick a todo,
  send a follow-up email.
- **Close-outs**: the last step that finishes something bigger. Merge an approved PR,
  move a ticket to Closed, send the signed contract, a work entry's only open todo.
- **Important work**: matters more than its size. An escalated incident, a reviewer who
  is blocked on you, a deal that closes this week.

On top of those, two adjusters:

- **Someone is waiting.** A review request, a question addressed to me, a customer reply.
  Waiting time makes it more urgent, and blocking a person beats blocking a process.
- **It will go stale or get worse.** A deadline, a PR drifting into conflicts, a meeting
  tomorrow that needs prep, a lead going cold.

And one filter: **it must be doable now.** Something blocked on others, on ice, a draft
I'm not ready to show, or waiting for CI is not an action, however important.

## The shared vocabulary

Each plugin already knows these things about its own items; it only needs to say them in
common terms. `Item` gains an optional `signals` field:

```python
class Signals(Model):
    effort: Literal["minutes", "hour", "day", "unknown"] = "unknown"
    closes: str | None = None
    """What doing it finishes, e.g. "APP-12" or "PR 330"; None if it only moves things along."""
    importance: int = 0
    """0 normal, 1 high, 2 critical. Plugin-specific inputs map onto this (escalation
    level, priority, deal size)."""
    waiting: list[str] = []
    """Who is waiting on me for it: logins, names, "customer"."""
    waiting_since: datetime | None = None
    due: datetime | None = None
    """A deadline, or when it will go stale (a lead's follow-up date, a review SLA)."""
    blocked: bool = False
    """Not doable now: waiting on someone else, on ice, CI pending."""
    verb: str = ""
    """What to do, as a short imperative: "Reply to 2 threads", "Merge", "Send the quote"."""
```

A plugin fills in what it knows and leaves the rest at the defaults. Nothing in it is
specific to software: "effort", "closes", "who's waiting" and "due" mean the same for a
PR, a ticket, an email thread or a sales opportunity.

### Examples

| Plugin | Item | effort | closes | importance | waiting | due | verb |
| --- | --- | --- | --- | --- | --- | --- | --- |
| github | Review request, +12 lines | minutes | | 0 | author | request age + SLA | Review |
| github | My PR approved, green | minutes | the PR (and its ticket, via the graph) | 0 | | | Merge |
| github | 2 unresolved threads on my PR | minutes | | 0 | reviewers | | Reply to 2 threads |
| github | CI failing on my PR | hour | | 0 | | | Fix CI |
| jira | Ticket In Review, all PRs merged | minutes | the ticket | 0 | | | Move to Closed |
| jira | Escalated incident | unknown | | 2 | reporter | | Look at the incident |
| notes | Entry's only open todo, `[quick]` | minutes | the entry | 0 | | | the todo's text |
| notes | Todo whose PRs all merged | minutes | | 0 | | | Tick it off |
| git | Branch merged, still local | minutes | | 0 | | | Delete the branch |
| slack | Reviewer quiet for 2 days | minutes | | 0 | | | Bump the review |
| crm (sales) | Opportunity, close date Friday, quote not sent | hour | the opportunity | 1 | buyer | Friday | Send the quote |
| email (sales) | Customer reply unanswered for 26h | minutes | | 1 | customer | reply SLA | Reply |
| calendar | Call tomorrow, no prep notes | hour | | 0 | attendees | the call | Prep for the call |

Effort can come from the tool (story points, a `[quick]` marker, a PR's size), from
simple rules (approve, merge, reply and delete are minutes), or stay unknown.

## Ranking

A score per item, then a diverse top list:

```
score = 3 * closes?                        # finishing beats starting
      + 2 * (effort == minutes) + 1 * (effort == hour)
      + 3 * importance
      + 2 * waiting? + log2(1 + days waiting)
      + due_pressure(due)                  # 0 far away, rising to 4 when overdue
      - 100 * blocked                      # out
```

The weights are a starting point, in config under `[act_now]`, not code:

```toml
[act_now]
size = 7                    # items shown
per_plugin = 3              # so one busy tool can't fill the list
weights = { closes = 3, minutes = 2, hour = 1, importance = 3, waiting = 2, due = 1 }
```

Then:

- **Diversity.** At most `per_plugin` items from one plugin, and at most one per subject:
  if a PR shows up as "Reply to threads" and "Fix CI", the higher one wins and the other
  is mentioned on it ("also: CI failing").
- **Merging across tools through the graph.** "Merge PR 330" and "Move APP-12 to Closed"
  are one close-out when the PR is the ticket's last open one. Act now shows them as one
  item, "Merge PR 330, then close APP-12", with both actions.
- **Explanations.** Every item says why it's there, from the signals that scored: "quick,
  closes APP-12, Alice waiting 2 days". This is what makes the ranking trustworthy and
  tunable.

## Feedback from the person using it

- **Snooze** an item until tomorrow or a date; **dismiss** it for good. Stored in the
  core's `act_now_feedback` table, keyed by the item's subject and verb, so a changed item
  (new commits, a new reply) comes back.
- **Pin** an item to keep it at the top until it's done.
- Optionally later: count which items get acted on from the list versus skipped, and
  nudge the weights. Not needed for a first version; the explanations plus config weights
  get most of the value without any learning.

## Where it lives

In core, not in a plugin. Each plugin decides what of its own it nominates, through one
hook in the plugin protocol:

```python
def act_now(self, ctx: Context[C], snapshot: S | None, graph: Graph) -> list[Item]:
    """This plugin's candidates for Act now, each with `signals` filled in."""
```

The plugin knows its tool best: which of its items are actionable, how long they take,
what they close and how important they are. Core knows nothing about any tool; it only
combines the nominations. Core then:

1. collects every enabled plugin's nominations,
2. drops blocked items and ones the person snoozed or dismissed,
3. merges nominations about the same subject, or about subjects the graph links (a PR and
   its ticket), into one item with all their actions,
4. scores, caps per plugin, and keeps the top `size`,
5. renders the result as the first panel on the page and as focus mode's "Act now".

A nominated item is usually also in one of the plugin's own panels; nominating doesn't move
it. A plugin that implements no `act_now` contributes nothing, and the fallback below lets
it opt in cheaply. Core owns one table for the person's feedback:

```sql
CREATE TABLE act_now_feedback (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    verb TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('snooze', 'dismiss', 'pin')),
    until TEXT,                        -- for snooze
    fingerprint TEXT NOT NULL,         -- the item's state when snoozed or dismissed
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, verb)
);
```

The urgent panels of today (Reviews for me, Feedback, CI failures, Merge conflicts) go back to
their plugins' groups, and the top of the page is this one ranked panel. Focus mode gets
an "Act now" category that walks the same list.

## A version that doesn't depend on every plugin filling in signals

Plugins will fill in signals unevenly, especially third-party ones. A fallback keeps the
panel useful:

- `BasePlugin.act_now` by default nominates every item in the plugin's panels marked
  `actionable` (today's urgent panels), with the panel's default effort and verb for items
  that carry no signals of their own.
- Items in non-actionable panels never appear unless the plugin nominates them itself.
- So a minimal plugin gets into Act now by setting two fields on a panel, and a careful
  plugin overrides `act_now` to nominate precisely and rank better.

## Open questions

- **How much to trust tool-provided importance.** Jira priority is often stale; an
  escalation label is more telling. Per-plugin mapping into 0–2 keeps that judgment in
  the plugin.
- **Effort for unknown items.** Treat unknown as "hour" for scoring, or keep them out of
  the quick-wins slot only? Probably the latter.
- **Time of day.** Quick wins at the end of the day, important work in the morning? An
  easy later addition: a weight profile per part of the day.
- **Teams.** "Someone is waiting" could weigh people on my teams more. That needs the
  `person` entities to know team membership, which github already fetches.
