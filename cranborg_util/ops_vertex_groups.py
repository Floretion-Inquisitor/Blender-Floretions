# cranborg_util/ops_vertex_groups.py
from __future__ import annotations

import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty

from . import ui_props as _ui_props

ATTR_NAME_BOTH = "neighbors_edges_and_verts"  # FACE float (bin 0..7)
ATTR_NAME_BASE_DEC = "base_dec"               # FACE int (id tile)

PREFIX_NEI  = "FLO_NEI_"
PREFIX_BALL = "FLO_BALL_"   # legacy cleanup
PREFIX_STATE = "FLO_STATE_"
TETRA_SUFFIX = "_tetra"


# Attributi da sincronizzare flat -> tetra per mantenere identica la resa shader-based.
# Il tetra non deve ricalcolare autonomamente il "look" neighbor-based: deve copiare
# il risultato visuale del tile corrispondente del flat.
_SYNC_FACE_FLOAT_ATTRS = (
    "face_coeff",
    "neighbors_edges",
    "neighbors_verts",
    "neighbors_edges_and_verts",
    "base_coeff",
    "base_coeff_abs",
    "base_coeff_min",
    "base_coeff_max",
    "base_coeff_abs_max",
    "base_coeff_q",
    "base_coeff_abs_q",
    "tile_orientation_sign",
)

_SYNC_FACE_INT_ATTRS = (
    "base_dec",
)

_SYNC_CORNER_COLOR_ATTRS = (
    "floretion_color",
    "color_edges",
    "color_verts",
    "color_edges_and_verts",
)


def _clamp_bin(v: float) -> int:
    try:
        b = int(round(float(v)))
    except Exception:
        b = 0
    if b < 0:
        b = 0
    if b > 7:
        b = 7
    return b


def _remove_prefixed_groups(obj: bpy.types.Object, prefix: str) -> int:
    removed = 0
    for vg in list(obj.vertex_groups):
        if vg.name.startswith(prefix):
            try:
                obj.vertex_groups.remove(vg)
                removed += 1
            except Exception:
                pass
    return removed


def _ensure_vg(obj: bpy.types.Object, name: str):
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    return vg


def _get_face_attr_bins(me: bpy.types.Mesh, attr_name: str) -> list[int] | None:
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return None
    n = len(me.polygons)
    out = [0] * n
    for i in range(n):
        try:
            out[i] = _clamp_bin(attr.data[i].value)
        except Exception:
            out[i] = 0
    return out


def _get_face_attr_ints(me: bpy.types.Mesh, attr_name: str) -> list[int] | None:
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return None
    n = len(me.polygons)
    out = [0] * n
    for i in range(n):
        try:
            out[i] = int(attr.data[i].value)
        except Exception:
            try:
                out[i] = int(round(float(attr.data[i].value)))
            except Exception:
                out[i] = 0
    return out


def _get_face_attr_floats(me: bpy.types.Mesh, attr_name: str) -> list[float] | None:
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return None
    n = len(me.polygons)
    out = [0.0] * n
    for i in range(n):
        try:
            out[i] = float(attr.data[i].value)
        except Exception:
            out[i] = 0.0
    return out


def _get_effective_face_coeffs(me: bpy.types.Mesh) -> list[float] | None:
    """Ritorna il miglior layer coeff disponibile per classificare ZERO/NONZERO/POS/NEG.

    Ordine di preferenza:
    1) face_coeff  -> scritto esplicitamente dal builder mesh
    2) base_coeff  -> layer shader/neighbor compat
    3) coeff       -> fallback legacy
    """
    for attr_name in ("face_coeff", "base_coeff", "coeff"):
        vals = _get_face_attr_floats(me, attr_name)
        if vals is not None:
            return vals
    return None


def _poly_center_local(me: bpy.types.Mesh, poly: bpy.types.MeshPolygon):
    try:
        return poly.center
    except Exception:
        pass

    try:
        from mathutils import Vector
        if not poly.vertices:
            return Vector((0.0, 0.0, 0.0))
        acc = Vector((0.0, 0.0, 0.0))
        for vi in poly.vertices:
            acc += me.vertices[vi].co
        return acc / float(len(poly.vertices))
    except Exception:
        return (0.0, 0.0, 0.0)


def _build_base_dec_maps_from_main_faces(
    main_obj: bpy.types.Object,
    face_bins: list[int],
    face_base_decs: list[int],
):
    me = main_obj.data
    bd_to_bin: dict[int, int] = {}
    bd_to_center = {}
    bd_to_best_z: dict[int, float] = {}

    for fi, poly in enumerate(me.polygons):
        if fi >= len(face_bins) or fi >= len(face_base_decs):
            break
        bd = int(face_base_decs[fi])
        if bd == 0:
            continue
        b = int(face_bins[fi])

        c = _poly_center_local(me, poly)
        try:
            z = float(c[2])
        except Exception:
            z = 0.0

        prev = bd_to_best_z.get(bd, None)
        if prev is None or z > prev:
            bd_to_best_z[bd] = z
            bd_to_bin[bd] = b
            bd_to_center[bd] = c

    return bd_to_bin, bd_to_center


def _write_bins_to_vertex_groups(obj: bpy.types.Object, bins_to_verts: list[set[int]]) -> int:
    created = 0
    for b in range(8):
        name = f"{PREFIX_NEI}NEI_BOTH_{b}" if b < 7 else f"{PREFIX_NEI}NEI_BOTH_7P"
        vg = _ensure_vg(obj, name)
        verts = sorted(int(v) for v in bins_to_verts[b])
        if verts:
            try:
                vg.add(verts, 1.0, 'REPLACE')
            except Exception:
                pass
        created += 1
    return created




def _state_name(kind: str) -> str:
    return f"{PREFIX_STATE}{str(kind).upper()}"


def _coeff_state_flags(coeff: float) -> dict[str, bool]:
    try:
        c = float(coeff)
    except Exception:
        c = 0.0
    eps = 1.0e-12
    return {
        "NONZERO": abs(c) > eps,
        "ZERO": abs(c) <= eps,
        "POS": c > eps,
        "NEG": c < -eps,
    }


def _write_state_groups(obj: bpy.types.Object, groups_to_verts: dict[str, set[int] | list[int]]) -> int:
    created = 0
    for key in ("NONZERO", "ZERO", "POS", "NEG"):
        name = _state_name(key)
        vg = _ensure_vg(obj, name)
        verts = sorted(int(v) for v in groups_to_verts.get(key, []))
        if verts:
            try:
                vg.add(verts, 1.0, 'REPLACE')
            except Exception:
                pass
        created += 1
    return created


def _create_state_groups_on_mesh(obj: bpy.types.Object, face_coeffs: list[float]) -> int:
    me = obj.data
    groups_to_verts = {k: set() for k in ("NONZERO", "ZERO", "POS", "NEG")}
    for fi, poly in enumerate(me.polygons):
        coeff = float(face_coeffs[fi]) if fi < len(face_coeffs) else 0.0
        flags = _coeff_state_flags(coeff)
        for key, active in flags.items():
            if not active:
                continue
            for vi in poly.vertices:
                groups_to_verts[key].add(int(vi))
    return _write_state_groups(obj, groups_to_verts)


def _build_base_dec_coeff_maps_from_main_faces(
    main_obj: bpy.types.Object,
    face_coeffs: list[float],
    face_base_decs: list[int],
):
    me = main_obj.data
    bd_to_coeff: dict[int, float] = {}
    bd_to_center = {}
    bd_to_best_z: dict[int, float] = {}

    for fi, poly in enumerate(me.polygons):
        if fi >= len(face_coeffs) or fi >= len(face_base_decs):
            break
        bd = int(face_base_decs[fi])
        if bd == 0:
            continue

        c = _poly_center_local(me, poly)
        try:
            z = float(c[2])
        except Exception:
            z = 0.0

        prev = bd_to_best_z.get(bd, None)
        if prev is None or z > prev:
            bd_to_best_z[bd] = z
            bd_to_coeff[bd] = float(face_coeffs[fi])
            bd_to_center[bd] = c

    return bd_to_coeff, bd_to_center


def _create_state_groups_on_centroids(
    cent_obj: bpy.types.Object,
    *,
    bd_to_coeff: dict[int, float],
    bd_to_center: dict[int, object],
) -> int:
    me = cent_obj.data
    n = len(me.vertices)
    if n == 0:
        return 0

    groups_to_verts = {k: [] for k in ("NONZERO", "ZERO", "POS", "NEG")}
    point_bds = _read_point_base_decs_if_present(cent_obj)

    if point_bds is not None:
        for vi, bd in enumerate(point_bds):
            flags = _coeff_state_flags(bd_to_coeff.get(int(bd), 0.0))
            for key, active in flags.items():
                if active:
                    groups_to_verts[key].append(vi)
    else:
        if not bd_to_center:
            groups_to_verts["ZERO"] = list(range(n))
        else:
            try:
                from mathutils.kdtree import KDTree

                bd_items = list(bd_to_center.items())
                kd = KDTree(len(bd_items))
                for idx, (bd, c) in enumerate(bd_items):
                    try:
                        kd.insert((float(c[0]), float(c[1]), float(c[2])), idx)
                    except Exception:
                        kd.insert((0.0, 0.0, 0.0), idx)
                kd.balance()

                for vi in range(n):
                    vco = me.vertices[vi].co
                    co = (float(vco[0]), float(vco[1]), float(vco[2]))
                    _, idx, _ = kd.find(co)
                    bd = int(bd_items[idx][0])
                    flags = _coeff_state_flags(bd_to_coeff.get(bd, 0.0))
                    for key, active in flags.items():
                        if active:
                            groups_to_verts[key].append(vi)
            except Exception:
                groups_to_verts["ZERO"] = list(range(n))

    return _write_state_groups(cent_obj, groups_to_verts)


def _create_tetra_state_groups_from_flat_face_membership(
    flat_obj: bpy.types.Object | None,
    tetra_obj: bpy.types.Object | None,
    *,
    clear_existing: bool = True,
) -> int:
    if flat_obj is None or tetra_obj is None:
        return 0
    if getattr(flat_obj, "type", "") != "MESH" or getattr(tetra_obj, "type", "") != "MESH":
        return 0

    flat_me = getattr(flat_obj, "data", None)
    tetra_me = getattr(tetra_obj, "data", None)
    if flat_me is None or tetra_me is None:
        return 0

    flat_face_coeffs = _get_effective_face_coeffs(flat_me)
    if flat_face_coeffs is None:
        return 0

    flat_groups = _group_face_indices_by_base_dec(flat_me) or {}
    tetra_groups = _group_face_indices_by_base_dec(tetra_me) or {}

    groups_to_verts = {k: set() for k in ("NONZERO", "ZERO", "POS", "NEG")}
    mapped_faces = 0

    if flat_groups and tetra_groups:
        for bd, tetra_face_indices in tetra_groups.items():
            flat_face_indices = flat_groups.get(int(bd))
            if not flat_face_indices:
                continue
            for occ, tetra_fi in enumerate(tetra_face_indices):
                flat_fi = flat_face_indices[min(occ, len(flat_face_indices) - 1)]
                if flat_fi >= len(flat_face_coeffs) or tetra_fi >= len(tetra_me.polygons):
                    continue
                flags = _coeff_state_flags(flat_face_coeffs[flat_fi])
                for key, active in flags.items():
                    if not active:
                        continue
                    for vi in tetra_me.polygons[tetra_fi].vertices:
                        groups_to_verts[key].add(int(vi))
                mapped_faces += 1

    if mapped_faces == 0:
        lim = min(len(flat_face_coeffs), len(tetra_me.polygons))
        for fi in range(lim):
            flags = _coeff_state_flags(flat_face_coeffs[fi])
            for key, active in flags.items():
                if not active:
                    continue
                for vi in tetra_me.polygons[fi].vertices:
                    groups_to_verts[key].add(int(vi))
            mapped_faces += 1

    if mapped_faces == 0:
        return 0

    if clear_existing:
        _remove_prefixed_groups(tetra_obj, PREFIX_STATE)

    return _write_state_groups(tetra_obj, groups_to_verts)


def _create_nei_groups_on_mesh(obj: bpy.types.Object, face_bins: list[int]) -> int:
    """Crea 8 gruppi globali per bin usando i vertici delle facce nel bin."""
    me = obj.data
    bins_to_verts = [set() for _ in range(8)]

    for fi, poly in enumerate(me.polygons):
        if fi >= len(face_bins):
            break
        b = _clamp_bin(face_bins[fi])
        for vi in poly.vertices:
            bins_to_verts[b].add(int(vi))

    return _write_bins_to_vertex_groups(obj, bins_to_verts)


def _create_tetra_nei_groups_from_flat_face_membership(
    flat_obj: bpy.types.Object | None,
    tetra_obj: bpy.types.Object | None,
    *,
    clear_existing: bool = True,
) -> int:
    """Crea i FLO_NEI_* sul tetra usando l'appartenenza faccia->bin del flat.

    Regola desiderata: un tile tetra è nel gruppo G se il tile corrispondente del flat
    è nel gruppo G. La corrispondenza usa base_dec + indice di occorrenza (utile con extend).
    """
    if flat_obj is None or tetra_obj is None:
        return 0
    if getattr(flat_obj, "type", "") != "MESH" or getattr(tetra_obj, "type", "") != "MESH":
        return 0

    flat_me = getattr(flat_obj, "data", None)
    tetra_me = getattr(tetra_obj, "data", None)
    if flat_me is None or tetra_me is None:
        return 0

    flat_face_bins = _get_face_attr_bins(flat_me, ATTR_NAME_BOTH)
    if flat_face_bins is None:
        return 0

    flat_groups = _group_face_indices_by_base_dec(flat_me) or {}
    tetra_groups = _group_face_indices_by_base_dec(tetra_me) or {}

    bins_to_verts = [set() for _ in range(8)]
    mapped_faces = 0

    if flat_groups and tetra_groups:
        for bd, tetra_face_indices in tetra_groups.items():
            flat_face_indices = flat_groups.get(int(bd))
            if not flat_face_indices:
                continue
            for occ, tetra_fi in enumerate(tetra_face_indices):
                flat_fi = flat_face_indices[min(occ, len(flat_face_indices) - 1)]
                if flat_fi >= len(flat_face_bins) or tetra_fi >= len(tetra_me.polygons):
                    continue
                b = _clamp_bin(flat_face_bins[flat_fi])
                for vi in tetra_me.polygons[tetra_fi].vertices:
                    bins_to_verts[b].add(int(vi))
                mapped_faces += 1

    if mapped_faces == 0:
        lim = min(len(flat_face_bins), len(tetra_me.polygons))
        for fi in range(lim):
            b = _clamp_bin(flat_face_bins[fi])
            for vi in tetra_me.polygons[fi].vertices:
                bins_to_verts[b].add(int(vi))
            mapped_faces += 1

    if mapped_faces == 0:
        return 0

    if clear_existing:
        _remove_prefixed_groups(tetra_obj, PREFIX_NEI)
        _remove_prefixed_groups(tetra_obj, PREFIX_BALL)

    return _write_bins_to_vertex_groups(tetra_obj, bins_to_verts)


def _read_point_base_decs_if_present(cent_obj: bpy.types.Object) -> list[int] | None:
    me = cent_obj.data
    attr = me.attributes.get(ATTR_NAME_BASE_DEC)
    if attr is None:
        return None

    if attr.domain == "POINT" and len(attr.data) == len(me.vertices):
        out = []
        for i in range(len(me.vertices)):
            try:
                out.append(int(attr.data[i].value))
            except Exception:
                out.append(0)
        return out

    if attr.domain == "FACE":
        if len(me.polygons) == len(me.vertices) and len(attr.data) >= len(me.vertices):
            out = []
            for i in range(len(me.vertices)):
                try:
                    out.append(int(attr.data[i].value))
                except Exception:
                    out.append(0)
            return out

    return None


def _create_nei_groups_on_centroids(
    cent_obj: bpy.types.Object,
    *,
    bd_to_bin: dict[int, int],
    bd_to_center: dict[int, object],
) -> int:
    me = cent_obj.data
    n = len(me.vertices)
    if n == 0:
        return 0

    bins_to_verts = [[] for _ in range(8)]

    point_bds = _read_point_base_decs_if_present(cent_obj)
    if point_bds is not None:
        for vi, bd in enumerate(point_bds):
            b = bd_to_bin.get(int(bd), 0)
            bins_to_verts[max(0, min(7, b))].append(vi)
    else:
        if not bd_to_center:
            bins_to_verts[0] = list(range(n))
        else:
            try:
                from mathutils.kdtree import KDTree

                bd_items = list(bd_to_center.items())
                kd = KDTree(len(bd_items))
                for idx, (bd, c) in enumerate(bd_items):
                    try:
                        kd.insert((float(c[0]), float(c[1]), float(c[2])), idx)
                    except Exception:
                        kd.insert((0.0, 0.0, 0.0), idx)
                kd.balance()

                for vi in range(n):
                    vco = me.vertices[vi].co
                    co = (float(vco[0]), float(vco[1]), float(vco[2]))
                    _, idx, _ = kd.find(co)
                    bd = int(bd_items[idx][0])
                    b = bd_to_bin.get(bd, 0)
                    bins_to_verts[max(0, min(7, b))].append(vi)
            except Exception:
                bins_to_verts[0] = list(range(n))

    created = 0
    for b in range(8):
        name = f"{PREFIX_NEI}NEI_BOTH_{b}" if b < 7 else f"{PREFIX_NEI}NEI_BOTH_7P"
        vg = _ensure_vg(cent_obj, name)
        verts = bins_to_verts[b]
        if verts:
            vg.add(verts, 1.0, 'REPLACE')
        created += 1

    return created


def _targets_from_choice(choice: str) -> list[str]:
    if choice == "X":
        return ["Flo_X"]
    if choice == "Y":
        return ["Flo_Y"]
    if choice == "XY":
        return ["Flo_XY"]
    return ["Flo_X", "Flo_Y", "Flo_XY"]


def _find_scene_object(context, name: str) -> bpy.types.Object | None:
    scn = context.scene
    obj = scn.objects.get(name)
    if obj:
        return obj
    return bpy.data.objects.get(name)


def _find_scene_mesh(context, name: str) -> bpy.types.Object | None:
    obj = _find_scene_object(context, name)
    if obj and obj.type == "MESH":
        return obj
    return None


def _iter_main_targets(context, target: str):
    for obj_name in _targets_from_choice(target):
        obj = _find_scene_mesh(context, obj_name)
        if obj is not None:
            yield obj


def _iter_main_and_tetra_targets(context, target: str):
    for base_name in _targets_from_choice(target):
        obj = _find_scene_mesh(context, base_name)
        if obj is not None:
            yield obj
        tetra = _find_scene_mesh(context, _tetra_name_for(base_name))
        if tetra is not None:
            yield tetra


def _tetra_name_for(base_name: str) -> str:
    return f"{str(base_name)}{TETRA_SUFFIX}"


def _tetra_cent_name_for(base_name: str) -> str:
    return f"{str(base_name)}_cent{TETRA_SUFFIX}"


def _copy_prefixed_vertex_groups_by_index(
    src_obj: bpy.types.Object | None,
    dst_obj: bpy.types.Object | None,
    *,
    prefix: str = PREFIX_NEI,
    clear_existing: bool = True,
) -> int:
    if src_obj is None or dst_obj is None:
        return 0
    if getattr(src_obj, "type", "") != "MESH" or getattr(dst_obj, "type", "") != "MESH":
        return 0

    src_me = getattr(src_obj, "data", None)
    dst_me = getattr(dst_obj, "data", None)
    if src_me is None or dst_me is None:
        return 0

    if clear_existing:
        _remove_prefixed_groups(dst_obj, prefix)

    if len(src_me.vertices) != len(dst_me.vertices):
        return 0

    src_groups = [vg for vg in src_obj.vertex_groups if str(vg.name).startswith(prefix)]
    if not src_groups:
        return 0

    src_idx_to_dst_vg = {}
    for vg in src_groups:
        src_idx_to_dst_vg[int(vg.index)] = _ensure_vg(dst_obj, vg.name)

    copied = 0
    for v in src_me.vertices:
        vi = int(v.index)
        if vi >= len(dst_me.vertices):
            continue
        for ge in v.groups:
            dst_vg = src_idx_to_dst_vg.get(int(ge.group))
            if dst_vg is None:
                continue
            try:
                w = float(ge.weight)
            except Exception:
                w = 0.0
            if w <= 0.0:
                continue
            try:
                dst_vg.add([vi], w, 'REPLACE')
                copied += 1
            except Exception:
                pass

    return copied



def _sync_tetra_main_nei_groups_from_own_face_attrs(
    flat_obj: bpy.types.Object | None,
    tetra_obj: bpy.types.Object | None,
    *,
    clear_existing: bool = True,
) -> int:
    """Sincronizza i FLO_NEI_* del tetra dalla membership del flat.

    Questo è più robusto della copia per indice-vertice e aderisce alla regola richiesta:
    un tile tetra appartiene al gruppo G se il tile corrispondente del flat appartiene a G.
    Solo come fallback estremo usa i face attrs del tetra stesso.
    """
    created = _create_tetra_nei_groups_from_flat_face_membership(
        flat_obj,
        tetra_obj,
        clear_existing=clear_existing,
    )
    if created > 0:
        return created

    if tetra_obj is None or getattr(tetra_obj, "type", "") != "MESH":
        return 0

    me = getattr(tetra_obj, "data", None)
    if me is None:
        return 0

    face_bins = _get_face_attr_bins(me, ATTR_NAME_BOTH)
    if face_bins is None:
        return 0

    if clear_existing:
        _remove_prefixed_groups(tetra_obj, PREFIX_NEI)
        _remove_prefixed_groups(tetra_obj, PREFIX_BALL)

    return _create_nei_groups_on_mesh(tetra_obj, face_bins)



def sync_tetra_vertex_groups_from_flat(context, *, target: str = "ALL") -> int:
    copied = 0
    for base_name in _targets_from_choice(target):
        flat_obj = _find_scene_mesh(context, base_name)
        tetra_obj = _find_scene_mesh(context, _tetra_name_for(base_name))

        created_on_tetra = _sync_tetra_main_nei_groups_from_own_face_attrs(
            flat_obj,
            tetra_obj,
            clear_existing=True,
        )
        if created_on_tetra > 0:
            copied += created_on_tetra
        else:
            copied += _copy_prefixed_vertex_groups_by_index(
                flat_obj,
                tetra_obj,
                prefix=PREFIX_NEI,
                clear_existing=True,
            )

        state_created_on_tetra = _create_tetra_state_groups_from_flat_face_membership(
            flat_obj,
            tetra_obj,
            clear_existing=True,
        )
        if state_created_on_tetra > 0:
            copied += state_created_on_tetra
        else:
            copied += _copy_prefixed_vertex_groups_by_index(
                flat_obj,
                tetra_obj,
                prefix=PREFIX_STATE,
                clear_existing=True,
            )

        flat_cent = _find_scene_mesh(context, f"{base_name}_cent")
        tetra_cent = _find_scene_mesh(context, _tetra_cent_name_for(base_name))
        copied += _copy_prefixed_vertex_groups_by_index(
            flat_cent,
            tetra_cent,
            prefix=PREFIX_NEI,
            clear_existing=True,
        )
        copied += _copy_prefixed_vertex_groups_by_index(
            flat_cent,
            tetra_cent,
            prefix=PREFIX_STATE,
            clear_existing=True,
        )

    return copied


def apply_vg_material_policy(context, *, target: str = "ALL") -> int:
    """Compat legacy: niente più auto-materiali VG.

    Mantiene i vertex groups sincronizzati, ripulisce eventuali slot/materiali legacy
    e riallinea flat/tetra sul display shader-based.
    """
    try:
        ensure_nei_vertex_groups(
            context,
            target=target,
            clear_existing=True,
            apply_to_centroids=True,
            apply_to_tetra=True,
        )
    except Exception:
        pass

    changed = 0
    for obj in _iter_main_and_tetra_targets(context, target):
        try:
            changed += _remove_vg_materials_on_object(obj)
        except Exception:
            pass

    try:
        sync_tetra_display_from_flat(context, target=target)
    except Exception:
        pass

    try:
        _purge_vg_material_datablocks()
    except Exception:
        pass

    return changed


def uses_vg_material_assignment(obj: bpy.types.Object | None) -> bool:
    if obj is None or getattr(obj, "type", "") != "MESH":
        return False

    me = getattr(obj, "data", None)
    if me is None:
        return False

    try:
        for m in me.materials:
            if m is not None and str(getattr(m, "name", "")).startswith("FloretionMaterial_VG"):
                return True
    except Exception:
        pass

    try:
        for poly in me.polygons:
            if int(poly.material_index) > 1:
                return True
    except Exception:
        pass

    return False


def _copy_material_slots(src_obj: bpy.types.Object, dst_obj: bpy.types.Object) -> None:
    src_me = getattr(src_obj, "data", None)
    dst_me = getattr(dst_obj, "data", None)
    if src_me is None or dst_me is None:
        return

    dst_mats = dst_me.materials
    dst_mats.clear()
    try:
        for m in src_me.materials:
            dst_mats.append(m)
    except Exception:
        pass


def _group_face_indices_by_base_dec(me: bpy.types.Mesh) -> dict[int, list[int]] | None:
    base_attr = me.attributes.get(ATTR_NAME_BASE_DEC)
    if base_attr is None:
        return None

    groups: dict[int, list[int]] = {}
    data = base_attr.data
    for poly in me.polygons:
        if poly.index >= len(data):
            continue
        try:
            bd = int(data[poly.index].value)
        except Exception:
            continue
        groups.setdefault(bd, []).append(int(poly.index))
    return groups


def _ensure_compatible_color_layer(dst_me: bpy.types.Mesh, src_layer) -> object | None:
    if src_layer is None:
        return None

    dst_layer = dst_me.color_attributes.get(src_layer.name)
    if dst_layer is not None:
        return dst_layer

    try:
        return dst_me.color_attributes.new(
            name=str(src_layer.name),
            type=str(src_layer.data_type),
            domain=str(src_layer.domain),
        )
    except Exception:
        pass

    try:
        return dst_me.color_attributes.new(
            name=str(src_layer.name),
            type='FLOAT_COLOR',
            domain='CORNER',
        )
    except Exception:
        return None



def _ensure_compatible_mesh_attr(dst_me: bpy.types.Mesh, src_attr) -> object | None:
    if src_attr is None:
        return None

    name = str(getattr(src_attr, "name", "") or "")
    data_type = str(getattr(src_attr, "data_type", "FLOAT") or "FLOAT")
    domain = str(getattr(src_attr, "domain", "FACE") or "FACE")

    dst_attr = dst_me.attributes.get(name)
    if dst_attr is not None:
        try:
            if str(getattr(dst_attr, "data_type", "")) == data_type and str(getattr(dst_attr, "domain", "")) == domain:
                return dst_attr
            dst_me.attributes.remove(dst_attr)
        except Exception:
            dst_attr = None

    try:
        return dst_me.attributes.new(name=name, type=data_type, domain=domain)
    except Exception:
        return None


def _build_face_map_by_base_dec(src_me: bpy.types.Mesh, dst_me: bpy.types.Mesh) -> dict[int, int]:
    src_groups = _group_face_indices_by_base_dec(src_me) or {}
    dst_groups = _group_face_indices_by_base_dec(dst_me) or {}

    face_map: dict[int, int] = {}
    if src_groups and dst_groups:
        for bd, dst_list in dst_groups.items():
            src_list = src_groups.get(int(bd))
            if not src_list:
                continue
            for occ, dst_idx in enumerate(dst_list):
                src_idx = src_list[min(occ, len(src_list) - 1)]
                face_map[int(dst_idx)] = int(src_idx)

    if not face_map:
        lim = min(len(src_me.polygons), len(dst_me.polygons))
        face_map = {int(i): int(i) for i in range(lim)}

    return face_map


def _copy_face_scalar_attr_by_mapping(
    src_me: bpy.types.Mesh,
    dst_me: bpy.types.Mesh,
    face_map: dict[int, int],
    attr_name: str,
) -> int:
    src_attr = src_me.attributes.get(str(attr_name))
    if src_attr is None:
        return 0
    if str(getattr(src_attr, "domain", "")) != "FACE":
        return 0

    dst_attr = _ensure_compatible_mesh_attr(dst_me, src_attr)
    if dst_attr is None:
        return 0

    src_data = getattr(src_attr, "data", None)
    dst_data = getattr(dst_attr, "data", None)
    if src_data is None or dst_data is None:
        return 0

    copied = 0
    for dst_idx, src_idx in face_map.items():
        if src_idx >= len(src_data) or dst_idx >= len(dst_data):
            continue
        src_item = src_data[src_idx]
        dst_item = dst_data[dst_idx]
        try:
            dst_item.value = src_item.value
            copied += 1
            continue
        except Exception:
            pass
        try:
            dst_item.vector = src_item.vector
            copied += 1
            continue
        except Exception:
            pass
        try:
            dst_item.color = src_item.color
            copied += 1
        except Exception:
            pass

    return copied


def _copy_loop_color_attr_by_mapping(
    src_me: bpy.types.Mesh,
    dst_me: bpy.types.Mesh,
    face_map: dict[int, int],
    attr_name: str,
) -> int:
    src_layer = src_me.color_attributes.get(str(attr_name))
    if src_layer is None:
        return 0

    dst_layer = _ensure_compatible_color_layer(dst_me, src_layer)
    if dst_layer is None:
        return 0

    src_data = getattr(src_layer, "data", None)
    dst_data = getattr(dst_layer, "data", None)
    if src_data is None or dst_data is None:
        return 0

    copied = 0
    for dst_idx, src_idx in sorted(face_map.items()):
        if dst_idx >= len(dst_me.polygons) or src_idx >= len(src_me.polygons):
            continue

        src_poly = src_me.polygons[src_idx]
        dst_poly = dst_me.polygons[dst_idx]
        src_li = int(src_poly.loop_start)
        dst_li = int(dst_poly.loop_start)
        copy_count = min(int(src_poly.loop_total), int(dst_poly.loop_total))

        for off in range(copy_count):
            si = src_li + off
            di = dst_li + off
            if si >= len(src_data) or di >= len(dst_data):
                continue
            try:
                dst_data[di].color = tuple(src_data[si].color)
                copied += 1
            except Exception:
                pass

    return copied

def sync_tetra_display_from_source(source_obj: bpy.types.Object | None, tetra_obj: bpy.types.Object | None) -> int:
    if source_obj is None or tetra_obj is None:
        return 0
    if getattr(source_obj, "type", "") != "MESH" or getattr(tetra_obj, "type", "") != "MESH":
        return 0

    src_me = source_obj.data
    dst_me = tetra_obj.data
    if src_me is None or dst_me is None:
        return 0

    _copy_material_slots(source_obj, tetra_obj)
    face_map = _build_face_map_by_base_dec(src_me, dst_me)
    if not face_map:
        return 0

    changed = 0

    for dst_idx, src_idx in sorted(face_map.items()):
        if dst_idx >= len(dst_me.polygons) or src_idx >= len(src_me.polygons):
            continue

        src_poly = src_me.polygons[src_idx]
        dst_poly = dst_me.polygons[dst_idx]

        try:
            dst_poly.material_index = int(src_poly.material_index)
        except Exception:
            pass

        changed += 1

    for attr_name in _SYNC_FACE_FLOAT_ATTRS:
        try:
            _copy_face_scalar_attr_by_mapping(src_me, dst_me, face_map, attr_name)
        except Exception:
            pass

    for attr_name in _SYNC_FACE_INT_ATTRS:
        try:
            _copy_face_scalar_attr_by_mapping(src_me, dst_me, face_map, attr_name)
        except Exception:
            pass

    for attr_name in _SYNC_CORNER_COLOR_ATTRS:
        try:
            _copy_loop_color_attr_by_mapping(src_me, dst_me, face_map, attr_name)
        except Exception:
            pass

    try:
        dst_me.update()
    except Exception:
        pass
    try:
        dst_me.update_gpu_tag()
    except Exception:
        pass
    return changed



def sync_tetra_display_from_flat(context, *, target: str = "ALL") -> int:
    changed = 0
    for base_name in _targets_from_choice(target):
        src_obj = _find_scene_mesh(context, base_name)
        dst_obj = _find_scene_mesh(context, _tetra_name_for(base_name))
        changed += sync_tetra_display_from_source(src_obj, dst_obj)

        src_cent = _find_scene_mesh(context, f"{base_name}_cent")
        dst_cent = _find_scene_mesh(context, _tetra_cent_name_for(base_name))
        changed += sync_tetra_display_from_source(src_cent, dst_cent)

    changed += sync_tetra_vertex_groups_from_flat(context, target=target)
    return changed


def assign_vg_materials_on_object(obj: bpy.types.Object | None) -> int:
    if obj is None:
        return 0
    return _remove_vg_materials_on_object(obj)


def ensure_nei_vertex_groups(
    context,
    *,
    target: str = "ALL",
    clear_existing: bool = True,
    apply_to_centroids: bool = True,
    apply_to_tetra: bool = True,
) -> int:
    """Sincronizza automaticamente i gruppi FLO_NEI_* / FLO_STATE_*.

    Nota importante:
    - FLO_NEI_* dipende dai face-attrs neighbor bins.
    - FLO_STATE_* dipende invece dai coeffs (face_coeff/base_coeff/coeff)
      e NON deve sparire solo perché un oggetto non ha il layer neighbor.
    """
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass

    total_created = 0
    for obj_name in _targets_from_choice(target):
        obj = _find_scene_mesh(context, obj_name)
        if obj is None:
            continue

        me = obj.data
        face_bins = _get_face_attr_bins(me, ATTR_NAME_BOTH)
        face_coeffs = _get_effective_face_coeffs(me)
        face_bds = _get_face_attr_ints(me, ATTR_NAME_BASE_DEC)

        if clear_existing:
            _remove_prefixed_groups(obj, PREFIX_NEI)
            _remove_prefixed_groups(obj, PREFIX_BALL)
            _remove_prefixed_groups(obj, PREFIX_STATE)

        # -----------------------------
        # Main object: NEI groups
        # -----------------------------
        if face_bins is not None:
            total_created += _create_nei_groups_on_mesh(obj, face_bins)

        # -----------------------------
        # Main object: STATE groups
        # -----------------------------
        if face_coeffs is not None:
            total_created += _create_state_groups_on_mesh(obj, face_coeffs)

        # -----------------------------
        # Centroids: NEI / STATE
        # -----------------------------
        if apply_to_centroids:
            cent_obj = _find_scene_mesh(context, f"{obj_name}_cent")
            if cent_obj is not None:
                if clear_existing:
                    _remove_prefixed_groups(cent_obj, PREFIX_NEI)
                    _remove_prefixed_groups(cent_obj, PREFIX_BALL)
                    _remove_prefixed_groups(cent_obj, PREFIX_STATE)

                if face_bins is not None and face_bds is not None:
                    bd_to_bin, bd_to_center = _build_base_dec_maps_from_main_faces(obj, face_bins, face_bds)
                    total_created += _create_nei_groups_on_centroids(
                        cent_obj,
                        bd_to_bin=bd_to_bin,
                        bd_to_center=bd_to_center,
                    )

                if face_coeffs is not None and face_bds is not None:
                    bd_to_coeff, bd_to_coeff_center = _build_base_dec_coeff_maps_from_main_faces(
                        obj,
                        face_coeffs,
                        face_bds,
                    )
                    total_created += _create_state_groups_on_centroids(
                        cent_obj,
                        bd_to_coeff=bd_to_coeff,
                        bd_to_center=bd_to_coeff_center,
                    )

        # -----------------------------
        # Tetra main / tetra centroids
        # -----------------------------
        if apply_to_tetra:
            tetra_obj = _find_scene_mesh(context, _tetra_name_for(obj_name))

            if tetra_obj is not None:
                created_on_tetra = _sync_tetra_main_nei_groups_from_own_face_attrs(
                    obj,
                    tetra_obj,
                    clear_existing=clear_existing,
                )
                if created_on_tetra > 0:
                    total_created += created_on_tetra
                else:
                    total_created += _copy_prefixed_vertex_groups_by_index(
                        obj,
                        tetra_obj,
                        prefix=PREFIX_NEI,
                        clear_existing=clear_existing,
                    )

                state_created_on_tetra = _create_tetra_state_groups_from_flat_face_membership(
                    obj,
                    tetra_obj,
                    clear_existing=clear_existing,
                )
                if state_created_on_tetra > 0:
                    total_created += state_created_on_tetra
                else:
                    total_created += _copy_prefixed_vertex_groups_by_index(
                        obj,
                        tetra_obj,
                        prefix=PREFIX_STATE,
                        clear_existing=clear_existing,
                    )

            flat_cent = _find_scene_mesh(context, f"{obj_name}_cent")
            tetra_cent = _find_scene_mesh(context, _tetra_cent_name_for(obj_name))
            total_created += _copy_prefixed_vertex_groups_by_index(
                flat_cent,
                tetra_cent,
                prefix=PREFIX_NEI,
                clear_existing=clear_existing,
            )
            total_created += _copy_prefixed_vertex_groups_by_index(
                flat_cent,
                tetra_cent,
                prefix=PREFIX_STATE,
                clear_existing=clear_existing,
            )

    return total_created


_VG_PALETTE_RGBA01 = {
    1: (0.10, 0.25, 0.95, 1.0),
    2: (0.10, 0.75, 0.85, 1.0),
    3: (0.15, 0.85, 0.20, 1.0),
    4: (0.95, 0.85, 0.15, 1.0),
    5: (0.95, 0.55, 0.10, 1.0),
    6: (0.95, 0.15, 0.15, 1.0),
    7: (0.80, 0.15, 0.85, 1.0),
}


def _ensure_principled_vg_material(bin_idx: int):
    mat_name = f"FloretionMaterial_VG{max(1, min(7, int(bin_idx)))}"
    rgba = _VG_PALETTE_RGBA01[max(1, min(7, int(bin_idx)))]
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes = nt.nodes
    out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if out is None:
        out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    if bsdf.outputs.get("BSDF") and out.inputs.get("Surface"):
        has_link = any(l.from_socket == bsdf.outputs["BSDF"] and l.to_socket == out.inputs["Surface"] for l in nt.links)
        if not has_link:
            for l in list(nt.links):
                if l.to_socket == out.inputs["Surface"]:
                    nt.links.remove(l)
            nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if bsdf.inputs.get("Base Color") is not None:
        bsdf.inputs["Base Color"].default_value = rgba
    emit_col = bsdf.inputs.get("Emission") or bsdf.inputs.get("Emission Color")
    if emit_col is not None:
        emit_col.default_value = rgba
    emit_str = bsdf.inputs.get("Emission Strength")
    if emit_str is not None and float(emit_str.default_value) <= 0.0:
        emit_str.default_value = 1.0
    return mat


def _face_bins_from_vgroups(obj: bpy.types.Object) -> list[int] | None:
    """
    Fallback robusto: deriva il bin faccia dai vertex groups FLO_NEI_*.
    Non è perfetto come il face-attr, ma è utile se il face-attr manca
    o se l'utente ha appena rigenerato i gruppi.
    """
    me = obj.data
    if len(me.polygons) == 0:
        return []

    vg_names = {
        0: f"{PREFIX_NEI}NEI_BOTH_0",
        1: f"{PREFIX_NEI}NEI_BOTH_1",
        2: f"{PREFIX_NEI}NEI_BOTH_2",
        3: f"{PREFIX_NEI}NEI_BOTH_3",
        4: f"{PREFIX_NEI}NEI_BOTH_4",
        5: f"{PREFIX_NEI}NEI_BOTH_5",
        6: f"{PREFIX_NEI}NEI_BOTH_6",
        7: f"{PREFIX_NEI}NEI_BOTH_7P",
    }

    vg_index_to_bin = {}
    for b, name in vg_names.items():
        vg = obj.vertex_groups.get(name)
        if vg is not None:
            vg_index_to_bin[int(vg.index)] = int(b)

    if not vg_index_to_bin:
        return None

    vert_bins = [set() for _ in range(len(me.vertices))]
    for v in me.vertices:
        bins = set()
        for g in v.groups:
            b = vg_index_to_bin.get(int(g.group))
            if b is not None and float(g.weight) > 0.0:
                bins.add(int(b))
        vert_bins[v.index] = bins

    face_bins = [0] * len(me.polygons)
    for poly in me.polygons:
        counts = [0] * 8
        for vi in poly.vertices:
            for b in vert_bins[vi]:
                counts[b] += 1

        best_b = 0
        best_c = counts[0]
        for b in range(1, 8):
            if counts[b] > best_c:
                best_b = b
                best_c = counts[b]

        face_bins[poly.index] = best_b

    return face_bins


def _best_face_bins_for_material_assignment(obj: bpy.types.Object) -> list[int] | None:
    me = obj.data
    try:
        obj.update_from_editmode()
    except Exception:
        pass

    bins = _get_face_attr_bins(me, ATTR_NAME_BOTH)
    if bins is not None:
        return bins
    return _face_bins_from_vgroups(obj)


def _assign_vg_materials_on_object(obj: bpy.types.Object) -> int:
    me = obj.data
    coeff_attr = me.attributes.get("face_coeff")
    face_bins = _best_face_bins_for_material_assignment(obj)

    base_mat = bpy.data.materials.get("FloretionMaterial")
    zero_mat = bpy.data.materials.get("FloretionCoeffZeroMaterial")
    vg_mats = [_ensure_principled_vg_material(i) for i in range(1, 8)]

    mats = me.materials
    mats.clear()
    if base_mat is not None:
        mats.append(base_mat)     # slot 0
    if zero_mat is not None:
        mats.append(zero_mat)     # slot 1
    for m in vg_mats:
        mats.append(m)            # slot 2..8

    if face_bins is None:
        me.update()
        return 0

    changed = 0
    for poly in me.polygons:
        coeff_face = 0.0
        nei_both = face_bins[poly.index] if poly.index < len(face_bins) else 0
        if coeff_attr is not None and poly.index < len(coeff_attr.data):
            try:
                coeff_face = float(coeff_attr.data[poly.index].value)
            except Exception:
                coeff_face = 0.0

        if abs(coeff_face) < 1e-12 and len(me.materials) >= 2:
            poly.material_index = 1
        else:
            # slot 0 = base, slot 1 = zero, slot 2..8 = VG1..VG7
            poly.material_index = 0 if nei_both <= 0 else min(1 + int(nei_both), len(me.materials) - 1)
        changed += 1

    me.update()
    try:
        me.update_gpu_tag()
    except Exception:
        pass
    return changed


def _remove_vg_materials_on_object(obj: bpy.types.Object) -> int:
    me = obj.data
    coeff_attr = me.attributes.get("face_coeff")
    changed = 0

    base_mat = bpy.data.materials.get("FloretionMaterial")
    zero_mat = bpy.data.materials.get("FloretionCoeffZeroMaterial")

    mats = me.materials
    mats.clear()
    if base_mat is not None:
        mats.append(base_mat)
    if zero_mat is not None:
        mats.append(zero_mat)

    if coeff_attr is None:
        me.update()
        return 0

    for poly in me.polygons:
        coeff_face = 0.0
        if poly.index < len(coeff_attr.data):
            try:
                coeff_face = float(coeff_attr.data[poly.index].value)
            except Exception:
                coeff_face = 0.0
        if abs(coeff_face) < 1e-12 and len(me.materials) >= 2:
            poly.material_index = 1
        else:
            poly.material_index = 0
        changed += 1

    me.update()
    return changed


def _purge_vg_material_datablocks():
    for i in range(1, 8):
        mat = bpy.data.materials.get(f"FloretionMaterial_VG{i}")
        if mat is not None and mat.users == 0:
            try:
                bpy.data.materials.remove(mat)
            except Exception:
                pass


class FLORET_MESH_OT_reset_vg_extrusion(Operator):
    bl_idname = "floret_mesh.reset_vg_extrusion"
    bl_label = "Reset VG Extrusion"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.floretion_mesh_settings
        try:
            _ui_props._bulk_updates_on()
        except Exception:
            pass
        try:
            for nm in ("mask_bin_0", "mask_bin_1", "mask_bin_2", "mask_bin_3", "mask_bin_4", "mask_bin_5", "mask_bin_6", "mask_bin_7p"):
                setattr(props, nm, 0.0)
            for nm in ("vg_wall_0", "vg_wall_1", "vg_wall_2", "vg_wall_3", "vg_wall_4", "vg_wall_5", "vg_wall_6", "vg_wall_7p"):
                setattr(props, nm, False)
        finally:
            try:
                _ui_props._bulk_updates_off()
            except Exception:
                pass
        try:
            bpy.ops.floret_mesh.rebuild_cached('EXEC_DEFAULT')
        except Exception:
            pass
        self.report({'INFO'}, "VG Extrusion reset.")
        return {'FINISHED'}


class FLORET_MESH_OT_make_nei_vertex_groups(Operator):
    bl_idname = "floret_mesh.make_nei_vertex_groups"
    bl_label = "Create Floretion Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[
            ("ALL", "All", ""),
            ("X",   "X (Flo_X)", ""),
            ("Y",   "Y (Flo_Y)", ""),
            ("XY",  "X·Y (Flo_XY)", ""),
        ],
        default="ALL",
    )

    clear_existing: BoolProperty(
        name="Clear existing",
        default=True,
        description="Rimuove prima i vertex groups FLO_NEI_* / FLO_STATE_* e pulisce eventuali FLO_BALL_* legacy",
    )

    apply_to_centroids: BoolProperty(
        name="Also apply to centroids",
        default=True,
        description="Crea gli stessi gruppi anche su Flo_*_cent",
    )

    def execute(self, context):
        created = ensure_nei_vertex_groups(
            context,
            target=self.target,
            clear_existing=bool(self.clear_existing),
            apply_to_centroids=bool(self.apply_to_centroids),
            apply_to_tetra=True,
        )
        self.report({'INFO'}, f"Creati/sincronizzati {created} gruppi FLO_NEI_* / FLO_STATE_*.")
        return {'FINISHED'}


class FLORET_MESH_OT_remove_floretion_vertex_groups(Operator):
    bl_idname = "floret_mesh.remove_floretion_vertex_groups"
    bl_label = "Remove FLO_NEI / FLO_STATE / FLO_BALL Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[
            ("ALL", "All", ""),
            ("X",   "X (Flo_X)", ""),
            ("Y",   "Y (Flo_Y)", ""),
            ("XY",  "X·Y (Flo_XY)", ""),
        ],
        default="ALL",
    )

    remove_from_centroids: BoolProperty(
        name="Also remove from centroids",
        default=True,
        description="Rimuove i gruppi anche da Flo_*_cent",
    )

    def execute(self, context):
        total = 0
        for obj_name in _targets_from_choice(self.target):
            obj = _find_scene_mesh(context, obj_name)
            if obj is not None:
                total += _remove_prefixed_groups(obj, PREFIX_NEI)
                total += _remove_prefixed_groups(obj, PREFIX_STATE)
                total += _remove_prefixed_groups(obj, PREFIX_BALL)

            tetra_obj = _find_scene_mesh(context, _tetra_name_for(obj_name))
            if tetra_obj is not None:
                total += _remove_prefixed_groups(tetra_obj, PREFIX_NEI)
                total += _remove_prefixed_groups(tetra_obj, PREFIX_STATE)
                total += _remove_prefixed_groups(tetra_obj, PREFIX_BALL)

            if self.remove_from_centroids:
                cent_obj = _find_scene_mesh(context, f"{obj_name}_cent")
                if cent_obj is not None:
                    total += _remove_prefixed_groups(cent_obj, PREFIX_NEI)
                    total += _remove_prefixed_groups(cent_obj, PREFIX_STATE)
                    total += _remove_prefixed_groups(cent_obj, PREFIX_BALL)

                tetra_cent_obj = _find_scene_mesh(context, _tetra_cent_name_for(obj_name))
                if tetra_cent_obj is not None:
                    total += _remove_prefixed_groups(tetra_cent_obj, PREFIX_NEI)
                    total += _remove_prefixed_groups(tetra_cent_obj, PREFIX_STATE)
                    total += _remove_prefixed_groups(tetra_cent_obj, PREFIX_BALL)

        self.report({'INFO'}, f"Rimossi {total} vertex groups (FLO_NEI / FLO_STATE / FLO_BALL).")
        return {'FINISHED'}


classes = (
    FLORET_MESH_OT_make_nei_vertex_groups,
    FLORET_MESH_OT_remove_floretion_vertex_groups,
    FLORET_MESH_OT_reset_vg_extrusion,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)