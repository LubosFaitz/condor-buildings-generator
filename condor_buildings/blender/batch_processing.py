"""
Batch processing for Condor Buildings (standalone, separate from the operators).

When BOTH "Batch processing" and "Import to Blender" are checked, Generate
Buildings hands each freshly imported patch to this module, which for that one
patch:

  1. rotates ALL its wind turbines by ONE random angle around Z (origin at base,
     so they spin in place - same angle for the whole patch, like file mode),
  2. merges the turbines (condor.merge_wind_turbines),
  3. exports a Condor-ready OBJ+MTL (same as the Export button; respects the
     Separate checkbox = 25000 split),
  4. deletes the patch's collection(s) from Blender,

then the operator moves on to the next patch. Processing one patch at a time
keeps memory low for big ranges. Terrain is NOT imported in batch mode.

With Batch processing off, none of this runs and Generate Buildings behaves as
before.
"""

import os
import re
import math
import random
import logging

import bpy

logger = logging.getLogger(__name__)


# =============================================================================
# Run log: a single log.txt in Working/Autogen listing each generated patch,
# its generation time, the objects it produced and any airport in the patch.
# =============================================================================

RUN_LOG_NAME = "generate_log.txt"


def write_run_summary(autogen, n_patches, n_lod0, n_lod1, total_ms):
    """Append a run summary header (total patch / LOD counts and total time) on top
    of the run's per-patch blocks. Never raises."""
    try:
        os.makedirs(autogen, exist_ok=True)
        total_s = total_ms / 1000.0
        mins = int(total_s // 60)
        secs = total_s - mins * 60
        lines = []
        lines.append("=" * 60)
        lines.append(f"Total patches: {n_patches}  (LOD0: {n_lod0}, LOD1: {n_lod1})")
        lines.append(f"Total time: {total_s:.1f} s  ({mins} min {secs:.0f} s)")
        lines.append("=" * 60)
        with open(os.path.join(autogen, RUN_LOG_NAME), 'a', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.warning("run log: summary failed: %s", e)


def append_run_log(autogen, header, elapsed_ms, object_names, airport_names):
    """Append one block to generate_log.txt: a header line (e.g. ``031018`` or
    ``031018_LOD1``), the generation time, the FINAL objects one per line, and any
    airport in the patch.

    The log is only ever APPENDED (never overwritten), so a batch lists its
    blocks one below another, each separated by a line. Never raises - logging
    must not break generation.
    """
    try:
        os.makedirs(autogen, exist_ok=True)
        lines = []
        lines.append(str(header))
        lines.append(f"Generation time: {elapsed_ms / 1000.0:.1f} s")
        lines.append("Objects:")
        if object_names:
            for nm in object_names:
                lines.append(f"  {nm}")
        else:
            lines.append("  (none)")
        if airport_names:
            lines.append(f"Airport: {', '.join(airport_names)}")
        lines.append("-" * 60)
        with open(os.path.join(autogen, RUN_LOG_NAME), 'a', encoding='utf-8') as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.warning("run log: append failed for %s: %s", header, e)


def airports_in_patch(autogen, projector, patch_half):
    """Names of airports whose centre falls inside this patch, from the shared
    airport/airports.json (written by download_airports_for_patch). Empty list if
    the file is missing or nothing is in range."""
    import json
    path = os.path.join(autogen, "airport", "airports.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return []
    names = []
    for name, info in data.items():
        center = info.get("center")
        if not center or len(center) != 2:
            continue
        try:
            x, y = projector.project(float(center[0]), float(center[1]))
        except Exception:
            continue
        if abs(x) <= patch_half and abs(y) <= patch_half:
            names.append(name)
    return names


def _patch_collections(landscape_name, patch_id, output_lod):
    """Return the [(lod_name, collection)] that exist for this patch."""
    lods = []
    base = f"Condor_{landscape_name}_{patch_id}"
    if output_lod in ('LOD0', 'BOTH'):
        col = bpy.data.collections.get(base)
        if col:
            lods.append(("LOD0", col))
    if output_lod in ('LOD1', 'BOTH'):
        col1 = bpy.data.collections.get(base + "_LOD1")
        if col1:
            lods.append(("LOD1", col1))
    return lods


def _turbine_index(name):
    """Index from a turbine object name: 'wind_turbine' -> 0, 'wind_turbine_3' -> 3
    (ignores any Blender '.NNN' dedup suffix)."""
    import re
    base = re.sub(r'\.\d+$', '', name)
    m = re.search(r'_(\d+)$', base)
    return int(m.group(1)) if m else 0


def _rotate_turbines(collection, angle=None, randomize=False, seed=0):
    """Rotate the collection's wind turbines around Z.
    - randomize=False: every turbine gets the SAME angle (one per patch).
    - randomize=True: each turbine gets its OWN angle, deterministic from
      (seed, turbine index) so LOD0 and LOD1 match.
    If angle is None (and not randomized) a random one is used.
    Returns (count, angle_degrees); angle_degrees is -1.0 when randomized."""
    turbines = [
        o for o in collection.objects
        if o.type == 'MESH' and (o.name == 'wind_turbine' or o.name.startswith('wind_turbine'))
    ]
    if not turbines:
        return 0, 0.0
    if randomize:
        for o in turbines:
            a = random.Random(f"{seed}-{_turbine_index(o.name)}").uniform(0.0, 2.0 * math.pi)
            o.rotation_euler[2] += a
        return len(turbines), -1.0
    if angle is None:
        angle = random.uniform(0.0, 2.0 * math.pi)
    for o in turbines:
        o.rotation_euler[2] += angle
    return len(turbines), math.degrees(angle)


def _merge_turbines(context):
    """Merge the patch's wind turbines via the existing operator (if any)."""
    has_turbines = any(
        o.name == 'wind_turbine' or o.name.startswith('wind_turbine_')
        for o in bpy.data.objects
    )
    if not has_turbines:
        return
    try:
        bpy.ops.condor.merge_wind_turbines()
    except Exception as e:
        logger.warning("batch: merge_wind_turbines failed: %s", e)


def _export_patch(props, patch_id, paths, lods):
    """
    Export the patch's collections to Condor-ready OBJ+MTL (same as the Export
    button), splitting large objects when Separate is on. Returns list of errors.
    """
    from ..config import (
        build_texture_map, CONDOR_AXIS_SWAP,
        CONDOR_EXPORT_TRIANGULATE, CONDOR_EXPORT_NORMALS,
    )
    from ..io.obj_exporter import export_condor_obj_mtl
    from .mesh_converter import blender_obj_to_meshdata

    errors = []

    tex_map = build_texture_map(patch_id, props.flat_roof_terrain_photo)
    condor_tex_map = dict(tex_map)
    if props.flat_roof_terrain_photo and 'flat_roof' in condor_tex_map:
        condor_tex_map['flat_roof'] = "T_" + condor_tex_map['flat_roof']

    for lod_name, col in lods:
        groups = {}
        for obj in col.objects:
            if obj.type != 'MESH':
                continue
            md = blender_obj_to_meshdata(obj, osm_id=obj.name)
            if md and not md.is_empty():
                group_name = re.sub(r'\.\d+$', '', obj.name)
                groups[group_name] = md
                if group_name not in condor_tex_map and obj.material_slots:
                    mat = obj.material_slots[0].material
                    if mat and mat.use_nodes and mat.node_tree:
                        for node in mat.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                img = node.image
                                tex_name = os.path.basename(bpy.path.abspath(img.filepath)) if img.filepath else img.name
                                if tex_name:
                                    condor_tex_map[group_name] = tex_name
                                break

        if not groups:
            errors.append(f"Patch {patch_id} {lod_name}: kolekce neobsahuje žádné mesh objekty")
            continue

        suffix = "" if lod_name == "LOD0" else f"_{lod_name}"
        out_obj = os.path.join(paths['autogen'], f"o{patch_id}{suffix}.obj")
        try:
            export_condor_obj_mtl(
                groups, out_obj, condor_tex_map,
                comment=f"{lod_name} - Patch {patch_id} (Condor-ready, batch)",
                axis_swap=CONDOR_AXIS_SWAP,
                triangulate=CONDOR_EXPORT_TRIANGULATE,
                include_normals=CONDOR_EXPORT_NORMALS,
            )
            print(f"[BATCH] {patch_id} {lod_name}: exported {len(groups)} objects -> {os.path.basename(out_obj)} + .mtl")
        except Exception as e:
            print(f"[BATCH] {patch_id} {lod_name}: EXPORT FAILED: {e}")
            errors.append(f"Patch {patch_id} {lod_name}: export failed: {e}")

    return errors


def _delete_patch(landscape_name, patch_id):
    """Remove the patch's building collections (objects AND the now-empty
    collection datablocks) so nothing is left in the outliner."""
    from .mesh_converter import cleanup_buildings_collection
    for name in (
        f"Condor_{landscape_name}_{patch_id}",
        f"Condor_{landscape_name}_{patch_id}_LOD1",
    ):
        try:
            cleanup_buildings_collection(name)  # removes the objects
            col = bpy.data.collections.get(name)
            if col is not None:
                # Unlink from any parent collections, then delete the datablock,
                # so the empty collection no longer shows in the outliner.
                for parent in bpy.data.collections:
                    if col.name in parent.children:
                        parent.children.unlink(col)
                if col.name in bpy.context.scene.collection.children:
                    bpy.context.scene.collection.children.unlink(col)
                bpy.data.collections.remove(col)
        except Exception as e:
            logger.warning("batch: cleanup of %s failed: %s", name, e)


def process_patch(context, props, patch_id, paths):
    """
    Run the full batch chain for ONE freshly imported patch:
    rotate turbines -> merge -> export OBJ+MTL -> delete the patch.

    Returns a list of error strings (empty on success).
    """
    print(f"[BATCH] ===== Patch {patch_id}: start =====")

    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    lods = _patch_collections(props.landscape_name, patch_id, props.output_lod)
    if not lods:
        print(f"[BATCH] {patch_id}: no collection to export - SKIPPED")
        return [f"Patch {patch_id}: žádná kolekce k exportu (batch)"]
    print(f"[BATCH] {patch_id}: generated, collections: {', '.join(n for n, _c in lods)}")

    # 1) random rotation of turbines (one angle for the whole patch)
    for lod_name, col in lods:
        count, deg = _rotate_turbines(col)
        if count:
            print(f"[BATCH] {patch_id} {lod_name}: rotated {count} turbines by {deg:.1f} deg")

    # 2) merge turbines
    _merge_turbines(context)
    print(f"[BATCH] {patch_id}: turbines merged")

    # 3) export Condor-ready OBJ+MTL
    errors = _export_patch(props, patch_id, paths, lods)

    # 4) delete the patch from Blender (only if export produced no errors)
    if not errors:
        _delete_patch(props.landscape_name, patch_id)
        print(f"[BATCH] {patch_id}: deleted from Blender")
        print(f"[BATCH] ===== Patch {patch_id}: done, next =====")
    else:
        print(f"[BATCH] {patch_id}: kept in Blender ({len(errors)} error(s)) - NOT deleted")

    return errors


# =============================================================================
# "add MTL" for file mode (Import to Blender off)
# =============================================================================
#
# When "add MTL" is checked and Import to Blender is off, the pipeline writes
# o<patch>.obj via export_mesh_groups (no MTL). This rewrites that OBJ into the
# Condor-ready OBJ + MTL format (the same the Export OBJ+MTL button produces:
# triangulated, normals, materials/textures). No splitting.

class _FMObj:
    """One 'o' object parsed from a file-mode OBJ (global 1-based v / vt)."""

    def __init__(self, name):
        self.name = name
        self.v = []        # "x y z" strings
        self.vt = []       # "u v" strings
        self.faces = []    # list of faces; each is a list of (vi, ti), ti None


def _parse_filemode_obj(obj_path):
    """Parse the file-mode OBJ into a list of _FMObj."""
    objects = []
    cur = None
    with open(obj_path, 'r', encoding='utf-8') as f:
        for raw in f:
            s = raw.strip()
            if s.startswith('o '):
                cur = _FMObj(s[2:].strip())
                objects.append(cur)
            elif cur is None:
                continue
            elif s.startswith('vt '):
                cur.vt.append(s[3:].strip())
            elif s.startswith('v '):
                cur.v.append(s[2:].strip())
            elif s.startswith('f '):
                corners = []
                for tok in s[2:].split():
                    p = tok.split('/')
                    vi = int(p[0])
                    ti = int(p[1]) if len(p) >= 2 and p[1] != '' else None
                    corners.append((vi, ti))
                cur.faces.append(corners)
    return objects


def _filemode_objs_to_groups(objects):
    """Convert parsed objects to {name: MeshData} (global -> local 1-based)."""
    from ..models.mesh import MeshData

    offsets = []
    v_off = 0
    vt_off = 0
    for o in objects:
        offsets.append((v_off, vt_off))
        v_off += len(o.v)
        vt_off += len(o.vt)

    groups = {}
    for o, (vo, to) in zip(objects, offsets):
        md = MeshData()
        for s in o.v:
            p = s.split()
            md.vertices.append((float(p[0]), float(p[1]), float(p[2])))
        for s in o.vt:
            p = s.split()
            md.uvs.append((float(p[0]), float(p[1])))
        has_uv = len(o.vt) > 0
        for face in o.faces:
            md.faces.append([vi - vo for (vi, _ti) in face])
            if has_uv and all(ti is not None for (_vi, ti) in face):
                md.face_uvs.append([ti - to for (_vi, ti) in face])
        if len(md.face_uvs) != len(md.faces):
            md.face_uvs = []
        groups[o.name] = md
    return groups


def add_mtl_to_filemode_obj(obj_path, texture_map=None, chimney_md=None):
    """
    Rewrite a file-mode OBJ into Condor-ready OBJ + MTL (materials/textures),
    same as the Export OBJ+MTL button. No splitting. Returns True if written.

    If chimney_md (already Condor-swapped MeshData) is given, it is added as a
    'chimney' object with the Chimney.dds texture in the MTL.
    """
    objects = _parse_filemode_obj(obj_path)
    if not objects:
        logger.info("add_mtl: no objects in %s", obj_path)
        return False

    groups = _filemode_objs_to_groups(objects)
    tex_map = dict(texture_map or {})
    if chimney_md is not None and not chimney_md.is_empty():
        groups['chimney'] = chimney_md
        tex_map['chimney'] = 'Chimney.dds'

    from ..io.obj_exporter import export_condor_obj_mtl
    # File-mode coordinates are already axis-swapped -> axis_swap=False here
    # (don't swap again). But the coords ARE swapped, so fix the header to
    # "Axis swap: True" below, otherwise the addon's Import Patch reads "False"
    # and rotates the whole patch 90 deg.
    export_condor_obj_mtl(
        groups, obj_path, tex_map,
        comment="File mode -> Condor-ready (add MTL)",
        axis_swap=False,
        triangulate=True,
        include_normals=True,
    )
    _set_axis_swap_header_true(obj_path)
    print(f"[add MTL] wrote MTL for {os.path.basename(obj_path)} ({len(groups)} objects)")
    return True


def _set_axis_swap_header_true(obj_path):
    """Rewrite the '# Axis swap: False' header line to True (coords are already
    swapped in file mode, so Import Patch must treat the file as swapped)."""
    try:
        with open(obj_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for i, ln in enumerate(lines):
            if ln.startswith('# Axis swap:'):
                lines[i] = ln.replace('Axis swap: False', 'Axis swap: True')
                break
        with open(obj_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    except Exception as e:
        logger.warning("add_mtl: header fix failed for %s: %s", obj_path, e)


def append_chimney_plain(obj_path, chimney_md):
    """
    Append a 'chimney' object to a plain file-mode OBJ (no MTL). chimney_md is
    already Condor-swapped; global v/vt indices continue from the existing file.
    """
    if chimney_md is None or chimney_md.is_empty():
        return
    v_count = 0
    vt_count = 0
    with open(obj_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('v '):
                v_count += 1
            elif line.startswith('vt '):
                vt_count += 1

    has_uv = len(chimney_md.uvs) > 0 and len(chimney_md.face_uvs) == len(chimney_md.faces)
    with open(obj_path, 'a', encoding='utf-8') as f:
        f.write("\no chimney\n")
        for (x, y, z) in chimney_md.vertices:
            f.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        if has_uv:
            for (u, v) in chimney_md.uvs:
                f.write(f"vt {u:.6f} {v:.6f}\n")
        for i, face in enumerate(chimney_md.faces):
            if has_uv:
                fu = chimney_md.face_uvs[i]
                f.write("f " + " ".join(f"{vi + v_count}/{ti + vt_count}" for vi, ti in zip(face, fu)) + "\n")
            else:
                f.write("f " + " ".join(str(vi + v_count) for vi in face) + "\n")
    print(f"[chimney] appended chimney to {os.path.basename(obj_path)}")


# =============================================================================
# Chimney batch: build a merged "chimney" MeshData for a patch (file mode)
# =============================================================================

def _parse_obj_as_meshdata(path):
    """Parse a simple OBJ asset (chimney_big/small.obj) into one MeshData."""
    from ..models.mesh import MeshData
    md = MeshData()
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            s = raw.strip()
            if s.startswith('v '):
                p = s.split()
                md.vertices.append((float(p[1]), float(p[2]), float(p[3])))
            elif s.startswith('vt '):
                p = s.split()
                md.uvs.append((float(p[1]), float(p[2])))
            elif s.startswith('f '):
                vids = []
                tids = []
                ok_uv = True
                for tok in s[2:].split():
                    parts = tok.split('/')
                    vids.append(int(parts[0]))
                    if len(parts) >= 2 and parts[1]:
                        tids.append(int(parts[1]))
                    else:
                        ok_uv = False
                md.faces.append(vids)
                if ok_uv and len(tids) == len(vids):
                    md.face_uvs.append(tids)
    if len(md.face_uvs) != len(md.faces):
        md.face_uvs = []
    return md


def inject_chimneys_from_source(paths, patch_id):
    """
    Optional extra chimney source. If a file ``chimney.osm`` (or ``Chimney.osm``)
    exists in Working/Autogen, pull the man_made=chimney nodes/ways that fall
    inside this patch out of it and append them into ``map_<patch>.osm`` so the
    normal chimney generation picks them up. If the file is NOT there, this does
    nothing and everything works exactly as before.

    The source is read STREAMED (iterparse) to stay memory-friendly. IDs already
    present in the target OSM are skipped, so re-runs don't create duplicates.
    """
    import xml.etree.ElementTree as ET
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata
    from ..config import PATCH_HALF

    autogen = paths['autogen']

    # Optional source file (chimney.osm / Chimney.osm). Missing -> behave as before.
    src = None
    for cand in ("chimney.osm", "Chimney.osm"):
        p = os.path.join(autogen, cand)
        if os.path.exists(p):
            src = p
            break
    if not src:
        return

    target = os.path.join(autogen, f"map_{patch_id}.osm")
    if not os.path.exists(target):
        return

    txt_path = None
    for cand in (os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                 os.path.join(paths['heightmaps'], f"H{patch_id}.txt")):
        if os.path.exists(cand):
            txt_path = cand
            break
    if not txt_path:
        return

    try:
        meta = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(
            meta.zone_number, meta.translate_x, meta.translate_y)
    except Exception as e:
        logger.warning("chimney inject: setup failed for %s: %s", patch_id, e)
        return

    half = PATCH_HALF

    # IDs already in the target, to skip duplicates.
    try:
        troot = ET.parse(target).getroot()
    except Exception as e:
        logger.warning("chimney inject: cannot read map_%s.osm: %s", patch_id, e)
        return
    existing_node_ids = {n.get("id") for n in troot.findall("node")}
    existing_way_ids = {w.get("id") for w in troot.findall("way")}

    def _in_patch(lat, lon):
        x, y = projector.project(lat, lon)
        return abs(x) <= half and abs(y) <= half

    # Stream the source: keep in-patch chimney nodes, and chimney ways (+ their
    # nodes) that have at least one in-patch node.
    node_coords = {}      # id -> (lat, lon)
    node_xml = {}         # id -> serialized <node>
    keep_node_ids = set()
    way_blocks = []       # (way_id, serialized <way>)
    for _ev, el in ET.iterparse(src, events=('end',)):
        if el.tag == 'node':
            nid = el.get('id')
            lat = float(el.get('lat')); lon = float(el.get('lon'))
            node_coords[nid] = (lat, lon)
            node_xml[nid] = ET.tostring(el, encoding='unicode')
            tags = {t.get('k'): t.get('v') for t in el.findall('tag')}
            if tags.get('man_made') == 'chimney' and _in_patch(lat, lon):
                keep_node_ids.add(nid)
            el.clear()
        elif el.tag == 'way':
            tags = {t.get('k'): t.get('v') for t in el.findall('tag')}
            if tags.get('man_made') == 'chimney':
                refs = [nd.get('ref') for nd in el.findall('nd')]
                inside = any(r in node_coords and _in_patch(*node_coords[r])
                             for r in refs)
                if inside:
                    way_blocks.append((el.get('id'), ET.tostring(el, encoding='unicode')))
                    for r in refs:
                        keep_node_ids.add(r)
            el.clear()

    # Collect the new elements (skip ids already in the target).
    blocks = []
    for nid in keep_node_ids:
        if nid not in existing_node_ids and nid in node_xml:
            blocks.append(node_xml[nid])
    for wid, wxml in way_blocks:
        if wid not in existing_way_ids:
            blocks.append(wxml)
    if not blocks:
        return

    # Insert before the closing </osm>.
    try:
        with open(target, 'r', encoding='utf-8') as f:
            content = f.read()
        idx = content.rfind('</osm>')
        if idx == -1:
            return
        injected = content[:idx] + "".join("  " + b + "\n" for b in blocks) + content[idx:]
        with open(target, 'w', encoding='utf-8') as f:
            f.write(injected)
        logger.info("chimney inject: added %d element(s) into map_%s.osm",
                    len(blocks), patch_id)
    except Exception as e:
        logger.warning("chimney inject: write failed for %s: %s", patch_id, e)


def build_chimney_meshdata(props, paths, patch_id, low=False):
    """
    Build one merged 'chimney' MeshData for a patch, in Condor-swapped coords so
    it aligns with the (already swapped) buildings in the file-mode OBJ.

    Places the chimney_big/small.obj asset at every OSM man_made=chimney location
    (node or way center), sitting on the terrain. Origin is 0,0,0 (coords baked
    into the mesh). Returns MeshData, or None if no chimneys / missing inputs.

    low=True uses the low-poly chimney_big_low.obj / chimney_small_low.obj models
    (for the LOD1 file).
    """
    import xml.etree.ElementTree as ET
    import shutil
    from ..projection.transverse_mercator import TransverseMercatorProjector
    from ..io.patch_metadata import load_patch_metadata
    from ..io.terrain_loader import load_terrain
    from ..models.geometry import Point2D, BBox
    from ..models.mesh import MeshData
    from ..io.obj_exporter import _condor_xform
    from .operators import _parse_height_str, _point_in_polygon

    osm_path = os.path.join(paths['autogen'], f"map_{patch_id}.osm")
    if not os.path.exists(osm_path):
        return None

    # Optional: pull this patch's chimneys out of an extra chimney.osm source
    # into map_<patch>.osm first (no-op if that file isn't there).
    inject_chimneys_from_source(paths, patch_id)

    txt_path = None
    for cand in (os.path.join(paths['heightmaps'], f"h{patch_id}.txt"),
                 os.path.join(paths['heightmaps'], f"H{patch_id}.txt")):
        if os.path.exists(cand):
            txt_path = cand
            break
    if not txt_path:
        return None

    terrain_file = os.path.join(paths['heightmaps'], "modified", f"h{patch_id}.obj")
    if not os.path.exists(terrain_file):
        terrain_file = os.path.join(paths['heightmaps'], f"h{patch_id}.obj")
    if not os.path.exists(terrain_file):
        return None

    try:
        metadata = load_patch_metadata(txt_path)
        projector = TransverseMercatorProjector(
            metadata.zone_number, metadata.translate_x, metadata.translate_y)
        terrain_mesh = load_terrain(terrain_file)
        root = ET.parse(osm_path).getroot()
    except Exception as e:
        logger.warning("chimney: setup failed for %s: %s", patch_id, e)
        return None

    node_coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon")))
                   for n in root.findall("node")}

    chimneys = []  # (cx, cy, height, has_height, material)
    for n in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in n.findall("tag")}
        if tags.get("man_made") == "chimney":
            has_h = "height" in tags
            h = _parse_height_str(tags.get("height", "30"))
            mat = tags.get("material")
            x, y = projector.project(float(n.get("lat")), float(n.get("lon")))
            chimneys.append((x, y, h, has_h, mat))
    for w in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if tags.get("man_made") == "chimney":
            coords = [node_coords[nd.get("ref")] for nd in w.findall("nd")
                      if nd.get("ref") in node_coords]
            if coords:
                poly_xy = [projector.project(la, lo) for la, lo in coords]
                if any(_point_in_polygon(cx, cy, poly_xy) for cx, cy, *_ in chimneys):
                    continue
                la = sum(c[0] for c in coords) / len(coords)
                lo = sum(c[1] for c in coords) / len(coords)
                has_h = "height" in tags
                h = _parse_height_str(tags.get("height", "30"))
                mat = tags.get("material")
                x, y = projector.project(la, lo)
                chimneys.append((x, y, h, has_h, mat))

    if not chimneys:
        return None

    assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "3Dobjects")
    big_file = "chimney_big_low.obj" if low else "chimney_big.obj"
    small_file = "chimney_small_low.obj" if low else "chimney_small.obj"
    big = _parse_obj_as_meshdata(os.path.join(assets_dir, big_file))
    small = _parse_obj_as_meshdata(os.path.join(assets_dir, small_file))

    def foot_z_at(cx, cy):
        pt = Point2D(cx, cy)
        for tri_idx in terrain_mesh.get_triangles_in_bbox(BBox(cx - 1, cy - 1, cx + 1, cy + 1)):
            tri = terrain_mesh.triangles[tri_idx]
            if tri.contains_point_2d(pt):
                z = tri.z_at_xy(cx, cy)
                if z is not None:
                    return z
        return 0.0

    merged = MeshData()
    for (cx, cy, h, has_h, mat) in chimneys:
        # Model choice: brick -> small (brick texture); otherwise big when taller
        # than 30 m, else small. Height scales the model only if a height tag exists.
        is_big = (mat != "brick") and (h > 30)
        template = big if is_big else small
        if template.is_empty():
            continue
        native_h = 100.0 if is_big else 30.0
        sz = (h / native_h) if (has_h and native_h > 0) else 1.0
        fz = foot_z_at(cx, cy)
        inst = MeshData()
        inst.vertices = [(vx * sz + cx, vy * sz + cy, vz * sz + fz) for (vx, vy, vz) in template.vertices]
        inst.uvs = list(template.uvs)
        inst.faces = [list(face) for face in template.faces]
        inst.face_uvs = [list(fu) for fu in template.face_uvs]
        merged.merge(inst)

    if merged.is_empty():
        return None

    # Returned in RAW pipeline coords (NOT axis-swapped). It is added to the
    # building groups and the exporter applies the Condor axis swap to ALL groups
    # together, so the chimney lines up with the buildings.

    # Make sure Chimney.dds is in Autogen/Textures so the MTL can reference it.
    tex_src = os.path.join(assets_dir, "Chimney.dds")
    tex_dst = os.path.join(paths['autogen'], "Textures", "Chimney.dds")
    if os.path.exists(tex_src) and not os.path.exists(tex_dst):
        try:
            os.makedirs(os.path.dirname(tex_dst), exist_ok=True)
            shutil.copy2(tex_src, tex_dst)
        except Exception as e:
            logger.warning("chimney: copy Chimney.dds failed: %s", e)

    print(f"[chimney] patch {patch_id}: built {len(chimneys)} chimneys")
    return merged


# =============================================================================
# File-mode export WITH correct roofs (briefly through Blender)
# =============================================================================

def _remove_collection(name):
    """Delete a collection and its objects (so nothing is left in the outliner)."""
    from .mesh_converter import cleanup_buildings_collection
    cleanup_buildings_collection(name)
    col = bpy.data.collections.get(name)
    if col is None:
        return
    for parent in bpy.data.collections:
        if col.name in parent.children:
            parent.children.unlink(col)
    if col.name in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.unlink(col)
    bpy.data.collections.remove(col)


def _merge_turbines_filemode(context, collection, angle=None, randomize=False, seed=0):
    """
    Wind turbines for file mode: rotate the turbines in the collection around each
    turbine's own origin (its foot), then join them into one 'wind_turbine' object
    and apply transforms (origin 0,0,0). With randomize=False all share one patch
    angle; with randomize=True each gets its own (deterministic from seed+index, so
    LOD0 and LOD1 match).
    """
    count, deg = _rotate_turbines(collection, angle, randomize=randomize, seed=seed)
    if not count:
        return
    turbines = [
        o for o in collection.objects
        if o.type == 'MESH' and (o.name == 'wind_turbine' or o.name.startswith('wind_turbine'))
    ]
    if not turbines:
        return
    for o in list(context.view_layer.objects):
        if o is not None:
            o.select_set(False)
    for o in turbines:
        o.select_set(True)
    context.view_layer.objects.active = turbines[0]
    bpy.ops.object.join()
    merged = context.active_object
    merged.name = 'wind_turbine'
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if deg < 0:
        print(f"[filemode] turbines: rotated {count} (each own random angle), merged -> wind_turbine")
    else:
        print(f"[filemode] turbines: rotated {count} by {deg:.1f} deg, merged -> wind_turbine")


def export_filemode_via_blender(context, props, patch_id, paths, result):
    """
    File-mode export with CORRECT roofs.

    The pure-data file export got the pitched roofs wrong (gabled not doubled,
    hipped bad normals), because the doubling / normal-recalc is done by Blender
    on import. So here we briefly import the generated groups into a TEMP Blender
    collection (which runs exactly that processing), read the objects back and
    write the OBJ - identical to the Export OBJ+MTL button - then delete the temp
    objects. With 'add mtl batch' it writes an MTL too; with 'Batch' (chimney) it
    adds the merged chimney. Also writes the o<patch>_report.json.
    """
    import re
    import json
    from dataclasses import asdict
    from ..config import (
        build_texture_map, CONDOR_AXIS_SWAP,
        CONDOR_EXPORT_TRIANGULATE, CONDOR_EXPORT_NORMALS,
    )
    from ..io.obj_exporter import export_condor_obj_mtl, export_mesh_groups
    from .mesh_converter import (
        import_grouped_meshes_to_blender, blender_obj_to_meshdata,
    )
    from ..generators.powerlines import pylon_assets_dir

    autogen = paths['autogen']
    texture_dir = os.path.join(autogen, "Textures")
    tex_map = build_texture_map(patch_id, props.flat_roof_terrain_photo)
    extra_dirs = [os.path.join(paths['landscape'], "Textures")]
    if props.generate_powerlines:
        extra_dirs.append(pylon_assets_dir())

    condor_tex_map = dict(tex_map)
    if props.flat_roof_terrain_photo and 'flat_roof' in condor_tex_map:
        condor_tex_map['flat_roof'] = "T_" + condor_tex_map['flat_roof']

    # Chimney (raw pipeline coords; the exporter swaps it with the buildings).
    # LOD0 uses the detailed models, LOD1 the low-poly _low models.
    chimney_md0 = build_chimney_meshdata(props, paths, patch_id) if props.chimney_batch else None
    chimney_md1 = build_chimney_meshdata(props, paths, patch_id, low=True) if props.chimney_batch else None

    # File mode always writes BOTH LOD0 and LOD1 (matches the previous behaviour
    # of the old file-mode export, regardless of the Output LOD dropdown).
    lods = []
    if result.grouped_lod0:
        lods.append(("", result.grouped_lod0))
    if result.grouped_lod1:
        lods.append(("_LOD1", result.grouped_lod1))

    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

    # ONE random turbine angle per patch (used when NOT randomizing each turbine),
    # so LOD0 and LOD1 turbines match. For the per-turbine randomization a single
    # per-patch seed is used (also shared by LOD0/LOD1).
    turbine_angle = random.uniform(0.0, 2.0 * math.pi)
    turbine_seed = random.randint(0, 2**31 - 1)
    randomize_turbines = props.randomize_wind_turbines

    exported = {}  # suffix -> object names actually written to the OBJ (for the log)
    for suffix, grouped in lods:
        tmp_col = f"_fmtmp_{patch_id}{suffix}"
        _remove_collection(tmp_col)  # clear any leftover
        try:
            # Import into Blender -> correct gabled doubling + hipped normals.
            objects = import_grouped_meshes_to_blender(
                grouped, collection_name=tmp_col,
                texture_dir=texture_dir, texture_map=tex_map,
                extra_texture_dirs=extra_dirs,
            )
            # Wind turbines (file mode): rotate ALL by ONE random angle around
            # each turbine's own origin (which sits at its foot), then merge them
            # into one 'wind_turbine' object and apply transforms (origin 0,0,0).
            col = bpy.data.collections.get(tmp_col)
            if col is not None:
                _merge_turbines_filemode(context, col, turbine_angle,
                                         randomize=randomize_turbines, seed=turbine_seed)

            # Read the (now processed) objects back to MeshData groups.
            read_objs = list(col.objects) if col is not None else objects
            groups = {}
            for obj in read_objs:
                if obj.type != 'MESH':
                    continue
                md = blender_obj_to_meshdata(obj, osm_id=obj.name)
                if md and not md.is_empty():
                    name = re.sub(r'\.\d+$', '', obj.name)
                    groups[name] = md
            chimney_md = chimney_md1 if suffix == "_LOD1" else chimney_md0
            if chimney_md is not None and not chimney_md.is_empty():
                groups['chimney'] = chimney_md

            out_obj = os.path.join(autogen, f"o{patch_id}{suffix}.obj")
            # 'add mtl batch' is hidden but treated as always ON -> always write
            # the MTL. (To make it a real toggle again: use `if props.add_mtl:`
            # with the export_condor_obj_mtl branch, else export_mesh_groups.)
            tm = dict(condor_tex_map)
            tm['chimney'] = 'Chimney.dds'
            # --- TRANSMITTER add-on (removable: delete blender/transmitters.py) ---
            try:
                from . import transmitters
                transmitters.add_filemode_groups(groups, props, paths, patch_id)
                tm['transmitter_big'] = 'transmitter_big.dds'
                tm['transmitter_small'] = 'transmitter_small.dds'
            except Exception as e:
                print(f"[transmitter] file-mode add failed: {e}")
            export_condor_obj_mtl(
                groups, out_obj, tm,
                comment=f"Patch {patch_id}{suffix} (file mode, correct roofs)",
                axis_swap=CONDOR_AXIS_SWAP,
                triangulate=CONDOR_EXPORT_TRIANGULATE,
                include_normals=CONDOR_EXPORT_NORMALS,
            )
            print(f"[filemode] {patch_id}{suffix}: exported {len(groups)} objects (correct roofs)")
            exported[suffix] = sorted(groups.keys())
        finally:
            _remove_collection(tmp_col)

    # JSON report (run_pipeline does not write it in memory mode)
    try:
        with open(os.path.join(autogen, f"o{patch_id}_report.json"), 'w', encoding='utf-8') as f:
            json.dump(asdict(result.report), f, indent=2)
    except Exception as e:
        logger.warning("filemode: report json failed: %s", e)

    return exported
