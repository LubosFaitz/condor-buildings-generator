"""
solar_scan.py - finds WHICH PATCHES have solar farms in them.

Walks every h??????.txt in  Landscapes/<landscape>/Working/HeightMaps , asks Overpass
(OSM) for the solar farms over the whole landscape area and writes the patches that
have one into  Working/Autogen/solar_farms.txt  (a header with the scenery name, then
one patch per line followed by the names of the farms in it).

Looks for EXACTLY THE SAME THING AS THE PLUGIN:
    power=plant     + plant:source=solar
    power=generator + generator:source=solar
ways (>= 3 points) and relations (multipolygon); roof panels (location=roof / building)
are left out. No other filters.

The area is cut into bands along its SHORTER axis in degrees - the fewest bands means the
fewest queries, and too many queries is what earns an HTTP 429. Which way that is gets
measured per scenery, so a tall one is cut vertically and a wide one horizontally.

Overpass has limits (HTTP 429), so:
  - every downloaded band is cached (Working/Autogen/solar_scan_cache/), so a re-run
    does not ask again and picks up where it stopped,
  - on a 429 / error it waits and tries again,
  - the list is written AS IT GOES into solar_farms.txt.part, after every band.

solar_farms.txt itself appears only at the very end, by renaming the .part once EVERY band
went through - so the list on disk is never a half-finished one. (The add-on's "Scan solar
farms" button goes by exactly that: no file = not done yet.) The cache is deleted at the
same moment; after an error both the .part and the cache stay, to pick up from.

Run it (from the command line, Blender is not needed - this is a standalone helper, the
add-on never imports it):
    python blender/solar_scan.py "D:\\Condor3BETA" Czech_Republic
    python blender/solar_scan.py              (uses CONDOR_DIR / LANDSCAPE below)
"""

import os
import sys
import math
import time
import shutil
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

# --- defaults (command line arguments override them) -----------------------
CONDOR_DIR = r"D:\Condor3BETA"
LANDSCAPE = "Czech_Republic"

OUT_NAME = "solar_farms.txt"
# The file is mostly read by people from abroad, hence English. The scenery name is filled in.
HEADER = "LIST OF PATCHES WHERE SOLAR FARMS ARE LOCATED ON THE SCENERY:"
NO_NAME = "(no name)"          # a farm with no name tag in OSM
# In OSM a domestic roof PV has EXACTLY THE SAME tags as a farm (power=generator,
# generator:source=solar, ...) - only the SIZE tells them apart. Real farms: FVE Stribro
# 301 270 m2, FVE Pribram 103 540 m2, FVE Stribro I 24 369 m2. Domestic PV: hundreds of m2.
MIN_AREA_M2 = 10000.0    # 1 ha
CHUNK_DEG = 0.5          # the area is asked for in bands of this height
TIMEOUT = 180            # timeout inside the Overpass query (s)
BAND_SLEEP = 15          # pause between bands (s) - to keep Overpass happy
RETRIES = 5              # how many times to retry a band when it fails (429 etc.)
RETRY_WAIT = 60          # base wait between attempts (s), multiplied by the attempt
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


def _read_meta(path):
    """Lat/lon bounds from h<patch>.txt (LatMin/LatMax/LonMin/LonMax lines). None = unreadable."""
    keys = {"latmin": "lat_min", "latmax": "lat_max",
            "lonmin": "lon_min", "lonmax": "lon_max"}
    v = {}
    try:
        with open(path, "r", errors="ignore") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, val = line.split(":", 1)
                k = k.strip().lower().replace("_", "")
                if k in keys:
                    try:
                        v[keys[k]] = float(val.strip())
                    except ValueError:
                        pass
    except OSError:
        return None
    return v if len(v) == 4 else None


def _query(bbox, cache_path):
    """Overpass query for the solar farms in bbox. Tries the on-disk cache first.
    On an error (incl. HTTP 429) it waits and retries. Returns an XML root, or None."""
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        try:
            root = ET.parse(cache_path).getroot()
            print("    (from cache)")
            return root
        except Exception:
            pass                                  # broken cache -> download again

    q = (f"[out:xml][timeout:{TIMEOUT}];\n(\n"
         f'way["power"="plant"]["plant:source"="solar"]({bbox});\n'
         f'relation["power"="plant"]["plant:source"="solar"]({bbox});\n'
         f'way["power"="generator"]["generator:source"="solar"]({bbox});\n'
         f'relation["power"="generator"]["generator:source"="solar"]({bbox});\n'
         ");\nout body;\n>;\nout skel qt;")
    data = urllib.parse.urlencode({"data": q}).encode("utf-8")

    for attempt in range(1, RETRIES + 1):
        for srv in OVERPASS:
            name = srv.split("/")[2]
            try:
                req = urllib.request.Request(
                    srv, data=data, headers={"User-Agent": "condor-solar-scan"})
                with urllib.request.urlopen(req, timeout=TIMEOUT + 60) as r:
                    raw = r.read()
                try:
                    with open(cache_path, "wb") as f:   # save to cache
                        f.write(raw)
                except OSError:
                    pass
                return ET.fromstring(raw)
            except urllib.error.HTTPError as e:
                print(f"    {name}: HTTP {e.code}"
                      f"{'  (query limit)' if e.code == 429 else ''}")
            except Exception as e:
                print(f"    {name}: {e}")
        if attempt < RETRIES:
            wait = RETRY_WAIT * attempt
            print(f"    waiting {wait}s and trying again (attempt {attempt + 1}/{RETRIES}) ...")
            time.sleep(wait)
    return None


def _area_m2(pts):
    """Approximate area of a polygon given in (lat, lon), in m2."""
    if len(pts) < 3:
        return 0.0
    lat0 = sum(p[0] for p in pts) / len(pts)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][1] * kx, pts[i][0] * ky
        x2, y2 = pts[(i + 1) % n][1] * kx, pts[(i + 1) % n][0] * ky
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _farms(root):
    """(lat, lon, name) of every solar farm - EXACTLY the same filter as the plugin:
    power=plant + plant:source=solar  /  power=generator + generator:source=solar,
    ways (>= 3 points) and relations; location=roof and building are left out. Nothing else."""
    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
             for n in root.findall("node")
             if n.get("lat") and n.get("lon")}
    ways = {}
    for w in root.findall("way"):
        pts = [nodes[nd.get("ref")] for nd in w.findall("nd") if nd.get("ref") in nodes]
        if pts:
            ways[w.get("id")] = pts

    def tags(el):
        return {t.get("k"): t.get("v") for t in el.findall("tag")}

    def is_solar(tg):
        if tg.get("location") == "roof" or tg.get("building"):
            return False                       # no roof panels (same as the plugin)
        return ((tg.get("power") == "plant" and tg.get("plant:source") == "solar")
                or (tg.get("power") == "generator"
                    and tg.get("generator:source") == "solar"))

    def take(pts, area, tg, out):
        if len(pts) < 3 or area < MIN_AREA_M2:
            return                         # smaller than a farm = domestic/roof PV
        out.append((sum(p[0] for p in pts) / len(pts),
                    sum(p[1] for p in pts) / len(pts),
                    tg.get("name", "")))

    out = []
    used = set()                       # IDs of ways that are a member of some farm relation
    for rel in root.findall("relation"):
        tg = tags(rel)
        members = [m.get("ref") for m in rel.findall("member")
                   if m.get("type") == "way" and m.get("role") in ("outer", "")
                   and m.get("ref") in ways]
        if not is_solar(tg) or not members:
            continue
        used.update(members)
        take([p for r in members for p in ways[r]],
             sum(_area_m2(ways[r]) for r in members),   # area = sum of the outer rings
             tg, out)

    for w in root.findall("way"):
        wid = w.get("id")
        if wid in used:                # already counted as part of a relation
            continue
        tg = tags(w)
        pts = ways.get(wid)
        if is_solar(tg) and pts:
            take(pts, _area_m2(pts), tg, out)
    return out


def _assign(found, patches, hits):
    """Assigns every farm to the patch its centre falls into."""
    for (la, lo, name) in found:
        for p in patches:
            if (p["lat_min"] <= la <= p["lat_max"]
                    and p["lon_min"] <= lo <= p["lon_max"]):
                hits.setdefault(p["patch_id"], []).append(name or NO_NAME)
                break


def _write(out_path, hits, land):
    """Writes the list of patches - one patch per line, sorted, no duplicates, the names
    of the farms in it after the patch number (comma separated). The farm names are kept
    exactly as they are in OSM (accents and all), so they can be found on a map."""
    head = f"{HEADER} {land}"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(head + "\n")
        f.write("=" * len(head) + "\n\n")
        for pid in sorted(hits):
            names = []
            for n in hits[pid]:                # a farm on a band border is found twice
                if n not in names:
                    names.append(n)
            f.write(f"{pid}   {', '.join(names)}\n")


def main():
    condor = sys.argv[1] if len(sys.argv) > 1 else CONDOR_DIR
    land = sys.argv[2] if len(sys.argv) > 2 else LANDSCAPE
    work = os.path.join(condor, "Landscapes", land, "Working")
    hm_dir = os.path.join(work, "HeightMaps")
    if not os.path.isdir(hm_dir):
        print(f"ERROR: folder does not exist: {hm_dir}")
        return 1

    # 1) every patch from h??????.txt
    patches = []
    for fn in sorted(os.listdir(hm_dir)):
        low = fn.lower()
        if not (low.startswith("h") and low.endswith(".txt")):
            continue
        pid = fn[1:-4]
        if not (len(pid) == 6 and pid.isdigit()):
            continue
        m = _read_meta(os.path.join(hm_dir, fn))
        if m:
            m["patch_id"] = pid
            patches.append(m)
    print(f"patches with h*.txt: {len(patches)}")
    if not patches:
        return 1

    lat_min = min(p["lat_min"] for p in patches)
    lat_max = max(p["lat_max"] for p in patches)
    lon_min = min(p["lon_min"] for p in patches)
    lon_max = max(p["lon_max"] for p in patches)
    print(f"area: lat {lat_min:.4f}..{lat_max:.4f}   lon {lon_min:.4f}..{lon_max:.4f}")

    out_dir = os.path.join(work, "Autogen")
    cache_dir = os.path.join(out_dir, "solar_scan_cache")
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(out_dir, OUT_NAME)
    part_path = out_path + ".part"      # written as we go; renamed only when it's complete

    # 2) which way to cut? Every band costs one query and a pause, and too many queries is
    # what gets us HTTP 429 - so cut along the SHORTER axis IN DEGREES, that gives the fewest
    # bands. Degrees, not km: the cut itself is done in degrees. (A scenery that looks wide on
    # the map can still be wider in degrees than it is tall, so this is measured, not guessed.)
    dlat, dlon = lat_max - lat_min, lon_max - lon_min
    vertical = dlon < dlat
    span_min, span_max = (lon_min, lon_max) if vertical else (lat_min, lat_max)
    n_bands = int(math.ceil((span_max - span_min) / CHUNK_DEG))
    print(f"cutting {'vertically (along lon)' if vertical else 'horizontally (along lat)'}: "
          f"{n_bands} band(s) - the other way would be "
          f"{int(math.ceil((dlat if vertical else dlon) / CHUNK_DEG))}")

    # 3) Overpass band by band (with cache + retry), written as it goes
    total, failed = 0, []
    hits = {}
    written = False
    a, band = span_min, 0
    while a < span_max:
        b = min(a + CHUNK_DEG, span_max)
        band += 1
        print(f"  band {band}/{n_bands}: {'lon' if vertical else 'lat'} {a:.3f}..{b:.3f} ...")
        tag = "lon" if vertical else "lat"
        cache = os.path.join(cache_dir, f"band_{tag}_{a:.3f}_{b:.3f}.osm")
        cached = os.path.exists(cache) and os.path.getsize(cache) > 0
        bbox = (f"{lat_min},{a},{lat_max},{b}" if vertical
                else f"{a},{lon_min},{b},{lon_max}")
        root = _query(bbox, cache)
        if root is None:
            print("    !! band could not be downloaded - run the script again, it picks up from cache")
            failed.append(band)
        else:
            found = _farms(root)
            total += len(found)
            _assign(found, patches, hits)
            _write(part_path, hits, land)
            written = True
            print(f"    farms: {len(found)}   patches so far: {len(hits)}  (written)")
        a = b
        if a < span_max and not cached:
            time.sleep(BAND_SLEEP)               # pause only when something was really downloaded

    # 4) summary
    print()
    for pid in sorted(hits):
        line = (f"  {pid}   {len(hits[pid])}x   {', '.join(hits[pid][:5])}"
                f"{' ...' if len(hits[pid]) > 5 else ''}")
        # the Windows console cannot do every character (e.g. 'I' in a name) -> replace
        enc = sys.stdout.encoding or "utf-8"
        print(line.encode(enc, "replace").decode(enc, "replace"))
    print(f"\nsolar farms: {total}   patches: {len(hits)}")

    # 5) finish. The .part becomes solar_farms.txt ONLY when every band went through, and the
    # rename is instant - so the list on disk is never a half-finished one. The add-on's
    # "Scan solar farms" button keys off exactly that: no file = not done, so it stays offered.
    # A run that failed leaves the .part behind and the cache with it, to pick up from.
    if failed:
        print(f"WARNING: bands {failed} were not downloaded - run the script again (the rest comes from cache)")
        print("         the cache therefore stays (without it everything would download again)")
        if written:
            print(f"partial list left in -> {part_path}")
        return 0
    if not written:
        print("nothing written - not a single band was downloaded")
        return 0
    try:
        os.replace(part_path, out_path)          # atomic: appears complete or not at all
        print(f"written -> {out_path}")
    except OSError as e:
        print(f"could not finish {out_path} ({e}) - the list is in {part_path}")
        return 1
    try:
        shutil.rmtree(cache_dir)
        print("cache deleted")
    except OSError as e:
        print(f"cache could not be deleted ({e}) - you can delete it by hand: {cache_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
