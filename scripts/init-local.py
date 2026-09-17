"""Create an isolated Docker smoke environment without copying local API keys."""
import os
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".deploy" / "local"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("outputs", "data/ref_assets", "logs"):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves credentials/settings on repeat runs.
    for path, value in (
        (runtime / "config.json", "{}\n"),
        (root / ".deploy" / "local.env", (
            "COMPOSE_PROJECT_NAME=meta-creative-local\n"
            "APP_IMAGE=meta-creative-tool:local\n"
            "APP_PORT=18000\n"
            "RUNTIME_DIR=./.deploy/local\n"
            "POSTGRES_USER=meta\n"
            f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n"
            "POSTGRES_DB=meta_creative\n"
        )),
    ):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(f"Preserved: {path.relative_to(root)}")
        else:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(value)
            print(f"Created: {path.relative_to(root)}")
    print("Local environment ready. No API keys were copied.")


if __name__ == "__main__":
    main()
