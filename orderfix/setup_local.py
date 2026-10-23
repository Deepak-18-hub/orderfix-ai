"""Create private local configuration without displaying credentials."""
from pathlib import Path
import secrets


def main():
    path = Path(".env")
    if path.exists():
        print("Existing .env preserved. Edit it locally to configure optional APIs.")
        return
    source = Path(".env.example").read_text(encoding="utf-8")
    source = source.replace("AGENT_SEED=\n", "AGENT_SEED=" + secrets.token_urlsafe(48) + "\n", 1)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(source)
    print("Created .env with a private stable agent seed. Keep this file out of Git and screenshots.")


if __name__ == "__main__":
    main()
