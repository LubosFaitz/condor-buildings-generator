"""
Condor 3 Autogen Buildings Generator

A standalone Python pipeline to generate 3D building meshes from OSM data
for Condor 3 flight simulator.

Can be used as:
- CLI tool: python -m condor_buildings.main
- Blender addon: Install condor_buildings package in Blender

Milestone A: Single-patch prototype with LOD0/LOD1 OBJ export.
Milestone B: Blender addon integration.
"""

__version__ = "0.9.4"
__author__ = "Condor Buildings Team"

# Blender addon metadata (must be at package root for Blender to detect)
bl_info = {
    "name": "Condor Buildings Generator",
    "author": "Condor Buildings Team (Wiek Schoenmakers, Juan Luis Gabriel, Claude)",
    "version": (0, 9, 4),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Condor",
    "description": "Generate 3D buildings from OSM data for Condor 3 flight simulator",
    "category": "Import-Export",
    "doc_url": "https://github.com/condor-buildings/condor-buildings-generator",
}


def register():
    """Register addon with Blender."""
    try:
        from .blender import properties, operators, panels
        properties.register()
        operators.register()
        panels.register()
        print(f"Condor Buildings Generator v{__version__} registered")
    except ImportError as e:
        print(f"Condor Buildings: Blender modules not available ({e})")
    # --- TRANSMITTER add-on (removable: delete blender/transmitters.py) ---
    try:
        from .blender import transmitters
        transmitters.register()
    except Exception as e:
        print(f"Condor Buildings: transmitters not loaded ({e})")


def unregister():
    """Unregister addon from Blender."""
    # --- TRANSMITTER add-on (removable) ---
    try:
        from .blender import transmitters
        transmitters.unregister()
    except Exception:
        pass
    try:
        from .blender import panels, operators, properties
        panels.unregister()
        operators.unregister()
        properties.unregister()
        print("Condor Buildings Generator unregistered")
    except ImportError:
        pass
