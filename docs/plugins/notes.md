# notes

The move-forward skill's work notes, in the database: work entries with their todos, merge
conditions and links, and what's on ice. Always on; a `[notes]` section only changes its
settings, and it has none yet. The smallest config, `email` alone, is a todo list.

Notes kept as the skill's markdown files (`work-notes.md`, `on-ice.md`,
`reviewer-map.md`) are read in once with `mfd notes import <dir>`; `mfd notes export
<dir>` writes them back out.

## Publishes

`entry` for each work entry, pointing at the PRs and tickets it's linked to and mentioning
its title (for the tickets it names), and `iced` for each act of icing, hiding what it
covers; see [entities.md](../entities.md). Entries are a linking kind: two things an entry
names are related through it.

## Panels

- **Todos**: open `Left to do` items from the work notes, easy close-outs first, blocked
  last. While the notes are empty (just upgraded, say), it says how to import them.
- **On ice**: parked stacks, PRs, tickets and work entries, with Reopen (thaw); git adds
  Delete branches and git_stack Untrack for a stack. The stack stays on ice with the
  commits it was iced at until it's reopened; Reopen asks first when its branches are
  gone, since the commits are forgotten with it. Everything on ice is left out of the other
  panels, with what it links: a stack's PRs, the PRs that mention an iced ticket, and an
  iced entry's PRs and tickets. Iced PRs, tickets and entries come off ice by themselves
  once their work is over (see the README's daily clean-up).

notes adds an Add todo field to every item about one of my open PRs or a ticket (the todo
goes to the entry covering it, or creates one), and Put on ice to every item about a local
branch (with its git-stack stack and group), one of my open PRs, a ticket or a work entry.

In focus mode, work entries are split into their todos, one per page, quick ones first.
Each todo lists its entry's PRs, the ones it mentions (by URL, `#number` or bare number)
first and in bold; merged PRs are purple and struck through, closed ones gray and struck
through. When every PR a todo mentions is merged or closed, it's flagged "its PRs are
done" and its Done box is drawn bigger. Each todo has a Done checkbox, a Delete button,
and a Link field that links it to a Jira ticket (by key) or a PR (by URL). Done records
the todo as done today and keeps it on the page, so unticking it reopens it; Delete drops
it without recording it.

## Act now

notes nominates entries whose open todos are all quick: close-outs, finishing the entry,
unless it's blocked.

## Buttons

`entry` is a work entry's title exactly as the page shows it. `todo` is a todo's id, which
`mfd dump` lists in the `fields` of a todo's actions under the todos panel's items'
`parts`.

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/notes/todo_add` | `entry`, `text` (or `input`), optional `size` (`quick` or `big`), optional `create=1` and `where` (PR or ticket URL) | Adds an open todo at the end of the entry's; with `create`, a missing entry is created first, with `where` as its `Where`. |
| `/actions/notes/todo_done` | `todo` (its id) | Records the todo as done today; redirects with `kept=notes:<id>`, so the focus page keeps it, ticked, to undo. |
| `/actions/notes/todo_undone` | `todo` | Reopens a done todo. |
| `/actions/notes/todo_delete` | `todo` | Drops the todo without recording it. |
| `/actions/notes/todo_link` | `todo`, `input` (Jira key or PR URL) | Links the todo to the ticket or PR; a key in a project jira hasn't seen yet is looked up in Jira. |
| `/actions/notes/ice` | `repo`, `branch` | Puts the branch on ice, with the rest of its stack and the other stacks in its git-stack group when git_stack knows them (git-stack's `restack`, `sync` and `push` act on the whole group), recording each branch's commit. |
| `/actions/notes/ice_item` | `kind` (`pr`, `ticket` or `entry`), `id`, optional `note` | Puts the PR, ticket or work entry on ice, with what it links. |
| `/actions/notes/thaw` | `ice` (its id, as the On ice panel's buttons have it) | Takes what's on ice off it. |

## Commands

Every `mfd notes` command works on the database without the server, and prints JSON with
`--json`, for scripts and the skill.

| Command | Does |
| --- | --- |
| `mfd notes list [--open]` | The entries with their open todos (`--open`: only those with some). Every `mfd notes` command works on the database without the server, and prints JSON with `--json`, for scripts and the skill. |
| `mfd notes show <entry>` | One entry with its todos, merge conditions and links. An entry is named by its id, its title, or a PR URL or ticket key it's about. |
| `mfd notes add-entry <title> [--link <PR URL or ticket key>]... [--status S] [--where W]` | Add an entry. A ticket key in the title makes it about that ticket (with `[jira]`). |
| `mfd notes remove <entry>` | Remove an entry with its todos, for finished work. |
| `mfd notes set <entry> status\|where\|blocked-on <text>` | Set an entry's status, where the work lives (repo, branches, PRs), or what it's blocked on. |
| `mfd notes link <entry> <PR URL or ticket key>` | Say an entry is about a PR or ticket. A key needs `[jira]`; one in a project jira hasn't seen yet is looked up in Jira, and that project's keys count from then on. |
| `mfd notes todo add <entry> <text> [--size quick\|big] [--link <PR URL or ticket key>]...` | Add a todo; PR URLs in the text are linked. Prints its id. |
| `mfd notes todo done\|undone\|delete <id>` | Tick a todo off, reopen it, or drop it without recording it as done. |
| `mfd notes todo link <id> <PR URL or ticket key>` | Link a todo to a PR or ticket. |
| `mfd notes condition add <entry> <text> --pr <URL>` / `remove <entry> <text> [--pr <URL>]` | Add a merge condition holding one of the entry's PRs, or remove one (from that PR, or from every PR it holds). Conditions kept before each named its PR, and those the import reads, are given one: the PR their text names, else each of the entry's PRs; one with no PR to hold becomes a line of its entry, `- Merge condition: …`, to add again with `--pr`. |
| `mfd notes decline <entry> <text>` | Record that something offered for an entry (a nudge, say) was turned down today. |
| `mfd notes checked <entry> [--on DATE]` | Record that the skill checked in on an entry (today by default); kept as its `checked_on`. |
| `mfd notes asked-done <entry> [--on DATE]` | Record that the skill asked whether an entry is done; kept as its `asked_done_on`. |
| `mfd notes offered <pr URL> nudge\|fresh-review` | Record that the skill offered to nudge a PR's reviewers, or a fresh review of a PR that changed since its reviewer's review; kept with the time, replacing the last one of that kind. The daily prune forgets those older than `prune_days`. |
| `mfd notes offers [--pr URL]` | When the skill last offered each kind on each PR, newest first. |
| `mfd notes asked <question> [--on DATE]` | Record that the skill asked a once-a-day question (e.g. `ice`, whether anything should go on ice) today. `ice`'s date is also `on-ice.md`'s `Last asked`, which import and export read and write. |
| `mfd notes last-asked <question>` | When the skill last asked it, or `never`. |
| `mfd notes ice (--branch <repo> <branch> \| --item <kind> <id>) [--note N]` | Put a branch, with its git-stack stack and group, on ice, or a `pr` (by URL), `ticket` (by key) or `entry` (by title); needs a refresh first, so the graph has it. |
| `mfd notes ices` | What's on ice, with the ids `thaw` takes. |
| `mfd notes thaw <id>` | Take what's on ice off it. |
| `mfd notes import <dir> [--replace]` | Read the skill's `work-notes.md` and `on-ice.md` from `<dir>` into the database. Lines it doesn't understand are kept with their entry. Refuses to replace notes already imported without `--replace`, which replaces the entries and stacks on ice but keeps the PRs, tickets and entries put on ice on their own. |
| `mfd notes export <dir> [--force]` | Write the notes back as `work-notes.md` and `on-ice.md`, in the skill's format, for reading or backup. `on-ice.md` holds only stacks: PRs, tickets and entries on ice stay in the database. |
