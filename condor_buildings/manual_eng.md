# Condor Buildings Generator — Complete Manual

Plugin for Blender that generates 3D buildings for the Condor flight simulator. Building data is sourced from OpenStreetMap; terrain comes from Condor's heightmap files. The resulting OBJ/MTL files are written directly to Working/Autogen.

---

## Panel: Condor Settings

### Condor Directory
Path to the root Condor folder (e.g. `C:\Condor3`). A `Landscapes` subfolder must exist here. Without a valid path nothing can run — all buttons are disabled.

### Landscape
A dropdown that is automatically populated from the `Landscapes` subfolder. The plugin reads the folder contents and shows only those landscapes that have a `Working` subfolder. If no such landscape exists, or if the path is wrong, the list shows only "-- Select Landscape --".

If the path is empty or no landscape is selected, a red error icon with an error message is shown directly in the panel.

---

## Panel: Patch Selection

A patch is a square tile of landscape in Condor. A patch ID is a six-digit number in the format `XXXYYY` (first three digits = X coordinate, next three = Y coordinate), for example `035023`.

### Single Patch toggle

Switches between two patch input modes.

---

## Mode: Single Patch (toggle enabled)

### Patch ID field
A text field for manually entering a six-digit patch ID (max. 6 characters), for example `035023`.

### tr3f checkbox
A small checkbox next to the Patch ID field. Changes where terrain is loaded from and how objects are named:

**tr3f UNCHECKED (default):**
- Terrain is first searched for at `Working/Heightmaps/modified/h{patch_id}.obj`
- If not found, falls back to `Working/Heightmaps/h{patch_id}.obj`
- The terrain object in Blender is named `TR3{patch_id}` (e.g. `TR3035023`)
- The terrain material is also named `TR3{patch_id}`

**tr3f CHECKED:**
- Terrain is searched for exclusively at `Working/Heightmaps/22.5m/h{patch_id}.obj`
- If the file does not exist in the 22.5m folder → the operation stops immediately with an error; nothing is imported
- The terrain object in Blender is named `TR3f{patch_id}` (e.g. `TR3f035023`)
- The terrain material is also named `TR3f{patch_id}`

---

### Import Patch button (Single Patch mode)

Imports terrain and building files for a single patch. Step-by-step:

**1. Switch workspace**
The plugin switches Blender to the "Layout" workspace (if it exists and is not already active).

**2. Import terrain**
Checks whether the terrain object (`TR3{patch_id}` or `TR3f{patch_id}` depending on tr3f) already exists in the `Patch_Terrain` collection.

If it does not exist, the terrain OBJ file is imported (path depends on tr3f — see above). The OBJ is imported with axes `forward=Y, up=Z`. The imported object is renamed correctly.

After importing the terrain the plugin sets up the texture:
- Creates a new material named after the terrain object
- Adds a texture from `Landscapes/{landscape}/Textures/t{patch_id}.dds` (orthophoto)
- Builds the shader: Texture Image → Principled BSDF → Material Output
- If the `.dds` file does not exist, the material is created without a texture (slot left empty)
- Calculates UV map from the mesh dimensions — normalises X and Y coordinates to the 0–1 range

**3. OBJ building file check (tr3f only)**
If tr3f is checked, the plugin verifies that at least one of the following files exists in `Working/Autogen/`:
- `o{patch_id}.obj`
- `o{patch_id}_LOD1.obj`

If neither exists → the viewport is set up (see step 6) and a warning "File not found in Autogen" is shown. The operation ends; no buildings are imported, but the terrain remains in the scene and the view is set up correctly.

**4. Import building OBJ files**
Searches for building files in `Working/Autogen/`:
- `o{patch_id}.obj` → imported into collection `Condor_{landscape}_{patch_id}`
- `o{patch_id}_LOD1.obj` → imported into collection `Condor_{landscape}_{patch_id}_LOD1`
- If both files exist, both are imported

If neither file exists → the viewport is set up and a warning is shown. The operation ends.

Each imported OBJ goes into its own collection. If the collection does not exist yet, it is created. OBJ is imported with axes `forward=X, up=Z`.

After import the plugin fixes material names: if an object's name is in the internal texture map, the material is renamed to `condor_{object_name}`. This prevents duplicates and ensures textures are linked correctly.

**5. Missing texture search**
After all OBJ files are imported the plugin scans `Working/Autogen/Textures/` and tries to locate texture files that Blender did not find automatically.

**6. Viewport setup**
Regardless of whether buildings were imported or not, at the end (or before an early return) the plugin sets:
- Shading: **Material Preview** (MATERIAL)
- Focal length: **50 mm**
- Clip Start: **0.01 m**
- Clip End: **100 000 m**
- View distance: **9051 m** (shows the full patch)
- View rotation: **top-down** (Euler 0°, 0°, 0°)
- View lock: to the terrain object (`TR3{patch_id}` or `TR3f{patch_id}`) — the viewport automatically centres on the terrain

---

### Export Terrain button (Single Patch mode)

Exports the terrain object from the scene back to an OBJ file.

**tr3f UNCHECKED:**
- Looks for an object named `TR3{patch_id}`
- If not found → error, nothing is exported
- Exports to `Working/Heightmaps/modified/h{patch_id}.obj`

**tr3f CHECKED:**
- Looks for an object named `TR3f{patch_id}`
- If not found → error, nothing is exported
- Exports to `Working/Heightmaps/22.5m/modified/h{patch_id}.obj`

The `modified` folder is created automatically if it does not exist. The export uses axes `forward=Y, up=Z`, exports a triangulated mesh, normals, UV maps, and materials. On the next import the plugin always prefers the file from `modified` over the original.

---

## Mode: Range (toggle disabled)

A coordinate range for patches is entered using four fields:
- **X Min / X Max** — X coordinate range
- **Y Min / Y Max** — Y coordinate range

The plugin displays the total number of patches to be processed (e.g. "Patches: 6 (2×3)").

### terrain checkbox
Visible only in range mode. When checked, terrain is imported for each patch in the range (see the procedure below).

### Import Patch button (Range mode)

Imports building OBJ files and optionally terrain for all patches in the specified range. Step-by-step:

**For each patch in order (Y outer loop, X inner loop):**

1. **Import terrain** (only if the terrain checkbox is checked)

   Checks whether `TR3{patch_id}` already exists in the `Patch_Terrain` collection.
   If not, searches:
   - First `Working/Heightmaps/modified/h{patch_id}.obj`
   - If not found, tries `Working/Heightmaps/h{patch_id}.obj`

   If the file exists:
   - Imports into the `Patch_Terrain` collection (created if missing)
   - Renames the object to `TR3{patch_id}`
   - Creates material `TR3{patch_id}` with texture `t{patch_id}.dds` from the landscape Textures folder
   - Builds the shader: Texture Image → Principled BSDF → Material Output
   - Calculates UV map from mesh dimensions

   If no file exists → terrain for this patch is skipped; building import continues.

2. **Import building OBJ files**

   Searches in `Working/Autogen/`:
   - `o{patch_id}.obj` → imported into collection `Condor_{landscape}_{patch_id}`
   - `o{patch_id}_LOD1.obj` → imported into collection `Condor_{landscape}_{patch_id}_LOD1`

   If neither file exists → a warning is logged for this patch and the next patch is processed. Other patches continue normally.

   After import, material names are fixed to `condor_*` variants.

**After all patches are processed:**

3. **Missing texture search**
   Scans `Working/Autogen/Textures/` and locates files that Blender did not find.

4. **Patch positioning** (only if the terrain checkbox is checked)

   The first patch (lowest X and Y) stays in place (offset 0, 0). Each subsequent patch is shifted by a multiple of 5 760 m (the exact size of one Condor patch):
   - X axis offset: `-(x - x_min) × 5760 m`
   - Y axis offset: `(y - y_min) × 5760 m`

   Both the building collection objects (**LOD0 and LOD1** — collections `Condor_{landscape}_{patch_id}` and `…_LOD1`) and the terrain object for each patch are moved, so terrain and buildings stay correctly aligned relative to each other.

5. **Viewport setup**

   Material Preview, focal length 50 mm, Clip Start 0.01 m, Clip End 100 000 m, top-down view, distance 9051 m.

   View lock:
   - If terrain is checked → view is locked to the terrain of the first patch (`TR3{first_patch_id}`)
   - If terrain is not checked → view is locked to the first mesh object from the building collection of the first imported patch

6. **Result**
   - If at least one patch was imported → "Imported N patch(es)"
   - If no patch was imported → error "No patches imported — check OBJ files"
   - Warnings for patches without OBJ files are shown in the header (max. 5 warnings displayed)

---

## Panel: OSM Data

### OSM Source
Selects the source of building data:

- **Download from Overpass** — downloads data from the internet via the Overpass API (OpenStreetMap). Downloaded data is saved as `map_{patch_id}.osm` in the Working folder. On subsequent runs this file is used if it exists.
- **Local OSM File** — uses an existing `map_{patch_id}.osm` file from the Working folder. Useful for repeated generation without internet access.

### MSprint — add buildings
When checked, the plugin adds missing buildings from Microsoft Global Building Footprints after downloading OSM data. How it works:
- Downloads compressed GZ files with buildings for the area (once; then cached locally)
- Adds Microsoft buildings that are missing from OSM
- Merges the result (OSM + Microsoft) into one file and uses it for generation

---

## Panel: Output

### LOD Level
Determines which detail levels are generated:

- **LOD0 (Detailed)** — detailed building mesh with a 0.5 m roof overhang beyond the walls. File: `o{patch_id}.obj`.
- **LOD1 (Simple)** — simplified mesh without roof overhang. File: `o{patch_id}_LOD1.obj`.
- **Both LODs** — generates and saves both files. Both are imported into Blender as separate collections.

### Save to Autogen
Saves the generated OBJ and MTL files to `Working/Autogen/`. Enabled by default. If disabled, files are not written to disk — they are only displayed in Blender.

### Import to Blender
After generation, imports the resulting files into the Blender viewport. Enabled by default.

**When disabled (file mode):** buildings are not shown in Blender, but written straight to disk as files **ready for Condor** — just like the Export Condor OBJ+MTL button:
- an `o{patch_id}.mtl` is always written next to `o{patch_id}.obj` (materials and textures from the internal TEXTURE_MAP)
- roofs are produced correctly — gabled roofs doubled for double-sided display, hipped roofs with correct normals
- wind turbines are automatically rotated by one random angle around their own axis, merged into a single object and added to the OBJ; the angle is **the same for LOD0 and LOD1** of the same patch
- if Chimney **Batch** is checked in the *Other objects* section, chimneys are added to the OBJ as well (see below)
- the textures of the generated objects are copied to `Working/Autogen/Textures/` in file mode too — the building atlases and roofs, plus **`Pylons.dds`** (pylons/lines/aerialways) and **`WindTurbine.dds`** (turbines)

---

## Panel: Powerlines

### Generate Powerlines
When enabled, three kinds of infrastructure are generated from OSM data — **whenever they are present in the patch's data** — all merged into a single object named `pylones` with the shared texture `Pylons.dds` (part of the same OBJ/MTL as the buildings):

- **Power lines** (`power=line`, `power=minor_line`) — a 3D pylon at every node, catenary cables between nodes, and aviation warning balls. LOD1 keeps only the large/medium pylons (no cables, balls or small pylons), optionally the `pylon_large_low.obj` model.
- **Wind turbines** (`power=generator`, wind) — tower + blades as two models; each rotor is **randomly rotated** (deterministic), the same for LOD0 and LOD1.
- **Aerialways** (`aerialway=*`) — see below.

Note: the former separate "Aerialways" checkbox was **removed** — aerialways now fall under this single checkbox. Right below the Powerlines box is a collapsible **Other objects** section (chimneys, see below).

### Aerialways
From OSM `aerialway=*` ways (both cable-car and chair lifts) the plugin generates pylons, a straight cable, and hanging cabins/seats:
- **Pylons** — cabin (`Pylon_AerialCab`) or chair (`Pylon_Aerialway`), oriented along the route, foot exactly on the terrain. Pylons reaching beyond the patch edge take their foot height from the **neighbouring patch's terrain** so they line up with it.
- **Sheave wheels (rollers)** at the pylon top **tilt to the cable slope** (uphill / downhill).
- **The cable** runs over the wheel tops; there are two (out and back).
- **Cabins / seats** hang from the cable at regular spacing (cabins ~77.5 m, seats 15 m). Cabins are rotated 180° so the hanger reaching up to the cable points outward (clears the wheels).
- **LOD1:** the cabin pylon uses the lighter `Pylon_AerialCab_ns_low.obj` model (if present in `assets/pylons`), otherwise falls back to the detailed one; everything else is identical in both LODs.

Everything is merged into the `pylones` object. When a patch has aerialways, the log (see "Generation log") writes `pylones (aerialway)` for that object.

### Wind Turbine Rotation
This slider appears only when an object named `wind_turbine` or `wind_turbine_*` exists in the scene. Controls the rotation of selected wind turbines around the Z axis (0°–360°). Only affects selected (highlighted) turbine objects.

### Merge wind_turbine (button)
Appears only when `wind_turbine` or `wind_turbine_*` objects exist in the scene. Merges all turbines into a single object ready for Condor export.

**Step-by-step:**

1. Finds all objects named `wind_turbine` or starting with `wind_turbine_` anywhere in the scene (including hidden ones).
2. Groups them by the `Condor_*` collection they belong to — each patch is merged separately.
3. For each patch: selects all turbines of that patch and merges them into one object (Join).
4. Names the result `wind_turbine`.
5. Applies transforms (location, rotation, scale):
   - **Before merge:** each turbine object has its own origin exactly at its world position — X and Y are the GPS coordinates of the turbine converted to metres, Z is the terrain height at that point (the turbine base sits on the terrain). **This is why the Wind Turbine Rotation slider works** — it rotates the turbine around its own Z axis passing through its origin. If the origin were elsewhere (e.g. at scene zero), rotation would orbit the turbine around the scene centre instead.
   - **After merge and apply transforms:** the origin of the merged object moves to world coordinates (0, 0, 0). The absolute coordinates of all turbine vertices are baked directly into the mesh. The rotation of each turbine is baked into the geometry. From this point on, individual turbines cannot be rotated separately — they are part of one object. This makes the object ready for OBJ export — Condor requires absolute coordinates in the mesh, not object transforms.
6. Removes all materials and assigns one material `condor_wind_turbine`.
7. Moves the object into collection `Condor_{landscape}_{patch_id}`.
8. Removes duplicate materials (`condor_wind_turbine.001`, `.002`, etc.) created during import or previous operations.

---

## Generate Buildings button (large button)

The main plugin operation — downloads data, generates buildings, and imports them. The button is active only when Condor Directory, Landscape, and Patch ID (or a valid X/Y range) are all filled in. During processing the button shows "Processing {patch_id}...".

**Steps for each patch:**

1. Copies any missing textures from the plugin to `Working/Autogen/Textures/` (Roof1–6.dds, Houses_Atlas.dds, Highrise_Atlas.dds, Industrial_Atlas.dds).

2. **In Single Patch mode:** imports terrain the same way as the Import Patch button (including tr3f support).

3. **In Range mode with terrain checkbox:** imports terrain for each patch the same way as the Range mode Import Patch.

4. For each patch:
   - Reads metadata from `Working/Heightmaps/h{patch_id}.txt` (height coordinates, dimensions). If the file is missing → the patch is skipped.
   - Downloads or reads OSM data (`map_{patch_id}.osm`)
   - Optionally adds Microsoft buildings (MSprint)
   - Runs the pipeline: parse footprints → classify buildings → generate roofs and walls → group into objects by material type
   - Saves OBJ+MTL to `Working/Autogen/` (if Save to Autogen is enabled)
   - Imports into Blender (if Import to Blender is enabled)

5. After all patches are processed, sets up the viewport (Material Preview, top-down view, locked to terrain).

6. Displays statistics: building count, patch count, processing time in ms.

---

## Building heights and floor counts

The height of each building is determined in the following priority order:

1. **OSM tag `height`** — direct height in metres (e.g. `height=12.5`). If present, it is used as-is.
2. **OSM tag `building:levels`** — floor count from OSM. Height = floors × 3 m. If both `height` and `building:levels` are present, `height` takes priority.
3. **Estimate by category and area** — if OSM contains no height tag:

| Category | Condition | Floors | Height |
|---|---|---|---|
| HOUSE | always | 2 | 6 m |
| INDUSTRIAL | always | 1 | 6 m (high ceilings) |
| APARTMENT | area > 500 m² | 4 | 12 m |
| APARTMENT | area > 200 m² | 3 | 9 m |
| APARTMENT | otherwise | 2 | 6 m |
| COMMERCIAL | area > 200 m² | 2 | 6 m |
| COMMERCIAL | otherwise | 1 | 3 m |
| OTHER | area > 300 m² | 3 | 9 m |
| OTHER | area > 100 m² | 2 | 6 m |
| OTHER | otherwise | 1 | 3 m |

**Protection against OSM errors:** If the `building:levels` tag contains an absurd value (e.g. typo `233` instead of `3`), it is clamped to a maximum of 60 floors and the height is clamped to the corresponding limit. Such a building is logged as a warning.

**Height and floor synchronisation:** If `building:levels` overrides the estimated floor count but no explicit height is given, the height is recalculated as `floors × 3 m`. This prevents mismatches (e.g. a 1-floor house estimated at 6 m would have a stretched texture).

---

## Texture atlas — sizes and UV mapping

### Houses_Atlas.dds (residential buildings)
Size: **512 × 12 288 px**

The atlas is split vertically into two areas:

**Roofs (V 0.75 to 1.0 — upper part of the atlas):**
- 6 roof patterns, each 512 × 512 px
- Pattern is selected deterministically by the building's seed (Random Seed)

**Facades (V 0.0 to 0.75 — lower part of the atlas):**
- 12 facade styles, each 512 × 768 px
- Each style has 3 sections vertically: ground floor (ground), upper floor (upper), gable (gable) — each section 256 px
- Facade style is selected deterministically by the building's seed

**Wall UV mapping:**
- Every 3 m of wall length = 1/3 of the U width in the atlas
- U offset 1/3 — the mapping start is shifted so that doors (which are on the left edge of the atlas) repeat less often
- V range depends on the building's floor count — ground floor always at the bottom, gable at the top

### Highrise_Atlas.dds (apartment buildings and commercial buildings)
Size: **2048 × 12 288 px**

The atlas contains 12 regions (6 apartment buildings + 6 commercial buildings):
- Each region: 4 floors, each 256 px tall
- Apartment buildings: regions 0–5 (upper half of the atlas)
- Commercial buildings: regions 6–11 (lower half of the atlas)

The region selected depends on the building category and floor count. UV mapping calculates exact coordinates for each floor individually.

### Industrial_Atlas.dds (industrial buildings)
Size: **512 × 9 216 px**

Why this size: 12 facade styles × 768 px per style = 9 216 px. No roof section — that is why the atlas is 3 072 px shorter than Houses_Atlas (which has an extra 6 roof patterns × 512 px at the top).

**Key difference from Houses_Atlas:** Industrial_Atlas has no roof section. The entire atlas (V 0.0 to 1.0) contains only facades. Houses_Atlas has facades only in the V 0.0–0.75 area (the remaining 0.25 is roofs).

Because the code calculates facade V coordinates the same way for both atlases (facades = V 0.0–0.75), all V values for industrial must be scaled by a factor of **1 / 0.75 = 1.333**. This stretches the facades over the full atlas.

**Facade structure:**
- Same as Houses_Atlas: 12 facade styles, each with 3 sections — ground, upper, gable
- Facade style selection is deterministic by the building's seed

**UV section definition (how many metres = 1 section):**
- 1 atlas section = 256 px = **3 m** of wall height in the real world
- Each style has 3 sections stacked: ground (bottom), upper (middle), gable (top) — total 768 px = 9 m
- Section assigned by floor index: floor 0 → ground, floor 1 → upper, floor 2+ → gable

**Floors and sections for industrial:**
- Industrial buildings always have **1 floor = 3 m**
- 1 floor → **ground** section (ground floor with windows/doors)
- Upper and gable sections are not used
- The physical wall height in the geometry is **6 m** (code sets `height=6.0` for industrial buildings — high ceilings), but the UV maps only 1 section (3 m) → texture is stretched 2× vertically over the 6 m wall

**Horizontal UV (U axis):**
- Every 3 m of wall length = 1/3 of the U width in the atlas
- U offset 1/3 — mapping start is shifted right in the atlas (skips the section with doors)

---

## Export Condor OBJ+MTL button

Exports generated buildings from the scene to OBJ+MTL files ready for use directly in Condor. Works in both single patch and range mode.

**Steps for each patch:**

1. Finds the collection `Condor_{landscape}_{patch_id}` (LOD0) and/or `Condor_{landscape}_{patch_id}_LOD1` depending on the LOD Level setting. If the collection does not exist, an error is logged and the patch is skipped.

2. **Special step in range mode with terrain checked:** Patches in range mode are shifted apart by 5 760 m in Blender (each patch is at a different position in the scene). Condor however requires OBJ file coordinates always relative to the origin (0,0,0). The plugin therefore:
   - Saves the current position of all mesh objects in the collection
   - Moves them to (0, 0, 0)
   - Applies transforms
   - Performs the export
   - After export, moves objects back to their original Blender positions

   In single patch mode or without the terrain checkbox this step is skipped — the objects are already in the correct position.

3. Iterates through all mesh objects in the collection and builds object groups by their names (without `.001` suffixes etc.).

4. For each group, looks up the texture in TEXTURE_MAP. If the texture is not in the map, it tries to read it from the object's material in Blender.

5. Writes the OBJ file:
   - Triangulated mesh
   - Axes: forward=X, up=Z (Condor format)
   - Face normals
   - UV coordinates
   - Reference to the MTL file

6. Writes the MTL file with correct Condor material values for each group.

7. If Generate Powerlines is enabled and a pylon texture exists, copies `Pylons.dds` to `Working/Autogen/Textures/`.

**Output files:**
- `Working/Autogen/o{patch_id}.obj` + `o{patch_id}.mtl` (LOD0)
- `Working/Autogen/o{patch_id}_LOD1.obj` + `o{patch_id}_LOD1.mtl` (LOD1)

At the end, displays the number of exported files, patch count, and elapsed time.

---

## Statistics (Last Import)

After Generate Buildings completes, a box shows the results:
- **Buildings** — total number of generated buildings
- **Patches** — number of patches processed (shown only if more than 1)
- **Time** — total processing time in milliseconds

---

## Generation log (generate_log.txt)

Every generation run writes/extends **`generate_log.txt`** in `Working/Autogen`. The log is only ever **appended, never overwritten** — runs stack one below another. Each run starts with a summary, followed by per-patch blocks (separate for LOD0 and LOD1):

```
============================================================
Total patches: 12  (LOD0: 12, LOD1: 12)
Total time: 640.3 s  (10 min 40 s)
============================================================
031018
Generation time: 8.0 s
Objects:
  Highrise_walls
  flat_roof_1
  ...
  houses
  industrial_walls
  pylones (aerialway)
  chimney
Airport: Letališče Lesce
------------------------------------------------------------
031018_LOD1
...
```

- **Total patches / Total time** — patch count (LOD0/LOD1) and the sum of the patch times.
- **Objects** are listed as the **final** objects (gabled/hipped roofs are merged into `houses`, so only `houses` is listed).
- **`pylones (aerialway)`** — when the patch has aerialways.
- **Airport** — the line appears only when an airport centre falls inside the patch (from `airport/airports.json`).

---

## Subpanel: Roof Options

### Roof Selection
Determines how the plugin decides the roof type for each building:

- **Geometry-based (Recommended)** — combines footprint shape (area, aspect ratio, rectangularity) and the building category from OSM. Good for realistic results without explicit tags.
- **OSM Tags Only** — only buildings explicitly tagged as `building=house`, `building=detached`, etc. get a pitched roof. All other buildings get a flat roof.

### Random Hipped Roofs
Approximately 50% of buildings eligible for a gabled roof are randomly assigned a hipped roof instead. Useful for testing visual variety. The result depends on the Random Seed value.

### Merge Flat Roofs
Merges all flat roofs of one patch into a single `flat_roof` object instead of splitting them into 6 groups by texture atlas (Roof1.dds through Roof6.dds). Required for the Terrain photo feature.

### Terrain photo on flat roofs
Available only when Merge Flat Roofs is enabled. Flat roofs receive the orthophoto texture from `Landscapes/{landscape}/Textures/t{patch_id}.dds`. UV coordinates are normalised to the patch dimensions — from above, roofs blend with the ground.

### Only industrial
Available only when Terrain photo is enabled. Only industrial buildings receive the orthophoto (merged into `flat_roof`). Other buildings with flat roofs keep their normal Roof1–6.dds textures and go into objects `flat_roof_1` through `flat_roof_6`.

### Gable Height
Height of the ridge of a gabled or hipped roof above the top of the walls, in metres. Applies to all generated pitched roofs. Default: 3 m.

### Roof Overhang
Roof overhang distance beyond the walls for LOD0 (detailed level), in metres. LOD1 has no overhang. Default: 0.5 m.

### Max Floors (Gabled)
Maximum number of floors a building may have in order to receive a gabled or hipped roof. Taller buildings automatically get a flat roof. Default: 2.

---

## Subpanel: Advanced

### House-Scale Constraints
Conditions a building footprint must meet to be assigned a gabled or hipped roof (in addition to the floor count):

- **Max House Area** — maximum footprint area in m². Default: 360 m².
- **Max Side Length** — maximum length of the longest side in m. Default: 30 m.
- **Min Side Length** — minimum length of the shortest side in m. Default: 3.2 m.
- **Max Aspect Ratio** — maximum length-to-width ratio. Default: 4.8.

All conditions must be met simultaneously. If any one is not met, the building gets a flat roof.

### Geometry Constraints

- **Min Rectangularity** — minimum "rectangularity" of the footprint (ratio of actual area to bounding rectangle area). Range 0–1. Buildings with irregular shapes (L, U, T) have a low value and get a flat roof. Default: 0.70.
- **Max Vertices (Polyskel)** — maximum number of footprint vertices for computing a hipped roof via polyskeleton. Complex footprints with more vertices get a gabled or flat roof instead. Default: 12.

### Terrain Integration

- **Floor Z Offset** — how many metres buildings are sunk below the terrain surface to avoid gaps where the terrain is not perfectly flat. Default: 0.3 m.

### Reproducibility

- **Random Seed** — integer for initialising the random number generator. The same seed produces the same results (same buildings get hipped roofs, same buildings get the same textures). Default: 42.

---

## Other objects (collapsible section below Powerlines)

This is not a separate subpanel at the bottom but a **collapsible box right below the Powerlines box** (expand/collapse with the arrow). When more than 2 collections are imported, the Outliner is also collapsed automatically so it doesn't get cluttered. The section is a container for extra objects — chimneys for now, more may be added.

### Optional chimney source: chimney.osm
If a file `chimney.osm` (or `Chimney.osm`) exists in `Working/Autogen`, the plugin pulls the chimneys belonging to the patch out of it before generating chimneys and adds them into `map_{patch_id}.osm` (read streamed; duplicate IDs are skipped). Those chimneys are then generated just like the ones from the main OSM. If the file isn't there, nothing changes and everything works as before.

### Chimney — Batch (checkbox next to the "Chimney" label)
For file mode only (Import to Blender disabled). When checked, after generating the OBJ the plugin also generates the chimneys (just like the Import button), merges them into a single `chimney` object with its origin at (0, 0, 0), and adds it into the same OBJ. If an MTL is being written too, the chimney gets the `condor_chimney` material and the `Chimney.dds` texture in the MTL. Off by default.

### Chimneys — Import
Imports chimneys for the patch entered in Patch ID. Works in both modes (Single Patch and Range).

**Steps:**

1. Reads the OSM file `map_{patch_id}.osm` from `Working/Autogen/`. If the file does not exist, the patch is skipped.
2. Reads metadata from `h{patch_id}.txt` (coordinate projection). If missing, the patch is skipped.
3. Checks terrain availability — looks for `Working/Heightmaps/modified/h{patch_id}.obj`, otherwise `Working/Heightmaps/h{patch_id}.obj`. If terrain does not exist, the patch is skipped.
4. Deletes all existing `Chimney_{patch_id}_*` objects from the scene and memory to prevent duplicates on repeated import.
5. Reads chimneys from OSM data:
   - **Nodes** with tag `man_made=chimney` — reads coordinates and height from the `height` tag (default 30 m)
   - **Polygons (way)** with tag `man_made=chimney` — calculates the centroid, reads height
   - **Duplicate check:** if a node lies inside a polygon, the polygon is skipped — only one chimney exists at that location. Detection uses the winding number algorithm.
6. For each chimney, imports a 3D model:
   - Height ≥ 31 m → `chimney_big.obj` (large industrial chimney)
   - Height < 31 m → `chimney_small.obj` (smaller chimney)
   - Both models are included with the plugin in the `assets/3Dobjects/` folder
7. Places the chimney at the correct X, Y coordinates. Z height (chimney base):
   - If the terrain object `TR3{patch_id}` is in the scene → casts a ray downward onto the terrain and finds the exact height at that point (ray cast)
   - If the terrain is not in the scene → loads the terrain mesh from file and interpolates height from triangles
8. Each chimney is named `Chimney_{patch_id}_{number:03d}` and the `patch_id` is stored as a custom attribute.
9. Chimneys are placed in subcollections:
   - Large → `chimney_big_{patch_id}` (subcollection of `Condor_{landscape}_{patch_id}`)
   - Small → `chimney_small_{patch_id}` (subcollection of `Condor_{landscape}_{patch_id}`)
10. In Range mode: if more than one patch was imported, chimneys are shifted by the correct offset (5760 m × patch position) the same way as terrain and buildings.
11. Copies the texture `Chimney.dds` from `assets/3Dobjects/` to `Working/Autogen/Textures/` (only if not already there).

**Chimney origin after import:** The origin lies at the chimney's X, Y world coordinates and at the terrain height Z at that point (the chimney base sits exactly on the terrain). The model geometry extends from Z=0 (relative to the origin) upward.

---

### Chimneys — Merge
Merges all chimneys of the current patch into a single object ready for Condor export.

**Step-by-step:**

1. Finds all objects starting with `Chimney_` that are visible in the view layer. Orphaned objects in memory (invisible) are ignored.
2. Groups them by the stored `patch_id` attribute — each patch is merged separately.
3. For each patch: selects all chimneys and merges them into one object (Join).
4. Names the result `chimney`.
5. Applies transforms (location, rotation, scale):
   - **Before merge:** each chimney has its own origin exactly at its position — X and Y are the chimney's world coordinates (metres), Z is the terrain height at that point (the chimney base lies exactly on the terrain). The chimney geometry stands from Z=0 of the origin upward. **This means each chimney can be moved or adjusted individually before merging** — the origin is where the chimney physically stands.
   - **After merge and apply in Single Patch mode:** the origin moves to world coordinates (0, 0, 0). The absolute coordinates of all chimneys are baked into the mesh geometry. From this point on, chimneys cannot be adjusted individually.
   - **After merge and apply in Range mode (with terrain):** the origin is set to the position of the terrain object `TR3{patch_id}`. The geometry is shifted so that chimneys remain in the correct position relative to the terrain object. This ensures chimneys are correctly aligned with their terrain even when the patch is offset from the scene origin.
6. Removes all materials and assigns the material `condor_chimney`.
7. Moves the object into collection `Condor_{landscape}_{patch_id}`.
8. Deletes the now-empty subcollections `chimney_big_{patch_id}` and `chimney_small_{patch_id}`.
9. Removes duplicate materials `condor_chimney.001`, `.002`, etc.

---

### Transmitter — in the Other objects section

Next to chimneys, the **Other objects** box also has a **Transmitter** row with **Import**, **Merge** buttons and a **Batch** checkbox. It generates communication transmitters (towers/masts) from OSM.

**OSM detection:** `man_made=communications_tower`, or `man_made=mast` / `man_made=tower` together with `tower:type=communication` (or another `communication*` tag). Height is taken from the `height` tag. A building at the transmitter location is **suppressed** (like chimneys). The OSM download also automatically adds these masts/towers to the query.

**Models** (`assets/3Dobjects`):
- height ≤ 100 m → `transmitter_small.obj`
- height > 100 m → `transmitter_big.obj`

The model is **uniformly scaled** to the height (keeps its shape), the base sits on the terrain. **Both models share one material `condor_transmitter` and one texture `transmitter.dds`.**

**Batch (checkbox)** — file mode only (Import to Blender off): after generating the OBJ, the transmitter is added into it as the object **`transmitter`** (material `condor_transmitter`, texture `transmitter.dds`). It is written **right before `pylones`**, so `pylones` stays the last object in the file, and it keeps the **model's own normals** (same shading as a manual import). Off by default.

**Import** — imports the transmitters for the given patch. Before importing it deletes existing `Transmitter_{patch_id}_*` (to avoid `.001` duplicates), places the models on the terrain and puts them in the subcollections `transmitter_big_{patch_id}` / `transmitter_small_{patch_id}`.

**Merge** — merges **all transmitters of the patch (big and small) into ONE object named `transmitter`** with the material `condor_transmitter`. Empty subcollections and duplicate materials are cleaned up.

The whole transmitter feature lives in one removable file `blender/transmitters.py`.

---

## Object-to-texture mapping (TEXTURE_MAP)

The plugin maintains an internal table that assigns each type of generated object its `.dds` texture file. This table is used when assigning materials in Blender and when writing the MTL file for Condor.

| Object / group name | Texture | Which buildings belong here |
|---|---|---|
| `houses` | `Houses_Atlas.dds` | Residential buildings with a pitched roof — walls and roof in one object |
| `Highrise_walls` | `Highrise_Atlas.dds` | Apartment buildings, commercial buildings, unknown buildings with footprint up to 200 m² |
| `industrial_walls` | `Industrial_Atlas.dds` | Industrial buildings (see OSM tag list below) + unknown buildings with footprint over 200 m² |
| `flat_roof_1` | `Roof1.dds` | Flat roofs — group 1 (random assignment) |
| `flat_roof_2` | `Roof2.dds` | Flat roofs — group 2 |
| `flat_roof_3` | `Roof3.dds` | Flat roofs — group 3 |
| `flat_roof_4` | `Roof4.dds` | Flat roofs — group 4 |
| `flat_roof_5` | `Roof5.dds` | Flat roofs — group 5 |
| `flat_roof_6` | `Roof6.dds` | Flat roofs — group 6 |
| `flat_roof` (merged) | `Roof1.dds` default, or `t{patch_id}.dds` with Terrain photo | All flat roofs merged into one object |
| `pylones` | `Pylons.dds` | Pylons and power line cables **+ aerialways** (merged into the same object, same texture) |
| `wind_turbine` | `WindTurbine.dds` | Wind turbines |
| `chimney` | `Chimney.dds` | Chimneys |
| `transmitter` | `transmitter.dds` | Communication transmitters (big and small share one material and texture) |

### Which OSM tags belong to the INDUSTRIAL category

From the OSM tag `building=*`, the following values are treated as industrial buildings:
`industrial`, `warehouse`, `factory`, `hangar`, `manufacture`, `storage_tank`, `silo`, `barn`, `greenhouse`, `farm_auxiliary`, `digester`

In addition: buildings with tag `building=yes` or any other unknown tag (category OTHER) **and a footprint larger than 200 m²** are also assigned to `industrial_walls` — because large untagged buildings are likely industrial or agricultural sheds.

Industrial buildings always receive a **flat roof** regardless of footprint shape. The default height is 6 m (1 floor with high ceilings).

**How the mapping is applied when importing OBJ into Blender:**
After importing an OBJ file the plugin goes through all new objects. If an object's name is in this table (e.g. the object is named `industrial_walls`), its material is renamed to `condor_industrial_walls`. This unifies materials — multiple patches share the same material instead of creating duplicates (`industrial_walls.001`, `industrial_walls.002`, etc.).

---

## Subpanel: Debug

### Debug OSM ID
A field for entering the OSM ID of a single specific building (numeric ID from OpenStreetMap). If filled in, Generate Buildings processes only that one building and ignores all others. Useful for debugging problematic footprints or testing new roof types.
