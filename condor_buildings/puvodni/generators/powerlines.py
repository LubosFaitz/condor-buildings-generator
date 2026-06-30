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

logger = logging.getLogger(__name__)

# Material/object name shared by towers + cables (Wiek's material is "pylones").
PYLON_MATERIAL = "pylones"
PYLON_TEXTURE = "Pylons.dds"

# Pylon template filenames keyed by the parser's pylon_type.
PYLON_FILES = {
    "Pylon_Large": "pylon_large.obj",
    "Pylon_Medium": "pylon_medium.obj",
    "Pylon_Small": "pylon_small.obj",
    "Wind_Turbine": "turbine.obj",
}

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
}

# Cable geometry / sag (metres). Confirmed by Wiek (2026-06-03).
# Cables are "0.1 m thick" in his geometry nodes -> tube circumradius ~0.05 m.
CABLE_RADIUS = 0.05

# Constant sag per pylon tier (Wiek: 8 m large / 4 m medium / 2 m small). NOT
# span-based — a fixed droop per tower type, matching his original tool.
SAG_BY_TYPE = {
    "Pylon_Large": 8.0,
    "Pylon_Medium": 4.0,
    "Pylon_Small": 2.0,
}
DEFAULT_SAG = 4.0

# Adaptive cable sectioning (Wiek's vertex-budget request): pick *just enough*
# segments so the straight-segment polyline never deviates more than
# CABLE_SMOOTH_TOL (m) from the true parabola. A parabola of sag s split into N
# equal segments has max deviation s/N^2, so N = ceil(sqrt(s / tol)), clamped.
# Short/near-straight spans get 1-2 cuts; only long deep-sag spans approach the
# cap. Was a fixed 8 cuts per span regardless of length (wasteful on short spans).
CABLE_SMOOTH_TOL = 0.30
CABLE_MIN_SAMPLES = 1
CABLE_MAX_SAMPLES = 10

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


# ---------------------------------------------------------------------------
# Pylon templates
# ---------------------------------------------------------------------------

@dataclass
class PylonTemplate:
    """A pylon mesh template loaded from an OBJ (upright, foot at origin, Z-up)."""
    name: str
    verts: List[Tuple[float, float, float]] = field(default_factory=list)
    uvs: List[Tuple[float, float]] = field(default_factory=list)
    # Each face: list of (vertex_index, uv_index), 0-based into verts / uvs.
    faces: List[List[Tuple[int, int]]] = field(default_factory=list)

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
    """Parse a (small) OBJ pylon template: v / vt / f 'v/vt' tokens."""
    verts: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    faces: List[List[Tuple[int, int]]] = []
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
            elif line.startswith("o "):
                name = line[2:].strip()
            elif line.startswith("f "):
                face: List[Tuple[int, int]] = []
                for tok in line.split()[1:]:
                    bits = tok.split("/")
                    vi = int(bits[0]) - 1
                    ti = int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1
                    face.append((vi, ti))
                faces.append(face)
    return PylonTemplate(name=name, verts=verts, uvs=uvs, faces=faces)


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
    return templates


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


def _foot_z(terrain: TerrainMesh, x: float, y: float) -> float:
    """
    Ground Z for a tower foot. Samples the node and a small ring around it and
    takes the minimum so the tower sits on (slightly into) the lowest nearby
    ground rather than floating, then sinks by TOWER_SINK.
    """
    samples = [(x, y)]
    for dx, dy in ((4.0, 0.0), (-4.0, 0.0), (0.0, 4.0), (0.0, -4.0)):
        samples.append((x + dx, y + dy))
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

    for vx, vy, vz in tmpl.verts:
        rx, ry = _rotz(vx, vy, c, s)
        mesh.add_vertex(rx + x, ry + y, vz + foot_z)
    for u, v in tmpl.uvs:
        mesh.add_uv(u, v)

    for face in tmpl.faces:
        vidx = [v_base + vi + 1 for vi, _ in face]
        uvidx = [uv_base + (ti if ti >= 0 else 0) + 1 for _, ti in face]
        mesh.add_polygon_with_uvs(vidx, uvidx)


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

    for i in range(n_samples):
        for k in range(3):
            a = rings[i][k]
            b = rings[i][(k + 1) % 3]
            c = rings[i + 1][(k + 1) % 3]
            d = rings[i + 1][k]
            mesh.add_quad_with_uvs(a[0], b[0], c[0], d[0],
                                   a[1], b[1], c[1], d[1])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class PowerlineMeshStats:
    towers: int = 0
    cables: int = 0
    lines_with_geometry: int = 0
    turbines: int = 0


def generate_powerline_meshes(
    lines,
    terrain: TerrainMesh,
    templates: Optional[Dict[str, PylonTemplate]] = None,
    patch_half: float = PATCH_HALF,
    draw_cables: bool = True,
) -> Tuple[MeshData, PowerlineMeshStats]:
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

    Returns:
        (MeshData for the 'pylones' object, PowerlineMeshStats).
    """
    if templates is None:
        templates = load_pylon_templates()

    mesh = MeshData(osm_id=PYLON_MATERIAL)
    stats = PowerlineMeshStats()

    for line in lines:
        pts = line.points
        if len(pts) < 2:
            continue
        tmpl = templates.get(line.pylon_type)
        if tmpl is None:
            logger.warning("No template for %s (way %s)", line.pylon_type, line.way_id)
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
        attach_local = CONDUCTOR_ATTACH.get(line.pylon_type, [])

        # World transform per node (only computed for placed nodes).
        node_world: Dict[int, Tuple[float, float, float, float]] = {}  # j -> (x,y,foot_z,yaw)
        for j in range(n):
            if not placed[j]:
                continue
            x, y = xy[j]
            foot_z = _foot_z(terrain, x, y)
            yaw = _yaw_at(xy, j)
            node_world[j] = (x, y, foot_z, yaw)
            _place_pylon(mesh, tmpl, x, y, foot_z, yaw)
            stats.towers += 1

        had_geometry = bool(node_world)

        if draw_cables and attach_local:
            sag = SAG_BY_TYPE.get(line.pylon_type, DEFAULT_SAG)
            for j in range(n - 1):
                if j not in node_world or (j + 1) not in node_world:
                    continue
                xa, ya, za, yawa = node_world[j]
                xb, yb, zb, yawb = node_world[j + 1]
                ca, sa = math.cos(yawa), math.sin(yawa)
                cb, sb = math.cos(yawb), math.sin(yawb)
                for (ax, ay, az) in attach_local:
                    rax, ray = _rotz(ax, ay, ca, sa)
                    rbx, rby = _rotz(ax, ay, cb, sb)
                    pa = (rax + xa, ray + ya, az + za)
                    pb = (rbx + xb, rby + yb, az + zb)
                    _add_cable(mesh, pa, pb, sag)
                    stats.cables += 1

        if had_geometry:
            stats.lines_with_geometry += 1

    logger.info(
        "Powerline mesh: %d towers, %d cable spans across %d lines (%d verts, %d faces)",
        stats.towers, stats.cables, stats.lines_with_geometry,
        mesh.vertex_count(), mesh.face_count(),
    )
    return mesh, stats


def generate_wind_turbines_mesh(
    turbines,
    terrain: TerrainMesh,
    templates: Optional[Dict[str, PylonTemplate]] = None,
) -> Tuple[List[MeshData], int]:
    if templates is None:
        templates = load_pylon_templates()
    tmpl = templates.get("Wind_Turbine")
    if tmpl is None:
        logger.warning("Sablona pro vetrnou elektrarnu nebyla nalezena.")
        return [], 0
    meshes = []
    for t in turbines:
        if not t.in_patch:
            continue
        foot_z = _foot_z(terrain, t.x, t.y)
        mesh = MeshData(osm_id=f"wind_turbine_{len(meshes)}", origin=(t.x, t.y, foot_z))
        _place_pylon(mesh, tmpl, 0.0, 0.0, 0.0, 0.0)
        meshes.append(mesh)
    logger.info("Vygenerovano %d vetrnych elektraren.", len(meshes))
    return meshes, len(meshes)


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

    terrain = load_terrain(os.path.join(args.patch_dir, f"h{args.patch_id}.obj"))

    mesh, stats = generate_powerline_meshes(
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
