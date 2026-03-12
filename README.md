# Condor Buildings Generator

[![Version](https://img.shields.io/badge/version-0.7.3-blue.svg)](https://github.com/yourusername/condor-buildings-generator)
[![Python](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/)
[![Blender](https://img.shields.io/badge/blender-4.0+-orange.svg)](https://www.blender.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

A Python pipeline that generates 3D building meshes from OpenStreetMap (OSM) data for use in the **Condor 3** flight simulator. The pipeline produces OBJ files with UV coordinates compatible with Condor's terrain system.

**Now available as a Blender addon!** Generate buildings directly in Blender's viewport.

---

## Quick Start

### Option 1: Command Line

```bash
# Clone the repository
git clone https://github.com/yourusername/condor-buildings-generator.git
cd condor-buildings-generator

# Run the pipeline (no dependencies required - uses only Python standard library)
python -m condor_buildings.main \
  --patch-dir ./your_patch_data \
  --patch-id 036019 \
  --output-dir ./output \
  --verbose
```

### Option 2: Blender Addon (v0.5.0+)

1. Download `condor_buildings_v0.7.3.zip` from releases
2. In Blender: Edit > Preferences > Add-ons > Install
3. Select the ZIP file
4. Enable "Condor Buildings Generator" addon
5. Open the sidebar in 3D View (press N)
6. Navigate to the "Condor" tab
7. Set your Condor installation path (e.g., `C:\Condor3`)
8. Select a landscape from the dropdown
9. Set patch range (X/Y min/max) or enable single patch mode
10. Click "Generate Buildings"

**New in v0.7.3:**
- **Fix UV mapping (floors/height sync)**: When a building had a `building:levels` OSM tag but no `height` tag, `floors` came from the tag while `height_m` came from the category estimate — they could diverge, causing wall textures to show the wrong number of floor sections. Now `height_m` is recomputed to match `floors` when height was estimated. Also fixes `int()` → `round()` truncation for explicit height tags.

**New in v0.7.2:**
- **Fix HOUSE height estimation**: Houses no longer estimated at 3 floors for footprints > 150 m². All houses now default to 2 floors (6m) unless OSM explicitly tags `building:levels` or `height`. This fixes the majority of houses incorrectly getting flat roofs and highrise textures.
- **Floor guard in roof selection**: `select_roof_type()` now checks floor count for HOUSE category, preventing tall houses (explicit OSM `building:levels > 2`) from being assigned pitched roofs.
- **Hipped roof stability**: Increased near-square detection tolerance and added ridge vertex validation to prevent "diamond" visual artifacts on near-square buildings.

**New in v0.7.0:**
- **Highrise wall system**: Apartment and commercial buildings now use a separate `Highrise_atlas.dds` texture (2048x12288, 12 regions). Multi-floor wall quads (up to 4 floors per quad) with category-specific texture regions (6 apartment, 6 commercial). Previous `apartment_walls` and `commercial_walls` OBJ objects merged into single `Highrise_walls`.

**New in v0.6.8:**
- **Correct polyskel roof UV mapping**: Roof tiles now have consistent size and aspect ratio across all faces of complex hipped roofs (L/T/U-shaped buildings). Uses orthographic planar projection with unified global scaling.

**New in v0.6.3:**
- **Texture-based mesh grouping**: Buildings grouped into 10 objects by texture type for optimal rendering in Condor (single draw call per texture)

**New in v0.6.2:**
- **Automatic vertex optimization**: Mesh vertex deduplication reduces file size by ~63% with no configuration required

**New in v0.6.1:**
- **Configurable parameters in UI**: Gable height, roof overhang, floor Z offset, max floors, rectangularity, polyskel vertices, and random seed adjustable in Blender

**New in v0.6.0:**
- **Polyskel hipped roofs**: Buildings with 5-12 vertices now get proper hipped roofs using bpypolyskel straight skeleton algorithm

**New in v0.5.0:**
- Auto-detects landscapes from Condor folder structure
- Downloads OSM building data on-the-fly from Overpass API
- Supports batch processing of multiple patches
- Saves OBJ files to `Working/Autogen` folder
- Imports meshes directly into Blender viewport

### Input Files Required

Place these files in your `--patch-dir`:

| File | Description |
|------|-------------|
| `h{patch_id}.txt` | Patch metadata (UTM zone, translation offsets) |
| `h{patch_id}.obj` | Terrain mesh |
| `map_*.osm` | OSM building data (auto-discovered) |

### Output Files Generated

| File | Description |
|------|-------------|
| `o{patch_id}_LOD0.obj` | Detailed mesh with 0.5m roof overhang |
| `o{patch_id}_LOD1.obj` | Simplified mesh without overhang |
| `o{patch_id}_report.json` | Processing statistics |
| `o{patch_id}.log` | Detailed processing log |

---

## Features

- **Multiple roof types**: Gabled, hipped (including polyskel for complex shapes), and flat roofs
- **OSM multipolygon support**: Buildings with holes/courtyards
- **Terrain integration**: Floor Z computed from terrain mesh intersection
- **UV mapping**: Full texture atlas support (6 roof patterns, 12 facade styles)
- **Two LOD levels**: LOD0 (detailed) and LOD1 (simplified)
- **Deterministic output**: Same seed produces identical results
- **Blender integration**: Import buildings directly into Blender (v0.5.0+ with Condor workflow support)
- **Zero dependencies**: Uses only Python standard library (works anywhere)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Pipeline Stages](#3-pipeline-stages)
4. [Coordinate System](#4-coordinate-system)
5. [Data Models](#5-data-models)
6. [OSM Parsing](#6-osm-parsing)
7. [Building Classification](#7-building-classification)
8. [Roof Generation](#8-roof-generation)
9. [Wall Generation](#9-wall-generation)
10. [UV Mapping and Texture Atlas](#10-uv-mapping-and-texture-atlas-phase-2)
11. [Terrain Integration](#11-terrain-integration)
12. [Output Format](#12-output-format)
13. [Configuration](#13-configuration)
14. [Usage](#14-usage)
15. [Test Results](#15-test-results)
16. [Pending Work](#16-pending-work)
17. [Appendices](#appendix-a-blosm-roof-semantics)

---

## 1. Overview

The Condor Buildings Generator is a standalone Python pipeline that generates 3D building meshes from OpenStreetMap (OSM) data for use in the Condor 3 flight simulator. The pipeline produces OBJ files compatible with Condor's terrain system.

### Goals

- Generate accurate building geometry from OSM footprints
- Support multiple roof types (gabled, flat, hipped)
- Produce two LOD levels per patch
- Integrate with Condor's terrain mesh for floor Z positioning
- Maintain exact footprint fidelity (no simplification)
- Full UV mapping with texture atlas support (Phase 2)

### Key Features

- Parses OSM XML including multipolygon relations with holes
- Projects WGS84 coordinates to Condor's local coordinate system
- Classifies buildings by type (house, apartment, industrial, commercial)
- Generates gabled roofs using OBB-based approach for stability
- Generates hipped roofs using BLOSM's analytical solution for quadrilaterals
- Generates hipped roofs for 5-12 vertex buildings using bpypolyskel straight skeleton with correct UV mapping
- Computes floor Z from terrain mesh intersection
- Exports OBJ with per-building groups and UV coordinates
- Texture atlas support with 6 roof patterns and 12 facade styles
- Deterministic variation selection per building (seed-based)

---

## 2. Architecture

### Module Structure

```
condor_buildings/
├── __init__.py              # Package version + Blender addon registration
├── main.py                  # CLI entry point and pipeline orchestrator
├── config.py                # Configuration constants and PipelineConfig
├── blender/                 # Blender addon package (v0.5.0+)
│   ├── __init__.py          # Blender addon initialization
│   ├── properties.py        # Blender PropertyGroup for UI fields
│   ├── operators.py         # Import/clear operators with Condor workflow
│   ├── panels.py            # UI panels (sidebar)
│   ├── mesh_converter.py    # MeshData → Blender mesh conversion
│   └── osm_downloader.py    # Download OSM data from Overpass API
├── models/
│   ├── geometry.py          # Point2D, Point3D, Polygon, BBox
│   ├── building.py          # BuildingRecord, BuildingCategory, RoofType
│   ├── mesh.py              # MeshData with vertex/face management
│   └── terrain.py           # TerrainMesh, TerrainTriangle
├── projection/
│   └── transverse_mercator.py  # UTM projection for Condor coordinates
├── io/
│   ├── osm_parser.py        # OSM XML parsing
│   ├── way_stitcher.py      # Multipolygon way stitching
│   ├── terrain_loader.py    # Terrain OBJ loader
│   ├── patch_metadata.py    # h*.txt header parser
│   └── obj_exporter.py      # OBJ file export
├── processing/
│   ├── footprint.py         # Footprint analysis, OBB, eligibility
│   ├── spatial_index.py     # Grid-based spatial index for terrain
│   ├── floor_z_solver.py    # Floor Z computation from terrain
│   └── patch_filter.py      # Filter buildings outside patch bounds
├── bpypolyskel/             # Embedded straight skeleton library (GPL v3)
│   ├── bpypolyskel.py       # Main algorithm
│   ├── bpyeuclid.py         # 2D geometry primitives
│   └── poly2FacesGraph.py   # Skeleton to faces conversion
├── generators/
│   ├── building_generator.py  # Orchestrator for walls + roof
│   ├── walls.py             # Wall mesh generation
│   ├── roof_flat.py         # Flat roof generation
│   ├── roof_gabled.py       # Gabled roof generation (OBB-based)
│   ├── roof_hipped.py       # Hipped roof generation (BLOSM analytical, 4 verts)
│   ├── roof_polyskel.py     # Hipped roof generation (straight skeleton, >4 verts)
│   └── uv_mapping.py        # UV coordinate generation for texture atlas
└── utils/
    ├── math_utils.py        # Mathematical utilities
    ├── triangulation.py     # Polygon triangulation (ear clipping)
    └── polygon_utils.py     # Polygon utilities (area, collinear removal)
```

### Data Flow

```
┌─────────────────┐
│   OSM XML File  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│   OSM Parser    │────▶│ BuildingRecord  │
└─────────────────┘     │    (list)       │
                        └────────┬────────┘
                                 │
┌─────────────────┐              │
│  Terrain Mesh   │──────────────┼─────────┐
└─────────────────┘              │         │
                                 ▼         ▼
                        ┌─────────────────────┐
                        │   Floor Z Solver    │
                        └──────────┬──────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │  Building Generator │
                        │  (walls + roof)     │
                        └──────────┬──────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │    OBJ Exporter     │
                        └──────────┬──────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │  LOD0.obj, LOD1.obj │
                        └─────────────────────┘
```

---

## 3. Pipeline Stages

The pipeline executes the following stages in order:

### Stage 1: Load Patch Metadata

Reads the Condor patch header file (`h{patch_id}.txt`) to extract:
- UTM zone number
- Translation offsets (TranslateX, TranslateY)

### Stage 2: Create Projector

Initializes the Transverse Mercator projector with UTM zone and translation offsets for converting WGS84 lat/lon to local Condor coordinates.

### Stage 3: Load Terrain Mesh

Loads the terrain OBJ file (`h{patch_id}.obj`) and builds a spatial index for efficient terrain queries.

### Stage 4: Parse OSM Buildings

Parses the OSM XML file, extracting:
- Node coordinates
- Way definitions (building footprints)
- Multipolygon relations (buildings with holes)
- Building tags (type, height, roof shape, etc.)

### Stage 5: Filter Buildings

Removes buildings that:
- Have centroids outside patch bounds (±2880m from origin)
- Are on patch edges (to avoid partial rendering)

### Stage 6: Process Buildings

For each building:
1. Compute floor Z from terrain intersection
2. Select roof type based on category and OSM tags
3. Analyze footprint for gabled eligibility
4. Generate LOD0 mesh (walls + roof with overhang)
5. Generate LOD1 mesh (walls + roof without overhang)

### Stage 7: Export OBJ Files

Exports combined meshes to:
- `o{patch_id}_LOD0.obj` - Detailed mesh
- `o{patch_id}_LOD1.obj` - Simplified mesh

### Stage 8: Generate Report

Creates `o{patch_id}_report.json` with statistics.

---

## 4. Coordinate System

### Condor Coordinate System

- **Origin:** Center of patch (0, 0, 0)
- **X-axis:** Positive East
- **Y-axis:** Positive North
- **Z-axis:** Positive Up
- **Patch extent:** ±2880m (5760m × 5760m total)

### Projection

The pipeline uses UTM (Transverse Mercator) projection:

```python
class TransverseMercatorProjector:
    def project(self, lat: float, lon: float) -> Tuple[float, float]:
        # 1. Convert lat/lon to UTM easting/northing
        # 2. Apply Condor translation offsets
        # 3. Return (x, y) in local coordinates
```

**Parameters from h*.txt:**
- `ZoneNumber`: UTM zone (e.g., 33 for Slovenia)
- `TranslateX`: X offset (typically negative UTM easting)
- `TranslateY`: Y offset (typically negative UTM northing)

### Winding Order

All geometry uses **counter-clockwise (CCW)** winding for outward-facing normals:
- Outer rings: CCW
- Holes: CW (reversed for inward-facing normals)
- Faces: CCW vertices for outward normal

---

## 5. Data Models

### Point2D / Point3D

```python
@dataclass
class Point2D:
    x: float
    y: float

@dataclass
class Point3D:
    x: float
    y: float
    z: float
```

### Polygon

```python
@dataclass
class Polygon:
    outer_ring: List[Point2D]  # CCW winding
    holes: List[List[Point2D]] # CW winding each

    def area(self) -> float
    def bbox(self) -> BBox
    def has_holes(self) -> bool
```

### BuildingRecord

```python
@dataclass
class BuildingRecord:
    osm_id: str
    category: BuildingCategory    # HOUSE, APARTMENT, INDUSTRIAL, COMMERCIAL, OTHER
    footprint: Polygon
    floors: int                   # Number of floors (default 2)
    height_m: float               # Wall height in meters
    roof_type: RoofType           # GABLED, FLAT, HIPPED
    roof_pitch_deg: float         # Pitch angle (30-60°)
    roof_direction_deg: float     # Ridge direction (0=East, CCW)
    floor_z: float                # Ground elevation from terrain
    seed: int                     # Deterministic random seed

    @property
    def wall_top_z(self) -> float  # floor_z + height_m
```

### MeshData

```python
@dataclass
class MeshData:
    osm_id: str
    vertices: List[Point3D]
    faces: List[List[int]]  # 0-indexed vertex indices

    def add_vertex(x, y, z) -> int       # Returns vertex index
    def add_triangle(v0, v1, v2)         # CCW winding
    def add_quad(v0, v1, v2, v3)         # CCW winding
    def merge(other: MeshData)           # Combine meshes
```

---

## 6. OSM Parsing

### Supported Elements

| Element | Usage |
|---------|-------|
| `<node>` | Coordinate storage (lat/lon) |
| `<way>` | Simple building footprints |
| `<relation type="multipolygon">` | Buildings with holes |

### Tag Extraction

| OSM Tag | Usage |
|---------|-------|
| `building=*` | Building type classification |
| `building:levels` | Number of floors |
| `height` | Total building height |
| `roof:shape` | Roof type (gabled, flat, hipped) |
| `roof:direction` | Ridge direction in degrees |
| `roof:angle` | Roof pitch angle |

### Multipolygon Handling

The parser uses `way_stitcher.py` to handle multipolygon relations where outer/inner rings are split across multiple ways:

```python
def stitch_ways(segments: List[WaySegment]) -> List[List[str]]:
    """
    Stitch way segments into closed rings.
    Handles unordered segments by matching endpoints.
    """
```

### Footprint Processing

After parsing, footprints are processed:
1. **Close ring** - Ensure first == last vertex
2. **Remove collinear points** - Simplify without changing shape
3. **Normalize winding** - CCW for outer, CW for holes

---

## 7. Building Classification

### Category Detection

Buildings are classified based on OSM `building=*` tag:

| Category | OSM Values |
|----------|-----------|
| HOUSE | house, detached, semidetached_house, terrace, farm, villa, bungalow |
| APARTMENT | apartments, flats, dormitory, tower, block |
| INDUSTRIAL | industrial, warehouse, factory, hangar, barn, silo |
| COMMERCIAL | commercial, retail, office, hotel, shop, restaurant, school |
| OTHER | yes, unknown values |

### Roof Type Selection

The `select_roof_type()` function determines roof type based on a configurable selection mode.

#### Roof Selection Modes (v0.3.5+)

Two modes are available via `--roof-selection-mode`:

| Mode | Description |
|------|-------------|
| `geometry` | (Default) Use geometry + category heuristics + area |
| `osm_tags_only` | Only buildings tagged as houses get pitched roofs |

#### Mode: `geometry` (Default)

1. **OSM tag** - If `roof:shape` is specified, use it
2. **Category rules:**
   - INDUSTRIAL → FLAT
   - COMMERCIAL → FLAT if >2 floors or >8m height
   - APARTMENT → FLAT if >3 floors or >10m height
   - HOUSE → GABLED
3. **Area heuristic for OTHER:**
   - < 200 m² → GABLED (likely house)
   - 200-400 m² → GABLED if ≤2 floors and ≤8m, else FLAT
   - > 400 m² → FLAT (likely industrial/commercial)

#### Mode: `osm_tags_only`

Only `BuildingCategory.HOUSE` receives pitched roofs:
- `building=house`, `detached`, `villa`, `bungalow`, etc. → GABLED (or HIPPED with `--random-hipped`)
- `building=yes`, `apartments`, `commercial`, etc. → FLAT

This mode is useful when OSM data has good building type tagging and you want to avoid false positives.

### Height Estimation

When height is not specified in OSM:

```python
def estimate_height(footprint_area: float, category: BuildingCategory):
    floor_height = 3.0  # meters per floor

    if category == INDUSTRIAL:
        return 1, 6.0
    elif category == APARTMENT:
        if area > 500: return 4, 12.0
        elif area > 200: return 3, 9.0
        else: return 2, 6.0
    # ... etc
```

---

## 8. Roof Generation

### Gabled Roof Algorithm

The gabled roof generator uses an **OBB-based approach** that guarantees no self-intersection:

#### Fixed Gable Height (v0.2.5+)

As of Phase 1, gabled roofs use a **fixed gable height of 3.0m** instead of calculating from pitch angle:

```python
GABLE_HEIGHT_FIXED = 3.0  # meters

# Ridge height is always 3.0m above wall top
ridge_z = wall_top_z + GABLE_HEIGHT_FIXED

# Pitch is now derived (for reference only)
derived_pitch = atan(3.0 / half_width)
```

This simplifies UV mapping since all gable triangles have the same height.

#### Step 1: Compute Ridge Direction

```python
def _get_ridge_direction(building, ring) -> float:
    # If OSM specifies direction, use it
    if building.roof_direction_deg is not None:
        return building.roof_direction_deg

    # Otherwise: ridge runs PARALLEL to longest edge
    ridge_direction = compute_longest_edge_axis(ring)
    return ridge_direction
```

**Key insight:** The ridge runs **parallel** to the longest edge of a building. The span (eave-to-eave distance) is the short dimension.

#### Step 2: Compute OBB

```python
def compute_obb(ring, direction_deg) -> dict:
    """
    Compute Oriented Bounding Box along given direction.

    Returns:
        - length: Size along direction (ridge length)
        - width: Size perpendicular (eave-to-eave distance)
        - center_x, center_y: OBB center
    """
```

#### Step 3: Generate Roof Geometry

```
Geometry layout (looking down, ridge points right →):

    c3 -------- r0 -------- c0
    |           |           |
    |   LEFT    |   RIGHT   |
    |   SLOPE   |   SLOPE   |
    |           |           |
    c2 -------- r1 -------- c1

Vertices:
- c0-c3: Eave corners at wall_top_z
- r0-r1: Ridge endpoints at wall_top_z + ridge_height

Faces (CCW winding):
- Right slope: c1 → c0 → r0 → r1
- Left slope: c3 → r0 → r1 → c2
- Back gable: c0 → c3 → r0
- Front gable: c2 → c1 → r1
```

#### Step 4: Generate Gable End Walls (Pentagonal Architecture)

As of v0.2.4, gabled buildings use **pentagonal gable walls** that extend from floor to ridge as a single solid body:

```
        v4 (apex at ridge_z)
        /\
       /  \
      /    \
   v3 ------ v2  (wall_top_z / eave)
    |        |
    |  RECT  |   <- Pentagon face
    |        |
   v0 ------ v1  (floor_z)
```

**Key architectural change:** The walls are one body (including gable), and the roof is an independent floating body (2 slope planes only).

For 1-floor buildings, the gable is a **separate triangular face** to enable proper UV mapping:
- Rectangle (floor to wall_top): ground section texture
- Triangle (wall_top to ridge): gable section texture

For multi-floor buildings, a single pentagon face is used.

### Gabled Eligibility Criteria

Not all footprints can have gabled roofs. Eligibility checks:

| Criterion | Threshold | Reason |
|-----------|-----------|--------|
| Vertex count | = 4 | Only rectangles (simplified from ≤20) |
| Convexity | Strictly convex | All cross products same sign |
| Rectangularity | ≥ 0.70 | Area/OBB ratio threshold |
| Angle tolerance | ±25° from 90° | Must be rectangle-like |
| Holes | None | Cannot gable buildings with courtyards |
| Floors | ≤ 2 | Gabled roofs only for 1-2 floor buildings |

**House-scale size gate (v0.2.1+, thresholds increased v0.3.6):**

| Criterion | Threshold | Fallback Reason |
|-----------|-----------|-----------------|
| Footprint area | ≤ 360 m² | `too_large_area` |
| Side length | 3.2m - 30m | `too_short_side` / `too_long_side` |
| Aspect ratio | ≤ 4.8 | `too_elongated` |

If any criterion fails, the building falls back to a flat roof with an explicit fallback reason.

### Ridge Height (Fixed)

As of v0.2.5, ridge height is **fixed at 3.0m**:

```python
ridge_height = GABLE_HEIGHT_FIXED  # 3.0m
derived_pitch = atan(3.0 / half_width)  # For reference only
```

### Overhang

- LOD0: 0.5m overhang (extends all eave edges)
- LOD1: No overhang

Roof slope is calculated so that at the overhang edge, Z = wall_top_z (visible overhang).

### Double-Sided Roof Faces (v0.2.3+)

Roof faces are duplicated with reversed winding for visibility from below:

```python
def _duplicate_faces_reversed(mesh, start_idx, end_idx):
    for face in mesh.faces[start_idx:end_idx]:
        reversed_face = face[::-1]  # Flip normal
        mesh.faces.append(reversed_face)
```

### Flat Roof

For flat roofs, the footprint is triangulated directly at `wall_top_z` using ear-clipping triangulation. UV coordinates use world-space XY scaled to 3m = 1.0 UV unit.

### Hipped Roof (v0.3.4+, Z fix v0.3.6, polyskel v0.6.0)

Hipped roofs are generated using two different algorithms depending on vertex count:

#### Analytical Hipped (4 vertices)

For quadrilateral buildings, uses BLOSM's analytical solution:

**Algorithm:**
1. Compute edge geometry (vectors, lengths, angles) on ORIGINAL footprint
2. Calculate "edge event" distances where bisectors meet
3. Find ridge endpoints from minimum distance edges
4. Create 4 roof faces: 2 triangular hips + 2 trapezoidal sides

**Special case:** Square footprints generate pyramidal roofs (single apex point).

#### Polyskel Hipped (5-12 vertices, Blender only)

For buildings with more than 4 vertices (L-shaped, T-shaped, U-shaped), uses the bpypolyskel straight skeleton algorithm:

**Algorithm:**
1. Compute straight skeleton of footprint polygon
2. Convert skeleton to roof faces with proper ridge lines and valleys
3. Apply overhang by expanding footprint before skeletonization
4. Adjust eave Z based on computed roof pitch

**Eligibility for polyskel:**
- Vertex count: 5-12 (configurable via `POLYSKEL_MAX_VERTICES`)
- No holes in footprint
- House-scale dimensions
- Floor count ≤ 2
- Running in Blender (requires mathutils)

**Configuration (both algorithms):**
- Fixed height: 3.0m (same as gabled)
- Max floors: 2 (same as gabled)
- Selection: Via OSM tag `roof:shape=hipped` or `--random-hipped` flag

**Geometry (v0.3.6 fix):**

The roof is positioned so the slope plane passes through `wall_top_z` at the original footprint boundary. When there is overhang, the eave corners are BELOW `wall_top_z`:

```python
tan_pitch = roof_height / max_distance_to_ridge
eave_z = wall_top_z - tan_pitch * overhang  # Lower due to slope
```

This ensures the roof "sits" correctly on the walls with no visible gap.

---

## 9. Wall Generation

Walls are generated by extruding each footprint edge vertically from `floor_z` to `wall_top_z`.

```python
def generate_walls(building):
    for each edge (p0, p1) in outer_ring:
        # Create quad: bottom-left, bottom-right, top-right, top-left
        # CCW winding for outward-facing normal
        mesh.add_quad(bl, br, tr, tl)

    for each hole:
        for each edge in hole:
            # Reversed winding for inward-facing normal
            mesh.add_quad(...)
```

**Walls follow exact footprint** - no simplification or OBB approximation.

### Wall Architecture for Gabled Buildings (v0.2.4+)

For gabled buildings, walls are generated with special handling:

**Side walls** (parallel to ridge): Rectangular quads from floor_z to wall_top_z

**Gable end walls** (perpendicular to ridge):
- 1-floor buildings: Rectangle + separate triangle
- Multi-floor buildings: Pentagon (single face)

```python
def generate_walls_for_gabled(building, ridge_direction_deg, ridge_z, obb_center):
    for each edge in footprint:
        if edge perpendicular to ridge (dot < 0.3):
            # GABLE END
            if building.floors == 1:
                generate_separated_gable_wall()  # rect + triangle
            else:
                generate_pentagonal_gable_wall()  # pentagon
        else:
            # SIDE WALL
            generate_side_wall_with_uvs()  # rectangle
```

---

## 10. UV Mapping and Texture Atlas (Phase 2)

### Texture Atlas Layout

```
Atlas: 512 x 12288 pixels
U: [0..∞] horizontal (wraps for tiling)
V: [0..1] vertical (NO wrapping - stays within slice)

ROOF REGION (V in [0.75, 1.0]):
├── Pattern 0: V [0.9583, 1.0000]
├── Pattern 1: V [0.9167, 0.9583]
├── Pattern 2: V [0.8750, 0.9167]
├── Pattern 3: V [0.8333, 0.8750]
├── Pattern 4: V [0.7917, 0.8333]
└── Pattern 5: V [0.7500, 0.7917]

FACADE REGION (V in [0.0, 0.75]):
12 styles, each with 3 sections:
├── GABLE (no windows):     top of block
├── UPPER (windows only):   middle of block
└── GROUND (doors+windows): bottom of block

Each style occupies V range of 0.0625 (0.75 / 12)
Each section occupies V range of ~0.0208 (0.0625 / 3)
```

### UV Coordinate Convention

- **V = 1.0** at atlas TOP (pixel y = 0)
- **V = 0.0** at atlas BOTTOM (pixel y = 12288)
- Roofs at TOP of atlas (high V), Facades at BOTTOM (low V)

### Wall UV Mapping

**Scale (v0.3.4):** 3 meters = 0.33 U units (1/3 of texture width)

**U offset:** Walls start at U = 0.33 to skip door section:
```
U 0.00 → 0.33 = Door section (skipped by default)
U 0.33 → 0.66 = Window section
U 0.66 → 1.00 = Window section
```

**Rounding:** Wall width rounded UP to nearest 3m multiple using `ceil()`

```python
def compute_wall_u_range(wall_width_m: float) -> Tuple[float, float]:
    rounded_width = ceil(wall_width_m / 3.0) * 3.0
    u_span = (rounded_width / 3.0) * 0.3333
    u_start = 0.3333  # Skip door section
    u_end = u_start + u_span
    return u_start, u_end
```

### Multi-Floor Wall UV Mapping

For gabled buildings (max 2 floors), sidewalls use **continuous UV mapping**:
- Single quad spans entire wall height
- GPU interpolates UV coordinates linearly
- z=0-3m maps to ground section, z=3-6m maps to upper section

For flat-roof buildings (3+ floors), walls are split into per-floor quads.

### Roof UV Mapping

- V: ridge (v_max) → eave (v_min), full slice height
- U: `u_span = roof_length / roof_width` (preserves aspect ratio)
- Can wrap horizontally (U > 1.0)

### Variation Selection

Deterministic per-building using seed:

```python
rng = random.Random(building.seed)
roof_index = rng.randint(0, 5)    # 6 patterns
facade_index = rng.randint(0, 11) # 12 styles
```

Same seed always produces same variations across runs.

### OBJ Export Format (v0.3.0+)

```obj
v 0.000000 0.000000 100.000000
v 10.000000 0.000000 100.000000
vt 0.333333 0.687500
vt 3.666667 0.687500
f 1/1 2/2 3/3
```

---

## 11. Terrain Integration

### Terrain Mesh

The terrain is loaded from `h{patch_id}.obj`:
- Typically 73,728 triangles per patch
- 30m grid spacing

### Spatial Index

A grid-based spatial index accelerates terrain queries:

```python
class GridSpatialIndex:
    def __init__(self, triangles, cell_size=60.0):
        # Build grid of triangle references

    def query(self, bbox) -> List[TerrainTriangle]:
        # Return triangles overlapping bbox
```

### Floor Z Computation

The `FloorZSolver` determines ground level for each building:

```python
class FloorZSolver:
    def solve(self, footprint: Polygon) -> FloorZResult:
        # 1. Get terrain triangles under footprint
        # 2. Find minimum Z of footprint-triangle intersections
        # 3. Subtract epsilon (0.3m) to ensure building is below terrain
        return FloorZResult(floor_z=min_z - epsilon)
```

This ensures buildings don't "float" above terrain on slopes.

---

## 12. Output Format

### OBJ Files

Standard Wavefront OBJ format with UV coordinates (v0.3.0+):

```obj
# Condor Buildings Generator v0.3.4
# Patch: 036019
# LOD: 0
# Buildings: 5416

g building_way_123456789
v 100.123456 200.234567 50.345678
v 100.123456 210.234567 50.345678
...
vt 0.333333 0.687500
vt 3.666667 0.687500
...
f 1/1 2/2 3/3
f 1/1 3/3 4/4
...

g building_way_987654321
...
```

**Features:**
- Per-building groups (`g building_{osm_id}`)
- 6 decimal places for vertex precision
- CCW face winding
- Triangles, quads, and pentagons (n-gons)
- UV coordinates (`vt u v`) for texture mapping
- Face format: `f v/vt` with vertex and UV indices

### Output Files

| File | Description |
|------|-------------|
| `o{patch_id}_LOD0.obj` | Detailed mesh with 0.5m roof overhang and UV coordinates |
| `o{patch_id}_LOD1.obj` | Simplified mesh without overhang, with UV coordinates |
| `o{patch_id}_report.json` | Processing statistics including roof type distribution |
| `o{patch_id}.log` | Detailed processing log |

### Report JSON

```json
{
  "patch_id": "036019",
  "version": "0.3.4",
  "success": true,
  "stats": {
    "buildings_parsed": 5635,
    "buildings_filtered_edge": 219,
    "buildings_processed": 5416,
    "gabled_roofs": 1811,
    "hipped_roofs": 0,
    "flat_roofs": 3605,
    "gabled_fallbacks": 2532,
    "hipped_fallbacks": 0,
    "lod0_vertices": 333695,
    "lod0_faces": 181547,
    "lod0_uvs": 341000,
    "terrain_triangles": 73728,
    "processing_time_ms": 5500
  },
  "vertex_count_stats": {
    "4_vertices": 2529,
    "5_to_6_vertices": 1181,
    "7_to_8_vertices": 841,
    "9_plus_vertices": 865
  },
  "fallback_reasons": {
    "too_many_vertices": 1407,
    "too_many_floors": 1346,
    "too_short_side": 209,
    "bad_aspect_ratio": 36,
    "too_long_side": 18,
    "too_elongated": 6,
    "not_rectangle_angles": 2,
    "has_holes": 1,
    "not_rectangular_enough": 1,
    "too_large_area": 1
  },
  "config_used": {
    "gabled_max_vertices": 4,
    "gabled_max_floors": 2,
    "house_max_footprint_area": 300.0,
    "house_max_side_length": 25.0,
    "house_min_side_length": 4.0,
    "house_max_aspect_ratio": 4.0,
    "gable_height_fixed": 3.0,
    "roof_overhang_lod0": 0.5
  }
}
```

---

## 13. Configuration

### Constants (config.py)

#### Patch Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `PATCH_SIZE` | 5760.0 | Patch dimension in meters |
| `PATCH_HALF` | 2880.0 | Half-patch (origin to edge) |
| `DEFAULT_FLOOR_HEIGHT` | 3.0 | Meters per floor |
| `FLOOR_Z_EPSILON` | 0.3 | Floor offset below terrain |

#### Roof Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `GABLE_HEIGHT_FIXED` | 3.0 | Fixed gable triangle height (meters) |
| `HIPPED_HEIGHT_FIXED` | 3.0 | Fixed hipped roof height (meters) |
| `ROOF_OVERHANG_LOD0` | 0.5 | LOD0 overhang in meters |

#### Gabled/Hipped Eligibility

| Constant | Value | Description |
|----------|-------|-------------|
| `GABLED_MAX_VERTICES` | 4 | Only rectangles allowed |
| `GABLED_MAX_FLOORS` | 2 | Max floors for gabled roofs |
| `HIPPED_MAX_FLOORS` | 2 | Max floors for hipped roofs |
| `GABLED_REQUIRE_CONVEX` | True | Must be strictly convex |
| `GABLED_REQUIRE_NO_HOLES` | True | No inner rings allowed |
| `GABLED_MIN_RECTANGULARITY` | 0.70 | Area/OBB ratio threshold |
| `GABLED_ANGLE_TOLERANCE_DEG` | 25.0 | Tolerance from 90 degrees |

#### House-Scale Gate (v0.3.6 - increased 20%)

| Constant | Value | Description |
|----------|-------|-------------|
| `HOUSE_MAX_FOOTPRINT_AREA` | 360.0 | Max area for house (m²) |
| `HOUSE_MAX_SIDE_LENGTH` | 30.0 | Max side length (m) |
| `HOUSE_MIN_SIDE_LENGTH` | 3.2 | Min side length (m) |
| `HOUSE_MAX_ASPECT_RATIO` | 4.8 | Max aspect ratio |

#### Texture Atlas Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `ATLAS_WIDTH_PX` | 512 | Atlas width in pixels |
| `ATLAS_HEIGHT_PX` | 12288 | Atlas height in pixels |
| `ROOF_PATTERN_COUNT` | 6 | Number of roof patterns |
| `FACADE_STYLE_COUNT` | 12 | Number of facade styles |
| `ROOF_REGION_V_MIN` | 0.75 | Roof region V start |
| `ROOF_REGION_V_MAX` | 1.0 | Roof region V end |
| `FACADE_REGION_V_MIN` | 0.0 | Facade region V start |
| `FACADE_REGION_V_MAX` | 0.75 | Facade region V end |

#### Wall UV Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `WALL_BLOCK_METERS` | 3.0 | Each 3m block maps to WALL_BLOCK_U |
| `WALL_BLOCK_U` | 0.3333 | U width per 3m block |
| `WALL_U_OFFSET` | 0.3333 | Start U offset (skip door section) |
| `WALL_MIN_METERS` | 3.0 | Minimum wall length for UV mapping |

### Runtime Configuration

```python
@dataclass
class PipelineConfig:
    patch_id: str
    patch_dir: str
    zone_number: int
    translate_x: float
    translate_y: float
    global_seed: int = 12345
    output_dir: str = "./output"
    verbose: bool = False

    # Gabled eligibility overrides
    gabled_max_vertices: int = GABLED_MAX_VERTICES
    gabled_require_convex: bool = GABLED_REQUIRE_CONVEX
    gabled_require_no_holes: bool = GABLED_REQUIRE_NO_HOLES
    gabled_min_rectangularity: float = GABLED_MIN_RECTANGULARITY

    # House-scale overrides
    house_max_footprint_area: float = HOUSE_MAX_FOOTPRINT_AREA
    house_max_side_length: float = HOUSE_MAX_SIDE_LENGTH
    house_min_side_length: float = HOUSE_MIN_SIDE_LENGTH
    house_max_aspect_ratio: float = HOUSE_MAX_ASPECT_RATIO

    # Debug options
    debug_osm_id: Optional[str] = None  # Single-building debugging
    random_hipped: bool = False  # Random hipped roof assignment for testing

    # Roof selection mode (v0.3.5+)
    roof_selection_mode: RoofSelectionMode = RoofSelectionMode.GEOMETRY
```

---

## 14. Usage

### Command Line

```bash
python -m condor_buildings.main \
  --patch-dir ./CLT3 \
  --patch-id 036019 \
  --output-dir ./output \
  --verbose
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--patch-dir` | Yes | Directory containing h*.txt, h*.obj, map_*.osm |
| `--patch-id` | Yes | 6-digit patch ID (e.g., 036019) |
| `--output-dir` | No | Output directory (default: ./output) |
| `--zone` | No | UTM zone (default: from h*.txt) |
| `--translate-x` | No | X offset (default: from h*.txt) |
| `--translate-y` | No | Y offset (default: from h*.txt) |
| `--seed` | No | Global random seed (default: 42) |
| `--groups` | No | Include per-building groups in OBJ |
| `--verbose` | No | Enable debug logging |

#### Gabled Eligibility Overrides

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--gabled-max-vertices` | 4 | Max vertices for gabled (4 = rectangles only) |
| `--gabled-allow-non-convex` | False | Allow non-convex footprints |

#### House-Scale Overrides

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--house-max-area` | 300 | Max footprint area (m²) |
| `--house-max-side` | 25 | Max side length (m) |
| `--house-min-side` | 4 | Min side length (m) |
| `--house-max-aspect` | 4 | Max aspect ratio |

#### Roof Selection Mode (v0.3.5+)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--roof-selection-mode` | `geometry` | `geometry` or `osm_tags_only` |

- `geometry`: Use geometry + category heuristics (default, backward compatible)
- `osm_tags_only`: Only buildings tagged as houses get pitched roofs

#### Debug Options

| Parameter | Description |
|-----------|-------------|
| `--debug-osm-id <id>` | Process only a single building by OSM ID |
| `--random-hipped` | Randomly assign hipped to 50% of eligible buildings (for visual testing) |

### Input Files

The pipeline expects these files in `--patch-dir`:

| File | Description |
|------|-------------|
| `h{patch_id}.txt` | Patch metadata (zone, translation) |
| `h{patch_id}.obj` | Terrain mesh |
| `map_*.osm` | OSM data (auto-discovered) |

### Example

```bash
# Process Slovenia patch 036019
python -m condor_buildings.main \
  --patch-dir C:\Condor3\Landscapes\Slovenia\CLT3 \
  --patch-id 036019 \
  --output-dir C:\Condor3\Output \
  --verbose
```

---

## 15. Test Results

### Patch 036019 (Slovenia) - v0.3.6 (with --random-hipped)

| Metric | Value |
|--------|-------|
| Buildings parsed | 5,635 |
| Buildings filtered | 219 (edge proximity) |
| Buildings processed | 5,416 |
| Gabled roofs | 1,065 (19.7%) |
| Hipped roofs | 900 (16.6%) |
| Flat roofs | 3,451 (63.7%) |
| Total pitched roofs | 1,965 (36.3%) |
| Gabled fallbacks | 2,754 |
| Hipped fallbacks | 154 |
| LOD0 vertices | 338,663 |
| LOD0 faces | 189,113 |
| Terrain triangles | 73,728 |
| Processing time | ~5.6 seconds |

### Footprint Vertex Distribution

| Vertices | Count | Percentage |
|----------|-------|------------|
| 4 (rectangles) | 2,529 | 46.7% |
| 5-6 | 1,181 | 21.8% |
| 7-8 | 841 | 15.5% |
| 9+ | 865 | 16.0% |

### Fallback Reasons (v0.3.6)

| Reason | Count |
|--------|-------|
| `too_many_vertices` | 1,434 |
| `too_many_floors` | 1,346 |
| `too_short_side` | 77 |
| `bad_aspect_ratio` | 36 |
| `too_long_side` | 6 |
| `too_elongated` | 3 |
| `not_rectangle_angles` | 2 |
| `too_large_area` | 2 |
| `has_holes` | 1 |
| `not_rectangular_enough` | 1 |

### Notes on Fallback Distribution

The significant increase in flat roofs (from 11.9% in v0.1.0 to 66.6% in v0.3.4) is due to:

1. **Stricter geometry gate**: Only 4-vertex rectangles allowed (was ≤20 vertices)
2. **Floor limit**: Buildings with 3+ floors now fall back to flat
3. **House-scale gate**: Large buildings (apartments, industrial) now correctly get flat roofs

---

## 16. Pending Work

### Completed (v0.3.4)

- [x] UV Mapping Implementation (Phase 2)
- [x] Texture Atlas Support (6 roof patterns, 12 facade styles)
- [x] Hipped Roof Support (BLOSM analytical solution)
- [x] Fixed Gable Height (3.0m constant)
- [x] Pentagonal Gable Walls Architecture
- [x] Floor Limit for Gabled/Hipped Roofs (max 2 floors)
- [x] House-Scale Gate for Roof Type Selection
- [x] UV V Coordinate Inversion Fix
- [x] Multi-Floor Wall UV Mapping
- [x] Wall UV Scale Update (3m blocks, door offset)

### High Priority

#### 1. MTL File Generation

Generate MTL file with texture references for Condor:
- Material definitions for walls and roofs
- Texture file references

#### 2. Texture Atlas Creation

Create actual texture image files:
- 6 roof patterns (tiles, shingles, etc.)
- 12 facade styles (3 sections each)

#### 3. Floor Z Extension

Extend building base below terrain level to prevent visual gaps on sloped terrain. Current epsilon (0.3m) may be insufficient for steep slopes.

### Medium Priority

#### 4. Geometry Optimization

- ~~Vertex deduplication~~ ✓ (v0.6.2 - automatic, ~63% reduction)
- Mesh simplification for LOD1
- Normal generation for smooth shading

#### 5. LOD1 Simplification

Currently LOD1 differs only by lack of overhang. Consider:
- Reduced vertex count
- Simplified geometry for distant viewing

### Low Priority

#### 6. Special Building Types

- Churches with steeples
- Hangars with curved roofs
- Towers

#### 7. Performance Optimization

- Parallel processing for large patches
- Incremental processing (only changed buildings)

---

## Appendix A: BLOSM Roof Semantics

The roof direction semantics follow BLOSM conventions:

- `roof:direction` in OSM = **slope direction** (perpendicular to ridge)
- Longest edge of footprint = **ridge direction** (ridge runs parallel to long edge)
- Span (eave-to-eave distance) = short dimension of building

**Important (v0.2.1 fix):** The ridge runs **parallel** to the longest edge, not perpendicular. This ensures the roof span is the short dimension, producing correctly proportioned roofs.

## Appendix B: Coordinate Transform Chain

```
WGS84 (lat, lon)
       │
       ▼ UTM Projection
UTM (easting, northing)
       │
       ▼ + TranslateX, TranslateY
Condor Local (x, y)
       │
       ▼ + floor_z from terrain
Condor 3D (x, y, z)
```

## Appendix C: References

- OpenStreetMap Wiki: [Key:roof:direction](https://wiki.openstreetmap.org/wiki/Key:roof:direction)
- BLOSM Wiki: [Profiled roofs](https://github.com/vvoovv/blosm/wiki/Profiled-roofs)
- Condor 3 Landscape Documentation (internal)

---

## Appendix D: Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| 0.1.0 | Jan 2026 | Initial release with basic gabled/flat roof support |
| 0.2.0 | Jan 11, 2026 | Robust gabled roofs - restricted to 4-vertex rectangles, explicit fallback reasons, UV groundwork |
| 0.2.1 | Jan 11, 2026 | Ridge direction fix (parallel to longest edge), house-scale size gate |
| 0.2.2 | Jan 12, 2026 | Gable wall connection fix, roof underside faces, enhanced debug logging |
| 0.2.3 | Jan 13, 2026 | Geometry v3 - visible overhang, gable end caps, double-sided roof faces |
| 0.2.4 | Jan 16, 2026 | Geometry v4 - pentagonal gable walls, independent roof body |
| 0.2.5 | Jan 17, 2026 | Phase 1 complete - fixed 3.0m gable height, separated gable for 1-floor |
| 0.3.0 | Jan 18, 2026 | Phase 2 - UV mapping + texture atlas (6 roof patterns, 12 facade styles) |
| 0.3.1 | Jan 19, 2026 | UV V coordinate inversion fix (V=1.0 at atlas top) |
| 0.3.2 | Jan 20, 2026 | Side wall UV multi-floor fix (per-floor quads) |
| 0.3.3 | Jan 20, 2026 | Sidewall UV no-split (continuous UV), gabled floor limit (max 2) |
| 0.3.4 | Jan 21, 2026 | Wall UV 3m blocks + door offset, hipped roofs implementation |
| 0.3.5 | Jan 24, 2026 | Roof selection mode (`geometry` / `osm_tags_only`), CLAUDE.md quick reference |
| 0.3.6 | Jan 24, 2026 | Hipped roof Z positioning fix (no more floating), house-scale thresholds +20% |
| 0.3.7 | Jan 25, 2026 | Hipped roof walls use continuous quads (no floor splits) |
| 0.4.0 | Jan 27, 2026 | Blender addon integration - import buildings directly into Blender |
| 0.5.0 | Jan 27, 2026 | Condor workflow support - auto-detect landscapes, download OSM from Overpass, batch patch processing |
| 0.6.0 | Jan 29, 2026 | Polyskel integration - hipped roofs for 5-12 vertex buildings using bpypolyskel straight skeleton |
| 0.6.1 | Jan 30, 2026 | Configurable parameters - expose all key parameters in Blender UI (gable height, overhang, floor Z, max floors, rectangularity, polyskel vertices, seed) |
| 0.6.2 | Jan 30, 2026 | Vertex optimization - automatic deduplication reduces mesh size by ~63% |
| 0.6.3 | Jan 31, 2026 | Texture-based mesh grouping (10 objects by texture type) and hipped roof UV mapping fix for non-square footprints |
| 0.6.8 | Feb 10, 2026 | Correct polyskel roof UV mapping - consistent tile size and aspect ratio across all faces using orthographic planar projection with unified global Z scaling |
| 0.7.0 | Feb 11, 2026 | Highrise wall system - separate Highrise_atlas.dds (2048x12288) for apartment/commercial walls, multi-floor quads, merged Highrise_walls OBJ object |
| 0.7.1 | Feb 12, 2026 | Fix HOUSE flat-roof grouping - buildings with flat roof fallback now route to Highrise_walls instead of houses |
| 0.7.2 | Feb 14, 2026 | Fix HOUSE height estimation (always 2 floors unless OSM tagged), floor guard in select_roof_type(), hipped roof near-square stability |
| 0.7.3 | Mar 12, 2026 | Fix UV mapping: sync floors/height_m when building:levels overrides estimate, round() for height-based floor estimation |

### Changelog Files

Detailed changelogs are available in the `docs/` directory.

---

## License

This project is licensed under the **GNU General Public License v3.0** (GPL-3.0) - see the [LICENSE](LICENSE) file for details.

**Note:** As of v0.6.0, the project includes the bpypolyskel library which is licensed under GPL v3. This requires the entire project to be distributed under GPL v3.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Acknowledgments

- OpenStreetMap contributors for building data
- BLOSM project for roof generation algorithms
- [bpypolyskel](https://github.com/prochitecture/bpypolyskel) for straight skeleton algorithm
- Condor Soaring community

---

## Team

This project was developed by:

- **Wiek Schoenmakers** - Technical Lead & Condor Specialist. Provided requirements, domain expertise on Condor flight simulator, and guidance on scenery building.

- **Juan Luis Gabriel** - Project Manager & Orchestrator. Coordinated requirements gathering, communication between team members, and project direction.

- **Anthropic Claude Opus 4.5** (via Claude Code) - Software Development. Designed the solution architecture and implemented the foundational codebase for this project.

- **Anthropic Claude Opus 4.6** (via Claude Code) - Software Development. Continued development from v0.6.3 onwards, including texture grouping, polyskel UV mapping, and ongoing improvements.
