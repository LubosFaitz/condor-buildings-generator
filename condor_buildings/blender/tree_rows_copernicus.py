"""
Copernicus Small Woody Features - SELF-CONTAINED, REMOVABLE source of trees.

An OPTIONAL second data source for blender/tree_rows.py. OSM only holds what somebody
drew there: in England nearly every field boundary is a mapped hedge, in Slovenia there
is almost nothing. This module fills that in from the Copernicus HRL "Small Woody
Features" layer - the pan-European map of woody vegetation OUTSIDE forests (hedgerows,
tree lines, small groves), 5 m raster.

Everything lives in THIS one file plus a few clearly-marked, try/except-guarded lines in
tree_rows.py. Delete this file (or comment those lines out) and the trees come only from
OSM again, exactly as before.

Limits, by the nature of the data:
  * EUROPE ONLY (EEA countries). Elsewhere the layer is empty and nothing is built.
  * The raster does not say whether it is a bush or a tree - everything becomes a tree.
  * It needs the internet. ONE image per patch is downloaded (not tiles) and cached, so
    a patch is fetched once and never again.

The service is a public ArcGIS ImageServer, no key needed:
  https://copernicus.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic/
      HRL_SmallWoodyFeatures_<year>_005m/ImageServer/exportImage
"""

import os
import math
import logging

import bpy

logger = logging.getLogger(__name__)

SWF_YEAR = 2018               # 2015 / 2018 / 2021 are published
SWF_URL = ("https://copernicus.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic/"
           "HRL_SmallWoodyFeatures_{year}_005m/ImageServer/exportImage")
MASK_PX = 1152                # 5760 m patch / 5 m = one pixel per raster cell
MIN_LUM = 0.15                # anything not (nearly) black is a woody feature
HTTP_TIMEOUT = 60

# --- lines only, no blocks -------------------------------------------------
# The service hands out a plain two-colour mask: it does NOT say which feature is a line
# (a hedgerow, a tree line) and which is a blob (a copse, an overgrown orchard, the edge
# of a village). We only want the LINES, so the shape decides: around a line the
# neighbourhood stays mostly empty, a blob is filled solid.
# A cell is kept when at most LINE_MAX_FILL of the square of +/-LINE_RADIUS cells around
# it is woody. A 2-cell wide hedge inside a 11x11 window fills about 20 %, a blob 100 %.
LINE_RADIUS = 5               # cells (5 m each), so an 11 x 11 = 55 x 55 m window
LINE_MAX_FILL = 0.45

_R = 6378137.0                # web mercator sphere radius (EPSG:3857)
PATCH_SIZE = 5760.0


# ---------------------------------------------------------------------------
# Web mercator (EPSG:3857) - the projection the service wants its bbox in
# ---------------------------------------------------------------------------
def _merc(lat, lon):
    lat = max(-85.05, min(85.05, lat))
    return (_R * math.radians(lon),
            _R * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)))


def _unmerc(mx, my):
    return (math.degrees(2.0 * math.atan(math.exp(my / _R)) - math.pi / 2.0),
            math.degrees(mx / _R))


# ---------------------------------------------------------------------------
# The mask: ONE download per patch, then read from the cache
# ---------------------------------------------------------------------------
def _cache_path(paths, patch_id):
    return os.path.join(paths['autogen'], f"swf_{patch_id}_{SWF_YEAR}.png")


def _download_mask(paths, patch_id, meta):
    """Fetch the woody-features mask of one patch into the cache - a single request for
    the whole patch, no tiles. Returns its path, or None when it is not available (no
    connection, service down)."""
    import urllib.request

    dst = _cache_path(paths, patch_id)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    x0, y0 = _merc(meta.lat_min, meta.lon_min)
    x1, y1 = _merc(meta.lat_max, meta.lon_max)
    url = (SWF_URL.format(year=SWF_YEAR)
           + f"?bbox={x0},{y0},{x1},{y1}&bboxSR=3857&imageSR=3857"
           + f"&size={MASK_PX},{MASK_PX}&format=png&f=image")
    tmp = dst + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "condor-tree-rows"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = r.read()
        if not data:
            return None
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
        return dst
    except Exception as e:
        logger.warning("tree_rows/copernicus: the mask for patch %s could not be "
                       "downloaded: %s", patch_id, e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None


def _read_mask(path, step):
    """[(col, row)] of the woody cells that are part of a LINE, walked with `step` so only
    the cells that can become a tree are returned. Row 0 is the NORTHERN edge.
    Returns (width, height, cells) or None when the file cannot be read."""
    import numpy as np

    try:
        img = bpy.data.images.load(path, check_existing=False)
    except Exception as e:
        logger.warning("tree_rows/copernicus: %s could not be opened: %s",
                       os.path.basename(path), e)
        return None
    try:
        w, h = img.size
        if w < 2 or h < 2:
            return None
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
    except Exception as e:
        logger.warning("tree_rows/copernicus: %s could not be read: %s",
                       os.path.basename(path), e)
        return None
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass

    px = buf.reshape(h, w, 4)[::-1]        # Blender row 0 = bottom, ours = north
    woody = ((px[:, :, 3] > 0.5)
             & (px[:, :, 0:3].mean(axis=2) > MIN_LUM)).astype(np.int32)

    # Blocks out, lines in: the count of woody cells in the window around each cell comes
    # from a summed-area table, so the whole patch costs one pass instead of one window
    # per cell.
    r = LINE_RADIUS
    s = np.zeros((h + 1, w + 1), dtype=np.int32)
    s[1:, 1:] = woody.cumsum(axis=0).cumsum(axis=1)
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    total = (s[np.ix_(y1, x1)] - s[np.ix_(y0, x1)]
             - s[np.ix_(y1, x0)] + s[np.ix_(y0, x0)])
    area = np.outer(y1 - y0, x1 - x0)
    lines = woody & (total <= LINE_MAX_FILL * area)

    ys, xs = np.nonzero(lines[::step, ::step])
    cells = [(int(x) * step, int(y) * step) for y, x in zip(ys, xs)]
    return w, h, cells


# ---------------------------------------------------------------------------
# The one function tree_rows.py calls
# ---------------------------------------------------------------------------
def extra_rows(paths, patch_id, meta, projector, spacing):
    """Tree positions from the Copernicus layer, in the same shape tree_rows.py uses for
    a single tree: (points, height, has_height, id, kind, offsets) with both points the
    same, i.e. exactly one tree. Returns [] whenever there is nothing (also outside
    Europe, where the layer is empty).

    The raster is walked with a step matching the Spacing slider, so these trees come out
    at the same density as the ones built along the OSM lines. tree_rows.py then drops
    any that would stand on top of a tree it has already built."""
    path = _download_mask(paths, patch_id, meta)
    if not path:
        return []
    cell_m = PATCH_SIZE / float(MASK_PX)          # 5 m per raster cell
    step = max(1, int(round(float(spacing) / cell_m)))
    mask = _read_mask(path, step)
    if not mask:
        return []
    w, h, cells = mask

    x0, y0 = _merc(meta.lat_min, meta.lon_min)
    x1, y1 = _merc(meta.lat_max, meta.lon_max)
    dx = (x1 - x0) / float(w)
    dy = (y1 - y0) / float(h)

    out = []
    for (rx, ry) in cells:
        mx = x0 + (rx + 0.5) * dx
        my = y1 - (ry + 0.5) * dy                 # row 0 = north edge
        lat, lon = _unmerc(mx, my)
        p = projector.project(lat, lon)
        out.append(([p, p], 0.0, False, f"swf:{patch_id}:{rx}:{ry}", "copernicus", (0.0,)))
    if out:
        print(f"[tree_rows] patch {patch_id}: {len(out)} tree(s) from the Copernicus "
              f"woody-features layer")
    else:
        print(f"[tree_rows] patch {patch_id}: the Copernicus layer has no woody feature "
              f"here (outside Europe, or genuinely none)")
    return out
