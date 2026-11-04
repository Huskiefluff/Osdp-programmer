"""PyInstaller entry point for the console app."""

import sys

from readerprog.cli import main

if __name__ == "__main__":
    sys.exit(main())
