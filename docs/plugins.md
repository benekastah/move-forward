# How the plugins interact

Plugins never import or call each other. Each one publishes entities to the graph and
reads what the others publish; [graph.md](graph.md) explains the graph, and
[entities.md](entities.md) lists every kind and attribute. Each plugin has a page of
its own: [github](plugins/github.md), [git](plugins/git.md),
[git_stack](plugins/git_stack.md), [jira](plugins/jira.md), [slack](plugins/slack.md)
and [notes](plugins/notes.md).
These diagrams show who publishes and who reads what. Keep them in step with the code: a
change to what a plugin publishes or reads, to its item actions or to its `requires`
updates this file in the same commit.

git and git_stack both nominate merged branches for Act now, each those it can clean up: git leaves
the ones git-stack tracks to git_stack's Clean.

Core adds importance choices to entity items and their individual todo parts (but not to
items about what hides things), and a Snooze to the items of a `snoozable` panel. Plugins can group their buttons with those of other plugins: notes' Put on ice joins git's
Delete under Disposition, and joins core's Snooze, Dismiss and Pin on Act now items.
Emoji buttons retain their text labels in titles and accessible names.

## Entities

Solid arrows publish, dashed arrows read.

```mermaid
flowchart LR
    subgraph Graph["Entity graph"]
        pr[pull_request]
        person[person]
        repo[git.repo]
        branch[git.branch]
        ticket[ticket]
        entry[notes.entry]
        ice[notes.iced]
    end

    github([github]) -- publishes --> pr
    github -- "publishes (me, emails)" --> person
    git([git]) -- publishes --> repo
    git -- "publishes (repo_dir, ahead, ...)" --> branch
    git_stack([git_stack]) -- "publishes, pointing at branch" --> stack_branch[git_stack.stack_branch]
    jira([jira]) -- publishes --> ticket
    notes([notes]) -- publishes --> entry
    notes -- "publishes, pointing at what it hides" --> ice
    ice -. "hides what it covers" .-> core([core])

    repo -. "checkouts: team PR scores, reviewer suggestions" .-> github
    entry -. "merge conditions (Held)" .-> github
    branch -. "my PRs' branches moving: refresh now; relates each PR to its branch" .-> github
    repo -. "checkout → owner/name, to relate PRs to branches" .-> github
    pr -. "a branch's PR (related by github); its head, to see a checkout behind" .-> git
    ice -. "its branches' commits, before deleting them" .-> git
    stack_branch -. "tracked: my unpushed work; merged: left to its Clean" .-> git
    branch -. "which checkouts to scan" .-> git_stack
    pr -. "a stack branch's PR (related by github)" .-> git_stack
    ice -. "its stack's branches: Untrack" .-> git_stack
    pr -. "transitions (related)" .-> jira
    pr -. "pings, nudges" .-> slack([slack])
    person -. "emails, to look up Slack ids" .-> slack
    pr -. "todo link states; an iced branch's PRs" .-> notes
    repo -. "an iced branch's checkout" .-> notes
    stack_branch -. "what tracks an iced branch" .-> notes
    ticket -. "Add todo titles" .-> notes
    stack_branch -. "the stack to ice, with its group (its stack)" .-> notes
    person -. "who I am; who an item is about, for contact types" .-> core([core])
```

## Refresh, requirements and item actions

Plugins collect in two phases: `collect` first, in parallel, then `collect_after` for
plugins that need the others' entities to know what to fetch. A plugin's `requires` must
be enabled too; one that collects after the graph is refreshed with them, unless it
`watches` them, when its `changed` decides. Plugins add buttons to each other's items by the items' `subjects`.

```mermaid
flowchart TB
    subgraph Phase1["Refresh, phase 1: collect (in parallel)"]
        github([github])
        git([git])
        jira([jira])
    end
    subgraph Phase2["Phase 2: collect_after (sees the graph)"]
        git_stack([git_stack])
    end
    Phase1 --> Phase2
    Phase2 --> after["after_collect and prune, on the rebuilt graph"]

    git_stack == "requires" ==> git

    subgraph Items["Items, by their subjects"]
        prItems["items about a pull_request<br/>(github, git, git_stack, slack)"]
        ticketItems["items about a ticket<br/>(jira)"]
        branchItems["items about a git.branch<br/>(git's Local work)"]
        icedItems["items about a notes.iced, with its branches<br/>(notes' On ice)"]
    end
    slack([slack]) -- "Ping / Bump link" --> prItems
    notes([notes]) -- "Add todo" --> prItems
    notes -- "Add todo" --> ticketItems
    notes -- "Put on ice" --> branchItems
    notes -- "Put on ice" --> prItems
    notes -- "Put on ice" --> ticketItems
    git -- "Delete branches" --> icedItems
    git_stack -- "Untrack" --> icedItems

    github -. "url_for(pr)" .-> urls{{graph.url_for}}
    jira -. "url_for(ticket)" .-> urls
    urls -. "ticket links" .-> notes
```

slack and notes have no snapshot, so they don't collect.

The daily prune is the one place a plugin reaches past the graph built from snapshots:
notes asks `graph.fetch` when old PRs closed (github) and tickets resolved (jira), to know
which entries are finished.
