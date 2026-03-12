# Condor Buildings Generator - Quick Reference for Claude

## Project Overview

This is a Python pipeline that generates 3D building meshes from OpenStreetMap (OSM) data for Condor 3 flight simulator. It produces OBJ files with UV coordinates for texture mapping.

**Available as:** CLI tool + Blender addon (v0.5.0+)

## How to Run the Pipeline

```bash
# Basic run
python -m condor_buildings.main --patch-dir test_data --patch-id 036019 --output-dir output

# With verbose logging
python -m condor_buildings.main --patch-dir test_data --patch-id 036019 --output-dir output --verbose

# OSM tags only mode (only tagged houses get pitched roofs)
python -m condor_buildings.main --patch-dir test_data --patch-id 036019 --output-dir output --roof-selection-mode osm_tags_only

# With random hipped roofs (50% gabled, 50% hipped for testing)
python -m condor_buildings.main --patch-dir test_data --patch-id 036019 --output-dir output --random-hipped

# Combined: OSM tags only + random hipped
python -m condor_buildings.main --patch-dir test_data --patch-id 036019 --output-dir output --roof-selection-mode osm_tags_only --random-hipped
```

## Test Data Location

```
test_data/
├── h036019.txt      # Patch metadata (UTM zone, translation offsets)
├── h036019.obj      # Terrain mesh
└── map_21.osm       # OSM building data
```

## Fake Condor Structure (for testing Blender addon without Condor3)

```
fake_condor/
└── Landscapes/
    └── TestLandscape/
        └── Working/
            ├── Heightmaps/
            │   ├── h036019.txt
            │   └── h036019.obj
            └── Autogen/  (output folder)
```

## Output Files

```
output/
├── o036019_LOD0.obj     # Detailed mesh (with 0.5m roof overhang)
├── o036019_LOD1.obj     # Simplified mesh (no overhang)
├── o036019_report.json  # Statistics and configuration used
└── o036019.log          # Detailed processing log
```

## Key CLI Arguments

| Argument | Description |
|----------|-------------|
| `--patch-dir` | Directory with input files (required) |
| `--patch-id` | Patch ID like 036019 (required) |
| `--output-dir` | Output directory (default: ./output) |
| `--verbose` | Enable debug logging |
| `--roof-selection-mode` | `geometry` (default) or `osm_tags_only` |
| `--random-hipped` | Mix 50% gabled / 50% hipped for testing |
| `--debug-osm-id <id>` | Process single building by OSM ID |

## Project Structure

```
condor_buildings/
├── __init__.py              # Package version + Blender addon registration
├── main.py                  # CLI entry point - START HERE
├── config.py                # Configuration constants and PipelineConfig
├── models/                  # Data models (BuildingRecord, MeshData, etc.)
│   ├── geometry.py          # Point2D, Point3D, Polygon, BBox
│   ├── building.py          # BuildingRecord, BuildingCategory, RoofType
│   ├── mesh.py              # MeshData with vertex/face management
│   └── terrain.py           # TerrainMesh, TerrainTriangle
├── projection/
│   └── transverse_mercator.py  # UTM projection for Condor coordinates
├── io/                      # File I/O (OSM parser, OBJ exporter)
│   ├── osm_parser.py        # OSM XML parsing
│   ├── way_stitcher.py      # Multipolygon way stitching
│   ├── terrain_loader.py    # Terrain OBJ loader
│   ├── patch_metadata.py    # h*.txt header parser
│   └── obj_exporter.py      # OBJ file export
├── processing/              # Footprint analysis, floor Z solver
│   ├── footprint.py         # Footprint analysis, OBB, eligibility
│   ├── spatial_index.py     # Grid-based spatial index for terrain
│   ├── floor_z_solver.py    # Floor Z computation from terrain
│   └── patch_filter.py      # Filter buildings outside patch bounds
├── bpypolyskel/             # Embedded straight skeleton library (GPL v3)
│   ├── bpypolyskel.py       # Main algorithm
│   ├── bpyeuclid.py         # 2D geometry primitives
│   └── poly2FacesGraph.py   # Skeleton to faces conversion
├── generators/              # Mesh generation (walls, roofs, UV mapping)
│   ├── building_generator.py  # Orchestrator for walls + roof
│   ├── walls.py             # Wall mesh generation
│   ├── roof_flat.py         # Flat roof generation
│   ├── roof_gabled.py       # Gabled roof generation (OBB-based)
│   ├── roof_hipped.py       # Hipped roof (BLOSM analytical, 4 verts)
│   ├── roof_polyskel.py     # Hipped roof (straight skeleton, >4 verts)
│   └── uv_mapping.py        # UV coordinate generation for texture atlas
├── utils/
│   ├── math_utils.py        # Mathematical utilities
│   ├── triangulation.py     # Polygon triangulation (ear clipping)
│   └── polygon_utils.py     # Polygon utilities (area, collinear removal)
└── blender/                 # Blender addon (v0.5.0+)
    ├── __init__.py          # Blender addon initialization
    ├── properties.py        # Blender PropertyGroup for UI fields
    ├── operators.py         # Import/clear operators with Condor workflow
    ├── panels.py            # UI panels (sidebar)
    ├── mesh_converter.py    # MeshData → Blender mesh conversion
    └── osm_downloader.py    # Download OSM data from Overpass API
```

## Documentation

- `README.md` - Complete technical reference (architecture, algorithms, configuration, version history)

## Blender Addon Usage (v0.6.1+)

The addon supports the real Condor folder structure:

1. **Condor Directory**: Path to Condor3 installation (e.g., `C:\Condor3`)
2. **Landscape**: Auto-detected from `Landscapes/` folder
3. **Patch Range**: X/Y min/max for batch processing, or single patch mode
4. **OSM Source**: Download from Overpass API or use local file
5. **Output**: Save to `Working/Autogen/` and/or import to Blender

### Programmatic Usage

```python
from condor_buildings.main import run_pipeline
from condor_buildings.config import PipelineConfig

config = PipelineConfig(
    patch_id="036019",
    patch_dir="/path/to/heightmaps",
    zone_number=0,  # Auto-loaded from h*.txt
    translate_x=0.0,
    translate_y=0.0,
    osm_path="/path/to/downloaded.osm",  # Optional: explicit OSM path
)

# Memory mode returns meshes directly (for Blender)
result = run_pipeline(config, output_mode="memory")
# result.lod0_meshes, result.lod1_meshes contain MeshData objects
```

### OSM Downloader

```python
from condor_buildings.blender.osm_downloader import download_osm_for_patch
from condor_buildings.io.patch_metadata import load_patch_metadata

metadata = load_patch_metadata("h036019.txt")
result = download_osm_for_patch(metadata, output_dir="./", filename_prefix="map")
# result.filepath contains path to downloaded .osm file
```

## Creating ZIP for Blender Installation

```bash
# From project root
powershell -Command "Compress-Archive -Path 'condor_buildings' -DestinationPath 'condor_buildings_v0.7.3.zip' -Force"
```

## Runtime Configuration (v0.6.1+)

Configure generator parameters before processing:

```python
from condor_buildings.generators import configure_generator

# Override defaults before processing
configure_generator(
    gable_height=4.0,           # Roof peak height (default 3.0m)
    roof_overhang_lod0=0.3,     # Overhang distance (default 0.5m)
    floor_z_epsilon=0.5,        # Sink below terrain (default 0.3m)
    gabled_max_floors=3,        # Max floors for gabled (default 2)
    polyskel_max_vertices=15,   # Max verts for polyskel (default 12)
)
```

## Session Workflow Rules

At the end of every work session, **always** generate or update the changelog file for the current version in `docs/CHANGELOG_x.x.x.md`. Version increments use patch bumps (e.g., 0.7.0 -> 0.7.1 -> 0.7.2). If changes are made within the same session, keep the same version number and update the existing changelog. The changelog must document all changes made, files modified, and test results. Also update the version in `__init__.py`, `README.md`, and `CLAUDE.md`. Finally, generate the Blender plugin ZIP with `powershell -Command "Compress-Archive -Path 'condor_buildings' -DestinationPath 'condor_buildings_vX.X.X.zip' -Force"`.

## Current Version

v0.7.3 - Fix UV mapping: floors/height_m synchronization:
- When building:levels tag overrode estimated floors but height came from estimate, they diverged
- Example: HOUSE with building:levels=1 got floors=1 but height_m=6.0 (from estimate) → 6m wall with 1-floor UV
- Fix: recompute height_m = floors * 3.0 when height was estimated (not explicit)
- Also: round() instead of int() for floor estimation from explicit height tags

Previous versions:
- v0.7.2: Fix HOUSE height estimation and roof selection
- v0.7.1: Fix HOUSE flat-roof grouping (route to Highrise_walls instead of houses)
- v0.7.0: Highrise wall system (Highrise_atlas.dds 2048x12288, multi-floor quads, merged Highrise_walls)
- v0.6.8: Polyskel UV mapping fix (orthographic planar projection with unified global Z scaling)
- v0.6.3: Texture-based mesh grouping (10 objects by texture type for optimal Condor rendering)
- v0.6.2: Automatic vertex deduplication (~63% reduction in vertex count)
- v0.6.1: Configurable parameters in Blender UI (gable height, overhang, floor Z, max floors, etc.)
- v0.6.0: Polyskel hipped roofs for 5-12 vertex buildings using bpypolyskel straight skeleton
- v0.5.0: Condor workflow support (auto-detect landscapes, download OSM, batch processing)
