#!/usr/bin/env python3
"""
Resize already-downloaded artwork JPEGs in place down to a max bounding box,
to reclaim repo space. Skips files already within bounds.

Usage:
  python3 resize_existing.py <slug> [<slug> ...]
  python3 resize_existing.py --all
"""
import os
import sys
from PIL import Image

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAX_WIDTH = 1280
MAX_HEIGHT = 800
ART_FOLDERS = ["sidekickboxart", "sidekickfanart", "sidekicklogos", "sidekickscreenshots"]


def resize_file(path):
    with Image.open(path) as img:
        if img.width <= MAX_WIDTH and img.height <= MAX_HEIGHT:
            return None
        orig_size = os.path.getsize(path)
        img = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img.copy()
        img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)
        img.save(path, format="JPEG", quality=80)
        new_size = os.path.getsize(path)
        return orig_size, new_size


def resize_slug(slug):
    total_before = 0
    total_after = 0
    changed = 0
    for folder in ART_FOLDERS:
        d = os.path.join(REPO_ROOT, folder, slug)
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            path = os.path.join(d, fname)
            try:
                result = resize_file(path)
            except Exception as e:
                print(f"[warn] {path}: {e}")
                continue
            if result:
                before, after = result
                total_before += before
                total_after += after
                changed += 1
    if changed:
        saved_mb = (total_before - total_after) / (1024 * 1024)
        print(f"[{slug}] resized {changed} files, saved {saved_mb:.1f} MB")
    else:
        print(f"[{slug}] nothing to resize")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: resize_existing.py <slug> [<slug> ...] | --all")
    if sys.argv[1] == "--all":
        slugs = set()
        for folder in ART_FOLDERS:
            d = os.path.join(REPO_ROOT, folder)
            if os.path.isdir(d):
                slugs.update(os.listdir(d))
        slugs = sorted(s for s in slugs if not s.startswith("."))
    else:
        slugs = sys.argv[1:]
    for slug in slugs:
        resize_slug(slug)
