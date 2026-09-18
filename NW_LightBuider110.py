bl_info = {
    "name": "Light Builder",
    "author": "NBW",
    "version": (1, 1),
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
from mathutils import Vector, Quaternion, geometry

# --- GLOBAL KEYMAP ---
addon_keymaps = []


@bpy.app.handlers.persistent
def nbw_on_load_post(dummy):
    """A modal operator can never actually resume across a file load - if
    nbw_lights_active (or the other tool-activity flags) was True when a
    file got saved, reopening it shows the toggle button stuck "on" with no
    live operator behind it, and nothing in the UI can ever clear a flag
    that no running operator is polling. SKIP_SAVE on these properties
    stops that from being written into new saves, but this is the backstop
    that self-heals any file where a stale True is already baked in from
    before that fix, or from any other future path that might set it."""
    _nbw_purge_stale_flags()

# --- UTILITIES ---
def get_mirror_vec(v, axis):
    """Returns a mirrored vector across the specified axis."""
    m = v.copy()
    if axis == 'X': m.x *= -1
    elif axis == 'Y': m.y *= -1
    elif axis == 'Z': m.z *= -1
    return m


def _nbw_on_ui_category_update(self, context):
    """Fires whenever nbw_ui_category changes from ANY source (hotkey OR
    the panel's enum buttons). We can't reach into a running modal operator
    instance directly from here, so we just flag it dirty; modal() polls
    this flag (via its timer, so it's caught even without mouse movement)
    and calls switch_mode() to bring the actual placement mode in line."""
    context.window_manager.nbw_mode_dirty = True


def nbw_safe_track_quat(vec):
    """Vector.to_track_quat('-Z', 'Y') builds its 'up' reference by
    projecting world +Z off the aim vector - a projection that degenerates
    to (near) zero length exactly when the aim vector is (near) vertical,
    which is numerically unstable and can resolve to a flipped or
    otherwise wrong orientation rather than just an arbitrary roll. That
    singularity is hit head-on by every uplight/downlight (always exactly
    vertical) and easily by Targeted/Aimed lights pointed near straight up
    or down - both reported as the light "resetting" or mirroring
    reversed. Falls back to the same explicit, stable quaternion the rest
    of the addon already uses for straight up/down in that case; a
    spotlight/area light's roll around its own aim axis isn't visible
    anyway, so there's nothing lost by not routing that case through
    to_track_quat at all."""
    if vec.length_squared > 0:
        v = vec.normalized()
    else:
        v = Vector((0.0, 0.0, -1.0))

    horizontal_len_sq = v.x * v.x + v.y * v.y
    if horizontal_len_sq < 1e-4:
        if v.z < 0:
            return Quaternion((1.0, 0.0, 0.0, 0.0))  # identity: -Z -> world -Z (down)
        else:
            return Quaternion((0.0, 1.0, 0.0, 0.0))  # 180 deg about X: -Z -> world +Z (up)

    return v.to_track_quat('-Z', 'Y')



def _nbw_uniform_color_shader():
    """Blender renamed the 'UNIFORM_COLOR' builtin shader from
    '3D_UNIFORM_COLOR' as of 4.0; some point releases have been fussy about
    which name resolves, so try the current name first and fall back."""
    try:
        return gpu.shader.from_builtin('UNIFORM_COLOR')
    except ValueError:
        return gpu.shader.from_builtin('3D_UNIFORM_COLOR')


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
    defaults_point: bpy.props.PointerProperty(type=NBW_LightDefaults)
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
    use_linked_point: bpy.props.BoolProperty(default=True, name="Link Point")
    use_linked_targeted: bpy.props.BoolProperty(default=False, name="Link Targeted")
    use_linked_aimed: bpy.props.BoolProperty(default=True, name="Link Aimed")

    data_uplight: bpy.props.PointerProperty(type=bpy.types.Light)
    data_downlight: bpy.props.PointerProperty(type=bpy.types.Light)
    data_point: bpy.props.PointerProperty(type=bpy.types.Light)
    data_targeted: bpy.props.PointerProperty(type=bpy.types.Light)
    data_aimed: bpy.props.PointerProperty(type=bpy.types.Light)


# --- DRAW CALLBACKS ---

def draw_hud_callback_px(self, context):
    # context captured at invoke() time can go stale (area/workspace
    # changes, or - if the draw handle ever leaks past this operator's
    # lifetime - self itself being freed by Blender, which raises
    # ReferenceError on any attribute access). Use the live context and
    # bail out quietly rather than let a stale reference throw on every
    # redraw.
    try:
        current_mode = self.current_mode
    except ReferenceError:
        return

    context = bpy.context
    font_id = 0

    mode_names = {
        'UPLIGHT': 'Uplight',
        'DOWNLIGHT': 'Downlight',
        'POINT': 'Point',
        'TARGETED': 'Targeted Area',
        'AIMED': 'Aimed'
    }
    mode_text = mode_names.get(current_mode, current_mode)

    linking = context.scene.nbw_light_linking
    is_linked = getattr(linking, f"use_linked_{current_mode.lower()}")
    link_status = "Linked" if is_linked else "Unique"
    sym_status = "On" if context.scene.use_symmetry else "Off"

    lines = [
        (24, f"Light Builder \u2014 {mode_text}   |   {link_status}   |   Symmetry: {sym_status}", 1.0, 32),
        (18, "U Uplight    P Point    T Targeted    Y Aimed    TAB Flip Up/Down", 0.9, 26),
        (18, "LMB Place    Shift+LMB Array    X Delete    Ctrl+Z Undo    RMB Cancel Step    Esc Exit", 0.9, 0),
    ]

    y = 24 + 32 + 26
    for size, text, alpha, gap_after in lines:
        blf.size(font_id, size)
        blf.position(font_id, 24, y, 0)
        blf.color(font_id, 1.0, 1.0, 1.0, alpha)
        blf.draw(font_id, text)
        y -= gap_after

def draw_callback_px(self, context):
    try:
        state = self.state
    except ReferenceError:
        return
    if state not in {'CUSTOM_ANGLE', 'CUSTOM_DISTANCE'}:
        return

    anchor = Vector(self.anchor_loc)
    target_vec = Vector(self.target_vector)
    true_normal = Vector(self.hit_normal)
    radius = self.sphere_radius

    shader = _nbw_uniform_color_shader()
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

    # The wireframe sphere is static for the whole CUSTOM_ANGLE step (anchor
    # and radius never change mid-step), so it's built once in
    # _build_sphere_batch() rather than re-computed on every redraw here.
    if state == 'CUSTOM_ANGLE' and getattr(self, "_sphere_batch", None):
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.2))
        self._sphere_batch.draw(shader)

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

    # ---------- shared helpers ----------

    def redraw_all(self, context):
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

    def _event_over_ui_region(self, context, event):
        """True if event.mouse_x/y (absolute screen coords) falls over any
        non-viewport region of the current area (N-panel, header, tool
        shelf, etc.) or outside the area entirely. Deliberately does NOT
        rely on context.region tracking the hovered region during a modal
        operator - that doesn't update reliably here - so this hit-tests
        directly against the area's live region rectangles instead."""
        area = context.area
        if area is None:
            return True

        if not (area.x <= event.mouse_x <= area.x + area.width and
                area.y <= event.mouse_y <= area.y + area.height):
            return True

        for region in area.regions:
            if region.type == 'WINDOW':
                continue
            if region.width <= 0 or region.height <= 0:
                continue
            if (region.x <= event.mouse_x <= region.x + region.width and
                    region.y <= event.mouse_y <= region.y + region.height):
                return True

        return False

    def discard_objects(self, objs):
        """Soft-delete: unlink + hide rather than bpy.data.objects.remove().
        Hard-removing objects while a modal/draw-handler context is active
        (especially with Cycles/Metal in the viewport) risks a crash, so we
        defer real removal and just make them invisible + orphaned instead."""
        valid = [o for o in objs if o and repr(o) != "<bpy_struct, Object invalid>"]
        for obj in valid:
            obj.hide_viewport = True
            obj.hide_render = True
            for coll in list(obj.users_collection):
                coll.objects.unlink(obj)

    def discard_active_step(self, context):
        """Cancel whatever is currently being interactively placed (but not
        yet committed to history) and drop back to WAITING, without exiting
        the tool. Shared by RMB-cancel, Ctrl+Z mid-drag, and X mid-drag."""
        objs = []
        if self.state in {'CUSTOM_ANGLE', 'CUSTOM_DISTANCE', 'AIMED_AIMING', 'AIMED_DISTANCE'}:
            if self.active_light:
                objs.append(self.active_light)
            if self.active_mirror_light:
                objs.append(self.active_mirror_light)
            self.active_light = None
            self.active_mirror_light = None
            if self.draw_handle:
                bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
                self.draw_handle = None
        elif self.state in {'COLLECTING', 'ADJUSTING_COUNT'}:
            objs.extend(self.temp_lights)
            self.temp_lights.clear()
            self.points.clear()
            self.normals.clear()

        self.discard_objects(objs)
        self.state = 'WAITING'
        self.redraw_all(context)
        return {'RUNNING_MODAL'}

    def delete_light_with_twin(self, context, obj):
        """X-delete for an already-committed light. Pulls its symmetry twin
        along (a driven twin left with no source isn't useful to keep) and
        strips any history entries referencing either, so Ctrl+Z doesn't try
        to resurrect something we've already removed."""
        objs = [obj]
        twin_name = obj.get("nbw_twin")
        if twin_name:
            twin = bpy.data.objects.get(twin_name)
            if twin and twin not in objs:
                objs.append(twin)

        self.history = [entry for entry in self.history if not any(o in objs for o in entry)]
        self.discard_objects(objs)

    def raycast_camera_visible(self, context, depsgraph, ray_origin, view_vector, max_steps=12):
        """scene.ray_cast() hits anything visible in the viewport, regardless
        of an object's Camera ray-visibility toggle. This re-casts past any
        hit that's flagged invisible to camera, so placement can't land on
        geometry that shouldn't be considered (e.g. a hidden blocker mesh
        sitting between the cursor and the intended surface)."""
        origin = ray_origin
        direction = view_vector.normalized()
        for _ in range(max_steps):
            result, location, normal, index, hit_object, matrix = context.scene.ray_cast(depsgraph, origin, direction)
            if not result:
                return False, None, None, None, None, None
            if hit_object.visible_camera:
                return result, location, normal, index, hit_object, matrix
            origin = location + direction * 1e-4
        return False, None, None, None, None, None

    def _build_sphere_batch(self, anchor, radius):
        shader = _nbw_uniform_color_shader()
        coords = []
        segments = 32
        for i in range(segments):
            a1 = (i / segments) * math.pi * 2
            a2 = ((i + 1) / segments) * math.pi * 2
            coords.extend([
                anchor + Vector((0, math.cos(a1)*radius, math.sin(a1)*radius)), anchor + Vector((0, math.cos(a2)*radius, math.sin(a2)*radius)),
                anchor + Vector((math.cos(a1)*radius, 0, math.sin(a1)*radius)), anchor + Vector((math.cos(a2)*radius, 0, math.sin(a2)*radius)),
                anchor + Vector((math.cos(a1)*radius, math.sin(a1)*radius, 0)), anchor + Vector((math.cos(a2)*radius, math.sin(a2)*radius, 0)),
            ])
        self._sphere_shader = shader
        self._sphere_batch = batch_for_shader(shader, 'LINES', {"pos": coords})

    # ---------- symmetry driver linking ----------

    def link_light_data_drivers(self, source_data, mirror_data):
        """Live-copy the numeric light-data properties (energy, color, spot
        params, area size) from source onto mirror. Safe to call any time,
        including mid-drag, since it never touches transforms."""
        if source_data is None or mirror_data is None or source_data == mirror_data:
            return
        if type(source_data) is not type(mirror_data):
            return

        mirror_data.driver_remove("color")
        for i in range(3):
            fcurve = mirror_data.driver_add("color", i)
            drv = fcurve.driver
            drv.type = 'SCRIPTED'
            var = drv.variables.new()
            var.name = "v"
            var.type = 'SINGLE_PROP'
            var.targets[0].id_type = 'LIGHT'
            var.targets[0].id = source_data
            var.targets[0].data_path = f"color[{i}]"
            drv.expression = "v"

        props = ["energy", "shadow_soft_size"]
        if source_data.type == 'SPOT':
            props += ["spot_size", "spot_blend"]
        elif source_data.type == 'AREA':
            props.append("size")
            if hasattr(source_data, "size_y"):
                props.append("size_y")

        for prop in props:
            if not hasattr(mirror_data, prop):
                continue
            mirror_data.driver_remove(prop)
            fcurve = mirror_data.driver_add(prop)
            drv = fcurve.driver
            drv.type = 'SCRIPTED'
            var = drv.variables.new()
            var.name = "v"
            var.type = 'SINGLE_PROP'
            var.targets[0].id_type = 'LIGHT'
            var.targets[0].id = source_data
            var.targets[0].data_path = prop
            drv.expression = "v"

    def link_transform_drivers(self, context, source, mirror, axis):
        """Live-link mirror's location + rotation to follow source, mirrored
        across `axis`. Location is a simple per-channel negate. Rotation is
        also a simple per-channel operation: mirroring a rotation across a
        world axis is algebraically just a sign-flip of two of its
        quaternion components (W and the mirrored axis's own component stay
        put, the other two negate - conjugating the rotation matrix by the
        mirror matrix and expanding in w/x/y/z terms reduces to exactly
        this, with no trig and no singularities). Reading source's own
        rotation_quaternion components as plain driver variables keeps this
        driver to bare variable references and negation, which Blender
        evaluates as ordinary safe math - unlike calling a registered
        Python function (the previous approach), this doesn't trip the
        "disable script execution" prompt on file load. Both lights are
        kept in quaternion rotation mode (set at creation) specifically so
        rotation_quaternion stays live and accurate for this to read; it
        goes stale if an object is left in Euler mode while only
        rotation_euler is being written. One-way: editing the mirror
        directly will fight the driver."""
        if source is None or mirror is None:
            return

        axis_index = {'X': 0, 'Y': 1, 'Z': 2}[axis]

        mirror.driver_remove("location")
        for i in range(3):
            fcurve = mirror.driver_add("location", i)
            drv = fcurve.driver
            drv.type = 'SCRIPTED'
            var = drv.variables.new()
            var.name = "v"
            var.type = 'SINGLE_PROP'
            var.targets[0].id = source
            var.targets[0].data_path = f"location[{i}]"
            drv.expression = "-v" if i == axis_index else "v"

        mirror.rotation_mode = 'QUATERNION'
        mirror.driver_remove("rotation_quaternion")

        # (w, x, y, z) sign multipliers per mirror axis.
        quat_flip = {
            'X': (1, 1, -1, -1),
            'Y': (1, -1, 1, -1),
            'Z': (1, -1, -1, 1),
        }[axis]

        for i in range(4):
            fcurve = mirror.driver_add("rotation_quaternion", i)
            drv = fcurve.driver
            drv.type = 'SCRIPTED'
            var = drv.variables.new()
            var.name = "q"
            var.type = 'SINGLE_PROP'
            var.targets[0].id = source
            var.targets[0].data_path = f"rotation_quaternion[{i}]"
            drv.expression = "q" if quat_flip[i] == 1 else "-q"

    # ---------- light creation ----------

    def create_light(self, context, location, normal, align_to_normal, light_style='UPLIGHT'):
        category = light_style
        linking = context.scene.nbw_light_linking
        prefs = self.prefs

        is_linked = getattr(linking, f"use_linked_{category.lower()}")
        linked_data = getattr(linking, f"data_{category.lower()}")

        if light_style == 'TARGETED':
            l_type = 'AREA'
        elif light_style == 'POINT':
            l_type = 'POINT'
        else:
            l_type = 'SPOT'
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
                elif l_type == 'POINT':
                    if hasattr(light.data, 'shadow_soft_size'):
                        light.data.shadow_soft_size = defaults.shadow_soft_size
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
                elif light_style == 'POINT':
                    light.data.energy = 200.0
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
        elif light_style == 'POINT':
            light.name = "Point Light"
            coll_name = "Point Lights"

        if coll_name not in bpy.data.collections:
            new_coll = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(new_coll)
        target_coll = bpy.data.collections[coll_name]

        for coll in light.users_collection:
            coll.objects.unlink(light)
        target_coll.objects.link(light)

        # Every light this tool creates uses quaternion rotation, not just
        # symmetry mirrors: it's what lets a mirror's driver read a live,
        # accurate rotation_quaternion off its source at all (that property
        # goes stale if an object is left in Euler mode while only
        # rotation_euler is written), and it sidesteps Euler gimbal/wrap
        # entirely for every light, symmetric or not.
        light.rotation_mode = 'QUATERNION'

        if align_to_normal:
            light.rotation_quaternion = nbw_safe_track_quat(normal)
        else:
            if light_style in {'UPLIGHT', 'DOWNLIGHT'}:
                if light_style == 'UPLIGHT':
                    light.rotation_quaternion = Quaternion((0.0, 1.0, 0.0, 0.0))  # 180 deg about X: -Z -> world +Z (up)
                else:
                    light.rotation_quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))  # identity: -Z -> world -Z (down)
            elif light_style == 'POINT':
                light.rotation_quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))  # identity: world-aligned

        if light_style != 'AIMED':
            light.location += normal * defaults.surface_offset

        # Tag every light this tool creates so X-delete and twin lookups can
        # reliably identify "ours" regardless of collection naming.
        light["nbw_light_builder"] = True

        return light

    def create_light_pair(self, context, location, normal, align_to_normal, light_style, link_transforms=True):
        """Creates a light (and its symmetry twin, if symmetry is on),
        tags them with a mutual twin reference, and links the twin's light
        data (always) and transform (when link_transforms) via drivers.
        link_transforms=False is used for Targeted/Aimed, whose interactive
        drag phase needs to freely reposition the mirror light itself —
        the caller links transforms explicitly once that drag commits."""
        light = self.create_light(context, location, normal, align_to_normal, light_style=light_style)
        mirror = None

        if context.scene.use_symmetry:
            axis = self.prefs.symmetry_axis
            m_loc = get_mirror_vec(location, axis)
            m_normal = get_mirror_vec(normal, axis)
            mirror = self.create_light(context, m_loc, m_normal, align_to_normal, light_style=light_style)

            light["nbw_twin"] = mirror.name
            mirror["nbw_twin"] = light.name
            mirror["nbw_mirror_axis"] = axis

            self.link_light_data_drivers(light.data, mirror.data)
            if link_transforms:
                self.link_transform_drivers(context, light, mirror, axis)

        return light, mirror

    def update_line_lights(self, context):
        self.discard_objects(self.temp_lights)
        self.temp_lights.clear()

        if len(self.points) < 2:
            return

        p1 = self.points[0]
        p2 = self.points[1]
        n1 = self.normals[0]

        for i in range(self.light_count):
            fac = i / (self.light_count - 1) if self.light_count > 1 else 0
            loc = p1.lerp(p2, fac)
            light, mirror = self.create_light_pair(context, loc, n1, self.align_normal, light_style=self.current_mode)
            self.temp_lights.append(light)
            if mirror:
                self.temp_lights.append(mirror)

    # ---------- mode / lifecycle ----------

    def switch_mode(self, context, new_mode):
        if self.state != 'WAITING':
            objs = []
            if self.active_light:
                objs.append(self.active_light)
                self.active_light = None
            if self.active_mirror_light:
                objs.append(self.active_mirror_light)
                self.active_mirror_light = None
            if self.draw_handle:
                bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
                self.draw_handle = None
            objs.extend(self.temp_lights)
            self.temp_lights.clear()
            self.discard_objects(objs)

        self.points.clear()
        self.normals.clear()
        self.state = 'WAITING'
        self.current_mode = new_mode
        if context.scene.nbw_ui_category != new_mode:
            context.scene.nbw_ui_category = new_mode

        self.redraw_all(context)

    def cancel_modal(self, context):
        context.window_manager.nbw_lights_active = False

        if getattr(self, "_timer", None):
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

        if self.draw_handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            self.draw_handle = None

        if self.draw_handle_2d:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_2d, 'WINDOW')
            self.draw_handle_2d = None

        objs = list(self.temp_lights)
        self.temp_lights.clear()

        if self.active_light and self.state != 'WAITING':
            objs.append(self.active_light)
        if self.active_mirror_light and self.state != 'WAITING':
            objs.append(self.active_mirror_light)
        self.active_light = None
        self.active_mirror_light = None

        self.discard_objects(objs)
        self.redraw_all(context)

        return {'CANCELLED'}

    def cancel(self, context):
        """Called BY Blender when it terminates this modal externally (file
        open/new, closing the invoking window) rather than us choosing to
        end it via ESC/the cancel flag/the toggle button. Without this
        method none of that cleanup runs at all: the timer and both draw
        handlers leak, and the leaked HUD draw handler keeps referencing
        this operator instance after Blender frees it, throwing on every
        subsequent viewport redraw until Blender restarts. Routes through
        the same cleanup as a normal cancel rather than duplicating it."""
        try:
            self.cancel_modal(context)
        except Exception:
            import traceback
            traceback.print_exc()

    # ---------- modal ----------

    def modal(self, context, event):
        """Thin wrapper: if _modal() ever raises, Blender would otherwise
        silently drop this operator from the handler stack mid-event,
        skipping our own cleanup entirely and leaving nbw_lights_active
        stuck True forever (button shows "on" while nothing responds, with
        no visible error). Catch it, report it, and cancel cleanly instead."""
        try:
            return self._modal(context, event)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Light Builder stopped after an internal error: {exc}")
            return self.cancel_modal(context)

    def _modal(self, context, event):
        # Always-checked, before any pause/transform gate can swallow them.
        # This keeps the toggle button and the panel's mode selector
        # reliably in sync even when no mouse movement is happening over
        # the viewport (e.g. immediately after clicking a button).
        if getattr(context.window_manager, "nbw_cancel_lights", False):
            context.window_manager.nbw_cancel_lights = False
            return self.cancel_modal(context)

        # context.area can go None if the area this modal was invoked from
        # gets merged, maximized away, or the workspace changes out from
        # under it. There's nothing useful this tool can do without an
        # area, and _event_over_ui_region previously treated a None area as
        # "this click is over the UI" - meaning every future click stayed
        # permanently paused rather than actually placing anything, with no
        # way back short of restarting. Just exit cleanly instead.
        if context.area is None:
            return self.cancel_modal(context)

        if getattr(context.window_manager, "nbw_mode_dirty", False):
            context.window_manager.nbw_mode_dirty = False
            new_mode = context.scene.nbw_ui_category
            if new_mode != self.current_mode:
                self.switch_mode(context, new_mode)

        if event.type == 'ESC' and event.value == 'PRESS':
            return self.cancel_modal(context)

        if event.type == 'TIMER':
            return {'PASS_THROUGH'}

        if event.type == 'U' and event.value == 'PRESS':
            self.switch_mode(context, 'UPLIGHT')
            return {'RUNNING_MODAL'}
        if event.type == 'P' and event.value == 'PRESS':
            self.switch_mode(context, 'POINT')
            return {'RUNNING_MODAL'}
        if event.type == 'T' and event.value == 'PRESS':
            self.switch_mode(context, 'TARGETED')
            return {'RUNNING_MODAL'}
        if event.type == 'Y' and event.value == 'PRESS':
            self.switch_mode(context, 'AIMED')
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and event.value == 'PRESS':
            is_ui_click = self._event_over_ui_region(context, event)

            if is_ui_click:
                self.is_ui_paused = True
                return {'PASS_THROUGH'}
            elif self.is_ui_paused:
                self.is_ui_paused = False

        if self.is_ui_paused:
            if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                self.is_ui_paused = False
            return {'PASS_THROUGH'}

        # G/R is deliberately just passed through, with no state tracked on
        # our side, rather than gated behind a flag we set and wait to see
        # cleared: Blender's own transform operator stacks above this modal
        # handler and takes events first while it's active, so it already
        # "just works" without our help. A flag here previously tried to
        # detect when the transform confirmed/cancelled to resume normal
        # handling, but the confirming click is consumed by the transform
        # operator and never reaches this modal at all - the flag stayed
        # stuck True forever after the very first G or R press, silently
        # PASS_THROUGH-ing every event (ESC, RMB, placement clicks) until
        # some unrelated click happened to clear it and also leaked through
        # as a viewport selection.
        if event.type in {'G', 'R'} and event.value == 'PRESS':
            return {'PASS_THROUGH'}

        # Hovering (not clicking) over a non-viewport region mid-drag used to
        # feed sidebar-relative mouse coordinates into the raycasts below,
        # contributing to placement getting spotty near the panel.
        if event.type == 'MOUSEMOVE' and self.state in {'CUSTOM_ANGLE', 'CUSTOM_DISTANCE', 'AIMED_AIMING', 'AIMED_DISTANCE'}:
            if self._event_over_ui_region(context, event):
                return {'PASS_THROUGH'}

        if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            if self.state != 'WAITING':
                return self.discard_active_step(context)
            return self.cancel_modal(context)

        is_undo = event.type == 'Z' and event.value == 'PRESS' and (event.ctrl or event.oskey)
        if is_undo:
            if self.state != 'WAITING':
                return self.discard_active_step(context)
            if self.history:
                objs = self.history.pop()
                self.discard_objects(objs)
                self.redraw_all(context)
            return {'RUNNING_MODAL'}

        if event.type in {'X', 'DEL'} and event.value == 'PRESS':
            if self.state != 'WAITING':
                return self.discard_active_step(context)
            active = context.active_object
            if active is not None and active.get("nbw_light_builder"):
                self.delete_light_with_twin(context, active)
                self.redraw_all(context)
            return {'RUNNING_MODAL'}

        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'NUMPAD_0'}:
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
                result, location, normal, index, hit_object, matrix = self.raycast_camera_visible(context, depsgraph, ray_origin, view_vector)

                target = location if result else ray_origin + view_vector * 20.0

                if self.active_light:
                    vec = (target - self.aimed_anchor).normalized()
                    if vec.length > 0:
                        self.active_light.rotation_quaternion = nbw_safe_track_quat(vec)

                if self.active_mirror_light:
                    axis = self.prefs.symmetry_axis
                    m_target = get_mirror_vec(target, axis)
                    m_vec = (m_target - self.mirror_aimed_anchor).normalized()
                    if m_vec.length > 0:
                        self.active_mirror_light.rotation_quaternion = nbw_safe_track_quat(m_vec)

            elif event.type in {'LEFTMOUSE'} and event.value == 'PRESS':
                region = context.region
                rv3d = context.region_data
                coord = event.mouse_region_x, event.mouse_region_y
                view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

                depsgraph = context.evaluated_depsgraph_get()
                result, location, normal, index, hit_object, matrix = self.raycast_camera_visible(context, depsgraph, ray_origin, view_vector)

                self.aimed_target = location if result else ray_origin + view_vector * 20.0
                self.aimed_distance = (self.aimed_anchor - self.aimed_target).length

                if self.active_mirror_light:
                    axis = self.prefs.symmetry_axis
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
                    # "Swivel from anchor" pivot: for Aimed lights this is
                    # the point being aimed AT (the surface you targeted),
                    # not the point the light itself originates from.
                    self.active_light["nbw_pivot"] = tuple(self.aimed_target)
                if self.active_mirror_light:
                    entry.append(self.active_mirror_light)
                    self.active_mirror_light["nbw_pivot"] = tuple(self.mirror_aimed_target)
                    axis = self.prefs.symmetry_axis
                    self.link_transform_drivers(context, self.active_light, self.active_mirror_light, axis)
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
                        self.active_light.rotation_quaternion = nbw_safe_track_quat(-raw_vec)

                    if self.active_mirror_light:
                        axis = self.prefs.symmetry_axis
                        m_raw = get_mirror_vec(raw_vec, axis)
                        self.mirror_target_vector = m_raw
                        self.active_mirror_light.location = self.mirror_anchor_loc + m_raw * self.distance
                        self.active_mirror_light.rotation_quaternion = nbw_safe_track_quat(-m_raw)

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
                    # Targeted mode's anchor is the actual surface point
                    # clicked, so it doubles directly as the swivel pivot.
                    self.active_light["nbw_pivot"] = tuple(self.anchor_loc)
                if self.active_mirror_light:
                    entry.append(self.active_mirror_light)
                    self.active_mirror_light["nbw_pivot"] = tuple(self.mirror_anchor_loc)
                    axis = self.prefs.symmetry_axis
                    self.link_transform_drivers(context, self.active_light, self.active_mirror_light, axis)
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
            result, location, normal, index, hit_object, matrix = self.raycast_camera_visible(context, depsgraph, ray_origin, view_vector)

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
                axis = self.prefs.symmetry_axis
                self.active_light, self.active_mirror_light = self.create_light_pair(
                    context, anchor, normal if result else -view_vector, False, 'AIMED', link_transforms=False
                )
                if self.active_mirror_light:
                    self.mirror_aimed_anchor = get_mirror_vec(anchor, axis)

                return {'RUNNING_MODAL'}

            if result and self.current_mode == 'TARGETED':
                self.anchor_loc = location
                self.target_vector = normal
                self.distance = self.sphere_radius
                self.state = 'CUSTOM_ANGLE'
                axis = self.prefs.symmetry_axis
                self.active_light, self.active_mirror_light = self.create_light_pair(
                    context, location, normal, False, 'TARGETED', link_transforms=False
                )
                if self.active_mirror_light:
                    self.mirror_anchor_loc = get_mirror_vec(location, axis)

                self._build_sphere_batch(Vector(location), self.sphere_radius)
                args = (self, context)
                self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, args, 'WINDOW', 'POST_VIEW')
                return {'RUNNING_MODAL'}

            if result and self.current_mode in {'UPLIGHT', 'DOWNLIGHT', 'POINT'}:
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
                        light, mirror = self.create_light_pair(context, location, normal, self.align_normal, self.current_mode)
                        self.temp_lights.append(light)
                        if mirror:
                            self.temp_lights.append(mirror)

                    elif len(self.points) == 2:
                        self.state = 'ADJUSTING_COUNT'
                        self.update_line_lights(context)
                else:
                    light, mirror = self.create_light_pair(context, location, normal, False, self.current_mode)
                    entry = [light]
                    if mirror:
                        entry.append(mirror)
                    self.history.append(entry)

            return {'RUNNING_MODAL'}

        # Anything not explicitly handled above should fall through to
        # normal Blender behavior rather than being silently swallowed.
        # This matters beyond tidiness: the Ctrl+Shift+L toggle-off is
        # handled by Blender's keymap system re-invoking this operator,
        # not by anything in this method, so that keypress has to make it
        # all the way past this modal handler to ever reach the keymap a
        # second time while the tool is running. The previous unconditional
        # RUNNING_MODAL here silently absorbed it (and ordinary things like
        # Numpad view navigation) instead of letting it through.
        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        if context.window_manager.nbw_lights_active:
            context.window_manager.nbw_cancel_lights = True
            return {'CANCELLED'}

        if context.space_data.type != 'VIEW_3D':
            self.report({'WARNING'}, "Active space must be a View3D")
            return {'CANCELLED'}

        self.draw_handle = None
        self.draw_handle_2d = None
        self._timer = None

        # Defensive: a prior session that ended abruptly (crash, reinstall,
        # a window closing) could leave one of these signal flags set with
        # nothing left to consume it. Starting a fresh session shouldn't
        # inherit a stale signal that could immediately act on it (e.g.
        # self-cancelling on the very first timer tick).
        context.window_manager.nbw_cancel_lights = False
        context.window_manager.nbw_mode_dirty = False

        try:
            self.prefs = context.preferences.addons[__name__].preferences
            self.current_mode = context.scene.nbw_ui_category
            self.state = 'WAITING'
            self.align_normal = False
            self.is_ui_paused = False

            self.history = []
            self.points = []
            self.normals = []
            self.temp_lights = []
            self.light_count = 2

            self.active_light = None
            self.active_mirror_light = None
            self.is_custom_targeting = False
            self.last_mouse_x = 0
            self.sphere_radius = 1.0
            self._sphere_batch = None

            self.hit_normal = Vector((0, 0, 1))
            self.mirror_anchor_loc = Vector((0, 0, 0))
            self.mirror_target_vector = Vector((0, 0, 1))

            self.aimed_anchor = Vector((0, 0, 0))
            self.aimed_target = Vector((0, 0, 0))
            self.mirror_aimed_anchor = Vector((0, 0, 0))
            self.mirror_aimed_target = Vector((0, 0, 0))
            self.aimed_distance = 1.0

            context.window_manager.nbw_lights_active = True
            context.window_manager.modal_handler_add(self)
            # Drives the "always-checked" block at the top of modal() so the
            # tool can react to a toggle-off or panel mode-change promptly,
            # even if the mouse never moves over the viewport afterward.
            self._timer = context.window_manager.event_timer_add(0.05, window=context.window)

            self.draw_handle_2d = bpy.types.SpaceView3D.draw_handler_add(draw_hud_callback_px, (self, context), 'WINDOW', 'POST_PIXEL')
        except Exception as exc:
            # If setup fails partway through, make sure the toggle flag and
            # any handlers/timers already registered don't get left stuck -
            # same stuck-button problem this whole fix is about, just at
            # startup instead of mid-session.
            import traceback
            traceback.print_exc()
            context.window_manager.nbw_lights_active = False
            if self._timer is not None:
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
            if self.draw_handle_2d is not None:
                bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_2d, 'WINDOW')
                self.draw_handle_2d = None
            self.report({'ERROR'}, f"Light Builder failed to start: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, "Light Builder Active. HUD displayed in corner.")
        return {'RUNNING_MODAL'}


class NBW_OT_create_rim_light(bpy.types.Operator):
    bl_idname = "lighting.create_rim_light"
    bl_label = "Isolated Rim Light Rig"
    bl_options = {'REGISTER', 'UNDO'}

    buffer_factor: bpy.props.FloatProperty(name="Buffer Factor", default=2.0)
    size_margin: bpy.props.FloatProperty(name="Size Margin", default=1.35, min=1.0)

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

        dx = max_x - min_x
        dy = max_y - min_y
        dz = max_z - min_z

        # The old version sized the light off a single largest axis, which
        # under-covers wide/flat or narrow/tall subjects and forces a
        # square shape regardless of actual proportions. Using the
        # horizontal footprint's diagonal (robust to whichever way the
        # camera happens to be facing) and the vertical extent separately
        # lets the rectangle actually match the subject's aspect ratio.
        horizontal_extent = math.sqrt(dx ** 2 + dy ** 2)
        vertical_extent = dz
        radius = max(horizontal_extent, vertical_extent) / 2

        cam_loc = cam.matrix_world.translation
        vector = (center - cam_loc).normalized()

        distance = max(radius * self.buffer_factor, 1.0)
        light_loc = center + vector * distance

        bpy.ops.object.light_add(type='AREA', location=light_loc)
        light = context.active_object
        light.name = "Rim_Light"
        light.data.energy = 500.0

        light.data.shape = 'RECTANGLE'
        light.data.size = max(horizontal_extent * self.size_margin, 0.01)
        light.data.size_y = max(vertical_extent * self.size_margin, 0.01)

        if context.scene.render.engine == 'CYCLES':
            light.data.cycles.max_bounces = 0
            light.data.cycles.use_multiple_importance_sampling = False

        constraint = light.constraints.new(type='TRACK_TO')
        constraint.target = cam
        constraint.track_axis = 'TRACK_NEGATIVE_Z'
        constraint.up_axis = 'UP_Y'

        coll_name = "Rim Lights"
        if coll_name not in bpy.data.collections:
            new_coll = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(new_coll)
        target_coll = bpy.data.collections[coll_name]

        for coll in light.users_collection:
            coll.objects.unlink(light)
        target_coll.objects.link(light)

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


class NBW_OT_stop_light_placement(bpy.types.Operator):
    """Deliberately minimal execute()-only companion to the toggle button:
    just requests the running placement modal to cancel, via the same
    nbw_cancel_lights flag it already polls every ~50ms through its timer.
    Kept free of its own invoke()/modal() so clicking it while the tool is
    active is an ordinary, single button press with no round-trip through
    this operator's own state - unlike re-invoking the placement operator
    itself, which still has to negotiate handing that same click past its
    own live modal instance first."""
    bl_idname = "lighting.stop_light_placement"
    bl_label = "Deactivate Light Placement"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.window_manager.nbw_lights_active

    def execute(self, context):
        context.window_manager.nbw_cancel_lights = True
        return {'FINISHED'}


class NBW_OT_set_pivot_to_anchor(bpy.types.Operator):
    """Move the 3D cursor to the active light's stored anchor point and set
    the Transform Pivot Point to it, so rotating (R) swivels the light
    around that surface point instead of its own origin. Targeted/Aimed
    lights only - the anchor is recorded when their placement commits.

    This is deliberately cursor-based rather than an Empty/constraint rig:
    a Light's origin IS where it emits from (unlike a mesh, there's no
    separate data offset to absorb a pivot elsewhere), so a persistent
    "select the light and it just orbits a fixed point" setup would need a
    parent Empty per light - extra Outliner entries this avoids entirely.
    The trade-off is one click here before rotating, instead of none."""
    bl_idname = "lighting.set_pivot_to_anchor"
    bl_label = "Swivel From Anchor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.get("nbw_pivot") is not None

    def execute(self, context):
        obj = context.active_object
        pivot = obj.get("nbw_pivot")
        context.scene.cursor.location = Vector(pivot)
        context.scene.tool_settings.transform_pivot_point = 'CURSOR'
        self.report({'INFO'}, "Cursor moved to anchor, Pivot Point set to Cursor - press R to swivel.")
        return {'FINISHED'}


class NBW_PT_procedural_lights_panel(bpy.types.Panel):
    bl_label = "Light Builder"
    bl_idname = "NBW_PT_procedural_lights_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Light Builder'

    def draw(self, context):
        layout = self.layout

        if context.window_manager.nbw_lights_active:
            layout.operator(
                NBW_OT_stop_light_placement.bl_idname,
                text="Deactivate Light Placement",
                icon='LIGHT_AREA',
                depress=True,
            )
        else:
            layout.operator(
                NBW_OT_place_procedural_light.bl_idname,
                text="Activate Light Placement",
                icon='LIGHT_AREA',
                depress=False,
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

        layout.operator(NBW_OT_set_pivot_to_anchor.bl_idname)
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

            if cat in {'UPLIGHT', 'DOWNLIGHT', 'POINT'}:
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

        layout.separator()

        # Collapsible reference lookup, closed by default, kept at the
        # bottom so it's a lookup rather than something always taking up
        # space above the controls you actually use. layout.panel() needs
        # Blender 4.1+; fall back to a plain always-open box on older
        # versions rather than erroring.
        if hasattr(layout, "panel"):
            header, body = layout.panel("NBW_hotkeys", default_closed=True)
            header.label(text="Hotkeys", icon='INFO')
        else:
            body = layout.box()
            body.label(text="Hotkeys", icon='INFO')

        if body:
            col = body.column(align=True)
            col.label(text="Ctrl Shift L  \u2014  Toggle tool")
            col.label(text="LMB  \u2014  Place light")
            col.label(text="Shift+LMB  \u2014  Draw array (\u2191/\u2193 count)")
            col.label(text="U / P / T / Y  \u2014  Uplight / Point / Targeted / Aimed")
            col.label(text="TAB  \u2014  Flip Up / Down")
            col.label(text="X  \u2014  Delete active or last light")
            col.label(text="Ctrl+Z  \u2014  Undo last light")
            col.label(text="RMB  \u2014  Cancel current step")
            col.label(text="Esc  \u2014  Exit tool")

classes = (
    NBW_LightDefaults,
    NBW_LightBuilderPreferences,
    NBW_LightLinking,
    NBW_OT_store_light_default,
    NBW_OT_clear_light_default,
    NBW_OT_place_procedural_light,
    NBW_OT_stop_light_placement,
    NBW_OT_create_rim_light,
    NBW_OT_align_lights_z,
    NBW_OT_set_pivot_to_anchor,
    NBW_PT_procedural_lights_panel,
)

def _nbw_purge_stale_flags():
    """del bpy.types.WindowManager.nbw_lights_active only removes the RNA
    definition - the actual stored value lives in the WindowManager's own
    ID-property storage and survives that del untouched. Re-registering the
    property later just re-exposes whatever was already sitting there, so
    a version reinstalled or reloaded mid-session (F3 Reload Scripts,
    disable/enable, installing a new version over the old one) can come
    back up still reading True with no live operator behind it - and
    nothing in the UI can ever clear a flag no running modal is polling.
    Explicitly popping the stored values (not just the property
    definition) closes that gap. Iterates every WindowManager rather than
    just the current one, since it's cheap and there's normally only ever
    one anyway.

    Must never be called directly from register()/unregister(): those run
    in a restricted context where bpy.data access is blocked (raises
    "'_RestrictData' object has no attribute ..."), so register()/
    unregister() only ever schedule this via bpy.app.timers, which runs it
    shortly after in a normal, unrestricted context instead."""
    try:
        for wm in bpy.data.window_managers:
            wm.pop("nbw_lights_active", None)
            wm.pop("nbw_cancel_lights", None)
            wm.pop("nbw_mode_dirty", None)
    except Exception:
        import traceback
        traceback.print_exc()


def register():
    bpy.app.timers.register(_nbw_purge_stale_flags, first_interval=0.0)

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.WindowManager.nbw_lights_active = bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    bpy.types.WindowManager.nbw_cancel_lights = bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    bpy.types.WindowManager.nbw_mode_dirty = bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    bpy.types.Scene.use_symmetry = bpy.props.BoolProperty(name="Symmetry", default=False)
    bpy.types.Scene.nbw_light_linking = bpy.props.PointerProperty(type=NBW_LightLinking)
    bpy.types.Scene.nbw_ui_category = bpy.props.EnumProperty(
        items=[
            ('UPLIGHT', "Up", ""),
            ('DOWNLIGHT', "Down", ""),
            ('POINT', "Point", ""),
            ('TARGETED', "Targeted", ""),
            ('AIMED', "Aimed", "")
        ],
        name="Category",
        update=_nbw_on_ui_category_update,
    )

    if nbw_on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(nbw_on_load_post)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
        kmi = km.keymap_items.new("lighting.place_procedural_light", 'L', 'PRESS', ctrl=True, shift=True)
        addon_keymaps.append((km, kmi))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    if nbw_on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(nbw_on_load_post)

    bpy.app.timers.register(_nbw_purge_stale_flags, first_interval=0.0)

    del bpy.types.WindowManager.nbw_lights_active
    del bpy.types.WindowManager.nbw_cancel_lights
    del bpy.types.WindowManager.nbw_mode_dirty
    del bpy.types.Scene.use_symmetry
    del bpy.types.Scene.nbw_light_linking
    del bpy.types.Scene.nbw_ui_category

    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

if __name__ == "__main__":
    register()
