# How the plugins interact

Plugins never import or call each other. Each one publishes entities to the graph and
reads what the others publish; [graph.md](graph.md) explains the graph, and
[entities.md](entities.md) lists every kind and attribute. Each plugin has a page of
its own: [github](plugins/github.md), [git](plugins/git.md),
[git_stack](plugins/git_stack.md), [jira](plugins/jira.md), [slack](plugins/slack.md),
[mail](plugins/mail.md) and [notes](plugins/notes.md).
These diagrams show who publishes and who reads what. Keep them in step with the code: a
change to what a plugin publishes or reads, to its item actions or to its `requires`
updates this file in the same commit.

git and git_stack both nominate merged branches for Act now, each those it can clean up: git leaves
the ones git stack tracks to git_stack's Clean.

Core adds importance choices to entity items and their individual todo parts (but not to
items about what hides things), and Snooze to every item with a task. Plugins can group their buttons with those of other plugins: notes' Put on ice joins git's
Delete under Disposition, and joins core's Snooze and Pin on Act now items.
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
        thread[mail.thread]
    end

    github([github]) -- "publishes, its labels as tags" --> pr
    github -- "publishes (me, emails)" --> person
    git([git]) -- publishes --> repo
    git -- "publishes (repo_dir, ahead, ...)" --> branch
    git_stack([git_stack]) -- "publishes, pointing at branch" --> stack_branch[git_stack.stack_branch]
    jira([jira]) -- "publishes, its labels as tags" --> ticket
    mail([mail]) -- "publishes, pointing at its sender, its Gmail labels as tags" --> thread
    mail -- "publishes (senders, by email)" --> person
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
    ticket -. "title, lifecycle: what a verified sender's mail is about" .-> mail
    tag[tag] -. "the tags a verified sender's mail has (its subject's, or mine), if something else open carries them" .-> mail
    pr -. "todo link states; an iced branch's PRs" .-> notes
    repo -. "an iced branch's checkout" .-> notes
    stack_branch -. "what tracks an iced branch" .-> notes
    ticket -. "Add todo titles" .-> notes
    stack_branch -. "the stack to ice, with its group (its stack)" .-> notes
    branch -. "the branch to ice, its commit; whether an iced one is gone" .-> notes
    person -. "who I am; who an item is about, for contact types" .-> core([core])
```

## Refresh, requirements and item actions

Plugins collect in two phases: first those that don't need the others' entities, in
parallel, then those that set `collects_after` (they need the others' entities to know what to
fetch), with the graph the first made. A plugin's `requires` must
be enabled too. Each plugin refreshes on its own schedule; one that `watches` another is
asked whether it `changed` right after that one refreshes. Plugins add buttons to each other's items by the items' `subjects`.

```mermaid
flowchart TB
    subgraph Phase1["Refresh, phase 1: collect (in parallel)"]
        github([github])
        git([git])
        jira([jira])
        mail([mail])
    end
    subgraph Phase2["Phase 2: collects_after (sees the new graph)"]
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

    jira -. "url_for(ticket)" .-> urls{{graph.url_for}}
    urls -. "ticket links" .-> notes
```

slack and notes have no snapshot, so they don't collect.

The daily prune is the one place a plugin reaches past the graph built from snapshots:
notes asks `graph.fetch` when old PRs closed (github) and tickets resolved (jira), to know
which entries are finished.
