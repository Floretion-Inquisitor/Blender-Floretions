import bpy
from bpy.types import Operator
import bmesh
import math
from mathutils import Vector

# ============================================================
# CONFIG
# ============================================================

TARGET_OBJECT_NAME = None   # es. "Flo_XY_tetra", oppure None = oggetto attivo
FILTER_BIN = None           # es. 5, oppure None = tutte

BIN_ATTRIBUTE_NAME = "neighbors_edges_and_verts"
COEFF_ATTR_NAME = "face_coeff"
BASE_DEC_ATTR_NAME = "base_dec"

# Attributi FACE legacy (scritti/aggiornati dallo script)
SPEED_ATTR_NAME = "spin_speed_factor"
DIRECTION_ATTR_NAME = "spin_direction"

# Attributi POINT per il path "in-place"
POINT_SELECTED_ATTR_NAME = "__spin_sel_pt"
POINT_CENTER_ATTR_NAME = "__spin_center_pt"
POINT_AXIS_X_ATTR_NAME = "__spin_axis_x_pt"
POINT_AXIS_Y_ATTR_NAME = "__spin_axis_y_pt"
POINT_AXIS_Z_ATTR_NAME = "__spin_axis_z_pt"
POINT_DIRECTION_ATTR_NAME = "__spin_dir_pt"
POINT_BIN_ATTR_NAME = "__spin_bin_pt"
POINT_COEFF_ABS_ATTR_NAME = "__spin_coeff_abs_pt"
POINT_BIN_MASK_PREFIX = "__spin_vgmask_"


def make_bin_mask_attr_name(bin_id: int) -> str:
    return f"{POINT_BIN_MASK_PREFIX}{int(bin_id)}"

USE_SCENE_FRAME_RANGE = True

# Usati solo se USE_SCENE_FRAME_RANGE = False
START_FRAME = 1
END_FRAME = 121

# Il verso base viene ancora dal segno del coefficiente.
POSITIVE_DIRECTION_FACTOR = -1.0
MAX_REVOLUTIONS = 1.0
LOOP = False
REVERSE = False

ZERO_EPS = 1e-12
INCLUDE_ZERO_TILES = False
ANIMATE_ZERO_COEFF_TILES = False
ZERO_COEFF_DIRECTION = 1.0

# Modalità velocità (controllate nel modifier)
# 0 = Uniform
# 1 = VG Speeds
# 2 = Base coeff speeds (linear)
# 3 = Base coeff speeds (log)
DEFAULT_SPEED_MODE = 0

# Default richiesto: Uniform attivo e VG speeds inizialmente tutte a zero.
VG_SPEEDS = {
    0: 0.00,
    1: 0.00,
    2: 0.00,
    3: 0.00,
    4: 0.00,
    5: 0.00,
    6: 0.00,
    7: 0.00,
}

ZERO_ALL_VG_SPEEDS = False
UNIFORM_SPEED = 1.0
COEFF_LINEAR_SCALE = 1.0
COEFF_LOG_SCALE = 0.35
SPEED_CLAMP = 4.0

# Assi locali del tile.
# X = direzione centro -> primo vertice del triangolo (il vecchio "trucco" utile per il vertice alto)
# Y = asse in piano perpendicolare a X
# Z = normale di faccia
AXIS_LOCAL_X = True
AXIS_LOCAL_Y = False
AXIS_LOCAL_Z = False

NODE_GROUP_BASENAME = "Floretion_TileSpin_GN_InPlace"
MODIFIER_BASENAME = "Floretion_TileSpin_GN_Mod_InPlace"
REPLACE_EXISTING = True

# Sicurezza:
# questo path assume tile triangolari con vertici non condivisi.
STRICT_REQUIRE_UNSHARED_TRIANGLE_VERTS = True
AUTO_FIX_SHARED_VERTS = True
AUTO_FIX_GHOST_ATTRS = True


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
    return obj


def sanitize_name(s: str) -> str:
    out = []
    for ch in str(s):
        out.append(ch if ch.isalnum() or ch in "._-" else "_")
    return "".join(out)


def make_node_group_name(obj):
    return f"{NODE_GROUP_BASENAME}__{sanitize_name(obj.name)}"


def make_modifier_name(obj):
    return f"{MODIFIER_BASENAME}__{sanitize_name(obj.name)}"


def has_spin_modifier(obj) -> bool:
    if obj is None:
        return False
    try:
        for mod in getattr(obj, "modifiers", []):
            if getattr(mod, "type", "") == 'NODES' and str(getattr(mod, "name", "")).startswith(MODIFIER_BASENAME):
                return True
    except Exception:
        pass
    return False


def refresh_spin_if_present(obj, *, preserve_selection: bool = True):
    """
    Se l'oggetto ha già un modifier Spin VGs, aggiorna gli attributi custom
    richiesti dal node-tree senza ricreare il modifier e senza perdere
    i valori già impostati nel pannello del modifier.
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False

    if not has_spin_modifier(obj):
        return False

    prev_active = None
    prev_selected = []
    if preserve_selection:
        try:
            vl = bpy.context.view_layer
            prev_active = getattr(vl.objects, "active", None)
            prev_selected = [o for o in bpy.context.selected_objects]
        except Exception:
            prev_active = None
            prev_selected = []

    try:
        force_object_mode()
        try:
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
        except Exception:
            pass
        write_spin_attrs(obj)
        return True
    finally:
        if preserve_selection:
            try:
                for o in bpy.context.selected_objects:
                    o.select_set(False)
                for o in prev_selected:
                    try:
                        o.select_set(True)
                    except Exception:
                        pass
                if prev_active is not None:
                    bpy.context.view_layer.objects.active = prev_active
            except Exception:
                pass


def refresh_spin_targets_if_present(context=None, *, target_names=None):
    """
    Aggiorna automaticamente lo spin su una lista di oggetti se hanno già
    il relativo modifier. Utile dopo rebuild/recolor del mesh.
    """
    if target_names is None:
        target_names = [
            "Flo_X", "Flo_Y", "Flo_XY",
            "Flo_X_tetra", "Flo_Y_tetra", "Flo_XY_tetra",
        ]

    changed = 0
    for name in target_names:
        try:
            obj = bpy.data.objects.get(str(name))
        except Exception:
            obj = None
        if obj is None:
            continue
        try:
            if refresh_spin_if_present(obj):
                changed += 1
        except Exception as e:
            print(f"[WARN] Spin refresh failed for {getattr(obj, 'name', name)}: {e}")
    return changed



def remove_modifier_if_present(obj, name):
    mod = obj.modifiers.get(name)
    if mod is not None:
        obj.modifiers.remove(mod)


def remove_node_group_if_present(name):
    ng = bpy.data.node_groups.get(name)
    if ng is not None:
        bpy.data.node_groups.remove(ng)


def _clear_driver_fcurve_if_possible(id_owner, data_path: str):
    try:
        if id_owner.animation_data and id_owner.animation_data.drivers:
            for fcu in list(id_owner.animation_data.drivers):
                if fcu.data_path == data_path:
                    id_owner.driver_remove(data_path)
                    break
    except Exception:
        pass


def add_prop_driver(id_owner, data_path: str, target_id, *, id_type: str, target_data_path: str):
    _clear_driver_fcurve_if_possible(id_owner, data_path)
    fcu = id_owner.driver_add(data_path)
    drv = fcu.driver
    drv.type = 'SCRIPTED'
    drv.expression = "v"
    while drv.variables:
        drv.variables.remove(drv.variables[0])
    var = drv.variables.new()
    var.name = "v"
    targ = var.targets[0]
    targ.id_type = id_type
    targ.id = target_id
    targ.data_path = target_data_path
    return fcu


def add_single_prop_driver(id_owner, data_path: str, scene, scene_data_path: str):
    return add_prop_driver(
        id_owner,
        data_path,
        scene,
        id_type='SCENE',
        target_data_path=scene_data_path,
    )


def ensure_modifier(obj, node_group):
    mod_name = make_modifier_name(obj)
    mod = obj.modifiers.get(mod_name)
    if mod is None:
        mod = obj.modifiers.new(mod_name, "NODES")
    mod.node_group = node_group
    return mod


def clamp_bin(v: int) -> int:
    if v < 0:
        return 0
    if v > 7:
        return 7
    return v


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



def ensure_attr_strict(me, name: str, domain: str, data_type: str):
    expected_len = len(me.polygons) if domain == 'FACE' else len(me.vertices)
    if expected_len <= 0:
        raise RuntimeError(f"Nessun elemento disponibile per attr '{name}' su domain {domain}.")

    attr = me.attributes.get(name)
    recreate = False

    if attr is None:
        recreate = True
    else:
        if attr.domain != domain or attr.data_type != data_type:
            recreate = True
        else:
            try:
                if len(attr.data) != expected_len:
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
        attr = me.attributes.new(name=name, type=data_type, domain=domain)
        me.update()
        # Blender ogni tanto crea attr "fantasma" con len(data)=0.
        # Ripeschiamo il riferimento fresco e ritentiamo una volta.
        attr = me.attributes.get(name)

    if attr is None or len(attr.data) != expected_len:
        raise RuntimeError(
            f"Attributo '{name}' incoerente: len(data)={0 if attr is None else len(attr.data)} vs expected_len={expected_len}"
        )

    return attr


def purge_spin_attrs(me):
    names = [
        SPEED_ATTR_NAME,
        DIRECTION_ATTR_NAME,
        POINT_SELECTED_ATTR_NAME,
        POINT_CENTER_ATTR_NAME,
        POINT_AXIS_ATTR_NAME,
        POINT_DIRECTION_ATTR_NAME,
        POINT_BIN_ATTR_NAME,
    ] + [make_bin_mask_attr_name(i) for i in range(8)]
    for nm in names:
        attr = me.attributes.get(nm)
        if attr is not None:
            try:
                me.attributes.remove(attr)
            except Exception:
                pass
    me.update()


def refresh_mesh_datablock_copy(obj):
    """
    Copia "pulita" del datablock via BMesh.
    È più robusta di me.copy() contro alcuni attr ghost len(data)=0.
    """
    old_me = obj.data
    bm = bmesh.new()
    bm.from_mesh(old_me)

    new_me = bpy.data.meshes.new(name=f"{old_me.name}_spinTmp")
    bm.to_mesh(new_me)
    bm.free()
    new_me.update()

    # Copia i material slots referenziati dal datablock vecchio tramite l'oggetto.
    obj.data = new_me
    return new_me


def make_vertices_unshared_per_face(obj):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    if bm.edges:
        bmesh.ops.split_edges(bm, edges=list(bm.edges))
    bm.to_mesh(me)
    bm.free()
    me.update()
    return me


def inspect_triangle_tile_topology(me):

    poly_count = len(me.polygons)
    vert_count = len(me.vertices)

    if poly_count <= 0:
        raise RuntimeError(f"La mesh '{me.name}' non ha facce.")

    non_tri_faces = [p.index for p in me.polygons if len(p.vertices) != 3]
    if non_tri_faces:
        raise RuntimeError(
            f"La mesh '{me.name}' non è composta solo da triangoli. "
            f"Prime facce non triangolari: {non_tri_faces[:8]}"
        )

    vertex_use_counts = [0] * vert_count
    for poly in me.polygons:
        for vid in poly.vertices:
            vertex_use_counts[vid] += 1

    shared_vertices = [i for i, c in enumerate(vertex_use_counts) if c != 1]

    return {
        "poly_count": poly_count,
        "vert_count": vert_count,
        "all_triangles": len(non_tri_faces) == 0,
        "all_vertices_single_use": len(shared_vertices) == 0,
        "shared_vertices_preview": shared_vertices[:12],
    }



def validate_topology_for_inplace_spin(obj):
    me = obj.data
    info = inspect_triangle_tile_topology(me)

    if not info["all_triangles"]:
        raise RuntimeError(
            "Questo script richiede tile triangolari puri. "
            "La mesh contiene facce non triangolari."
        )

    if STRICT_REQUIRE_UNSHARED_TRIANGLE_VERTS and not info["all_vertices_single_use"]:
        if AUTO_FIX_SHARED_VERTS:
            print(
                "[WARN] Trovati vertici condivisi tra facce. "
                "Provo a separarli automaticamente (split edges) e riprovo..."
            )
            make_vertices_unshared_per_face(obj)
            me = obj.data
            info = inspect_triangle_tile_topology(me)

        if not info["all_vertices_single_use"]:
            raise RuntimeError(
                "Questo path GN ruota i tile spostando i vertici originali. "
                "Quindi richiede vertici non condivisi tra facce. "
                f"Vertici problematici (preview): {info['shared_vertices_preview']}"
            )

    return info


# ============================================================
# WRITE FACE ATTRS: SPEED + DIRECTION
# ============================================================



def _write_attr_values_foreach(attr, values, key="value", attr_name_override=None):
    owner = getattr(attr, "id_data", None)

    try:
        attr_name = str(attr_name_override or getattr(attr, "name", "") or "")
    except Exception:
        attr_name = str(attr_name_override or "<attr-name-unreadable>")

    try:
        attr_domain = getattr(attr, "domain", None)
    except Exception:
        attr_domain = None

    try:
        attr_type = getattr(attr, "data_type", None)
    except Exception:
        attr_type = None

    def _refresh_attr():
        nonlocal attr
        if owner is not None and hasattr(owner, "attributes") and attr_name_override:
            try:
                attr = owner.attributes.get(attr_name_override)
            except Exception:
                attr = None
            if attr is None and AUTO_FIX_GHOST_ATTRS and attr_type is not None and attr_domain is not None:
                try:
                    attr = owner.attributes.new(name=attr_name_override, type=attr_type, domain=attr_domain)
                    owner.update()
                    attr = owner.attributes.get(attr_name_override)
                except Exception:
                    attr = None
        return attr

    def _expected_prop_name(a):
        dt = getattr(a, "data_type", None)
        if dt in {"FLOAT_VECTOR", "FLOAT2", "FLOAT_VECTOR_2D"}:
            return "vector"
        if dt in {"FLOAT_COLOR", "BYTE_COLOR"}:
            return "color"
        return "value"

    def _coerce_scalar_list(vals, dt):
        if dt == "BOOLEAN":
            return [bool(v) for v in vals]
        if dt in {"INT", "INT8", "INT32", "BYTE_INT"}:
            return [int(v) for v in vals]
        return [float(v) for v in vals]

    attr = _refresh_attr()
    if attr is None:
        raise RuntimeError(f"Impossibile recuperare l'attributo '{attr_name}'.")

    expected_prop = _expected_prop_name(attr)
    if key != expected_prop:
        key = expected_prop

    expected_len = len(values)
    if len(attr.data) != expected_len and AUTO_FIX_GHOST_ATTRS and owner is not None and hasattr(owner, "attributes"):
        try:
            owner.attributes.remove(attr)
        except Exception:
            pass
        owner.update()
        recreate_name = attr_name_override or attr_name
        if recreate_name and attr_type is not None and attr_domain is not None:
            attr = owner.attributes.new(name=recreate_name, type=attr_type, domain=attr_domain)
            owner.update()
            attr = owner.attributes.get(recreate_name)
            if attr is not None:
                key = _expected_prop_name(attr)

    if attr is None or len(attr.data) != expected_len:
        raise RuntimeError(
            f"Lunghezza mismatch su '{attr_name}': len(data)={0 if attr is None else len(attr.data)} vs len(values)={expected_len}"
        )

    attr_dt = getattr(attr, "data_type", None)

    try:
        if key == "vector":
            flat = []
            for v in values:
                flat.extend((float(v[0]), float(v[1]), float(v[2])))
            attr.data.foreach_set("vector", flat)
        elif key == "color":
            flat = []
            for c in values:
                if len(c) == 4:
                    flat.extend((float(c[0]), float(c[1]), float(c[2]), float(c[3])))
                else:
                    flat.extend((float(c[0]), float(c[1]), float(c[2]), 1.0))
            attr.data.foreach_set("color", flat)
        else:
            attr.data.foreach_set("value", _coerce_scalar_list(values, attr_dt))
        return
    except Exception:
        pass

    scalar_mode = attr_dt not in {"FLOAT_VECTOR", "FLOAT2", "FLOAT_VECTOR_2D", "FLOAT_COLOR", "BYTE_COLOR"}

    for i, val in enumerate(values):
        item = attr.data[i]

        if scalar_mode:
            if not hasattr(item, "value"):
                raise RuntimeError(
                    f"Impossibile scrivere scalar attr '{attr_name}' di tipo {attr_dt}: la data item non espone '.value'."
                )
            if attr_dt == "BOOLEAN":
                item.value = bool(val)
            elif attr_dt in {"INT", "INT8", "INT32", "BYTE_INT"}:
                item.value = int(val)
            else:
                item.value = float(val)
            continue

        if attr_dt in {"FLOAT_VECTOR", "FLOAT2", "FLOAT_VECTOR_2D"}:
            if not hasattr(item, "vector"):
                raise RuntimeError(
                    f"Impossibile scrivere vector attr '{attr_name}' di tipo {attr_dt}: la data item non espone '.vector'."
                )
            if isinstance(val, (bool, int, float)):
                raise RuntimeError(
                    f"Valore non vettoriale passato a '{attr_name}': {val!r}"
                )
            vv = tuple(val)
            if len(vv) == 2:
                item.vector = (float(vv[0]), float(vv[1]))
            else:
                item.vector = (float(vv[0]), float(vv[1]), float(vv[2]))
            continue

        if attr_dt in {"FLOAT_COLOR", "BYTE_COLOR"}:
            if not hasattr(item, "color"):
                raise RuntimeError(
                    f"Impossibile scrivere color attr '{attr_name}' di tipo {attr_dt}: la data item non espone '.color'."
                )
            if isinstance(val, (bool, int, float)):
                raise RuntimeError(
                    f"Valore non colore passato a '{attr_name}': {val!r}"
                )
            cc = tuple(val)
            if len(cc) == 4:
                item.color = (float(cc[0]), float(cc[1]), float(cc[2]), float(cc[3]))
            else:
                item.color = (float(cc[0]), float(cc[1]), float(cc[2]), 1.0)
            continue

        raise RuntimeError(
            f"Impossibile scrivere l'attributo '{attr_name}' di tipo {attr_dt}: nessuna property compatibile trovata."
        )




def write_spin_face_attrs(me):

    poly_count = len(me.polygons)
    coeff_vals = get_face_attr_values_float(me, COEFF_ATTR_NAME)
    bin_vals = get_face_attr_values_int(me, BIN_ATTRIBUTE_NAME)

    speed_attr = ensure_attr_strict(me, SPEED_ATTR_NAME, 'FACE', 'FLOAT')
    dir_attr = ensure_attr_strict(me, DIRECTION_ATTR_NAME, 'FACE', 'FLOAT')

    speed_values = [0.0] * poly_count
    dir_values = [0.0] * poly_count
    bin_values = [0] * poly_count
    coeff_abs_values = [0.0] * poly_count
    selected_face_mask = [False] * poly_count
    selected_count = 0

    for i in range(poly_count):
        coeff = float(coeff_vals[i])
        coeff_abs = abs(coeff)
        coeff_abs_values[i] = coeff_abs
        bin_id = clamp_bin(int(bin_vals[i]))
        bin_values[i] = bin_id

        if FILTER_BIN is not None and bin_id != int(FILTER_BIN):
            continue

        selected = True if INCLUDE_ZERO_TILES else (coeff_abs > ZERO_EPS)
        if not selected:
            continue

        if coeff_abs <= ZERO_EPS:
            direction = float(ZERO_COEFF_DIRECTION) if ANIMATE_ZERO_COEFF_TILES else 0.0
        else:
            direction = 1.0 if coeff > 0.0 else -1.0

        # Legacy/debug only: il vero calcolo velocità ora avviene nel node tree.
        speed_values[i] = 1.0
        dir_values[i] = direction
        selected_face_mask[i] = True
        selected_count += 1

    _write_attr_values_foreach(speed_attr, speed_values, "value", SPEED_ATTR_NAME)
    _write_attr_values_foreach(dir_attr, dir_values, "value", DIRECTION_ATTR_NAME)
    me.update()

    return {
        "selected_count": selected_count,
        "selected_face_mask": selected_face_mask,
        "speed_values": speed_values,
        "dir_values": dir_values,
        "bin_values": bin_values,
        "coeff_abs_values": coeff_abs_values,
    }



def _safe_normalize(v: Vector, fallback=(0.0, 0.0, 1.0)) -> Vector:
    out = Vector(v)
    if out.length <= 1e-12:
        out = Vector(fallback)
    else:
        out.normalize()
    return out


def _compute_local_tile_axes(me, poly):
    center = poly.center.copy()
    normal = _safe_normalize(poly.normal, (0.0, 0.0, 1.0))

    verts = list(poly.vertices)
    if len(verts) >= 1:
        vx = me.vertices[verts[0]].co - center
        axis_x = _safe_normalize(vx, (1.0, 0.0, 0.0))
    else:
        axis_x = Vector((1.0, 0.0, 0.0))

    # Evita quasi-parallelismo patologico tra normal e axis_x
    if abs(axis_x.dot(normal)) > 0.999:
        if len(verts) >= 2:
            edge = me.vertices[verts[1]].co - center
            axis_x = _safe_normalize(edge, (1.0, 0.0, 0.0))
        if abs(axis_x.dot(normal)) > 0.999:
            trial = Vector((1.0, 0.0, 0.0))
            if abs(trial.dot(normal)) > 0.999:
                trial = Vector((0.0, 1.0, 0.0))
            axis_x = _safe_normalize(normal.cross(trial).cross(normal), (1.0, 0.0, 0.0))

    axis_y = normal.cross(axis_x)
    axis_y = _safe_normalize(axis_y, (0.0, 1.0, 0.0))
    # Ri-ortogonalizza X rispetto a Y e normal
    axis_x = _safe_normalize(axis_y.cross(normal), (1.0, 0.0, 0.0))

    return center, axis_x, axis_y, normal


def write_spin_point_attrs(obj):
    me = obj.data
    topo_info = validate_topology_for_inplace_spin(obj)
    me = obj.data
    face_stats = write_spin_face_attrs(me)

    sel_attr = ensure_attr_strict(me, POINT_SELECTED_ATTR_NAME, 'POINT', 'BOOLEAN')
    center_attr = ensure_attr_strict(me, POINT_CENTER_ATTR_NAME, 'POINT', 'FLOAT_VECTOR')
    axis_x_attr = ensure_attr_strict(me, POINT_AXIS_X_ATTR_NAME, 'POINT', 'FLOAT_VECTOR')
    axis_y_attr = ensure_attr_strict(me, POINT_AXIS_Y_ATTR_NAME, 'POINT', 'FLOAT_VECTOR')
    axis_z_attr = ensure_attr_strict(me, POINT_AXIS_Z_ATTR_NAME, 'POINT', 'FLOAT_VECTOR')
    dir_attr = ensure_attr_strict(me, POINT_DIRECTION_ATTR_NAME, 'POINT', 'FLOAT')
    bin_attr = ensure_attr_strict(me, POINT_BIN_ATTR_NAME, 'POINT', 'INT')
    coeff_abs_attr = ensure_attr_strict(me, POINT_COEFF_ABS_ATTR_NAME, 'POINT', 'FLOAT')
    mask_attrs = [ensure_attr_strict(me, make_bin_mask_attr_name(i), 'POINT', 'BOOLEAN') for i in range(8)]

    vcount = len(me.vertices)
    point_selected = [False] * vcount
    point_centers = [(0.0, 0.0, 0.0)] * vcount
    point_axes_x = [(1.0, 0.0, 0.0)] * vcount
    point_axes_y = [(0.0, 1.0, 0.0)] * vcount
    point_axes_z = [(0.0, 0.0, 1.0)] * vcount
    point_dirs = [0.0] * vcount
    point_bins = [0] * vcount
    point_coeff_abs = [0.0] * vcount
    point_bin_masks = [[False] * vcount for _ in range(8)]
    bin_face_counts = [0] * 8

    for poly in me.polygons:
        center, axis_x, axis_y, axis_z = _compute_local_tile_axes(me, poly)

        direction = float(face_stats["dir_values"][poly.index])
        selected = bool(face_stats["selected_face_mask"][poly.index])
        bin_id = int(face_stats["bin_values"][poly.index])
        coeff_abs = float(face_stats["coeff_abs_values"][poly.index])

        if selected and 0 <= bin_id <= 7:
            bin_face_counts[bin_id] += 1

        for vid in poly.vertices:
            point_selected[vid] = selected
            point_centers[vid] = (center.x, center.y, center.z)
            point_axes_x[vid] = (axis_x.x, axis_x.y, axis_x.z)
            point_axes_y[vid] = (axis_y.x, axis_y.y, axis_y.z)
            point_axes_z[vid] = (axis_z.x, axis_z.y, axis_z.z)
            point_dirs[vid] = direction
            point_bins[vid] = bin_id
            point_coeff_abs[vid] = coeff_abs
            for bi in range(8):
                point_bin_masks[bi][vid] = (selected and bin_id == bi)

    _write_attr_values_foreach(sel_attr, point_selected, "value", POINT_SELECTED_ATTR_NAME)
    _write_attr_values_foreach(center_attr, point_centers, "vector", POINT_CENTER_ATTR_NAME)
    _write_attr_values_foreach(axis_x_attr, point_axes_x, "vector", POINT_AXIS_X_ATTR_NAME)
    _write_attr_values_foreach(axis_y_attr, point_axes_y, "vector", POINT_AXIS_Y_ATTR_NAME)
    _write_attr_values_foreach(axis_z_attr, point_axes_z, "vector", POINT_AXIS_Z_ATTR_NAME)
    _write_attr_values_foreach(dir_attr, point_dirs, "value", POINT_DIRECTION_ATTR_NAME)
    _write_attr_values_foreach(bin_attr, point_bins, "value", POINT_BIN_ATTR_NAME)
    _write_attr_values_foreach(coeff_abs_attr, point_coeff_abs, "value", POINT_COEFF_ABS_ATTR_NAME)
    for bi in range(8):
        _write_attr_values_foreach(mask_attrs[bi], point_bin_masks[bi], "value", make_bin_mask_attr_name(bi))
    me.update()

    return {
        "selected_count": face_stats["selected_count"],
        "topology": topo_info,
        "bin_face_counts": bin_face_counts,
    }


def write_spin_attrs(obj):
    try:
        return write_spin_point_attrs(obj)
    except Exception as e:
        print(f"[WARN] write_spin_attrs normal path failed: {e}")
        print("[INFO] Fallback 1: purgo attrs spin, duplico pulito il datablock mesh e riprovo...")
        purge_spin_attrs(obj.data)
        refresh_mesh_datablock_copy(obj)
        try:
            return write_spin_point_attrs(obj)
        except Exception as e2:
            print(f"[WARN] write_spin_attrs fallback 1 failed: {e2}")
            if AUTO_FIX_SHARED_VERTS:
                print("[INFO] Fallback 2: split degli edge per separare i vertici condivisi e nuovo tentativo...")
                make_vertices_unshared_per_face(obj)
            purge_spin_attrs(obj.data)
            return write_spin_point_attrs(obj)


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




def add_interface_socket(
    interface,
    name,
    in_out,
    socket_type,
    default_value=None,
    min_value=None,
    max_value=None,
    description=None,
):
    sock = None
    try:
        sock = interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    except Exception:
        return None

    for attr_name, value in (
        ("default_value", default_value),
        ("min_value", min_value),
        ("max_value", max_value),
        ("description", description),
    ):
        if value is None:
            continue
        try:
            setattr(sock, attr_name, value)
        except Exception:
            pass
    return sock




def _default_position_flags_for_object(obj) -> tuple[bool, bool, bool]:
    name = str(getattr(obj, "name", "") or "")
    low = name.lower()
    if "flo_xy" in low:
        return (False, False, True)
    if "flo_y" in low and "flo_xy" not in low:
        return (False, True, False)
    return (True, False, False)


def _position_target_candidates_for_object(obj) -> dict[str, list[str]]:
    name = str(getattr(obj, "name", "") or "")
    suffixes = []
    if name.endswith("_tetra"):
        suffixes = ["_tetra", ""]
    elif name.endswith("_tet"):
        suffixes = ["_tet", "_tetra", ""]
    else:
        suffixes = ["", "_tetra", "_tet"]

    out = {}
    for key, base in (("X", "Flo_X"), ("Y", "Flo_Y"), ("XY", "Flo_XY")):
        out[key] = [f"{base}{s}" for s in suffixes]
    return out


def _find_first_existing_object_name(candidates: list[str]) -> str | None:
    for name in candidates:
        try:
            if bpy.data.objects.get(name) is not None:
                return name
        except Exception:
            pass
    return None


def _make_location_vector_source_nodes(node_tree, target_obj, *, location, label_prefix: str):
    if target_obj is None:
        combine = new_node(node_tree, ["ShaderNodeCombineXYZ"], location, f"{label_prefix} Location")
        return combine, get_output(combine, "Vector")

    val_x = new_node(node_tree, ["ShaderNodeValue"], (location[0], location[1] + 140), f"{label_prefix} Loc X")
    val_y = new_node(node_tree, ["ShaderNodeValue"], (location[0], location[1]), f"{label_prefix} Loc Y")
    val_z = new_node(node_tree, ["ShaderNodeValue"], (location[0], location[1] - 140), f"{label_prefix} Loc Z")
    combine = new_node(node_tree, ["ShaderNodeCombineXYZ"], (location[0] + 220, location[1]), f"{label_prefix} Location")

    for idx, val_node in enumerate((val_x, val_y, val_z)):
        try:
            val_node.outputs[0].default_value = float(target_obj.location[idx])
        except Exception:
            pass
        try:
            add_prop_driver(
                val_node.outputs[0],
                "default_value",
                target_obj,
                id_type='OBJECT',
                target_data_path=f"location[{idx}]",
            )
        except Exception:
            pass

    try:
        links = node_tree.links
        links.new(get_output(val_x, "Value"), get_input(combine, "X"))
        links.new(get_output(val_y, "Value"), get_input(combine, "Y"))
        links.new(get_output(val_z, "Value"), get_input(combine, "Z"))
    except Exception:
        pass

    return combine, get_output(combine, "Vector")


def ensure_spin_interface(node_tree, obj=None):
    interface = node_tree.interface
    try:
        interface.clear()
    except Exception:
        pass

    add_interface_socket(interface, "Geometry", 'INPUT', 'NodeSocketGeometry')

    add_interface_socket(
        interface,
        "Speed Mode",
        'INPUT',
        'NodeSocketInt',
        default_value=int(DEFAULT_SPEED_MODE),
        min_value=0,
        max_value=3,
        description=(
            "0 = Uniform speed per tutti i tile; "
            "1 = velocità da VG0..VG7; "
            "2 = velocità lineare da abs(coeff) con verso da sign(coeff); "
            "3 = velocità logaritmica da abs(coeff) con verso da sign(coeff)."
        ),
    )
    add_interface_socket(
        interface,
        "Zero All VG Speeds",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(ZERO_ALL_VG_SPEEDS),
        description=(
            "Override pratico: quando attivo forza VG0..VG7 Speed a zero senza "
            "toccare i valori salvati negli slider."
        ),
    )
    add_interface_socket(
        interface,
        "Uniform Speed",
        'INPUT',
        'NodeSocketFloat',
        default_value=float(UNIFORM_SPEED),
        min_value=0.0,
        max_value=20.0,
        description="Velocità uniforme usata quando Speed Mode = 0.",
    )

    for i in range(8):
        add_interface_socket(
            interface,
            f"VG{i} Speed",
            'INPUT',
            'NodeSocketFloat',
            default_value=float(VG_SPEEDS.get(i, 0.0)),
            min_value=0.0,
            max_value=20.0,
            description=f"Velocità assegnata ai tile del gruppo VG{i} quando Speed Mode = 1.",
        )

    add_interface_socket(
        interface,
        "Coeff Linear Scale",
        'INPUT',
        'NodeSocketFloat',
        default_value=float(COEFF_LINEAR_SCALE),
        min_value=0.0,
        max_value=1000.0,
        description="Scala moltiplicativa per Speed Mode = 2 (base coeff lineare).",
    )
    add_interface_socket(
        interface,
        "Coeff Log Scale",
        'INPUT',
        'NodeSocketFloat',
        default_value=float(COEFF_LOG_SCALE),
        min_value=0.0,
        max_value=1000.0,
        description=(
            "Scala moltiplicativa per Speed Mode = 3. "
            "Per abs(coeff) < 1 il sistema torna automaticamente al ramo lineare."
        ),
    )
    add_interface_socket(
        interface,
        "Speed Clamp",
        'INPUT',
        'NodeSocketFloat',
        default_value=float(SPEED_CLAMP),
        min_value=0.0,
        max_value=1000000.0,
        description="Clampa la velocità finale per evitare runaway sui coefficienti molto grandi.",
    )

    add_interface_socket(
        interface,
        "Direction Multiplier",
        'INPUT',
        'NodeSocketFloat',
        default_value=float(POSITIVE_DIRECTION_FACTOR),
        min_value=-20.0,
        max_value=20.0,
        description="Moltiplicatore finale del verso di rotazione.",
    )
    add_interface_socket(
        interface,
        "Max Revolutions",
        'INPUT',
        'NodeSocketFloat',
        default_value=float(MAX_REVOLUTIONS),
        min_value=0.0,
        max_value=200.0,
        description="Numero massimo di rivoluzioni nel frame range attivo.",
    )
    add_interface_socket(
        interface,
        "Loop",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(LOOP),
        description="Se attivo, la rotazione usa una fase periodica continua.",
    )

    add_interface_socket(
        interface,
        "Axis Local X",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(AXIS_LOCAL_X),
        description=(
            "Asse locale X del tile. Default consigliato: attivo. "
            "Per i tile equilateri coincide bene con il vertice 'alto' del triangolo."
        ),
    )
    add_interface_socket(
        interface,
        "Axis Local Y",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(AXIS_LOCAL_Y),
        description="Aggiunge l'asse locale Y del tile al vettore di rotazione finale.",
    )
    add_interface_socket(
        interface,
        "Axis Local Z",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(AXIS_LOCAL_Z),
        description="Aggiunge la normale di faccia (asse locale Z) al vettore di rotazione finale.",
    )

    pos_x_default, pos_y_default, pos_xy_default = _default_position_flags_for_object(obj)
    add_interface_socket(
        interface,
        "Set Position X",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(pos_x_default),
        description=(
            "Sposta l'intera geometria verso la posizione di Flo_X / Flo_X_tetra. "
            "Consigliato usarne uno solo alla volta."
        ),
    )
    add_interface_socket(
        interface,
        "Set Position Y",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(pos_y_default),
        description=(
            "Sposta l'intera geometria verso la posizione di Flo_Y / Flo_Y_tetra. "
            "Consigliato usarne uno solo alla volta."
        ),
    )
    add_interface_socket(
        interface,
        "Set Position XY",
        'INPUT',
        'NodeSocketBool',
        default_value=bool(pos_xy_default),
        description=(
            "Sposta l'intera geometria verso la posizione di Flo_XY / Flo_XY_tetra. "
            "Consigliato usarne uno solo alla volta."
        ),
    )

    add_interface_socket(interface, "Geometry", 'OUTPUT', 'NodeSocketGeometry')


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


# ============================================================
# BUILD NODE GROUP
# ============================================================



def build_node_group(obj):
    node_group_name = make_node_group_name(obj)

    if REPLACE_EXISTING:
        remove_node_group_if_present(node_group_name)

    ng = bpy.data.node_groups.get(node_group_name)
    if ng is None:
        ng = bpy.data.node_groups.new(node_group_name, "GeometryNodeTree")

    ensure_spin_interface(ng, obj)
    clear_node_tree(ng)
    links = ng.links
    scene = bpy.context.scene

    group_in = new_node(ng, ["NodeGroupInput"], (-4200, 0), "Group Input")
    group_out = new_node(ng, ["NodeGroupOutput"], (2100, 0), "Group Output")

    # attrs point-domain
    sel_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, 860), "Point Selected")
    center_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, 660), "Point Center")
    axis_x_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, 460), "Point Axis X")
    axis_y_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, 260), "Point Axis Y")
    axis_z_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, 60), "Point Axis Z")
    dir_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, -140), "Point Direction")
    coeff_abs_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-4200, -340), "Point Coeff Abs")

    for n, dtype, attr_name in (
        (sel_named, 'BOOLEAN', POINT_SELECTED_ATTR_NAME),
        (center_named, 'FLOAT_VECTOR', POINT_CENTER_ATTR_NAME),
        (axis_x_named, 'FLOAT_VECTOR', POINT_AXIS_X_ATTR_NAME),
        (axis_y_named, 'FLOAT_VECTOR', POINT_AXIS_Y_ATTR_NAME),
        (axis_z_named, 'FLOAT_VECTOR', POINT_AXIS_Z_ATTR_NAME),
        (dir_named, 'FLOAT', POINT_DIRECTION_ATTR_NAME),
        (coeff_abs_named, 'FLOAT', POINT_COEFF_ABS_ATTR_NAME),
    ):
        try:
            n.data_type = dtype
        except Exception:
            pass
        try:
            get_input(n, "Name").default_value = attr_name
        except Exception:
            pass

    pos_node = new_node(ng, ["GeometryNodeInputPosition"], (-3560, -1400), "Position")

    # ========================================================
    # Phase timeline
    # ========================================================
    scene_time = new_node(ng, ["GeometryNodeInputSceneTime"], (-3920, 1400), "Scene Time")
    start_value = new_node(ng, ["ShaderNodeValue"], (-3920, 1640), "Start Frame Value")
    end_value = new_node(ng, ["ShaderNodeValue"], (-3920, 1520), "End Frame Value")

    if USE_SCENE_FRAME_RANGE:
        add_single_prop_driver(start_value.outputs[0], "default_value", scene, "frame_start")
        add_single_prop_driver(end_value.outputs[0], "default_value", scene, "frame_end")
    else:
        start_value.outputs[0].default_value = float(START_FRAME)
        end_value.outputs[0].default_value = float(END_FRAME)

    frame_minus_start = new_node(ng, ["ShaderNodeMath"], (-3680, 1400), "Frame - Start")
    frame_minus_start.operation = 'SUBTRACT'
    duration_raw = new_node(ng, ["ShaderNodeMath"], (-3680, 1560), "End - Start")
    duration_raw.operation = 'SUBTRACT'
    duration_max = new_node(ng, ["ShaderNodeMath"], (-3440, 1560), "Max(1, Duration)")
    duration_max.operation = 'MAXIMUM'
    duration_max.inputs[1].default_value = 1.0
    div_duration = new_node(ng, ["ShaderNodeMath"], (-3440, 1400), "/ Duration")
    div_duration.operation = 'DIVIDE'

    links.new(get_output(scene_time, "Frame"), frame_minus_start.inputs[0])
    links.new(get_output(start_value, "Value"), frame_minus_start.inputs[1])
    links.new(get_output(end_value, "Value"), duration_raw.inputs[0])
    links.new(get_output(start_value, "Value"), duration_raw.inputs[1])
    links.new(get_output(duration_raw, "Value"), duration_max.inputs[0])
    links.new(get_output(frame_minus_start, "Value"), div_duration.inputs[0])
    links.new(get_output(duration_max, "Value"), div_duration.inputs[1])

    fract_phase = new_node(ng, ["ShaderNodeMath"], (-3200, 1440), "Fract Phase")
    fract_phase.operation = 'FRACT'
    links.new(get_output(div_duration, "Value"), fract_phase.inputs[0])

    clamp_phase = new_node(ng, ["ShaderNodeClamp"], (-3200, 1320), "Clamp Phase")
    links.new(get_output(div_duration, "Value"), get_input(clamp_phase, "Value"))

    loop_switch = new_node(ng, ["GeometryNodeSwitch"], (-2960, 1400), "Loop Switch")
    try:
        loop_switch.input_type = 'FLOAT'
    except Exception:
        pass
    links.new(get_output(group_in, "Loop"), get_input(loop_switch, "Switch"))
    links.new(get_output(clamp_phase, "Result"), get_input(loop_switch, "False"))
    links.new(get_output(fract_phase, "Value"), get_input(loop_switch, "True"))

    phase_times_tau = new_node(ng, ["ShaderNodeMath"], (-2720, 1400), "Phase * Tau")
    phase_times_tau.operation = 'MULTIPLY'
    phase_times_tau.inputs[1].default_value = math.tau

    phase_revolutions = new_node(ng, ["ShaderNodeMath"], (-2480, 1400), "Phase * MaxRev")
    phase_revolutions.operation = 'MULTIPLY'
    links.new(get_output(loop_switch, "Output"), phase_times_tau.inputs[0])
    links.new(get_output(phase_times_tau, "Value"), phase_revolutions.inputs[0])
    links.new(get_output(group_in, "Max Revolutions"), phase_revolutions.inputs[1])

    # ========================================================
    # Speed modes
    # ========================================================
    speed_uniform = get_output(group_in, "Uniform Speed")

    # VG speed sum via bin masks
    speed_sum = None
    for i in range(8):
        mask_named = new_node(ng, ["GeometryNodeInputNamedAttribute"], (-3920, -620 - i * 120), f"VG Mask {i}")
        try:
            mask_named.data_type = 'BOOLEAN'
        except Exception:
            pass
        try:
            get_input(mask_named, "Name").default_value = make_bin_mask_attr_name(i)
        except Exception:
            pass

        speed_sw = new_node(ng, ["GeometryNodeSwitch"], (-3680, -620 - i * 120), f"VG{i} Speed If Mask")
        try:
            speed_sw.input_type = 'FLOAT'
        except Exception:
            pass
        links.new(get_output(mask_named, "Attribute", "Selection", "Value"), get_input(speed_sw, "Switch"))
        get_input(speed_sw, "False").default_value = 0.0
        links.new(get_output(group_in, f"VG{i} Speed"), get_input(speed_sw, "True"))

        if speed_sum is None:
            speed_sum = get_output(speed_sw, "Output")
        else:
            add_node = new_node(ng, ["ShaderNodeMath"], (-3440, -620 - i * 120), f"Add VG Speed {i}")
            add_node.operation = 'ADD'
            links.new(speed_sum, add_node.inputs[0])
            links.new(get_output(speed_sw, "Output"), add_node.inputs[1])
            speed_sum = get_output(add_node, "Value")

    zero_vg_switch = new_node(ng, ["GeometryNodeSwitch"], (-3200, -240), "Zero All VG Speeds Switch")
    try:
        zero_vg_switch.input_type = 'FLOAT'
    except Exception:
        pass
    links.new(get_output(group_in, "Zero All VG Speeds"), get_input(zero_vg_switch, "Switch"))
    get_input(zero_vg_switch, "True").default_value = 0.0
    links.new(speed_sum, get_input(zero_vg_switch, "False"))
    speed_vg = get_output(zero_vg_switch, "Output")

    # Coeff linear: abs(coeff) * scale
    coeff_lin = new_node(ng, ["ShaderNodeMath"], (-3200, 320), "Coeff Linear")
    coeff_lin.operation = 'MULTIPLY'
    links.new(get_output(coeff_abs_named, "Attribute", "Value"), coeff_lin.inputs[0])
    links.new(get_output(group_in, "Coeff Linear Scale"), coeff_lin.inputs[1])

    # Coeff log: if abs(coeff) < 1 -> linear; else log10(1 + abs(coeff)) * scale
    coeff_plus_one = new_node(ng, ["ShaderNodeMath"], (-3200, 120), "Coeff + 1")
    coeff_plus_one.operation = 'ADD'
    links.new(get_output(coeff_abs_named, "Attribute", "Value"), coeff_plus_one.inputs[0])
    coeff_plus_one.inputs[1].default_value = 1.0

    coeff_log10 = new_node(ng, ["ShaderNodeMath"], (-2960, 120), "log10(1+Coeff)")
    coeff_log10.operation = 'LOGARITHM'
    links.new(get_output(coeff_plus_one, "Value"), coeff_log10.inputs[0])
    coeff_log10.inputs[1].default_value = 10.0

    coeff_log_scaled = new_node(ng, ["ShaderNodeMath"], (-2720, 120), "Coeff Log Scaled")
    coeff_log_scaled.operation = 'MULTIPLY'
    links.new(get_output(coeff_log10, "Value"), coeff_log_scaled.inputs[0])
    links.new(get_output(group_in, "Coeff Log Scale"), coeff_log_scaled.inputs[1])

    coeff_lt_one = new_node(ng, ["FunctionNodeCompare"], (-2960, 320), "Coeff < 1")
    try:
        coeff_lt_one.data_type = 'FLOAT'
        coeff_lt_one.operation = 'LESS_THAN'
    except Exception:
        pass
    links.new(get_output(coeff_abs_named, "Attribute", "Value"), get_input(coeff_lt_one, "A"))
    try:
        get_input(coeff_lt_one, "B").default_value = 1.0
    except Exception:
        pass

    coeff_log_switch = new_node(ng, ["GeometryNodeSwitch"], (-2480, 200), "Coeff Log Fallback")
    try:
        coeff_log_switch.input_type = 'FLOAT'
    except Exception:
        pass
    links.new(get_output(coeff_lt_one, "Result", "Output"), get_input(coeff_log_switch, "Switch"))
    links.new(get_output(coeff_lin, "Value"), get_input(coeff_log_switch, "True"))
    links.new(get_output(coeff_log_scaled, "Value"), get_input(coeff_log_switch, "False"))
    speed_coeff_log = get_output(coeff_log_switch, "Output")

    # Mode select: 0 Uniform, 1 VG, 2 CoeffLin, 3 CoeffLog
    speed_mode_out = get_output(group_in, "Speed Mode")

    mode_eq_1 = new_node(ng, ["FunctionNodeCompare"], (-2240, -40), "Mode == 1")
    mode_eq_2 = new_node(ng, ["FunctionNodeCompare"], (-2240, -200), "Mode == 2")
    mode_eq_3 = new_node(ng, ["FunctionNodeCompare"], (-2240, -360), "Mode == 3")
    for n, target in ((mode_eq_1, 1), (mode_eq_2, 2), (mode_eq_3, 3)):
        try:
            n.data_type = 'INT'
            n.operation = 'EQUAL'
        except Exception:
            pass
        links.new(speed_mode_out, get_input(n, "A"))
        try:
            get_input(n, "B").default_value = int(target)
        except Exception:
            pass

    mode_sw_1 = new_node(ng, ["GeometryNodeSwitch"], (-2000, -40), "Mode Uniform/VG")
    mode_sw_2 = new_node(ng, ["GeometryNodeSwitch"], (-1760, -200), "Mode -> CoeffLin")
    mode_sw_3 = new_node(ng, ["GeometryNodeSwitch"], (-1520, -360), "Mode -> CoeffLog")
    for n in (mode_sw_1, mode_sw_2, mode_sw_3):
        try:
            n.input_type = 'FLOAT'
        except Exception:
            pass

    links.new(get_output(mode_eq_1, "Result", "Output"), get_input(mode_sw_1, "Switch"))
    links.new(speed_uniform, get_input(mode_sw_1, "False"))
    links.new(speed_vg, get_input(mode_sw_1, "True"))

    links.new(get_output(mode_eq_2, "Result", "Output"), get_input(mode_sw_2, "Switch"))
    links.new(get_output(mode_sw_1, "Output"), get_input(mode_sw_2, "False"))
    links.new(get_output(coeff_lin, "Value"), get_input(mode_sw_2, "True"))

    links.new(get_output(mode_eq_3, "Result", "Output"), get_input(mode_sw_3, "Switch"))
    links.new(get_output(mode_sw_2, "Output"), get_input(mode_sw_3, "False"))
    links.new(speed_coeff_log, get_input(mode_sw_3, "True"))

    speed_clamp_node = new_node(ng, ["ShaderNodeMath"], (-1280, -280), "Clamp Speed")
    speed_clamp_node.operation = 'MINIMUM'
    links.new(get_output(mode_sw_3, "Output"), speed_clamp_node.inputs[0])
    links.new(get_output(group_in, "Speed Clamp"), speed_clamp_node.inputs[1])

    # ========================================================
    # Axis selection
    # ========================================================
    zero_vec = new_node(ng, ["ShaderNodeCombineXYZ"], (-2720, -860), "Zero Vector")

    axis_x_sw = new_node(ng, ["GeometryNodeSwitch"], (-2480, -860), "Use Axis X")
    axis_y_sw = new_node(ng, ["GeometryNodeSwitch"], (-2480, -1040), "Use Axis Y")
    axis_z_sw = new_node(ng, ["GeometryNodeSwitch"], (-2480, -1220), "Use Axis Z")
    for n in (axis_x_sw, axis_y_sw, axis_z_sw):
        try:
            n.input_type = 'VECTOR'
        except Exception:
            pass

    links.new(get_output(group_in, "Axis Local X"), get_input(axis_x_sw, "Switch"))
    links.new(get_output(zero_vec, "Vector"), get_input(axis_x_sw, "False"))
    links.new(get_output(axis_x_named, "Attribute", "Vector"), get_input(axis_x_sw, "True"))

    links.new(get_output(group_in, "Axis Local Y"), get_input(axis_y_sw, "Switch"))
    links.new(get_output(zero_vec, "Vector"), get_input(axis_y_sw, "False"))
    links.new(get_output(axis_y_named, "Attribute", "Vector"), get_input(axis_y_sw, "True"))

    links.new(get_output(group_in, "Axis Local Z"), get_input(axis_z_sw, "Switch"))
    links.new(get_output(zero_vec, "Vector"), get_input(axis_z_sw, "False"))
    links.new(get_output(axis_z_named, "Attribute", "Vector"), get_input(axis_z_sw, "True"))

    axis_sum_xy = new_node(ng, ["ShaderNodeVectorMath"], (-2240, -940), "Axis X + Y")
    axis_sum_xy.operation = 'ADD'
    links.new(get_output(axis_x_sw, "Output"), axis_sum_xy.inputs[0])
    links.new(get_output(axis_y_sw, "Output"), axis_sum_xy.inputs[1])

    axis_sum_xyz = new_node(ng, ["ShaderNodeVectorMath"], (-2000, -1040), "Axis XY + Z")
    axis_sum_xyz.operation = 'ADD'
    links.new(get_output(axis_sum_xy, "Vector"), axis_sum_xyz.inputs[0])
    links.new(get_output(axis_z_sw, "Output"), axis_sum_xyz.inputs[1])

    axis_len = new_node(ng, ["ShaderNodeVectorMath"], (-1760, -1040), "Axis Length")
    axis_len.operation = 'LENGTH'
    links.new(get_output(axis_sum_xyz, "Vector"), axis_len.inputs[0])

    axis_has_len = new_node(ng, ["FunctionNodeCompare"], (-1520, -1040), "Axis Len > EPS")
    try:
        axis_has_len.data_type = 'FLOAT'
        axis_has_len.operation = 'GREATER_THAN'
    except Exception:
        pass
    links.new(get_output(axis_len, "Value"), get_input(axis_has_len, "A"))
    try:
        get_input(axis_has_len, "B").default_value = 1e-8
    except Exception:
        pass

    axis_fallback_sw = new_node(ng, ["GeometryNodeSwitch"], (-1280, -1040), "Axis Fallback -> Z")
    try:
        axis_fallback_sw.input_type = 'VECTOR'
    except Exception:
        pass
    links.new(get_output(axis_has_len, "Result", "Output"), get_input(axis_fallback_sw, "Switch"))
    links.new(get_output(axis_z_named, "Attribute", "Vector"), get_input(axis_fallback_sw, "False"))
    links.new(get_output(axis_sum_xyz, "Vector"), get_input(axis_fallback_sw, "True"))

    axis_norm = new_node(ng, ["ShaderNodeVectorMath"], (-1040, -1040), "Axis Normalize")
    axis_norm.operation = 'NORMALIZE'
    links.new(get_output(axis_fallback_sw, "Output"), axis_norm.inputs[0])

    # ========================================================
    # Position routing (object-level placement inside the modifier)
    # ========================================================
    target_candidates = _position_target_candidates_for_object(obj)
    self_obj = obj
    x_target_obj = bpy.data.objects.get(_find_first_existing_object_name(target_candidates["X"]) or "")
    y_target_obj = bpy.data.objects.get(_find_first_existing_object_name(target_candidates["Y"]) or "")
    xy_target_obj = bpy.data.objects.get(_find_first_existing_object_name(target_candidates["XY"]) or "")

    _, self_loc_out = _make_location_vector_source_nodes(ng, self_obj, location=(-2480, -1760), label_prefix="Self")
    _, x_loc_out = _make_location_vector_source_nodes(ng, x_target_obj, location=(-2480, -2140), label_prefix="Target X")
    _, y_loc_out = _make_location_vector_source_nodes(ng, y_target_obj, location=(-2480, -2520), label_prefix="Target Y")
    _, xy_loc_out = _make_location_vector_source_nodes(ng, xy_target_obj, location=(-2480, -2900), label_prefix="Target XY")

    pos_zero_vec = new_node(ng, ["ShaderNodeCombineXYZ"], (-1760, -2460), "Position Zero Vector")

    x_delta = new_node(ng, ["ShaderNodeVectorMath"], (-1520, -2140), "Target X - Self")
    x_delta.operation = 'SUBTRACT'
    links.new(x_loc_out, x_delta.inputs[0])
    links.new(self_loc_out, x_delta.inputs[1])

    y_delta = new_node(ng, ["ShaderNodeVectorMath"], (-1520, -2520), "Target Y - Self")
    y_delta.operation = 'SUBTRACT'
    links.new(y_loc_out, y_delta.inputs[0])
    links.new(self_loc_out, y_delta.inputs[1])

    xy_delta = new_node(ng, ["ShaderNodeVectorMath"], (-1520, -2900), "Target XY - Self")
    xy_delta.operation = 'SUBTRACT'
    links.new(xy_loc_out, xy_delta.inputs[0])
    links.new(self_loc_out, xy_delta.inputs[1])

    pos_x_sw = new_node(ng, ["GeometryNodeSwitch"], (-1280, -2140), "Use Position X")
    pos_y_sw = new_node(ng, ["GeometryNodeSwitch"], (-1280, -2520), "Use Position Y")
    pos_xy_sw = new_node(ng, ["GeometryNodeSwitch"], (-1280, -2900), "Use Position XY")
    for n in (pos_x_sw, pos_y_sw, pos_xy_sw):
        try:
            n.input_type = 'VECTOR'
        except Exception:
            pass

    links.new(get_output(group_in, "Set Position X"), get_input(pos_x_sw, "Switch"))
    links.new(get_output(pos_zero_vec, "Vector"), get_input(pos_x_sw, "False"))
    links.new(get_output(x_delta, "Vector"), get_input(pos_x_sw, "True"))

    links.new(get_output(group_in, "Set Position Y"), get_input(pos_y_sw, "Switch"))
    links.new(get_output(pos_zero_vec, "Vector"), get_input(pos_y_sw, "False"))
    links.new(get_output(y_delta, "Vector"), get_input(pos_y_sw, "True"))

    links.new(get_output(group_in, "Set Position XY"), get_input(pos_xy_sw, "Switch"))
    links.new(get_output(pos_zero_vec, "Vector"), get_input(pos_xy_sw, "False"))
    links.new(get_output(xy_delta, "Vector"), get_input(pos_xy_sw, "True"))

    pos_sum_xy = new_node(ng, ["ShaderNodeVectorMath"], (-1040, -2520), "Position X + Y")
    pos_sum_xy.operation = 'ADD'
    links.new(get_output(pos_x_sw, "Output"), pos_sum_xy.inputs[0])
    links.new(get_output(pos_y_sw, "Output"), pos_sum_xy.inputs[1])

    pos_sum_xyz = new_node(ng, ["ShaderNodeVectorMath"], (-800, -2700), "Position XY + (X+Y)")
    pos_sum_xyz.operation = 'ADD'
    links.new(get_output(pos_sum_xy, "Vector"), pos_sum_xyz.inputs[0])
    links.new(get_output(pos_xy_sw, "Output"), pos_sum_xyz.inputs[1])

    # ========================================================
    # Final angle and position
    # ========================================================
    speed_x_dir = new_node(ng, ["ShaderNodeMath"], (-1040, -80), "Speed * Direction")
    speed_x_dir.operation = 'MULTIPLY'
    links.new(get_output(speed_clamp_node, "Value"), speed_x_dir.inputs[0])
    links.new(get_output(dir_named, "Attribute", "Value"), speed_x_dir.inputs[1])

    signed_factor = new_node(ng, ["ShaderNodeMath"], (-800, -80), "Signed Factor")
    signed_factor.operation = 'MULTIPLY'
    links.new(get_output(speed_x_dir, "Value"), signed_factor.inputs[0])
    links.new(get_output(group_in, "Direction Multiplier"), signed_factor.inputs[1])

    final_angle = new_node(ng, ["ShaderNodeMath"], (-560, 1400), "Angle")
    final_angle.operation = 'MULTIPLY'
    links.new(get_output(phase_revolutions, "Value"), final_angle.inputs[0])
    links.new(get_output(signed_factor, "Value"), final_angle.inputs[1])

    vec_sub = new_node(ng, ["ShaderNodeVectorMath"], (-2720, -1400), "Pos - Center")
    vec_sub.operation = 'SUBTRACT'
    links.new(get_output(pos_node, "Position", "Vector"), vec_sub.inputs[0])
    links.new(get_output(center_named, "Attribute", "Vector"), vec_sub.inputs[1])

    vec_rotate = new_node(ng, ["ShaderNodeVectorRotate"], (-1760, -1400), "Rotate Around Local Axis")
    try:
        vec_rotate.rotation_type = 'AXIS_ANGLE'
    except Exception:
        pass
    links.new(get_output(vec_sub, "Vector", "Value"), get_input(vec_rotate, "Vector"))
    links.new(get_output(axis_norm, "Vector"), get_input(vec_rotate, "Axis"))
    links.new(get_output(final_angle, "Value"), get_input(vec_rotate, "Angle"))

    vec_add = new_node(ng, ["ShaderNodeVectorMath"], (-1280, -1400), "Rotated + Center")
    vec_add.operation = 'ADD'
    links.new(get_output(vec_rotate, "Vector"), vec_add.inputs[0])
    links.new(get_output(center_named, "Attribute", "Vector"), vec_add.inputs[1])

    vec_add_position_offset = new_node(ng, ["ShaderNodeVectorMath"], (-1040, -1560), "Add Position Offset")
    vec_add_position_offset.operation = 'ADD'
    links.new(get_output(vec_add, "Vector", "Value"), vec_add_position_offset.inputs[0])
    links.new(get_output(pos_sum_xyz, "Vector"), vec_add_position_offset.inputs[1])

    set_pos = new_node(ng, ["GeometryNodeSetPosition"], (-560, 0), "Set Position In Place")
    links.new(get_output(group_in, "Geometry"), get_input(set_pos, "Geometry"))
    links.new(get_output(sel_named, "Attribute", "Selection", "Value"), get_input(set_pos, "Selection"))
    links.new(get_output(vec_add_position_offset, "Vector", "Value"), get_input(set_pos, "Position"))

    links.new(get_output(set_pos, "Geometry"), get_input(group_out, "Geometry"))
    return ng




def _resolve_target_base_name(target: str) -> str:
    return {
        "X": "Flo_X",
        "Y": "Flo_Y",
        "Z": "Flo_XY",
    }.get(str(target).upper(), "Flo_X")


def _target_object_names_for_spin(context, target: str) -> list[str]:
    """
    Ritorna SEMPRE il flat e, se presente, anche il tetra corrispondente.
    Regola richiesta: quando premi Spin, il tetra deve ricevere lo stesso
    trattamento del base object, non essere lasciato in panchina a guardare.
    """
    base_name = _resolve_target_base_name(target)
    out = []

    flat_obj = bpy.data.objects.get(base_name)
    if flat_obj is not None:
        out.append(base_name)

    tetra_name = f"{base_name}_tetra"
    tetra_obj = bpy.data.objects.get(tetra_name)
    if tetra_obj is not None:
        out.append(tetra_name)
    else:
        tetra_legacy = f"{base_name}_tet"
        if bpy.data.objects.get(tetra_legacy) is not None:
            out.append(tetra_legacy)

    if not out:
        raise RuntimeError(
            f"Nessun oggetto trovato per target {target}. Cercavo: {base_name}, {base_name}_tetra"
        )

    return out


def _copy_modifier_idprops(src_mod, dst_mod) -> None:
    """
    Copia i valori dei socket/interfaccia dal modifier sorgente a quello destinazione.
    Utile per avere tetra e flat allineati subito dopo l'applicazione dello spin.
    """
    if src_mod is None or dst_mod is None:
        return

    try:
        src_keys = list(src_mod.keys())
    except Exception:
        src_keys = []

    for k in src_keys:
        if k in {"_RNA_UI"}:
            continue
        try:
            dst_mod[k] = src_mod[k]
        except Exception:
            pass

    try:
        if "_RNA_UI" in src_mod.keys():
            dst_mod["_RNA_UI"] = dict(src_mod["_RNA_UI"])
    except Exception:
        pass

def run_spin(*, target_object_name: str | None = None, filter_bin: int | None = None):
    global TARGET_OBJECT_NAME, FILTER_BIN

    TARGET_OBJECT_NAME = target_object_name
    FILTER_BIN = filter_bin

    force_object_mode()
    obj = choose_target_object()

    # rende attivo il target per coerenza UI/Modifier
    try:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
    except Exception:
        pass

    if REPLACE_EXISTING:
        remove_modifier_if_present(obj, make_modifier_name(obj))

    stats = write_spin_attrs(obj)
    ng = build_node_group(obj)
    mod = ensure_modifier(obj, ng)

    print("\n=== Geometry Nodes tile spin IN-PLACE pronto ===")
    print(f"Oggetto: {obj.name}")
    print(f"Node group: {ng.name}")
    print(f"Modifier: {mod.name}")
    print(f"Filtro bin: {FILTER_BIN}")
    print(f"Facce animate: {stats['selected_count']}")
    print(f"Topology triangles-only: {stats['topology']['all_triangles']}")
    print(f"Topology vertices single-use: {stats['topology']['all_vertices_single_use']}")
    if USE_SCENE_FRAME_RANGE:
        print(f"Frame range: scena attiva ({bpy.context.scene.frame_start} -> {bpy.context.scene.frame_end})")
    else:
        print(f"Frame range: manuale ({START_FRAME} -> {END_FRAME})")
    print("Nota: questo path NON ricrea i tile.")
    print("Muove i vertici originali via Set Position, per preservare materiali/attributi/VG.")
    print("Controlli utili nel modifier: Speed Mode, Zero All VG Speeds, Uniform Speed, VG0..VG7 Speed, Coeff Linear/Log Scale, Axis Local X/Y/Z, Direction Multiplier, Max Revolutions, Loop.")
    try:
        print("Conteggio facce per bin:", stats.get("bin_face_counts"))
    except Exception:
        pass
    print("Premi Play nella timeline.")

    return obj, ng, mod, stats


class FLORET_MESH_OT_spin_vgs(Operator):
    bl_idname = "floret_mesh.spin_vgs"
    bl_label = "Spin VGs"
    bl_description = (
        "Applica lo spin Geometry Nodes ai tile del Floretion target, "
        "preservando materiali/attributi/VG"
    )
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(
        name="Target",
        items=[
            ("X", "X", "Applica a Flo_X / Flo_X_tetra"),
            ("Y", "Y", "Applica a Flo_Y / Flo_Y_tetra"),
            ("Z", "Z", "Applica a Flo_XY / Flo_XY_tetra"),
        ],
        default="X",
    )

    filter_bin: bpy.props.IntProperty(
        name="Filter bin",
        description="Se >= 0 anima solo il bin indicato; -1 = tutti",
        default=-1,
        min=-1,
        max=7,
        options={'SKIP_SAVE'},
    )

    def execute(self, context):
        try:
            target_names = _target_object_names_for_spin(context, self.target)
            filter_bin = None if int(self.filter_bin) < 0 else int(self.filter_bin)

            applied = []
            total_faces = 0
            first_mod = None
            first_obj = None

            for i, target_name in enumerate(target_names):
                obj, ng, mod, stats = run_spin(
                    target_object_name=target_name,
                    filter_bin=filter_bin,
                )

                # Se stiamo applicando anche il tetra, allineiamo i valori del modifier
                # a quelli del primo oggetto creato (di solito il flat).
                if i == 0:
                    first_mod = mod
                    first_obj = obj
                else:
                    try:
                        _copy_modifier_idprops(first_mod, mod)
                    except Exception:
                        pass

                applied.append(f"{obj.name} [{mod.name}]")
                total_faces += int(stats.get("selected_count", 0) or 0)

            self.report(
                {'INFO'},
                f"Spin VGs applicato a {len(applied)} oggetti: {', '.join(applied)}. Facce animate: {total_faces}"
            )
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Spin VGs fallito: {e}")
            return {'CANCELLED'}
