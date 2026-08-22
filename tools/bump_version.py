#!/usr/bin/env python3
"""Bump the student app's version.

The version lives in two files that must agree: docs/version.js (what the page
displays) and docs/sw.js (what names the cache). Editing either by hand is how
they drift, so this is the only thing that should write them.

    python3 tools/bump_version.py              # 4.3.11 -> 4.3.12
    python3 tools/bump_version.py --minor      # 4.3.11 -> 4.4.0
    python3 tools/bump_version.py --major      # 4.3.11 -> 5.0.0
    python3 tools/bump_version.py --set 4.3.11
    python3 tools/bump_version.py --show

Run automatically by tools/hooks/pre-commit whenever anything else in docs/
changes, because a docs/ change that does not move the version is exactly the
bug this exists to prevent: phones keep serving the cached old build.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_JS = ROOT / "docs" / "version.js"
SW_JS = ROOT / "docs" / "sw.js"

_VERSION_JS = re.compile(r'(const MATTEND_VERSION = ")(\d+\.\d+\.\d+)(")')
_SW_CONST = re.compile(r'(const VERSION = ")(\d+\.\d+\.\d+)(")')
_SW_COMMENT = re.compile(r'(mattend service worker -- version )(\d+\.\d+\.\d+)')


class VersionError(RuntimeError):
    pass


def read_version() -> str:
    if not VERSION_JS.exists():
        raise VersionError(f"missing {VERSION_JS}")
    match = _VERSION_JS.search(VERSION_JS.read_text())
    if not match:
        raise VersionError(f"no MATTEND_VERSION literal in {VERSION_JS}")
    return match.group(2)


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(piece) for piece in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(new: str) -> list[Path]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise VersionError(f"{new!r} is not MAJOR.MINOR.PATCH")

    touched = []
    text = VERSION_JS.read_text()
    updated = _VERSION_JS.sub(lambda m: m.group(1) + new + m.group(3), text)
    if updated != text:
        VERSION_JS.write_text(updated)
        touched.append(VERSION_JS)

    if not SW_JS.exists():
        raise VersionError(f"missing {SW_JS}")
    text = SW_JS.read_text()
    updated = _SW_CONST.sub(lambda m: m.group(1) + new + m.group(3), text)
    updated = _SW_COMMENT.sub(lambda m: m.group(1) + new, updated)
    if not _SW_CONST.search(updated):
        raise VersionError(f"no VERSION literal in {SW_JS}")
    if updated != text:
        SW_JS.write_text(updated)
        touched.append(SW_JS)
    return touched


def sw_version() -> str:
    match = _SW_CONST.search(SW_JS.read_text())
    if not match:
        raise VersionError(f"no VERSION literal in {SW_JS}")
    return match.group(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--major", action="store_true")
    group.add_argument("--minor", action="store_true")
    group.add_argument("--patch", action="store_true", help="the default")
    group.add_argument("--set", dest="exact", metavar="X.Y.Z")
    group.add_argument("--show", action="store_true", help="print and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        current = read_version()
        if args.show:
            print(current)
            if sw_version() != current:
                print(f"[!] sw.js says {sw_version()} -- run this script to resync",
                      file=sys.stderr)
                return 1
            return 0

        part = "major" if args.major else "minor" if args.minor else "patch"
        new = args.exact if args.exact else bump(current, part)
        write_version(new)
    except VersionError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"[+] {current} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
