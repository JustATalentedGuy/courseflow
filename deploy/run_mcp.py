import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    backend = Path(__file__).resolve().parent.parent / "backend"
    environment_file = backend / ".env.mcp"
    if not environment_file.exists():
        raise RuntimeError(
            f"Missing {environment_file}. Copy .env.mcp.example to .env.mcp and configure it."
        )

    for raw_line in environment_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()

    os.chdir(backend)
    sys.path.insert(0, str(backend))
    runpy.run_module("app.mcp_server", run_name="__main__")


if __name__ == "__main__":
    main()
