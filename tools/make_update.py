#!/usr/bin/env python3
"""Pack the committed project into golfmap-update.tgz for pi/update.sh.

    python tools/make_update.py D:\\          # straight onto the USB stick
    python tools/make_update.py dist         # or into a folder

Uses `git archive`, so only committed files go in: commit first. It refuses
if there are uncommitted changes, so the Pi never gets something git can't
reproduce. On the Pi:

    cd ~ && tar xzf /media/fampi/<stick>/golfmap-update.tgz
    sudo bash tashua-golf-map/pi/update.sh
    sudo reboot
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], check=True, capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", help="folder (or drive) to write golfmap-update.tgz into")
    ap.add_argument("--allow-dirty", action="store_true", help="pack HEAD even with uncommitted changes")
    args = ap.parse_args()

    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty and not args.allow_dirty:
        sys.exit(f"Uncommitted changes; commit them first (or --allow-dirty to pack HEAD anyway):\n{dirty}")
    os.makedirs(args.dest, exist_ok=True)
    out = os.path.join(args.dest, "golfmap-update.tgz")
    git("archive", "--format=tar.gz", "--prefix=tashua-golf-map/", "-o", out, "HEAD")
    print(f"Wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB) from commit {git('log', '-1', '--format=%h %s')}")


if __name__ == "__main__":
    main()
