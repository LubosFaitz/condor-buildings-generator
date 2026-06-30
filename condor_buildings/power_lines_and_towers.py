"""
This file is part of blender-osm (OpenStreetMap importer for Blender).
Copyright (C) 2014-2018 Vladimir Elistratov
prokitektura+support@gmail.com

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from parse.osm.relation.building import Building

from manager import BaseManager, Linestring, Polygon, PolygonAcceptBroken, WayManager
from renderer import Renderer2d
from renderer.node_renderer import BaseNodeRenderer
from renderer.curve_renderer import CurveRenderer

from building.manager import BuildingManager, BuildingParts, BuildingRelations
from building.renderer import BuildingRenderer

from manager.logging import Logger


def tunnel(tags, e):
    if tags.get("tunnel") == "yes":
        e.valid = False
        return True
    return False


import bpy
class ChimneyRenderer:
    def __init__(self, app):
        self.app = app
        self._counter = 0
        self._collection = None
    def _ensureCollection(self):
        if self._collection is not None: return
        self._collection = bpy.data.collections.new("Chimneys")
        bpy.context.scene.collection.children.link(self._collection)
    def _addChimney(self, x, y, tags):
        from mathutils import Vector
        z = 0.0
        depsgraph = bpy.context.evaluated_depsgraph_get()
        result, location, normal, face_idx, obj, matrix = bpy.context.scene.ray_cast(depsgraph, Vector((x, y, 10000.0)), Vector((0, 0, -1)))
        if result: z = location.z
        self._ensureCollection()
        self._counter += 1
        height = 30.0
        if tags and "height" in tags:
            try: height = float(tags["height"].replace("m", "").strip())
            except: pass
        meshName = f"Chimney_{self._counter:03d}"
        mesh = bpy.data.meshes.new(meshName)
        mesh.from_pydata([(0, 0, 0)], [], [])
        mesh.update()
        obj = bpy.data.objects.new(meshName, mesh)
        obj.location = (x, y, z)
        obj["height"] = height
        if tags and "name" in tags: obj["osm_name"] = tags["name"]
        self._collection.objects.link(obj)
    def renderNode(self, node, osm):
        coords = node.getData(osm)
        self._addChimney(coords[0], coords[1], node.tags)
    def renderPolygon(self, element, osm):
        verts = list(element.getData(osm))
        if not verts: return
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        self._addChimney(cx, cy, element.tags)
    def preRender(self, element): pass
    def postRender(self, element): pass
    def renderLineString(self, element, osm): pass
    def renderMultiLineString(self, element, osm): pass
    def renderMultiPolygon(self, element, osm): self.renderPolygon(element, osm)
    def finalize(self):
        from mathutils import Vector
        def fix_heights():
            if self._collection is None: return None
            depsgraph = bpy.context.evaluated_depsgraph_get()
            for obj in self._collection.objects:
                x, y = obj.location.x, obj.location.y
                result, location, _, _, _, _ = bpy.context.scene.ray_cast(depsgraph, Vector((x, y, 10000.0)), Vector((0, 0, -1)))
                if result: obj.location.z = location.z
            return None
        bpy.app.timers.register(fix_heights, first_interval=5.0)

class WindTurbineRenderer:
    def __init__(self, app):
        self.app = app
        self._counter = 0
        self._collection = None
    def _ensureCollection(self):
        if self._collection is not None: return
        self._collection = bpy.data.collections.new("WindTurbines")
        bpy.context.scene.collection.children.link(self._collection)
    def _addTurbine(self, x, y, tags):
        from mathutils import Vector
        z = 0.0
        depsgraph = bpy.context.evaluated_depsgraph_get()
        result, location, normal, face_idx, obj, matrix = bpy.context.scene.ray_cast(depsgraph, Vector((x, y, 10000.0)), Vector((0, 0, -1)))
        if result: z = location.z
        self._ensureCollection()
        self._counter += 1
        height = 100.0
        if tags and "height" in tags:
            try: height = float(tags["height"].replace("m", "").strip())
            except: pass
        meshName = f"WindTurbine_{self._counter:03d}"
        mesh = bpy.data.meshes.new(meshName)
        mesh.from_pydata([(0, 0, 0)], [], [])
        mesh.update()
        obj = bpy.data.objects.new(meshName, mesh)
        obj.location = (x, y, z)
        obj["height"] = height
        if tags and "name" in tags: obj["osm_name"] = tags["name"]
        self._collection.objects.link(obj)
    def renderNode(self, node, osm):
        coords = node.getData(osm)
        self._addTurbine(coords[0], coords[1], node.tags)
    def renderPolygon(self, element, osm):
        verts = list(element.getData(osm))
        if not verts: return
        cx = sum(v[0] for v in verts) / len(verts)
        cy = sum(v[1] for v in verts) / len(verts)
        self._addTurbine(cx, cy, element.tags)
    def preRender(self, element): pass
    def postRender(self, element): pass
    def renderLineString(self, element, osm): pass
    def renderMultiLineString(self, element, osm): pass
    def renderMultiPolygon(self, element, osm): self.renderPolygon(element, osm)
    def finalize(self):
        from mathutils import Vector
        coll = self._collection
        if coll is None: return
        def fix_heights():
            depsgraph = bpy.context.evaluated_depsgraph_get()
            count = 0
            for obj in coll.objects:
                x, y = obj.location.x, obj.location.y
                result, location, _, _, _, _ = bpy.context.scene.ray_cast(depsgraph, Vector((x, y, 10000.0)), Vector((0, 0, -1)))
                if result:
                    obj.location.z = location.z
                    count += 1
            print(f"[WindTurbine] Posunuto {count} bodu na teren")
            return None
        bpy.app.timers.register(fix_heights, first_interval=3.0)
        print("[WindTurbine] Timer naplanovan")

class WindTurbineManager(BaseManager):
    def parseNode(self, element, elementId): pass
    def parseWay(self, element, elementId):
        if element.closed:
            import parse
            element.t = parse.polygon
            element.r = True
        else:
            element.valid = False
    def render(self):
        super().render()
        print("[WindTurbineManager] render volana")
        if hasattr(self.renderer, 'finalize'):
            print("[WindTurbineManager] volam finalize")
            self.renderer.finalize()
class ChimneyManager(BaseManager):
    def parseNode(self, element, elementId): pass
    def parseWay(self, element, elementId):
        if element.closed:
            import parse
            element.t = parse.polygon
            element.r = True
        else:
            element.valid = False
    def render(self):
        super().render()
        if hasattr(self.renderer, 'finalize'): self.renderer.finalize()

def setup(app, osm):
    # comment the next line if logging isn't needed
    Logger(app, osm)
    
    if bpy.context.scene.get('importing_chimneys', 0) == 1:
        renderer = ChimneyRenderer(app)
        m = ChimneyManager(osm)
        m.setRenderer(renderer)
        m.setNodeRenderer(renderer)
        osm.addCondition(lambda tags, e: tags.get("man_made") == "chimney", "chimneys", m)
        osm.addNodeCondition(lambda tags, e: tags.get("man_made") == "chimney", "chimneys", m)
        app.managers.append(m)
        return
    
    # create managers
    wayManager = WayManager(osm, CurveRenderer(app))
    linestring = Linestring(osm)
    polygon = Polygon(osm)
    polygonAcceptBroken = PolygonAcceptBroken(osm)
    
    # custom setup begins
    if bpy.context.scene.get('importing_chimneys', 0) == 0:
        osm.addCondition(lambda tags, e: tags.get("power") == "line", "power_lines", linestring)
        osm.addCondition(lambda tags, e: tags.get("power") == "minor_line", "minor_lines", linestring)
        osm.addCondition(lambda tags, e: tags.get("aerialway") == "chair_lift", "chair_lift", linestring)
        osm.addCondition(lambda tags, e: tags.get("aerialway") == "gondola", "Gondola", linestring)
        wt_renderer = WindTurbineRenderer(app)
        wt_manager = WindTurbineManager(osm)
        wt_manager.setRenderer(wt_renderer)
        wt_manager.setNodeRenderer(wt_renderer)
        osm.addCondition(lambda tags, e: tags.get("power") == "generator", "wind_turbines", wt_manager)
        osm.addNodeCondition(lambda tags, e: tags.get("power") == "generator", "wind_turbines", wt_manager)
        app.managers.append(wt_manager)
    else:
        renderer = ChimneyRenderer(app)
        m = ChimneyManager(osm)
        m.setRenderer(renderer)
        m.setNodeRenderer(renderer)
        osm.addCondition(lambda tags, e: tags.get("man_made") == "chimney", "chimneys", m)
        osm.addNodeCondition(lambda tags, e: tags.get("man_made") == "chimney", "chimneys", m)
        app.managers.append(m)
        return
    # custom setup ends
    
    if app.buildings:
        if app.mode is app.twoD:
            osm.addCondition(
                lambda tags, e: "building" in tags,
                "buildings", 
                polygon
            )
        else: # 3D
            buildingParts = BuildingParts()
            buildingRelations = BuildingRelations()
            buildings = BuildingManager(osm, buildingParts)
            
            # Important: <buildingRelation> beform <building>,
            # since there may be a tag building=* in an OSM relation of the type 'building'
            osm.addCondition(
                lambda tags, e: isinstance(e, Building),
                None,
                buildingRelations
            )
            osm.addCondition(
                lambda tags, e: "building" in tags,
                "buildings",
                buildings
            )
            osm.addCondition(
                lambda tags, e: "building:part" in tags,
                None,
                buildingParts
            )
            buildings.setRenderer(
                BuildingRenderer(app)
            )
            app.managers.append(buildings)
    
    if app.highways or app.railways:
        osm.addCondition(tunnel)
    
    if app.highways:
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("motorway", "motorway_link"),
            "roads_motorway",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("trunk", "trunk_link"),
            "roads_trunk",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("primary", "primary_link"),
            "roads_primary",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("secondary", "secondary_link"),
            "roads_secondary",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("tertiary", "tertiary_link"),
            "roads_tertiary",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "unclassified",
            "roads_unclassified",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("residential", "living_street"),
            "roads_residential",
            wayManager
        )
        # footway to optimize the walk through conditions
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("footway", "path"),
            "paths_footway",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "service",
            "roads_service",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "pedestrian",
            "roads_pedestrian",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "track",
            "roads_track",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "steps",
            "paths_steps",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "cycleway",
            "paths_cycleway",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") == "bridleway",
            "paths_bridleway",
            wayManager
        )
        osm.addCondition(
            lambda tags, e: tags.get("highway") in ("road", "escape", "raceway"),
            "roads_other",
            wayManager
        )
    if app.railways:
        osm.addCondition(
            lambda tags, e: "railway" in tags,
            "railways",
            wayManager
        )
    if app.water:
        osm.addCondition(
            lambda tags, e: tags.get("natural") == "water" or tags.get("waterway") == "riverbank" or tags.get("landuse") == "reservoir",
            "water",
            polygonAcceptBroken
        )
        osm.addCondition(
            lambda tags, e: tags.get("natural") == "coastline",
            "coastlines",
            linestring
        )
    if app.forests:
        osm.addCondition(
            lambda tags, e: tags.get("natural") == "wood" or tags.get("landuse") == "forest",
            "forest",
            polygon
        )
    if app.vegetation:
        osm.addCondition(
            lambda tags, e: ("landuse" in tags and tags["landuse"] in ("grass", "meadow", "farmland")) or ("natural" in tags and tags["natural"] in ("scrub", "grassland", "heath")),
            "vegetation",
            polygon
        )
    
    numConditions = len(osm.conditions)
    if not app.mode is app.twoD and app.buildings:
        # 3D buildings aren't processed by BaseManager
        numConditions -= 1
    if numConditions:
        m = BaseManager(osm)
        m.setRenderer(Renderer2d(app))
        m.setNodeRenderer(wt_renderer)
        app.managers.append(m)