# Condor Buildings Generator

[![Version](https://img.shields.io/badge/version-0.9.0-blue.svg)](https://github.com/yourusername/condor-buildings-generator)
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

1. Download `condor_buildings_v0.9.0.zip` from releases
2. In Blender: Edit > Preferences > Add-ons > Install
3. Select the ZIP file
4. Enable "Condor Buildings Generator" addon
5. Open the sidebar in 3D View (press N)
6. Navigate to the "Condor" tab
7. Set your Condor installation path (e.g., `C:\Condor3`)
8. Select a landscape from the dropdown
9. Set patch range (X/Y min/max) or enable single patch mode
10. Click "Generate Buildings"

**New in v0.9.0 — milestone release:**
- **Stable build of the OSM → Condor building generator.** Consolidates the v0.8.x stabilization series into a single milestone: courtyard roofs open as holes, gable-end walls face outward, degenerate geometry is cleaned out of both the Blender mesh and the exported c3d, UV mapping is applied correctly, materials self-heal when their texture appears, and low-voltage power lines are filtered. The generator produces textured houses, apartment/commercial highrise, industrial, and flat-roofed buildings (gabled / hipped / polyskel / flat roofs) with Condor-ready OBJ + MTL output for LOD0 and LOD1. See the **Project Status** section below for the full capability list.

**New in v0.8.14:**
- **Courtyard roofs now open up — Luboš Faitz's report**: buildings with an inner courtyard (OSM multipolygon: outer ring + inner ring) got a solid flat roof covering the courtyard. `utils/triangulation.py :: triangulate_with_holes()` was rewritten as a three-strategy orchestrator — Blender's native `mathutils.geometry.tessellate_polygon` (production path, robust on the non-convex L/U-shaped city blocks where the old bridge-and-earclip seam self-intersected and fell back to a solid roof), then `mapbox_earcut` (optional, for CLI/tests), then the legacy bridge method as last resort. Roof triangles are now forced CCW-from-above (+Z up) in `generators/roof_flat.py` since the tessellators don't guarantee winding. Validated: square + U-shaped courtyards triangulate as openings (8/8 CLI checks); the legacy method provably fails on the U-shape.
- **Fixed v0.8.13 UV regression — Luboš Faitz's report**: v0.8.13's safety-net `mesh.validate()` ran *before* the UV mapping in `blender/mesh_converter.py`; since `validate()` can delete a degenerate face and renumber polygons, UVs were then applied to the wrong faces. `_add_uv_layer()` now runs **before** `mesh.validate()` (UVs assigned while indices still match; validate then drops a bad face with its own UV loops). Also added `_recalc_normals_outside()` so imported objects render correctly without a manual Shift+N — flat-roof sheets are forced +Z (up), closed building volumes get `recalc_face_normals` outward (a no-op on already-correct winding, so the v0.8.10 gable fix is preserved). Validated in Blender 5.x via MCP (9/9 checks, including reproducing the old UV corruption and confirming the new order fixes it).

**New in v0.8.13:**
- **Fixed degenerate house geometry that froze Blender's Edit Mode — Luboš Faitz's report**: on certain patches the merged "houses" object hung Blender (forcing a restart) when switched from Object to Edit Mode. A mesh check found collapsed edges (zero-length, start == end vertex) and corrupted faces with duplicate vertices. Root cause: `MeshData.optimize()` merges vertices within 0.1 mm during deduplication but never re-checked the remapped faces, so a face that referenced two now-merged vertices ended up referencing one index twice — a collapsed edge / corrupted face. Fix (3 layers): **(A)** `optimize()` now drops duplicate corner indices and discards any face left with fewer than 3 unique vertices, keeping `face_uvs` parallel; **(B)** `MeshData.validate()` now detects duplicate-index faces and face_uv desync; **(C)** the Blender converter calls `mesh.validate()` as a safety net before display. This cleans both the Blender mesh *and* the exported Condor OBJ/c3d. Verified: 5/5 synthetic checks; real patch 036019 had 16 degenerate faces auto-removed; exported OBJs across 036019 + Andy's 003023 are 100% free of duplicate-index faces (331k+ faces). A new `degenerate_faces_removed` stat is logged and added to the report.

**New in v0.8.12:**
- **Very-low-voltage powerlines (<1 kV) are no longer drawn — Andy's "powerline along a road, probably underground" report**: in `io/powerline_parser.py`, lines whose known `voltage` is below `MIN_DRAWN_VOLTAGE_KV` (1 kV) are now skipped. A 230 V / 400 V service drop carries only ~6 m poles that are invisible from a glider, and in the UK such feeders very often run **underground along the road** — OSM rarely tags them `location=underground`, so we filter by voltage instead. Only KNOWN low voltages are dropped; untagged lines still pass through the name/line-type classifier. On Andy's patch 003024 this removes the five `voltage=230` `minor_line` ways (39 → 34 lines, 352 → 314 placed towers); the 11 / 33 / 132 kV lines are untouched. Powerlines remain opt-in/beta. (Separately, Andy's "main scenery dds texture not loading" is the `T_` orthophoto-prefix not being rewritten by Uros's latest Landscape Editor — an LE-side item, not a change here.)

**New in v0.8.11:**
- **Fixed low-voltage powerlines rendered as giant transmission towers — Andy's "pylons too close together" report**: in `io/powerline_parser.py`, `_parse_voltage_kv()` used a heuristic ("bare values below 1000 are already kV") that misread `voltage=230` — 230 **volts**, a low-voltage distribution feeder — as 230 **kV**, so the line was classified `major → Pylon_Large` and got ~38 m transmission towers stamped on every node, 10–50 m apart: a wall of giant pylons in the middle of patch 003024. OSM stores `voltage` in volts ([Key:voltage](https://wiki.openstreetmap.org/wiki/Key:voltage)), so the parser now always divides by 1000 (honouring an explicit `kV` suffix). On Andy's patch 003024 this drops large pylons from **42 → 4** — only the real 132 kV Richborough–Folkestone line stays large — and the five 230 V `minor_line` ways become small distribution poles. Powerlines remain opt-in/beta (off by default), awaiting Wiek's in-sim sign-off.

**New in v0.8.10:**
- **Fixed gable-end walls rendering inward (back-face culled) — Andy's "missing walls" report**: the triangular gable-end walls of pitched houses were wound with their normal pointing **inward** on a large fraction of buildings, so Condor back-face-culled them and the house looked like it was missing a wall — only obvious up close, viewed end-on. Root cause in `generators/walls.py`: the outward-normal test projected `(gable midpoint − OBB center)` onto `perp = (-ridge_dy, ridge_dx)` (the gable **edge** axis) instead of the ridge-aligned **normal** axis, so for centred/symmetric buildings both gable ends collapsed to ~0 and flipped inward. Fix: gable walls now use the CCW outer-ring winding `[v0,v1,v2,v3,v4]` (normal `(edge_dy, -edge_dx)`) — the same convention the side walls use; the OSM parser already guarantees CCW outer rings. Validated on Andy's patch 004001 (16,231 buildings): inward-facing gable wall faces went from **11,602 → 0** across 8,373 gabled houses; side/flat/hipped/highrise walls were already correct and are unchanged. Confirmed visually in Blender (back-face culling): the buggy gable is a see-through hole, the fixed one is solid.

**New in v0.8.9:**
- **LOD filename convention reverted (Wiek, after talking to Uros)**: the current Landscape Editor expects `o<patch>.obj` for LOD0 (no suffix) and `o<patch>_LOD1.obj` for LOD1, each with its matching `.mtl`. The Condor export *and* the CLI now write that naming again, reverting the explicit `_LOD0` suffix introduced in v0.8.8 (re-aligns with the v0.8.5 scheme). The bare LOD0 name keeps backwards compatibility with the LE in use today.
- **Materials self-heal when their texture appears later (Luboš Faitz)**: the addon reuses each `condor_<group>` material by name across runs. Before, if a material was first created while its `.dds` was missing from `Working\Autogen\Textures\`, it stayed "white" (no Image Texture node) and every later regeneration reused it untextured — the only fix was to delete the material by hand. Now, when reusing a material that has no image, the addon re-checks the Textures folder and attaches the Image Texture node automatically if the `.dds` is present. No manual deleting, no manual node wiring. (A plain "Clear Buildings" never fixed this on its own — the material survives as orphan data and is reused.)

**New in v0.8.8:**
- **New Landscape Editor LOD naming (Wiek/Uros)**: Uros's new LE merges `o######_LOD0.obj` + `o######_LOD1.obj` (each with its matching `.mtl`) into a single `o######.c3d` with the LOD fields filled in. The Condor export now writes **both** LODs with an explicit suffix (`o<patch>_LOD0.{obj,mtl}` and `o<patch>_LOD1.{obj,mtl}`) so the LE can pair them — no more manual renaming. Reverts the v0.8.5 no-suffix LOD0 name that the old LE required.
- **`T_` prefix for the flat-roof orthophoto (Wiek)**: the new LE rewrites a `T_` texture prefix to the landscape ground-texture path (`Landscapes\<name>\Textures\`) on c3d conversion. When "Terrain photo on flat roofs" is on, the merged `flat_roof` texture is emitted as `T_t<patch>.dds` in the Condor MTL only (the Blender preview keeps `t<patch>.dds`). All tiled atlases and `Pylons.dds` stay bare — the LE resolves them from `Autogen\Textures` automatically.
- **OSM height clamp documented (Andy's "giant building")**: a single OSM typo (e.g. `building:levels="233"` on a house) otherwise produces an absurd ~700 m skyscraper. `MAX_BUILDING_LEVELS = 60` / `MAX_BUILDING_HEIGHT_M = 200` clamp clearly-bad values and emit a WARNING with the OSM id/address (caps are generous — The Shard is 72 floors / 310 m — so real buildings are never clipped). Built/validated previously, released here.

**New in v0.8.7:**
- **Terrain photo on flat roofs is now optional (Wiek/Chris/Uros feedback)**: the aerial-photo texture on flat roofs is decoupled from merging. "Merge Flat Roofs" now only merges the flat-roof geometry into a single object; a separate **"Terrain photo on flat roofs"** checkbox (off by default) applies the patch orthophoto. With the photo off, merged flat roofs use the roof atlas (`Roof1.dds`) with building-aligned UVs. Enabling the photo implies merging.
- **Condor MTL texture paths reverted to bare filenames (Andy feedback)**: the Condor Landscape Editor adds a `Texture/` folder itself when converting the object to a c3d, so a `Textures/` prefix in our output produced a doubled `Texture/Textures/<file>.dds` that wouldn't load in the Condor sim. The MTL now references every texture by **bare filename** (`map_Kd t<patch>.dds`, `map_Kd <Atlas>.dds`) — `CONDOR_TEXTURE_PREFIX = ""`, matching the pre-v0.8.5 output. Uniform across the orthophoto and the wall atlases.

**New in v0.8.6:**
- **Terrain photo on flat roofs (Michel's trick, requested via Andy)**: when "Merge Flat Roofs" is on, all flat roofs merge into one `flat_roof` object textured with the **patch orthophoto** `t<patch>.dds` (the same aerial image Condor uses for the terrain, from the landscape `Textures` folder). The roof UVs are normalized to the patch (`u=(X+2880)/5760`, `v=(Y+2880)/5760`), so each roof samples the exact pixel of the photo it sits on and **blends with the terrain from the air**. The Condor MTL references it as `map_Kd Textures/t<patch>.dds`; the Blender preview also loads it (searches the landscape `Textures` folder). Validated end-to-end against Andy's real patch 004001: UVs land 1:1 on the orthophoto and roofs are seamless from above. A V-flip toggle (`FLAT_ROOF_ORTHOPHOTO_V_FLIP`, default off) is provided in case Condor samples V the other way.

**New in v0.8.5:**
- **Andy/Condor beta feedback on the export**: (1) the detailed object is now written as `o<patch>.obj` (no `_LOD0` suffix) as Condor scenery processing expects; (2) the `.mtl` references textures as `map_Kd Textures/<file>.dds` (Condor keeps `.dds` in a `Textures` subfolder) — no more manual path editing; (3) "Export Condor OBJ+MTL" now also imports the meshes into the Blender viewport when "Import to Blender" is on, so single-patch work shows the result. Validated in Condor by the beta team (materials confirmed: Spec 0, Shiny 0, RGB + alpha 1).

**New in v0.8.4:**
- **Condor-ready OBJ + MTL export**: New "Export Condor OBJ+MTL" button in the Blender plugin writes files the Condor Landscape Editor accepts with **no manual tweaking**. The output is triangulated, the axis transform is baked in (matching Blender's "Forward: X, Up: Z" export, measured empirically as `(x,y,z)→(y,−x,z)`), and a matching `.mtl` is generated with the Condor material values (`Kd 1 1 1`, `Ks 0 0 0`, `Ns 0`, `d 1`, `map_Kd <Atlas>.dds`) plus `mtllib`/`usemtl` wiring. Eliminates the manual Blender export + MTL fix-up the Condor beta team had to do on every patch. Files are saved to `Working/Autogen` for the selected LOD(s).

**New in v0.8.3:**
- **Deterministic output**: Building seed now uses `hashlib.sha256` instead of Python's `hash()`, ensuring identical results across processes and machines.
- **floor_z_epsilon now works**: The configurable floor Z offset is now properly passed from config/UI to the solver (was previously hardcoded to 0.3).
- **CLI config passthrough**: All generator parameters (gable_height, max_floors, house constraints, etc.) now properly flow from `PipelineConfig` to the generator in CLI mode.
- **Fixed roof direction stats**: Report statistics for `roof_direction_source` now correctly match enum values (was silently undercounting).
- **Fixed duplicate `is_empty()`**: Removed second `MeshData.is_empty()` definition that shadowed the correct one.
- **Fixed MeshGrouper allocation**: Flat roof array size now matches actual group count in merge mode.

**New in v0.8.0:**
- **Automatic Blender materials**: Each imported object now receives a Principled BSDF material with the correct .dds texture loaded as Image Texture on Base Color. Textures are loaded from `Working/Autogen/Textures/` in the Condor landscape folder. Materials are reused across patches to avoid duplicates. If a texture file is not found, the material is created without an image (pink in viewport, user assigns manually).

**New in v0.7.6:**
- **Flat roof merge option**: New `--flat-roof-merge` CLI flag and "Merge Flat Roofs" Blender checkbox. When enabled, all flat roofs are merged into a single `flat_roof` object with global UV projection (world coordinates as UVs). Useful for mapping terrain texture onto flat roofs. When disabled, v0.7.5 behavior is unchanged.

**New in v0.7.5:**
- **Flat roof UV alignment**: Flat roof UVs are now rotated to align with each building's longest edge instead of using global world X/Y projection. UVs are centered on the building centroid. This breaks visual uniformity across differently-oriented buildings while keeping the same 1m = 1 UV scale.

**New in v0.7.4:**
- **Flat roof UV mapping**: Flat roofs (1-6) now use planar projection where 1 meter in world space = UV 0-1. Textures tile in all directions at 1m intervals. Previously used atlas-based UV mapping with only X-axis tiling.

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
| `o{patch_id}.obj` | Detailed mesh, LOD0 (0.5m roof overhang) |
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
- `o{patch_id}.obj` - Detailed mesh (LOD0)
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
| `o{patch_id}.obj` | Detailed mesh, LOD0 (0.5m roof overhang and UV coordinates) |
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
| `--flat-roof-merge` | Merge all flat roofs into single object with global UV projection (for terrain texture) |

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

## 16. Project Status

v0.9.0 is a stable release of the OSM → Condor building generator. From an OpenStreetMap extract and a Condor terrain patch it produces textured 3D building meshes (LOD0 + LOD1) with Condor-ready OBJ + MTL output, usable from the command line or as a Blender addon.

**What it generates**

- Building footprints from OSM ways and multipolygons — courtyards open as holes
- Roof types: gabled, hipped (BLOSM analytical), hipped via straight skeleton (polyskel, 5–12 vertices), and flat
- Wall meshes with per-floor UV mapping against the facade atlases
- Texture-based object grouping (houses, highrise/commercial walls, roofs, flat roofs, industrial) for efficient Condor rendering
- Optional terrain orthophoto on merged flat roofs
- Optional power-line towers (off by default)

**Output**

- `o<patch>.obj` (LOD0) and `o<patch>_LOD1.obj`, each with a matching `.mtl`, triangulated and axis-corrected for the Condor Landscape Editor
- A processing report (`o<patch>_report.json`) and a detailed log

**Validated**

- Processed end-to-end on real Condor scenery patches via both the CLI and the Blender addon
- Mesh geometry verified in Blender (correct winding, no degenerate faces, aligned UVs, outward normals); Condor materials confirmed by the beta team (Spec 0, Shiny 0, RGB + alpha 1)

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
| 0.7.4 | Mar 13, 2026 | Flat roof UV mapping: planar projection where 1m world space = UV 0-1, textures tile in all directions |
| 0.7.5 | Mar 16, 2026 | Flat roof UV alignment: UVs rotated to building's longest edge, centered on centroid |
| 0.7.6 | Mar 16, 2026 | Flat roof merge option: `--flat-roof-merge` flag merges all flat roofs into single object with global UV projection (for terrain texture) |
| 0.8.0 | Mar 17, 2026 | Blender material assignment: auto-creates Principled BSDF + Image Texture materials per object, textures from Working/Autogen/Textures/ |
| 0.8.1 | Mar 17, 2026 | Fix texture path: folder name was "Texture" (singular), Condor uses "Textures" (plural) |
| 0.8.2 | Apr 6, 2026 | Texture diagnostics: [Condor] console logging, case-insensitive filename fallback, texture status in info bar |
| 0.8.3 | Apr 9, 2026 | Internal consistency fixes: deterministic seeds (hashlib), floor_z_epsilon passthrough, CLI config sync, stats fix, MeshData/MeshGrouper cleanup |
| 0.8.4 | May 29, 2026 | Condor-ready OBJ+MTL export: new "Export Condor OBJ+MTL" button writes triangulated, axis-corrected (Forward X / Up Z) OBJ + matching .mtl (Spec 0, Shiny 0, RGB+alpha 1, map_Kd) to Working/Autogen — no manual Blender tweaking needed |
| 0.8.5 | May 30, 2026 | Condor beta feedback: object named `o<patch>.obj` (no LOD0 suffix), `map_Kd Textures/<file>.dds` path prefix, and Export also imports to Blender viewport for single-patch preview |
| 0.8.6 | Jun 1, 2026 | Terrain orthophoto on merged flat roofs (Michel's trick): `flat_roof` textured with patch `t<patch>.dds`, patch-normalized UVs so roofs blend with the aerial photo from the air; validated against Andy's real patch 004001 |
| 0.8.7 | Jun 2, 2026 | Terrain photo on flat roofs made optional (separate checkbox, off by default — Wiek/Chris/Uros); decoupled from geometry merge. Condor MTL texture paths reverted to bare filenames (`CONDOR_TEXTURE_PREFIX=""`) — the Landscape Editor adds the `Texture/` folder, so a prefix doubled it and broke sim loading (Andy) |
| 0.8.8 | Jun 8, 2026 | New LE (Uros) conventions: Condor export writes both LODs with explicit suffix (`o<patch>_LOD0`/`_LOD1` obj+mtl) so the LE pairs them into one c3d (reverts v0.8.5 no-suffix LOD0); `T_` prefix on the flat-roof orthophoto in the Condor MTL so the LE routes it to the landscape Textures folder. OSM height clamp documented (`MAX_BUILDING_LEVELS=60`/`MAX_BUILDING_HEIGHT_M=200`). Powerlines bundled but opt-in/beta, awaiting Wiek's sim sign-off |
| 0.8.9 | Jun 9, 2026 | LOD filename convention reverted (Wiek/Uros): LOD0 is `o<patch>.obj` (no suffix), LOD1 is `o<patch>_LOD1.obj` for current-LE backwards compatibility (re-reverts the v0.8.8 `_LOD0` suffix; applies to both the Condor export and the CLI). Materials self-heal: reusing a textureless `condor_*` material now attaches the Image Texture node when the `.dds` is present in the Textures folder, so adding a texture after a first generation no longer needs a manual material delete (Luboš Faitz) |
| 0.8.10 | Jun 9, 2026 | Fixed gable-end walls facing inward (back-face culled → "missing walls" up close; Andy's UK3 report). The gable winding in `generators/walls.py` projected onto the gable edge axis instead of the ridge-aligned normal axis, flipping ~half of gable ends inward; now uses the CCW side-wall winding convention. Validated on patch 004001: inward gable wall faces 11,602 → 0 across 8,373 gabled houses; side/flat/hipped/highrise walls unchanged. Confirmed in Blender via back-face culling |
| 0.8.11 | Jun 10, 2026 | Fixed low-voltage powerlines drawn as giant transmission towers (Andy's "pylons too close together", patch 003024). `_parse_voltage_kv` in `io/powerline_parser.py` misread `voltage=230` (volts) as 230 kV → `Pylon_Large` towers every 10–50 m; OSM voltage is always in volts, so the parser now divides by 1000 (explicit `kV` suffix honoured). Large pylons on patch 003024: 42 → 4 (only the real 132 kV line stays large); five 230 V `minor_line` feeders become small poles. Powerlines still opt-in/beta |
| 0.8.12 | Jun 10, 2026 | Skip very-low-voltage powerlines (<1 kV) — Andy's "powerline along a road, probably underground" (patch 003024). `io/powerline_parser.py` now drops lines whose known `voltage` is below `MIN_DRAWN_VOLTAGE_KV` (1 kV): a 230 V service drop is invisible from the air and usually runs underground along roads, and OSM rarely tags `location=underground`. Untagged lines still pass through the classifier. On 003024: five `voltage=230` feeders dropped (39 → 34 lines, 352 → 314 towers); 11/33/132 kV untouched. Powerlines still opt-in/beta |
| 0.8.13 | Jun 15, 2026 | Fixed degenerate house geometry that froze Blender's Edit Mode (Luboš Faitz). Vertex dedup in `MeshData.optimize()` merged sub-0.1 mm vertices but left faces referencing the same index twice → collapsed edges / corrupted faces. Now `optimize()` drops duplicate corners and < 3-vertex faces (keeping `face_uvs` parallel), `validate()` detects them, and the Blender converter calls `mesh.validate()` as a safety net. Cleans the Blender mesh *and* the exported OBJ/c3d. Patch 036019 had 16 such faces auto-removed; exported OBJs verified 0 duplicate-index faces across 036019 + 003023 (331k+ faces) |
| 0.8.14 | Jun 16, 2026 | Two Luboš Faitz fixes. **(1) Courtyard roofs**: buildings with an inner courtyard got a solid roof; `triangulate_with_holes()` rewritten as a 3-strategy orchestrator (Blender `tessellate_polygon` → `mapbox_earcut` → legacy bridge), so non-convex city blocks open up instead of falling back to a solid roof; roof triangles forced +Z-up in `roof_flat.py`. **(2) UV regression from v0.8.13**: the safety-net `mesh.validate()` ran before UV mapping and (when it removed a degenerate face) mis-assigned UVs; `mesh_converter.py` now maps UVs *before* validate, and adds `_recalc_normals_outside()` (flat sheets → +Z, closed volumes → outward, idempotent so it keeps the v0.8.10 gable winding). Validated: 8/8 CLI triangulation checks + 9/9 Blender 5.x MCP checks |
| 0.9.0 | Jun 16, 2026 | **Milestone release** — stable build of the OSM → Condor building generator. Consolidates the v0.8.x stabilization series (courtyard roofs open as holes, outward-facing gable walls, degenerate-geometry cleanup, correct UV mapping, material self-heal, low-voltage power-line filtering). Generates textured houses, highrise/commercial, industrial and flat-roof buildings (gabled / hipped / polyskel / flat) with Condor-ready OBJ + MTL for LOD0 + LOD1, from the CLI or the Blender addon |

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

- **Andy Souter** - Condor Scenery Designer & Beta Tester. Validated the export pipeline in real Condor sceneries and the Landscape Editor, and provided detailed feedback on c3d texture paths, empty-patch handling, vertex-count limits, and OSM building fidelity.

- **Luboš Faitz** - Developer & Beta Tester. Reported and helped diagnose several core-generator fixes — courtyard roofs, degenerate geometry that froze Blender's Edit Mode, UV mapping, and material self-heal — contributing fix notes for each.

- **Anthropic Claude Opus 4.5** (via Claude Code) - Software Development. Designed the solution architecture and implemented the foundational codebase for this project.

- **Anthropic Claude Opus 4.6** (via Claude Code) - Software Development. Continued development from v0.6.3 onwards, including texture grouping, polyskel UV mapping, and ongoing improvements.

- **Anthropic Claude Opus 4.8** (via Claude Code) - Software Development. Continued development from v0.8.7 onwards, including the optional flat-roof terrain photo and Condor c3d texture-path handling.
