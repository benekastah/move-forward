"""The github plugin's tables: the people it knows, and what its PR searches found."""

MIGRATIONS = [
    """
    CREATE TABLE github_people (       -- kept: logins and their commit emails
        login TEXT PRIMARY KEY,
        email TEXT,                    -- NULL: looked up, and none found
        source TEXT NOT NULL CHECK (source IN ('import', 'lookup'))
    );
    -- The emails core's reviewer table held for github before it had its own; core keeps the
    -- table, emptied.
    INSERT INTO github_people
        SELECT login, email, CASE source WHEN 'lookup' THEN 'lookup' ELSE 'import' END
        FROM reviewer_users WHERE email IS NOT NULL OR source = 'lookup';
    """,
    """
    CREATE TABLE github_account (      -- fetched: whose PRs these are
        login TEXT PRIMARY KEY
    );
    CREATE TABLE github_team_members ( -- fetched: logins in my teams
        login TEXT PRIMARY KEY
    );
    CREATE TABLE github_prs (          -- fetched: a PR, once per search that found it
        list TEXT NOT NULL CHECK (list IN ('mine', 'direct_requests', 'reviewed_by_me',
            'team_requests', 'merged_recent', 'closed_recent')),
        url TEXT NOT NULL,
        position INTEGER NOT NULL,     -- the search's order
        number INTEGER NOT NULL,
        title TEXT NOT NULL,
        repo TEXT NOT NULL,
        default_branch TEXT,
        is_draft INTEGER NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        merged_at TEXT,
        body TEXT NOT NULL,
        head TEXT NOT NULL,
        head_oid TEXT NOT NULL,
        base TEXT NOT NULL,
        author TEXT,
        size INTEGER NOT NULL,
        changed_files INTEGER NOT NULL,
        mergeable TEXT NOT NULL,
        merge_state TEXT NOT NULL,
        review_decision TEXT,
        in_merge_queue INTEGER NOT NULL,
        rollup_state TEXT,
        last_commit_at TEXT,
        PRIMARY KEY (list, url)
    );
    CREATE TABLE github_pr_labels (    -- fetched
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        label TEXT NOT NULL,
        PRIMARY KEY (list, url, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    CREATE TABLE github_pr_requests (  -- fetched: requested reviewers
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('user', 'team')),
        name TEXT NOT NULL,            -- a login or a team slug
        PRIMARY KEY (list, url, kind, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    CREATE TABLE github_pr_reviews (   -- fetched: each reviewer's latest review
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        author TEXT, state TEXT NOT NULL, submitted_at TEXT,
        PRIMARY KEY (list, url, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    CREATE TABLE github_pr_threads (   -- fetched: review threads, by their first comment
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        resolved INTEGER NOT NULL, outdated INTEGER NOT NULL, path TEXT, author TEXT,
        body TEXT NOT NULL, comment_url TEXT, created_at TEXT,
        PRIMARY KEY (list, url, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    CREATE TABLE github_pr_checks (    -- fetched
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        name TEXT NOT NULL, state TEXT NOT NULL, check_url TEXT,
        PRIMARY KEY (list, url, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    CREATE TABLE github_pr_events (    -- fetched: the timeline events the panels read
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        type TEXT NOT NULL, at TEXT NOT NULL, actor TEXT,
        reviewer_kind TEXT CHECK (reviewer_kind IN ('user', 'team')), reviewer TEXT,
        reason TEXT,
        PRIMARY KEY (list, url, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    CREATE TABLE github_pr_files (     -- fetched: changed paths, for the searches that ask
        list TEXT NOT NULL, url TEXT NOT NULL, position INTEGER NOT NULL,
        path TEXT NOT NULL,
        PRIMARY KEY (list, url, position),
        FOREIGN KEY (list, url) REFERENCES github_prs ON DELETE CASCADE
    );
    -- What the last refresh kept as JSON in core's snapshots, so the page has it at once.
    INSERT OR IGNORE INTO github_account
        SELECT json_extract(payload, '$.login') FROM snapshots
        WHERE source = 'github' AND payload IS NOT NULL;
    INSERT OR IGNORE INTO github_team_members
        SELECT member.value FROM snapshots, json_each(snapshots.payload, '$.team_members') AS member
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL;
    INSERT OR IGNORE INTO github_prs
        SELECT list.key, json_extract(pr.value, '$.url'), pr.key,
            json_extract(pr.value, '$.number'), json_extract(pr.value, '$.title'),
            json_extract(pr.value, '$.repo'), json_extract(pr.value, '$.default_branch'),
            json_extract(pr.value, '$.is_draft'), json_extract(pr.value, '$.state'),
            json_extract(pr.value, '$.created_at'), json_extract(pr.value, '$.updated_at'),
            json_extract(pr.value, '$.merged_at'), COALESCE(json_extract(pr.value, '$.body'), ''),
            json_extract(pr.value, '$.head'), json_extract(pr.value, '$.head_oid'),
            json_extract(pr.value, '$.base'), json_extract(pr.value, '$.author'),
            json_extract(pr.value, '$.size'), json_extract(pr.value, '$.changed_files'),
            json_extract(pr.value, '$.mergeable'), json_extract(pr.value, '$.merge_state'),
            json_extract(pr.value, '$.review_decision'),
            COALESCE(json_extract(pr.value, '$.in_merge_queue'), 0),
            json_extract(pr.value, '$.rollup_state'), json_extract(pr.value, '$.last_commit_at')
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_labels
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, item.value
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.labels') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_requests
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, 'user', item.value
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.requested_users') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_requests
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, 'team', item.value
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.requested_teams') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_reviews
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, json_extract(item.value, '$.author'), json_extract(item.value, '$.state'), json_extract(item.value, '$.submitted_at')
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.reviews') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_threads
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, json_extract(item.value, '$.resolved'), json_extract(item.value, '$.outdated'), json_extract(item.value, '$.path'), json_extract(item.value, '$.author'), COALESCE(json_extract(item.value, '$.body'), ''), json_extract(item.value, '$.url'), json_extract(item.value, '$.created_at')
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.threads') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_checks
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, json_extract(item.value, '$.name'), json_extract(item.value, '$.state'), json_extract(item.value, '$.url')
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.checks') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_events
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, json_extract(item.value, '$.type'), json_extract(item.value, '$.at'), json_extract(item.value, '$.actor'), json_extract(item.value, '$.reviewer_kind'), json_extract(item.value, '$.reviewer'), json_extract(item.value, '$.reason')
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.events') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    INSERT OR IGNORE INTO github_pr_files
        SELECT list.key, json_extract(pr.value, '$.url'), item.key, item.value
        FROM snapshots, json_each(snapshots.payload) AS list, json_each(list.value) AS pr, json_each(pr.value, '$.files') AS item
        WHERE snapshots.source = 'github' AND snapshots.payload IS NOT NULL
        AND list.key IN ('mine', 'direct_requests', 'reviewed_by_me', 'team_requests',
            'merged_recent', 'closed_recent');
    """,
    """
    CREATE TABLE github_reviewer_suggestions (  -- fetched: whom to ask, for my team-requested PRs
        pr_url TEXT NOT NULL,
        position INTEGER NOT NULL,     -- most commits first
        login TEXT NOT NULL,
        commits INTEGER NOT NULL,
        PRIMARY KEY (pr_url, position)
    );
    CREATE TABLE github_team_scores (  -- fetched: how much of a team-requested PR is my teams'
        pr_url TEXT PRIMARY KEY,
        owned_files INTEGER NOT NULL,
        required_files INTEGER NOT NULL,
        my_files INTEGER NOT NULL
    );
    -- What the last refresh kept as JSON in core's snapshots, so the page has it at once.
    INSERT OR IGNORE INTO github_reviewer_suggestions
        SELECT pr.key, suggestion.key, json_extract(suggestion.value, '$.login'),
            json_extract(suggestion.value, '$.commits')
        FROM snapshots, json_each(snapshots.payload, '$.suggestions') AS pr,
            json_each(pr.value) AS suggestion
        WHERE snapshots.source = 'github.derived' AND snapshots.payload IS NOT NULL;
    INSERT OR IGNORE INTO github_team_scores
        SELECT score.key, json_extract(score.value, '$.owned_files'),
            json_extract(score.value, '$.required_files'), json_extract(score.value, '$.my_files')
        FROM snapshots, json_each(snapshots.payload, '$.team_scores') AS score
        WHERE snapshots.source = 'github.derived' AND snapshots.payload IS NOT NULL;
    """,
]
"""The github plugin's migrations."""
