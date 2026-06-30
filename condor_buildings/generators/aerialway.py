"""
Aerialway (cable car / chair lift) geometry generator.

Mirrors ``powerlines.py``: places a pylon at every aerialway support node and a
straight cable between consecutive pylon tops, all baked into a single
``aerialway`` object that shares the power lines' material/texture (Pylons.dds).

Phase 1: pylons + straight cable (cabins/chairs follow in phase 2). Reuses the
generic helpers from ``powerlines.py`` (``_foot_z``, ``_yaw_at``, ``_place_pylon``,
``_add_cable``, ``_rotz``, ``_load_obj_template``).
"""

import logging
import math
import os
from typing import Dict, List, Optional, Tuple

from ..models.mesh import MeshData
from ..models.terrain import TerrainMesh
from ..config import PATCH_HALF
from .powerlines import (
    PylonTemplate, _load_obj_template, _default_assets_dir,
    _terrain_z, _yaw_at, _rotz, _place_pylon, _add_cable,
)

logger = logging.getLogger(__name__)


class NeighborTerrain:
    """Loads neighbouring patches' terrain on demand so an aerialway pylon placed
    just OUTSIDE the current patch sits on the real ground of the ADJACENT patch
    (and lines up with that patch's own generation - same node, same yaw, and now
    the same height). The node's (x, y) - in the current patch's local frame - is
    shifted into the neighbour's local frame via the ``translate`` difference; the
    terrain Z is absolute elevation, so the height is directly usable. Each
    neighbour terrain is loaded at most once (cached)."""

    def __init__(self, heightmaps_dir, patch_id, translate_x, translate_y):
        self.dir = heightmaps_dir
        self.col = int(str(patch_id)[:3])
        self.row = int(str(patch_id)[3:])
        self.tx = translate_x
        self.ty = translate_y
        self._cache = {}

    def _neighbor_id(self, x, y, bb):
        dcol = 1 if x > bb.max_x else (-1 if x < bb.min_x else 0)
        drow = 1 if y > bb.max_y else (-1 if y < bb.min_y else 0)
        if dcol == 0 and drow == 0:
            return None
        return f"{self.col + dcol:03d}{self.row + drow:03d}"

    def _load(self, pid):
        if pid not in self._cache:
            import os
            from ..io.patch_metadata import load_patch_metadata
            from ..io.terrain_loader import load_terrain
            try:
                meta = load_patch_metadata(os.path.join(self.dir, f"h{pid}.txt"))
                terr = load_terrain(os.path.join(self.dir, f"h{pid}.obj"))
                self._cache[pid] = (terr, meta.translate_x, meta.translate_y)
            except Exception as e:
                logger.warning("Aerialway: neighbour patch %s terrain unavailable: %s", pid, e)
                self._cache[pid] = None
        return self._cache[pid]

    def foot_z(self, x, y, current_terrain):
        """Height at (x, y) (current-patch frame) from the neighbour patch's
        terrain, or None if (x, y) is in-patch / the neighbour can't be loaded."""
        pid = self._neighbor_id(x, y, current_terrain.bbox)
        if pid is None:
            return None
        loaded = self._load(pid)
        if loaded is None:
            return None
        terr, ntx, nty = loaded
        # local = utm + translate, so the same world point in the neighbour frame
        # is shifted by (neighbour_translate - current_translate).
        nx = x + (ntx - self.tx)
        ny = y + (nty - self.ty)
        return _terrain_z(terr, nx, ny)


def _aerial_foot_z(terrain, x, y, neighbor=None) -> float:
    """Ground Z for an aerialway pylon foot = the terrain SURFACE at the node.
    Inside the patch the current terrain is used; OUTSIDE the patch the adjacent
    patch's terrain (via ``neighbor``) so border pylons sit on the real ground of
    the next patch. Unlike the power-line ``_foot_z`` there is no min-over-ring or
    TOWER_SINK, so the pylon sits ON the surface, not buried."""
    bb = terrain.bbox
    if bb.min_x <= x <= bb.max_x and bb.min_y <= y <= bb.max_y:
        z = _terrain_z(terrain, x, y)
        if z is not None:
            return z
    if neighbor is not None:
        z = neighbor.foot_z(x, y, terrain)
        if z is not None:
            return z
    z = _terrain_z(terrain, x, y)
    return z if z is not None else terrain.z_min

# One merged object; same Pylons.dds material as the power lines (see TEXTURE_MAP).
AERIALWAY_OBJECT = "aerialway"

AERIALWAY_FILES = {
    "Pylon_AerialCab": "Pylon_AerialCab_ns.obj",   # cabin lift pylon (~24.9 m)
    "Pylon_Aerialway": "Pylon_Aerialway_ns.obj",   # chair/drag lift pylon (~11 m)
}

# LOD1 low-poly pylon variants (optional - loaded only if the file exists). When
# building LOD1 the cabin pylon uses this lighter model; everything else stays
# the same. Missing file -> LOD1 falls back to the detailed model.
AERIALWAY_LOW_FILES = {
    "Pylon_AerialCab_Low": "Pylon_AerialCab_ns_low.obj",
}
AERIAL_LOW = {  # pylon_type -> low template key used for LOD1
    "Pylon_AerialCab": "Pylon_AerialCab_Low",
}

# Cable saddles (attach points) in pylon-LOCAL coords (foot at origin), measured
# from the models: two cables per lift.
#   * Cabin: on the SHEAVE WHEELS (Z ~22.59, Y ~+-7.68), not the arm tip.
#   * Chair: on the SHEAVE WHEELS (Z ~8.2, Y ~+-2.8), not the arm top.
AERIAL_ATTACH = {
    "Pylon_AerialCab": [(0.0, 7.678, 22.589), (0.0, -7.678, 22.589)],
    "Pylon_Aerialway": [(0.0, 2.81, 8.20), (0.0, -2.81, 8.20)],
}

# Carriers (cabins / seats) hung from the cables. Model origin sits at the cable
# (the hang point), so the body hangs below.
AERIALWAY_CARRIER_FILES = {
    "Telecabine": "Telecabine.obj",        # cabin (gondola / cable car)
    "Aerialway_Cab": "Aerialway_Cab.obj",  # chair / drag-lift seat
}
# pylon_type -> carrier model key
AERIAL_CARRIER = {
    "Pylon_AerialCab": "Telecabine",
    "Pylon_Aerialway": "Aerialway_Cab",
}
# pylon_type -> spacing between carriers along the cable (m)
AERIAL_SPACING = {
    "Pylon_AerialCab": 77.5,   # cabins 75-80 m apart
    "Pylon_Aerialway": 15.0,   # seats 15 m apart
}
# pylon_type -> extra yaw (rad) added to the carrier. Cabins are flipped 180 deg
# around Z so the hanger kink reaching up to the cable points OUTWARD (away from
# the pylon centre) and clears the rollers; seats keep their travel orientation.
AERIAL_CARRIER_YAW = {
    "Pylon_AerialCab": math.pi,
    "Pylon_Aerialway": 0.0,
}

# Sheave wheels (rollers) of the chair pylon, split off so they can be TILTED to
# the cable's slope. The roller assembly is rotated around the Y axis through
# ROLLER_PIVOT (the axle) by the local cable pitch, then placed like the pylon.
AERIALWAY_ROLLER_FILES = {
    "Pylon_Aerialway_Rollers": "Pylon_Aerialway_rollers.obj",
    "Pylon_AerialCab_Rollers": "Pylon_AerialCab_rollers.obj",
}
AERIAL_ROLLERS = {  # pylon_type -> rollers template key
    "Pylon_Aerialway": "Pylon_Aerialway_Rollers",
    "Pylon_AerialCab": "Pylon_AerialCab_Rollers",
}
# pylon_type -> pivot (axle) the rollers tilt around (Y axis through this point)
AERIAL_ROLLER_PIVOT = {
    "Pylon_Aerialway": (0.0, 0.0, 7.97),
    "Pylon_AerialCab": (0.0, 0.0, 22.34),
}


def load_aerialway_templates(assets_dir: Optional[str] = None) -> Dict[str, PylonTemplate]:
    """Load the aerialway pylon + carrier templates keyed by name."""
    base = assets_dir or _default_assets_dir()
    templates: Dict[str, PylonTemplate] = {}
    for key, filename in AERIALWAY_FILES.items():
        path = os.path.join(base, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Aerialway template not found: {path}")
        templates[key] = _load_obj_template(path)
    # Carriers + rollers + LOD1 low pylons (optional - missing one is skipped).
    for extra in (AERIALWAY_CARRIER_FILES, AERIALWAY_ROLLER_FILES, AERIALWAY_LOW_FILES):
        for key, filename in extra.items():
            path = os.path.join(base, filename)
            if os.path.exists(path):
                templates[key] = _load_obj_template(path)
    return templates


def _place_carriers(mesh, carrier_tmpl, polyline, spacing, start_leftover,
                    yaw_offset=0.0) -> Tuple[int, float]:
    """Walk a polyline of world cable points placing carriers every ``spacing``
    (3D distance). ``start_leftover`` is the distance from the polyline start to
    the FIRST carrier; the carrier origin sits on the cable (hangs below) and its
    yaw follows the local segment heading (plus ``yaw_offset``). Returns
    ``(count, leftover)`` so the walk can continue onto the next polyline keeping
    the spacing continuous (e.g. the return cable). Reverse the polyline to make
    those carriers face the other way (the segment heading flips by 180 deg)."""
    if carrier_tmpl is None or len(polyline) < 2 or spacing <= 0:
        return 0, start_leftover
    count = 0
    leftover = start_leftover
    for a, b in zip(polyline, polyline[1:]):
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        seglen = math.sqrt(dx * dx + dy * dy + dz * dz)
        if seglen < 1e-6:
            continue
        heading = math.atan2(dy, dx)
        d = leftover
        while d < seglen:
            t = d / seglen
            _place_pylon(mesh, carrier_tmpl,
                         a[0] + dx * t, a[1] + dy * t, a[2] + dz * t,
                         heading + yaw_offset)
            count += 1
            d += spacing
        leftover = d - seglen
    return count, leftover


def _tilt_rollers(tmpl: PylonTemplate, pitch: float, pivot) -> PylonTemplate:
    """Return a copy of the rollers template tilted by ``pitch`` (cable slope)
    around the Y axis through ``pivot``, so the wheels follow the cable up/down
    the hill. The roller assembly runs along local X (travel); +pitch lifts its
    forward (+X) end."""
    px, py, pz = pivot
    c, s = math.cos(pitch), math.sin(pitch)
    verts = []
    for (x, y, z) in tmpl.verts:
        dx, dz = x - px, z - pz
        verts.append((px + dx * c - dz * s, y, pz + dx * s + dz * c))
    return PylonTemplate(name=tmpl.name, verts=verts, uvs=tmpl.uvs, faces=tmpl.faces)


def _pitch_at(node_world, xy, j) -> float:
    """Cable pitch (radians) at node j: the slope of the cable through the node,
    from the previous placed node to the next (forward = increasing index, which
    matches local +X after the pylon yaw). 0 at an isolated node."""
    prev = j - 1 if (j - 1) in node_world else j
    nxt = j + 1 if (j + 1) in node_world else j
    if prev == nxt:
        return 0.0
    dz = node_world[nxt][2] - node_world[prev][2]   # foot_z (cable rises with it)
    dxy = math.hypot(xy[nxt][0] - xy[prev][0], xy[nxt][1] - xy[prev][1])
    if dxy < 1e-6:
        return 0.0
    return math.atan2(dz, dxy)


def generate_aerialway_meshes(
    lines,
    terrain: TerrainMesh,
    templates: Optional[Dict[str, PylonTemplate]] = None,
    patch_half: float = PATCH_HALF,
    neighbor=None,
    low: bool = False,
) -> Tuple[MeshData, Dict[str, int]]:
    """
    Build the single ``aerialway`` mesh: a pylon at every support node inside the
    patch (plus the first node just beyond each in-patch run, so cables continue
    across the patch border) and a STRAIGHT cable (sag = 0) between consecutive
    placed pylon tops. Returns (MeshData, stats).

    ``low=True`` builds the LOD1 variant: the cabin pylon uses its low-poly model
    (AERIAL_LOW) if loaded; everything else is identical to LOD0.
    """
    if templates is None:
        templates = load_aerialway_templates()

    mesh = MeshData(osm_id=AERIALWAY_OBJECT)
    stats = {"pylons": 0, "cables": 0, "carriers": 0, "lines": 0}

    for line in lines:
        pts = line.points
        if len(pts) < 2:
            continue
        tmpl = templates.get(line.pylon_type)
        if low:
            low_key = AERIAL_LOW.get(line.pylon_type)
            if low_key and low_key in templates:
                tmpl = templates[low_key]
        attach = AERIAL_ATTACH.get(line.pylon_type, [])
        if tmpl is None:
            logger.warning("No aerialway template for %s (way %s)",
                           line.pylon_type, line.way_id)
            continue

        # A node is "placed" if it's in-patch or adjacent to an in-patch node.
        n = len(pts)
        placed = [False] * n
        for j, p in enumerate(pts):
            if p.in_patch:
                placed[j] = True
                if j > 0:
                    placed[j - 1] = True
                if j < n - 1:
                    placed[j + 1] = True
        if not any(placed):
            continue

        xy = [(p.x, p.y) for p in pts]

        # Pass 1: world transform per placed node (x, y, foot_z, yaw).
        node_world: Dict[int, Tuple[float, float, float, float]] = {}
        for j in range(n):
            if not placed[j]:
                continue
            x, y = xy[j]
            foot_z = _aerial_foot_z(terrain, x, y, neighbor)
            # Aerialway pylons carry the cables on an arm along local Y (the power
            # pylons use local X), so rotate an extra -90 deg to line the arm up
            # perpendicular to the route - same visual orientation as the towers.
            yaw = _yaw_at(xy, j) - math.pi / 2.0
            node_world[j] = (x, y, foot_z, yaw)

        # Pass 2: place the static pylon + (chair only) the rollers tilted to the
        # local cable slope. node_world is complete now so the pitch can look at
        # both neighbouring nodes.
        rollers_key = AERIAL_ROLLERS.get(line.pylon_type)
        rollers_tmpl = templates.get(rollers_key) if rollers_key else None
        rollers_pivot = AERIAL_ROLLER_PIVOT.get(line.pylon_type, (0.0, 0.0, 0.0))
        for j, (x, y, foot_z, yaw) in node_world.items():
            _place_pylon(mesh, tmpl, x, y, foot_z, yaw)
            stats["pylons"] += 1
            if rollers_tmpl is not None:
                _place_pylon(mesh,
                             _tilt_rollers(rollers_tmpl, _pitch_at(node_world, xy, j), rollers_pivot),
                             x, y, foot_z, yaw)

        had_geometry = bool(node_world)

        # Straight cables (sag = 0) between consecutive placed nodes.
        if attach:
            for j in range(n - 1):
                if j not in node_world or (j + 1) not in node_world:
                    continue
                xa, ya, za, yawa = node_world[j]
                xb, yb, zb, yawb = node_world[j + 1]
                ca, sa = math.cos(yawa), math.sin(yawa)
                cb, sb = math.cos(yawb), math.sin(yawb)
                for (ax, ay, az) in attach:
                    rax, ray = _rotz(ax, ay, ca, sa)
                    rbx, rby = _rotz(ax, ay, cb, sb)
                    pa = (rax + xa, ray + ya, az + za)
                    pb = (rbx + xb, rby + yb, az + zb)
                    _add_cable(mesh, pa, pb, 0.0)
                    stats["cables"] += 1

        # Cabins / seats hung from the cables, spaced by type (cabins ~77.5 m,
        # seats 15 m). Placed as ONE continuous loop per contiguous run of placed
        # nodes: up the first cable (forward), then down the second cable
        # (reversed -> those carriers face the other way), with the leftover
        # distance carried over so the spacing stays continuous around the loop.
        carrier_key = AERIAL_CARRIER.get(line.pylon_type)
        carrier_tmpl = templates.get(carrier_key) if carrier_key else None
        spacing = AERIAL_SPACING.get(line.pylon_type, 0.0)
        carrier_yaw = AERIAL_CARRIER_YAW.get(line.pylon_type, 0.0)
        if carrier_tmpl is not None and spacing > 0 and len(attach) >= 2:
            a0, a1 = attach[0], attach[1]

            def _world_pt(a, j):
                x, y, fz, yaw = node_world[j]
                c, s = math.cos(yaw), math.sin(yaw)
                rx, ry = _rotz(a[0], a[1], c, s)
                return (rx + x, ry + y, a[2] + fz)

            # Contiguous runs of placed nodes (cables only span those).
            runs, cur = [], []
            for j in range(n):
                if j in node_world:
                    cur.append(j)
                else:
                    if len(cur) >= 2:
                        runs.append(cur)
                    cur = []
            if len(cur) >= 2:
                runs.append(cur)

            for run in runs:
                up_pts = [_world_pt(a0, j) for j in run]
                down_pts = [_world_pt(a1, j) for j in reversed(run)]
                # Start at the pylon (offset 0), then carry the leftover from the
                # up cable into the down cable so the spacing stays continuous.
                cnt, lo = _place_carriers(mesh, carrier_tmpl, up_pts, spacing, 0.0, carrier_yaw)
                stats["carriers"] += cnt
                cnt, lo = _place_carriers(mesh, carrier_tmpl, down_pts, spacing, lo, carrier_yaw)
                stats["carriers"] += cnt

        if had_geometry:
            stats["lines"] += 1

    logger.info(
        "Aerialway mesh: %d pylons, %d cables, %d carriers across %d lines "
        "(%d verts, %d faces)",
        stats["pylons"], stats["cables"], stats["carriers"], stats["lines"],
        mesh.vertex_count(), mesh.face_count(),
    )
    return mesh, stats
