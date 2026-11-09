# notes

The move-forward skill's work notes, in the database: work entries with their todos, merge
conditions and links, and what's on ice. Always on; a `[notes]` section only changes its
settings, and it has none yet. The smallest config, `email` alone, is a todo list.

Notes kept as the skill's markdown files (`work-notes.md`, `on-ice.md`) are read in once
with `mfd notes import <dir>`; `mfd notes export <dir>` writes them back out. (The skill's
`reviewer-map.md` is read by `mfd github import` and `mfd slack import`.) The markdown can't
hold everything the database does: an export loses the links made with Link and the order of
an entry's lines, and an import gives todos new ids.

## Publishes

`notes.entry` for each work entry, pointing at the PRs and tickets it's linked to and mentioning
its title (for the tickets it names), `notes.todo` for each open todo, and `notes.iced` for each
act of icing, hiding what it covers; see [entities.md](../entities.md). Entries are a linking kind: two things an entry
names are related through it.

## Panels

- **Todos**: open `Left to do` items from the work notes, easy close-outs first, blocked
  last. An entry with one open todo has its Done checkbox, Delete and Link on the entry, with
  the PRs and tickets it's linked to; entries with several show each todo below the entry
  with its own, and Add todo on the entry (and on its last todo in focus mode). Its New todo
  field, with a size drop-down (or a leading `[quick]` or `[big]`), starts a todo from nothing, as an entry
  of its own titled by it; the empty panel says so. A title with a line break, or the work
  notes' "Reviews requested of me", is refused, as is a todo for an entry on ice. A todo ticked
  off today stays listed, ticked and dimmed, to untick, as focus mode keeps it, but isn't a step
  in focus mode nor counted; an entry with nothing left but those goes last, and isn't a
  close-out.
  Focus mode also shows the todos one at a time; an entry's only todo stands in for it,
  with its importance and Put on ice. While the notes are empty (just
  upgraded, say), it says how to import them.
- **On ice**: parked stacks, PRs, tickets and work entries, with Reopen (thaw) and a field to
  say why (Replace note, offering what it says now, once it says something); git adds
  Delete branches and git_stack Untrack for a stack. The stack stays on ice with the
  commits it was iced at until it's reopened; Reopen asks first when its branches are
  gone, since the commits are forgotten with it. Everything on ice is left out of the other
  panels, with what it links: a stack's PRs, my PRs that mention an iced ticket (not someone
  else's: their text isn't mine to hide by), and an iced entry's PRs and tickets. Iced PRs, tickets and entries come off ice by themselves
  once their work is over (see the README's daily clean-up).

notes adds an Add todo field to every item about one of my open PRs or a ticket (the todo
goes to the entry covering it, or creates one), and Put on ice to every item about a local
branch (with its stack and group in git stack), one of my open PRs, a ticket or a work entry.
Put on ice has an ice icon with its label in the title. It joins git's Delete button
in a group when both are offered, and joins Snooze, Dismiss and Pin on Act now items.

In focus mode, work entries are split into their todos, one per page, quick ones first.
Each todo lists its entry's PRs, the ones it mentions (by URL, `#number` or bare number, but
not a number within a version, an amount, a path or a range: `1.2.5`, `$5`, `1230-1234`) first
and in bold; merged PRs are purple and struck through, closed ones gray and struck
through. When every PR a todo mentions is merged or closed, it's flagged "its PRs are
done" and its Done box is drawn bigger. Each todo has a Done checkbox, a Delete
button, Quick and Big buttons (its size highlighted), and a Link field that links it to a Jira ticket (by key) or a PR (by URL). Add todo's drop-down
sizes a new one. An entry whose open todos are all quick is what reaches Act now. Done records
the todo as done today and keeps it on the page, so unticking it reopens it; Delete drops
it without recording it.

## Act now

notes nominates entries whose open todos are all quick: close-outs, finishing the entry. A
blocked one ranks lower until it's gone `[act_now] blocked_stale_days` untouched. An entry
with one todo has its buttons in Act now, and joined to another item it's listed beneath that
one with them. An entry with several lists each todo with its own buttons, as the Todos panel
does. Once a lone todo's PRs
are done it's badged as probably done, which brings it back if it was snoozed or dismissed.

## Buttons

`entry` is a work entry's title exactly as the page shows it. `todo` is a todo's id, which
`mfd dump` lists in the `fields` of a todo's actions under the todos panel's items'
`parts`.

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/notes/todo_new` | `input` (the todo; a leading `[quick]` or `[big]` sizes it), optional `choice` (`quick` or `big`) | Starts a todo from nothing: an entry titled by it, with it as its one todo; an entry of that title gets the todo instead (not one on ice, nor the same todo twice). |
| `/actions/notes/todo_add` | `entry`, `text` (or `input`; a leading `[quick]` or `[big]` sizes it), optional `size` (or `choice`: `quick` or `big`), optional `create=1` and `where` (PR or ticket URL) | Adds an open todo at the end of the entry's; with `create`, a missing entry is created first, with `where` as its `Where`. |
| `/actions/notes/todo_size` | `todo`, `size` (`quick` or `big`) | Says how big an open todo is. |
| `/actions/notes/todo_done` | `todo` (its id) | Records the todo as done today; redirects with `kept=notes:<id>`, so the focus page keeps it, ticked, to undo. |
| `/actions/notes/todo_undone` | `todo` | Reopens a done todo. |
| `/actions/notes/todo_delete` | `todo` | Drops the todo without recording it. |
| `/actions/notes/todo_link` | `todo`, `input` (Jira key or PR URL) | Links the todo to the ticket or PR; a key in a project jira hasn't seen yet is looked up in Jira. |
| `/actions/notes/ice` | `repo`, `branch`, optional `note` | Puts the branch on ice, with the rest of its stack and the other stacks in its git stack group when git_stack knows them (git stack's `restack`, `sync` and `push` act on the whole group), recording each branch's commit. |
| `/actions/notes/ice_item` | `kind` (`pull_request`, `ticket` or `notes.entry`), `id`, optional `note` | Puts the PR, ticket or work entry on ice, with what it links. |
| `/actions/notes/ice_note` | `ice` (its id), `input` | Says why it's on ice, in place of what's said now. |
| `/actions/notes/thaw` | `ice` (its id, as the On ice panel's buttons have it) | Takes what's on ice off it. |

## Commands

Every `mfd notes` command works on the database without the server, and prints JSON with
`--json`, for scripts and the skill.

| Command | Does |
| --- | --- |
| `mfd notes list [--open]` | The entries, a line each: its id, title, status, what it's blocked on and how many todos are open (`--open`: only those with some); with `--json`, each entry whole. |
| `mfd notes show <entry>` | One entry with its todos, merge conditions and links. An entry is named by its id, its title, or a PR URL or ticket key it's about. |
| `mfd notes add-entry <title> [--link <PR URL or ticket key>]... [--status S] [--where W]` | Add an entry. A ticket key in the title makes it about that ticket (with `[jira]`). |
| `mfd notes remove <entry>` | Remove an entry with its todos, for finished work. |
| `mfd notes set <entry> status\|where\|blocked-on <text>` | Set an entry's status, where the work lives (repo, branches, PRs), or what it's blocked on. |
| `mfd notes link <entry> <PR URL or ticket key>` | Say an entry is about a PR or ticket. A key needs `[jira]`; one in a project jira hasn't seen yet is looked up in Jira, and that project's keys count from then on. |
| `mfd notes todo add <entry> <text> [--size quick\|big] [--link <PR URL or ticket key>]...` | Add a todo (a leading `[quick]` or `[big]` in it sizes it, unless `--size` does); PR URLs in the text are linked. Prints its id. |
| `mfd notes todo done\|undone\|delete <id>` | Tick a todo off, reopen it, or drop it without recording it as done. |
| `mfd notes todo link <id> <PR URL or ticket key>` | Link a todo to a PR or ticket. |
| `mfd notes condition add <entry> <text> --pr <URL>` / `remove <entry> <text> [--pr <URL>]` | Add a merge condition holding one of the entry's PRs, or remove one (from that PR, or from every PR it holds). Conditions kept before each named its PR, and those the import reads, are given one: the PR their text names, else each of the entry's PRs; one with no PR to hold becomes a line of its entry, `- Merge condition: …`, to add again with `--pr`. |
| `mfd notes remember <key> [--on DATE]` | Record that what `<key>` names happened, today (or on `DATE`, `YYYY-MM-DD`), replacing the day kept before. A key is any text the skill chooses; see the keys below. The daily prune forgets a day `prune_days` old. |
| `mfd notes recall <key>` | The day `remember` last recorded for `<key>`, `YYYY-MM-DD`, or an empty line when it never did (or the prune forgot it); with `--json`, `{"key": …, "on": "YYYY-MM-DD" or null}`. |
| `mfd notes ice (--branch <repo> <branch> \| --item <kind> <id>) [--note N]` | Put a branch, with its stack and group in git stack, on ice, or a `pull_request` (by URL), `ticket` (by key) or `notes.entry` (by title); needs a refresh first, so the graph has it. |
| `mfd notes ices` | What's on ice, with the ids `thaw` takes. |
| `mfd notes thaw <id>` | Take what's on ice off it. |
| `mfd notes import <dir> [--replace]` | Read the skill's `work-notes.md` and `on-ice.md` from `<dir>` into the database, refused when it has neither. Lines it doesn't understand are kept with their entry. Refuses to replace notes already there without `--replace` (anything read replaces them all, even a preamble alone), which replaces the entries and stacks on ice but keeps the PRs, tickets and entries put on ice on their own. |
| `mfd notes export <dir> [--force]` | Write the notes back as `work-notes.md` and `on-ice.md`, in the skill's format, for reading or backup. `on-ice.md` holds only stacks: PRs, tickets and entries on ice stay in the database. |

### Keys to remember by

`remember` and `recall` are memory for the skill: the page never reads them. A key is the
thing that happened and what it's about, joined by `:`, an entry by its title (titles are
unique, and survive an import, which gives entries new ids). The skill's keys, and the
commands they replaced:

| Key | Remembers | Replaces |
| --- | --- | --- |
| `checked:<entry title>` | the skill checked in on the entry | `mfd notes checked <entry> [--on D]`, and the entry's `checked_on` |
| `asked-done:<entry title>` | it asked whether the entry is done | `mfd notes asked-done <entry> [--on D]`, and `asked_done_on` |
| `decline:<entry title>:<what>` | what it offered for the entry was turned down; recall it to see whether that was today | `mfd notes decline <entry> <what>`, and the entry's `declined` list |
| `offered:<PR URL>:nudge`, `offered:<PR URL>:fresh-review` | it offered to nudge the PR's reviewers, or a fresh review of it | `mfd notes offered <PR URL> nudge\|fresh-review`; `mfd notes offers --pr <URL>` is a `recall` of each |
| `asked:<question>` | it asked a once-a-day question, e.g. `asked:ice` (whether anything should go on ice) | `mfd notes asked <question> [--on D]` and `last-asked <question>` |

A key is matched exactly, character for character, but for the space around it, which is
dropped: an entry's key has its title as it is now, so one named by its ticket, or by its title
before an import changed it, is another key. Every key is forgotten `prune_days` after its
day, so an empty `recall` means "not lately", not "never".

`asked:ice` is also `on-ice.md`'s `Last asked`, which `import` remembers and `export` writes;
once the prune has forgotten it, `export` writes no `Last asked` line.
An offer is remembered by its day, not its time. The import keeps a `- Declined <date>: …`
line (as the skill wrote declines and check-ins) as a line of its entry, as written, and
remembers nothing of it.
