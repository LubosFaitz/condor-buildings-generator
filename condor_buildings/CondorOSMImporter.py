bl_info = {
    "name": "Condor OSM importer",
    "author": "Wiek Schoenmakers & Uros Bergant",
    "version": (0, 2, 2),
    "blender": (4, 0, 2),
    "location": "Sidebar menu",
    "description": "Imports BlenderOSM data specifically for Condor sceneries",
    "warning": "",
    "wiki_url": "www.condorsoaring.com",
    "support": 'COMMUNITY',
    "category": "Import-Export"}

import bpy
from pathlib import Path
import os
import bmesh
import mathutils
from bpy.props import (StringProperty,
                       BoolProperty,
                       IntProperty,
                       FloatProperty,
                       FloatVectorProperty,
                       EnumProperty,
                       PointerProperty,
                       )
from bpy.types import (Panel,
                       Operator,
                       AddonPreferences,
                       PropertyGroup,
                       )
#Uros' custom obj exporter
def saveOBJ(context, filepath):
    path = os.path.splitext(filepath)[0]
    name = os.path.splitext(os.path.basename(filepath))[0]

    #checks if there were buildings imported
    
    # Write MTL file
    filename = path + ".mtl"
    with open(filename, 'w') as f:
        f.write("# Blender 2.80 custom Condor exporter v1.0\n")
        for obj in bpy.context.selected_objects:
            if obj.active_material is not None:
                material = obj.active_material
                for node in material.node_tree.nodes:
                    if node.type == 'BSDF_PRINCIPLED':
                        inputs = material.node_tree.nodes["Principled BSDF"].inputs
                        Ns = inputs[2].default_value #Roughness
                        Kd = inputs[0].default_value #Base Color
                        Ks = inputs[13].default_value #Specular
                        Ke = inputs[26].default_value #Emissive
                        Ni = inputs[3].default_value #Refraction depth
                        d = inputs[4].default_value #Alpha
                        f.write("newmtl " + material.name + "\n")
                        f.write("Ns %.6f\n" % Ns) #Roughness Spec
                        f.write("Ka 1.0 1.0 1.0\n") #Not used
                        f.write("Kd %.6f %.6f %.6f\n" % Kd[:3])  # Diffuse Red Green Blue
                        f.write("Ks %.6f %.6f %.6f\n" % Ks[:3])  # Specular Spec
                        f.write("Ke %.6f %.6f %.6f\n" % Ke[:3]) # Emissive  Env
                        f.write("Ni %.6f\n" % Ni)  # Refraction depth Not used
                        f.write("d %.6f\n" % d)  # Alpha (obj uses 'd' for dissolve)
                        f.write("illum 2\n") 

                        try:
                            principled = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
                            base_color = principled.inputs['Base Color']
                            img_name = base_color.links[0].from_node.image.name
                            import re
                            img_name_clean = re.sub(r'\.\d{3}$', '', img_name)
                            f.write("map_Kd " + img_name_clean + "\n\n")
                        except:
                            f.write("\n")
                
    # Write OBJ file
    filename = path + ".obj"
    with open(filename, 'w') as f:
        f.write("# Blender 2.80 custom Condor exporter v1.0\n")
        f.write("mtllib " + name + ".mtl\n")
        i = 1 # Indices
        for obj in bpy.data.objects: 
            if obj.type == 'MESH' and obj.visible_get(): 
                mesh = obj.data

                mesh.calc_loop_triangles()
                mesh.calc_normals_split()
                                
                if len(mesh.uv_layers) > 0:
                    uv_layer = mesh.uv_layers[0].data
                else:
                    uv_layer = None

                f.write("o " + obj.name + "\n")
            
                if len(mesh.materials) > 0 and mesh.materials[0] != None:
                    f.write("usemtl " + mesh.materials[0].name + "\n")

                for tri in mesh.loop_triangles:
                    for iTri in range(0, 3):                    
                        vertex = obj.matrix_world @ mesh.vertices[tri.vertices[iTri]].co
                        normal = tri.split_normals[iTri]
                        normal = mathutils.Vector((normal[0], normal[1], normal[2]))
                        mat_rot = obj.matrix_world.to_3x3().normalized()
                        normal = mat_rot @ normal
                        # Write vertex coordinates, normals and texture coordinates
                        f.write("v %.8f %.8f %.8f\n" % vertex[:])
                        f.write("vn %.8f %.8f %.8f\n" % normal[:])
                        if uv_layer:
                            f.write("vt %.8f %.8f\n" % uv_layer[tri.loops[iTri]].uv[:])
                        else:
                            f.write("vt 0.0 0.0\n")
                    # Write faces
                    f.write("f %d/%d/%d %d/%d/%d %d/%d/%d\n" % (i, i, i, i + 1, i + 1, i + 1, i + 2, i + 2, i + 2))
                    i += 3
    return {'FINISHED'}

def PatchViewerImport():
    print('Importing Patch')
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)

    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
    
    # iterate patches
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):  
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                patch = str(i).zfill(2) + str(j).zfill(2) 
            else:
                patch = str(i).zfill(3) + str(j).zfill(3) 
            print("Processing patch: " + patch)
            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')
            autogenPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            TexturePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Textures')
            TextureName = Path('t' + patch + '.dds')
            TexturePathBMP = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Textures')
            TextureNameBMP = Path('t' + patch + '.bmp')
            if bpy.context.scene.Condor_OSM.Import_BMP_textures == False:
                ImportBMPTextures = 0
            else:
                ImportBMPTextures = 1

            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])
            
            #sets some variables
            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
                     
            # imports terrain from file
            file_loc = os.path.join(landscapePath, TileName)
            imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
            obj_object = bpy.context.selected_objects[0] 
            print('Terrain imported with name: ', obj_object.name)
            for obj in bpy.context.selected_objects:
                obj.name = patch

            #UVmaps Patch               
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.cube_project(cube_size=1.0, correct_aspect=True, clip_to_bounds=False, scale_to_bounds=True)
            bpy.ops.object.editmode_toggle()

            #adds image to shader editor
            ob = bpy.context.active_object
            mat = bpy.data.materials.get("Material")
            if mat is None:
                mat = bpy.data.materials.new(name="Material")
            if ob.data.materials:
                ob.data.materials[0] = mat
            else:
                ob.data.materials.append(mat)
            mat.use_nodes=True

            #loads texture
            if ImportBMPTextures == 0:
                file_loc = os.path.join(TexturePath, TextureName)
                bsdf = mat.node_tree.nodes["Principled BSDF"]
                texImage = mat.node_tree.nodes.new('ShaderNodeTexImage')
                texImage.image = bpy.data.images.load(file_loc)
                mat.node_tree.links.new(bsdf.inputs['Base Color'], texImage.outputs['Color'])
            if ImportBMPTextures == 1:
                file_loc = os.path.join(TexturePathBMP, TextureNameBMP)
                bsdf = mat.node_tree.nodes["Principled BSDF"]
                texImage = mat.node_tree.nodes.new('ShaderNodeTexImage')
                texImage.image = bpy.data.images.load(file_loc)
                mat.node_tree.links.new(bsdf.inputs['Base Color'], texImage.outputs['Color'])
                        
            #imports autogen object
            autogenName = 'o' + patch + '.obj'
            file_loc = os.path.join(autogenPath, autogenName)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='X', up_axis='Z')
                if len(bpy.context.selected_objects) > 0:
                    obj_object = bpy.context.selected_objects[0] 
                print('Imported autogen')
                
                # OPRAVA RÙŽOVÝCH TEXTUR PØI NAÈÍTÁNÍ
                import re
                for mat in bpy.data.materials:
                    if mat.node_tree:
                        for node in mat.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                orig_name = re.sub(r'\.\d{3}$', '', node.image.name)
                                if orig_name in bpy.data.images and orig_name != node.image.name:
                                    node.image = bpy.data.images[orig_name]
            else:
                print ('Autogen not found')

def PatchViewerExport():
    #sets some variables
    print('Exporting Patch')
    if bpy.context.scene.Condor_OSM.UseNewFormat == False:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
    else:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
    #deletes terrain
    bpy.ops.object.select_all(action='DESELECT')
    bpy.data.objects[Terrpatch1].select_set(True) 
    bpy.ops.object.delete()    
    
    #some more variables
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
    if bpy.context.scene.Condor_OSM.Export_Default_Material == False:
        UseDefaultMat = 0
    else:
        UseDefaultMat = 1
    
    #itterates patches
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):   
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                patch = str(i).zfill(2) + str(j).zfill(2) 
            else:
                patch = str(i).zfill(3) + str(j).zfill(3) 
            
            print("Processing patch: " + patch)

            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')

            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
            
    #Sets material properties
    if UseDefaultMat == 1:
        for mat in bpy.data.materials:
            if hasattr(mat.node_tree, "nodes"):
                for node in mat.node_tree.nodes:
                    if node.type == 'BSDF_PRINCIPLED':
                        for input in node.inputs:
                            if input.name == 'Roughness':
                                input.default_value = 0.0
                            if input.name == 'Base Color':
                                input.default_value = (1.0, 1.0, 1.0, 1.0)
                            if input.name == 'Specular':
                                input.default_value = 0.0
                            if input.name == 'Emission Color':
                                input.default_value = (0.0, 0.0, 0.0, 0.0)
                            if input.name == 'Emission Strength':
                                input.default_value = 0.0
                            if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                                if input.name == 'Specular Tint':
                                    input.default_value = (0.0, 0.0, 0.0, 0.0)

    #exports autogen en purges the file
    target_file = os.path.join(ExportPath, ObjectName)
    bpy.ops.object.select_all(action='SELECT')
    bpy.context.scene.cursor.location = (0,0,0)
    bpy.context.scene.transform_orientation_slots[0].type = 'GLOBAL'
    bpy.context.scene.tool_settings.transform_pivot_point = 'CURSOR'
    if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
        UseLegacyExp = 0
    else:
        UseLegacyExp = 1
    if UseLegacyExp == 1:
        bpy.ops.export_scene.obj(filepath=target_file, use_normals=True, use_uvs=True, use_materials=True, use_triangles=True, path_mode='COPY', axis_forward='X', axis_up='Z', use_selection=True)
    if UseLegacyExp == 0:
        bpy.ops.transform.rotate(value=1.5708, orient_axis='Z', orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        saveOBJ(1, target_file)
    bpy.ops.object.delete(use_global=False, confirm=False)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
 
def PatchViewerExportTerrain():
    #exports just the terrain to .obj but leaves everything in place
    print('exporting TR3 to .obj')
    if bpy.context.scene.Condor_OSM.UseNewFormat == False:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
    else:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.data.objects[Terrpatch1].select_set(True)
    
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
    
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):   
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                patch = str(i).zfill(2) + str(j).zfill(2) 
            else:
                patch = str(i).zfill(3) + str(j).zfill(3) 
            
            print("Processing patch: " + patch)

            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')
            
            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\HeightMaps', 'Modified')
            ObjectName = Path('h' + patch + '.obj')

    target_file = os.path.join(ExportPath, ObjectName)
    if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
        UseLegacyExp = 0
    else:
        UseLegacyExp = 1
    if UseLegacyExp == 1:
        bpy.ops.export_scene.obj(filepath=target_file, use_normals=True, use_uvs=True, use_materials=True, use_triangles=True, path_mode='COPY', axis_forward='Y', axis_up='Z', use_selection=True)
    else:
        saveOBJ(1, target_file)

def PowerlineImporter():
    #imports powerline locations
    print('Importing Powerlines')
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
    XpatchNo = 0
    YpatchNo = 0

    # iterate patches
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):   
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                patch = str(i).zfill(2) + str(j).zfill(2) 
                patch2 = str(i + 1).zfill(2) + str(j).zfill(2)
                patch3 = str(i).zfill(2) + str(j + 1).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
                patch2 = str(i + 1).zfill(3) + str(j).zfill(3)
                patch3 = str(i).zfill(3) + str(j + 1).zfill(3) 
 
            
            print("Processing patch: " + patch)
                    
            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')
            autogenPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            TexturePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Textures')
            TextureName = Path('t' + patch + '.dds')

            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
            
            #Populates Blender-OSM window and downloads data        
            bpy.context.scene.blosm.maxLat = maxLat
            bpy.context.scene.blosm.maxLon = maxLon
            bpy.context.scene.blosm.minLon = minLon
            bpy.context.scene.blosm.minLat = minLat
            bpy.context.scene.blosm.mode = '3Dsimple'
            bpy.context.scene.blosm.buildings = False
            bpy.context.scene.blosm.water = False
            bpy.context.scene.blosm.forests = False
            bpy.context.scene.blosm.vegetation = False
            bpy.context.scene.blosm.highways = False
            bpy.context.scene.blosm.railways = False
            bpy.context.scene.blosm.singleObject = False
            bpy.context.scene.blosm.ignoreGeoreferencing = False
            bpy.context.scene.blosm.setupScript = os.path.join(os.path.dirname(os.path.realpath(__file__)), "power_lines_and_towers.py")
            bpy.context.scene['importing_chimneys'] = 0

            bpy.ops.blosm.import_data()
          
            #Converts imported data to BezierCurves and adds shrinkwrap modifiers
            PowerlineCheck = len (bpy.context.window.scene.objects)
            if PowerlineCheck <= 20: #checks if powerlines are found
                print ("No powerlines found in this patch")
                break
            bpy.ops.object.select_by_type(extend=False, type='EMPTY')
            bpy.ops.object.delete(use_global=False, confirm=False)
            obj = None
            for o in bpy.context.view_layer.objects:
                if o.type == 'MESH' and o.name not in ('Komin Velky', 'KominMaly'):
                    obj = o
                    break
            if obj is None:
                print("No powerlines found in this patch")
                break
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.convert(target='CURVE')
            bpy.ops.object.editmode_toggle()
            bpy.ops.curve.spline_type_set(type='BEZIER')
            bpy.ops.object.editmode_toggle()
                               
            # imports terrain from file
            file_loc = os.path.join(landscapePath, TileName)
            imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
            obj_object = bpy.context.selected_objects[0] 
            print('Imported name: ', obj_object.name)
            for obj in bpy.context.selected_objects:
                obj.name = patch
                
            #UVmaps Patch               
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.cube_project(cube_size=1.0, correct_aspect=True, clip_to_bounds=False, scale_to_bounds=True)
            bpy.ops.object.editmode_toggle()

            #adds image to shader editor
            ob = bpy.context.active_object
            mat = bpy.data.materials.get("Material")
            if mat is None:
                mat = bpy.data.materials.new(name="Material")
            if ob.data.materials:
                ob.data.materials[0] = mat
            else:
                ob.data.materials.append(mat)
            mat.use_nodes=True

            #loads texture    
            file_loc = os.path.join(TexturePath, TextureName)
            bsdf = mat.node_tree.nodes["Principled BSDF"]
            texImage = mat.node_tree.nodes.new('ShaderNodeTexImage')
            texImage.image = bpy.data.images.load(file_loc)
            mat.node_tree.links.new(bsdf.inputs['Base Color'], texImage.outputs['Color'])
            

            #loads additional patches (top and left)
            TileName = 'h' + patch2 + '.obj'
            file_loc = os.path.join(landscapePath, TileName)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
                obj_object = bpy.context.selected_objects[0] 
                print('Imported name: ', obj_object.name)
                bpy.ops.transform.translate(value=(-5760, 0, 0), orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)), orient_matrix_type='GLOBAL', constraint_axis=(False, True, False), mirror=False, use_proportional_edit=False, proportional_edit_falloff='SMOOTH', proportional_size=1, use_proportional_connected=False, use_proportional_projected=False)
                for obj in bpy.context.selected_objects:
                    obj.name = patch2
            else:
                print("patch" + TileName + "not found")
                XpatchNo = 1
                            
            TileName = 'h' + patch3 + '.obj'
            file_loc = os.path.join(landscapePath, TileName)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
                obj_object = bpy.context.selected_objects[0] 
                print('Imported name: ', obj_object.name)
                bpy.ops.transform.translate(value=(0, 5760, -0), orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)), orient_matrix_type='GLOBAL', constraint_axis=(True, False, False), mirror=False, use_proportional_edit=False, proportional_edit_falloff='SMOOTH', proportional_size=1, use_proportional_connected=False, use_proportional_projected=False)
                for obj in bpy.context.selected_objects:
                    obj.name = patch3
            else:
                print("patch" + TileName + "not found")
                YpatchNo = 1
                
            #adds terrain patch names to shrinkwrap modifiers and applies them
            #imports dummy object
            Cube = "Cube"
            bpy.ops.mesh.primitive_cube_add(size=2, enter_editmode=False, align='WORLD', location=(0, 0, 0), scale=(1, 1, 1))
            obj_object = bpy.context.selected_objects[0]
            for obj in bpy.context.selected_objects:
                obj.name = Cube
            #Adds Shrinkwrap modifiers
            bpy.ops.object.select_all(action='DESELECT')
            bpy.ops.object.select_by_type(type='CURVE')
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_add(type='SHRINKWRAP')
            bpy.ops.object.make_links_data(type='MODIFIERS')
            bpy.context.object.modifiers["Shrinkwrap"].wrap_method = 'PROJECT'
            bpy.context.object.modifiers["Shrinkwrap"].use_project_z = True
            bpy.context.object.modifiers["Shrinkwrap"].use_negative_direction = True
            bpy.context.object.modifiers["Shrinkwrap"].use_positive_direction = True
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
                Terrpatch2 = str((bpy.context.scene.Condor_OSM.Patch_X_Max) + 1).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
                Terrpatch3 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str((bpy.context.scene.Condor_OSM.Patch_Y_Max) + 1).zfill(2)
            else:
                Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
                Terrpatch2 = str((bpy.context.scene.Condor_OSM.Patch_X_Max) + 1).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
                Terrpatch3 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str((bpy.context.scene.Condor_OSM.Patch_Y_Max) + 1).zfill(3)
            bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[Terrpatch1]
            
            #Checks for cases on the edges of the scenery
            if XpatchNo == 1:
                if YpatchNo == 0:
                    bpy.context.object.modifiers["Shrinkwrap"].auxiliary_target = bpy.data.objects[Terrpatch3]
                    bpy.ops.object.make_links_data(type='MODIFIERS')
                    for ob in bpy.context.selected_objects:
                        bpy.context.view_layer.objects.active = ob
                        for mod in [m for m in ob.modifiers if m.type == 'SHRINKWRAP']:
                            bpy.ops.object.modifier_apply( modifier = 'Shrinkwrap')
            if XpatchNo == 0:
                print ("im in the XpatchNo = 0 if")
                if YpatchNo == 1:
                    bpy.context.object.modifiers["Shrinkwrap"].auxiliary_target = bpy.data.objects[Terrpatch2]
                    bpy.ops.object.make_links_data(type='MODIFIERS')
                    for ob in bpy.context.selected_objects:
                        bpy.context.view_layer.objects.active = ob
                        for mod in [m for m in ob.modifiers if m.type == 'SHRINKWRAP']:
                            bpy.ops.object.modifier_apply( modifier = 'Shrinkwrap')
                else:
                    bpy.context.object.modifiers["Shrinkwrap"].auxiliary_target = bpy.data.objects[Terrpatch2]
                    bpy.ops.object.make_links_data(type='MODIFIERS')
                    for ob in bpy.context.selected_objects:
                        bpy.context.view_layer.objects.active = ob
                        for mod in [m for m in ob.modifiers if m.type == 'SHRINKWRAP']:
                            bpy.ops.object.modifier_apply( modifier = 'Shrinkwrap')
                    bpy.ops.object.select_by_type(extend=False, type='CURVE') 
                    bpy.ops.object.modifier_add(type='SHRINKWRAP')
                    bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[Terrpatch3]
                    bpy.context.object.modifiers["Shrinkwrap"].wrap_method = 'PROJECT'
                    bpy.context.object.modifiers["Shrinkwrap"].use_project_z = True
                    bpy.context.object.modifiers["Shrinkwrap"].use_negative_direction = True
                    bpy.context.object.modifiers["Shrinkwrap"].use_positive_direction = True
                    bpy.ops.object.make_links_data(type='MODIFIERS')
                    bpy.ops.object.select_by_type(extend=False, type='CURVE')   
                    for ob in bpy.context.selected_objects:
                        bpy.context.view_layer.objects.active = ob
                        for mod in [m for m in ob.modifiers if m.type == 'SHRINKWRAP']:
                            bpy.ops.object.modifier_apply(modifier = 'Shrinkwrap')
            if XpatchNo == 1:
                if YpatchNo == 1:
                    bpy.context.object.modifiers["Shrinkwrap"].target = bpy.data.objects[Terrpatch1]
                    bpy.ops.object.make_links_data(type='MODIFIERS')
                    for ob in bpy.context.selected_objects:
                        bpy.context.view_layer.objects.active = ob
                        for mod in [m for m in ob.modifiers if m.type == 'SHRINKWRAP']:
                            bpy.ops.object.modifier_apply(modifier = 'Shrinkwrap')
            
            # Pripnuti vetrnych elektraren na teren a smazani duplikatu
            wt_objects = []
            for ob_wt in bpy.context.view_layer.objects:
                if ob_wt.name.startswith("WindTurbine") and ob_wt.type == 'MESH':
                    mod = ob_wt.modifiers.new("Shrinkwrap", 'SHRINKWRAP')
                    mod.wrap_method = 'PROJECT'
                    mod.use_project_z = True
                    mod.use_negative_direction = True
                    mod.use_positive_direction = True
                    mod.target = bpy.data.objects[Terrpatch1]
                    bpy.context.view_layer.objects.active = ob_wt
                    bpy.ops.object.modifier_apply(modifier='Shrinkwrap')
                    wt_objects.append(ob_wt)

            if wt_objects:
                bpy.ops.object.select_all(action='DESELECT')
                for obj in wt_objects:
                    obj.select_set(True)
                bpy.context.view_layer.objects.active = wt_objects[0]
                bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
                
                seen_locations = set()
                objects_to_delete = []
                for obj in wt_objects:
                    loc = (round(obj.location.x, 3), round(obj.location.y, 3), round(obj.location.z, 3))
                    if loc in seen_locations:
                        objects_to_delete.append(obj)
                    else:
                        seen_locations.add(loc)
                
                bpy.ops.object.select_all(action='DESELECT')
                for obj in objects_to_delete:
                    obj.select_set(True)
                if objects_to_delete:
                    bpy.ops.object.delete(use_global=False, confirm=False)

            #Removes dummy object
            bpy.ops.object.select_all(action='DESELECT')
            bpy.data.objects['Cube'].select_set(True)
            bpy.ops.object.delete(use_global=False, confirm=False)

            #Adds Geometry node modifiers

            #Adds Geometry node modifiers
            bpy.ops.object.select_by_type(extend=False, type='CURVE') 
            bpy.ops.object.modifier_add(type='NODES')
            bpy.ops.object.make_links_data(type='MODIFIERS')
            
            #Scales nodes to 0
            bpy.ops.object.editmode_toggle()
            bpy.context.scene.tool_settings.transform_pivot_point = 'INDIVIDUAL_ORIGINS'
            context = bpy.context
            override = context.copy()

            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    override["area"] = area
                    override["space_data"] = area.spaces.active
                    override["region"] = area.regions[-1]
                    break

            bpy.ops.transform.resize(value=(0, 0, 0))
            bpy.ops.object.editmode_toggle()

            #imports autogen object
            autogenName = 'o' + patch + '.obj'
            file_loc = os.path.join(autogenPath, autogenName)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='X', up_axis='Z')
                obj_object = bpy.context.selected_objects[0] 
                print('Imported autogen')
            else:
                print ('Autogen not found')
                
            bpy.ops.object.select_by_type(extend=False, type='CURVE')   


def ChimneyImporter():
    print('Importing Chimneys')
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax

    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                patch = str(i).zfill(2) + str(j).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
            print("Processing patch for chimneys: " + patch)
            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            # Zadej cestu k tvemu velkemu OSM souboru
            osm_zdroj = r"C:\Users\lubos\Desktop\TestServer\kominy.osm"
            osm_docasny = r"C:\Users\lubos\Desktop\TestServer\docasny_komin.osm"

            import xml.etree.ElementTree as ET
            try:
                with open(osm_docasny, 'wb') as f_out:
                    f_out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="CondorFilter">\n')
                    # Postupne cteni bez zateze pameti
                    for event, elem in ET.iterparse(osm_zdroj, events=('end',)):
                        if elem.tag == 'node':
                            lat = float(elem.get('lat', 0))
                            lon = float(elem.get('lon', 0))
                            if minLon <= lon <= maxLon and minLat <= lat <= maxLat:
                                f_out.write(ET.tostring(elem, encoding='utf-8'))
                            elem.clear() # Okamzite uvolni z pameti
                    f_out.write(b'</osm>\n')
                
                bpy.context.scene.blosm.dataType = 'osm'
                bpy.context.scene.blosm.dataObj = 'file'
                bpy.context.scene.blosm.osmFilepath = osm_docasny
            except Exception as e:
                print("Chyba orezani OSM:", e)

            bpy.context.scene.blosm.maxLat = maxLat
            bpy.context.scene.blosm.maxLon = maxLon
            bpy.context.scene.blosm.minLon = minLon
            bpy.context.scene.blosm.minLat = minLat
            bpy.context.scene.blosm.mode = '3Dsimple'
            bpy.context.scene.blosm.buildings = False
            bpy.context.scene.blosm.water = False
            bpy.context.scene.blosm.forests = False
            bpy.context.scene.blosm.vegetation = False
            bpy.context.scene.blosm.highways = False
            bpy.context.scene.blosm.railways = False
            bpy.context.scene.blosm.singleObject = False
            bpy.context.scene.blosm.ignoreGeoreferencing = False
            bpy.context.scene.blosm.setupScript = os.path.join(os.path.dirname(os.path.realpath(__file__)), "power_lines_and_towers.py")
            bpy.context.scene['importing_chimneys'] = 1
            bpy.ops.blosm.import_data()
    
    # Po importu - vytvor NTChimney a pridej modifikatory
    ChimneySetupModifiers()


def ChimneySetupModifiers():
    """Vytvori NTChimney node group a prida modifikator na vsechny Chimney_xxx objekty."""
    print('Setting up chimney modifiers')
    
    if "NTChimney" in bpy.data.node_groups:
        bpy.data.node_groups.remove(bpy.data.node_groups["NTChimney"])
    
    ng = bpy.data.node_groups.new("NTChimney", 'GeometryNodeTree')
    ng.inputs.new('NodeSocketGeometry', "Geometry")
    ng.inputs.new('NodeSocketObject', "Model")
    ng.inputs.new('NodeSocketFloat', "Scale")
    ng.outputs.new('NodeSocketGeometry', "Geometry")
    ng.inputs['Scale'].default_value = 1.0
    
    gi = ng.nodes.new('NodeGroupInput')
    go = ng.nodes.new('NodeGroupOutput')
    oi = ng.nodes.new('GeometryNodeObjectInfo')
    oi.inputs['As Instance'].default_value = True
    cx = ng.nodes.new('ShaderNodeCombineXYZ')
    iop = ng.nodes.new('GeometryNodeInstanceOnPoints')
    ri = ng.nodes.new('GeometryNodeRealizeInstances')
    
    L = ng.links
    L.new(gi.outputs[0], iop.inputs['Points'])
    L.new(gi.outputs[1], oi.inputs['Object'])
    L.new(oi.outputs['Geometry'], iop.inputs['Instance'])
    L.new(gi.outputs[2], cx.inputs[0])
    L.new(gi.outputs[2], cx.inputs[1])
    L.new(gi.outputs[2], cx.inputs[2])
    L.new(cx.outputs[0], iop.inputs['Scale'])
    L.new(iop.outputs['Instances'], ri.inputs['Geometry'])
    L.new(ri.outputs['Geometry'], go.inputs[0])
    
    maly = bpy.data.objects.get("KominMaly")
    velky = bpy.data.objects.get("Komin Velky")
    
    # Zjisteni nazvu aktualniho terenu (podle toho, ktery format se zrovna pouziva)
    if bpy.context.scene.Condor_OSM.UseNewFormat == False:
        terrain_name = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
    else:
        terrain_name = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
        
    terrain_obj = bpy.data.objects.get(terrain_name)

    n = 0
    for obj in bpy.data.objects:
        if obj.name.startswith("Chimney_"):
            # Musime hledat osm_name, protoze tak to Blosm uklada
            if "osm_name" in obj:
                obj.name = str(obj["osm_name"])
            elif "name" in obj:
                obj.name = str(obj["name"])
                
            for m in list(obj.modifiers):
                obj.modifiers.remove(m)
                
            # Prilepeni bodu ciste a jenom k terenu (ignoruje budovy)
            if terrain_obj:
                sw_mod = obj.modifiers.new("Shrinkwrap", 'SHRINKWRAP')
                sw_mod.wrap_method = 'PROJECT'
                sw_mod.use_project_z = True
                sw_mod.use_negative_direction = True
                sw_mod.use_positive_direction = True
                sw_mod.target = terrain_obj

            mod = obj.modifiers.new("ChimneyModel", 'NODES')
            mod.node_group = ng
            h = obj.get("height", 10.0)
            if h >= 31:
                mod["Input_1"] = velky
                mod["Input_2"] = h / 100.0
            else:
                mod["Input_1"] = maly
                mod["Input_2"] = h / 30.0
            obj.update_tag()
            n += 1
    print(f"DONE: {n} kominu nastaveno")


def ChimneyApplyModifiers():
    print('Applying chimney modifiers')
    # Definujeme, ze nas zajimaji jen objekty v kolekci Chimneys
    chimney_coll = bpy.data.collections.get("Chimneys")
    
    if not chimney_coll:
        print("Kolekce 'Chimneys' nenalezena")
        return

    # Vezmeme POUZE to, co jsi rucne oznacil (vybral) a zaroven to patri do kolekce Chimneys
    selected_chimneys = [obj for obj in bpy.context.selected_objects if obj.name in chimney_coll.objects]
    
    if not selected_chimneys:
        print("Zadne OZNACENE kominy v kolekci Chimneys nenalezeny. Vyber kominy, ktere chces upravit.")
        return

    # Provedeme prevod jen na tom, co jsi vybral
    bpy.context.view_layer.objects.active = selected_chimneys[0]
    bpy.ops.object.convert(target='MESH')
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    
    print(f"Hotovo: Aplikovano na {len(selected_chimneys)} oznacenych kominu")
    bpy.ops.object.select_all(action='DESELECT')
    for c in selected_chimneys:
        c.select_set(True)
    bpy.context.view_layer.objects.active = selected_chimneys[0]
    bpy.ops.object.convert(target='MESH')
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    print(f"Aplikovano na {len(selected_chimneys)} kominu")


def PowerlineExporter():
    #exports autogen with powerlines
    print('Exporting Powerlines')
    #makes sure right terrain patches are selected for deletion
    if bpy.context.scene.Condor_OSM.UseNewFormat == False:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
        Terrpatch2 = str((bpy.context.scene.Condor_OSM.Patch_X_Max) + 1).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
        Terrpatch3 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str((bpy.context.scene.Condor_OSM.Patch_Y_Max) + 1).zfill(2)
    else:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
        Terrpatch2 = str((bpy.context.scene.Condor_OSM.Patch_X_Max) + 1).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
        Terrpatch3 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str((bpy.context.scene.Condor_OSM.Patch_Y_Max) + 1).zfill(3)
    
    #deletes terrain patches from file
    bpy.ops.object.select_all(action='DESELECT')
    for o in bpy.context.scene.objects:
        if o.name == Terrpatch1:
            bpy.data.objects[Terrpatch1].select_set(True)
    for p in bpy.context.scene.objects:
        if p.name == Terrpatch2:
            bpy.data.objects[Terrpatch2].select_set(True)        
    for q in bpy.context.scene.objects:
        if q.name == Terrpatch3:
            bpy.data.objects[Terrpatch3].select_set(True)
    bpy.ops.object.delete()    
    
    #sets some variables
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
     
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:
                patch = str(i).zfill(2) + str(j).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
            print("Processing patch: " + patch)
            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')

            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
    
    #sets materials for export (ignores Use default material setting)
    for mat in bpy.data.materials:
        if hasattr(mat.node_tree, "nodes"):
            for node in mat.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    for input in node.inputs:
                        if input.name == 'Roughness':
                            input.default_value = 0.0
                        if input.name == 'Base Color':
                            input.default_value = (1.0, 1.0, 1.0, 1.0)
                        if input.name == 'Specular':
                            input.default_value = 0.0
                        if input.name == 'Emission Color':
                            input.default_value = (0.0, 0.0, 0.0, 0.0)
                        if input.name == 'Emission Strength':
                            input.default_value = 0.0
                        if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                            if input.name == 'Specular Tint':
                                input.default_value = (0.0, 0.0, 0.0, 0.0)     
    #exports autogen and purges file
    target_file = os.path.join(ExportPath, ObjectName)
    bpy.ops.object.select_all(action='SELECT')
    if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
        UseLegacyExp = 0
    else:
        UseLegacyExp = 1
    if UseLegacyExp == 1:
        bpy.ops.export_scene.obj(filepath=target_file, use_normals=True, use_uvs=True, use_materials=True, use_triangles=True, path_mode='COPY', axis_forward='X', axis_up='Z', use_selection=True)
    if UseLegacyExp == 0:
        bpy.ops.transform.rotate(value=1.5708, orient_axis='Z', orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        saveOBJ(1, target_file)
    bpy.ops.object.delete(use_global=False, confirm=False)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)

def ConvertAttribute():
    bpy.ops.geometry.attribute_convert(mode='GENERIC',domain='CORNER',data_type='FLOAT2')

def ImportOSMData():
    #imports OSM data and saves it to .obj
    print('Importing OSM data')
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = bpy.context.scene.Condor_OSM.Patch_X_Min
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = bpy.context.scene.Condor_OSM.Patch_Y_Min
    if bpy.context.scene.Condor_OSM.Export_Default_Objects == False:
        UseDefaultObjects = 0
    else:
        UseDefaultObjects = 1
    
    # iterate patches
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:   
                patch = str(i).zfill(2) + str(j).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
            
            print("Processing patch: " + patch)
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')

            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
            
            # imports terrain from file
            file_loc = os.path.join(landscapePath, TileName)
            imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
            obj_object = bpy.context.selected_objects[0] 
            print('Imported name: ', obj_object.name)
            for obj in bpy.context.selected_objects:
                obj.name = 'Terrain'
                        
            # sets values in Blender-OSM and imports data from server
            bpy.context.scene.blosm.maxLat = maxLat
            bpy.context.scene.blosm.maxLon = maxLon
            bpy.context.scene.blosm.minLon = minLon
            bpy.context.scene.blosm.minLat = minLat
            bpy.context.scene.blosm.mode = '3Drealistic'
            if UseDefaultObjects == 1:
                bpy.context.scene.blosm.assetPackage = 'CondorV3'
            bpy.context.scene.blosm.importForExport = True
            bpy.context.scene.blosm.buildings = True
            bpy.context.scene.blosm.singleObject = True
            bpy.context.scene.blosm.forests = False
            bpy.context.scene.blosm.ignoreGeoreferencing = True
            bpy.context.scene.blosm.setupScript = ""
            bpy.context.scene.blosm.terrainObject = obj.name
            bpy.context.scene.blosm.highways = False
            
            bpy.ops.blosm.import_data()

            # deletes Terrain which is not needed for export
            bpy.data.objects["Terrain"].select_set(True) 
            bpy.ops.object.delete()

            # separates object by material
            bpy.ops.object.select_all(action='SELECT')
            sel = bpy.context.selected_objects
            for obj in sel:
                if obj.type == 'MESH':
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.editmode_toggle()
                    bpy.ops.mesh.separate(type='MATERIAL')
                    bpy.ops.object.editmode_toggle()
            
            #Sets material properties (ignores use default material settings)
            for mat in bpy.data.materials:
                if hasattr(mat.node_tree, "nodes"):
                    for node in mat.node_tree.nodes:
                        if node.type == 'BSDF_PRINCIPLED':
                            for input in node.inputs:
                                if input.name == 'Roughness':
                                    input.default_value = 0.0
                                if input.name == 'Base Color':
                                    input.default_value = (1.0, 1.0, 1.0, 1.0)
                                if input.name == 'Specular':
                                    input.default_value = 0.0
                                if input.name == 'Emission Color':
                                    input.default_value = (0.0, 0.0, 0.0, 0.0)
                                if input.name == 'Emission Strength':
                                    input.default_value = 0.0
                                if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                                    if input.name == 'Specular Tint':
                                        input.default_value = (0.0, 0.0, 0.0, 0.0)
            
            #Renames objects to a specific pattern so they can be found later
            autogenName = 'o' + patch
            for obj in bpy.context.selected_objects:
                obj.name = autogenName
                        
            #Rotates buildings to correct orientation (only using custom exporter)
            if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                bpy.ops.transform.rotate(value=1.5708, orient_axis='Z', orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
            
            # Exports .obj    
            target_file = os.path.join(ExportPath, ObjectName)
            if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                UseLegacyExp = 0
            else:
                UseLegacyExp = 1
            if UseLegacyExp == 1:
                bpy.ops.export_scene.obj(filepath=target_file, use_normals=True, use_uvs=True, use_materials=True, use_triangles=True, path_mode='COPY', axis_forward='X', axis_up='Z', use_selection=True)
            if UseLegacyExp == 0: 
                saveOBJ(1, target_file)
            
            # Deletes object after export ready for next tile
            bpy.ops.object.delete()

def ReplaceOSMData():
    print('Replacing OSM objects')
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = bpy.context.scene.Condor_OSM.Patch_X_Min
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = bpy.context.scene.Condor_OSM.Patch_Y_Min
    if bpy.context.scene.Condor_OSM.Export_Default_Objects == False:
        UseDefaultObjects = 0
    else:
        UseDefaultObjects = 1
    
    # iterate patches
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:    
                patch = str(i).zfill(2) + str(j).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
            
            print("Processing patch: " + patch)
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')
            autogenPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')

            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
            
            # imports terrain from file
            file_loc = os.path.join(landscapePath, TileName)
            imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
            obj_object = bpy.context.selected_objects[0] 
            print('Imported name: ', obj_object.name)
            for obj in bpy.context.selected_objects:
                obj.name = 'Terrain'
                
            # sets values in Blender-OSM and imports data from server
            bpy.context.scene.blosm.maxLat = maxLat
            bpy.context.scene.blosm.maxLon = maxLon
            bpy.context.scene.blosm.minLon = minLon
            bpy.context.scene.blosm.minLat = minLat
            bpy.context.scene.blosm.mode = '3Drealistic'
            if UseDefaultObjects == 1:
                bpy.context.scene.blosm.assetPackage = 'CondorV3'
            bpy.context.scene.blosm.importForExport = True
            bpy.context.scene.blosm.buildings = True
            bpy.context.scene.blosm.singleObject = True
            bpy.context.scene.blosm.forests = False
            bpy.context.scene.blosm.ignoreGeoreferencing = True
            bpy.context.scene.blosm.setupScript = ""
            bpy.context.scene.blosm.terrainObject = obj.name
            bpy.context.scene.blosm.highways = False

            #imports new OSM buildings
            bpy.ops.blosm.import_data()

            #imports old autogen object
            autogenName = 'o' + patch + '.obj'
            file_loc = os.path.join(autogenPath, autogenName)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='X', up_axis='Z')
                obj_object = bpy.context.selected_objects[0] 
                print('Imported autogen and deleted old buildings, but left powerlines and custom objects in place')
            else:
                print ('Autogen not found')

            #selects all named autogen objects for deletion
            bpy.ops.object.select_all(action='DESELECT')
            FindObject = 'o'+ patch
            for obj in bpy.context.scene.objects:
                if obj.name.startswith(FindObject):
                    obj.select_set(True)
            bpy.ops.object.delete()

           # deletes Terrain which is not needed for export
            bpy.data.objects["Terrain"].select_set(True) 
            bpy.ops.object.delete()

            # separates object by material
            for obj in bpy.context.scene.objects:
                    if obj.name.startswith("map"):
                         obj.select_set(True)
            sel = bpy.context.selected_objects
            for obj in sel:
                if obj.type == 'MESH':
                    bpy.context.view_layer.objects.active = obj
                    bpy.ops.object.editmode_toggle()
                    bpy.ops.mesh.separate(type='MATERIAL')
                    bpy.ops.object.editmode_toggle()
            
            #Sets material properties (ignores use default material settings)
            for mat in bpy.data.materials:
                if hasattr(mat.node_tree, "nodes"):
                    for node in mat.node_tree.nodes:
                        if node.type == 'BSDF_PRINCIPLED':
                            for input in node.inputs:
                                if input.name == 'Roughness':
                                    input.default_value = 0.0
                                if input.name == 'Base Color':
                                    input.default_value = (1.0, 1.0, 1.0, 1.0)
                                if input.name == 'Specular':
                                    input.default_value = 0.0
                                if input.name == 'Emission Color':
                                    input.default_value = (0.0, 0.0, 0.0, 0.0)
                                if input.name == 'Emission Strength':
                                    input.default_value = 0.0
                                if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                                    if input.name == 'Specular Tint':
                                        input.default_value = (0.0, 0.0, 0.0, 0.0)

            #Renames objects to a specific pattern so they can be found later
            autogenName = 'o' + patch
            for obj in bpy.context.selected_objects:
                obj.name = autogenName
            
            bpy.ops.object.select_all(action='SELECT')

            #Rotates buildings to correct orientation (only using custom exporter)
            if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                bpy.ops.transform.rotate(value=1.5708, orient_axis='Z', orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
            
            # Exports .obj    
            target_file = os.path.join(ExportPath, ObjectName)
            if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
                UseLegacyExp = 0
            else:
                UseLegacyExp = 1
            if UseLegacyExp == 1:
                bpy.ops.export_scene.obj(filepath=target_file, use_normals=True, use_uvs=True, use_materials=True, use_triangles=True, path_mode='COPY', axis_forward='X', axis_up='Z', use_selection=True)
            if UseLegacyExp == 0: 
                saveOBJ(1, target_file)

            # Deletes object after export ready for next tile
            bpy.ops.object.delete()
            
def ImportTR3F():
    #imports TR3F file for modification including autogen
    print('importing TR3F')
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)

    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
    Foldername = '22.5m'
    
    if bpy.context.scene.Condor_OSM.Import_From_Modified == False:
        ImportFromModified = 0
    else:
        ImportFromModified = 1
    
    # iterate patches
    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:    
                patch = str(i).zfill(2) + str(j).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
            
            print("Processing patch: " + patch)
            
            if ImportFromModified == 0:
                landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps', Foldername)
            if ImportFromModified == 1:
                landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps', Foldername, 'Modified')
            autogenPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            TexturePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Textures')
            TextureName = Path('t' + patch + '.dds')

            #sets some variables
            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Autogen')
            ObjectName = Path('o' + patch + '.obj')
            
            # imports terrain from file
            file_loc = os.path.join(landscapePath, TileName)
            print ('Importing from:' + file_loc)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
                obj_object = bpy.context.selected_objects[0] 
                print('Imported name: ', obj_object.name)
                for obj in bpy.context.selected_objects:
                    obj.name = patch
            else:
                print('patch not found')
                return {'FINISHED'}
            
            #UVmaps Patch               
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.cube_project(cube_size=1.0, correct_aspect=True, clip_to_bounds=False, scale_to_bounds=True)
            bpy.ops.object.editmode_toggle()
                                
            #adds image to shader editor
            ob = bpy.context.active_object
            mat = bpy.data.materials.get("Material")
            if mat is None:
                mat = bpy.data.materials.new(name="Material")
            if ob.data.materials:
                ob.data.materials[0] = mat
            else:
                ob.data.materials.append(mat)
            mat.use_nodes=True
            
            #loads texture    
            file_loc = os.path.join(TexturePath, TextureName)
            bsdf = mat.node_tree.nodes["Principled BSDF"]
            texImage = mat.node_tree.nodes.new('ShaderNodeTexImage')
            texImage.image = bpy.data.images.load(file_loc)
            mat.node_tree.links.new(bsdf.inputs['Base Color'], texImage.outputs['Color'])
                        
            #imports autogen object
            autogenName = 'o' + patch + '.obj'
            file_loc = os.path.join(autogenPath, autogenName)
            if os.path.isfile(file_loc):
                imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='X', up_axis='Z')
                if len(bpy.context.selected_objects) > 0:
                    obj_object = bpy.context.selected_objects[0] 
                print('Imported autogen')
                
                # OPRAVA RÙŽOVÝCH TEXTUR A DUPLICIT
                import re
                for mat in bpy.data.materials:
                    if mat.node_tree:
                        for node in mat.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                orig_name = re.sub(r'\.\d{3}$', '', node.image.name)
                                if orig_name in bpy.data.images and orig_name != node.image.name:
                                    node.image = bpy.data.images[orig_name]
            else:
                print ('Autogen not found')

def ExportTR3F():
    #exports just the terrain to TR3F modified but leaves everything in place
    print('exporting TR3F')

    if bpy.context.scene.Condor_OSM.UseNewFormat == False:   
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(2) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(2)
    else:
        Terrpatch1 = str(bpy.context.scene.Condor_OSM.Patch_X_Max).zfill(3) + str(bpy.context.scene.Condor_OSM.Patch_Y_Max).zfill(3)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.data.objects[Terrpatch1].select_set(True)
    
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax
    Foldername = '22.5m'

    for i in range(patchXmin, patchXmax + 1):
        for j in range(patchYmin, patchYmax + 1):   
            if bpy.context.scene.Condor_OSM.UseNewFormat == False:    
                patch = str(i).zfill(2) + str(j).zfill(2)
            else:
                patch = str(i).zfill(3) + str(j).zfill(3)
            
            print("Processing patch: " + patch)

            landscapePath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\Heightmaps')
            
            # read patch properties from TXT file
            TXTName = Path('h' + patch + '.txt')
            lines = open(os.path.join(landscapePath, TXTName)).readlines()
            bpy.context.scene['ZoneNumber'] = int(lines[0].split(': ')[1])
            bpy.context.scene['TranslateX'] = float(lines[1].split(': ')[1])
            bpy.context.scene['TranslateY'] = float(lines[2].split(': ')[1])
            minLat = float(lines[3].split(': ')[1])
            maxLat = float(lines[4].split(': ')[1])
            minLon = float(lines[5].split(': ')[1])
            maxLon = float(lines[6].split(': ')[1])

            TileName = 'h' + patch + '.obj' 
            ExportPath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Working\HeightMaps', Foldername, 'Modified')
            ObjectName = Path('h' + patch + '.obj')
    
    target_file = os.path.join(ExportPath, ObjectName)
    if bpy.context.scene.Condor_OSM.Use_Legacy_Export == False:
        UseLegacyExp = 0
    else:
        UseLegacyExp = 1
    if UseLegacyExp == 1:
        bpy.ops.export_scene.obj(filepath=target_file, use_normals=True, use_uvs=True, use_materials=True, use_triangles=True, path_mode='COPY', axis_forward='X', axis_up='Z', use_selection=True)
    if UseLegacyExp == 0: 
        bpy.ops.transform.rotate(value=1.5708, orient_axis='Z', orient_type='GLOBAL', orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)))
        saveOBJ(1, target_file)

def cleanfile():
    #deletes every object except for the assets and purges the file
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False, confirm=False)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
   
def deleteautogen():
    #removes autogen from patch (finds objects named o####.... and deletes them)
    patchXmax = bpy.context.scene.Condor_OSM.Patch_X_Max
    patchXmin = patchXmax
    patchYmax = bpy.context.scene.Condor_OSM.Patch_Y_Max
    patchYmin = patchYmax

    for i in range(patchXmin, patchXmax + 1):
            for j in range(patchYmin, patchYmax + 1):   
                patch = str(i).zfill(2) + str(j).zfill(2)

    bpy.ops.object.select_all(action='DESELECT')
    AutogenName = 'o' + patch
    for obj in bpy.context.scene.objects:
        if obj.name.startswith(AutogenName):
            obj.select_set(True)
            bpy.ops.object.delete(use_global=False, confirm=False)

def importairport():
    AirportName = bpy.context.scene.Condor_OSM.Airport_Name
    AirportGfile = AirportName + 'G.obj'
    print ("importing V2 airport " + AirportName)
    condorPath = bpy.context.scene.Condor_OSM.Condor_Path
    landscapeName = bpy.context.scene.Condor_OSM.Landscape_Name
    airportpath = os.path.join(condorPath, 'Landscapes', landscapeName, 'Airports')
    file_loc = os.path.join(airportpath, AirportGfile)
    imported_object = bpy.ops.wm.obj_import(filepath=file_loc, forward_axis='Y', up_axis='Z')
    bpy.ops.mesh.primitive_plane_add(size=3000, enter_editmode=True, align='WORLD', location=(0, 0, 0), scale=(1, 1, 1))
    bpy.ops.mesh.subdivide(number_cuts=99)
    bpy.ops.object.editmode_toggle()
    ob = bpy.context.scene.objects["Grass"]      
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = ob  
    ob.select_set(True)     
    bpy.ops.object.editmode_toggle()
    bpy.ops.transform.translate(value=(-0, -0, -1), orient_type='GLOBAL', orient_matrix_type='GLOBAL')
    bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='FACE')
    bpy.ops.mesh.extrude_region_move(MESH_OT_extrude_region={"use_normal_flip":False, "use_dissolve_ortho_edges":False, "mirror":False}, TRANSFORM_OT_translate={"value":(0, 0, 2), "orient_type":'GLOBAL', "orient_matrix":((1, 0, 0), (0, 1, 0), (0, 0, 1)), "orient_matrix_type":'GLOBAL', "constraint_axis":(False, False, True), "mirror":False, "use_proportional_edit":False, "proportional_edit_falloff":'SMOOTH', "proportional_size":1, "use_proportional_connected":False, "use_proportional_projected":False, "snap":False, "snap_elements":{'INCREMENT'}, "use_snap_project":False, "snap_target":'CLOSEST', "use_snap_self":True, "use_snap_edit":True, "use_snap_nonedit":True, "use_snap_selectable":False, "snap_point":(0, 0, 0), "snap_align":False, "snap_normal":(0, 0, 0), "gpencil_strokes":False, "cursor_transform":False, "texture_space":False, "remove_on_cancel":False, "use_duplicated_keyframes":False, "view2d_edge_pan":False, "release_confirm":False, "use_accurate":False, "alt_navigation":True, "use_automerge_and_split":False})
    bpy.ops.object.editmode_toggle()
    bpy.ops.object.modifier_add(type='BOOLEAN')
    bpy.context.object.modifiers["Boolean"].operation = 'INTERSECT'
    ob = bpy.context.scene.objects["Plane"]      
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = ob  
    ob.select_set(True)    
    bpy.ops.object.modifier_add(type='BOOLEAN')
    bpy.context.object.modifiers["Boolean"].operation = 'INTERSECT'
    bpy.context.object.modifiers["Boolean"].object = bpy.data.objects["Grass"]
    bpy.ops.object.modifier_apply(modifier="Boolean")
    ob = bpy.context.scene.objects["Grass"]      
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = ob  
    ob.select_set(True)
    bpy.ops.object.delete(use_global=False, confirm=False)
    ob = bpy.context.scene.objects["Plane"]      
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = ob  
    ob.select_set(True)
    ob.name = "Grass3D"

#Classes pointing to the defs for the different scripts
class patch_viewer_import(bpy.types.Operator):
    bl_idname = "wm.patch_viewer_import"
    bl_label = "patch_viewer_import"
    bl_description = "Imports terrain and autogen"

    def execute(self, context):
        PatchViewerImport()
        return {'FINISHED'}
        
class patch_viewer_export(bpy.types.Operator):
    bl_idname = "wm.patch_viewer_export"
    bl_label = "patch_viewer_export"
    bl_description = "Exports autogen without terrain"

    def execute(self, context):
        PatchViewerExport()
        return {'FINISHED'}

class patch_viewer_export_terrain(bpy.types.Operator):
    bl_idname = "wm.patch_viewer_export_terrain"
    bl_label = "patch_viewer_export_terrain"
    bl_description = "Exports terrain without autogen"

    def execute(self, context):
        PatchViewerExportTerrain()
        return {'FINISHED'}
        
class power_line_import(bpy.types.Operator):
    bl_idname = "wm.power_line_import"
    bl_label = "power_line_import"
    bl_description = "Imports powerline locations"

    def execute(self, context):
        PowerlineImporter()
        return {'FINISHED'}
        
class power_line_export(bpy.types.Operator):
    bl_idname = "wm.power_line_export"
    bl_label = "power_line_export"
    bl_description = "Exports autogen and powerlines without terrain"

    def execute(self, context):
        PowerlineExporter()
        return {'FINISHED'}

class convert_attribute(bpy.types.Operator):
    bl_idname = "wm.convert_attribute"
    bl_label = "convert_attribute"
    bl_description = "Converts Attribute to UVmap"

    def execute(self, context):
        ConvertAttribute()
        return {'FINISHED'}

class chimney_import(bpy.types.Operator):
    bl_idname = "wm.chimney_import"
    bl_label = "chimney_import"
    bl_description = "Imports chimney locations"

    def execute(self, context):
        ChimneyImporter()
        return {'FINISHED'}

class chimney_apply(bpy.types.Operator):
    bl_idname = "wm.chimney_apply"
    bl_label = "chimney_apply"
    bl_description = "Applies modifiers on selected Chimney objects"

    def execute(self, context):
        ChimneyApplyModifiers()
        return {'FINISHED'}
        
class osm_data_import(bpy.types.Operator):
    bl_idname = "wm.osm_data_import"
    bl_label = "osm_data_import"
    bl_description = "exports autogen for selected patchnumbers"

    def execute(self, context):
        ImportOSMData()
        return {'FINISHED'}
 
class osm_data_replace(bpy.types.Operator):
    bl_idname = "wm.osm_data_replace"
    bl_label = "osm_data_replace"
    bl_description = "Replaces autogen for selected patchnumbers but leaves powerlines in place"

    def execute(self, context):
        ReplaceOSMData()
        return {'FINISHED'}

class import_tr3f(bpy.types.Operator):
    bl_idname = "wm.import_tr3f"
    bl_label = "import_tr3f"
    bl_description = "Imports TR3F file and autogen, requires conversion to 22.5m via Landscape Editor"

    def execute(self, context):
        ImportTR3F()
        return {'FINISHED'}

class export_tr3f(bpy.types.Operator):
    bl_idname = "wm.export_tr3f"
    bl_label = "export_tr3f"
    bl_description = "Exports TR3F without autogen ready for conversion to .TR3F file via Landscape Editor"

    def execute(self, context):
        ExportTR3F()
        return {'FINISHED'}

class clean_file(bpy.types.Operator):
    bl_idname = "wm.clean_file"
    bl_label = "clean_file"
    bl_description = "Deletes everything and cleans the file"

    def execute(self, context):
        cleanfile()
        return {'FINISHED'}

class delete_autogen(bpy.types.Operator):
    bl_idname = "wm.delete_autogen"
    bl_label = "delete_autogen"
    bl_description = "Removes autogen from patch but leaves powerlines and custom objects"

    def execute(self, context):
        deleteautogen()
        return {'FINISHED'}
    
class import_airport(bpy.types.Operator):
    bl_idname = "wm.import_airport"
    bl_label = "import_airport"
    bl_description = "Imports V2 style airport for conversion to V3 airport with grass"

    def execute (self, context):
        importairport()
        return {'FINISHED'}

#sidebar menu
class MySettings(PropertyGroup):

    Import_BMP_textures: BoolProperty(
        name="ImportBMPtextures",
        description="Tick this box to import bmp textures for the terrain from the working folder",
        default = False
        )

    Export_Default_Material: BoolProperty(
        name="UseDefaultMaterial",
        description="Tick this box to export autogen with default materials (only used in PatchViewer)",
        default = True
        )
    
    Export_Default_Objects: BoolProperty(
        name="UseDefaultObjects",
        description="Tick this box to use the default asset package",
        default = True
        )
        
    Use_Legacy_Export: BoolProperty(
        name="UseLegacyExport",
        description="Tick this box when using Blender 3.6 and below with the legacy exporter",
        default = False
        )
    
    UseNewFormat: BoolProperty(
        name="UseNewFormat",
        description="Tick this box to use the new XXXYYY file format when importing or exporting",
        default = True
    )

    Import_From_Modified: BoolProperty(
        name="ImportFromModified",
        description="Tick this box to import TR3F from 22.5m/modified instead of 22.5m (use if you have previously modified and exported this patch)",
        default = False
        )

    Patch_X_Max : IntProperty(
        name = "PatchXMax",
        description="Maximum Patch X value",
        default = 0,
        min = 0,
        max = 999
        )
        
    Patch_X_Min : IntProperty(
        name = "PatchXMin",
        description="Minimum Patch X value",
        default = 0,
        min = 0,
        max = 999
        )
    
    Patch_Y_Max : IntProperty(
        name = "PatchYMax",
        description="Maximum Patch Y value",
        default = 0,
        min = 0,
        max = 999
        )

    Patch_Y_Min : IntProperty(
        name = "PatchYMin",
        description="Minimum Patch Y value",
        default = 0,
        min = 0,
        max = 999
        )
        
    Condor_Path : StringProperty(
        name = "CondorPath",
        description = "Set your Condor 3 install directory",
        default = "C:\Condor3",
        )
    
    Landscape_Name : StringProperty(
        name = "LandscapeName",
        description = "Enter landscape name",
        default = "Slovenia3",
        )
    Airport_Name : StringProperty(
        name = "AirportName",
        description = "Enter airport name",
        default = "Lesce-Bled"
    )


class UV_PT_my_panel(Panel):
    bl_idname = "UV_PT_my_panel"
    bl_label = "CondorOSM"
    bl_category = "CondorOSM"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        CondorOSM = scene.Condor_OSM

        # display the GUI
        box = layout.box()
        box.prop(CondorOSM, "Condor_Path", text="Condor Path")
        box.prop(CondorOSM, "Landscape_Name", text="Landscape Name")
        box.prop(CondorOSM, "Airport_Name", text="Airport Name")
        row = box.row()
        box.prop(CondorOSM, "Patch_X_Max", text="Patch X max")
        box.prop(CondorOSM, "Patch_X_Min", text="Patch X min")
        box.prop(CondorOSM, "Patch_Y_Max", text="Patch Y max")
        box.prop(CondorOSM, "Patch_Y_Min", text="Patch Y min")
        row = box.row()
        box.prop(CondorOSM, "Use_Legacy_Export", text="Use legacy .obj exporter (Blender 3.6)")
        box.prop(CondorOSM, "UseNewFormat", text = "Use new file format")
        row = self.layout.row()
        box = layout.box()
        row = box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="Import Autogen buildings")   
        row = box.row()
        row.operator("wm.osm_data_import", text="Import CondorOSM patches")
        row = box.row()
        row.operator("wm.osm_data_replace", text="Replace CondorOSM patches")
        row = self.layout.row()
        box.prop(CondorOSM, "Export_Default_Objects", text="Use default object textures")
        box = layout.box()
        row = box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="Patch viewer")   
        row = box.row()
        row.operator("wm.patch_viewer_import", text="Import Patch")
        row=box.row(align=True)
        row.operator("wm.patch_viewer_export", text="Export Patch")
        row.operator("wm.patch_viewer_export_terrain", text="Export Terrain")
        box.prop(CondorOSM, "Export_Default_Material", text="Use default materials for export")
        box.prop(CondorOSM, "Import_BMP_textures", text="Import BMP textures")
        row = self.layout.row()
        box = layout.box()
        row = box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="Powerline importer")  
        row = box.row()
        row.operator("wm.power_line_import", text="Import Powerlines")
        row.operator("wm.power_line_export", text="Export Powerlines")
        row = box.row()
        row.operator("wm.convert_attribute", text="Convert attribute")
        row = box.row()
        row.operator("wm.chimney_import", text="Import Chimneys")
        row.operator("wm.chimney_apply", text="Apply Chimney Models")
        box = layout.box()
        row = box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="Import forest maps")
        row = box.row()
        row.operator("wm.import_forestmap", text="Import forestmap BMP")
        box = layout.box()
        row = box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="Airport editor")   
        row = box.row()
        row.operator("wm.import_airport", text="Import V2 airport")
        row = box.row()
        row.operator("wm.import_tr3f", text="Import TR3F")
        row.operator("wm.export_tr3f", text="Export TR3F")
        box.prop(CondorOSM, "Import_From_Modified", text="Import from modified")
        row = self.layout.row()
        box = layout.box()
        row = box.row(align=True)
        row.alignment = "CENTER"
        row.label(text="Clean file")
        row = box.row()
        row.operator("wm.clean_file", text="Clean file")
        row.operator("wm.delete_autogen", text="Remove Autogen")

classes = (
    MySettings,
    UV_PT_my_panel,
)

@bpy.app.handlers.persistent
def _condor_set_default_setup_script(dummy):
    try:
        bpy.context.scene.blosm.setupScript = os.path.join(os.path.dirname(os.path.realpath(__file__)), "power_lines_and_towers.py")
    except Exception:
        pass

def register():
    from bpy.utils import register_class
    if _condor_set_default_setup_script not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_condor_set_default_setup_script)
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.Condor_OSM = PointerProperty(type=MySettings)
    bpy.utils.register_class(osm_data_import)
    bpy.utils.register_class(osm_data_replace)
    bpy.utils.register_class(patch_viewer_import)
    bpy.utils.register_class(patch_viewer_export)
    bpy.utils.register_class(patch_viewer_export_terrain)
    bpy.utils.register_class(power_line_import)
    bpy.utils.register_class(power_line_export)
    bpy.utils.register_class(convert_attribute)
    bpy.utils.register_class(chimney_import)
    bpy.utils.register_class(chimney_apply)
    bpy.utils.register_class(import_tr3f)
    bpy.utils.register_class(export_tr3f)
    bpy.utils.register_class(clean_file)
    bpy.utils.register_class(delete_autogen)
    bpy.utils.register_class(import_airport)

def unregister():
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)

    del bpy.types.Scene.Condor_OSM

if __name__ == "__main__":
    register()