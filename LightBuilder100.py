bl_info = {
    "name": "Light Builder",
    "author": "NBW",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Light Builder",
    "description": "Procedural light placement and rim lighting",
    "category": "Light Builder",
}

import bpy
import math
import gpu
import blf
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils
from mathutils import Vector, geometry

# --- GLOBAL KEYMAP ---
addon_keymaps = []

# --- UTILITIES ---
def get_mirror_vec(v, axis):
    """Returns a mirrored vector across the specified axis."""
    m = v.copy()
    if axis == 'X': m.x *= -1
    elif axis == 'Y': m.y *= -1
    elif axis == 'Z': m.z *= -1
    return m

# --- PROPERTY GROUPS & PREFERENCES ---

class NBW_LightDefaults(bpy.types.PropertyGroup):
    has_custom: bpy.props.BoolProperty(default=False)
    energy: bpy.props.FloatProperty(default=200.0)
    color: bpy.props.FloatVectorProperty(subtype='COLOR', default=(1.0, 1.0, 1.0))
    spot_size: bpy.props.FloatProperty(default=math.radians(60))
    spot_blend: bpy.props.FloatProperty(default=0.15)
    shadow_soft_size: bpy.props.FloatProperty(default=0.1)
    shape: bpy.props.StringProperty(default='SQUARE')
    size: bpy.props.FloatProperty(default=0.25)
    size_y: bpy.props.FloatProperty(default=0.25)
    surface_offset: bpy.props.FloatProperty(default=0.05, name="Surface Offset")

class NBW_LightBuilderPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    symmetry_axis: bpy.props.EnumProperty(
        name="Symmetry Axis",
        items=[('X', 'X Axis', ''), ('Y', 'Y Axis', ''), ('Z', 'Z Axis', '')],
        default='X'
    )

    defaults_uplight: bpy.props.PointerProperty(type=NBW_LightDefaults)
    defaults_downlight: bpy.props.PointerProperty(type=NBW_LightDefaults)
    defaults_targeted: bpy.props.PointerProperty(type=NBW_LightDefaults)
    defaults_aimed: bpy.props.PointerProperty(type=NBW_LightDefaults)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "symmetry_axis")
        layout.separator()
        layout.label(text="Light Builder Defaults are managed directly in the 3D Viewport N-Panel.")

class NBW_LightLinking(bpy.types.PropertyGroup):
    use_linked_uplight: bpy.props.BoolProperty(default=True, name="Link Uplight")
    use_linked_downlight: bpy.props.BoolProperty(default=True, name="Link Downlight")
    use_linked_targeted: bpy.props.BoolProperty(default=False, name="Link Targeted")
    use_linked_aimed: bpy.props.BoolProperty(default=True, name="Link Aimed")
    
    data_uplight: bpy.props.PointerProperty(type=bpy.types.Light)
    data_downlight: bpy.props.PointerProperty(type=bpy.types.Light)
    data_targeted: bpy.props.PointerProperty(type=bpy.types.Light)
    data_aimed: bpy.props.PointerProperty(type=bpy.types.Light)


# --- DRAW CALLBACKS ---

def draw_hud_callback_px(self, context):
    font_id = 0
    blf.position(font_id, 20, 30, 0)
    blf.size(font_id, 18)
    
    mode_names = {
        'UPLIGHT': 'Uplight',
        'DOWNLIGHT': 'Downlight',
        'TARGETED': 'Targeted Area', 
        'AIMED': 'Aimed'
    }
    mode_text = mode_names.get(self.current_mode, self.current_mode)
    
    linking = context.scene.nbw_light_linking
    is_linked = getattr(linking, f"use_linked_{self.current_mode.lower()}")
    link_status = "(Linked)" if is_linked else "(Unique)"
    sym_status = " | Symmetry: ON" if context.scene.use_symmetry else ""
    
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    blf.draw(font_id, f"Light Builder | Mode: {mode_text} {link_status}{sym_status} | [U] Up/Down [T] Targeted [Y] Aimed | [TAB] Flip Up/Down | [ESC] Cancel")

def draw_callback_px(self, context):
    if self.state not in {'CUSTOM_ANGLE', 'CUSTOM_DISTANCE'}:
        return

    anchor = Vector(self.anchor_loc)
    target_vec = Vector(self.target_vector)
    true_normal = Vector(self.hit_normal)
    radius = self.sphere_radius

    shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.0)

    normal_coords = [anchor, anchor + true_normal * radius]
    batch_normal = batch_for_shader(shader, 'LINES', {"pos": normal_coords})
    shader.bind()
    shader.uniform_float("color", (0.0, 1.0, 1.0, 1.0))
    batch_normal.draw(shader)

    coords = [anchor, anchor + target_vec * self.distance]
    batch_line = batch_for_shader(shader, 'LINES', {"pos": coords})
    shader.uniform_float("color", (1.0, 1.0, 0.0, 1.0))
    batch_line.draw(shader)

    if self.state == 'CUSTOM_ANGLE':
        sphere_coords = []
        segments = 32
        for i in range(segments):
            a1 = (i / segments) * math.pi * 2
            a2 = ((i + 1) / segments) * math.pi * 2
            
            sphere_coords.extend([
                anchor + Vector((0, math.cos(a1)*radius, math.sin(a1)*radius)), anchor + Vector((0, math.cos(a2)*radius, math.sin(a2)*radius)),
                anchor + Vector((math.cos(a1)*radius, 0, math.sin(a1)*radius)), anchor + Vector((math.cos(a2)*radius, 0, math.sin(a2)*radius)),
                anchor + Vector((math.cos(a1)*radius, math.sin(a1)*radius, 0)), anchor + Vector((math.cos(a2)*radius, math.sin(a2)*radius, 0))
            ])
            
        batch_sphere = batch_for_shader(shader, 'LINES', {"pos": sphere_coords})
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.2))
        batch_sphere.draw(shader)

    gpu.state.blend_set('NONE')


# --- OPERATORS ---

class NBW_OT_store_light_default(bpy.types.Operator):
    bl_idname = "lighting.store_light_default"
    bl_label = "Set Active as Default"
    bl_options = {'REGISTER', 'UNDO'}
    
    category: bpy.props.StringProperty()
    
    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'LIGHT'
        
    def execute(self, context):
        l_data = context.active_object.data
        prefs = context.preferences.addons[__name__].preferences
        defaults = getattr(prefs, f"defaults_{self.category.lower()}")
        
        defaults.has_custom = True
        defaults.energy = l_data.energy
        defaults.color = l_data.color
        if l_data.type == 'SPOT':
            defaults.spot_size = l_data.spot_size
            defaults.spot_blend = l_data.spot_blend
            if hasattr(l_data, 'shadow_soft_size'):
                defaults.shadow_soft_size = l_data.shadow_soft_size
        elif l_data.type == 'AREA':
            defaults.shape = l_data.shape
            defaults.size = l_data.size
            if hasattr(l_data, 'size_y'):
                defaults.size_y = l_data.size_y
                
        linking = context.scene.nbw_light_linking
        setattr(linking, f"data_{self.category.lower()}", None)
        
        bpy.ops.wm.save_userpref()
        self.report({'INFO'}, f"Saved global defaults for {self.category}")
        return {'FINISHED'}


class NBW_OT_clear_light_default(bpy.types.Operator):
    bl_idname = "lighting.clear_light_default"
    bl_label = "Clear Default"
    
    category: bpy.props.StringProperty()
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        defaults = getattr(prefs, f"defaults_{self.category.lower()}")
        defaults.has_custom = False
        
        linking = context.scene.nbw_light_linking
        setattr(linking, f"data_{self.category.lower()}", None)
        
        bpy.ops.wm.save_userpref()
        self.report({'INFO'}, f"Cleared global defaults for {self.category}")
        return {'FINISHED'}


class NBW_OT_place_procedural_light(bpy.types.Operator):
    bl_idname = "lighting.place_procedural_light"
    bl_label = "Activate Light Placement"
    bl_options = {'REGISTER', 'UNDO'}

    def switch_mode(self, context, new_mode):
        if self.state not in {'WAITING'}:
            objs_to_delete = []
            if self.active_light:
                objs_to_delete.append(self.active_light)
                self.active_light = None
            if self.active_mirror_light:
                objs_to_delete.append(self.active_mirror_light)
                self.active_mirror_light = None
                
            if self.draw_handle:
                bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
                self.draw_handle = None
                
            if self.temp_lights:
                objs_to_delete.extend(self.temp_lights)
                self.temp_lights.clear()
                
            valid_objs = [o for o in objs_to_delete if repr(o) != "<bpy_struct, Object invalid>"]
            if valid_objs:
                bpy.data.batch_remove(valid_objs)
            
        self.points.clear()
        self.normals.clear()
        self.state = 'WAITING'
        self.current_mode = new_mode
        
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

    def create_light(self, context, location, normal, align_to_normal, light_style='UPLIGHT'):
        category = light_style
        linking = context.scene.nbw_light_linking
        prefs = context.preferences.addons[__name__].preferences
        
        is_linked = getattr(linking, f"use_linked_{category.lower()}")
        linked_data = getattr(linking, f"data_{category.lower()}")
        
        l_type = 'AREA' if light_style == 'TARGETED' else 'SPOT'
        defaults = getattr(prefs, f"defaults_{category.lower()}")
        
        if is_linked and linked_data:
            light = bpy.data.objects.new(name="Temp", object_data=linked_data)
            context.collection.objects.link(light)
            light.location = location
            bpy.ops.object.select_all(action='DESELECT')
            light.select_set(True)
            context.view_layer.objects.active = light
        else:
            bpy.ops.object.light_add(type=l_type, location=location)
            light = context.active_object
            
            if defaults.has_custom:
                light.data.energy = defaults.energy
                light.data.color = defaults.color
                if l_type == 'SPOT':
                    light.data.spot_size = defaults.spot_size
                    light.data.spot_blend = defaults.spot_blend
                    if hasattr(light.data, 'shadow_soft_size'):
                        light.data.shadow_soft_size = defaults.shadow_soft_size
                elif l_type == 'AREA':
                    light.data.shape = defaults.shape
                    light.data.size = defaults.size
                    if defaults.shape in {'RECTANGLE', 'ELLIPSE'} and hasattr(light.data, 'size_y'):
                        light.data.size_y = defaults.size_y
            else:
                if light_style == 'AIMED':
                    light.data.energy = 500.0
                    light.data.spot_blend = 1.0
                elif light_style in {'UPLIGHT', 'DOWNLIGHT'}:
                    light.data.energy = 200.0
                    light.data.spot_size = math.radians(60)
                    light.data.spot_blend = 1.0
                    if hasattr(light.data, 'shadow_soft_size'):
                        light.data.shadow_soft_size = 0.1
                else:
                    light.data.energy = 100.0
                        
            if is_linked:
                setattr(linking, f"data_{category.lower()}", light.data)
        
        if light_style == 'TARGETED':
            light.name = "Targeted Area Light"
            coll_name = "Targeted Lights"
        elif light_style == 'AIMED':
            light.name = "Aimed Light"
            coll_name = "Aimed Lights"
        elif light_style == 'UPLIGHT':
            light.name = "Uplight"
            coll_name = "Up Lights"
        elif light_style == 'DOWNLIGHT':
            light.name = "Downlight"
            coll_name = "Down Lights"
            
        if coll_name not in bpy.data.collections:
            new_coll = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(new_coll)
        target_coll = bpy.data.collections[coll_name]
        
        for coll in light.users_collection:
            coll.objects.unlink(light)
        target_coll.objects.link(light)
        
        if align_to_normal:
            rot_quat = normal.to_track_quat('-Z', 'Y')
            light.rotation_euler = rot_quat.to_euler()
        else:
            if light_style in {'UPLIGHT', 'DOWNLIGHT'}:
                if light_style == 'UPLIGHT':
                    light.rotation_euler = (math.radians(180), 0, 0)
                else:
                    light.rotation_euler = (0, 0, 0)
            
        if light_style != 'AIMED':
            offset = defaults.surface_offset if defaults else 0.05
            light.location += normal * offset
            
        return light

    def update_line_lights(self, context):
        objs_to_delete = [obj for obj in self.temp_lights if repr(obj) != "<bpy_struct, Object invalid>"]
        if objs_to_delete:
            bpy.data.batch_remove(objs_to_delete)
        self.temp_lights.clear()

        if len(self.points) < 2:
            return

        p1 = self.points[0]
        p2 = self.points[1]
        n1 = self.normals[0]

        for i in range(self.light_count):
            fac = i / (self.light_count - 1) if self.light_count > 1 else 0
            loc = p1.lerp(p2, fac)
            light = self.create_light(context, loc, n1, self.align_normal, light_style=self.current_mode)
            self.temp_lights.append(light)
            
            if context.scene.use_symmetry:
                axis = context.preferences.addons[__name__].preferences.symmetry_axis
                m_loc = get_mirror_vec(loc, axis)
                m_n1 = get_mirror_vec(n1, axis)
                m_light = self.create_light(context, m_loc, m_n1, self.align_normal, light_style=self.current_mode)
                self.temp_lights.append(m_light)

    def cancel_modal(self, context):
        context.window_manager.nbw_lights_active = False
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
                
        if self.draw_handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            self.draw_handle = None
            
        if self.draw_handle_2d:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_2d, 'WINDOW')
            self.draw_handle_2d = None
            
        objs_to_delete = []
        if self.temp_lights:
            objs_to_delete.extend(self.temp_lights)
            
        if self.active_light and self.state != 'WAITING':
            objs_to_delete.append(self.active_light)
        if self.active_mirror_light and self.state != 'WAITING':
            objs_to_delete.append(self.active_mirror_light)
            
        valid_objs = [o for o in objs_to_delete if repr(o) != "<bpy_struct, Object invalid>"]
        if valid_objs:
            bpy.data.batch_remove(valid_objs)
                
        return {'CANCELLED'}

    def modal(self, context, event):
        if event.type == 'U' and event.value == 'PRESS':
            self.switch_mode(context, 'UPLIGHT')
            return {'RUNNING_MODAL'}
        if event.type == 'T' and event.value == 'PRESS':
            self.switch_mode(context, 'TARGETED')
            return {'RUNNING_MODAL'}
        if event.type == 'Y' and event.value == 'PRESS':
            self.switch_mode(context, 'AIMED')
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and event.value == 'PRESS':
            is_ui_click = False
            area = context.area
            
            if not (area.x <= event.mouse_x <= area.x + area.width and area.y <= event.mouse_y <= area.y + area.height):
                is_ui_click = True
            else:
                for region in area.regions:
                    if region.type in {'UI', 'HEADER', 'TOOL_HEADER'}:
                        if region.x <= event.mouse_x <= region.x + region.width and \
                           region.y <= event.mouse_y <= region.y + region.height:
                            is_ui_click = True
                            break
                            
            if is_ui_click:
                self.is_ui_paused = True
                return {'PASS_THROUGH'}
            elif self.is_ui_paused:
                self.is_ui_paused = False

        if self.is_ui_paused:
            if event.type in {'RET', 'NUMPAD_ENTER', 'ESC'} and event.value == 'PRESS':
                self.is_ui_paused = False
            return {'PASS_THROUGH'}

        if event.type in {'G', 'R'} and event.value == 'PRESS':
            self.is_transforming = True
            return {'PASS_THROUGH'}

        if self.is_transforming:
            if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'RET', 'NUMPAD_ENTER', 'ESC'} and event.value == 'PRESS':
                self.is_transforming = False
            return {'PASS_THROUGH'}

        if getattr(context.window_manager, "nbw_cancel_lights", False):
            context.window_manager.nbw_cancel_lights = False
            return self.cancel_modal(context)

        # Cancellation while mid-placement
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            if self.state != 'WAITING':
                objs_to_delete = []
                if self.state in {'CUSTOM_ANGLE', 'CUSTOM_DISTANCE', 'AIMED_AIMING', 'AIMED_DISTANCE'}:
                    if self.active_light:
                        objs_to_delete.append(self.active_light)
                    self.active_light = None
                    if self.active_mirror_light:
                        objs_to_delete.append(self.active_mirror_light)
                    self.active_mirror_light = None
                        
                    if self.draw_handle:
                        bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
                        self.draw_handle = None
                elif self.state in {'COLLECTING', 'ADJUSTING_COUNT'}:
                    objs_to_delete.extend(self.temp_lights)
                    self.temp_lights.clear()
                    self.points.clear()
                    self.normals.clear()
                    
                valid_objs = [o for o in objs_to_delete if repr(o) != "<bpy_struct, Object invalid>"]
                if valid_objs:
                    bpy.data.batch_remove(valid_objs)
                    
                self.state = 'WAITING'
                for window in context.window_manager.windows:
                    for area in window.screen.areas:
                        area.tag_redraw()
                return {'RUNNING_MODAL'}
            else:
                return self.cancel_modal(context)

        # Batch Undo Cleanups
        is_undo = event.type == 'Z' and event.value == 'PRESS' and (event.ctrl or event.oskey)
        if is_undo:
            objs_to_delete = []
            if self.state != 'WAITING':
                if self.state in {'CUSTOM_ANGLE', 'CUSTOM_DISTANCE', 'AIMED_AIMING', 'AIMED_DISTANCE'}:
                    if self.active_light:
                        objs_to_delete.append(self.active_light)
                    self.active_light = None
                    if self.active_mirror_light:
                        objs_to_delete.append(self.active_mirror_light)
                    self.active_mirror_light = None
                    if self.draw_handle:
                        bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
                        self.draw_handle = None
                elif self.state in {'COLLECTING', 'ADJUSTING_COUNT'}:
                    objs_to_delete.extend(self.temp_lights)
                    self.temp_lights.clear()
                    self.points.clear()
                    self.normals.clear()
                self.state = 'WAITING'
            elif self.history:
                objs_to_remove = self.history.pop()
                objs_to_delete.extend(objs_to_remove)
                
            valid_objs = [o for o in objs_to_delete if repr(o) != "<bpy_struct, Object invalid>"]
            if valid_objs:
                bpy.data.batch_remove(valid_objs)
            
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'Z'}:
            return {'PASS_THROUGH'}
        if event.alt and event.type != 'LEFTMOUSE':
            return {'PASS_THROUGH'}

        if event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'} and event.value == 'RELEASE':
            if self.state == 'COLLECTING' and len(self.points) == 1:
                self.history.append(list(self.temp_lights))
                self.temp_lights.clear()
                self.points.clear()
                self.normals.clear()
                self.state = 'WAITING'

        if event.type == 'TAB' and event.value == 'PRESS':
            if self.current_mode in {'UPLIGHT', 'DOWNLIGHT'}:
                self.switch_mode(context, 'DOWNLIGHT' if self.current_mode == 'UPLIGHT' else 'UPLIGHT')

        if self.state == 'ADJUSTING_COUNT':
            if event.type == 'UP_ARROW' and event.value == 'PRESS':
                self.light_count += 1
                self.update_line_lights(context)
            elif event.type == 'DOWN_ARROW' and event.value == 'PRESS':
                self.light_count = max(2, self.light_count - 1)
                self.update_line_lights(context)
            elif event.type in {'RET', 'NUMPAD_ENTER', 'LEFTMOUSE'} and event.value == 'PRESS':
                self.history.append(list(self.temp_lights))
                self.temp_lights.clear()
                self.points.clear()
                self.normals.clear()
                self.state = 'WAITING'
            return {'RUNNING_MODAL'}

        if self.state == 'AIMED_AIMING':
            if event.type == 'MOUSEMOVE':
                region = context.region
                rv3d = context.region_data
                coord = event.mouse_region_x, event.mouse_region_y
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

                depsgraph = context.evaluated_depsgraph_get()
                result, location, normal, index, hit_object, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)

                target = location if result else ray_origin + view_vector * 20.0
                
                if self.active_light:
                    vec = (target - self.aimed_anchor).normalized()
                    if vec.length > 0:
                        self.active_light.rotation_euler = vec.to_track_quat('-Z', 'Y').to_euler()

                if self.active_mirror_light:
                    axis = context.preferences.addons[__name__].preferences.symmetry_axis
                    m_target = get_mirror_vec(target, axis)
                    m_vec = (m_target - self.mirror_aimed_anchor).normalized()
                    if m_vec.length > 0:
                        self.active_mirror_light.rotation_euler = m_vec.to_track_quat('-Z', 'Y').to_euler()

            elif event.type in {'LEFTMOUSE'} and event.value == 'PRESS':
                region = context.region
                rv3d = context.region_data
                coord = event.mouse_region_x, event.mouse_region_y
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

                depsgraph = context.evaluated_depsgraph_get()
                result, location, normal, index, hit_object, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)

                self.aimed_target = location if result else ray_origin + view_vector * 20.0
                self.aimed_distance = (self.aimed_anchor - self.aimed_target).length
                
                if self.active_mirror_light:
                    axis = context.preferences.addons[__name__].preferences.symmetry_axis
                    self.mirror_aimed_target = get_mirror_vec(self.aimed_target, axis)

                self.state = 'AIMED_DISTANCE'
                self.last_mouse_x = event.mouse_x
            return {'RUNNING_MODAL'}

        if self.state == 'AIMED_DISTANCE':
            if event.type == 'MOUSEMOVE':
                delta = event.mouse_x - self.last_mouse_x
                mult = 0.01 if event.shift else 0.05
                self.aimed_distance += delta * mult
                self.aimed_distance = max(0.1, self.aimed_distance)
                self.last_mouse_x = event.mouse_x
                
                if self.active_light:
                    direction = (self.aimed_anchor - self.aimed_target).normalized()
                    self.active_light.location = self.aimed_target + direction * self.aimed_distance
                    
                if self.active_mirror_light:
                    m_direction = (self.mirror_aimed_anchor - self.mirror_aimed_target).normalized()
                    self.active_mirror_light.location = self.mirror_aimed_target + m_direction * self.aimed_distance
                    
            elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                self.state = 'WAITING'
                entry = []
                if self.active_light:
                    entry.append(self.active_light)
                if self.active_mirror_light:
                    entry.append(self.active_mirror_light)
                if entry:
                    self.history.append(entry)
                    
                self.active_light = None
                self.active_mirror_light = None
            return {'RUNNING_MODAL'}

        if self.state == 'CUSTOM_ANGLE':
            if event.type == 'MOUSEMOVE':
                region = context.region
                rv3d = context.region_data
                coord = event.mouse_region_x, event.mouse_region_y
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
                
                center = Vector(self.anchor_loc)
                L = ray_origin - center
                a = view_vector.dot(view_vector)
                b = 2.0 * view_vector.dot(L)
                c = L.dot(L) - self.sphere_radius**2
                disc = b**2 - 4*a*c
                
                raw_vec = None
                if disc >= 0:
                    t = (-b - math.sqrt(disc)) / (2.0 * a)
                    intersect = ray_origin + view_vector * t
                    raw_vec = (intersect - center).normalized()
                else:
                    hit_plane = geometry.intersect_line_plane(ray_origin, ray_origin + view_vector*1000, center, -view_vector)
                    if hit_plane:
                        raw_vec = (hit_plane - center).normalized()
                
                if raw_vec:
                    if event.shift:
                        pitch = math.asin(raw_vec.z)
                        yaw = math.atan2(raw_vec.y, raw_vec.x)
                        snap = math.radians(15)
                        pitch = round(pitch / snap) * snap
                        yaw = round(yaw / snap) * snap
                        raw_vec = Vector((math.cos(pitch)*math.cos(yaw), math.cos(pitch)*math.sin(yaw), math.sin(pitch)))
                    
                    self.target_vector = raw_vec
                    
                    if self.active_light:
                        self.active_light.location = center + raw_vec * self.distance
                        self.active_light.rotation_euler = (-raw_vec).to_track_quat('-Z', 'Y').to_euler()
                        
                    if self.active_mirror_light:
                        axis = context.preferences.addons[__name__].preferences.symmetry_axis
                        m_raw = get_mirror_vec(raw_vec, axis)
                        self.mirror_target_vector = m_raw
                        self.active_mirror_light.location = self.mirror_anchor_loc + m_raw * self.distance
                        self.active_mirror_light.rotation_euler = (-m_raw).to_track_quat('-Z', 'Y').to_euler()
                        
            elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                self.state = 'CUSTOM_DISTANCE'
                self.last_mouse_x = event.mouse_x
            return {'RUNNING_MODAL'}

        if self.state == 'CUSTOM_DISTANCE':
            if event.type == 'MOUSEMOVE':
                delta = event.mouse_x - self.last_mouse_x
                mult = 0.002 if event.shift else 0.01
                self.distance += delta * mult
                self.last_mouse_x = event.mouse_x
                
                if self.active_light:
                    self.active_light.location = Vector(self.anchor_loc) + Vector(self.target_vector) * self.distance
                    
                if self.active_mirror_light:
                    self.active_mirror_light.location = self.mirror_anchor_loc + self.mirror_target_vector * self.distance
                    
            elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                if self.draw_handle:
                    bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
                    self.draw_handle = None
                self.state = 'WAITING'
                
                entry = []
                if self.active_light:
                    entry.append(self.active_light)
                if self.active_mirror_light:
                    entry.append(self.active_mirror_light)
                if entry:
                    self.history.append(entry)
                    
                self.active_light = None
                self.active_mirror_light = None
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and self.state in {'WAITING', 'COLLECTING'}:
            region = context.region
            rv3d = context.region_data
            coord = event.mouse_region_x, event.mouse_region_y

            view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
            ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

            depsgraph = context.evaluated_depsgraph_get()
            result, location, normal, index, hit_object, matrix = context.scene.ray_cast(depsgraph, ray_origin, view_vector)

            if self.current_mode == 'AIMED':
                self.state = 'AIMED_AIMING'
                if result:
                    anchor = location
                else:
                    meshes = [o for o in context.scene.objects if o.type == 'MESH']
                    max_depth = 0.0
                    found_mesh = False
                    for obj in meshes:
                        for corner in obj.bound_box:
                            world_c = obj.matrix_world @ Vector(corner)
                            depth = (world_c - ray_origin).dot(view_vector)
                            if depth > max_depth:
                                max_depth = depth
                                found_mesh = True
                    if not found_mesh:
                        max_depth = 20.0
                    anchor = ray_origin + view_vector * max_depth
                    
                self.aimed_anchor = anchor
                self.active_light = self.create_light(context, anchor, normal if result else -view_vector, align_to_normal=False, light_style='AIMED')
                
                if context.scene.use_symmetry:
                    axis = context.preferences.addons[__name__].preferences.symmetry_axis
                    self.mirror_aimed_anchor = get_mirror_vec(anchor, axis)
                    m_norm = get_mirror_vec(normal if result else -view_vector, axis)
                    self.active_mirror_light = self.create_light(context, self.mirror_aimed_anchor, m_norm, align_to_normal=False, light_style='AIMED')

                return {'RUNNING_MODAL'}

            if result and self.current_mode == 'TARGETED':
                self.anchor_loc = location
                self.target_vector = normal
                self.distance = self.sphere_radius
                self.state = 'CUSTOM_ANGLE'
                self.active_light = self.create_light(context, location, normal, align_to_normal=False, light_style='TARGETED')
                
                if context.scene.use_symmetry:
                    axis = context.preferences.addons[__name__].preferences.symmetry_axis
                    self.mirror_anchor_loc = get_mirror_vec(location, axis)
                    m_norm = get_mirror_vec(normal, axis)
                    self.active_mirror_light = self.create_light(context, self.mirror_anchor_loc, m_norm, align_to_normal=False, light_style='TARGETED')
                
                args = (self, context)
                self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, args, 'WINDOW', 'POST_VIEW')
                return {'RUNNING_MODAL'}

            if result and self.current_mode in {'UPLIGHT', 'DOWNLIGHT'}:
                self.hit_normal = normal
                if event.shift:
                    if self.state == 'WAITING':
                        self.state = 'COLLECTING'
                        self.points.clear()
                        self.normals.clear()
                        self.temp_lights.clear()
                        self.light_count = 2
                        
                    self.points.append(location.copy())
                    self.normals.append(normal.copy())
                    
                    if len(self.points) == 1:
                        self.align_normal = False
                        light = self.create_light(context, location, normal, self.align_normal, light_style=self.current_mode)
                        self.temp_lights.append(light)
                        
                        if context.scene.use_symmetry:
                            axis = context.preferences.addons[__name__].preferences.symmetry_axis
                            m_loc = get_mirror_vec(location, axis)
                            m_norm = get_mirror_vec(normal, axis)
                            m_light = self.create_light(context, m_loc, m_norm, self.align_normal, light_style=self.current_mode)
                            self.temp_lights.append(m_light)
                        
                    elif len(self.points) == 2:
                        self.state = 'ADJUSTING_COUNT'
                        self.update_line_lights(context)
                else:
                    light = self.create_light(context, location, normal, align_to_normal=False, light_style=self.current_mode)
                    entry = [light]
                    
                    if context.scene.use_symmetry:
                        axis = context.preferences.addons[__name__].preferences.symmetry_axis
                        m_loc = get_mirror_vec(location, axis)
                        m_norm = get_mirror_vec(normal, axis)
                        m_light = self.create_light(context, m_loc, m_norm, align_to_normal=False, light_style=self.current_mode)
                        entry.append(m_light)
                        
                    self.history.append(entry)

            return {'RUNNING_MODAL'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        if context.window_manager.nbw_lights_active:
            context.window_manager.nbw_lights_active = False
            context.window_manager.nbw_cancel_lights = True
            return {'CANCELLED'}

        if context.space_data.type == 'VIEW_3D':
            self.current_mode = 'UPLIGHT'
            self.state = 'WAITING'
            self.align_normal = False
            self.is_transforming = False
            self.is_ui_paused = False
            
            self.history = []
            self.points = []
            self.normals = []
            self.temp_lights = []
            self.light_count = 2
            
            self.active_light = None
            self.active_mirror_light = None
            self.draw_handle = None
            self.is_custom_targeting = False
            self.last_mouse_x = 0
            self.sphere_radius = 1.0
            
            self.hit_normal = Vector((0,0,1))
            self.mirror_anchor_loc = Vector((0,0,0))
            self.mirror_target_vector = Vector((0,0,1))
            
            self.aimed_anchor = Vector((0,0,0))
            self.aimed_target = Vector((0,0,0))
            self.mirror_aimed_anchor = Vector((0,0,0))
            self.mirror_aimed_target = Vector((0,0,0))
            self.aimed_distance = 1.0

            context.window_manager.nbw_lights_active = True
            context.window_manager.modal_handler_add(self)
            
            self.draw_handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_hud_callback_px, (self, context), 'WINDOW', 'POST_PIXEL')
            
            self.report({'INFO'}, "Light Builder Active. HUD displayed in corner.")
            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "Active space must be a View3D")
            return {'CANCELLED'}


class NBW_OT_create_rim_light(bpy.types.Operator):
    bl_idname = "lighting.create_rim_light"
    bl_label = "Isolated Rim Light Rig"
    bl_options = {'REGISTER', 'UNDO'}
    
    buffer_factor: bpy.props.FloatProperty(name="Buffer Factor", default=2.0)

    @classmethod
    def poll(cls, context):
        return context.scene.camera and len(context.selected_objects) > 0

    def execute(self, context):
        selected = context.selected_objects
        cam = context.scene.camera
        
        min_x, min_y, min_z = float('inf'), float('inf'), float('inf')
        max_x, max_y, max_z = float('-inf'), float('-inf'), float('-inf')

        for obj in selected:
            if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}:
                continue
            try:
                bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
                for corner in bbox_corners:
                    min_x = min(min_x, corner.x)
                    min_y = min(min_y, corner.y)
                    min_z = min(min_z, corner.z)
                    max_x = max(max_x, corner.x)
                    max_y = max(max_y, corner.y)
                    max_z = max(max_z, corner.z)
            except:
                pass
        
        if min_x == float('inf'):
            self.report({'WARNING'}, "No valid geometry selected.")
            return {'CANCELLED'}

        center = Vector(((min_x + max_x) / 2, (min_y + max_y) / 2, (min_z + max_z) / 2))
        radius = max(max_x - min_x, max_y - min_y, max_z - min_z) / 2
        bounding_size = max(max_x - min_x, max_y - min_y, max_z - min_z)
        
        cam_loc = cam.matrix_world.translation
        vector = (center - cam_loc).normalized()
        
        distance = max(radius * self.buffer_factor, 1.0)
        light_loc = center + vector * distance
        
        bpy.ops.object.light_add(type='AREA', location=light_loc)
        light = context.active_object
        light.name = "Rim_Light"
        light.data.energy = 500.0
        
        light.data.shape = 'RECTANGLE'
        light.data.size = bounding_size * 1.2
        light.data.size_y = bounding_size * 1.2
        
        if context.scene.render.engine == 'CYCLES':
            light.data.cycles.max_bounces = 0
            light.data.cycles.use_multiple_importance_sampling = False
        
        constraint = light.constraints.new(type='TRACK_TO')
        constraint.target = cam
        constraint.track_axis = 'TRACK_NEGATIVE_Z'
        constraint.up_axis = 'UP_Y'
        
        # Object Sorting Collection
        coll_name = "Rim Lights"
        if coll_name not in bpy.data.collections:
            new_coll = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(new_coll)
        target_coll = bpy.data.collections[coll_name]
        
        for coll in light.users_collection:
            coll.objects.unlink(light)
        target_coll.objects.link(light)
        
        # Hidden Data-Level Receiver Collection
        link_coll = bpy.data.collections.new("Rim_Linking")
        for obj in selected:
            link_coll.objects.link(obj)
                
        light.light_linking.receiver_collection = link_coll
        
        bpy.ops.object.select_all(action='DESELECT')
        light.select_set(True)
        context.view_layer.objects.active = light
        
        return {'FINISHED'}


class NBW_OT_align_lights_z(bpy.types.Operator):
    bl_idname = "lighting.align_lights_z"
    bl_label = "Align Z: Lowest / Highest (Ctrl)"
    bl_options = {'REGISTER', 'UNDO'}

    use_highest: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def invoke(self, context, event):
        self.use_highest = event.ctrl
        return self.execute(context)

    def execute(self, context):
        lights = [obj for obj in context.selected_objects if obj.type == 'LIGHT']
        
        if not lights:
            self.report({'WARNING'}, "No lights selected.")
            return {'CANCELLED'}
            
        if self.use_highest:
            target_z = max(light.location.z for light in lights)
        else:
            target_z = min(light.location.z for light in lights)
        
        for light in lights:
            light.location.z = target_z
            
        label = "Highest" if self.use_highest else "Lowest"
        self.report({'INFO'}, f"Aligned {len(lights)} lights to {label} Z: {target_z:.3f}")
        return {'FINISHED'}


class NBW_PT_procedural_lights_panel(bpy.types.Panel):
    bl_label = "Light Builder"
    bl_idname = "NBW_PT_procedural_lights_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Light Builder'

    def draw(self, context):
        layout = self.layout
        
        layout.operator(
            NBW_OT_place_procedural_light.bl_idname, 
            icon='LIGHT_AREA',
            depress=context.window_manager.nbw_lights_active
        )
        
        layout.separator()
        layout.label(text="Placement Options:")
        row = layout.row()
        row.prop(context.scene, "use_symmetry", text="Symmetry Mode", toggle=True)
        
        layout.separator()
        
        layout.operator(NBW_OT_create_rim_light.bl_idname, icon='LIGHT_AREA')
        layout.separator()
        
        layout.operator(NBW_OT_align_lights_z.bl_idname, icon='TRIA_DOWN')
        layout.separator()

        layout.label(text="Light Preferences:")
        row = layout.row()
        row.prop(context.scene, "nbw_ui_category", expand=True)
        
        cat = context.scene.nbw_ui_category
        linking = context.scene.nbw_light_linking
        
        prefs = context.preferences.addons.get(__name__)
        if prefs:
            defaults = getattr(prefs.preferences, f"defaults_{cat.lower()}")
            
            box = layout.box()
            box.prop(linking, f"use_linked_{cat.lower()}")
            
            if cat in {'UPLIGHT', 'DOWNLIGHT'}:
                box.prop(defaults, "surface_offset", text="Surface Offset")
            
            row = box.row()
            if defaults.has_custom:
                row.label(text="Status: Custom", icon='USER')
                op = row.operator(NBW_OT_clear_light_default.bl_idname, text="", icon='X')
                op.category = cat
            else:
                row.label(text="Status: Built-In", icon='PRESET')
                
            op = box.operator(NBW_OT_store_light_default.bl_idname, text="Set Active as Default", icon='FILE_TICK')
            op.category = cat
        else:
            layout.label(text="Save script to apply preferences.", icon='ERROR')

classes = (
    NBW_LightDefaults,
    NBW_LightBuilderPreferences,
    NBW_LightLinking,
    NBW_OT_store_light_default,
    NBW_OT_clear_light_default,
    NBW_OT_place_procedural_light,
    NBW_OT_create_rim_light,
    NBW_OT_align_lights_z,
    NBW_PT_procedural_lights_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
        
    bpy.types.WindowManager.nbw_lights_active = bpy.props.BoolProperty(default=False)
    bpy.types.WindowManager.nbw_cancel_lights = bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    
    bpy.types.Scene.use_symmetry = bpy.props.BoolProperty(name="Symmetry", default=False)
    bpy.types.Scene.nbw_light_linking = bpy.props.PointerProperty(type=NBW_LightLinking)
    bpy.types.Scene.nbw_ui_category = bpy.props.EnumProperty(
        items=[
            ('UPLIGHT', "Up", ""),
            ('DOWNLIGHT', "Down", ""),
            ('TARGETED', "Targeted", ""),
            ('AIMED', "Aimed", "")
        ],
        name="Category"
    )

    # Hotkey Registration
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new("lighting.place_procedural_light", 'L', 'PRESS', ctrl=True, shift=True)
        addon_keymaps.append((km, kmi))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    del bpy.types.WindowManager.nbw_lights_active
    del bpy.types.WindowManager.nbw_cancel_lights
    del bpy.types.Scene.use_symmetry
    del bpy.types.Scene.nbw_light_linking
    del bpy.types.Scene.nbw_ui_category

    # Hotkey Cleanup
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

if __name__ == "__main__":
    register()