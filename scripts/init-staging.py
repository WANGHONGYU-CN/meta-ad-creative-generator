"""Initialize new, isolated staging data; existing credentials are never replaced."""
import os
from pathlib import Path
import re
import secrets
import sys


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"meta-creative-tool:sha-[0-9a-f]{40}", sys.argv[1]):
        raise SystemExit("Usage: python3 scripts/init-staging.py meta-creative-tool:sha-<40-char-commit>")
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".deploy" / "staging"
    for name in ("outputs", "data/ref_assets", "logs"):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    values = (
        (runtime / "config.json", "{}\n"),
        (root / ".deploy" / "staging.env", (
            f"STAGING_IMAGE={sys.argv[1]}\n"
            "STAGING_DB_USER=meta_staging\n"
            f"STAGING_DB_PASSWORD={secrets.token_hex(24)}\n"
            "STAGING_DB_NAME=meta_creative_staging\n"
        )),
    )
    for path, value in values:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(f"Preserved: {path.relative_to(root)} (including the existing image tag)")
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(value)
            print(f"Created: {path.relative_to(root)}")
    print("No production credentials or data were copied. Set STAGING_IMAGE explicitly for later releases.")


if __name__ == "__main__":
    main()
