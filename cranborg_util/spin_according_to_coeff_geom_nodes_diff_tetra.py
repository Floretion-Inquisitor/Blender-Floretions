import bpy
import math
from mathutils import Vector

# ============================================================
# CONFIG
# ============================================================

TARGET_OBJECT_NAME = None   # es. "Flo_XY", oppure None = oggetto attivo
FILTER_BIN = None           # es. 5, oppure None = tutte

BIN_ATTRIBUTE_NAME = "neighbors_edges_and_verts"
COEFF_ATTR_NAME = "face_coeff"
ORIENTATION_ATTR_NAME = "tile_orientation_sign"   # verrà creato se manca
BASE_DEC_ATTR_NAME = "base_dec"

# Attributi scritti da questo script
SPEED_ATTR_NAME = "spin_speed_factor"
DIRECTION_ATTR_NAME = "spin_direction"

START_FRAME = 1
END_FRAME = 121

ROT_AXIS = "Z"
SOURCE_INPLANE_OFFSET_Z = math.pi / 2.0
POSITIVE_DIRECTION_FACTOR = -1.0
MAX_REVOLUTIONS = 1.0

ZERO_EPS = 1e-12
INCLUDE_ZERO_TILES = False
ANIMATE_ZERO_COEFF_TILES = False
ZERO_COEFF_DIRECTION = 1.0

# Se True: la velocità viene dai bin/VG.
# Se False: tutte le facce selezionate hanno speed = 1.0
USE_VG_SPEEDS = False

VG_SPEEDS = {
    0: 0.00,
    1: 0.15,
    2: 0.30,
    3: 0.45,
    4: 0.60,
    5: 0.75,
    6: 0.90,
    7: 1.00,
}

REMOVE_SELECTED_FROM_BASE = True
APPLY_SOURCE_MATERIAL = True

NODE_GROUP_NAME = "Floretion_TileSpin_GN_FaceNormalAuto"
MODIFIER_NAME = "Floretion_TileSpin_GN_FaceNormalMod"
REPLACE_EXISTING = True


# ============================================================
# HELPERS BASE
# ============================================================

def force_object_mode():
    try:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def choose_target_object():
    obj = bpy.data.objects.get(TARGET_OBJECT_NAME) if TARGET_OBJECT_NAME else bpy.context.active_object
    if obj is None:
        raise RuntimeError("Nessun oggetto target trovato.")
    if obj.type != 'MESH':
        raise RuntimeError(f"L'oggetto '{obj.name}' non è una mesh.")
    if obj.name.endswith("__BASE_HOLE"):
        raise RuntimeError(
            f"L'oggetto attivo '{obj.name}' sembra un output temporaneo. "
            f"Seleziona l'oggetto base Flo_* oppure imposta TARGET_OBJECT_NAME."
        )
    if obj.name.endswith("_cent") or obj.name.endswith("_curve"):
        raise RuntimeError(
            f"L'oggetto '{obj.name}' è un helper cent/curve. "
            f"Seleziona Flo_* oppure Flo_*_tetra."
        )
    return obj


def remove_modifier_if_present(obj, name):
    mod = obj.modifiers.get(name)
    if mod is not None:
        obj.modifiers.remove(mod)


def remove_node_group_if_present(name):
    ng = bpy.data.node_groups.get(name)
    if ng is not None:
        bpy.data.node_groups.remove(ng)


def ensure_modifier(obj, node_group):
    mod = obj.modifiers.get(MODIFIER_NAME)
    if mod is None:
        mod = obj.modifiers.new(MODIFIER_NAME, "NODES")
    mod.node_group = node_group
    return mod


def clamp_bin(v: int) -> int:
    if v < 0:
        return 0
    if v > 7:
        return 7
    return v


def compute_reasonable_triangle_side(obj):
    me = obj.data
    verts = me.vertices
    polys = me.polygons

    lengths = []
    for poly in polys[: min(400, len(polys))]:
        vids = list(poly.vertices)
        if len(vids) < 3:
            continue
        for i in range(len(vids)):
            a = verts[vids[i]].co
            b = verts[vids[(i + 1) % len(vids)]].co
            lengths.append((a - b).length)

    if not lengths:
        return 1.0

    lengths.sort()
    return lengths[len(lengths) // 2]


def get_face_attr_values_float(me, attr_name: str):
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != 'FACE':
        raise RuntimeError(f"Attributo FACE '{attr_name}' non trovato sulla mesh '{me.name}'.")
    vals = []
    for poly in me.polygons:
        try:
            vals.append(float(attr.data[poly.index].value))
        except Exception:
            vals.append(0.0)
    return vals


def get_face_attr_values_int(me, attr_name: str):
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != 'FACE':
        raise RuntimeError(f"Attributo FACE '{attr_name}' non trovato sulla mesh '{me.name}'.")
    vals = []
    for poly in me.polygons:
        try:
            vals.append(int(round(float(attr.data[poly.index].value))))
        except Exception:
            vals.append(0)
    return vals


def ensure_face_float_attribute_strict(me, name: str):
    poly_count = len(me.polygons)
    if poly_count <= 0:
        raise RuntimeError(f"La mesh '{me.name}' non ha facce.")

    attr = me.attributes.get(name)
    recreate = False

    if attr is None:
        recreate = True
    else:
        if attr.domain != 'FACE' or attr.data_type != 'FLOAT':
            recreate = True
        else:
            try:
                if len(attr.data) != poly_count:
                    recreate = True
            except Exception:
                recreate = True

    if recreate:
        if attr is not None:
            try:
                me.attributes.remove(attr)
            except Exception:
                pass
        me.update()
        attr = me.attributes.new(name=name, type='FLOAT', domain='FACE')
        me.update()

    if len(attr.data) != poly_count:
        raise RuntimeError(
            f"Attributo '{name}' incoerente: len(data)={len(attr.data)} vs poly_count={poly_count}"
        )

    return attr


def get_preferred_material(obj):
    for slot in obj.material_slots:
        mat = slot.material
        if mat and mat.name == "FloretionMaterial":
            return mat

    for slot in obj.material_slots:
        mat = slot.material
        if mat and mat.name.startswith("FloretionMaterial"):
            return mat

    for slot in obj.material_slots:
        mat = slot.material
        if mat is not None:
            return mat

    return None


def refresh_mesh_datablock_copy(obj):
    old_me = obj.data
    new_me = old_me.copy()
    obj.data = new_me
    return new_me


# ============================================================
# ORIENTATION AUTO
# ============================================================

def orientation_sign_from_oct_string(oct_str: str) -> float:
    """
    Replica la logica usata in triangleize / mesh_build:
    count di 1,2,4 e parità dell'ordine.
    Convenzione:
      up   -> +1
      down -> -1
    """
    oct_str = str(oct_str).strip()
    count = sum(1 for d in oct_str if d in "124")
    order = len(oct_str)

    if count % 2 == 0:
        return 1.0 if order % 2 == 0 else -1.0
    return -1.0 if order % 2 == 0 else 1.0


def compute_orientation_from_base_dec(me):
    attr = me.attributes.get(BASE_DEC_ATTR_NAME)
    if attr is None or attr.domain != 'FACE':
        return None

    values = []
    for poly in me.polygons:
        try:
            base_dec = int(round(float(attr.data[poly.index].value)))
            oct_str = format(base_dec, "o")
            values.append(orientation_sign_from_oct_string(oct_str))
        except Exception:
            values.append(0.0)
    return values


def compute_orientation_from_face_geometry(me):
    """
    Fallback geometrico:
    trova il vertice della faccia più distante dal centro lungo Y locale.
    Se è sopra il centro -> up (+1), altrimenti down (-1).

    Per la geometria FloXY classica questo funziona bene.
    """
    out = []
    for poly in me.polygons:
        coords = [me.vertices[vi].co.copy() for vi in poly.vertices]
        if len(coords) < 3:
            out.append(0.0)
            continue

        center = sum(coords, Vector((0.0, 0.0, 0.0))) / len(coords)
        rel = [co - center for co in coords]

        apex = max(rel, key=lambda v: abs(v.y))
        sign = 1.0 if apex.y >= 0.0 else -1.0
        out.append(sign)

    return out


def ensure_orientation_attribute(me):
    """
    Garantisce che ORIENTATION_ATTR_NAME esista come FACE/FLOAT.
    Priorità:
      1) usa l'attributo già esistente se contiene valori non-zero
      2) altrimenti calcola da base_dec
      3) altrimenti calcola dalla geometria faccia
    """
    poly_count = len(me.polygons)
    orient_attr = ensure_face_float_attribute_strict(me, ORIENTATION_ATTR_NAME)

    existing = []
    try:
        existing = [float(orient_attr.data[i].value) for i in range(poly_count)]
    except Exception:
        existing = []

    unique_nonzero = {int(v) for v in existing if abs(v) > 0.5}
    if unique_nonzero:
        return existing, "existing_attr"

    vals = compute_orientation_from_base_dec(me)
    source = "base_dec"

    if vals is None:
        vals = compute_orientation_from_face_geometry(me)
        source = "face_geometry"

    orient_attr.data.foreach_set("value", vals)
    me.update()
    return vals, source


# ============================================================
# WRITE FACE ATTRS: SPEED + DIRECTION
# ============================================================

def _write_attr_values_foreach(attr, values):
    if len(attr.data) != len(values):
        raise RuntimeError(
            f"Lunghezza mismatch su '{attr.name}': len(data)={len(attr.data)} vs len(values)={len(values)}"
        )
    attr.data.foreach_set("value", values)


def _write_spin_attrs_impl(obj):
    me = obj.data
    poly_count = len(me.polygons)
    if poly_count <= 0:
        raise RuntimeError(f"La mesh '{me.name}' non ha facce.")

    coeff_vals = get_face_attr_values_float(me, COEFF_ATTR_NAME)
    bin_vals = get_face_attr_values_int(me, BIN_ATTRIBUTE_NAME)
    orient_vals, orient_source = ensure_orientation_attribute(me)

    speed_attr = ensure_face_float_attribute_strict(me, SPEED_ATTR_NAME)
    dir_attr = ensure_face_float_attribute_strict(me, DIRECTION_ATTR_NAME)

    speed_values = [0.0] * poly_count
    dir_values = [0.0] * poly_count

    unique_orients = set()
    selected_count = 0

    for i in range(poly_count):
        coeff = float(coeff_vals[i])
        orient = float(orient_vals[i])
        bin_id = clamp_bin(int(bin_vals[i]))

        if orient > 0.0:
            unique_orients.add(1)
        elif orient < 0.0:
            unique_orients.add(-1)
        else:
            unique_orients.add(0)

        if FILTER_BIN is not None and bin_id != int(FILTER_BIN):
            continue

        selected = False
        if INCLUDE_ZERO_TILES:
            selected = True
        else:
            selected = abs(coeff) > ZERO_EPS

        if not selected:
            continue

        selected_count += 1

        if USE_VG_SPEEDS:
            speed = max(0.0, min(1.0, float(VG_SPEEDS.get(bin_id, 0.0))))
        else:
            speed = 1.0

        if abs(coeff) <= ZERO_EPS:
            if ANIMATE_ZERO_COEFF_TILES:
                direction = float(ZERO_COEFF_DIRECTION)
            else:
                direction = 0.0
        else:
            direction = 1.0 if coeff > 0.0 else -1.0

        if direction == 0.0:
            speed = 0.0

        speed_values[i] = speed
        dir_values[i] = direction

    _write_attr_values_foreach(speed_attr, speed_values)
    _write_attr_values_foreach(dir_attr, dir_values)
    me.update()

    return {
        "selected_count": selected_count,
        "unique_orients": sorted(unique_orients),
        "orientation_source": orient_source,
    }


def write_spin_attrs(obj):
    try:
        return _write_spin_attrs_impl(obj)
    except Exception as e:
        print(f"[WARN] write_spin_attrs normal path failed: {e}")
        print("[INFO] Fallback: duplico il datablock mesh e riprovo...")
        refresh_mesh_datablock_copy(obj)
        return _write_spin_attrs_impl(obj)


# ============================================================
# GN HELPERS
# ============================================================

def ensure_geo_interface(node_tree):
    interface = node_tree.interface

    def has_socket(name, in_out):
        for item in interface.items_tree:
            try:
                if item.name == name and item.in_out == in_out:
                    return True
            except Exception:
                pass
        return False

    if not has_socket("Geometry", "INPUT"):
        interface.new_socket(
            name="Geometry",
            in_out='INPUT',
            socket_type='NodeSocketGeometry',
        )

    if not has_socket("Geometry", "OUTPUT"):
        interface.new_socket(
            name="Geometry",
            in_out='OUTPUT',
            socket_type='NodeSocketGeometry',
        )


def clear_node_tree(node_tree):
    node_tree.nodes.clear()


def new_node(node_tree, type_candidates, location, name=None, label=None):
    last_err = None
    for t in type_candidates:
        try:
            n = node_tree.nodes.new(t)
            n.location = location
            if name:
                n.name = name
            if label:
                n.label = label
            return n
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Impossibile creare nodo da candidati {type_candidates}: {last_err}")


def get_input(node, *names):
    for n in names:
        sock = node.inputs.get(n)
        if sock is not None:
            return sock
    if len(node.inputs):
        return node.inputs[0]
    return None


def get_output(node, *names):
    for n in names:
        sock = node.outputs.get(n)
        if sock is not None:
            return sock
    if len(node.outputs):
        return node.outputs[0]
    return None


def set_rotate_instances_local(node):
    for attr, value in (
        ("space", 'LOCAL'),
        ("transform_space", 'LOCAL'),
        ("local_space", True),
    ):
        try:
            setattr(node, attr, value)
            return
        except Exception:
            pass


# ============================================================
# BUILD NODE GROUP
# ============================================================

def build_node_group(obj, source_material):
    """
    Variante "tetra-safe":
    - usa i centroidi faccia come punti
    - allinea il triangolo istanziato alla normale della faccia
    - lo spin avviene in LOCAL Z, quindi anche sui tile tetraedrici gira attorno
      alla normale del tile e non attorno a un asse globale ubriaco.
    """
    if REPLACE_EXISTING:
        remove_node_group_if_present(NODE_GROUP_NAME)

    ng = bpy.data.node_groups.get(NODE_GROUP_NAME)
    if ng is None:
        ng = bpy.data.node_groups.new(NODE_GROUP_NAME, "GeometryNodeTree")

    ensure_geo_interface(ng)
    clear_node_tree(ng)
    links = ng.links

    side = compute_reasonable_triangle_side(obj)
    tri_radius = side / math.sqrt(3.0)
    duration = max(1.0, float(END_FRAME - START_FRAME))

    group_in = new_node(ng, ["NodeGroupInput"], (-2400, 0), "Group Input")
    group_out = new_node(ng, ["NodeGroupOutput"], (1700, 0), "Group Output")

    # --------------------------------------------------------
    # Animated selection: speed > 0
    # --------------------------------------------------------
    speed_named_face = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-2400, 260), "Face Speed")
    try:
        speed_named_face.data_type = 'FLOAT'
    except Exception:
        pass
    try:
        get_input(speed_named_face, "Name").default_value = SPEED_ATTR_NAME
    except Exception:
        pass

    cmp_speed = new_node(ng, ["FunctionNodeCompare"], (-2160, 260), "Speed > 0")
    try:
        cmp_speed.data_type = 'FLOAT'
    except Exception:
        pass
    try:
        cmp_speed.operation = 'GREATER_THAN'
    except Exception:
        pass
    try:
        get_input(cmp_speed, "B").default_value = 0.0
    except Exception:
        pass
    links.new(get_output(speed_named_face, "Attribute"), get_input(cmp_speed, "A"))

    separate_anim = new_node(ng, ["GeometryNodeSeparateGeometry"], (-2160, 0), "Separate Animated")
    try:
        separate_anim.domain = 'FACE'
    except Exception:
        pass
    links.new(get_output(group_in, "Geometry"), get_input(separate_anim, "Geometry"))
    links.new(get_output(cmp_speed, "Result"), get_input(separate_anim, "Selection"))

    # --------------------------------------------------------
    # Mesh -> Points (centri faccia)
    # --------------------------------------------------------
    face_points = new_node(ng, ["GeometryNodeMeshToPoints"], (-1880, 0), "Faces -> Points")
    try:
        face_points.mode = 'FACES'
    except Exception:
        pass
    links.new(get_output(separate_anim, "Selection"), get_input(face_points, "Mesh", "Geometry"))

    # Attr in point context
    speed_named_point = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-1880, -280), "Point Speed")
    dir_named_point = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-1640, -280), "Point Dir")
    for n in (speed_named_point, dir_named_point):
        try:
            n.data_type = 'FLOAT'
        except Exception:
            pass
    try:
        get_input(speed_named_point, "Name").default_value = SPEED_ATTR_NAME
        get_input(dir_named_point, "Name").default_value = DIRECTION_ATTR_NAME
    except Exception:
        pass

    # Normale faccia -> rotazione base dell'istanza
    normal_node = new_node(ng, ["GeometryNodeInputNormal"], (-1880, 300), "Normal")
    align_euler = new_node(ng, ["FunctionNodeAlignEulerToVector"], (-1640, 300), "Align Z to Normal")
    try:
        align_euler.axis = 'Z'
    except Exception:
        pass
    try:
        align_euler.pivot_axis = 'AUTO'
    except Exception:
        pass
    links.new(get_output(normal_node, "Normal", "Vector"), get_input(align_euler, "Vector"))

    # Sorgente triangolo
    mesh_circle = new_node(ng, ["GeometryNodeMeshCircle"], (-1400, 0), "Triangle Source")
    try:
        get_input(mesh_circle, "Vertices").default_value = 3
    except Exception:
        pass
    try:
        get_input(mesh_circle, "Radius").default_value = tri_radius
    except Exception:
        pass
    try:
        mesh_circle.fill_type = 'NGON'
    except Exception:
        pass

    inst = new_node(ng, ["GeometryNodeInstanceOnPoints"], (-1120, 0), "Instance on Points")
    links.new(get_output(face_points, "Points"), get_input(inst, "Points", "Geometry"))
    links.new(get_output(mesh_circle, "Mesh", "Geometry"), get_input(inst, "Instance"))
    try:
        links.new(get_output(align_euler, "Rotation", "Euler"), get_input(inst, "Rotation"))
    except Exception:
        pass

    # --------------------------------------------------------
    # Time angle = phase * tau * max_revolutions
    # --------------------------------------------------------
    scene_time = new_node(ng, ["GeometryNodeInputSceneTime"], (-1880, -620), "Scene Time")

    frame_minus_start = new_node(ng, ["ShaderNodeMath"], (-1640, -620), "Frame - Start")
    frame_minus_start.operation = 'SUBTRACT'
    frame_minus_start.inputs[1].default_value = float(START_FRAME)

    div_duration = new_node(ng, ["ShaderNodeMath"], (-1400, -620), "/ Duration")
    div_duration.operation = 'DIVIDE'
    div_duration.inputs[1].default_value = duration

    clamp_phase = new_node(ng, ["ShaderNodeClamp"], (-1160, -620), "Clamp Phase")
    mul_tau = new_node(ng, ["ShaderNodeMath"], (-920, -620), "* tau")
    mul_tau.operation = 'MULTIPLY'
    mul_tau.inputs[1].default_value = math.tau

    mul_rev = new_node(ng, ["ShaderNodeMath"], (-680, -620), "* MaxRev")
    mul_rev.operation = 'MULTIPLY'
    mul_rev.inputs[1].default_value = float(MAX_REVOLUTIONS)

    links.new(get_output(scene_time, "Frame"), frame_minus_start.inputs[0])
    links.new(get_output(frame_minus_start, "Value"), div_duration.inputs[0])
    links.new(get_output(div_duration, "Value"), clamp_phase.inputs["Value"])
    links.new(get_output(clamp_phase, "Result"), mul_tau.inputs[0])
    links.new(get_output(mul_tau, "Value"), mul_rev.inputs[0])

    # angle = base_roll + phase * speed * direction * sign
    angle_by_speed = new_node(ng, ["ShaderNodeMath"], (-440, -220), "Angle * Speed")
    angle_by_speed.operation = 'MULTIPLY'
    angle_by_dir = new_node(ng, ["ShaderNodeMath"], (-200, -220), "Angle * Dir")
    angle_by_dir.operation = 'MULTIPLY'
    angle_by_sign = new_node(ng, ["ShaderNodeMath"], (40, -220), "Angle * Sign")
    angle_by_sign.operation = 'MULTIPLY'
    angle_plus_base = new_node(ng, ["ShaderNodeMath"], (280, -220), "Angle + BaseRoll")
    angle_plus_base.operation = 'ADD'
    angle_plus_base.inputs[1].default_value = float(SOURCE_INPLANE_OFFSET_Z)

    links.new(get_output(mul_rev, "Value"), angle_by_speed.inputs[0])
    links.new(get_output(speed_named_point, "Attribute"), angle_by_speed.inputs[1])
    links.new(get_output(angle_by_speed, "Value"), angle_by_dir.inputs[0])
    links.new(get_output(dir_named_point, "Attribute"), angle_by_dir.inputs[1])
    links.new(get_output(angle_by_dir, "Value"), angle_by_sign.inputs[0])
    angle_by_sign.inputs[1].default_value = float(POSITIVE_DIRECTION_FACTOR)
    links.new(get_output(angle_by_sign, "Value"), angle_plus_base.inputs[0])

    spin_vec = new_node(ng, ["ShaderNodeCombineXYZ"], (520, -220), "Spin Vec Local Z")
    links.new(get_output(angle_plus_base, "Value"), get_input(spin_vec, "Z"))

    rotate_inst = new_node(ng, ["GeometryNodeRotateInstances"], (760, 0), "Rotate Instances")
    set_rotate_instances_local(rotate_inst)
    links.new(get_output(inst, "Instances"), get_input(rotate_inst, "Instances"))
    links.new(get_output(spin_vec, "Vector"), get_input(rotate_inst, "Rotation"))

    realize = new_node(ng, ["GeometryNodeRealizeInstances"], (1000, 0), "Realize")
    links.new(get_output(rotate_inst, "Instances"), get_input(realize, "Geometry"))

    anim_geo = get_output(realize, "Geometry")

    if APPLY_SOURCE_MATERIAL and source_material is not None:
        set_mat = new_node(ng, ["GeometryNodeSetMaterial"], (1240, 0), "Set Material")
        links.new(anim_geo, get_input(set_mat, "Geometry"))
        try:
            get_input(set_mat, "Material").default_value = source_material
        except Exception:
            pass
        anim_geo = get_output(set_mat, "Geometry")

    final_join = new_node(ng, ["GeometryNodeJoinGeometry"], (1480, 0), "Join Final")
    links.new(anim_geo, get_input(final_join, "Geometry"))

    if REMOVE_SELECTED_FROM_BASE:
        links.new(get_output(separate_anim, "Inverted"), get_input(final_join, "Geometry"))
    else:
        links.new(get_output(group_in, "Geometry"), get_input(final_join, "Geometry"))

    links.new(get_output(final_join, "Geometry"), get_input(group_out, "Geometry"))
    return ng


# ============================================================
# MAIN
# ============================================================

force_object_mode()

obj = choose_target_object()
source_material = get_preferred_material(obj)

if REPLACE_EXISTING:
    remove_modifier_if_present(obj, MODIFIER_NAME)

stats = write_spin_attrs(obj)
ng = build_node_group(obj, source_material)
mod = ensure_modifier(obj, ng)

print("\n=== Geometry Nodes tile spin (flat + tetra) pronto ===")
print(f"Oggetto: {obj.name}")
print(f"Node group: {ng.name}")
print(f"Modifier: {mod.name}")
print(f"Filtro bin: {FILTER_BIN}")
print(f"Facce animate: {stats['selected_count']}")
print(f"Orientazioni trovate in '{ORIENTATION_ATTR_NAME}': {stats['unique_orients']}")
print(f"Sorgente orientazione: {stats['orientation_source']}")
print(f"Asse locale di spin: {ROT_AXIS}")
print(f"Base roll around local normal: {SOURCE_INPLANE_OFFSET_Z}")
print("Premi Play nella timeline.")