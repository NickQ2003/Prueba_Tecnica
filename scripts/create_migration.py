import argparse
import re
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def next_revision(script: ScriptDirectory) -> str:
    heads = script.get_heads()
    if len(heads) > 1:
        raise ValueError("migration graph has multiple heads; resolve the branch before generating")
    revisions = list(script.walk_revisions())
    visited: set[str] = set()
    current = script.get_revision(heads[0]) if heads else None
    while current is not None:
        if current.revision in visited:
            raise ValueError("migration graph contains a cycle")
        visited.add(current.revision)
        down_revision = current.down_revision
        if isinstance(down_revision, tuple):
            raise ValueError(
                "merge revisions are not supported by this sequential migration policy"
            )
        current = script.get_revision(down_revision) if down_revision else None
    if len(visited) != len(revisions):
        raise ValueError("migration files contain disconnected or ambiguous revisions")
    numbers = []
    for revision in revisions:
        if not re.fullmatch(r"\d{3,}", revision.revision):
            raise ValueError(
                f"revision id is not numeric with at least three digits: {revision.revision}"
            )
        numbers.append(int(revision.revision))
    if numbers and sorted(numbers) != list(range(min(numbers), max(numbers) + 1)):
        raise ValueError("migration numbering has gaps; resolve the sequence before generating")
    return f"{max(numbers, default=0) + 1:03d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the next sequential Alembic migration")
    parser.add_argument("name", help="migration name in snake_case")
    parser.add_argument(
        "--autogenerate", action="store_true", help="compare metadata to the database"
    )
    args = parser.parse_args()
    if not NAME_PATTERN.fullmatch(args.name):
        parser.error("name must be snake_case and start with a lowercase letter")

    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    try:
        revision = next_revision(script)
        command.revision(
            config,
            message=args.name,
            autogenerate=args.autogenerate,
            rev_id=revision,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"migration not created: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
