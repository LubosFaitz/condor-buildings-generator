"""
Mesh generators for Condor Buildings Generator.

Contains wall generator, flat roof generator, gabled roof generator,
polyskel hipped roof generator, and the main building generator
that orchestrates them all.
"""

from .walls import generate_walls, WallGeneratorConfig
from .roof_flat import generate_flat_roof, FlatRoofConfig
from .roof_gabled import generate_gabled_roof, GabledRoofConfig
from .building_generator import (
    generate_building,
    generate_building_lod0,
    generate_building_lod1,
    BuildingGeneratorResult,
    configure_generator,
    reset_generator_config,
    get_runtime_config,
    GeneratorRuntimeConfig,
)

# Conditional import of polyskel roof generator
# Available only in Blender (requires mathutils + bpypolyskel)
try:
    from .roof_polyskel import generate_polyskel_roof, PolyskelRoofConfig, POLYSKEL_AVAILABLE
except ImportError:
    POLYSKEL_AVAILABLE = False

__all__ = [
    'generate_walls',
    'WallGeneratorConfig',
    'generate_flat_roof',
    'FlatRoofConfig',
    'generate_gabled_roof',
    'GabledRoofConfig',
    'generate_building',
    'generate_building_lod0',
    'generate_building_lod1',
    'BuildingGeneratorResult',
    'configure_generator',
    'reset_generator_config',
    'get_runtime_config',
    'GeneratorRuntimeConfig',
]
