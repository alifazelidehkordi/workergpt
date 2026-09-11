"""Interactive ChatGPT login bootstrap for a persistent Patchright profile."""

from __future__ import annotations

import argparse
from pathlib import Path

from orchestrator.tools.browser_runtime import open_login_session


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a persistent ChatGPT login profile")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--profile-dir", type=Path, default=None)
    args = parser.parse_args()

    session = open_login_session(profile=args.profile, profile_dir=args.profile_dir)
    try:
        print(f"Browser profile: {session.options.profile_dir}")
        print("Log in to ChatGPT in the opened browser, then press Enter here.")
        input()
        if not session.is_authenticated():
            raise SystemExit("ChatGPT login could not be verified. Finish login and retry.")
        print("Login verified. Persistent profile is ready for real executor runs.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
