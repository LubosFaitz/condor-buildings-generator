"""
Powerline mesh generator for Condor Buildings Generator.

Turns parsed OSM powerlines (``condor_buildings.io.powerline_parser``) into Condor
mesh geometry: a pylon (tower) stamped at every OSM node of a line, oriented to the
line and sitting on the terrain, plus catenary cables strung between consecutive
towers. Everything is emitted as a single ``pylones`` object so it shares one
material/texture (``Pylons.dds``) and rides in the same patch object file as the
buildings.

Design follows Wiek Schoenmakers' answers (group chat, 2026-06-02 + 2026-06-03):
  * Tower assets: the three low-poly pylons Wiek delivered, extracted upright/Z-up
    to ``condor_buildings/assets/pylons/`` (pylon_large/medium/small.obj). These are
    the LOD1 towers; Wiek will supply more detailed LOD0 assets later.
  * Tower tier: Large (>=110 kV trunk) / Medium (sub-transmission) / Small
    (distribution / minor_line). Chosen in the parser, voltage-tag preferred (Q5).
  * Conductors per tower: 3 large / 4 medium / 2 small, NO shield/earth wires (Q2).
    Medium's 4th is the wire along the top of the tower (confirmed by Wiek).
  * Cables: 3-sided triangular tubes, ~0.1 m thick, single grey, one conductor per
    phase. Sag is a fixed droop per tier: 8 m large / 4 m medium / 2 m small (Q3/Q6).
    UVs land on a small non-degenerate patch of the grey corner of ``Pylons.dds``
    (mapping all verts to a single point renders pink; see CABLE_UV_ORIGIN) (Q4/Q7).
  * Cable sectioning is adaptive: just enough cuts to stay within CABLE_SMOOTH_TOL
    of the true curve (Wiek's vertex-budget request, 2026-06-03).
  * Towers only on OSM nodes, no interpolated intermediate towers (Q6). One shared
    material/texture; powerlines go into the same C3D as the buildings (Q18/Q23).

This module produces a :class:`MeshData`. It can be run standalone to write a
viewer OBJ for a patch::

    python -m condor_buildings.generators.powerlines --patch-dir test_data --patch-id 036019

TODO (cross-patch, Wiek Q13-15): currently a tower is placed one node *over* the
border so cables continue (overlap -> double geometry at seams). Wiek prefers
CLIPPING the cables exactly at the patch boundary instead, since both patches share
the same OSM + projection so the clip point matches. Switch to clipping + cut any
outside loop; needs cross-patch testing.
"""

import argparse
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..models.mesh import MeshData
from ..models.geometry import Point2D, BBox
from ..models.terrain import TerrainMesh
from ..config import PATCH_HALF
from ..io.powerline_parser import _parse_voltage_kv

logger = logging.getLogger(__name__)

# Material/object name shared by towers + cables (Wiek's material is "pylones").
PYLON_MATERIAL = "pylones"
PYLON_TEXTURE = "Pylons.dds"

# Pylon template filenames keyed by the parser's pylon_type.
PYLON_FILES = {
    "Pylon_Large": "pylon_large.obj",
    "Pylon_Medium": "pylon_medium.obj",
    "Pylon_Small": "pylon_small.obj",
    "Pylon_Substation": "pylon_substation.obj",
    # Wind turbine split into a static tower+nacelle and a rotor (blades) so the
    # blades can be spun to a random angle per turbine; merged back into one object.
    "Wind_Turbine_Tower": "turbine_tower.obj",
    "Wind_Turbine_Tower_Low": "turbine_tower_low.obj",
    "Wind_Turbine_Blades": "turbine_blades.obj",
    "Wind_Turbine_Blades_Low": "turbine_blades_low.obj",
}

# Optional low-poly pylon variants for LOD1. Loaded only if the file exists, so a
# missing one (e.g. no medium yet) just falls back to the full model.
OPTIONAL_PYLON_FILES = {
    "Pylon_Large_Low": "pylon_large_low.obj",
    "Pylon_Medium_Low": "pylon_medium_low.obj",
    "Pylon_Substation_Low": "pylon_substation_low.obj",
}

# Rotor hub in the turbine template frame: the blades spin around the Y axis
# passing through this point (disc lies in the XZ plane). Measured from the model.
WT_BLADE_HUB = (0.0, 5.84, 84.23)

# Conductor attachment points in pylon-LOCAL coordinates (upright, foot at origin):
#   x = position along the crossarm, y ~ 0, z = height of the insulator bottom.
# Derived by inspecting the extracted meshes; all CONFIRMED by Wiek (2026-06-03):
#   * Large  -> 3 conductors on the lower crossarm at z~27.8 (x = -13.7, 0, +13.7).
#   * Small  -> 2 conductors at the little crossarm tips (x = +-0.75, z~5.55).
#   * Medium -> 4 wires: 3 arm tips (-6.2/15.2, -5.1/21.0, +5.85/18.2) + 1 along
#     the TOP of the tower (Wiek: "medium indeed has a wire along the top of the
#     towers as you have already done... the wires are strung correctly now").
# NB: Wiek may swap in new tower assets later -> then just update these coords.
CONDUCTOR_ATTACH: Dict[str, List[Tuple[float, float, float]]] = {
    "Pylon_Large": [
        (-13.7, 0.0, 27.8),
        (0.0, 0.0, 27.8),
        (13.7, 0.0, 27.8),
    ],
    "Pylon_Medium": [
        (-6.20, 0.0, 15.24),
        (5.85, 0.0, 18.16),
        (-5.12, 0.0, 21.04),
        (0.0, 0.0, 26.40),   # top-of-tower wire — confirmed by Wiek
    ],
    "Pylon_Small": [
        (-0.75, 0.0, 5.55),
        (0.75, 0.0, 5.55),
    ],
    "Pylon_Substation": [
        (-4.61, 4.64, 14.07),
        (0.00, 4.64, 14.07),
        (4.61, 4.64, 14.07),
    ],
}

# Cable geometry / sag (metres). Confirmed by Wiek (2026-06-03).
# Cables are "0.1 m thick" in his geometry nodes -> tube circumradius ~0.05 m.
CABLE_RADIUS = 0.05

# Constant sag per pylon tier (Wiek: 8 m large / 4 m medium / 2 m small). NOT
# span-based — a fixed droop per tower type, matching his original tool.
SAG_BY_TYPE = {
    "Pylon_Large": 5.0,
    "Pylon_Medium": 2.5,
    "Pylon_Small": 1.2,
}
DEFAULT_SAG = 2.5

# Adaptive cable sectioning (Wiek's vertex-budget request): pick *just enough*
# segments so the straight-segment polyline never deviates more than
# CABLE_SMOOTH_TOL (m) from the true parabola. A parabola of sag s split into N
# equal segments has max deviation s/N^2, so N = ceil(sqrt(s / tol)), clamped.
# Short/near-straight spans get 1-2 cuts; only long deep-sag spans approach the
# cap. Was a fixed 8 cuts per span regardless of length (wasteful on short spans).
CABLE_SMOOTH_TOL = 0.15
CABLE_MIN_SAMPLES = 2
CABLE_MAX_SAMPLES = 14

# Cable UVs: a tiny *non-degenerate* patch inside the uniform grey corner of
# Pylons.dds. Wiek maps cables to "(0,0)" (the bottom-left grey), but mapping all
# three tube vertices to a single point gives zero-area UV triangles, which the
# GPU samples from the mip-averaged texture -> the cables render pink. Instead we
# spread the cable UVs over a small area (a few texels) inside the flat grey patch
# at the bottom-left (measured uniform for >=32x48 px), so faces have UV area and
# sample the solid steel-grey reliably.
CABLE_UV_ORIGIN = (0.02, 0.02)
CABLE_UV_SPREAD = 0.012

# Place a tower a tiny bit into the ground so feet don't float on slopes.
TOWER_SINK = 0.3

# The substation outline, drawn by the powerline module as its OWN object.
SUBSTATION_OUTLINE_NAME = "substation"
SUBSTATION_OUTLINE_WIDTH = 3.0     # width of the traced fence line, metres
SUBSTATION_OUTLINE_LIFT = 1.0      # lifted off the ground so it does not z-fight

# Two large/medium towers must stand at least this far apart (centre to centre). A pylon
# is ~27.7 m wide, so 30 m leaves ~2.3 m between the arm tips -- they never touch.
MIN_TOWER_DIST = 31.5
# Two minor lines sharing a node each stamp their own small pylon on the same spot. The
# second one (and any after it) is raised by this much so the two do not sit inside each
# other with their cables touching.
SMALL_STACK_LIFT = 0.5
# Per-type keep-out circle around a tower origin, as a DIAMETER in metres: the crossarm
# span plus a safety margin (the margin is already included in these numbers). The circles
# of two towers must never overlap, so their cables cannot cross each other. The minimum
# centre-to-centre distance of a pair is therefore the sum of their radii, (da + db) / 2.
# A type that is not listed here (e.g. the substation gantry) falls back to MIN_TOWER_DIST.
TOWER_CLEARANCE = {
    "Pylon_Large": 28.7,
    "Pylon_Medium": 13.0,
    "Pylon_Small": 2.0,
}
# ...but the pushing-apart is done ONLY near a substation (within this of its centre).
# Out in the country the lines are left exactly as OSM has them -- nothing is moved.
SUBSTATION_ZONE_RADIUS = 500.0
# A large/medium tower this close to the substation fence is an ENTRY pylon and is turned
# toward the fence (crossarm blended with the nearest fence edge).
SUBSTATION_ENTRY_DIST = 150.0

# --- Aviation warning balls (on the TOP conductor) ---
# Ball model (origin = centre). Diameter + cable spacing per case:
#   airport, < 69 kV  -> 60 cm / 40 m
#   airport, >= 69 kV -> 60 cm / 30 m   (denser on the big transmission lines)
#   deep valley (any) -> 120 cm / 60 m
WARNING_BALL_FILE = "WarningSphere.obj"
BALL_AIRPORT_DIAMETER = 0.6
BALL_AIRPORT_SPACING_LV = 40.0   # airport, line < 69 kV
BALL_AIRPORT_SPACING_HV = 30.0   # airport, line >= 69 kV
BALL_VALLEY_DIAMETER = 1.2
BALL_VALLEY_SPACING = 60.0
# Top cable higher than this above the terrain below it -> deep-valley balls.
VALLEY_CLEARANCE = 45.0
# Voltage split for the airport spacing (kV).
HV_THRESHOLD_KV = 69.0


# ---------------------------------------------------------------------------
# Pylon templates
# ---------------------------------------------------------------------------

@dataclass
class PylonTemplate:
    """A pylon mesh template loaded from an OBJ (upright, foot at origin, Z-up)."""
    name: str
    verts: List[Tuple[float, float, float]] = field(default_factory=list)
    uvs: List[Tuple[float, float]] = field(default_factory=list)
    normals: List[Tuple[float, float, float]] = field(default_factory=list)
    # Each face: list of (vertex_index, uv_index, normal_index), 0-based.
    faces: List[List[Tuple[int, int, int]]] = field(default_factory=list)

    @property
    def height(self) -> float:
        return max((v[2] for v in self.verts), default=0.0)


def _default_assets_dir() -> str:
    """Package-internal pylon assets dir (ships in the addon ZIP)."""
    return os.path.join(os.path.dirname(__file__), "..", "assets", "pylons")


def pylon_assets_dir() -> str:
    """
    Absolute path to the packaged pylon assets directory.

    Ships inside the addon ZIP and holds the three pylon OBJ templates plus the
    shared ``Pylons.dds`` texture. Public so the Blender operators can add it to
    the texture search path (preview) and copy the texture next to the export.
    """
    return os.path.normpath(_default_assets_dir())


def pylon_texture_path() -> Optional[str]:
    """Path to the packaged ``Pylons.dds``, or None if it isn't bundled."""
    path = os.path.join(pylon_assets_dir(), PYLON_TEXTURE)
    return path if os.path.exists(path) else None


def _load_obj_template(path: str) -> PylonTemplate:
    """Parse a (small) OBJ pylon template: v / vt / vn / f 'v/vt/vn' tokens."""
    verts: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    normals: List[Tuple[float, float, float]] = []
    faces: List[List[Tuple[int, int, int]]] = []
    name = os.path.splitext(os.path.basename(path))[0]

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split()
                uvs.append((float(p[1]), float(p[2])))
            elif line.startswith("vn "):
                p = line.split()
                normals.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("o "):
                name = line[2:].strip()
            elif line.startswith("f "):
                face: List[Tuple[int, int, int]] = []
                for tok in line.split()[1:]:
                    bits = tok.split("/")
                    vi = int(bits[0]) - 1
                    ti = int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1
                    ni = int(bits[2]) - 1 if len(bits) > 2 and bits[2] else -1
                    face.append((vi, ti, ni))
                faces.append(face)
    return PylonTemplate(name=name, verts=verts, uvs=uvs, normals=normals, faces=faces)


def load_pylon_templates(assets_dir: Optional[str] = None) -> Dict[str, PylonTemplate]:
    """Load the three pylon templates keyed by pylon_type."""
    base = assets_dir or _default_assets_dir()
    templates: Dict[str, PylonTemplate] = {}
    for pylon_type, filename in PYLON_FILES.items():
        path = os.path.join(base, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Pylon template not found: {path}")
        templates[pylon_type] = _load_obj_template(path)
        logger.debug(
            "Loaded %s: %d verts, %.1f m tall",
            pylon_type, len(templates[pylon_type].verts), templates[pylon_type].height,
        )
    # Optional low-poly pylon variants (LOD1): load only if present.
    for pylon_type, filename in OPTIONAL_PYLON_FILES.items():
        path = os.path.join(base, filename)
        if os.path.exists(path):
            templates[pylon_type] = _load_obj_template(path)
            logger.debug("Loaded optional %s (%d verts)", pylon_type,
                         len(templates[pylon_type].verts))
    return templates


def _tower_clearance(tmpl: Optional[PylonTemplate]) -> float:
    """
    Diameter of the keep-out circle of one tower (see TOWER_CLEARANCE).

    A template is named after its OBJ file ("pylon_large", "pylon_medium_low", ...), so
    the type is recognised by the keyword in that name -- the same way the rest of the
    module does it. Unknown types (the substation gantry) keep the old MIN_TOWER_DIST.
    """
    name = tmpl.name.lower() if tmpl is not None else ""
    for pylon_type, diameter in TOWER_CLEARANCE.items():
        if pylon_type.split("_")[-1].lower() in name:
            return diameter
    return MIN_TOWER_DIST


def _min_tower_dist(tmpl_a: Optional[PylonTemplate],
                    tmpl_b: Optional[PylonTemplate]) -> float:
    """How close two towers may stand (centre to centre): the sum of their radii."""
    return (_tower_clearance(tmpl_a) + _tower_clearance(tmpl_b)) / 2.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _terrain_z(terrain: TerrainMesh, x: float, y: float) -> Optional[float]:
    """
    Terrain elevation at (x, y) via the terrain's spatial index.

    Border towers are placed just *outside* the patch so cables continue across
    the edge, but the terrain mesh only covers the patch (+-PATCH_HALF). For a
    query that lands outside terrain coverage we clamp to the terrain bbox and use
    the nearest edge height — a good approximation since border towers sit only a
    span beyond the edge. Returns None only if the terrain is empty at the edge too.
    """
    def query(qx: float, qy: float) -> Optional[float]:
        bbox = BBox(qx - 0.5, qy - 0.5, qx + 0.5, qy + 0.5)
        pt = Point2D(qx, qy)
        for ti in terrain.get_triangles_in_bbox(bbox):
            tri = terrain.triangles[ti]
            if tri.contains_point_2d(pt):
                z = tri.z_at_xy(qx, qy)
                if z is not None:
                    return z
        return None

    z = query(x, y)
    if z is not None:
        return z

    bb = terrain.bbox
    cx = min(max(x, bb.min_x + 1.0), bb.max_x - 1.0)
    cy = min(max(y, bb.min_y + 1.0), bb.max_y - 1.0)
    if cx != x or cy != y:
        return query(cx, cy)
    return None


def _point_in_polys(x: float, y: float, polys) -> bool:
    """True if (x, y) lies inside any of the substation outlines (ray casting)."""
    for ring in polys or ():
        inside = False
        n = len(ring)
        jx, jy = ring[-1]
        for ix, iy in ring:
            if ((iy > y) != (jy > y)) and \
               (x < (jx - ix) * (y - iy) / (jy - iy + 1e-12) + ix):
                inside = not inside
            jx, jy = ix, iy
        if inside:
            return True
    return False


def _gantry_points_inside(gx: float, gy: float, yaw: float, polys) -> bool:
    """True if the gantry center and its physical bounds are inside the substation outlines."""
    c, s = math.cos(yaw), math.sin(yaw)
    # Check center and the four extremities of the Pylon_Substation template bounding box
    # (width is ~12.2m: X from -6.1 to 6.1; depth is ~5.5m: Y from -0.5 to 5.0).
    points = [
        (gx, gy),                           # center
        (gx - 6.1 * c, gy - 6.1 * s),       # left tip
        (gx + 6.1 * c, gy + 6.1 * s),       # right tip
        (gx - 5.0 * s, gy + 5.0 * c),       # front
        (gx + 0.5 * s, gy - 0.5 * c),       # back
    ]
    return all(_point_in_polys(px, py, polys) for px, py in points)


def _gantry_corners(gx: float, gy: float, yaw: float) -> List[Tuple[float, float]]:
    """Return the 4 world-space corners of the gantry rectangle."""
    c, s = math.cos(yaw), math.sin(yaw)
    # Local corners:
    local_corners = [(-6.1, -0.5), (6.1, -0.5), (6.1, 5.0), (-6.1, 5.0)]
    world_corners = []
    for lx, ly in local_corners:
        wx = gx + lx * c - ly * s
        wy = gy + lx * s + ly * c
        world_corners.append((wx, wy))
    return world_corners


def _rects_intersect(rect1: List[Tuple[float, float]], rect2: List[Tuple[float, float]]) -> bool:
    """Check if two 2D oriented rectangles intersect using the Separating Axis Theorem (SAT)."""
    for rect in (rect1, rect2):
        for i in range(4):
            p1 = rect[i]
            p2 = rect[(i + 1) % 4]
            # Normal to the edge
            nx = -(p2[1] - p1[1])
            ny = p2[0] - p1[0]
            dist = math.hypot(nx, ny)
            if dist < 1e-9:
                continue
            nx /= dist
            ny /= dist
            
            proj1 = [v[0] * nx + v[1] * ny for v in rect1]
            proj2 = [v[0] * nx + v[1] * ny for v in rect2]
            
            min1, max1 = min(proj1), max(proj1)
            min2, max2 = min(proj2), max(proj2)
            
            if max1 < min2 - 1e-3 or max2 < min1 - 1e-3:
                return False  # Separating axis found
    return True  # No separating axis found, they intersect


def _segments_intersect(a: Tuple[float, float], b: Tuple[float, float],
                        c: Tuple[float, float], d: Tuple[float, float]) -> bool:
    """True if line segments AB and CD intersect in 2D."""
    def ccw(A, B, C):
        return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])
    return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)


def _gantry_collides_with_tower(gx: float, gy: float, yaw: float, tx: float, ty: float, r: float) -> bool:
    """Check if a gantry rectangle intersects a circle (tower with radius r)."""
    c, s = math.cos(yaw), math.sin(yaw)
    dx = tx - gx
    dy = ty - gy
    # Local coordinates of tower center relative to gantry
    lx = dx * c + dy * s
    ly = -dx * s + dy * c
    # Closest point on gantry AABB (X in [-6.1, 6.1], Y in [-0.5, 5.0])
    cx = max(-6.1, min(6.1, lx))
    cy = max(-0.5, min(5.0, ly))
    # Distance from tower center to closest point
    dist = math.hypot(lx - cx, ly - cy)
    return dist < r


def _foot_z(terrain: TerrainMesh, x: float, y: float, neighbor=None) -> float:
    """
    Ground Z for a tower foot. Samples the node and a small ring around it and
    takes the minimum so the tower sits on (slightly into) the lowest nearby
    ground rather than floating, then sinks by TOWER_SINK.

    A border tower stands just OUTSIDE the patch, where this patch's terrain has
    no coverage. With ``neighbor`` (a NeighborTerrain) the samples are taken from
    the ADJACENT patch's terrain instead, so the tower stands at the real ground
    height and matches the very same tower generated by that patch. Without a
    neighbour - or when it cannot be loaded - the old fallback applies: the
    samples clamp to this patch's edge (see ``_terrain_z``).
    """
    samples = [(x, y)]
    for dx, dy in ((4.0, 0.0), (-4.0, 0.0), (0.0, 4.0), (0.0, -4.0)):
        samples.append((x + dx, y + dy))

    bb = terrain.bbox
    if neighbor is not None and not (bb.min_x <= x <= bb.max_x and
                                     bb.min_y <= y <= bb.max_y):
        nzs = [z for z in (neighbor.foot_z(sx, sy, terrain) for sx, sy in samples)
               if z is not None]
        if nzs:
            return min(nzs) - TOWER_SINK

    zs = [z for z in (_terrain_z(terrain, sx, sy) for sx, sy in samples) if z is not None]
    if not zs:
        return terrain.z_min - TOWER_SINK
    return min(zs) - TOWER_SINK


def _yaw_at(points: List[Tuple[float, float]], j: int) -> float:
    """
    Crossarm yaw (radians) at point j: the crossarm is built perpendicular to the
    line so consecutive towers' conductor tips line up along the cables. Uses the
    bisector of the adjacent segments at interior points.
    """
    n = len(points)
    px, py = points[j]

    def seg_dir(a, b):
        dx, dy = points[b][0] - points[a][0], points[b][1] - points[a][1]
        d = math.hypot(dx, dy)
        return (dx / d, dy / d) if d > 1e-9 else None

    d_in = seg_dir(j - 1, j) if j > 0 else None
    d_out = seg_dir(j, j + 1) if j < n - 1 else None

    if d_in and d_out:
        tx, ty = d_in[0] + d_out[0], d_in[1] + d_out[1]
        if math.hypot(tx, ty) < 1e-9:   # near-reversal, fall back to outgoing
            tx, ty = d_out
    elif d_out:
        tx, ty = d_out
    elif d_in:
        tx, ty = d_in
    else:
        tx, ty = 1.0, 0.0

    return math.atan2(ty, tx) + math.pi / 2.0


def _rotz(x: float, y: float, c: float, s: float) -> Tuple[float, float]:
    return (x * c - y * s, x * s + y * c)


def _place_pylon(
    mesh: MeshData,
    tmpl: PylonTemplate,
    x: float,
    y: float,
    foot_z: float,
    yaw: float,
) -> None:
    """Stamp a rotated/translated copy of a pylon template into the mesh."""
    c, s = math.cos(yaw), math.sin(yaw)
    v_base = mesh.vertex_count()
    uv_base = mesh.uv_count()

    # Initialise normal storage on the MeshData (dynamic attributes).
    if not hasattr(mesh, '_normals'):
        mesh._normals = []
        mesh._face_normal_map = {}
    n_base = len(mesh._normals)

    for vx, vy, vz in tmpl.verts:
        rx, ry = _rotz(vx, vy, c, s)
        mesh.add_vertex(rx + x, ry + y, vz + foot_z)
    for u, v in tmpl.uvs:
        mesh.add_uv(u, v)
    # Rotate normals the same way as vertices and store them.
    for nx, ny, nz in tmpl.normals:
        rnx, rny = _rotz(nx, ny, c, s)
        mesh._normals.append((rnx, rny, nz))

    for face in tmpl.faces:
        vidx = [v_base + f[0] + 1 for f in face]
        uvidx = [uv_base + (f[1] if f[1] >= 0 else 0) + 1 for f in face]
        mesh.add_polygon_with_uvs(vidx, uvidx)
        # Store normal indices for this face (so exporter / converter can use them).
        nidx = [n_base + f[2] + 1 for f in face if f[2] >= 0]
        if len(nidx) == len(face):
            mesh._face_normal_map[len(mesh.faces) - 1] = nidx


def _cable_sections(sag: float) -> int:
    """
    Number of length-wise segments for a cable of the given sag, so the chord
    polyline stays within CABLE_SMOOTH_TOL of the true parabola (max dev = s/N^2).
    """
    if sag <= 1e-6:
        return CABLE_MIN_SAMPLES
    n = math.ceil(math.sqrt(sag / CABLE_SMOOTH_TOL))
    return max(CABLE_MIN_SAMPLES, min(CABLE_MAX_SAMPLES, n))


def _add_cable(
    mesh: MeshData,
    p0: Tuple[float, float, float],
    p1: Tuple[float, float, float],
    sag: float,
) -> None:
    """
    Add one catenary cable as a 3-sided triangular tube between p0 and p1.

    ``sag`` (m) is the fixed droop for this pylon tier (SAG_BY_TYPE). The cable
    droops in the vertical plane of the span (parabola approximation), with a
    constant cross-section frame (horizontal-perpendicular + vertical) so the tube
    never twists. UVs land on a small non-degenerate patch of the flat grey corner
    of Pylons.dds (see CABLE_UV_ORIGIN) so the tube samples a solid steel-grey
    instead of the mip-averaged (pink) texture.
    """
    dx, dy, dz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
    hlen = math.hypot(dx, dy)
    n_samples = _cable_sections(sag)

    # Constant cross-section axes: n1 horizontal perpendicular to span, n2 vertical.
    if hlen > 1e-6:
        n1 = (-dy / hlen, dx / hlen, 0.0)
    else:
        n1 = (1.0, 0.0, 0.0)
    n2 = (0.0, 0.0, 1.0)

    # Equilateral triangle cross-section (one vertex up).
    angles = (math.radians(90), math.radians(210), math.radians(330))
    offsets = [
        (
            CABLE_RADIUS * (math.cos(a) * n1[0] + math.sin(a) * n2[0]),
            CABLE_RADIUS * (math.cos(a) * n1[1] + math.sin(a) * n2[1]),
            CABLE_RADIUS * (math.cos(a) * n1[2] + math.sin(a) * n2[2]),
        )
        for a in angles
    ]

    u0, v0 = CABLE_UV_ORIGIN
    # rings[i] = list of (vertex_index, uv_index) for the 3 tube sides at sample i
    rings: List[List[Tuple[int, int]]] = []
    for i in range(n_samples + 1):
        t = i / n_samples
        cx = p0[0] + dx * t
        cy = p0[1] + dy * t
        cz = p0[2] + dz * t - 4.0 * sag * t * (1.0 - t)
        ring: List[Tuple[int, int]] = []
        for side, (ox, oy, oz) in enumerate(offsets):
            vidx = mesh.add_vertex(cx + ox, cy + oy, cz + oz)
            uidx = mesh.add_uv(u0 + CABLE_UV_SPREAD * (side * 0.5),
                               v0 + CABLE_UV_SPREAD * (i % 2))
            ring.append((vidx, uidx))
        rings.append(ring)

    face_start = len(mesh.faces)
    for i in range(n_samples):
        for k in range(3):
            a = rings[i][k]
            b = rings[i][(k + 1) % 3]
            c = rings[i + 1][(k + 1) % 3]
            d = rings[i + 1][k]
            mesh.add_quad_with_uvs(a[0], b[0], c[0], d[0],
                                   a[1], b[1], c[1], d[1])
    if not hasattr(mesh, 'smooth_faces'):
        mesh.smooth_faces = set()
    mesh.smooth_faces.update(range(face_start, len(mesh.faces)))
            


# ---------------------------------------------------------------------------
# Warning balls (on the top conductor)
# ---------------------------------------------------------------------------

def _top_attach(attach_local):
    """
    The single highest conductor attach point (ties -> the one nearest the tower
    centre). So warning balls land on ONE top cable even when several conductors
    share the top height. Returns (x, y, z) local, or None.
    """
    if not attach_local:
        return None
    zmax = max(a[2] for a in attach_local)
    tops = [a for a in attach_local if abs(a[2] - zmax) < 1e-6]
    return min(tops, key=lambda a: abs(a[0]))


def _place_ball(mesh: MeshData, tmpl: PylonTemplate,
                x: float, y: float, z: float, scale: float) -> None:
    """Stamp a uniformly-scaled ball template centred at (x, y, z)."""
    v_base = mesh.vertex_count()
    uv_base = mesh.uv_count()

    # Same normal handling as _place_pylon, so the ball keeps its shading in the
    # export instead of falling back to flat per-triangle normals (faceted look).
    if not hasattr(mesh, '_normals'):
        mesh._normals = []
        mesh._face_normal_map = {}
    n_base = len(mesh._normals)

    for vx, vy, vz in tmpl.verts:
        mesh.add_vertex(vx * scale + x, vy * scale + y, vz * scale + z)
    for u, v in tmpl.uvs:
        mesh.add_uv(u, v)
    # The ball is never rotated, so model normals go in unchanged. A model with
    # no normals gets radial ones (the template is already centred on its own
    # bbox), which is exactly the smooth sphere shading.
    from_model = bool(tmpl.normals)
    if from_model:
        mesh._normals.extend(tmpl.normals)
    else:
        for vx, vy, vz in tmpl.verts:
            d = math.sqrt(vx * vx + vy * vy + vz * vz)
            mesh._normals.append((vx / d, vy / d, vz / d) if d > 1e-9 else (0.0, 0.0, 1.0))

    for face in tmpl.faces:
        vidx = [v_base + f[0] + 1 for f in face]
        uvidx = [uv_base + (f[1] if f[1] >= 0 else 0) + 1 for f in face]
        mesh.add_polygon_with_uvs(vidx, uvidx)
        # Model normals are indexed per corner (f[2]), radial ones per vertex (f[0]).
        nidx = ([n_base + f[2] + 1 for f in face if f[2] >= 0] if from_model
                else [n_base + f[0] + 1 for f in face])
        if len(nidx) == len(face):
            mesh._face_normal_map[len(mesh.faces) - 1] = nidx


def _place_balls_on_cable(mesh, ball_tmpl, scale, pa, pb, sag, spacing) -> int:
    """
    Distribute balls along the catenary top cable pa->pb at ~`spacing` metres,
    evenly spread (none sitting on the pylons). Returns the number placed.
    """
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    span = math.hypot(dx, dy)
    if span < 1e-6:
        return 0

    # The cable is DRAWN as a polyline (straight chords between `ns` sample points
    # on the parabola). The smooth parabola dips BELOW those chords, so a ball put
    # on the parabola hangs under the drawn wire. Put the ball on the SAME chord
    # the cable renders, so it sits exactly on the wire.
    ns = _cable_sections(sag)

    def _parab(tt):
        return pa[2] + (pb[2] - pa[2]) * tt - 4.0 * sag * tt * (1.0 - tt)

    def cable_z(t):
        seg = min(int(t * ns), ns - 1)
        t0, t1 = seg / ns, (seg + 1) / ns
        z0, z1 = _parab(t0), _parab(t1)
        f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return z0 + (z1 - z0) * f

    n_balls = max(1, int(round(span / spacing)))
    for k in range(n_balls):
        t = (k + 0.5) / n_balls
        cx = pa[0] + dx * t
        cy = pa[1] + dy * t
        _place_ball(mesh, ball_tmpl, cx, cy, cable_z(t), scale)
    return n_balls


def _line_is_hv(line):
    """
    >= 69 kV? Uses the real ``voltage`` tag; if it's missing, treats the big
    transmission towers (Pylon_Large) as HV and everything else as < 69 kV.
    """
    kv = _parse_voltage_kv(line.voltage)
    if kv is not None:
        return kv >= HV_THRESHOLD_KV
    return line.pylon_type == "Pylon_Large"


def _ball_mode_for_span(pa, pb, sag, terrain, airport_zones, is_hv):
    """
    Decide whether (and how) a span's top cable gets balls. Returns
    (diameter, spacing) or None.
      * deep valley (top cable > VALLEY_CLEARANCE above terrain) -> 120 cm / 60 m (any line)
      * inside an airport zone -> 60 cm, 30 m if line >= 69 kV else 40 m
    """
    # Valley check first (sample a few points of the catenary vs the terrain below).
    max_clear = 0.0
    for t in (0.25, 0.5, 0.75):
        cx = pa[0] + (pb[0] - pa[0]) * t
        cy = pa[1] + (pb[1] - pa[1]) * t
        cz = pa[2] + (pb[2] - pa[2]) * t - 4.0 * sag * t * (1.0 - t)
        tz = _terrain_z(terrain, cx, cy)
        if tz is not None:
            max_clear = max(max_clear, cz - tz)
    if max_clear > VALLEY_CLEARANCE:
        return (BALL_VALLEY_DIAMETER, BALL_VALLEY_SPACING)

    # Airport zone check (span midpoint inside a runway zone).
    mx, my = (pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0
    for (zx, zy, zr) in airport_zones:
        if math.hypot(mx - zx, my - zy) <= zr:
            spacing = BALL_AIRPORT_SPACING_HV if is_hv else BALL_AIRPORT_SPACING_LV
            return (BALL_AIRPORT_DIAMETER, spacing)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class PowerlineMeshStats:
    towers: int = 0
    cables: int = 0
    lines_with_geometry: int = 0
    turbines: int = 0
    balls: int = 0


def generate_substation_outline(substation_polygons, terrain: TerrainMesh) -> MeshData:
    """Trace the substation fence(s) as a narrow ribbon -- its OWN object, built here in
    the powerline module. Empty mesh when the patch has no substation."""
    mesh = MeshData(osm_id=SUBSTATION_OUTLINE_NAME)
    half = SUBSTATION_OUTLINE_WIDTH / 2.0
    for ring in (substation_polygons or []):
        pts = list(ring)
        if len(pts) >= 2 and math.dist(pts[0], pts[-1]) < 1e-6:
            pts.pop()
        if len(pts) < 3:
            continue
        n = len(pts)
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            dx, dy = bx - ax, by - ay
            d = math.hypot(dx, dy)
            if d < 1e-6:
                continue
            nx, ny = -dy / d * half, dx / d * half
            za = _terrain_z(terrain, ax, ay)
            zb = _terrain_z(terrain, bx, by)
            za = (terrain.z_min if za is None else za) + SUBSTATION_OUTLINE_LIFT
            zb = (terrain.z_min if zb is None else zb) + SUBSTATION_OUTLINE_LIFT
            base = len(mesh.vertices)
            mesh.vertices.extend([
                (ax + nx, ay + ny, za), (ax - nx, ay - ny, za),
                (bx - nx, by - ny, zb), (bx + nx, by + ny, zb),
            ])
            mesh.uvs.extend([(0.02, 0.02), (0.03, 0.02), (0.03, 0.03), (0.02, 0.03)])
            mesh.faces.append((base, base + 1, base + 2))
            mesh.faces.append((base, base + 2, base + 3))
    return mesh


def generate_powerline_meshes(
    lines,
    terrain: TerrainMesh,
    templates: Optional[Dict[str, PylonTemplate]] = None,
    patch_half: float = PATCH_HALF,
    draw_cables: bool = True,
    draw_balls: bool = False,
    airport_zones=None,
    substation_polygons=None,
    neighbor=None,
    border_log=None,
    patch_id=None,
) -> Tuple[MeshData, MeshData, PowerlineMeshStats]:
    """
    Build the single ``pylones`` mesh (towers + cables) for a list of PowerLines.

    A tower is placed at every line node that is inside the patch, plus the first
    node just beyond each in-patch run on either side, so cables continue across
    the patch border (mirrors Wiek's manual "keep the first pylon over the border"
    trick, but automatic). Cables connect consecutive placed nodes.

    Args:
        lines: PowerLine records from ``parse_powerlines``.
        terrain: Loaded TerrainMesh (with spatial index) for foot elevations.
        templates: Pre-loaded pylon templates (loaded from package if None).
        patch_half: Patch half-size in metres.
        draw_cables: If False, only place towers (placement-only preview).
        neighbor: Optional NeighborTerrain, so a tower placed just beyond the
            patch border takes its foot height from the ADJACENT patch's terrain
            and lines up with that patch's own copy of the same tower.
        border_log: Optional BorderPylonLog shared by all patches of the landscape.
            A tower just beyond the border that the NEIGHBOURING patch has already
            built is not built again -- its position, foot height and yaw are read
            from the log and only the cables are hung on it. Without a log the
            generator behaves exactly as before (every patch builds its own copy).
        patch_id: Id of the patch being generated, so the log can tell this patch's
            own records from a neighbour's. Only used together with ``border_log``.

    Returns:
        (MeshData for the 'pylones' object, PowerlineMeshStats).
    """
    if templates is None:
        templates = load_pylon_templates()

    # DIAGNOSTIC: how many substation outlines reached the powerline generator. If this
    # says 0, the spacing/entry-orientation cannot run (no fence to work from).
    logger.info("POWERLINES: received %d substation outline(s)",
                len(substation_polygons or []))
    print(f"[Condor] POWERLINES got {len(substation_polygons or [])} substation outline(s)")

    mesh = MeshData(osm_id=PYLON_MATERIAL)    # LOD0: all towers + cables + balls
    mesh1 = MeshData(osm_id=PYLON_MATERIAL)   # LOD1: large+medium pylons only
    stats = PowerlineMeshStats()
    # how many small pylons already stand on a spot (see SMALL_STACK_LIFT)
    _small_at: Dict[Tuple[float, float], int] = {}

    # Warning-ball template (loaded only when requested; never breaks powerlines).
    ball_tmpl = None
    ball_native_d = 1.0
    airport_zones = airport_zones or []
    if draw_balls:
        try:
            ball_tmpl = _load_obj_template(
                os.path.join(_default_assets_dir(), WARNING_BALL_FILE)
            )
            xs = [v[0] for v in ball_tmpl.verts]
            ys = [v[1] for v in ball_tmpl.verts]
            zs = [v[2] for v in ball_tmpl.verts]
            # Re-centre the ball on its own bounding box, so wherever the model's
            # origin sits, the ball's CENTRE lands exactly on the cable (otherwise
            # an off-centre origin makes the ball hang above/below the wire).
            cx0 = (min(xs) + max(xs)) / 2.0
            cy0 = (min(ys) + max(ys)) / 2.0
            cz0 = (min(zs) + max(zs)) / 2.0
            ball_tmpl.verts = [(vx - cx0, vy - cy0, vz - cz0)
                               for (vx, vy, vz) in ball_tmpl.verts]
            ball_native_d = max(max(xs) - min(xs), max(ys) - min(ys),
                                max(zs) - min(zs)) or 1.0
        except Exception as e:
            logger.warning("Warning balls: could not load %s: %s", WARNING_BALL_FILE, e)
            ball_tmpl = None

    # Crossarm yaw per SHARED OSM node (large/medium only). Several lines meeting at the
    # SAME node share one tower, turned to the AVERAGE of their directions (mean on the
    # doubled angle, because a pylon is symmetric). A node used by a single line keeps its
    # own yaw. A weak resultant (lines fan out / cross) -> 45 deg bisector, so no line is
    # squeezed. Small pylons (minor_line) are never merged.
    _pos_yaws: Dict[Tuple[float, float], List[float]] = {}
    for _line in lines:
        _p = _line.points
        if len(_p) < 2 or _line.pylon_type == "Pylon_Small":
            continue
        _lxy = [(q.x, q.y) for q in _p]
        for _j in range(len(_p)):
            _key = (round(_lxy[_j][0], 1), round(_lxy[_j][1], 1))
            _pos_yaws.setdefault(_key, []).append(_yaw_at(_lxy, _j))
    shared_yaw: Dict[Tuple[float, float], float] = {}
    for _key, _ys in _pos_yaws.items():
        if len(_ys) == 1:
            shared_yaw[_key] = _ys[0]
            continue
        _sx = sum(math.cos(2.0 * a) for a in _ys)
        _sy = sum(math.sin(2.0 * a) for a in _ys)
        if math.hypot(_sx, _sy) < 0.6 * len(_ys):
            shared_yaw[_key] = _ys[0] + math.pi / 4.0
        else:
            shared_yaw[_key] = 0.5 * math.atan2(_sy, _sx)

    def _placed_flags(pts):
        """A node is 'placed' if in-patch or adjacent to an in-patch node."""
        n = len(pts)
        placed = [False] * n
        for j, p in enumerate(pts):
            if p.in_patch:
                placed[j] = True
                if j > 0:
                    placed[j - 1] = True
                if j < n - 1:
                    placed[j + 1] = True
        return placed

    def _span_cables(mesh_, attach_local, sag, spans_xyz, world_a=None, world_b=None):
        """Draw one line's cables. Each conductor attaches to the ARM TIP on the SAME
        side of the span at both ends, so the three never cross even at a turned tower.

        ``world_a``/``world_b`` are the arm tips READ FROM THE SHARED LOG for an end
        whose tower this patch did not build (see the border log). Where they are
        given the cable ends exactly on the tower the neighbour really stamped
        instead of on a recomputed guess; the side pairing is unchanged, only the
        side is read off the stored point's true position across the span."""
        for (xa, ya, za, yawa, xb, yb, zb, yawb) in spans_xyz:
            ca, sa = math.cos(yawa), math.sin(yawa)
            cb, sb = math.cos(yawb), math.sin(yawb)
            _sl = math.hypot(xb - xa, yb - ya) or 1.0
            px, py = -(yb - ya) / _sl, (xb - xa) / _sl
            for i, (ax, ay, az) in enumerate(attach_local):
                wa = world_a[i] if world_a is not None and i < len(world_a) else None
                wb = world_b[i] if world_b is not None and i < len(world_b) else None
                if wa is not None:
                    rax, ray = wa[0] - xa, wa[1] - ya
                    pa = (wa[0], wa[1], wa[2])
                else:
                    rax, ray = _rotz(ax, ay, ca, sa)
                    pa = (rax + xa, ray + ya, az + za)
                side_a = rax * px + ray * py
                if wb is not None:
                    rbx, rby = wb[0] - xb, wb[1] - yb
                    if (rbx * px + rby * py) * side_a < 0.0:
                        rbx, rby = -rbx, -rby
                    pb = (rbx + xb, rby + yb, wb[2])
                else:
                    rbx, rby = _rotz(ax, ay, cb, sb)
                    if (rbx * px + rby * py) * side_a < 0.0:
                        rbx, rby = _rotz(-ax, -ay, cb, sb)
                    pb = (rbx + xb, rby + yb, az + zb)
                _add_cable(mesh_, pa, pb, sag)
                stats.cables += 1

    def _span_cables_mixed(mesh_, attach_a, attach_b, sag, span_xyz,
                           world_a=None, world_b=None):
        """Draw one span whose two ends use DIFFERENT attach points (e.g. a line
        pylon at one end, a substation portal at the other). Conductors are paired
        by side (left-to-left ... right-to-right) so they stay parallel and never
        cross, even though the two crossarms have different widths.

        ``world_a``/``world_b`` are the arm tips read from the shared log for an end
        this patch did not build -- see :func:`_span_cables`."""
        (xa, ya, za, yawa, xb, yb, zb, yawb) = span_xyz
        ca, sa = math.cos(yawa), math.sin(yawa)
        cb, sb = math.cos(yawb), math.sin(yawb)
        _sl = math.hypot(xb - xa, yb - ya) or 1.0
        px, py = -(yb - ya) / _sl, (xb - xa) / _sl      # unit vector across the span

        # Each attach point carries its logged arm tip (or None), so dropping the
        # earth wire below drops the matching stored point with it.
        def _pair(att, world):
            return [(a, (world[i] if world is not None and i < len(world) else None))
                    for i, a in enumerate(att)]

        pair_a = _pair(attach_a, world_a)
        pair_b = _pair(attach_b, world_b)

        # Only the ARM conductors go onto the gantry. The earth wire (the TOPMOST one
        # on the tower) does not belong there -- it runs along the tops from pylon to
        # pylon and ends at the gantry. A Medium has 4 attachments, the gantry 3, so
        # without this the side-pairing would drop one arm and route the earth wire
        # onto the gantry instead.
        def _drop_earth(att, other):
            if len(att) <= len(other):
                return att
            zmax = max(a[0][2] for a in att)
            return [a for a in att if a[0][2] < zmax - 1e-6]

        pair_a = _drop_earth(pair_a, pair_b)
        pair_b = _drop_earth(pair_b, pair_a)

        A, B = [], []
        for ((ax, ay, az), w) in pair_a:
            if w is not None:
                rx, ry, rz = w[0] - xa, w[1] - ya, w[2] - za
            else:
                rx, ry = _rotz(ax, ay, ca, sa)
                rz = az
            A.append((rx * px + ry * py, rx, ry, rz))   # (side, rx, ry, z)
        for ((bx, by, bz), w) in pair_b:
            if w is not None:
                rx, ry, rz = w[0] - xb, w[1] - yb, w[2] - zb
            else:
                rx, ry = _rotz(bx, by, cb, sb)
                rz = bz
            B.append((rx * px + ry * py, rx, ry, rz))
        A.sort(key=lambda t: t[0])
        B.sort(key=lambda t: t[0])
        for (sA, rax, ray, az), (sB, rbx, rby, bz) in zip(A, B):
            _add_cable(mesh_, (rax + xa, ray + ya, az + za),
                       (rbx + xb, rby + yb, bz + zb), sag)
            stats.cables += 1

    # --- Phase 1: decide WHERE the large/medium towers go, without stamping yet. -------
    # One tower per unique OSM node (a node shared by several lines -> one tower). Small
    # pylons are stamped right here, as before -- they are not spaced or merged.
    towers: Dict[Tuple[float, float], List] = {}   # key -> [x, y, yaw, tmpl, tmpl1]
    # Which OSM node each tower stands on, and whether that node is inside the patch.
    # Needed by the shared border log: only a node OUTSIDE the patch may be borrowed
    # from the neighbour, and a record is keyed by the OSM node id.
    node_info: Dict[Tuple[float, float], Tuple[str, bool, str]] = {}  # key -> (node id, in patch, type)
    # Conductor attach points of the line that put each tower there. They belong to
    # the LINE, not to the tower, so they are remembered per tower key (the first
    # line wins -- lines sharing a tower are of the same type). Needed to write the
    # tower's arm tips into the shared border log.
    tower_attach: Dict[Tuple[float, float], List[Tuple[float, float, float]]] = {}
    line_spans: List[Tuple] = []                    # (attach_local, sag, [(keyA,keyB),...])
    # Towers through which the line continues INTO the substation per OSM -> the
    # gantry is wired from them. They are recognised by a neighbouring node of the
    # same way lying inside the outline. Small pylons do not qualify.
    _feed_keys = set()

    for line in lines:
        pts = line.points
        if len(pts) < 2:
            continue
        tmpl = templates.get(line.pylon_type)
        if tmpl is None:
            logger.warning("No template for %s (way %s)", line.pylon_type, line.way_id)
            continue

        n = len(pts)
        placed = _placed_flags(pts)
        if not any(placed):
            continue

        xy = [(p.x, p.y) for p in pts]
        attach_local = CONDUCTOR_ATTACH.get(line.pylon_type, [])
        is_small = (line.pylon_type == "Pylon_Small")
        tmpl1 = templates.get(line.pylon_type + "_Low", tmpl)
        sag = SAG_BY_TYPE.get(line.pylon_type, DEFAULT_SAG)

        # Small pylons: stamp now, on their own nodes, and draw their cables inline.
        if is_small:
            nw: Dict[int, Tuple[float, float, float, float]] = {}
            for j in range(n):
                if not placed[j]:
                    continue
                x, y = xy[j]
                foot_z = _foot_z(terrain, x, y, neighbor)
                yaw = _yaw_at(xy, j)
                # Where two minor lines share a node they each stamp their own small
                # pylon, and the two ended up in exactly the same place at different
                # angles -- so the cables of one ran straight through the arms of the
                # other. Every pylon after the first on a spot is lifted by SMALL_STACK_LIFT
                # so they sit above each other instead of inside each other.
                _spot = (round(x, 1), round(y, 1))
                _lift = SMALL_STACK_LIFT * _small_at.get(_spot, 0)
                _small_at[_spot] = _small_at.get(_spot, 0) + 1
                foot_z += _lift
                nw[j] = (x, y, foot_z, yaw)
                _place_pylon(mesh, tmpl, x, y, foot_z, yaw)
                stats.towers += 1
            if nw:
                stats.lines_with_geometry += 1
            if draw_cables and attach_local:
                spans_xyz = [(*nw[j], *nw[j + 1]) for j in range(n - 1)
                             if j in nw and (j + 1) in nw]
                _span_cables(mesh, attach_local, sag, spans_xyz)
            continue

        # Large/medium: no tower inside a substation yard (only small may stand there).
        if substation_polygons:
            # Which node of this way lies INSIDE the yard. The tower standing outside
            # but whose neighbour on the same way is inside is the one THROUGH which
            # the line continues into the substation -> the gantry is wired from it.
            _in = [_point_in_polys(xy[j][0], xy[j][1], substation_polygons)
                   for j in range(n)]
            if not is_small:
                for j in range(n):
                    if _in[j]:
                        continue
                    if (j > 0 and _in[j - 1]) or (j < n - 1 and _in[j + 1]):
                        _feed_keys.add((round(xy[j][0], 1), round(xy[j][1], 1)))
            for j in range(n):
                if placed[j] and _in[j]:
                    placed[j] = False
            # The line ends AT the fence: drop any tower left with no neighbour to string
            # a cable to (an unconnected pylon).
            for _ in range(n):
                changed = False
                for j in range(n):
                    if not placed[j]:
                        continue
                    if not ((j > 0 and placed[j - 1]) or (j < n - 1 and placed[j + 1])):
                        placed[j] = False
                        changed = True
                if not changed:
                    break
            if not any(placed):
                continue

        node_key: Dict[int, Tuple[float, float]] = {}
        for j in range(n):
            if not placed[j]:
                continue
            x, y = xy[j]
            key = (round(x, 1), round(y, 1))
            if key not in towers:
                towers[key] = [x, y, shared_yaw.get(key, _yaw_at(xy, j)), tmpl, tmpl1]
            node_info.setdefault(key, (pts[j].node_id, pts[j].in_patch, line.pylon_type))
            tower_attach.setdefault(key, attach_local)
            node_key[j] = key
        if node_key:
            stats.lines_with_geometry += 1
        spans = [(node_key[j], node_key[j + 1]) for j in range(n - 1)
                 if j in node_key and (j + 1) in node_key
                 and node_key[j] != node_key[j + 1]]
        line_spans.append([attach_local, sag, spans, line])

    # --- Phase 1.2: borrow the towers the neighbouring patch already built. -------------
    # A tower near the seam belongs to both patches, no matter on which side of the
    # border line it stands. When the OTHER patch got there first, its position, foot
    # height and yaw are taken from the shared log and the geometry is NOT stamped
    # again -- this patch only strings its cables onto it. Whether the node lies inside
    # the patch or beyond it plays no part here; only who noted it down counts.
    # Keys stay in ``towers`` so the spans still find their endpoints.
    borrowed_z: Dict[Tuple[float, float], float] = {}   # key -> foot z read from the log
    # key -> the tower's arm tips as the neighbour really built them, so the cables
    # end ON them. Empty for a record written before the log carried them.
    borrowed_attach: Dict[Tuple[float, float], List[Tuple[float, float, float]]] = {}
    if border_log is not None and patch_id is not None:
        for k, t in towers.items():
            info = node_info.get(k)
            if info is None:
                continue                      # a gantry, not a line node
            entry = border_log.lookup(info[0], *border_log.to_utm(t[0], t[1]))
            if entry is None or entry["patch"] == str(patch_id):
                continue                      # nothing noted, or our own note
            t[0] = entry["x"]
            t[1] = entry["y"]
            t[2] = entry["yaw"]
            borrowed_z[k] = entry["z"]
            att = entry.get("attach")
            if att:
                borrowed_attach[k] = att
        if borrowed_z:
            logger.info("Powerlines: %d border tower(s) reused from neighbouring patches",
                        len(borrowed_z))

    def _tower_z(key, x, y):
        """Foot height of a tower: the value noted by the neighbouring patch for a
        borrowed border tower (so both patches agree to the millimetre), otherwise
        measured on the terrain exactly as before."""
        if key in borrowed_z:
            return borrowed_z[key]
        return _foot_z(terrain, x, y, neighbor)

    # --- Phase 1.5: Identify terminal pylons (portals will be added at the end) --------
    _terminal_pylons = set()
    _gantry_keys = set()   # portal keys -> their cables use the portal's own attach points
    _edges = []
    for _ring in (substation_polygons or []):
        _m = len(_ring)
        for _i in range(_m):
            _ax, _ay = _ring[_i]
            _bx, _by = _ring[(_i + 1) % _m]
            if math.hypot(_bx - _ax, _by - _ay) > 1e-6:
                _edges.append((_ax, _ay, _bx, _by))

    if substation_polygons and _edges:
        _neighbours_temp = {}
        for (_a, _s, _sp, _l) in line_spans:
            for (ka, kb) in _sp:
                _neighbours_temp.setdefault(ka, []).append(kb)
                _neighbours_temp.setdefault(kb, []).append(ka)
        for k in towers:
            if len(_neighbours_temp.get(k, [])) == 1:
                _terminal_pylons.add(k)
    # --- Phase 2: push towers apart so none are closer than MIN_TOWER_DIST. -------------
    _neighbours: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
    for (_a, _s, _sp, _l) in line_spans:
        for (ka, kb) in _sp:
            _neighbours.setdefault(ka, []).append(kb)
            _neighbours.setdefault(kb, []).append(ka)

    _sub_centres = []
    for _ring in (substation_polygons or []):
        _rxs = [p[0] for p in _ring]
        _rys = [p[1] for p in _ring]
        _sub_centres.append((sum(_rxs) / len(_rxs), sum(_rys) / len(_rys)))

    def _near_substation(x, y):
        return any(math.hypot(x - cx, y - cy) < SUBSTATION_ZONE_RADIUS
                   for (cx, cy) in _sub_centres)

    if _sub_centres:
        _keys = list(towers)
        # Grid cell = the LARGEST keep-out diameter in play, so a neighbour search over the
        # 3x3 cells around a tower can never miss a pair that is still too close.
        _cell = max([MIN_TOWER_DIST] + list(TOWER_CLEARANCE.values()))
        _all_spans = [(ka, kb) for (_a, _s, sp, _l) in line_spans for (ka, kb) in sp]
        _radius: Dict[Tuple[float, float], float] = {}
        for k in _keys:
            _tm = towers[k][3]
            if _tm.verts:
                _rx = [v[0] for v in _tm.verts]
                _ry = [v[1] for v in _tm.verts]
                _radius[k] = max(max(_rx) - min(_rx), max(_ry) - min(_ry)) / 2.0
            else:
                _radius[k] = 0.0

        # Both jobs run TOGETHER in one loop until settled: (a) no two towers closer than
        # 30 m, (b) no cable running through a tower. Doing them one after the other let
        # (b) shove towers back under 30 m -- that was the 25.5 m gap. Now each pass fixes
        # what the other disturbed, until nothing moves.
        for _ in range(300):
            _moved = False

            # (a) push apart any pair closer than 30 m (zone towers only).
            _grid: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
            for k in _keys:
                t = towers[k]
                _grid.setdefault((int(t[0] // _cell), int(t[1] // _cell)), []).append(k)
            for k in _keys:
                t = towers[k]
                gx, gy = int(t[0] // _cell), int(t[1] // _cell)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for k2 in _grid.get((gx + dx, gy + dy), ()):
                            if k2 <= k:
                                continue
                            t2 = towers[k2]
                            ddx, ddy = t2[0] - t[0], t2[1] - t[1]
                            d = math.hypot(ddx, ddy)
                            # Minimum distance of THIS pair, from the two tower types.
                            need = _min_tower_dist(t[3], t2[3])
                            if d >= need:
                                continue
                            a_in = _near_substation(t[0], t[1])
                            b_in = _near_substation(t2[0], t2[1])
                            # A borrowed border tower is nailed down by the log --
                            # the neighbouring patch already stands there.
                            if k in borrowed_z:
                                a_in = False
                            if k2 in borrowed_z:
                                b_in = False
                            if not a_in and not b_in:
                                continue
                            if d <= 1e-9:
                                ux, uy, d = 1.0, 0.0, 1e-9
                            else:
                                ux, uy = ddx / d, ddy / d
                            gap = need - d
                            if a_in and b_in:
                                p = gap / 2.0 + 0.05
                                t[0] -= ux * p
                                t[1] -= uy * p
                                t2[0] += ux * p
                                t2[1] += uy * p
                            elif a_in:
                                t[0] -= ux * (gap + 0.05)
                                t[1] -= uy * (gap + 0.05)
                            else:
                                t2[0] += ux * (gap + 0.05)
                                t2[1] += uy * (gap + 0.05)
                            _moved = True

            # (b) push a zone tower sideways off any cable of another span running through it.
            for k in _keys:
                t = towers[k]
                if not _near_substation(t[0], t[1]):
                    continue
                if k in _terminal_pylons or k in borrowed_z:
                    continue
                for (ka, kb) in _all_spans:
                    if k == ka or k == kb:
                        continue
                    ax, ay = towers[ka][0], towers[ka][1]
                    bx, by = towers[kb][0], towers[kb][1]
                    vx, vy = bx - ax, by - ay
                    L2 = vx * vx + vy * vy
                    if L2 < 1e-9:
                        continue
                    s = ((t[0] - ax) * vx + (t[1] - ay) * vy) / L2
                    if not (0.0 < s < 1.0):
                        continue
                    cxp, cyp = ax + s * vx, ay + s * vy
                    ox, oy = t[0] - cxp, t[1] - cyp
                    d = math.hypot(ox, oy)
                    # Distance to keep from THIS span: the wider of the two pairings with
                    # its end towers, so the cable clears the tower's keep-out circle.
                    keep = max(_min_tower_dist(t[3], towers[ka][3]),
                               _min_tower_dist(t[3], towers[kb][3]))
                    if d >= keep:
                        continue
                    if d < 1e-9:
                        nlen = math.sqrt(L2)
                        ox, oy, d = -vy / nlen, vx / nlen, 1.0
                    t[0] = cxp + ox / d * keep
                    t[1] = cyp + oy / d * keep
                    _moved = True

            if not _moved:
                break

    # --- Phase 2b: push towers apart EVERYWHERE, not just near a substation. ------------
    # Separate from Phase 2 above (which is substation-only and stays untouched) -- this
    # is the general case: two towers anywhere in the patch must not stand closer than
    # their combined TOWER_CLEARANCE radii, and no tower may stand closer than its clearance
    # to any span/cable of another line (corridor clearance between parallel lines).
    # Borrowed border towers (in borrowed_z) are nailed down by the shared log and never
    # move; if only one side of a too-close pair is borrowed, the whole correction falls
    # on the other one.
    _keys_all = list(towers)
    _cell_all = max([MIN_TOWER_DIST] + list(TOWER_CLEARANCE.values()))

    _all_spans_all = [(ka, kb) for (_a, _s, sp, _l) in line_spans for (ka, kb) in sp]
    _span_to_line: Dict[Tuple[Tuple[float, float], Tuple[float, float]], int] = {}
    _tower_to_lines: Dict[Tuple[float, float], set] = {}
    for _line_idx, (_a, _s, _sp, _l) in enumerate(line_spans):
        for (_ka, _kb) in _sp:
            _span_to_line[(_ka, _kb)] = _line_idx
            _tower_to_lines.setdefault(_ka, set()).add(_line_idx)
            _tower_to_lines.setdefault(_kb, set()).add(_line_idx)

    for _ in range(300):
        _moved = False
        # (a) Tower-to-tower push
        _grid_all: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
        for k in _keys_all:
            t = towers[k]
            _grid_all.setdefault((int(t[0] // _cell_all), int(t[1] // _cell_all)), []).append(k)
        for k in _keys_all:
            t = towers[k]
            gx, gy = int(t[0] // _cell_all), int(t[1] // _cell_all)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for k2 in _grid_all.get((gx + dx, gy + dy), ()):
                        if k2 <= k:
                            continue
                        t2 = towers[k2]
                        ddx, ddy = t2[0] - t[0], t2[1] - t[1]
                        d = math.hypot(ddx, ddy)
                        need = _min_tower_dist(t[3], t2[3])
                        if d >= need:
                            continue
                        a_free = k not in borrowed_z
                        b_free = k2 not in borrowed_z
                        if not a_free and not b_free:
                            continue
                        if d <= 1e-9:
                            ux, uy, d = 1.0, 0.0, 1e-9
                        else:
                            ux, uy = ddx / d, ddy / d
                        gap = need - d
                        if a_free and b_free:
                            p = gap / 2.0 + 0.05
                            t[0] -= ux * p
                            t[1] -= uy * p
                            t2[0] += ux * p
                            t2[1] += uy * p
                        elif a_free:
                            t[0] -= ux * (gap + 0.05)
                            t[1] -= uy * (gap + 0.05)
                        else:
                            t2[0] += ux * (gap + 0.05)
                            t2[1] += uy * (gap + 0.05)
                        _moved = True

        # (b) Tower-to-span push: keep tower away from cables/spans of other lines
        for k in _keys_all:
            t = towers[k]
            a_free = k not in borrowed_z
            k_lines = _tower_to_lines.get(k, set())

            for (ka, kb) in _all_spans_all:
                if k == ka or k == kb:
                    continue
                span_line = _span_to_line.get((ka, kb))
                if span_line is not None and span_line in k_lines:
                    continue

                ax, ay = towers[ka][0], towers[ka][1]
                bx, by = towers[kb][0], towers[kb][1]
                vx, vy = bx - ax, by - ay
                L2 = vx * vx + vy * vy
                if L2 < 1e-9:
                    continue
                s = ((t[0] - ax) * vx + (t[1] - ay) * vy) / L2
                if not (0.0 < s < 1.0):
                    continue
                cxp, cyp = ax + s * vx, ay + s * vy
                ox, oy = t[0] - cxp, t[1] - cyp
                d = math.hypot(ox, oy)
                keep = max(_min_tower_dist(t[3], towers[ka][3]),
                           _min_tower_dist(t[3], towers[kb][3]))
                if d >= keep:
                    continue

                ka_free = ka not in borrowed_z
                kb_free = kb not in borrowed_z
                span_free = ka_free or kb_free
                if not a_free and not span_free:
                    continue

                if d <= 1e-9:
                    nlen = math.sqrt(L2)
                    ux, uy = -vy / nlen, vx / nlen
                else:
                    ux, uy = ox / d, oy / d
                gap = keep - d

                if a_free and (ka_free and kb_free):
                    p = gap / 2.0 + 0.05
                    t[0] += ux * p
                    t[1] += uy * p
                    factor = (1.0 - s) ** 2 + s ** 2
                    w = p / max(factor, 0.5)
                    towers[ka][0] -= ux * (1.0 - s) * w
                    towers[ka][1] -= uy * (1.0 - s) * w
                    towers[kb][0] -= ux * s * w
                    towers[kb][1] -= uy * s * w
                elif a_free and span_free:
                    p = gap / 2.0 + 0.05
                    t[0] += ux * p
                    t[1] += uy * p
                    if ka_free and s < 0.9:
                        w = p / (1.0 - s)
                        towers[ka][0] -= ux * w
                        towers[ka][1] -= uy * w
                    elif kb_free and s > 0.1:
                        w = p / s
                        towers[kb][0] -= ux * w
                        towers[kb][1] -= uy * w
                    else:
                        t[0] += ux * p
                        t[1] += uy * p
                elif a_free:
                    p = gap + 0.05
                    t[0] += ux * p
                    t[1] += uy * p
                elif ka_free and kb_free:
                    p = gap + 0.05
                    factor = (1.0 - s) ** 2 + s ** 2
                    w = p / max(factor, 0.5)
                    towers[ka][0] -= ux * (1.0 - s) * w
                    towers[ka][1] -= uy * (1.0 - s) * w
                    towers[kb][0] -= ux * s * w
                    towers[kb][1] -= uy * s * w
                elif ka_free and s < 0.9:
                    w = (gap + 0.05) / (1.0 - s)
                    towers[ka][0] -= ux * w
                    towers[ka][1] -= uy * w
                elif kb_free and s > 0.1:
                    w = (gap + 0.05) / s
                    towers[kb][0] -= ux * w
                    towers[kb][1] -= uy * w
                _moved = True

        if not _moved:
            break

    # --- Entry pylons: turn the last towers before the fence TOWARD the outline. --------
    # The large/medium pylon just outside a substation, from which the line runs into the
    # yard, is turned toward the fence: its crossarm is blended halfway between the line's
    # own direction and the nearest fence edge. A line meeting the fence square stays
    # square; one arriving at an angle ends up ~45 deg to the fence.
    _edges = []
    for _ring in (substation_polygons or []):
        _m = len(_ring)
        for _i in range(_m):
            _ax, _ay = _ring[_i]
            _bx, _by = _ring[(_i + 1) % _m]
            if math.hypot(_bx - _ax, _by - _ay) > 1e-6:
                _edges.append((_ax, _ay, _bx, _by))

    def _fence_dist(x, y):
        best = 1e18
        for (ax, ay, bx, by) in _edges:
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            if L2 < 1e-9:
                continue
            s = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / L2))
            best = min(best, math.hypot(x - (ax + s * vx), y - (ay + s * vy)))
        return best

    if _edges:
        # Which towers each tower is cabled to (to read the incoming line direction).
        _neighbours: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
        for (_a, _s, _sp, _l) in line_spans:
            for (ka, kb) in _sp:
                _neighbours.setdefault(ka, []).append(kb)
                _neighbours.setdefault(kb, []).append(ka)

        for k, t in towers.items():
            if "substation" in t[3].name.lower():
                continue
            if k not in _terminal_pylons and k not in _feed_keys:
                continue
            if abs(t[0]) > patch_half or abs(t[1]) > patch_half:
                continue
            if k in borrowed_z:
                continue          # borrowed from the log -- its yaw is the neighbour's
            nbrs = _neighbours.get(k)
            if not nbrs:
                continue
            if any(nbr in towers and (abs(towers[nbr][0]) > patch_half or abs(towers[nbr][1]) > patch_half) for nbr in nbrs):
                continue
            # Incoming line = toward the neighbour FURTHEST from the fence (the run out
            # into the country). The crossarm is built PERPENDICULAR to it, so the three
            # conductors always spread across -- never collapse into one line.
            nb = max(nbrs, key=lambda kk: _fence_dist(towers[kk][0], towers[kk][1]))
            cdx, cdy = towers[nb][0] - t[0], towers[nb][1] - t[1]
            base = math.atan2(cdy, cdx) + math.pi / 2.0
            # ...but NOT backwards. Pylon_Medium is not symmetric -- its arms sit left and
            # right at different heights -- so turning it by 180 deg puts every conductor
            # on the wrong side and the cables no longer meet the next tower's arms. The
            # direction above points from the tower TOWARD its neighbour, which is the
            # opposite of the way's own direction, so keep whichever of base / base+180
            # is the nearer to the yaw the line itself asked for.
            _keep = t[2]
            if abs(math.atan2(math.sin(base - _keep), math.cos(base - _keep))) > math.pi / 2.0:
                base += math.pi

            # Nearest fence edge direction, to lean the crossarm toward the outline.
            best_dir, bd = None, SUBSTATION_ZONE_RADIUS
            for (ax, ay, bx, by) in _edges:
                vx, vy = bx - ax, by - ay
                L2 = vx * vx + vy * vy
                if L2 < 1e-9:
                    continue
                s = max(0.0, min(1.0, ((t[0] - ax) * vx + (t[1] - ay) * vy) / L2))
                d = math.hypot(t[0] - (ax + s * vx), t[1] - (ay + s * vy))
                if d < bd:
                    bd, best_dir = d, math.atan2(vy, vx)
            if best_dir is None:
                t[2] = base
                continue
            # Lean the crossarm from perpendicular-to-line toward the fence, but no more
            # than 45 deg -- so a square approach faces the fence and an angled one ends
            # up ~45 deg, and the conductors never line up.
            diff = best_dir - base
            while diff > math.pi / 2.0:
                diff -= math.pi
            while diff <= -math.pi / 2.0:
                diff += math.pi
            rot = max(-math.pi / 4.0, min(math.pi / 4.0, diff))
            # The fence is a LINE, so leaning by +rot and by -rot faces it equally well --
            # but the two are not equal for the cables. With more than one span meeting
            # here, one of them lays the crossarm along a span (the conductors collapse
            # into a single line) while the other stays across all of them. Take whichever
            # keeps the LARGEST angle to the nearest span.
            # ALL the cables that will leave this tower. Besides the spans to its
            # neighbours that includes the one into the yard: the gantry is not built yet
            # at this point, but it is always placed on the perpendicular from the tower
            # to the nearest wall of the outline, so that perpendicular IS the direction
            # of that cable. Without it the check would judge the crossarm against half
            # the cables and let it end up in line with the other half.
            _cable_dirs = []
            for nbr in nbrs:
                if nbr not in towers:
                    continue
                sdx = towers[nbr][0] - t[0]
                sdy = towers[nbr][1] - t[1]
                if abs(sdx) > 1e-9 or abs(sdy) > 1e-9:
                    _cable_dirs.append(math.atan2(sdy, sdx))
            _qx = _qy = None
            _qd = 1e18
            for (ax, ay, bx, by) in _edges:
                vx, vy = bx - ax, by - ay
                L2 = vx * vx + vy * vy
                if L2 < 1e-9:
                    continue
                s = max(0.0, min(1.0, ((t[0] - ax) * vx + (t[1] - ay) * vy) / L2))
                px_, py_ = ax + s * vx, ay + s * vy
                d = math.hypot(t[0] - px_, t[1] - py_)
                if d < _qd:
                    _qd, _qx, _qy = d, px_, py_
            if _qx is not None and _qd > 1e-9:
                _cable_dirs.append(math.atan2(_qy - t[1], _qx - t[0]))

            def _worst_angle(yaw):
                worst = math.pi
                for sd in _cable_dirs:
                    a = yaw - sd
                    # a crossarm and a cable are both undirected -> fold into [0, 90 deg]
                    a = abs(math.atan2(math.sin(2.0 * a), math.cos(2.0 * a))) / 2.0
                    worst = min(worst, a)
                return worst

            if _worst_angle(base - rot) > _worst_angle(base + rot):
                rot = -rot
            # Never leave the crossarm running along a cable. base is square to the line;
            # a lean that ends up closer to a cable than that would make the conductors
            # parallel with it, so it is dropped.
            if _worst_angle(base + rot) < _worst_angle(base):
                rot = 0.0
            t[2] = base + rot

        # Line the entry pylons up 30 m apart ALONG the fence they face, so the last
        # towers before the substation stand in a neat row, 30 m centre to centre.
        _groups: Dict[int, List[Tuple[float, float]]] = {}
        for k, t in towers.items():
            if "substation" in t[3].name.lower():
                continue
            if k not in _terminal_pylons and k not in _feed_keys:
                continue
            if abs(t[0]) > patch_half or abs(t[1]) > patch_half:
                continue
            if k in borrowed_z:
                continue          # borrowed from the log -- it must not be moved
            bi, bd = None, SUBSTATION_ZONE_RADIUS
            for ei, (ax, ay, bx, by) in enumerate(_edges):
                vx, vy = bx - ax, by - ay
                L2 = vx * vx + vy * vy
                if L2 < 1e-9:
                    continue
                s = max(0.0, min(1.0, ((t[0] - ax) * vx + (t[1] - ay) * vy) / L2))
                d = math.hypot(t[0] - (ax + s * vx), t[1] - (ay + s * vy))
                if d < bd:
                    bd, bi = d, ei
            if bi is not None:
                _groups.setdefault(bi, []).append(k)
        for ei, ks in _groups.items():
            ax, ay, bx, by = _edges[ei]
            el = math.hypot(bx - ax, by - ay) or 1.0
            ux, uy = (bx - ax) / el, (by - ay) / el      # unit vector ALONG the fence
            ks.sort(key=lambda kk: towers[kk][0] * ux + towers[kk][1] * uy)
            for _ in range(60):
                moved = False
                for i in range(len(ks) - 1):
                    t1, t2 = towers[ks[i]], towers[ks[i + 1]]
                    a1 = t1[0] * ux + t1[1] * uy
                    a2 = t2[0] * ux + t2[1] * uy
                    if a2 - a1 < MIN_TOWER_DIST:
                        push = (MIN_TOWER_DIST - (a2 - a1)) / 2.0 + 0.05
                        t1[0] -= ux * push
                        t1[1] -= uy * push
                        t2[0] += ux * push
                        t2[1] += uy * push
                        moved = True
                if not moved:
                    break

    # --- FINAL pass: within 1000 m of a substation, push any tower off a cable of another
    # line that runs through it. Done LAST so nothing shoves it back onto the cable. -------
    if _sub_centres:
        _keysF = list(towers)
        _spansF = [(ka, kb) for (_a, _s, sp, _l) in line_spans for (ka, kb) in sp]
        _radF: Dict[Tuple[float, float], float] = {}
        for k in _keysF:
            _tm = towers[k][3]
            if _tm.verts:
                _rx = [v[0] for v in _tm.verts]
                _ry = [v[1] for v in _tm.verts]
                _radF[k] = max(max(_rx) - min(_rx), max(_ry) - min(_ry)) / 2.0
            else:
                _radF[k] = 0.0
        for _ in range(60):
            _moved = False
            for k in _keysF:
                t = towers[k]
                if not _near_substation(t[0], t[1]):
                    continue
                if k in _terminal_pylons or k in borrowed_z:
                    continue
                keep = MIN_TOWER_DIST
                for (ka, kb) in _spansF:
                    if k == ka or k == kb:
                        continue
                    ax, ay = towers[ka][0], towers[ka][1]
                    bx, by = towers[kb][0], towers[kb][1]
                    vx, vy = bx - ax, by - ay
                    L2 = vx * vx + vy * vy
                    if L2 < 1e-9:
                        continue
                    s = ((t[0] - ax) * vx + (t[1] - ay) * vy) / L2
                    if not (0.0 < s < 1.0):
                        continue
                    cxp, cyp = ax + s * vx, ay + s * vy
                    ox, oy = t[0] - cxp, t[1] - cyp
                    d = math.hypot(ox, oy)
                    if d >= keep:
                        continue
                    if d < 1e-9:
                        nlen = math.sqrt(L2)
                        ox, oy, d = -vy / nlen, vx / nlen, 1.0
                    t[0] = cxp + ox / d * keep
                    t[1] = cyp + oy / d * keep
                    _moved = True
            if not _moved:
                break

    # --- Phase 2.5: Add Pylon_Substation gantries inside the substation yard ------------
    if substation_polygons and _edges:
        _neighbours_temp = {}
        for (_a, _s, _sp, _l) in line_spans:
            for (ka, kb) in _sp:
                _neighbours_temp.setdefault(ka, []).append(kb)
                _neighbours_temp.setdefault(kb, []).append(ka)

        gantries_to_add = []
        for line_idx, (attach_local, sag, spans, line) in enumerate(line_spans):
            for k in towers:
                # A gantry goes to a tower where the line ends (wire from one side),
                # OR to a tower through which the line continues into the substation per OSM.
                if k in _terminal_pylons or k in _feed_keys:
                    is_in_line = any(k == ka or k == kb for (ka, kb) in spans)
                    if not is_in_line:
                        continue
                    if any(g[3] == k for g in gantries_to_add):
                        continue                      # this pylon already got a gantry
                    tx, ty = towers[k][0], towers[k][1]
                    if abs(tx) > patch_half or abs(ty) > patch_half:
                        continue
                    if k in borrowed_z:
                        continue      # borrowed from the log -- the gantry would shift
                                      # and re-aim a tower the neighbour has built
                    if not _near_substation(tx, ty):
                        continue
                    
                    _nb = _neighbours_temp.get(k, [])
                    _nbk = max(_nb, key=lambda kk: _fence_dist(towers[kk][0], towers[kk][1])) if _nb else k
                    ddx, ddy = tx - towers[_nbk][0], ty - towers[_nbk][1]
                    _d1 = math.hypot(ddx, ddy) or 1.0
                    ddx, ddy = ddx / _d1, ddy / _d1

                    # 1. Find the best wall based on the wire direction and the perpendicular
                    best_d = 1e18
                    best_d_comp = 1e18
                    hit_edge = None
                    cxp, cyp = tx, ty
                    best_nx, best_ny = 0.0, -1.0
                    # Fallback in case the perpendicular hits no wall of the whole outline.
                    fb_d = 1e18
                    fb_d_comp = 1e18
                    fb_edge = None
                    fb_nx, fb_ny = 0.0, -1.0

                    for ax, ay, bx, by in _edges:
                        vx, vy = bx - ax, by - ay
                        L2 = vx * vx + vy * vy
                        if L2 < 1e-9: continue
                        
                        # Inner normal
                        _en = math.hypot(vx, vy) or 1.0
                        n1x, n1y = -vy / _en, vx / _en
                        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
                        if _point_in_polys(mx + 1.0 * n1x, my + 1.0 * n1y, substation_polygons):
                            nx, ny = n1x, n1y
                        else:
                            nx, ny = -n1x, -n1y
                            
                        # The pylon must stand OUTSIDE this wall (otherwise we would reach
                        # the opposite wall). The wall is NOT discarded by the wire
                        # direction -- only the perpendicular decides.
                        if (tx - ax) * nx + (ty - ay) * ny >= 0.0:
                            continue

                        # The perpendicular from the pylon centre is extended as far as
                        # needed and searched along the WHOLE outline. A wall the
                        # perpendicular really lands on (s in <0,1>) always wins, even if
                        # it is further away than the nearest corner.
                        s = ((tx - ax) * vx + (ty - ay) * vy) / L2
                        px, py = ax + s * vx, ay + s * vy
                        d = math.hypot(tx - px, ty - py)
                        if -0.01 <= s <= 1.01:
                            d_comp = d - 1e-5 if abs(vy) < abs(vx) else d
                            if d_comp < best_d_comp:
                                best_d_comp = d_comp
                                best_d = d
                                hit_edge = (ax, ay, bx, by)
                                best_nx, best_ny = nx, ny
                        else:
                            # perpendicular misses this wall -> fallback only (nearest fence point)
                            s_c = max(0.0, min(1.0, s))
                            d_c = math.hypot(tx - (ax + s_c * vx), ty - (ay + s_c * vy))
                            d_c_comp = d_c - 1e-5 if abs(vy) < abs(vx) else d_c
                            if d_c_comp < fb_d_comp:
                                fb_d_comp = d_c_comp
                                fb_d = d_c
                                fb_edge = (ax, ay, bx, by)
                                fb_nx, fb_ny = nx, ny

                    if hit_edge is None and fb_edge is not None:
                        # The perpendicular hit no wall of the whole outline -> nearest fence point.
                        hit_edge = fb_edge
                        best_nx, best_ny = fb_nx, fb_ny

                    if hit_edge is not None:
                        ax, ay, bx, by = hit_edge
                        vx, vy = bx - ax, by - ay
                        L2 = vx * vx + vy * vy

                        # Projection without clamping for a perfect perpendicular
                        s = ((tx - ax) * vx + (ty - ay) * vy) / L2
                        px, py = ax + s * vx, ay + s * vy
                        
                        # Gantry clearance from the fence (always exactly 16.0m along the perpendicular)
                        offset = 16.0
                        actual_offset = 16.0
                        gx = px + offset * best_nx
                        gy = py + offset * best_ny
                        s_actual = s
                        
                        # Check that the gantry does not end up outside (e.g. due to a large s)
                        if not _point_in_polys(gx, gy, substation_polygons):
                            # Fallback na clamped s a dynamic offset
                            s_c = max(0.0, min(1.0, s))
                            px_c, py_c = ax + s_c * vx, ay + s_c * vy
                            gx_c = px_c + offset * best_nx
                            gy_c = py_c + offset * best_ny
                            if _point_in_polys(gx_c, gy_c, substation_polygons):
                                gx, gy = gx_c, gy_c
                                s_actual = s_c
                            else:
                                # Absolute fallback to a 16.0m offset
                                gx, gy = px_c + 16.0 * best_nx, py_c + 16.0 * best_ny
                                s_actual = s_c
                                actual_offset = 16.0
                            
                        yaw = math.atan2(-best_ny, -best_nx) - math.pi / 2.0
                        
                        d_pylon_wall = math.hypot(tx - px, ty - py)
                        total_dist = d_pylon_wall + actual_offset
                        # Stored as a list so the coordinates and s_actual can be modified later (including unclamped s, offset and total distance)
                        gantries_to_add.append([gx, gy, yaw, k, line_idx, hit_edge, (best_nx, best_ny), s_actual, s, actual_offset, total_dist])
                        
        # Push-apart loop along the fence wall to prevent overlap (collisions), only on a real intersection (SAT)
        if gantries_to_add:
            # Compute the radii for the existing pylons
            tower_radii = {}
            for tk, t in towers.items():
                tmpl = t[3]
                if tmpl and tmpl.verts:
                    rx = [v[0] for v in tmpl.verts]
                    ry = [v[1] for v in tmpl.verts]
                    tower_radii[tk] = max(max(rx) - min(rx), max(ry) - min(ry)) / 2.0
                else:
                    tower_radii[tk] = 0.0

            # Build a map of the edges and their start distances along the polygon perimeter
            edge_start_dist = {}
            edge_is_forward = {}
            if substation_polygons:
                poly = substation_polygons[0]
                # Check the orientation (signed area)
                area = 0.0
                n_verts = len(poly)
                for idx in range(n_verts):
                    v1 = poly[idx]
                    v2 = poly[(idx + 1) % n_verts]
                    area += (v1[0] * v2[1] - v2[0] * v1[1])
                if area < 0.0:  # It is CW, flip it to CCW
                    poly = list(reversed(poly))
                
                cum_dist = 0.0
                for idx in range(n_verts):
                    v1 = poly[idx]
                    v2 = poly[(idx + 1) % n_verts]
                    k_fw = (v1[0], v1[1], v2[0], v2[1])
                    k_rv = (v2[0], v2[1], v1[0], v1[1])
                    edge_start_dist[k_fw] = cum_dist
                    edge_start_dist[k_rv] = cum_dist
                    edge_is_forward[k_fw] = True
                    edge_is_forward[k_rv] = False
                    cum_dist += math.hypot(v2[0] - v1[0], v2[1] - v1[1])

            def _get_ccw_dist(g_entry, s_val, L_val):
                edge = g_entry[5]
                base_dist = edge_start_dist.get(edge, 0.0)
                if edge_is_forward.get(edge, True):
                    return base_dist + s_val * L_val
                else:
                    return base_dist + (1.0 - s_val) * L_val

            # Movement step in a single iteration
            step = 0.2
            
            for _ in range(100):
                moved = False
                
                # 1. Push the gantries apart from each other (slide along the fence on intersection)
                for i in range(len(gantries_to_add)):
                    for j in range(i + 1, len(gantries_to_add)):
                        g1 = gantries_to_add[i]
                        g2 = gantries_to_add[j]
                        
                        # Get the OBB corners of both gantries
                        rect1 = _gantry_corners(g1[0], g1[1], g1[2])
                        rect2 = _gantry_corners(g2[0], g2[1], g2[2])
                        
                        if _rects_intersect(rect1, rect2):
                            dx = g2[0] - g1[0]
                            dy = g2[1] - g1[1]
                            d = math.hypot(dx, dy)
                            
                            # For g1: projection of the movement onto the direction of its fence wall
                            ax1, ay1, bx1, by1 = g1[5]
                            L1 = math.hypot(bx1 - ax1, by1 - ay1) or 1.0
                            ux1, uy1 = (bx1 - ax1) / L1, (by1 - ay1) / L1
                            
                            # For g2: projection of the movement onto the direction of its fence wall
                            ax2, ay2, bx2, by2 = g2[5]
                            L2 = math.hypot(bx2 - ax2, by2 - ay2) or 1.0
                            ux2, uy2 = (bx2 - ax2) / L2, (by2 - ay2) / L2
                            
                            # Compute the positions along the fence perimeter
                            d_p1 = _get_ccw_dist(g1, g1[8], L1)
                            d_p2 = _get_ccw_dist(g2, g2[8], L2)
                            
                            gap_meters = 12.2
                            dist1 = edge_start_dist.get(g1[5], 0.0)
                            dist2 = edge_start_dist.get(g2[5], 0.0)
                            fw1 = edge_is_forward.get(g1[5], True)
                            fw2 = edge_is_forward.get(g2[5], True)
                            
                            # Limits for s1 and s2 so the order along the fence perimeter is not swapped (even across corners)
                            s1_max_limit = 1.0
                            s1_min_limit = 0.0
                            s2_max_limit = 1.0
                            s2_min_limit = 0.0
                            
                            if d_p1 < d_p2:
                                # g1 (smaller d) must be <= g2 (larger d) - gap
                                limit_d1 = _get_ccw_dist(g2, g2[7], L2) - gap_meters
                                if fw1:
                                    s1_max_limit = max(0.0, min(1.0, (limit_d1 - dist1) / L1))
                                else:
                                    s1_min_limit = max(0.0, min(1.0, 1.0 - (limit_d1 - dist1) / L1))
                                    
                                # g2 (larger d) must be >= g1 (smaller d) + gap
                                limit_d2 = _get_ccw_dist(g1, g1[7], L1) + gap_meters
                                if fw2:
                                    s2_min_limit = max(0.0, min(1.0, (limit_d2 - dist2) / L2))
                                else:
                                    s2_max_limit = max(0.0, min(1.0, 1.0 - (limit_d2 - dist2) / L2))
                            else:
                                # g1 (larger d) must be >= g2 (smaller d) + gap
                                limit_d1 = _get_ccw_dist(g2, g2[7], L2) + gap_meters
                                if fw1:
                                    s1_min_limit = max(0.0, min(1.0, (limit_d1 - dist1) / L1))
                                else:
                                    s1_max_limit = max(0.0, min(1.0, 1.0 - (limit_d1 - dist1) / L1))
                                    
                                # g2 (smaller d) must be <= g1 (larger d) - gap
                                limit_d2 = _get_ccw_dist(g1, g1[7], L1) - gap_meters
                                if fw2:
                                    s2_max_limit = max(0.0, min(1.0, (limit_d2 - dist2) / L2))
                                else:
                                    s2_min_limit = max(0.0, min(1.0, 1.0 - (limit_d2 - dist2) / L2))
                            
                            if d < 1e-9:
                                if d_p1 < d_p2:
                                    slide1 = -step * L1 if fw1 else step * L1
                                    slide2 = step * L2 if fw2 else -step * L2
                                else:
                                    slide1 = step * L1 if fw1 else -step * L1
                                    slide2 = -step * L2 if fw2 else step * L2
                            else:
                                ux_c, uy_c = dx / d, dy / d
                                slide1 = -step * (ux_c * ux1 + uy_c * uy1)
                                slide2 = step * (ux_c * ux2 + uy_c * uy2)
                                
                            applied1 = 0.0
                            applied2 = 0.0
                            
                            # Aplikace posunu na g1
                            if abs(slide1) > 1e-5:
                                s1_new = max(s1_min_limit, min(s1_max_limit, g1[7] + slide1 / L1))
                                ds1 = (s1_new - g1[7]) * L1
                                if abs(ds1) > 1e-5:
                                    shift_x = ds1 * ux1
                                    shift_y = ds1 * uy1
                                    if _gantry_points_inside(g1[0] + shift_x, g1[1] + shift_y, g1[2], substation_polygons):
                                        g1[0] += shift_x
                                        g1[1] += shift_y
                                        g1[7] = s1_new
                                        towers[g1[3]][0] += shift_x
                                        towers[g1[3]][1] += shift_y
                                        applied1 = ds1
                                        moved = True
                                        
                            # Aplikace posunu na g2
                            if abs(slide2) > 1e-5:
                                s2_new = max(s2_min_limit, min(s2_max_limit, g2[7] + slide2 / L2))
                                ds2 = (s2_new - g2[7]) * L2
                                if abs(ds2) > 1e-5:
                                    shift_x = ds2 * ux2
                                    shift_y = ds2 * uy2
                                    if _gantry_points_inside(g2[0] + shift_x, g2[1] + shift_y, g2[2], substation_polygons):
                                        g2[0] += shift_x
                                        g2[1] += shift_y
                                        g2[7] = s2_new
                                        towers[g2[3]][0] += shift_x
                                        towers[g2[3]][1] += shift_y
                                        applied2 = ds2
                                        moved = True
                                        
                            # If g1 was blocked but slide1 was needed, try to move g2 by the full amount (double step)
                            if abs(applied1) < 1e-5 and abs(slide1) > 1e-5:
                                if d < 1e-9:
                                    if d_p1 < d_p2:
                                        extra_slide2 = (step * 2.0 * L2) if fw2 else (-step * 2.0 * L2)
                                    else:
                                        extra_slide2 = (-step * 2.0 * L2) if fw2 else (step * 2.0 * L2)
                                else:
                                    extra_slide2 = step * 2.0 * (ux_c * ux2 + uy_c * uy2)
                                s2_new = max(s2_min_limit, min(s2_max_limit, g2[7] + extra_slide2 / L2))
                                ds2 = (s2_new - g2[7]) * L2
                                if abs(ds2) > abs(applied2) + 1e-5:
                                    inc_ds2 = ds2 - applied2
                                    shift_x = inc_ds2 * ux2
                                    shift_y = inc_ds2 * uy2
                                    if _gantry_points_inside(g2[0] + shift_x, g2[1] + shift_y, g2[2], substation_polygons):
                                        g2[0] += shift_x
                                        g2[1] += shift_y
                                        g2[7] = s2_new
                                        towers[g2[3]][0] += shift_x
                                        towers[g2[3]][1] += shift_y
                                        moved = True
                                        
                            # If g2 was blocked but slide2 was needed, try to move g1 by the full amount (double step)
                            if abs(applied2) < 1e-5 and abs(slide2) > 1e-5:
                                if d < 1e-9:
                                    if d_p1 < d_p2:
                                        extra_slide1 = (-step * 2.0 * L1) if fw1 else (step * 2.0 * L1)
                                    else:
                                        extra_slide1 = (step * 2.0 * L1) if fw1 else (-step * 2.0 * L1)
                                else:
                                    extra_slide1 = -step * 2.0 * (ux_c * ux1 + uy_c * uy1)
                                s1_new = max(s1_min_limit, min(s1_max_limit, g1[7] + extra_slide1 / L1))
                                ds1 = (s1_new - g1[7]) * L1
                                if abs(ds1) > abs(applied1) + 1e-5:
                                    inc_ds1 = ds1 - applied1
                                    shift_x = inc_ds1 * ux1
                                    shift_y = inc_ds1 * uy1
                                    if _gantry_points_inside(g1[0] + shift_x, g1[1] + shift_y, g1[2], substation_polygons):
                                        g1[0] += shift_x
                                        g1[1] += shift_y
                                        g1[7] = s1_new
                                        towers[g1[3]][0] += shift_x
                                        towers[g1[3]][1] += shift_y
                                        moved = True

                # 2. Push the gantries away from the other pylons on intersection (circle vs OBB)
                for i, g in enumerate(gantries_to_add):
                    k_term = g[3]
                    for tk, t in towers.items():
                        if tk == k_term:
                            continue  # Own feed pylon handled by the dynamic offset
                            
                        r = tower_radii.get(tk, 0.0)
                        if _gantry_collides_with_tower(g[0], g[1], g[2], t[0], t[1], r + 1.0): # Add a 1.0m margin for the pylon arms
                            dx = g[0] - t[0]
                            dy = g[1] - t[1]
                            d = math.hypot(dx, dy)
                            if d < 1e-9:
                                ux_c, uy_c = 1.0, 0.0
                            else:
                                ux_c, uy_c = dx / d, dy / d
                            
                            # Projection of the movement onto the direction of gantry g's fence wall
                            ax, ay, bx, by = g[5]
                            L = math.hypot(bx - ax, by - ay) or 1.0
                            ux, uy = (bx - ax) / L, (by - ay) / L
                            slide = step * 2.0 * (ux_c * ux + uy_c * uy)  # Larger step, we push only one gantry
                            
                            if abs(slide) > 1e-5:
                                s_new = max(0.0, min(1.0, g[7] + slide / L))
                                ds = (s_new - g[7]) * L
                                if abs(ds) > 1e-5:
                                    shift_x = ds * ux
                                    shift_y = ds * uy
                                    if _gantry_points_inside(g[0] + shift_x, g[1] + shift_y, g[2], substation_polygons):
                                        g[0] += shift_x
                                        g[1] += shift_y
                                        g[7] = s_new
                                        towers[g[3]][0] += shift_x
                                        towers[g[3]][1] += shift_y
                                        moved = True
                                        
                if not moved:
                    break
                    
        if templates is None: templates = load_pylon_templates()
        for g_entry in gantries_to_add:
            gx, gy, yaw, k_term, line_idx = g_entry[0], g_entry[1], g_entry[2], g_entry[3], g_entry[4]
            g_key = (gx, gy)
            tmpl = templates.get("Pylon_Substation")
            tmpl1 = templates.get("Pylon_Substation_Low") or tmpl
            if tmpl:
                towers[g_key] = [gx, gy, yaw, tmpl, tmpl1]
                _terminal_pylons.add(g_key)
                _gantry_keys.add(g_key)
                line_spans[line_idx][2].append((k_term, g_key))
                if k_term in towers:
                    # Do not turn towards the substation if it has a neighbour outside the patch bounds, or it is a Pylon_Medium
                    nbrs_term = _neighbours.get(k_term, [])
                    has_outside_nbr = any(
                        nbr in towers and (abs(towers[nbr][0]) > patch_half or abs(towers[nbr][1]) > patch_half)
                        for nbr in nbrs_term
                    )
                    is_medium = towers[k_term][3] and "medium" in towers[k_term][3].name.lower()
                    if not has_outside_nbr and not is_medium:
                        # NOT LINING THE CABLES UP COMES FIRST. This is the last place a
                        # tower's yaw is set, so the check belongs here: taking the yaw
                        # the gantry would like must never leave the crossarm lying along
                        # a cable, or the conductors end up stacked in a single line. If
                        # it would, the tower keeps the yaw it already has.
                        def _worst(_yaw, _key=k_term):
                            _t = towers[_key]
                            _dirs = [(gx - _t[0], gy - _t[1])]
                            for _n in _neighbours.get(_key, []):
                                if _n in towers:
                                    _dirs.append((towers[_n][0] - _t[0], towers[_n][1] - _t[1]))
                            _w = math.pi
                            for _dx, _dy in _dirs:
                                if abs(_dx) < 1e-9 and abs(_dy) < 1e-9:
                                    continue
                                _a = _yaw - math.atan2(_dy, _dx)
                                _a = abs(math.atan2(math.sin(2.0 * _a), math.cos(2.0 * _a))) / 2.0
                                _w = min(_w, _a)
                            return _w

                        if _worst(yaw) >= _worst(towers[k_term][2]):
                            towers[k_term][2] = yaw

    # --- Every tower faces ALL the cables that leave it. -------------------------------
    # A tower's yaw came from the line it belongs to, which is right while the cables only
    # run along that line. Where another cable joins - a second line, or the run into a
    # substation - that one was never taken into account and the crossarm could end up
    # lying along it, stacking those conductors into a single line. The crossarm is now
    # set square to the AVERAGE of every cable that actually leaves the tower (averaged on
    # the doubled angle, because a crossarm has no front or back), so it stands across all
    # of them at once. Towers with a single cable, borrowed ones and the substation
    # gantries keep exactly what they had.
    _all_nbrs: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
    for (_a, _s, _sp, _l) in line_spans:
        for (ka, kb) in _sp:
            _all_nbrs.setdefault(ka, []).append(kb)
            _all_nbrs.setdefault(kb, []).append(ka)
    for k, t in towers.items():
        if k in borrowed_z or k in _gantry_keys:
            continue
        if t[3] is not None and "substation" in t[3].name.lower():
            continue
        dirs = []
        for nbr in _all_nbrs.get(k, ()):
            if nbr not in towers:
                continue
            dx, dy = towers[nbr][0] - t[0], towers[nbr][1] - t[1]
            if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                dirs.append(math.atan2(dy, dx))
        if len(dirs) < 2:
            continue
        sx = sum(math.cos(2.0 * a) for a in dirs)
        sy = sum(math.sin(2.0 * a) for a in dirs)
        if math.hypot(sx, sy) < 1e-9:
            continue
        t[2] = 0.5 * math.atan2(sy, sx) + math.pi / 2.0

    # --- Phase 3: stamp the large/medium towers, then the cables (and warning balls). ---
    for k, t in towers.items():
        x, y, yaw, tmpl, tmpl1 = t
        if k in borrowed_z:
            continue          # already standing there, built by the neighbouring patch
        foot_z = _tower_z(k, x, y)
        _place_pylon(mesh, tmpl, x, y, foot_z, yaw)
        _place_pylon(mesh1, tmpl1, x, y, foot_z, yaw)
        stats.towers += 1
        # Note the finished tower down for the neighbours -- now that its position,
        # height and yaw are final. An in-patch record always wins over a border one.
        if border_log is not None:
            info = node_info.get(k)
            if info is not None:
                # The arm tips exactly where the cables will be hung (same maths as
                # _span_cables), so a neighbour can end its wires on them.
                _ca, _sa = math.cos(yaw), math.sin(yaw)
                att_world = []
                for (_ax, _ay, _az) in tower_attach.get(k, ()):
                    _rx, _ry = _rotz(_ax, _ay, _ca, _sa)
                    att_world.append((_rx + x, _ry + y, _az + foot_z))
                border_log.record(info[0], x, y, foot_z, yaw, info[2], info[1],
                                  att_world)

    _sub_attach = CONDUCTOR_ATTACH.get("Pylon_Substation", [])
    for (attach_local, sag, spans, line) in line_spans:
        if not spans:
            continue
        spans_xyz = []
        for (ka, kb) in spans:
            if ka in borrowed_z and kb in borrowed_z:
                continue      # both ends built by the neighbour -- it drew this span too
            ta, tb = towers[ka], towers[kb]
            za = _tower_z(ka, ta[0], ta[1])
            zb = _tower_z(kb, tb[0], tb[1])
            sx = (ta[0], ta[1], za, ta[2], tb[0], tb[1], zb, tb[2])
            spans_xyz.append(sx)
            if draw_cables and attach_local:
                a_g = ka in _gantry_keys
                b_g = kb in _gantry_keys
                # A borrowed end: its arm tips come from the log, so the wire ends
                # on the tower the neighbouring patch really built.
                wa = borrowed_attach.get(ka)
                wb = borrowed_attach.get(kb)
                if a_g or b_g:
                    # One end is a substation portal: attach its conductors to the
                    # portal's own crossarm points, not to the line pylon's arms.
                    _span_cables_mixed(mesh,
                                       _sub_attach if a_g else attach_local,
                                       _sub_attach if b_g else attach_local,
                                       sag, sx, wa, wb)
                else:
                    _span_cables(mesh, attach_local, sag, [sx], wa, wb)
        if draw_balls and ball_tmpl is not None:
            top = _top_attach(attach_local)
            if top is not None:
                is_hv = _line_is_hv(line)
                tax, tay, taz = top
                for (xa, ya, za, yawa, xb, yb, zb, yawb) in spans_xyz:
                    ca, sa = math.cos(yawa), math.sin(yawa)
                    cb, sb = math.cos(yawb), math.sin(yawb)
                    rax, ray = _rotz(tax, tay, ca, sa)
                    rbx, rby = _rotz(tax, tay, cb, sb)
                    bpa = (rax + xa, ray + ya, taz + za)
                    bpb = (rbx + xb, rby + yb, taz + zb)
                    mode = _ball_mode_for_span(bpa, bpb, sag, terrain, airport_zones, is_hv)
                    if mode is None:
                        continue
                    diameter, spacing = mode
                    stats.balls += _place_balls_on_cable(
                        mesh, ball_tmpl, diameter / ball_native_d, bpa, bpb, sag, spacing)

    logger.info(
        "Powerline mesh: %d towers, %d cable spans, %d warning balls across %d "
        "lines (%d verts, %d faces)",
        stats.towers, stats.cables, stats.balls, stats.lines_with_geometry,
        mesh.vertex_count(), mesh.face_count(),
    )
    return mesh, mesh1, stats


def _spin_blades(tmpl: PylonTemplate, theta: float) -> PylonTemplate:
    """Return a copy of the rotor template spun around the Y axis through
    WT_BLADE_HUB by ``theta`` (the disc lies in the XZ plane; Y is unchanged)."""
    hx, hy, hz = WT_BLADE_HUB
    c, s = math.cos(theta), math.sin(theta)
    verts = []
    for (x, y, z) in tmpl.verts:
        dx, dz = x - hx, z - hz
        verts.append((hx + dx * c + dz * s, y, hz - dx * s + dz * c))
        
    # Rotate normals around Y axis the same way (without hub translation)
    normals = []
    for (nx, ny, nz) in tmpl.normals:
        normals.append((nx * c + nz * s, ny, -nx * s + nz * c))
        
    return PylonTemplate(
        name=tmpl.name, 
        verts=verts, 
        uvs=tmpl.uvs, 
        normals=normals, 
        faces=tmpl.faces
    )


def generate_wind_turbines_mesh(
    turbines,
    terrain: TerrainMesh,
    templates: Optional[Dict[str, PylonTemplate]] = None,
    bake_world: bool = False,
    yaw: float = 0.0,
    seed: int = 0,
) -> Tuple[List[MeshData], List[MeshData], int]:
    """Build wind turbines for BOTH LODs in a single pass: the placement
    (foot_z, yaw, blade spin) is computed once per turbine and stamped with the
    detailed model (LOD0) and the low model (LOD1). Towers/nacelles share the
    patch yaw; each rotor is spun to its own random angle around the hub (same
    angle in both LODs). Returns (lod0_meshes, lod1_meshes, count)."""
    import random
    if templates is None:
        templates = load_pylon_templates()
    tower0 = templates.get("Wind_Turbine_Tower")
    blades0 = templates.get("Wind_Turbine_Blades")
    tower1 = templates.get("Wind_Turbine_Tower_Low")
    blades1 = templates.get("Wind_Turbine_Blades_Low")
    if None in (tower0, blades0, tower1, blades1):
        logger.warning("Sablona pro vetrnou elektrarnu nebyla nalezena.")
        return [], [], 0

    rng = random.Random(seed)

    if bake_world:
        # File mode: bake every turbine into ONE merged mesh per LOD, at its world
        # position (x,y from OSM, z from terrain).
        merged0 = MeshData(osm_id="wind_turbine")
        merged1 = MeshData(osm_id="wind_turbine")
        count = 0
        for t in turbines:
            spin = rng.uniform(0.0, 2.0 * math.pi)
            if not t.in_patch:
                continue
            foot_z = _foot_z(terrain, t.x, t.y)
            _place_pylon(merged0, tower0, t.x, t.y, foot_z, yaw)
            _place_pylon(merged0, _spin_blades(blades0, spin), t.x, t.y, foot_z, yaw)
            _place_pylon(merged1, tower1, t.x, t.y, foot_z, yaw)
            _place_pylon(merged1, _spin_blades(blades1, spin), t.x, t.y, foot_z, yaw)
            count += 1
        if count == 0:
            return [], [], 0
        logger.info("Vygenerovano %d vetrnych elektraren (slouceno, zapeceno).", count)
        return [merged0], [merged1], count

    meshes0 = []
    meshes1 = []
    for t in turbines:
        spin = rng.uniform(0.0, 2.0 * math.pi)
        if not t.in_patch:
            continue
        idx = len(meshes0)
        foot_z = _foot_z(terrain, t.x, t.y)
        m0 = MeshData(osm_id=f"wind_turbine_{idx}", origin=(t.x, t.y, foot_z))
        _place_pylon(m0, tower0, 0.0, 0.0, 0.0, 0.0)
        _place_pylon(m0, _spin_blades(blades0, spin), 0.0, 0.0, 0.0, 0.0)
        m1 = MeshData(osm_id=f"wind_turbine_{idx}", origin=(t.x, t.y, foot_z))
        _place_pylon(m1, tower1, 0.0, 0.0, 0.0, 0.0)
        _place_pylon(m1, _spin_blades(blades1, spin), 0.0, 0.0, 0.0, 0.0)
        meshes0.append(m0)
        meshes1.append(m1)
    logger.info("Vygenerovano %d vetrnych elektraren.", len(meshes0))
    return meshes0, meshes1, len(meshes0)


# ---------------------------------------------------------------------------
# Standalone runner (writes a viewer OBJ)
# ---------------------------------------------------------------------------

def _main() -> None:
    from ..io.patch_metadata import load_patch_metadata
    from ..io.terrain_loader import load_terrain
    from ..io.powerline_parser import parse_powerlines
    from ..io.obj_exporter import export_mesh_groups
    from ..projection import create_projector

    parser = argparse.ArgumentParser(
        description="Generate powerline geometry (towers + cables) for a Condor patch."
    )
    parser.add_argument("--patch-dir", required=True)
    parser.add_argument("--patch-id", required=True)
    parser.add_argument("--osm", default=None, help="OSM file (default: map_21.osm in patch-dir)")
    parser.add_argument("--output", default=None, help="Output OBJ (default: <patch-dir>/powerlines_<patch>.obj)")
    parser.add_argument("--no-cables", action="store_true", help="Towers only")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    metadata = load_patch_metadata(os.path.join(args.patch_dir, f"h{args.patch_id}.txt"))
    projector = create_projector(metadata.zone_number, metadata.translate_x, metadata.translate_y)

    osm_path = args.osm or os.path.join(args.patch_dir, "map_21.osm")
    result = parse_powerlines(osm_path, projector)

    from ..blender.terrain_smooth import load_terrain_smoothed
    terrain = load_terrain_smoothed(args.patch_dir, args.patch_id)

    mesh, _mesh_lod1, stats = generate_powerline_meshes(
        result.lines, terrain, draw_cables=not args.no_cables
    )
    opt = mesh.optimize()
    logger.info("Optimized: %s", opt)

    out = args.output or os.path.join(args.patch_dir, f"powerlines_{args.patch_id}.obj")
    export_mesh_groups({PYLON_MATERIAL: mesh}, out, comment=f"Powerlines patch {args.patch_id}")
    print(
        f"\nWrote {out}\n"
        f"  towers ............ {stats.towers}\n"
        f"  cable spans ....... {stats.cables}\n"
        f"  lines w/ geometry . {stats.lines_with_geometry}\n"
        f"  vertices .......... {mesh.vertex_count()}\n"
        f"  faces ............. {mesh.face_count()}\n"
    )


if __name__ == "__main__":
    _main()
