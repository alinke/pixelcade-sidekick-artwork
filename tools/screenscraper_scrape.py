#!/usr/bin/env python3
"""
Scrape game metadata + artwork from screenscraper.fr for a given console.

Credentials are read from environment variables (never hardcode secrets here):
  SS_DEVID, SS_DEVPASSWORD, SS_SOFTNAME, SS_USER, SS_PASSWORD

Usage:
  python3 screenscraper_scrape.py <console-slug> <systemescraper-name-hint> --roms-file <path>

  <console-slug>            key used for folder/file naming, e.g. "atari5200"
                             (must have a matching metadata/console_info/<slug>.json)
  <systemescraper-name-hint> text to fuzzy-match against ScreenScraper's system list,
                             e.g. "Atari 5200"
  --roms-file                text file, one ROM filename (with extension) per line

Writes:
  metadata/descriptions/<slug>.json
  sidekickboxart/<slug>/<romname-no-ext>.jpg
  sidekickfanart/<slug>/<romname-no-ext>.jpg
  sidekicklogos/<slug>/<romname-no-ext>.jpg
  sidekickscreenshots/<slug>/<romname-no-ext>.jpg
  tools/screenscraper-cache/<slug>-<romname-no-ext>[-<arttype>].missing  (negative cache)
"""
import os
import sys
import re
import json
import time
import argparse
import io
import urllib.request
import urllib.parse
import urllib.error
from PIL import Image

MAX_WIDTH = 1280
MAX_HEIGHT = 800

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(REPO_ROOT, "tools", "screenscraper-cache")
SYSTEMS_CACHE = os.path.join(CACHE_DIR, "_systems.json")
BASE_URL = "https://www.screenscraper.fr/api2"

ART_TYPE_TO_FOLDER = {
    "boxart": "sidekickboxart",
    "fanart": "sidekickfanart",
    "logo": "sidekicklogos",
    "screenshot": "sidekickscreenshots",
}
# screenscraper media "type" values we accept per art type, in preference order
MEDIA_TYPE_CANDIDATES = {
    "boxart": ["box-2D"],
    "fanart": ["fanart"],
    "logo": ["wheel"],
    "screenshot": ["ss", "sstitle"],
}
REGION_PREFERENCE = ["us", "wor", "eu", "ss", "jp"]

REQUEST_DELAY_SECONDS = 1.3


def auth_params():
    required = ["SS_DEVID", "SS_DEVPASSWORD", "SS_USER", "SS_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing required env vars: {', '.join(missing)}")
    return {
        "devid": os.environ["SS_DEVID"],
        "devpassword": os.environ["SS_DEVPASSWORD"],
        "softname": os.environ.get("SS_SOFTNAME", "pixelcadesidekick"),
        "ssid": os.environ["SS_USER"],
        "sspassword": os.environ["SS_PASSWORD"],
        "output": "json",
    }


def api_get(endpoint, params, timeout=20):
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "pixelcade-sidekick-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def resize_to_fit(data, max_w=MAX_WIDTH, max_h=MAX_HEIGHT):
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.width <= max_w and img.height <= max_h:
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80)
        return out.getvalue()
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return out.getvalue()


def download(url, dest_path, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "pixelcade-sidekick-scraper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    data = resize_to_fit(data)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(data)


def normalize(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_systems_list():
    if os.path.exists(SYSTEMS_CACHE):
        with open(SYSTEMS_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    raw = api_get("systemesListe.php", auth_params())
    data = json.loads(raw)
    systems = data.get("response", {}).get("systemes", [])
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(SYSTEMS_CACHE, "w", encoding="utf-8") as f:
        json.dump(systems, f)
    return systems


def find_systemeid(name_hint):
    systems = load_systems_list()
    target = normalize(name_hint)
    exact = []
    partial = []
    for sysinfo in systems:
        noms = sysinfo.get("noms", {})
        candidates = []
        if isinstance(noms, dict):
            candidates.extend(noms.values())
        names_list = sysinfo.get("nomsAlternatifs", [])
        if isinstance(names_list, list):
            candidates.extend(names_list)
        for cand in candidates:
            if not isinstance(cand, str):
                continue
            norm_cand = normalize(cand)
            if norm_cand == target:
                exact.append(sysinfo)
                break
            if target in norm_cand or norm_cand in target:
                partial.append(sysinfo)
                break
    pick = exact[0] if exact else (partial[0] if partial else None)
    if not pick:
        sys.exit(f"Could not find a ScreenScraper system matching '{name_hint}'. "
                 f"Inspect {SYSTEMS_CACHE} manually.")
    return pick["id"]


def cache_path(slug, romname, arttype=None):
    if arttype:
        return os.path.join(CACHE_DIR, f"{slug}-{romname}-{arttype}.missing")
    return os.path.join(CACHE_DIR, f"{slug}-{romname}.missing")


def is_marked_missing(slug, romname, arttype=None):
    return os.path.exists(cache_path(slug, romname, arttype))


def mark_missing(slug, romname, arttype=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    open(cache_path(slug, romname, arttype), "a").close()


def pick_media(medias, arttype):
    wanted_types = MEDIA_TYPE_CANDIDATES[arttype]
    best = None
    best_rank = (999, 999)
    for m in medias:
        mtype = m.get("type")
        if mtype not in wanted_types:
            continue
        type_rank = wanted_types.index(mtype)
        region = m.get("region", "")
        region_rank = REGION_PREFERENCE.index(region) if region in REGION_PREFERENCE else len(REGION_PREFERENCE)
        rank = (type_rank, region_rank)
        if rank < best_rank:
            best = m
            best_rank = rank
    return best


def first_text(entries, region_pref=("us", "wor", "eu", "ss", "jp")):
    if isinstance(entries, dict) and "text" in entries:
        return entries["text"]
    if not isinstance(entries, list):
        return None
    by_region = {e.get("region"): e.get("text") for e in entries if isinstance(e, dict)}
    for r in region_pref:
        if by_region.get(r):
            return by_region[r]
    for e in entries:
        if isinstance(e, dict) and e.get("text"):
            return e["text"]
    return None


def first_lang_text(entries, lang_pref=("en", "us", "wor")):
    if isinstance(entries, dict) and "text" in entries:
        return entries["text"]
    if not isinstance(entries, list):
        return None
    by_lang = {e.get("langue"): e.get("text") for e in entries if isinstance(e, dict)}
    for l in lang_pref:
        if by_lang.get(l):
            return by_lang[l]
    for e in entries:
        if isinstance(e, dict) and e.get("text"):
            return e["text"]
    return None


def parse_players(text):
    if not text:
        return None
    nums = re.findall(r"\d+", text)
    if not nums:
        return None
    return int(nums[-1])


def scrape_console(slug, name_hint, roms_file):
    with open(roms_file, "r", encoding="utf-8") as f:
        rom_filenames = [line.strip() for line in f if line.strip()]

    systemeid = find_systemeid(name_hint)
    print(f"[{slug}] systemeid={systemeid}, {len(rom_filenames)} ROMs to process")

    descriptions_path = os.path.join(REPO_ROOT, "metadata", "descriptions", f"{slug}.json")
    games = []
    existing_ids = set()
    if os.path.exists(descriptions_path):
        with open(descriptions_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
            games = existing.get("games", [])
            existing_ids = {g["id"] for g in games}

    auth = auth_params()

    for i, filename in enumerate(rom_filenames, 1):
        romname = os.path.splitext(filename)[0]
        print(f"[{i}/{len(rom_filenames)}] {romname}", end=" ")

        if is_marked_missing(slug, romname):
            print("-> skipped (marked missing)")
            continue

        need_meta = romname not in existing_ids
        need_art = {
            at: not os.path.exists(os.path.join(REPO_ROOT, folder, slug, f"{romname}.jpg"))
                and not is_marked_missing(slug, romname, at)
            for at, folder in ART_TYPE_TO_FOLDER.items()
        }
        if not need_meta and not any(need_art.values()):
            print("-> skipped (already complete)")
            continue

        params = dict(auth)
        params["systemeid"] = systemeid
        params["romnom"] = filename

        try:
            raw = api_get("jeuInfos.php", params)
        except urllib.error.HTTPError as e:
            if e.code in (430, 426, 429):
                print(f"\n[{slug}] quota/rate-limit hit ({e.code}) at '{romname}' -- stopping, progress saved")
                break
            print(f"-> HTTP error {e.code}, treating as not found")
            mark_missing(slug, romname)
            time.sleep(REQUEST_DELAY_SECONDS)
            continue
        except Exception as e:
            print(f"-> request failed ({e}), will retry next run")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        text_body = raw.decode("utf-8", errors="replace")
        if "Erreur" in text_body[:200] and "jeu" not in text_body[:400]:
            low = text_body.lower()
            if "quota" in low or "trop de requ" in low or "max" in low:
                print(f"\n[{slug}] quota message from API at '{romname}' -- stopping, progress saved")
                break

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("-> non-JSON response, treating as not found")
            mark_missing(slug, romname)
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        jeu = data.get("response", {}).get("jeu")
        if not jeu:
            print("-> not found")
            mark_missing(slug, romname)
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if need_meta:
            noms = jeu.get("noms", [])
            clean_name = None
            if isinstance(noms, list):
                for n in noms:
                    if n.get("region") in ("ss", "wor", "us"):
                        clean_name = n.get("text")
                        break
                if not clean_name and noms:
                    clean_name = noms[0].get("text")

            entry = {
                "id": romname,
                "description": first_lang_text(jeu.get("synopsis")),
                "name": clean_name or romname,
                "year": first_text(jeu.get("dates")),
                "publisher": (jeu.get("editeur") or {}).get("text"),
                "genre": first_lang_text(
                    (jeu.get("genres") or [{}])[0].get("noms") if jeu.get("genres") else None
                ),
                "players": parse_players((jeu.get("joueurs") or {}).get("text")),
                "rating": (jeu.get("note") or {}).get("text"),
            }
            games.append(entry)
            existing_ids.add(romname)

        medias = jeu.get("medias", [])
        got = []
        for arttype, folder in ART_TYPE_TO_FOLDER.items():
            if not need_art[arttype]:
                continue
            media = pick_media(medias, arttype)
            if not media or not media.get("url"):
                mark_missing(slug, romname, arttype)
                continue
            dest = os.path.join(REPO_ROOT, folder, slug, f"{romname}.jpg")
            try:
                download(media["url"], dest)
                got.append(arttype)
            except Exception as e:
                print(f"[warn] failed to download {arttype} for '{romname}': {e}")
                mark_missing(slug, romname, arttype)

        print(f"-> ok ({', '.join(got) if got else 'meta only'})")

        os.makedirs(os.path.dirname(descriptions_path), exist_ok=True)
        with open(descriptions_path, "w", encoding="utf-8") as f:
            json.dump({"games": games}, f, indent=2, ensure_ascii=False)

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"[{slug}] done. {len(games)} games with metadata.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", help="console slug, e.g. atari5200")
    parser.add_argument("name_hint", help="ScreenScraper system name to match, e.g. 'Atari 5200'")
    parser.add_argument("--roms-file", required=True, help="text file, one ROM filename per line")
    args = parser.parse_args()
    scrape_console(args.slug, args.name_hint, args.roms_file)
