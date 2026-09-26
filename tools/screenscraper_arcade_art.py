#!/usr/bin/env python3
"""
Download arcade-only artwork from screenscraper.fr -- the cabinet marquee,
one flyer, and the cabinet photo -- for games that already have a Sidekick
logo. One jeuInfos.php lookup per game covers all three.

Game names come from sidekicklogos/<slug>/<name>.jpg, so the new folders
line up 1:1 with the existing art folders.

Arcade only (mame, daphne): a sample of ScreenScraper's "marquee" media came
back 0/40 for every cartridge console tried (Atari 2600/5200/7800, C64).
"screenmarquee"/"screenmarqueesmall" are skipped on purpose -- for consoles
they're a generated template (console logo strip + the same wheel logo we
already have in sidekicklogos over a gradient), not real artwork.

ScreenScraper media types used:
  marquee     -> sidekickmarquees   scan of the real cabinet marquee
  flyer       -> sidekickflyers     ONE flyer: region preference first; a
                                    region's extra flyers carry a "support"
                                    number, the lowest one wins
  support-2D  -> sidekickcabinets   for arcade games this is the cabinet
                                    photo (transparent PNG, flattened onto
                                    black -- the Sidekick card background)

Credentials: same env vars as screenscraper_scrape.py
  SS_DEVID, SS_DEVPASSWORD, SS_SOFTNAME, SS_USER, SS_PASSWORD

Usage:
  python3 screenscraper_arcade_art.py daphne mame

Writes:
  sidekick{marquees,flyers,cabinets}/<slug>/<name>.jpg
  tools/screenscraper-cache/<slug>-<name>-<folder>.missing  (negative cache)
Safe to stop and rerun: files and .missing markers already on disk are skipped.
"""
import io
import os
import sys
import json
import time
import argparse
import urllib.error
import urllib.request

from PIL import Image

from screenscraper_scrape import (
    REPO_ROOT, REQUEST_DELAY_SECONDS, REGION_PREFERENCE, MAX_WIDTH, MAX_HEIGHT,
    auth_params, api_get, is_marked_missing, mark_missing,
)

# console slug -> ScreenScraper systemeid
SYSTEMS = {
    "mame": 75,
    "daphne": 49,
}

# output folder -> ScreenScraper media type
ART_TYPES = {
    "sidekickmarquees": "marquee",
    "sidekickflyers": "flyer",
    "sidekickcabinets": "support-2D",
}


class QuotaHit(Exception):
    pass


def pick_media(medias, media_type):
    """Best single entry of media_type: REGION_PREFERENCE order first, then the
    lowest "support" number within a region (a game's extra flyers are
    numbered; the unnumbered one is the first). Region comes first because a
    game can have an unnumbered Japanese flyer and a numbered English one."""
    best, best_rank = None, None
    for m in medias:
        if m.get("type") != media_type or not m.get("url"):
            continue
        region = m.get("region") or ""
        region_rank = REGION_PREFERENCE.index(region) if region in REGION_PREFERENCE else len(REGION_PREFERENCE)
        rank = (region_rank, int(m.get("support") or 0))
        if best_rank is None or rank < best_rank:
            best, best_rank = m, rank
    return best


def save_jpeg(url, dest_path, timeout=30):
    """Download, flatten any transparency onto black, fit to MAX_WIDTH x MAX_HEIGHT, save as JPEG."""
    req = urllib.request.Request(url, headers={"User-Agent": "pixelcade-sidekick-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (0, 0, 0))
        flat.paste(img, mask=img.split()[-1])
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > MAX_WIDTH or img.height > MAX_HEIGHT:
        img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    img.save(dest_path, format="JPEG", quality=80)


def lookup(auth, systemeid, filename):
    """Returns the game's media list, [] if ScreenScraper doesn't know it, None on a transient error."""
    params = dict(auth, systemeid=systemeid, romnom=filename, romtype="rom")
    try:
        raw = api_get("jeuInfos.php", params)
    except urllib.error.HTTPError as e:
        if e.code in (426, 429, 430, 431):
            raise QuotaHit(e.code)
        if e.code == 404:
            return []
        return None
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        low = raw[:300].decode("utf-8", errors="replace").lower()
        if "quota" in low or "trop de requ" in low:
            raise QuotaHit("message")
        return []
    return (data.get("response", {}).get("jeu") or {}).get("medias", [])


def run(slug):
    systemeid = SYSTEMS[slug]
    names = sorted(f[:-4] for f in os.listdir(os.path.join(REPO_ROOT, "sidekicklogos", slug))
                   if f.endswith(".jpg"))
    auth = auth_params()
    counts = {folder: {"new": 0, "had": 0, "none": 0} for folder in ART_TYPES}
    errors = 0
    for i, name in enumerate(names, 1):
        if i % 100 == 0:
            print(f"[{slug}] {i}/{len(names)}", flush=True)
        needed = []
        for folder in ART_TYPES:
            if os.path.exists(os.path.join(REPO_ROOT, folder, slug, f"{name}.jpg")):
                counts[folder]["had"] += 1
            elif is_marked_missing(slug, name, folder):
                counts[folder]["none"] += 1
            else:
                needed.append(folder)
        if not needed:
            continue

        medias = lookup(auth, systemeid, name + ".zip")
        if medias is None:
            errors += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            continue
        for folder in needed:
            m = pick_media(medias, ART_TYPES[folder])
            if not m:
                mark_missing(slug, name, folder)
                counts[folder]["none"] += 1
                continue
            try:
                save_jpeg(m["url"], os.path.join(REPO_ROOT, folder, slug, f"{name}.jpg"))
                counts[folder]["new"] += 1
            except Exception as e:
                print(f"[{slug}] {folder} download failed for '{name}': {e}", flush=True)
                errors += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    for folder, c in counts.items():
        print(f"[{slug}] {folder}: {c['new'] + c['had']}/{len(names)} "
              f"(new {c['new']}, already had {c['had']}, none {c['none']})", flush=True)
    print(f"[{slug}] errors {errors} (rerun to retry)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="+", choices=sorted(SYSTEMS))
    args = ap.parse_args()
    try:
        for s in args.slugs:
            run(s)
    except QuotaHit as e:
        sys.exit(f"quota/rate-limit hit ({e}) -- stopping, progress saved; rerun to resume")
