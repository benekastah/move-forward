"""Reading tables out of markdown, for plugins importing a file someone keeps by hand."""


def tables(text: str) -> dict[str, list[list[str]]]:
    """The first table in each `##` section, by heading: its body rows (header and divider
    skipped) as cells, stripped of spaces and backticks."""
    rows_by_heading: dict[str, list[str]] = {}
    heading: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            rows_by_heading[heading] = []
        elif heading is not None and line.strip().startswith("|"):
            rows_by_heading[heading].append(line)
    return {
        name: [
            [cell.strip().strip("`").strip() for cell in row.strip().strip("|").split("|")]
            for row in rows[2:]
        ]
        for name, rows in rows_by_heading.items()
    }


def cell(row: list[str], index: int) -> str:
    """The row's cell at `index`, or "" when the row is short."""
    return row[index] if index < len(row) else ""
