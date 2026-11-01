# How the plugins interact

Plugins never import or call each other. Each one publishes entities to the graph and
reads what the others publish; [entities.md](entities.md) lists every kind and attribute.
These diagrams show who publishes and who reads what. Keep them in step with the code: a
change to what a plugin publishes or reads, to its item actions or to its `requires`
updates this file in the same commit.

## Entities

Solid arrows publish, dashed arrows read.

```mermaid
flowchart LR
    subgraph Graph["Entity graph"]
        pr[pr]
        person[person]
        repo[repo]
        branch[branch]
        ticket[ticket]
        entry[entry]
        iced[iced_branch]
    end

    github([github]) -- publishes --> pr
    github -- "publishes (me)" --> person
    git([git]) -- publishes --> repo
    git -- "publishes (repo_dir, ahead, ...)" --> branch
    git_stack([git_stack]) -- "adds stack_parent, stack_status" --> branch
    jira([jira]) -- publishes --> ticket
    notes([notes]) -- publishes --> entry
    notes -- publishes --> iced

    repo -. "checkouts: team PR scores, reviewer suggestions, iced checks" .-> github
    entry -. "merge conditions (Held)" .-> github
    iced -. "skip iced work" .-> github
    pr -. "a branch's PR" .-> git
    iced -. "iced badge" .-> git
    branch -. "which checkouts to scan" .-> git_stack
    repo -. "checkout → owner/name" .-> git_stack
    pr -. "a stack branch's PR" .-> git_stack
    iced -. "refuse stack clean" .-> git_stack
    pr -. "transitions (related)" .-> jira
    pr -. "pings, nudges" .-> slack([slack])
    iced -. "don't ping iced PRs" .-> slack
    repo -. "a PR's checkout, for iced checks" .-> slack
    pr -. "todo link states" .-> notes
    ticket -. "Add todo titles" .-> notes
    branch -. "the stack to ice" .-> notes
    person -. "who I am" .-> core([core])
```

## Refresh, requirements and item actions

Plugins collect in two phases: `collect` first, in parallel, then `collect_after` for
plugins that need the others' entities to know what to fetch. A plugin's `requires` must
be enabled too. Plugins add buttons to each other's items by the items' `subjects`.

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
        prItems["items about a pr<br/>(github, git, git_stack, slack)"]
        ticketItems["items about a ticket<br/>(jira)"]
        branchItems["items about a branch<br/>(git's Local work)"]
        icedItems["items about iced branches<br/>(notes' On ice)"]
    end
    slack([slack]) -- "Ping / Bump link" --> prItems
    notes([notes]) -- "Add todo" --> prItems
    notes -- "Add todo" --> ticketItems
    notes -- "Put on ice" --> branchItems
    git -- "Delete branches" --> icedItems
    git_stack -- "Untrack" --> icedItems

    github -. "url_for(pr)" .-> urls{{graph.url_for}}
    jira -. "url_for(ticket)" .-> urls
    urls -. "ticket links" .-> notes
```

slack and notes have no snapshot, so they don't collect.
