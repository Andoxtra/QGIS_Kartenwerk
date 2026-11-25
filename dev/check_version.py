#!/usr/bin/env python3
"""Fail unless a release tag (v2.2.0) matches version= in plugin/kartenwerk/metadata.txt."""
import configparser
import sys
from pathlib import Path

METADATA = Path(__file__).resolve().parent.parent / "plugin" / "kartenwerk" / "metadata.txt"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_version.py <tag>", file=sys.stderr)
        return 2
    tag = sys.argv[1]
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(METADATA, encoding="utf-8")
    version = parser["general"]["version"].strip()
    if tag != f"v{version}":
        print(f"Tag {tag} does not match metadata.txt version {version} (expected v{version}).",
              file=sys.stderr)
        return 1
    print(f"Tag {tag} matches metadata.txt version {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
