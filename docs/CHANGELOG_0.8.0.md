# Changelog v0.8.0 - Blender Material Assignment

**Date**: 2026-03-17

## Summary

Automatic material creation and texture assignment when importing buildings into Blender. Each object gets a Principled BSDF material with the corresponding .dds texture loaded as Image Texture on Base Color.

## Changes

### New: Blender material assignment
- Each imported object automatically receives a Principled BSDF material
- Image Texture node connected to Base Color with settings matching Wiek's reference:
  - Interpolation: Linear
  - Projection: Flat
  - Extension: Repeat
  - Color Space: sRGB
  - Alpha: Straight
- Textures loaded from `Working/Autogen/Texture/` in the Condor landscape folder
- Materials reused across patches (no duplicates when importing multiple patches)
- If texture .dds not found on disk, material is created without image (Blender shows pink, user assigns manually)

### Texture mapping
| Object | Texture |
|--------|---------|
| `houses` | `Houses_Atlas.dds` |
| `Highrise_walls` | `Highrise_Atlas.dds` |
| `industrial_walls` | `Industrial_Atlas.dds` |
| `flat_roof_1..6` | `Roof1.dds` .. `Roof6.dds` |
| `flat_roof` (merged) | `Roof1.dds` (placeholder) |

### New: TEXTURE_MAP config
- Added `TEXTURE_MAP` dictionary in `config.py` mapping group names to .dds filenames
- Will also be used for MTL export in a future version

## Files Modified

- `condor_buildings/config.py` - Added TEXTURE_MAP dictionary
- `condor_buildings/blender/mesh_converter.py` - Added `_create_material()`, `_assign_material()`, updated `import_grouped_meshes_to_blender()` with `texture_dir` parameter
- `condor_buildings/blender/operators.py` - Builds texture_dir path from Condor landscape folder, passes to mesh converter
- `condor_buildings/__init__.py` - Version bump to 0.8.0, bl_info version synced to (0, 8, 0)
- `CLAUDE.md` - Updated version references

## Test Plan

1. Import buildings in Blender with textures in `Working/Autogen/Texture/`
2. Verify each object has material with correct texture loaded
3. Import without textures present - verify materials created but pink (no image)
4. Import multiple patches - verify materials are reused (no `condor_houses.001` duplicates)
