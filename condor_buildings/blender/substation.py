"""
Substation outline - SELF-CONTAINED, REMOVABLE add-on.

Mirrors the Bridges feature: everything lives in THIS one file plus ONE tiny,
try/except-guarded hook in blender/operators.py (right after a patch is imported).
Delete this file (or comment out that hook) and the plugin behaves exactly as before --
nothing else depends on it. It does NOT touch buildings, pylons, cables, the pipeline
or the exporter.

What it does:
  OSM marks an electrical switchyard with a closed way tagged ``power=substation``
  (w23232738 "Iver Substation", w28457258 "Rozvodna Reporyje"). This traces that fence
  on the ground and adds it to the scene as its OWN object, 'substation_<patch>', so the
  yard is visible against the aerial photo and can be used as a reference.

  Only real switchyards are drawn: the little kiosks on street corners
  (substation=minor_distribution, a few metres across) are skipped.

Needs ``way["power"="substation"]`` in the Overpass query (blender/osm_downloader.py).
An OSM file downloaded before that was added simply has no substation in it, and then
this quietly does nothing.
"""

import os
import math
import logging
import xml.etree.ElementTree as ET

import bpy

logger = logging.getLogger(__name__)

# Name of the created object: substation_<patch>.
OBJECT_PREFIX = "substation"
# Width of the traced fence line, and how far it is lifted off the ground so it is
# visible and does not z-fight with the terrain.
LINE_WIDTH = 4.0
LIFT = 2.0
# A switchyard is a fenced yard. Anything smaller across than this is a distribution
# kiosk, not a substation.
MIN_SIZE_M = 100.0


def _read_substations(osm_path, projector):
    """Substation fences from OSM, as lists of (x, y) in patch coordinates."""
    root = ET.parse(osm_path).getroot()
    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
             for n in root.findall("node")}

    rings = []
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag")}
        if tags.get("power") != "substation":
            continue

        pts = []
        for nd in way.findall("nd"):
            nid = nd.get("ref")
            if nid in nodes:
                lat, lon = nodes[nid]
                pts.append(projector.project(lat, lon))
        if len(pts) < 3:
            continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < MIN_SIZE_M:
            continue                       # a kiosk, not a switchyard

        logger.info("Substation: way %s '%s', %d nodes, %.0f x %.0f m, %s V",
                    way.get("id"), tags.get("name") or "-", len(pts),
                    max(xs) - min(xs), max(ys) - min(ys), tags.get("voltage"))
        rings.append(pts)

    return rings


def _ground_z(terrain, x, y):
    from ..generators.powerlines import _terrain_z
    z = _terrain_z(terrain, x, y)
    return (terrain.z_min if z is None else z) + LIFT


def _outline_mesh(rings, terrain):
    """The fence as a narrow ribbon lying on the terrain."""
    verts, faces = [], []
    half = LINE_WIDTH / 2.0

    for ring in rings:
        pts = list(ring)
        if len(pts) >= 2 and math.dist(pts[0], pts[-1]) < 1e-6:
            pts.pop()                      # closed way: last point repeats the first
        n = len(pts)
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]      # wrap around: it is a closed loop
            dx, dy = bx - ax, by - ay
            d = math.hypot(dx, dy)
            if d < 1e-6:
                continue
            nx, ny = -dy / d * half, dx / d * half     # sideways: gives the line width
            za = _ground_z(terrain, ax, ay)
            zb = _ground_z(terrain, bx, by)
            base = len(verts)
            verts += [
                (ax + nx, ay + ny, za), (ax - nx, ay - ny, za),
                (bx - nx, by - ny, zb), (bx + nx, by + ny, zb),
            ]
            faces.append((base, base + 1, base + 2, base + 3))

    return verts, faces


def build_for_patch(patch_id, osm_path, paths, collection_name):
    """Add the substation outline of this patch to the scene, as its own object.

    Called from operators.py once a patch has been imported. Does nothing (and never
    raises) when the patch has no substation in its OSM.
    """
    from ..io.patch_metadata import load_patch_metadata
    from ..io.terrain_loader import load_terrain
    from ..projection import create_projector

    meta = load_patch_metadata(os.path.join(paths['heightmaps'], f"h{patch_id}.txt"))
    projector = create_projector(meta.zone_number, meta.translate_x, meta.translate_y)

    rings = _read_substations(osm_path, projector)
    if not rings:
        return 0

    from .terrain_smooth import load_terrain_smoothed
    terrain = load_terrain_smoothed(paths['heightmaps'], patch_id)
    verts, faces = _outline_mesh(rings, terrain)
    if not verts:
        return 0

    name = f"{OBJECT_PREFIX}_{patch_id}"
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)

    coll = bpy.data.collections.get(collection_name) or bpy.context.scene.collection
    coll.objects.link(obj)

    logger.info("Substation outline: '%s' (%d fence(s))", name, len(rings))
    return len(rings)
