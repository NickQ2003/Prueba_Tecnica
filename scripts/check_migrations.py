import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())
    heads = script.get_heads()
    if len(heads) != 1:
        raise SystemExit(f"expected exactly one Alembic head, found {len(heads)}")
    if not revisions:
        raise SystemExit("no Alembic revisions found")

    mapping = {item.revision: item for item in revisions}
    ordered: list[str] = []
    current = mapping[heads[0]]
    while current:
        if isinstance(current.down_revision, tuple):
            raise SystemExit(f"merge revision is not allowed: {current.revision}")
        ordered.append(current.revision)
        current = mapping.get(current.down_revision) if current.down_revision else None
    if len(ordered) != len(revisions):
        raise SystemExit("revision chain is disconnected or ambiguous")
    ascending = list(reversed(ordered))
    expected = 1
    for revision in ascending:
        if not re.fullmatch(r"\d{3,}", revision):
            raise SystemExit(f"revision id must be numeric and at least three digits: {revision}")
        if int(revision) != expected:
            raise SystemExit(f"expected revision {expected:03d}, found {revision}")
        expected += 1
    for path in (Path(script.dir) / "versions").glob("*.py"):
        if path.name == "__init__.py":
            continue
        if not re.fullmatch(r"\d{3,}_[a-z0-9_]+\.py", path.name):
            raise SystemExit(f"invalid migration filename: {path.name}")
    print(f"migration chain valid: {' -> '.join(ascending)} (head {heads[0]})")


if __name__ == "__main__":
    main()
