"""Convenience wrapper; requires DRY_RUN=false and Telegram environment variables."""

import sys

from revival_radar.main import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "test-telegram"]
    main()
