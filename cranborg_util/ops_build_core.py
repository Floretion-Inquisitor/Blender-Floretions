# cranborg_util/ops_build_core.py

from __future__ import annotations

import bpy
from bpy.types import Operator
import bmesh

import numpy as np


# Merge-by-distance sempre ON: salda vertici coincidenti per ottenere adiacenze reali.
# Valore molto piccolo: unisce solo duplicati numerici, non 'chiude' gap dovuti a tile size/spacing.
_MERGE_BY_DISTANCE_DIST = 1e-6
from floretion import Floretion

from .ui_props import FloretionMeshSettings
from . import seeds
from .sampling import sample_floretion

# NOTE: non importiamo più compute_colors "per nome"
# perché il tuo color_adapter aggiornato potrebbe non esportarlo.
from . import color_adapter as _color_adapter

from .mesh_build import (
    build_geometry,
    ensure_floretion_material,
    ensure_floretion_zero_material,
)

from .shader_neighbor_attrs import compute_neighbor_counts_bmesh, write_neighbor_bmesh_layers
from .shader_neighbor_nodes import ensure_neighbor_color_nodes, set_neighbor_color_mode

# centroid distance (firma nuova: keyword-only, niente coeff_mode)
from lib.triangleize_utils.centroid_distance import flo_from_centroid_distance

from .ops_build_scene import (
    ensure_floretion_collection,
    ensure_object_in_collection,
    _ensure_unique_obj_data,
    _manifest_needs_reset,
    _hard_reset_floretion_objects,
    _ensure_viewport_label,
    _scene_manifest_set,
    _MANAGED_TAG,
    _MANAGED_ROLE,
)

# ---------------------------------------------------------------------------
# Colori: compatibilità con color_adapter "nuovo"
# ---------------------------------------------------------------------------

try:
    from lib.triangleize_utils.coloring import choose_max_val_for_colors, map_color
except Exception:  # pragma: no cover
    choose_max_val_for_colors = None
    map_color = None

def _mode_id_to_str(mode_id: str) -> str:
    fn = getattr(_color_adapter, "mode_id_to_str", None)
    if callable(fn):
        try:
            return str(fn(mode_id))
        except Exception:
            pass

    s = str(mode_id or "").strip()
    if s and (("-" in s) or s.islower()):
        return s

    return {
        'ABS_HSV':   "abs-hsv",
        'DIVERGING': "diverging",
        'GRAY':      "gray",
        'LOG_HSV':   "log-hsv",
        'BANDED':    "banded",
        'LEGACY':    "legacy",
        'PASTEL':    "pastel",
        'COOLWARM':  "coolwarm",
        'HEAT':      "heat",
        'NEON':      "neon",
        'INK':       "ink",
    }.get(s, "abs-hsv")


def _neg_policy_id_to_str(neg_id: str) -> str:
    fn = getattr(_color_adapter, "neg_policy_id_to_str", None)
    if callable(fn):
        try:
            return str(fn(neg_id))
        except Exception:
            pass

    s = str(neg_id or "").strip()
    if s and (("-" in s) or s.islower()):
        return s

    return {
        'HUE_180': "hue-180",
        'HUE_90':  "hue-90",
        'NONE':    "none",
    }.get(s, "hue-180")


def compute_colors(
    coeffs: np.ndarray,
    basevec_at_pct: np.ndarray,
    dist_norm: np.ndarray,
    *,
    color_mode_id: str,
    max_val_config: float,
    auto_clip_pct: float,
    gamma: float,
    sat_dist_weight: float,
    neg_policy_id: str,
    band_count: int = 8,
):
    """Compat layer.

    - Se color_adapter espone compute_colors, usa quella.
    - Altrimenti: fallback su coloring.map_color (lib triangleize_utils).
    """
    fn = getattr(_color_adapter, "compute_colors", None)
    if callable(fn):
        return fn(
            coeffs=coeffs,
            basevec_at_pct=basevec_at_pct,
            dist_norm=dist_norm,
            color_mode_id=base_color_mode_id,
            max_val_config=max_val_config,
            auto_clip_pct=auto_clip_pct,
            gamma=gamma,
            sat_dist_weight=sat_dist_weight,
            neg_policy_id=neg_policy_id,
            band_count=band_count,
        )

    if map_color is None or choose_max_val_for_colors is None:
        raise ImportError("lib.triangleize_utils.coloring non disponibile e compute_colors mancante in color_adapter.py")

    mode_str = _mode_id_to_str(color_mode_id)
    neg_policy_str = _neg_policy_id_to_str(neg_policy_id)

    if max_val_config > 0:
        max_val_eff = float(max_val_config)
    else:
        max_val_eff = choose_max_val_for_colors(coeffs, None, auto_clip_pct)

    n = len(coeffs)
    colors = np.zeros((n, 3), dtype=float)

    for i in range(n):
        bgr, _ = map_color(
            coeff=float(coeffs[i]),
            basevec_at_pct=float(basevec_at_pct[i]) if len(basevec_at_pct) > i else 0.0,
            dist_norm=float(dist_norm[i]) if len(dist_norm) > i else 0.0,
            mode=mode_str,
            max_val=float(max_val_eff),
            gamma=float(gamma),
            sat_dist_weight=float(sat_dist_weight),
            neg_policy=neg_policy_str,
            band_count=int(band_count),
        )
        colors[i, :] = bgr

    mags = np.abs(coeffs.astype(float))
    if max_val_eff > 0:
        brightness = np.clip(mags / max_val_eff, 0.0, 1.0)
    else:
        brightness = np.zeros_like(mags, dtype=float)

    return colors, brightness



# ---------------------------------------------------------------------------
# Neighbors (adiacenza tile) via TOPOLOGIA MESH
#
# Funziona solo se le facce condividono davvero vertici/edge → per questo il
# build fa merge-by-distance sempre ON.
# ---------------------------------------------------------------------------

_NEIGH_PALETTE_RGBA01 = [
    (0.0, 0.0, 1.0, 1.0),  # 0 blu
    (0.0, 1.0, 1.0, 1.0),  # 1 ciano
    (1.0, 0.0, 0.0, 1.0),  # 2 rosso
    (0.0, 1.0, 0.0, 1.0),  # 3 verde
    (1.0, 0.0, 1.0, 1.0),  # 4 viola/magenta
    (1.0, 1.0, 0.0, 1.0),  # 5 giallo
    (1.0, 0.5, 0.0, 1.0),  # 6 arancione
    (1.0, 1.0, 1.0, 1.0),  # 7+ bianco
]


def _compute_neighbor_counts_bmesh(bm: bmesh.types.BMesh, face_coeffs_list, mode_id: str):
    """
    Conta i vicini NON-ZERO per faccia usando link_faces su edge/vertici.

    mode_id:
      - NEIGH_EDGE_HUE : vicini che condividono un EDGE
      - NEIGH_VERT_HUE : vicini SOLO-vertice (condividono un vertice ma NON un edge)
      - NEIGH_EDGE_SAT : vicini edge OR vertice (unione)

    I vicini sono conteggiati come set: un vicino vale 1 anche se tocca in più punti.
    """
    bm.faces.ensure_lookup_table()
    n = len(bm.faces)
    if n == 0:
        return []

    # non-zero: coeff != 0 (positivo o negativo)
    active = [abs(float(face_coeffs_list[i])) > 1e-12 for i in range(min(n, len(face_coeffs_list)))]
    if len(active) < n:
        active.extend([False] * (n - len(active)))

    counts = [0] * n
    for i, f in enumerate(bm.faces):
        if not active[i]:
            counts[i] = 0
            continue

        edge_neigh = set()
        for e in f.edges:
            for ff in e.link_faces:
                if ff != f:
                    j = ff.index
                    if 0 <= j < n and active[j]:
                        edge_neigh.add(j)

        vert_neigh = set()
        for v in f.verts:
            for ff in v.link_faces:
                if ff != f:
                    j = ff.index
                    if 0 <= j < n and active[j]:
                        vert_neigh.add(j)

        if mode_id == "NEIGH_EDGE_HUE":
            nset = edge_neigh
        elif mode_id == "NEIGH_VERT_HUE":
            nset = (vert_neigh - edge_neigh)
        else:
            nset = (edge_neigh | vert_neigh)

        counts[i] = len(nset)

    return counts





def _build_mesh_triplet(context, props: FloretionMeshSettings, flo_x: Floretion, flo_y: Floretion, flo_z: Floretion, op: Operator | None = None):
    """Costruisce/aggiorna oggetti X, Y, X·Y usando Floretions già pronte (NO moltiplicazione)."""

    flo_coll = ensure_floretion_collection(context)
    needs_reset, reason = _manifest_needs_reset(context, flo_coll)
    if needs_reset:
        _hard_reset_floretion_objects(context, flo_coll, op=op, reason=reason)

    try:
        spacing = float(props.spacing)
    except Exception:
        spacing = 6.0

    try:
        max_h = float(props.max_height)
    except Exception:
        max_h = 2.0

    if props.height_mode == "coeff":
        z_mode = "COEFF_SIGNED"
        z_coeff_scale = max_h
    elif props.height_mode == "index":
        z_mode = "COEFF_SIGNED"
        z_coeff_scale = max_h
    else:
        z_mode = "FLAT"
        z_coeff_scale = 0.0

    color_mode_id = props.color_mode or "ABS_HSV"

    auto_clip_pct = 99.0
    gamma = 0.6
    sat_dist_weight = 0.5
    neg_policy_id = "HUE_180"
    max_val_config = -1.0  # <0 => usa clip automatico

    try:
        emission_strength = max(0.0, float(props.emission_strength))
    except Exception:
        emission_strength = 50.0

    try:
        extrusion_depth = max(0.0, float(props.extrusion_depth))
    except Exception:
        extrusion_depth = 0.0

    mat = ensure_floretion_material()
    try:
        ensure_neighbor_color_nodes(mat)
        set_neighbor_color_mode(mat, str(color_mode_id))
    except Exception:
        pass
    mat_zero = ensure_floretion_zero_material()

    if mat is not None and mat.node_tree is not None:
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                es_input = node.inputs.get("Emission Strength")
                if es_input is not None:
                    es_input.default_value = emission_strength
                break

    def _report(level: str, msg: str) -> None:
        print(msg)
        if op is not None:
            try:
                op.report({level}, msg)
            except Exception:
                pass

    def _parent_keep_world(child: bpy.types.Object, parent: bpy.types.Object) -> None:
        if child is None or parent is None:
            return
        try:
            mw = child.matrix_world.copy()
            child.parent = parent
            child.matrix_parent_inverse = parent.matrix_world.inverted()
            child.matrix_world = mw
        except Exception:
            try:
                child.parent = parent
            except Exception:
                pass

    def _ensure_linked(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
        # membership checks su bpy_prop_collection sono un po' “capricciose” tra versioni:
        # usiamo l'helper robusto.
        ensure_object_in_collection(obj, collection)

    def build_object_from_floretion(flo, name_prefix: str, x_offset: float, label_text: str):
        try:
            ignore_zero = not bool(props.full_grid)
            samples = sample_floretion(flo, ignore_zero=ignore_zero)
        except Exception as e:
            msg_loc = f"Sampling error for {name_prefix}: {e}"
            _report('ERROR', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        coeffs = samples["coeffs"]
        if len(coeffs) == 0:
            return None, None, None

        n = len(coeffs)
        indices = samples["indices"].astype(float)
        dists = samples["dists"]

        basevec_at_pct = indices / (n - 1.0) if n > 1 else np.zeros_like(indices)
        max_dist = float(dists.max()) if np.any(dists) else 1.0
        dist_norm = dists / max(max_dist, 1e-12)

        try:
            base_color_mode_id = color_mode_id
            if str(color_mode_id) in ('NEIGH_EDGE_HUE','NEIGH_VERT_HUE','NEIGH_EDGE_SAT'):
                # placeholder per la geometria: poi sovrascriviamo i colori con i neighbors
                base_color_mode_id = 'ABS_HSV'
            colors_bgr, brightness = compute_colors(
                coeffs=coeffs,
                basevec_at_pct=basevec_at_pct,
                dist_norm=dist_norm,
                color_mode_id=base_color_mode_id,
                max_val_config=max_val_config,
                auto_clip_pct=auto_clip_pct,
                gamma=gamma,
                sat_dist_weight=sat_dist_weight,
                neg_policy_id=neg_policy_id,
            )
        except Exception as e:
            msg_loc = f"Color mapping error for {name_prefix}: {e}"
            _report('ERROR', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        samples_for_geom = dict(samples)
        if props.height_mode == "index":
            idx_norm = np.linspace(-1.0, 1.0, n, dtype=float)
            samples_for_geom["coeffs"] = idx_norm

        try:
            verts, faces, face_colors, centroids, face_coeffs, face_base_decs = build_geometry(
                samples_for_geom,
                colors_bgr,
                brightness,
                global_scale=1.0,
                tri_size=1.0,
                z_mode=z_mode,
                z_coeff_scale=z_coeff_scale,
                plot_mode="TRIANGLES",
                extrusion_depth=0.0,
                coeffs_for_flags=coeffs,
            )
        except Exception as e:
            msg_loc = f"Geometry build error for {name_prefix}: {e}"
            _report('ERROR', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        if not verts:
            msg_loc = f"[WARN] {name_prefix}: no vertices after geometry build."
            _report('WARNING', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        safe_verts = []
        for v in verts:
            try:
                if len(v) >= 3:
                    x, y, z = v[0], v[1], v[2]
                elif len(v) == 2:
                    x, y = v[0], v[1]
                    z = 0.0
                else:
                    continue
                safe_verts.append((float(x), float(y), float(z)))
            except Exception:
                continue

        if not safe_verts:
            msg_loc = f"[WARN] {name_prefix}: no valid vertices after sanitization."
            _report('WARNING', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        max_idx = len(safe_verts) - 1
        safe_faces = []
        safe_face_coeffs = []
        safe_face_base_decs = []

        for idx_f, f in enumerate(faces):
            if len(f) != 3:
                continue
            i0, i1, i2 = f
            if (0 <= i0 <= max_idx and 0 <= i1 <= max_idx and 0 <= i2 <= max_idx):
                safe_faces.append((int(i0), int(i1), int(i2)))
                if idx_f < len(face_coeffs):
                    safe_face_coeffs.append(float(face_coeffs[idx_f]))
                else:
                    safe_face_coeffs.append(0.0)
                if 'face_base_decs' in locals() and idx_f < len(face_base_decs):
                    safe_face_base_decs.append(int(face_base_decs[idx_f]))
                else:
                    safe_face_base_decs.append(0)

        if not safe_faces:
            msg_loc = f"[WARN] {name_prefix}: no valid faces after sanitization."
            _report('WARNING', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        mesh_name = f"{name_prefix}_Mesh"

        obj = bpy.data.objects.get(name_prefix)
        if obj is None or obj.type != 'MESH':
            me = bpy.data.meshes.new(mesh_name)
            obj = bpy.data.objects.new(name_prefix, me)
            try:
                obj[_MANAGED_TAG] = True
                obj[_MANAGED_ROLE] = name_prefix
            except Exception:
                pass
            _ensure_linked(obj, flo_coll)
        else:
            _ensure_unique_obj_data(obj)
            me = obj.data
            try:
                me.clear_geometry()
            except Exception:
                me = bpy.data.meshes.new(mesh_name)
                obj.data = me

        bm = bmesh.new()
        bm_verts = [bm.verts.new(v) for v in safe_verts]
        bm.verts.ensure_lookup_table()

        # Manteniamo allineati coeff/colore con le facce effettivamente create
        kept_face_coeffs = []
        kept_face_colors = []
        kept_face_base_decs = []

        for idx_f, f in enumerate(safe_faces):
            try:
                bm.faces.new((bm_verts[f[0]], bm_verts[f[1]], bm_verts[f[2]]))
            except ValueError:
                continue
            kept_face_coeffs.append(float(safe_face_coeffs[idx_f]) if idx_f < len(safe_face_coeffs) else 0.0)
            kept_face_base_decs.append(int(safe_face_base_decs[idx_f]) if idx_f < len(safe_face_base_decs) else 0)
            kept_face_colors.append(face_colors[idx_f] if idx_f < len(face_colors) else (0.0, 0.0, 0.0, 1.0))

        bm.faces.ensure_lookup_table()

        # Merge-by-distance sempre ON: salda duplicati numerici (vertici coincidenti)
        try:
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=_MERGE_BY_DISTANCE_DIST)
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
        except Exception as e:
            print(f"[WARN] remove_doubles failed for {name_prefix}:", e)

        neighbor_info = None
        try:
            # Calcoliamo SEMPRE i layer (neighbors + coeff + base_dec) così gli Attr_ nodes
            # non dipendono dal Color Mode selezionato nel pannello.
            neighbor_info = compute_neighbor_counts_bmesh(bm, kept_face_coeffs, mode_id=str(color_mode_id))
        except Exception as e:
            print(f"[WARN] neighbor count compute failed for {name_prefix}:", e)
            # fallback minimale: niente neighbors, solo coeff_abs(_norm)
            coeffs_tmp = [float(c) for c in kept_face_coeffs]
            abs_c = [abs(c) for c in coeffs_tmp]
            max_abs = max(abs_c) if abs_c else 1.0
            if max_abs <= 1e-12:
                max_abs = 1.0
            abs_n = [v / max_abs for v in abs_c]
            neighbor_info = {
                'edges': [0] * len(coeffs_tmp),
                'verts': [0] * len(coeffs_tmp),
                'both':  [0] * len(coeffs_tmp),
                'coeff_abs': abs_c,
                'coeff_abs_norm': abs_n,
                'eps': 1e-12,
            }
        try:
            write_neighbor_bmesh_layers(bm, neighbor_info, kept_face_coeffs, face_base_decs=kept_face_base_decs)
        except Exception as e:
            print(f"[WARN] neighbor layer write failed for {name_prefix}:", e)

        bm.to_mesh(me)
        bm.free()

        mats = obj.data.materials
        mats.clear()
        if mat is not None:
            mats.append(mat)
        if mat_zero is not None:
            mats.append(mat_zero)

        try:
            face_attr = me.attributes.get("face_coeff")
            if face_attr is None:
                face_attr = me.attributes.new(name="face_coeff", type='FLOAT', domain='FACE')
            attr_data = face_attr.data
            for i, val in enumerate(kept_face_coeffs):
                if i < len(attr_data):
                    attr_data[i].value = float(val)
        except Exception as e:
            print(f"[WARN] Unable to write 'face_coeff' attribute for {name_prefix}:", e)

        if safe_faces and len(face_colors) >= len(safe_faces):
            color_layer = me.color_attributes.get("floretion_color")
            if color_layer is None:
                color_layer = me.color_attributes.new(
                    name="floretion_color",
                    type='FLOAT_COLOR',
                    domain='CORNER',
                )

            color_data = color_layer.data
            # NB: l'ordine di me.polygons corrisponde alle facce del bmesh convertito.
            for face_idx, poly in enumerate(me.polygons):
                coeff_face = kept_face_coeffs[face_idx] if face_idx < len(kept_face_coeffs) else 0.0
                if abs(coeff_face) < 1e-12 and len(obj.data.materials) >= 2:
                    poly.material_index = 1
                else:
                    poly.material_index = 0

                # --- colore ---
                if face_idx < len(kept_face_colors):
                    r, g, b, a = kept_face_colors[face_idx]
                else:
                    r, g, b, a = (0.0, 0.0, 0.0, 1.0)

                try:
                    mx = max(float(r), float(g), float(b), float(a))
                except Exception:
                    mx = 1.0

                # face_colors può arrivare in [0..255] oppure già [0..1]
                if mx > 1.5:
                    R = float(r) / 255.0
                    G = float(g) / 255.0
                    B = float(b) / 255.0
                    A = 1.0
                else:
                    R = float(r)
                    G = float(g)
                    B = float(b)
                    A = float(a) if a is not None else 1.0
                    if A <= 0.0:
                        A = 1.0

                li = poly.loop_start
                for j in range(poly.loop_total):
                    if (li + j) < len(color_data):
                        color_data[li + j].color = (R, G, B, A)

        obj.location.x = x_offset
        obj.location.y = 0.0

        solid = obj.modifiers.get("FloExtrude")
        if solid is None:
            solid = obj.modifiers.new("FloExtrude", 'SOLIDIFY')

        if extrusion_depth > 1e-9:
            solid.show_viewport = True
            solid.show_render = True
            solid.thickness = -float(extrusion_depth)
            solid.offset = 1.0
        else:
            solid.thickness = 0.0
            solid.show_viewport = False
            solid.show_render = False

        obj_cent = None
        if centroids:
            safe_cent_verts = []
            safe_cent_coeffs = []

            for i, c in enumerate(centroids):
                try:
                    if len(c) >= 3:
                        cx, cy, cz = c[0], c[1], c[2]
                    elif len(c) == 2:
                        cx, cy = c[0], c[1]
                        cz = 0.0
                    else:
                        continue
                    safe_cent_verts.append((float(cx), float(cy), float(cz)))
                    safe_cent_coeffs.append(float(coeffs[i]))
                except Exception:
                    continue

            if safe_cent_verts:
                cent_obj_name = f"{name_prefix}_cent"
                cent_mesh_name = f"{name_prefix}_cent_Mesh"

                obj_cent = bpy.data.objects.get(cent_obj_name)
                if obj_cent is None or obj_cent.type != 'MESH':
                    me2 = bpy.data.meshes.new(cent_mesh_name)
                    obj_cent = bpy.data.objects.new(cent_obj_name, me2)
                    try:
                        obj_cent[_MANAGED_TAG] = True
                        obj_cent[_MANAGED_ROLE] = cent_obj_name
                    except Exception:
                        pass
                    _ensure_linked(obj_cent, flo_coll)
                else:
                    _ensure_unique_obj_data(obj_cent)
                    me2 = obj_cent.data
                    try:
                        me2.clear_geometry()
                    except Exception:
                        me2 = bpy.data.meshes.new(cent_mesh_name)
                        obj_cent.data = me2

                me2.from_pydata(safe_cent_verts, [], [])
                me2.update()

                try:
                    coeff_attr = me2.attributes.get("coeff")
                    if coeff_attr is None:
                        coeff_attr = me2.attributes.new(name="coeff", type='FLOAT', domain='POINT')
                    attr_data = coeff_attr.data
                    for ii, val in enumerate(safe_cent_coeffs):
                        if ii < len(attr_data):
                            attr_data[ii].value = float(val)
                except Exception as e:
                    print(f"[WARN] Unable to write 'coeff' attribute for {name_prefix}_cent:", e)

                obj_cent.location.x = x_offset
                obj_cent.location.y = 0.0
                _parent_keep_world(obj_cent, obj)

                if mat is not None:
                    mats2 = obj_cent.data.materials
                    mats2.clear()
                    mats2.append(mat)

        obj_curve = None
        if centroids and len(centroids) >= 2:
            safe_curve_points = []
            for c in centroids:
                try:
                    if len(c) >= 3:
                        cx, cy, cz = c[0], c[1], c[2]
                    elif len(c) == 2:
                        cx, cy = c[0], c[1]
                        cz = 0.0
                    else:
                        continue
                    safe_curve_points.append((float(cx), float(cy), float(cz)))
                except Exception:
                    continue

            if len(safe_curve_points) >= 2:
                curve_obj_name = f"{name_prefix}_curve"
                curve_data_name = f"{name_prefix}_curve_Data"

                obj_curve = bpy.data.objects.get(curve_obj_name)
                if obj_curve is None or obj_curve.type != 'CURVE':
                    cu = bpy.data.curves.new(curve_data_name, 'CURVE')
                    cu.dimensions = '3D'
                    obj_curve = bpy.data.objects.new(curve_obj_name, cu)
                    try:
                        obj_curve[_MANAGED_TAG] = True
                        obj_curve[_MANAGED_ROLE] = curve_obj_name
                    except Exception:
                        pass
                    _ensure_linked(obj_curve, flo_coll)
                else:
                    _ensure_unique_obj_data(obj_curve)
                    cu = obj_curve.data
                    try:
                        while cu.splines:
                            cu.splines.remove(cu.splines[0])
                    except Exception:
                        pass

                spline = cu.splines.new('POLY')
                spline.points.add(len(safe_curve_points) - 1)
                for i, (cx, cy, cz) in enumerate(safe_curve_points):
                    spline.points[i].co = (cx, cy, cz, 1.0)

                obj_curve.location.x = x_offset
                obj_curve.location.y = 0.0
                _parent_keep_world(obj_curve, obj)

                if mat is not None:
                    mats3 = obj_curve.data.materials
                    mats3.clear()
                    mats3.append(mat)

        # Applica visibilità helper (checkbox pannello)
        try:
            show_cent = bool(getattr(props, 'show_centroids', False))
            show_curve = bool(getattr(props, 'show_curve', False))
            if obj_cent is not None:
                obj_cent.hide_viewport = (not show_cent)
                obj_cent.hide_render = (not show_cent)
            if obj_curve is not None:
                obj_curve.hide_viewport = (not show_curve)
                obj_curve.hide_render = (not show_curve)
        except Exception:
            pass

        return obj, obj_cent, obj_curve

    obj_x, obj_x_cent, obj_x_curve = build_object_from_floretion(flo_x, "Flo_X", -spacing, "X")
    obj_y, obj_y_cent, obj_y_curve = build_object_from_floretion(flo_y, "Flo_Y", 0.0, "Y")
    obj_z, obj_z_cent, obj_z_curve = build_object_from_floretion(flo_z, "Flo_XY", spacing, "X·Y")

    active_obj = (
        obj_y or obj_y_curve or obj_y_cent or
        obj_x or obj_x_curve or obj_x_cent or
        obj_z or obj_z_curve or obj_z_cent
    )
    if active_obj is not None:
        context.view_layer.objects.active = active_obj

    # --- Labels (viewport-only) + Manifest ---
    labels_enabled = bool(getattr(props, "include_labels", False))
    try:
        y_off = -max(1.0, float(spacing) * 0.35)
    except Exception:
        y_off = -2.0
    try:
        z_off = max(0.0, float(props.max_height)) + 0.25
    except Exception:
        z_off = 0.25

    lab_x = _ensure_viewport_label(
        collection=flo_coll,
        enabled=labels_enabled,
        name="Flo_Label_X",
        text="X",
        location=(-float(spacing), float(y_off), float(z_off)),
    )
    lab_y = _ensure_viewport_label(
        collection=flo_coll,
        enabled=labels_enabled,
        name="Flo_Label_Y",
        text="Y",
        location=(0.0, float(y_off), float(z_off)),
    )
    lab_xy = _ensure_viewport_label(
        collection=flo_coll,
        enabled=labels_enabled,
        name="Flo_Label_XY",
        text="X·Y",
        location=(float(spacing), float(y_off), float(z_off)),
    )

    # Tagga anche i mesh/cent/curve esistenti (nel caso fossero già presenti)
    for o in (obj_x, obj_x_cent, obj_x_curve, obj_y, obj_y_cent, obj_y_curve, obj_z, obj_z_cent, obj_z_curve):
        if o is None:
            continue
        try:
            o[_MANAGED_TAG] = True
        except Exception:
            pass

    # Aggiorna manifest: lista esatta degli oggetti che *devono* esistere dopo questo build
    scene = getattr(context, "scene", None) or bpy.context.scene
    manifest_objs = [o for o in (obj_x, obj_x_cent, obj_x_curve, obj_y, obj_y_cent, obj_y_curve, obj_z, obj_z_cent, obj_z_curve, lab_x, lab_y, lab_xy) if o is not None]
    _scene_manifest_set(scene, manifest_objs)

    return {'FINISHED'}


