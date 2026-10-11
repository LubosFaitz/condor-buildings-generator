"""
Condor Buildings Generator - Blender Addon

This addon integrates the Condor Buildings pipeline into Blender,
allowing users to generate 3D building meshes from OSM data directly
in Blender's viewport.

Usage:
    1. Install this addon in Blender (Edit > Preferences > Add-ons > Install)
    2. Enable "Condor Buildings Generator" addon
    3. Open sidebar in 3D View (press N)
    4. Navigate to "Condor" tab
    5. Set patch directory and ID
    6. Click "Import Condor Buildings"
"""

bl_info = {
    "name": "Condor Buildings Generator",
    "author": "Condor Buildings Team (Wiek Schoenmakers, Juan Luis Gabriel, Claude)",
    "version": (0, 6, 1),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Condor",
    "description": "Generate 3D buildings from OSM data for Condor 3 flight simulator",
    "category": "Import-Export",
    "doc_url": "https://github.com/yourusername/condor-buildings-generator",
}

# Check if running inside Blender
_BLENDER_AVAILABLE = False
try:
    import bpy
    _BLENDER_AVAILABLE = True
except ImportError:
    pass


def register():
    """Register addon classes with Blender."""
    if not _BLENDER_AVAILABLE:
        print("Condor Buildings: Not running in Blender, skipping registration")
        return

    from . import properties
    from . import operators
    from . import panels

    # Register properties first (others depend on it)
    properties.register()
    operators.register()
    panels.register()

    print(f"Condor Buildings Generator v{'.'.join(map(str, bl_info['version']))} registered")


def unregister():
    """Unregister addon classes from Blender."""
    if not _BLENDER_AVAILABLE:
        return

    from . import panels
    from . import operators
    from . import properties

    # Unregister in reverse order
    panels.unregister()
    operators.unregister()
    properties.unregister()

    print("Condor Buildings Generator unregistered")


# Allow running as script for testing
if __name__ == "__main__":
    register()
