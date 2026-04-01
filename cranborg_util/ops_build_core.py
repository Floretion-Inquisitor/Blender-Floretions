# cranborg_util/ops_build_core.py

from __future__ import annotations

import bpy
from bpy.types import Operator
import bmesh

import numpy as np
import math


# Merge-by-distance sempre ON: salda vertici coincidenti per ottenere adiacenze reali.
# Valore molto piccolo: unisce solo duplicati numerici, non 'chiude' gap dovuti a tile size/spacing.
_MERGE_BY_DISTANCE_DIST = 1e-6
from floretion import Floretion

from .ui_props import FloretionMeshSettings
from . import seeds
from .sampling import sample_floretion, tetra_coords_scaled_to_max_height

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
from .ops_vertex_groups import (
    ensure_nei_vertex_groups,
    sync_tetra_display_from_flat,
    uses_vg_material_assignment,
    assign_vg_materials_on_object,
    apply_vg_material_policy,
)

from .ops_spin_vg import refresh_spin_targets_if_present

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



def _vg_materials_allowed(props) -> bool:
    """I materiali VG legacy sono disattivati.

    Il display dell'add-on resta guidato dal materiale base/zero + attributi shader.
    Questo evita conflitti di priorità e tiene flat/tetra perfettamente allineati.
    """
    return False


def _purge_legacy_vg_materials() -> None:
    for i in range(1, 8):
        mat = bpy.data.materials.get(f"FloretionMaterial_VG{i}")
        if mat is not None and getattr(mat, "users", 0) == 0:
            try:
                bpy.data.materials.remove(mat)
            except Exception:
                pass


def _ensure_vg_material_bank(color_mode_id: str) -> list[bpy.types.Material]:
    """Compat legacy: non crea più materiali VG, ripulisce solo eventuali residui."""
    _purge_legacy_vg_materials()
    return []



def _strip_vg_material_assignment_local(obj: bpy.types.Object | None) -> None:
    """Rimuove i materiali VG dall'oggetto e ripristina una visualizzazione semplice:
    slot 0 = FloretionMaterial, slot 1 = FloretionCoeffZeroMaterial, poligoni
    assegnati in base al coefficiente di faccia. Questo evita che i materiali VG
    continuino a sovrascrivere static/quantile colors.
    """
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    me = getattr(obj, "data", None)
    if me is None:
        return

    base_mat = ensure_floretion_material()
    zero_mat = ensure_floretion_zero_material()

    mats = me.materials
    # porta i due slot canonici in testa
    if len(mats) == 0:
        mats.append(base_mat)
    else:
        mats[0] = base_mat
    if len(mats) < 2:
        mats.append(zero_mat)
    else:
        mats[1] = zero_mat

    coeff_attr = me.attributes.get("face_coeff") or me.attributes.get("coeff")
    for pi, poly in enumerate(me.polygons):
        coeff_val = 1.0
        if coeff_attr is not None:
            try:
                coeff_val = float(coeff_attr.data[pi].value)
            except Exception:
                coeff_val = 1.0
        poly.material_index = 1 if abs(coeff_val) <= 1.0e-12 else 0

    # rimuovi tutti gli slot extra/VG dalla coda
    try:
        for idx in range(len(mats) - 1, 1, -1):
            try:
                mats.pop(index=idx)
            except TypeError:
                # API vecchie
                try:
                    mats.pop(idx)
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass




def _orientation_sign_from_base_dec(base_dec: int) -> float:
    try:
        oct_str = format(int(base_dec), "o")
    except Exception:
        return 0.0

    count = sum(1 for d in str(oct_str) if d in "124")
    order = len(str(oct_str))
    if count % 2 == 0:
        return 1.0 if order % 2 == 0 else -1.0
    return -1.0 if order % 2 == 0 else 1.0


def _write_tile_orientation_attr(me: bpy.types.Mesh, face_base_decs) -> None:
    if me is None:
        return
    try:
        attr = me.attributes.get("tile_orientation_sign")
        if attr is None or attr.domain != 'FACE':
            if attr is not None:
                try:
                    me.attributes.remove(attr)
                except Exception:
                    pass
            attr = me.attributes.new(name="tile_orientation_sign", type='FLOAT', domain='FACE')
        data = attr.data
        for i in range(min(len(data), len(face_base_decs))):
            data[i].value = float(_orientation_sign_from_base_dec(face_base_decs[i]))
    except Exception as e:
        print(f"[WARN] Unable to write 'tile_orientation_sign' attribute for {getattr(me, 'name', '<mesh>')}: {e}")

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




def _effective_color_mode_from_props(props) -> str:
    fn = getattr(_color_adapter, "resolve_effective_color_mode", None)
    if callable(fn):
        try:
            return str(fn(props))
        except Exception:
            pass
    try:
        fam = str(getattr(props, "color_family", "") or "").strip().upper()
        if fam == "NEIGHBOR":
            return str(getattr(props, "neighbor_color_mode", "NEIGH_EDGE_SAT") or "NEIGH_EDGE_SAT")
        if fam == "QUANTILE":
            return str(getattr(props, "quantile_color_mode", "QUANTILE_8") or "QUANTILE_8")
        if fam == "STATIC":
            return str(getattr(props, "static_color_mode", "ABS_HSV") or "ABS_HSV")
    except Exception:
        pass
    return str(getattr(props, "color_mode", "ABS_HSV") or "ABS_HSV")
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
            color_mode_id=color_mode_id,
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



def _line_intersection(line_a, line_b):
    ax, ay, ad = line_a
    bx, by, bd = line_b
    det = (ax * by) - (ay * bx)
    if abs(det) <= 1e-12:
        return None
    x = ((ad * by) - (ay * bd)) / det
    y = ((ax * bd) - (ad * bx)) / det
    return (float(x), float(y))


def _triangle_area_from_lines(lines):
    if len(lines) != 3:
        return float("inf")
    p0 = _line_intersection(lines[0], lines[1])
    p1 = _line_intersection(lines[1], lines[2])
    p2 = _line_intersection(lines[2], lines[0])
    if p0 is None or p1 is None or p2 is None:
        return float("inf")
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    return abs(0.5 * (((x1 - x0) * (y2 - y0)) - ((x2 - x0) * (y1 - y0))))


def _candidate_support_lines(points2d, *, upward: bool):
    s3 = math.sqrt(3.0)
    if upward:
        normals = [
            (0.0, 1.0),
            (s3 * 0.5, -0.5),
            (-s3 * 0.5, -0.5),
        ]
    else:
        normals = [
            (0.0, -1.0),
            (s3 * 0.5, 0.5),
            (-s3 * 0.5, 0.5),
        ]

    lines = []
    for nx, ny in normals:
        d = max((nx * float(x) + ny * float(y)) for x, y in points2d)
        lines.append((float(nx), float(ny), float(d)))
    return lines


def _choose_support_lines(points2d):
    pts = [(float(x), float(y)) for x, y in points2d]
    if not pts:
        s3 = math.sqrt(3.0)
        return [(0.0, 1.0, 0.0), (s3 * 0.5, -0.5, 0.0), (-s3 * 0.5, -0.5, 0.0)]

    up = _candidate_support_lines(pts, upward=True)
    dn = _candidate_support_lines(pts, upward=False)
    return up if _triangle_area_from_lines(up) <= _triangle_area_from_lines(dn) else dn


def _reflect_point_xy(pt, line):
    x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
    nx, ny, d = line
    delta = ((nx * x) + (ny * y) - d)
    xr = x - (2.0 * delta * nx)
    yr = y - (2.0 * delta * ny)
    return (float(xr), float(yr), float(z))


def _round_v3(v, ndigits=6):
    return (
        round(float(v[0]), ndigits),
        round(float(v[1]), ndigits),
        round(float(v[2]), ndigits),
    )


def _extend_mesh_geometry(verts, faces, face_colors, face_coeffs, face_base_decs, *, level: int):
    level = max(0, int(level))
    if level <= 0 or not verts or not faces:
        return verts, faces, face_colors, face_coeffs, face_base_decs

    cur_verts = [tuple(map(float, v[:3])) for v in verts]
    cur_faces = [tuple(map(int, f[:3])) for f in faces]
    cur_colors = list(face_colors)
    cur_coeffs = [float(v) for v in face_coeffs]
    cur_base_decs = [int(v) for v in face_base_decs]

    for _step in range(level):
        lines = _choose_support_lines([(v[0], v[1]) for v in cur_verts])

        prev_verts = list(cur_verts)
        prev_faces = list(cur_faces)
        prev_colors = list(cur_colors)
        prev_coeffs = list(cur_coeffs)
        prev_base_decs = list(cur_base_decs)

        for line in lines:
            off = len(cur_verts)
            cur_verts.extend(_reflect_point_xy(v, line) for v in prev_verts)
            for fi, f in enumerate(prev_faces):
                cur_faces.append((off + f[0], off + f[2], off + f[1]))
                cur_colors.append(prev_colors[fi] if fi < len(prev_colors) else (0.0, 0.0, 0.0, 1.0))
                cur_coeffs.append(prev_coeffs[fi] if fi < len(prev_coeffs) else 0.0)
                cur_base_decs.append(prev_base_decs[fi] if fi < len(prev_base_decs) else 0)

    vmap = {}
    verts_out = []
    faces_out = []
    colors_out = []
    coeffs_out = []
    base_decs_out = []

    def _get_vidx(v):
        key = _round_v3(v, 6)
        idx = vmap.get(key)
        if idx is None:
            idx = len(verts_out)
            vmap[key] = idx
            verts_out.append((float(v[0]), float(v[1]), float(v[2])))
        return idx

    face_seen = set()
    for fi, f in enumerate(cur_faces):
        if len(f) != 3:
            continue
        try:
            a = _get_vidx(cur_verts[f[0]])
            b = _get_vidx(cur_verts[f[1]])
            c = _get_vidx(cur_verts[f[2]])
        except Exception:
            continue
        if a == b or b == c or a == c:
            continue
        key = tuple(sorted((a, b, c)))
        if key in face_seen:
            continue
        face_seen.add(key)
        faces_out.append((a, b, c))
        colors_out.append(cur_colors[fi] if fi < len(cur_colors) else (0.0, 0.0, 0.0, 1.0))
        coeffs_out.append(cur_coeffs[fi] if fi < len(cur_coeffs) else 0.0)
        base_decs_out.append(cur_base_decs[fi] if fi < len(cur_base_decs) else 0)

    return verts_out, faces_out, colors_out, coeffs_out, base_decs_out


def _extend_point_cloud(points, *value_lists, level: int):
    level = max(0, int(level))
    pts = [tuple(map(float, p[:3])) for p in points]
    values = [list(vs) for vs in value_lists]

    if level <= 0 or not pts:
        return (pts, *values)

    cur_pts = list(pts)
    cur_vals = [list(vs) for vs in values]

    for _step in range(level):
        lines = _choose_support_lines([(p[0], p[1]) for p in cur_pts])
        prev_pts = list(cur_pts)
        prev_vals = [list(vs) for vs in cur_vals]

        for line in lines:
            cur_pts.extend(_reflect_point_xy(p, line) for p in prev_pts)
            for arr_idx, arr in enumerate(cur_vals):
                arr.extend(prev_vals[arr_idx])

    seen = {}
    pts_out = []
    vals_out = [[] for _ in cur_vals]

    for i, p in enumerate(cur_pts):
        key = _round_v3(p, 6)
        if key in seen:
            continue
        seen[key] = len(pts_out)
        pts_out.append(p)
        for arr_idx, arr in enumerate(cur_vals):
            vals_out[arr_idx].append(arr[i] if i < len(arr) else 0)

    return (pts_out, *vals_out)


def _extend_polyline_groups(groups, *, level: int):
    level = max(0, int(level))
    out_groups = [[tuple(map(float, p[:3])) for p in grp] for grp in groups if grp]
    if level <= 0 or not out_groups:
        return out_groups

    for _step in range(level):
        flat = [p for grp in out_groups for p in grp]
        if not flat:
            break
        lines = _choose_support_lines([(p[0], p[1]) for p in flat])
        prev_groups = [list(grp) for grp in out_groups]
        for line in lines:
            for grp in prev_groups:
                out_groups.append([_reflect_point_xy(p, line) for p in grp])

    dedup = []
    seen = set()
    for grp in out_groups:
        if not grp:
            continue
        key = (len(grp), _round_v3(grp[0], 6), _round_v3(grp[-1], 6))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(grp)
    return dedup


def _face_centroids_from_geometry(verts, faces):
    out = []
    for f in faces:
        try:
            pts = [verts[int(idx)] for idx in f[:3]]
            if len(pts) < 3:
                out.append((0.0, 0.0, 0.0))
                continue
            cx = sum(float(p[0]) for p in pts) / 3.0
            cy = sum(float(p[1]) for p in pts) / 3.0
            cz = sum(float(p[2]) for p in pts) / 3.0
            out.append((cx, cy, cz))
        except Exception:
            out.append((0.0, 0.0, 0.0))
    return out


def _normalize_face_coeffs(face_coeffs) -> list[float]:
    vals = [float(c) for c in face_coeffs] if face_coeffs is not None else []
    if not vals:
        return []
    m = max(abs(v) for v in vals)
    if m <= 1.0e-12:
        return [0.0 for _ in vals]
    return [float(v) / float(m) for v in vals]


def _coeff_height_values(coeffs, *, scale_mode: str = "linear", normalize: bool = True, clip: float = 1.0) -> np.ndarray:
    arr = np.asarray(coeffs, dtype=float).copy()
    mode = str(scale_mode or "linear").strip().lower()

    if mode == "log":
        s = np.sign(arr)
        arr = s * np.log10(1.0 + np.abs(arr))

    if bool(normalize):
        m = float(np.max(np.abs(arr))) if arr.size else 1.0
        if m > 1.0e-12:
            arr = arr / m

    clip = max(1.0e-6, float(clip))
    arr = np.clip(arr, -clip, clip)
    return arr


def _explode_faces_to_unique_verts(verts, faces):
    if not verts or not faces:
        return [tuple(map(float, v[:3])) for v in verts], [tuple(map(int, f[:3])) for f in faces]

    new_verts = []
    new_faces = []
    for f in faces:
        if len(f) < 3:
            continue
        tri = []
        for idx in f[:3]:
            v = verts[int(idx)]
            new_verts.append((float(v[0]), float(v[1]), float(v[2])))
            tri.append(len(new_verts) - 1)
        new_faces.append(tuple(tri))
    return new_verts, new_faces


def _tile_area_value_from_coeff(coeff_norm: float, mode: str) -> float:
    mode = str(mode or "none").strip().lower()
    a = abs(float(coeff_norm))
    if mode == "coeff_abs":
        return a
    if mode == "coeff_log":
        # dopo la normalizzazione |coeff| <= 1, quindi log e linear coincidono per design
        return a
    return 1.0


def _apply_tile_area_scaling_to_geometry(
    verts,
    faces,
    face_coeffs,
    *,
    mode: str,
    min_side_scale: float = 1.0e-4,
):
    mode = str(mode or "none").strip().lower()
    if mode == "none" or not verts or not faces:
        return [tuple(map(float, v[:3])) for v in verts]

    coeff_norm = _normalize_face_coeffs(face_coeffs)
    out = [list(map(float, v[:3])) for v in verts]

    for fi, f in enumerate(faces):
        if len(f) < 3:
            continue
        try:
            i0, i1, i2 = int(f[0]), int(f[1]), int(f[2])
            coeff = float(coeff_norm[fi]) if fi < len(coeff_norm) else 1.0
        except Exception:
            continue

        pts = [out[i0], out[i1], out[i2]]
        cx = (pts[0][0] + pts[1][0] + pts[2][0]) / 3.0
        cy = (pts[0][1] + pts[1][1] + pts[2][1]) / 3.0
        cz = (pts[0][2] + pts[1][2] + pts[2][2]) / 3.0

        area_scale = _tile_area_value_from_coeff(coeff, mode)
        side_scale = math.sqrt(max(area_scale, 0.0))
        side_scale = max(float(side_scale), float(min_side_scale))

        for idx_v in (i0, i1, i2):
            vx, vy, vz = out[idx_v]
            out[idx_v][0] = cx + (vx - cx) * side_scale
            out[idx_v][1] = cy + (vy - cy) * side_scale
            out[idx_v][2] = cz + (vz - cz) * side_scale

    return [tuple(v) for v in out]


def _tetra_radial_value_from_coeff(coeff_norm: float, mode: str) -> float:
    mode = str(mode or "none").strip().lower()
    c = float(coeff_norm)
    a = abs(c)
    s = -1.0 if c < 0.0 else 1.0
    if mode == "coeff":
        return c
    if mode == "coeff_log":
        # dopo normalizzazione |c| <= 1, quindi log e linear coincidono per design
        return s * a
    return 1.0


def _apply_tetra_coeff_radial_shift_to_geometry(
    verts,
    faces,
    face_coeffs,
    *,
    center=(0.0, 0.0, 0.0),
    mode: str = "coeff",
    amount: float = 1.0,
):
    if not verts or not faces:
        return [tuple(map(float, v[:3])) for v in verts]

    mode = str(mode or "none").strip().lower()
    amount = max(0.0, min(1.0, float(amount)))
    if mode == "none" or amount <= 1.0e-12:
        return [tuple(map(float, v[:3])) for v in verts]

    coeff_norm = _normalize_face_coeffs(face_coeffs)
    cx0, cy0, cz0 = map(float, center[:3])
    out = [list(map(float, v[:3])) for v in verts]

    for fi, f in enumerate(faces):
        if len(f) < 3:
            continue
        try:
            i0, i1, i2 = int(f[0]), int(f[1]), int(f[2])
            coeff = float(coeff_norm[fi]) if fi < len(coeff_norm) else 1.0
        except Exception:
            continue

        pts = [out[i0], out[i1], out[i2]]
        cx = (pts[0][0] + pts[1][0] + pts[2][0]) / 3.0
        cy = (pts[0][1] + pts[1][1] + pts[2][1]) / 3.0
        cz = (pts[0][2] + pts[1][2] + pts[2][2]) / 3.0

        coeff_scale = _tetra_radial_value_from_coeff(coeff, mode)
        blended_scale = (1.0 - amount) + amount * coeff_scale

        nx = cx0 + (cx - cx0) * blended_scale
        ny = cy0 + (cy - cy0) * blended_scale
        nz = cz0 + (cz - cz0) * blended_scale

        dx = nx - cx
        dy = ny - cy
        dz = nz - cz

        for idx_v in (i0, i1, i2):
            out[idx_v][0] += dx
            out[idx_v][1] += dy
            out[idx_v][2] += dz

    return [tuple(v) for v in out]



def _tetra_radial_value_from_coeff(coeff_norm: float, mode: str) -> float:
    mode = str(mode or "none").strip().lower()
    c = float(coeff_norm)
    a = abs(c)
    s = -1.0 if c < 0.0 else 1.0
    if mode == "coeff":
        return c
    if mode == "coeff_log":
        # dopo normalizzazione |c| <= 1: log e linear coincidono volutamente
        return s * a
    return 1.0


def _apply_tetra_coeff_radial_shift_to_geometry(
    verts,
    faces,
    face_coeffs,
    *,
    center=(0.0, 0.0, 0.0),
    mode: str = "coeff",
    amount: float = 1.0,
):
    if not verts or not faces:
        return [tuple(map(float, v[:3])) for v in verts]

    mode = str(mode or "none").strip().lower()
    amount = max(0.0, min(1.0, float(amount)))
    if mode == "none" or amount <= 1.0e-12:
        return [tuple(map(float, v[:3])) for v in verts]

    coeff_norm = _normalize_face_coeffs(face_coeffs)
    cx0, cy0, cz0 = map(float, center[:3])
    out = [list(map(float, v[:3])) for v in verts]

    for fi, f in enumerate(faces):
        if len(f) < 3:
            continue
        try:
            i0, i1, i2 = int(f[0]), int(f[1]), int(f[2])
            coeff = float(coeff_norm[fi]) if fi < len(coeff_norm) else 1.0
        except Exception:
            continue

        pts = [out[i0], out[i1], out[i2]]
        cx = (pts[0][0] + pts[1][0] + pts[2][0]) / 3.0
        cy = (pts[0][1] + pts[1][1] + pts[2][1]) / 3.0
        cz = (pts[0][2] + pts[1][2] + pts[2][2]) / 3.0

        coeff_scale = _tetra_radial_value_from_coeff(coeff, mode)
        blended_scale = (1.0 - amount) + amount * coeff_scale

        nx = cx0 + (cx - cx0) * blended_scale
        ny = cy0 + (cy - cy0) * blended_scale
        nz = cz0 + (cz - cz0) * blended_scale

        dx = nx - cx
        dy = ny - cy
        dz = nz - cz

        for idx_v in (i0, i1, i2):
            out[idx_v][0] += dx
            out[idx_v][1] += dy
            out[idx_v][2] += dz

    return [tuple(v) for v in out]


def _build_tetrahedral_geometry(

    samples,
    colors_bgr,
    brightness,
    *,
    max_height: float,
    coeffs_for_flags,
    tri_size: float = 1.0,
    global_scale: float = 1.0,
):
    tetra_coords = tetra_coords_scaled_to_max_height(samples.get("coords_tetra_raw"), max_height)
    tetra_samples = dict(samples)
    tetra_samples["coords"] = tetra_coords

    return build_geometry(
        tetra_samples,
        colors_bgr,
        brightness,
        global_scale=global_scale,
        tri_size=tri_size,
        z_mode="FLAT",
        z_coeff_scale=0.0,
        plot_mode="TRIANGLES",
        extrusion_depth=0.0,
        coeffs_for_flags=coeffs_for_flags,
    )


def _extend_tetrahedral_geometry(
    samples,
    colors_bgr,
    brightness,
    *,
    max_height: float,
    coeffs_for_flags,
    level: int,
    tri_size: float = 1.0,
    global_scale: float = 1.0,
):
    level = max(0, int(level))

    planar_samples = dict(samples)
    planar_samples["coords"] = np.asarray(samples["coords"], dtype=float)

    planar_verts, planar_faces, planar_face_colors, _planar_centroids, planar_face_coeffs, planar_face_base_decs = build_geometry(
        planar_samples,
        colors_bgr,
        brightness,
        global_scale=global_scale,
        tri_size=tri_size,
        z_mode="FLAT",
        z_coeff_scale=0.0,
        plot_mode="TRIANGLES",
        extrusion_depth=0.0,
        coeffs_for_flags=coeffs_for_flags,
    )

    ext_planar_verts, ext_planar_faces, ext_planar_face_colors, ext_planar_face_coeffs, ext_planar_face_base_decs = _extend_mesh_geometry(
        planar_verts,
        planar_faces,
        planar_face_colors,
        planar_face_coeffs,
        planar_face_base_decs,
        level=level,
    )

    tetra_verts, tetra_faces, tetra_face_colors, tetra_centroids, tetra_face_coeffs, tetra_face_base_decs = _build_tetrahedral_geometry(
        samples,
        colors_bgr,
        brightness,
        max_height=max_height,
        coeffs_for_flags=coeffs_for_flags,
        tri_size=tri_size,
        global_scale=global_scale,
    )

    canonical_planar_centroids = _face_centroids_from_geometry(planar_verts, planar_faces)
    canonical_tetra_centroids = tetra_centroids if tetra_centroids else _face_centroids_from_geometry(tetra_verts, tetra_faces)

    by_base_dec = {}
    for fi, base_dec in enumerate(tetra_face_base_decs):
        if fi >= len(tetra_faces) or fi >= len(canonical_tetra_centroids):
            continue
        base_dec = int(base_dec)
        if base_dec in by_base_dec:
            continue

        try:
            face = tetra_faces[fi]
            tet_cent = canonical_tetra_centroids[fi]
            plan_cent = canonical_planar_centroids[fi]
            local_offsets = []
            for vidx in face[:3]:
                vx, vy, vz = tetra_verts[int(vidx)]
                local_offsets.append((
                    float(vx) - float(tet_cent[0]),
                    float(vy) - float(tet_cent[1]),
                    float(vz) - float(tet_cent[2]),
                ))
            by_base_dec[base_dec] = {
                "plan_centroid": (float(plan_cent[0]), float(plan_cent[1]), float(plan_cent[2])),
                "tet_centroid": (float(tet_cent[0]), float(tet_cent[1]), float(tet_cent[2])),
                "local_offsets": local_offsets,
            }
        except Exception:
            continue

    out_verts = []
    out_faces = []
    out_colors = []
    out_centroids = []
    out_coeffs = []
    out_base_decs = []

    ext_planar_centroids = _face_centroids_from_geometry(ext_planar_verts, ext_planar_faces)

    for fi, face in enumerate(ext_planar_faces):
        try:
            base_dec = int(ext_planar_face_base_decs[fi]) if fi < len(ext_planar_face_base_decs) else 0
            ref = by_base_dec.get(base_dec)
            if ref is None:
                continue

            plan_ref = ref["plan_centroid"]
            tet_ref = ref["tet_centroid"]
            ext_cent = ext_planar_centroids[fi]

            dx = float(ext_cent[0]) - float(plan_ref[0])
            dy = float(ext_cent[1]) - float(plan_ref[1])

            new_cent = (
                float(tet_ref[0]) + dx,
                float(tet_ref[1]) + dy,
                float(tet_ref[2]),
            )

            i0 = len(out_verts)
            for off in ref["local_offsets"]:
                out_verts.append((
                    float(new_cent[0]) + float(off[0]),
                    float(new_cent[1]) + float(off[1]),
                    float(new_cent[2]) + float(off[2]),
                ))
            out_faces.append((i0, i0 + 1, i0 + 2))
            out_colors.append(ext_planar_face_colors[fi] if fi < len(ext_planar_face_colors) else (0.0, 0.0, 0.0, 1.0))
            out_centroids.append(new_cent)
            out_coeffs.append(float(ext_planar_face_coeffs[fi]) if fi < len(ext_planar_face_coeffs) else 0.0)
            out_base_decs.append(base_dec)
        except Exception:
            continue

    return out_verts, out_faces, out_colors, out_centroids, out_coeffs, out_base_decs


def _bundle_half_width(*objs):
    min_x = None
    max_x = None
    for obj in objs:
        if obj is None:
            continue
        try:
            from mathutils import Vector
            bb = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        except Exception:
            try:
                bb = [obj.matrix_world.translation]
            except Exception:
                bb = []
        for v in bb:
            x = float(v[0])
            min_x = x if min_x is None else min(min_x, x)
            max_x = x if max_x is None else max(max_x, x)
    if min_x is None or max_x is None:
        return 0.5
    return max(0.5, 0.5 * (max_x - min_x))


def _set_bundle_xy(obj, x_pos: float, y_pos: float = 0.0):
    if obj is None:
        return
    try:
        obj.location.x = float(x_pos)
        obj.location.y = float(y_pos)
    except Exception:
        pass


def _delete_object_if_exists(name: str) -> None:
    obj = bpy.data.objects.get(str(name))
    if obj is None:
        return

    data = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        return

    try:
        if data is not None and getattr(data, "users", 0) == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
    except Exception:
        pass






_SHADER_ONLY_COLOR_MODES = {'NEIGH_EDGE_HUE', 'NEIGH_VERT_HUE', 'NEIGH_EDGE_SAT', 'QUANTILE_2', 'QUANTILE_4', 'QUANTILE_8'}



def _mask_bin_offsets_from_props(props) -> list[float]:
    vals = []
    for nm in ("mask_bin_0", "mask_bin_1", "mask_bin_2", "mask_bin_3", "mask_bin_4", "mask_bin_5", "mask_bin_6", "mask_bin_7p"):
        try:
            vals.append(float(getattr(props, nm, 0.0)))
        except Exception:
            vals.append(0.0)
    while len(vals) < 8:
        vals.append(0.0)
    return vals[:8]


def _mask_bin_wall_flags_from_props(props) -> list[bool]:
    vals = []
    for nm in ("vg_wall_0", "vg_wall_1", "vg_wall_2", "vg_wall_3", "vg_wall_4", "vg_wall_5", "vg_wall_6", "vg_wall_7p"):
        try:
            vals.append(bool(getattr(props, nm, False)))
        except Exception:
            vals.append(False)
    while len(vals) < 8:
        vals.append(False)
    return vals[:8]



def _apply_vg_extrusions_to_mesh(me: bpy.types.Mesh, face_bins: list[int], offsets: list[float], wall_flags: list[bool]) -> None:
    """Applica l'estrusione per bucket VG direttamente alla mesh.

    Modalità:
      - walls=False: il tile viene spostato lasciando un foro.
        Manteniamo un bordo "wire" duplicando gli edge di contorno originali.
      - walls=True:  il tile viene estruso creando pareti laterali.
    """
    if me is None or not offsets or not any(abs(float(v)) > 1e-9 for v in offsets):
        return

    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        def _bin_of_face_index(fi: int) -> int:
            try:
                b = int(round(float(face_bins[fi])))
            except Exception:
                b = 0
            return max(0, min(7, b))

        # 1) bucket con pareti
        faces_snapshot = list(bm.faces)
        for b in range(min(8, len(offsets))):
            dz = float(offsets[b])
            if abs(dz) <= 1e-12 or not (b < len(wall_flags) and wall_flags[b]):
                continue

            region_faces = []
            for fi, f in enumerate(faces_snapshot):
                if fi >= len(face_bins):
                    continue
                if _bin_of_face_index(fi) == b and f.is_valid:
                    region_faces.append(f)

            if not region_faces:
                continue

            try:
                ret = bmesh.ops.extrude_face_region(bm, geom=region_faces)
                geom_ex = ret.get("geom", [])
                verts_ex = [g for g in geom_ex if isinstance(g, bmesh.types.BMVert)]
                if verts_ex:
                    bmesh.ops.translate(bm, verts=verts_ex, vec=(0.0, 0.0, dz))
            except Exception as e:
                print(f"[WARN] wall extrusion failed for VG {b}: {e}")

        # 2) bucket senza pareti: duplicate faces upward/downward, delete originals,
        #    keep duplicated boundary edges in place to preserve the hole outline.
        faces_snapshot = list(bm.faces)
        for b in range(min(8, len(offsets))):
            dz = float(offsets[b])
            if abs(dz) <= 1e-12 or (b < len(wall_flags) and wall_flags[b]):
                continue

            region_faces = []
            for fi, f in enumerate(faces_snapshot):
                if fi >= len(face_bins):
                    continue
                if _bin_of_face_index(fi) == b and f.is_valid:
                    region_faces.append(f)

            if not region_faces:
                continue

            try:
                region_set = set(region_faces)
                boundary_edges = []
                boundary_geom = []
                for f in region_faces:
                    for e in f.edges:
                        linked_other = [lf for lf in e.link_faces if lf not in region_set]
                        if linked_other or len(e.link_faces) <= 1:
                            boundary_edges.append(e)
                            boundary_geom.append(e)
                            boundary_geom.extend(list(e.verts))

                # bordo fermo attorno al foro
                if boundary_geom:
                    try:
                        bmesh.ops.duplicate(bm, geom=list(dict.fromkeys(boundary_geom)))
                    except Exception:
                        pass

                # facce duplicate che vengono spostate
                geom = list(region_faces)
                for f in region_faces:
                    geom.extend(list(f.edges))
                    geom.extend(list(f.verts))
                ret = bmesh.ops.duplicate(bm, geom=list(dict.fromkeys(geom)))
                geom_dup = ret.get("geom", [])
                dup_verts = [g for g in geom_dup if isinstance(g, bmesh.types.BMVert)]
                valid_orig = [f for f in region_faces if f.is_valid]

                if dup_verts:
                    bmesh.ops.translate(bm, verts=dup_verts, vec=(0.0, 0.0, dz))
                if valid_orig:
                    bmesh.ops.delete(bm, geom=valid_orig, context='FACES')

            except Exception as e:
                print(f"[WARN] hole extrusion failed for VG {b}: {e}")

        bm.to_mesh(me)
        me.update()
    finally:
        try:
            bm.free()
        except Exception:
            pass


def _bgr_to_rgba01(color_bgr, brightness01: float):
    try:
        b, g, r = color_bgr
    except Exception:
        return (0.0, 0.0, 0.0, 1.0)
    r01 = min(1.0, max(0.0, (float(r) / 255.0) * float(brightness01)))
    g01 = min(1.0, max(0.0, (float(g) / 255.0) * float(brightness01)))
    b01 = min(1.0, max(0.0, (float(b) / 255.0) * float(brightness01)))
    return (r01, g01, b01, 1.0)


def _set_material_emission_strength(mat: bpy.types.Material | None, strength: float) -> None:
    """Legacy no-op.

    L'intensità viene già pilotata dal materiale/shader editor in base ai coeffs.
    Lasciamo la funzione per compatibilità, ma non forziamo più alcun reset.
    """
    return


def _assign_material_slots(obj: bpy.types.Object | None, mat: bpy.types.Material | None, mat_zero: bpy.types.Material | None = None) -> None:
    if obj is None or getattr(obj, "data", None) is None or not hasattr(obj.data, "materials"):
        return
    mats = obj.data.materials
    try:
        mats.clear()
    except Exception:
        pass
    if mat is not None:
        try:
            mats.append(mat)
        except Exception:
            pass
    if mat_zero is not None:
        try:
            mats.append(mat_zero)
        except Exception:
            pass


def _recolor_mesh_object_from_floretion(
    obj: bpy.types.Object | None,
    flo: Floretion,
    *,
    props,
    color_mode_id: str,
    max_val_config: float,
    auto_clip_pct: float,
    gamma: float,
    sat_dist_weight: float,
    neg_policy_id: str,
) -> bool:
    if obj is None or getattr(obj, "type", None) != 'MESH' or getattr(obj, "data", None) is None:
        return False

    ignore_zero = not bool(getattr(props, "full_grid", False))
    samples = sample_floretion(flo, ignore_zero=ignore_zero)
    coeffs = samples["coeffs"]
    if len(coeffs) == 0:
        return False

    n = len(coeffs)
    indices = samples["indices"].astype(float)
    dists = samples["dists"]
    base_decs = samples.get("base_decs")

    basevec_at_pct = indices / (n - 1.0) if n > 1 else np.zeros_like(indices)
    max_dist = float(dists.max()) if np.any(dists) else 1.0
    dist_norm = dists / max(max_dist, 1e-12)

    colors_bgr, brightness = compute_colors(
        coeffs=coeffs,
        basevec_at_pct=basevec_at_pct,
        dist_norm=dist_norm,
        color_mode_id=color_mode_id,
        max_val_config=max_val_config,
        auto_clip_pct=auto_clip_pct,
        gamma=gamma,
        sat_dist_weight=sat_dist_weight,
        neg_policy_id=neg_policy_id,
    )

    color_by_base = {}
    if base_decs is not None:
        for i, bd in enumerate(base_decs):
            try:
                color_by_base[int(bd)] = _bgr_to_rgba01(colors_bgr[i], brightness[i])
            except Exception:
                pass

    me = obj.data
    color_layer = me.color_attributes.get("floretion_color")
    if color_layer is None:
        try:
            color_layer = me.color_attributes.new(name="floretion_color", type='FLOAT_COLOR', domain='CORNER')
        except Exception:
            return False

    base_attr = me.attributes.get("base_dec")
    coeff_attr = me.attributes.get("face_coeff")
    color_data = color_layer.data

    for poly in me.polygons:
        bd = None
        if base_attr is not None and poly.index < len(base_attr.data):
            try:
                bd = int(base_attr.data[poly.index].value)
            except Exception:
                bd = None
        rgba = color_by_base.get(int(bd)) if bd is not None else None
        if rgba is None:
            rgba = (0.0, 0.0, 0.0, 1.0)

        coeff_face = 0.0
        if coeff_attr is not None and poly.index < len(coeff_attr.data):
            try:
                coeff_face = float(coeff_attr.data[poly.index].value)
            except Exception:
                coeff_face = 0.0

        # Il recolor standard non deve cambiare priorità materiali per-bin.
        # Manteniamo solo la distinzione base/zero; il colore arriva dallo shader
        # e dagli attributi della mesh, non dallo slot materiale.
        if abs(coeff_face) < 1e-12 and len(me.materials) >= 2:
            poly.material_index = 1
        else:
            poly.material_index = 0

        li = poly.loop_start
        for j in range(poly.loop_total):
            idx = li + j
            if idx < len(color_data):
                color_data[idx].color = rgba

    try:
        me.update()
    except Exception:
        pass
    return True


def refresh_colors_from_cache(context, props) -> bool:
    """Aggiorna colori/materiali senza ricostruire mesh/extend/helper."""
    from .ops_build_cache import _cache_get, _cache_matches_props

    try:
        order = max(1, int(props.typical_order))
    except Exception:
        order = 1

    if not _cache_matches_props(props, order):
        return False

    c = _cache_get()
    if not c:
        return False

    flo_x = c.get("flo_x")
    flo_y = c.get("flo_y")
    flo_z = c.get("flo_z")
    if flo_x is None or flo_y is None or flo_z is None:
        return False

    color_mode_id = _effective_color_mode_from_props(props)

    auto_clip_pct = 99.0
    gamma = 0.6
    sat_dist_weight = 0.5
    neg_policy_id = "HUE_180"
    max_val_config = -1.0

    mat = ensure_floretion_material()
    mat_zero = ensure_floretion_zero_material()
    allow_vg_display = _vg_materials_allowed(props)

    try:
        ensure_neighbor_color_nodes(mat)
        set_neighbor_color_mode(mat, color_mode_id)
    except Exception:
        pass

    _ensure_vg_material_bank(color_mode_id)

    for obj_name in ("Flo_X", "Flo_Y", "Flo_XY", "Flo_X_cent", "Flo_Y_cent", "Flo_XY_cent", "Flo_X_curve", "Flo_Y_curve", "Flo_XY_curve"):
        _assign_material_slots(bpy.data.objects.get(obj_name), mat, mat_zero if obj_name in ("Flo_X", "Flo_Y", "Flo_XY") else None)

    if not allow_vg_display:
        for obj_name in ("Flo_X", "Flo_Y", "Flo_XY", "Flo_X_tetra", "Flo_Y_tetra", "Flo_XY_tetra"):
            try:
                _strip_vg_material_assignment_local(bpy.data.objects.get(obj_name))
            except Exception:
                pass

    if color_mode_id in _SHADER_ONLY_COLOR_MODES:
        try:
            sync_tetra_display_from_flat(context, target="ALL")
        except Exception:
            pass
        return True

    ok = False
    ok = _recolor_mesh_object_from_floretion(bpy.data.objects.get("Flo_X"), flo_x, props=props, color_mode_id=color_mode_id, max_val_config=max_val_config, auto_clip_pct=auto_clip_pct, gamma=gamma, sat_dist_weight=sat_dist_weight, neg_policy_id=neg_policy_id) or ok
    ok = _recolor_mesh_object_from_floretion(bpy.data.objects.get("Flo_Y"), flo_y, props=props, color_mode_id=color_mode_id, max_val_config=max_val_config, auto_clip_pct=auto_clip_pct, gamma=gamma, sat_dist_weight=sat_dist_weight, neg_policy_id=neg_policy_id) or ok
    ok = _recolor_mesh_object_from_floretion(bpy.data.objects.get("Flo_XY"), flo_z, props=props, color_mode_id=color_mode_id, max_val_config=max_val_config, auto_clip_pct=auto_clip_pct, gamma=gamma, sat_dist_weight=sat_dist_weight, neg_policy_id=neg_policy_id) or ok

    try:
        sync_tetra_display_from_flat(context, target="ALL")
    except Exception:
        pass

    return ok

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

    color_mode_id = _effective_color_mode_from_props(props)

    auto_clip_pct = 99.0
    gamma = 0.6
    sat_dist_weight = 0.5
    neg_policy_id = "HUE_180"
    max_val_config = -1.0  # <0 => usa clip automatico

    try:
        extend_level = max(1, int(getattr(props, "extend_level", "1") or "1"))
    except Exception:
        extend_level = 1

    extend_mesh = bool(getattr(props, "extend_mesh", False))
    extend_cent = bool(getattr(props, "extend_cent", False))
    extend_curve = bool(getattr(props, "extend_curve", False))
    mask_bin_offsets = _mask_bin_offsets_from_props(props)
    mask_bin_walls = _mask_bin_wall_flags_from_props(props)

    mat = ensure_floretion_material()
    try:
        ensure_neighbor_color_nodes(mat)
        set_neighbor_color_mode(mat, str(color_mode_id))
    except Exception:
        pass
    mat_zero = ensure_floretion_zero_material()
    allow_vg_display = _vg_materials_allowed(props)
    _ensure_vg_material_bank(str(color_mode_id))


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

    def build_object_from_floretion(
        flo,
        name_prefix: str,
        x_offset: float,
        label_text: str,
        *,
        forced_height_mode: str | None = None,
        forced_tetra: bool | None = None,
    ):
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

        # I materiali VG esistono come datablock di supporto, ma non vengono
        # auto-assegnati agli slot dell'oggetto per evitare conflitti di priorità.
        vg_mats_local = []

        n = len(coeffs)
        indices = samples["indices"].astype(float)
        dists = samples["dists"]

        basevec_at_pct = indices / (n - 1.0) if n > 1 else np.zeros_like(indices)
        max_dist = float(dists.max()) if np.any(dists) else 1.0
        dist_norm = dists / max(max_dist, 1e-12)

        try:
            base_color_mode_id = color_mode_id
            if str(color_mode_id) in _SHADER_ONLY_COLOR_MODES:
                # placeholder per la geometria: poi sovrascriviamo i colori con i neighbors
                base_color_mode_id = 'ABS_HSV'
            colors_bgr, brightness = compute_colors(
                coeffs=coeffs,
                basevec_at_pct=basevec_at_pct,
                dist_norm=dist_norm,
                color_mode_id=color_mode_id,
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

        local_height_mode = str(forced_height_mode or props.height_mode or "flat")
        samples_for_geom = dict(samples)
        tetra_mode = bool(getattr(props, "use_tetrahedral", False)) if forced_tetra is None else bool(forced_tetra)
        centroids_already_extended = False

        if local_height_mode == "index":
            idx_norm = np.linspace(-1.0, 1.0, n, dtype=float)
            samples_for_geom["coeffs"] = idx_norm
        elif local_height_mode == "coeff":
            coeff_height_mode = str(getattr(props, "coeff_height_scale_mode", "linear") or "linear")
            try:
                coeff_height_clip = float(getattr(props, "coeff_height_clip", 1.0) or 1.0)
            except Exception:
                coeff_height_clip = 1.0
            samples_for_geom["coeffs"] = _coeff_height_values(
                coeffs,
                scale_mode=coeff_height_mode,
                normalize=True,
                clip=coeff_height_clip,
            )

        try:
            if tetra_mode:
                if extend_mesh:
                    verts, faces, face_colors, centroids, face_coeffs, face_base_decs = _extend_tetrahedral_geometry(
                        samples_for_geom,
                        colors_bgr,
                        brightness,
                        max_height=max_h,
                        coeffs_for_flags=coeffs,
                        level=extend_level,
                        tri_size=1.0,
                        global_scale=1.0,
                    )
                    centroids_already_extended = True
                else:
                    tetra_coords = tetra_coords_scaled_to_max_height(samples_for_geom.get("coords_tetra_raw"), max_h)
                    samples_for_geom["coords"] = tetra_coords
                    verts, faces, face_colors, centroids, face_coeffs, face_base_decs = build_geometry(
                        samples_for_geom,
                        colors_bgr,
                        brightness,
                        global_scale=1.0,
                        tri_size=1.0,
                        z_mode="FLAT",
                        z_coeff_scale=0.0,
                        plot_mode="TRIANGLES",
                        extrusion_depth=0.0,
                        coeffs_for_flags=coeffs,
                    )
            else:
                local_z_mode = "FLAT"
                local_z_coeff_scale = 0.0
                if local_height_mode == "coeff":
                    local_z_mode = "COEFF_SIGNED"
                    local_z_coeff_scale = max_h
                elif local_height_mode == "index":
                    local_z_mode = "COEFF_SIGNED"
                    local_z_coeff_scale = max_h

                verts, faces, face_colors, centroids, face_coeffs, face_base_decs = build_geometry(
                    samples_for_geom,
                    colors_bgr,
                    brightness,
                    global_scale=1.0,
                    tri_size=1.0,
                    z_mode=local_z_mode,
                    z_coeff_scale=local_z_coeff_scale,
                    plot_mode="TRIANGLES",
                    extrusion_depth=0.0,
                    coeffs_for_flags=coeffs,
                )
        except Exception as e:
            msg_loc = f"Geometry build error for {name_prefix}: {e}"
            _report('ERROR', msg_loc)
            props.log_message = msg_loc
            return None, None, None

        if local_height_mode == "coeff":
            try:
                face_coeffs = [float(v) for v in coeffs]
            except Exception:
                pass

        if extend_mesh and not tetra_mode:
            try:
                verts, faces, face_colors, face_coeffs, face_base_decs = _extend_mesh_geometry(
                    verts,
                    faces,
                    face_colors,
                    face_coeffs,
                    face_base_decs,
                    level=extend_level,
                )
            except Exception as e:
                print(f"[WARN] extend mesh failed for {name_prefix}: {e}")

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

        if (not tetra_mode) and local_height_mode == "coeff":
            try:
                coeff_height_clip = float(getattr(props, "coeff_height_clip", 1.0) or 1.0)
            except Exception:
                coeff_height_clip = 1.0
            z_lim = max(1.0e-6, float(max_h) * max(1.0e-6, coeff_height_clip))
            safe_verts = [
                (float(x), float(y), max(-z_lim, min(z_lim, float(z))))
                for (x, y, z) in safe_verts
            ]

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

        canonical_safe_verts = [tuple(map(float, v[:3])) for v in safe_verts]
        display_safe_verts = list(canonical_safe_verts)
        display_faces = list(safe_faces)

        area_scale_mode = str(getattr(props, "tile_area_scaling_mode", "none") or "none").strip().lower()
        tetra_radial_mode = str(getattr(props, "tetra_coeff_radial_mode", "coeff") or "coeff").strip().lower()
        tetra_radial_amount = float(getattr(props, "tetra_coeff_radial_amount", 1.0) or 0.0)
        scaling_changes_display = False

        if (
            (tetra_mode and local_height_mode == "coeff" and tetra_radial_mode != "none" and tetra_radial_amount > 1.0e-12)
            or area_scale_mode != "none"
        ):
            # Display mesh only: explode faces so every tile keeps its own equilateral shape
            # and per-face scaling/translation can never deform shared vertices.
            display_safe_verts, display_faces = _explode_faces_to_unique_verts(display_safe_verts, safe_faces)
            scaling_changes_display = True

        if tetra_mode and local_height_mode == "coeff" and tetra_radial_mode != "none" and tetra_radial_amount > 1.0e-12:
            display_safe_verts = _apply_tetra_coeff_radial_shift_to_geometry(
                display_safe_verts,
                display_faces,
                safe_face_coeffs,
                center=(0.0, 0.0, 0.0),
                mode=tetra_radial_mode,
                amount=tetra_radial_amount,
            )

        if area_scale_mode != "none":
            # Vale sia per la mesh flat sia per la mesh tetra:
            # il ridimensionamento area è una trasformazione di display per-tile
            # applicata alla geometria finale prima di scrivere il BMesh.
            display_safe_verts = _apply_tile_area_scaling_to_geometry(
                display_safe_verts,
                display_faces,
                safe_face_coeffs,
                mode=area_scale_mode,
            )

        mesh_name = f"{name_prefix}_Mesh"

        obj = bpy.data.objects.get(name_prefix)
        had_vg_display = (uses_vg_material_assignment(obj) if (obj is not None and not tetra_mode and allow_vg_display) else False)
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
        bm_verts = [bm.verts.new(v) for v in display_safe_verts]
        bm.verts.ensure_lookup_table()

        # Manteniamo allineati coeff/colore con le facce effettivamente create
        kept_face_coeffs = []
        kept_face_colors = []
        kept_face_base_decs = []
        kept_source_face_indices = []

        for idx_f, f in enumerate(display_faces):
            try:
                bm.faces.new((bm_verts[f[0]], bm_verts[f[1]], bm_verts[f[2]]))
            except ValueError:
                continue
            kept_source_face_indices.append(int(idx_f))
            kept_face_coeffs.append(float(safe_face_coeffs[idx_f]) if idx_f < len(safe_face_coeffs) else 0.0)
            kept_face_base_decs.append(int(safe_face_base_decs[idx_f]) if idx_f < len(safe_face_base_decs) else 0)
            kept_face_colors.append(face_colors[idx_f] if idx_f < len(face_colors) else (0.0, 0.0, 0.0, 1.0))

        bm.faces.ensure_lookup_table()

        # Merge-by-distance solo sulla geometria display "canonica".
        # Se abbiamo scalato radialmente o ridotto l'area dei tile, molte facce possono
        # collassare o sovrapporsi: saldarle qui rompe materiali/colori/topologia visiva.
        if not scaling_changes_display:
            try:
                bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=_MERGE_BY_DISTANCE_DIST)
                bm.verts.ensure_lookup_table()
                bm.faces.ensure_lookup_table()
            except Exception as e:
                print(f"[WARN] remove_doubles failed for {name_prefix}:", e)

        neighbor_info = None
        neighbor_bm = bm
        neighbor_bm_is_temp = False

        if scaling_changes_display:
            try:
                neighbor_bm = bmesh.new()
                neighbor_bm_verts = [neighbor_bm.verts.new(v) for v in canonical_safe_verts]
                neighbor_bm.verts.ensure_lookup_table()
                for idx_f in kept_source_face_indices:
                    f = safe_faces[idx_f]
                    try:
                        neighbor_bm.faces.new((neighbor_bm_verts[f[0]], neighbor_bm_verts[f[1]], neighbor_bm_verts[f[2]]))
                    except ValueError:
                        continue
                neighbor_bm.faces.ensure_lookup_table()
                try:
                    bmesh.ops.remove_doubles(neighbor_bm, verts=neighbor_bm.verts, dist=_MERGE_BY_DISTANCE_DIST)
                    neighbor_bm.verts.ensure_lookup_table()
                    neighbor_bm.faces.ensure_lookup_table()
                except Exception:
                    pass
                neighbor_bm_is_temp = True
            except Exception as e:
                print(f"[WARN] canonical neighbor BM build failed for {name_prefix}:", e)
                neighbor_bm = bm
                neighbor_bm_is_temp = False

        try:
            # Calcoliamo SEMPRE i layer (neighbors + coeff + base_dec) sulla geometria canonica,
            # così il color mode nearest-neighbors non si rompe se i tile vengono scalati.
            neighbor_info = compute_neighbor_counts_bmesh(neighbor_bm, kept_face_coeffs, mode_id=str(color_mode_id))
        except Exception as e:
            print(f"[WARN] neighbor count compute failed for {name_prefix}:", e)
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
        finally:
            if neighbor_bm_is_temp:
                try:
                    neighbor_bm.free()
                except Exception:
                    pass
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
        for _vgm in vg_mats_local:
            if _vgm is not None:
                mats.append(_vgm)

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

        try:
            _write_tile_orientation_attr(me, kept_face_base_decs)
        except Exception as e:
            print(f"[WARN] Unable to write 'tile_orientation_sign' for {name_prefix}:", e)

        try:
            face_bins = list(neighbor_info.get('both', [])) if isinstance(neighbor_info, dict) else []
            _apply_vg_extrusions_to_mesh(me, face_bins, mask_bin_offsets, mask_bin_walls)
        except Exception as e:
            print(f"[WARN] VG extrusion failed for {name_prefix}:", e)

        if display_faces and len(face_colors) >= len(display_faces):
            color_layer = me.color_attributes.get("floretion_color")
            if color_layer is None:
                color_layer = me.color_attributes.new(
                    name="floretion_color",
                    type='FLOAT_COLOR',
                    domain='CORNER',
                )

            color_data = color_layer.data
            # NB: le facce aggiunte da VG extrusion (walls/holes) dovrebbero conservare
            # il custom-data duplicato/extruso dal BMesh. Reimpostiamo esplicitamente solo
            # le facce "originali" mappate 1:1 con kept_face_colors/kept_face_coeffs.
            for face_idx, poly in enumerate(me.polygons):
                if face_idx < len(kept_face_coeffs):
                    coeff_face = kept_face_coeffs[face_idx]
                    if abs(coeff_face) < 1e-12 and len(obj.data.materials) >= 2:
                        poly.material_index = 1
                    else:
                        poly.material_index = 0

                if face_idx >= len(kept_face_colors):
                    continue

                # --- colore ---
                r, g, b, a = kept_face_colors[face_idx]

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

        if not tetra_mode:
            if had_vg_display and allow_vg_display:
                try:
                    assign_vg_materials_on_object(obj)
                except Exception as e:
                    print(f"[WARN] reapply VG materials failed for {name_prefix}: {e}")
            else:
                try:
                    _strip_vg_material_assignment_local(obj)
                except Exception as e:
                    print(f"[WARN] strip VG materials failed for {name_prefix}: {e}")
        else:
            # Anche il tetra deve restare su materiale base/zero quando non siamo in neighbor mode.
            if not allow_vg_display:
                try:
                    _strip_vg_material_assignment_local(obj)
                except Exception as e:
                    print(f"[WARN] strip VG materials failed for {name_prefix}: {e}")

        obj.location.x = 0.0
        obj.location.y = 0.0
        try:
            obj.hide_viewport = False
            obj.hide_render = False
        except Exception:
            pass

        solid = obj.modifiers.get("FloExtrude")
        if solid is not None:
            try:
                solid.thickness = 0.0
                solid.show_viewport = False
                solid.show_render = False
            except Exception:
                pass

        obj_cent = None
        if centroids:
            safe_cent_verts = []
            safe_cent_coeffs = []
            safe_cent_base_decs = []

            src_base_decs = samples.get("base_decs")
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
                    if src_base_decs is not None and i < len(src_base_decs):
                        safe_cent_base_decs.append(int(src_base_decs[i]))
                    else:
                        safe_cent_base_decs.append(0)
                except Exception:
                    continue

            if extend_cent and safe_cent_verts and not centroids_already_extended:
                try:
                    safe_cent_verts, safe_cent_coeffs, safe_cent_base_decs = _extend_point_cloud(
                        safe_cent_verts,
                        safe_cent_coeffs,
                        safe_cent_base_decs,
                        level=extend_level,
                    )
                except Exception as e:
                    print(f"[WARN] extend cent failed for {name_prefix}: {e}")

            if safe_cent_verts:
                if str(name_prefix).endswith("_tetra"):
                    _root = str(name_prefix)[:-6]
                    cent_obj_name = f"{_root}_cent_tetra"
                    cent_mesh_name = f"{_root}_cent_tetra_Mesh"
                else:
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

                try:
                    base_attr = me2.attributes.get("base_dec")
                    if base_attr is None:
                        base_attr = me2.attributes.new(name="base_dec", type='INT', domain='POINT')
                    base_data = base_attr.data
                    for ii, val in enumerate(safe_cent_base_decs):
                        if ii < len(base_data):
                            base_data[ii].value = int(val)
                except Exception as e:
                    print(f"[WARN] Unable to write 'base_dec' attribute for {name_prefix}_cent:", e)

                obj_cent.location.x = 0.0
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
                curve_groups = [safe_curve_points]
                if extend_curve and not centroids_already_extended:
                    try:
                        curve_groups = _extend_polyline_groups(curve_groups, level=extend_level)
                    except Exception as e:
                        print(f"[WARN] extend curve failed for {name_prefix}: {e}")

                if str(name_prefix).endswith("_tetra"):
                    _root = str(name_prefix)[:-6]
                    curve_obj_name = f"{_root}_curve_tetra"
                    curve_data_name = f"{_root}_curve_tetra_Data"
                else:
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

                for grp in curve_groups:
                    if len(grp) < 2:
                        continue
                    spline = cu.splines.new('POLY')
                    spline.points.add(len(grp) - 1)
                    for i, (cx, cy, cz) in enumerate(grp):
                        spline.points[i].co = (cx, cy, cz, 1.0)

                obj_curve.location.x = 0.0
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


    global_tetra_mode = bool(getattr(props, "use_tetrahedral", False))

    if global_tetra_mode:
        # Manteniamo sempre i Flo_* classici piatti come riferimento, e costruiamo
        # in più i Flo_*_tetra usando il mapping tetraedrico + i controlli Height correnti.
        obj_x, obj_x_cent, obj_x_curve = build_object_from_floretion(flo_x, "Flo_X", 0.0, "X", forced_height_mode="flat", forced_tetra=False)
        obj_y, obj_y_cent, obj_y_curve = build_object_from_floretion(flo_y, "Flo_Y", 0.0, "Y", forced_height_mode="flat", forced_tetra=False)
        obj_z, obj_z_cent, obj_z_curve = build_object_from_floretion(flo_z, "Flo_XY", 0.0, "X·Y", forced_height_mode="flat", forced_tetra=False)

        obj_x_t, obj_x_t_cent, obj_x_t_curve = build_object_from_floretion(flo_x, "Flo_X_tetra", 0.0, "X", forced_height_mode=str(props.height_mode or "flat"), forced_tetra=True)
        obj_y_t, obj_y_t_cent, obj_y_t_curve = build_object_from_floretion(flo_y, "Flo_Y_tetra", 0.0, "Y", forced_height_mode=str(props.height_mode or "flat"), forced_tetra=True)
        obj_z_t, obj_z_t_cent, obj_z_t_curve = build_object_from_floretion(flo_z, "Flo_XY_tetra", 0.0, "X·Y", forced_height_mode=str(props.height_mode or "flat"), forced_tetra=True)
    else:
        for stale_name in (
            "Flo_X_tetra", "Flo_X_tetra_cent", "Flo_X_tetra_curve", "Flo_X_cent_tetra", "Flo_X_curve_tetra",
            "Flo_Y_tetra", "Flo_Y_tetra_cent", "Flo_Y_tetra_curve", "Flo_Y_cent_tetra", "Flo_Y_curve_tetra",
            "Flo_XY_tetra", "Flo_XY_tetra_cent", "Flo_XY_tetra_curve", "Flo_XY_cent_tetra", "Flo_XY_curve_tetra",
        ):
            _delete_object_if_exists(stale_name)

        obj_x, obj_x_cent, obj_x_curve = build_object_from_floretion(flo_x, "Flo_X", 0.0, "X", forced_height_mode=str(props.height_mode or "flat"), forced_tetra=False)
        obj_y, obj_y_cent, obj_y_curve = build_object_from_floretion(flo_y, "Flo_Y", 0.0, "Y", forced_height_mode=str(props.height_mode or "flat"), forced_tetra=False)
        obj_z, obj_z_cent, obj_z_curve = build_object_from_floretion(flo_z, "Flo_XY", 0.0, "X·Y", forced_height_mode=str(props.height_mode or "flat"), forced_tetra=False)

        obj_x_t = obj_x_t_cent = obj_x_t_curve = None
        obj_y_t = obj_y_t_cent = obj_y_t_curve = None
        obj_z_t = obj_z_t_cent = obj_z_t_curve = None

    half_x = _bundle_half_width(obj_x, obj_x_cent, obj_x_curve)
    half_y = _bundle_half_width(obj_y, obj_y_cent, obj_y_curve)
    half_z = _bundle_half_width(obj_z, obj_z_cent, obj_z_curve)

    try:
        spacing_gap = max(0.0, float(spacing))
    except Exception:
        spacing_gap = 6.0

    dist_xy = max(spacing_gap, half_x + half_y + 0.5)
    dist_yz = max(spacing_gap, half_y + half_z + 0.5)

    x_pos_x = -float(dist_xy)
    x_pos_y = 0.0
    x_pos_z = float(dist_yz)

    _set_bundle_xy(obj_x, x_pos_x, 0.0)
    _set_bundle_xy(obj_y, x_pos_y, 0.0)
    _set_bundle_xy(obj_z, x_pos_z, 0.0)

    tetra_row_y = 0.0
    if global_tetra_mode:
        tetra_row_y = max(3.0, float(spacing_gap) * 0.70, float(max_h) + 1.5)
        _set_bundle_xy(obj_x_t, x_pos_x, tetra_row_y)
        _set_bundle_xy(obj_y_t, x_pos_y, tetra_row_y)
        _set_bundle_xy(obj_z_t, x_pos_z, tetra_row_y)

    if global_tetra_mode:
        active_obj = (
            obj_y_t or obj_x_t or obj_z_t or
            obj_y or obj_x or obj_z or
            obj_y_t_cent or obj_y_t_curve or
            obj_y_cent or obj_y_curve or
            obj_x_t_cent or obj_x_t_curve or
            obj_x_cent or obj_x_curve or
            obj_z_t_cent or obj_z_t_curve or
            obj_z_cent or obj_z_curve
        )
    else:
        active_obj = (
            obj_y or obj_x or obj_z or
            obj_y_cent or obj_y_curve or
            obj_x_cent or obj_x_curve or
            obj_z_cent or obj_z_curve
        )
    if active_obj is not None:
        context.view_layer.objects.active = active_obj

    # --- Labels (viewport-only) + Manifest ---
    labels_enabled = bool(getattr(props, "include_labels", False))
    try:
        y_off = -max(1.0, float(spacing_gap) * 0.35)
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
        location=(float(x_pos_x), float(y_off), float(z_off)),
    )
    lab_y = _ensure_viewport_label(
        collection=flo_coll,
        enabled=labels_enabled,
        name="Flo_Label_Y",
        text="Y",
        location=(float(x_pos_y), float(y_off), float(z_off)),
    )
    lab_xy = _ensure_viewport_label(
        collection=flo_coll,
        enabled=labels_enabled,
        name="Flo_Label_XY",
        text="X·Y",
        location=(float(x_pos_z), float(y_off), float(z_off)),
    )

    all_objs = (
        obj_x, obj_x_cent, obj_x_curve,
        obj_y, obj_y_cent, obj_y_curve,
        obj_z, obj_z_cent, obj_z_curve,
        obj_x_t, obj_x_t_cent, obj_x_t_curve,
        obj_y_t, obj_y_t_cent, obj_y_t_curve,
        obj_z_t, obj_z_t_cent, obj_z_t_curve,
    )

    # Tagga anche i mesh/cent/curve esistenti (nel caso fossero già presenti)
    for o in all_objs:
        if o is None:
            continue
        try:
            o[_MANAGED_TAG] = True
        except Exception:
            pass

    # Aggiorna manifest: lista esatta degli oggetti che *devono* esistere dopo questo build
    scene = getattr(context, "scene", None) or bpy.context.scene
    manifest_objs = [o for o in (*all_objs, lab_x, lab_y, lab_xy) if o is not None]
    _scene_manifest_set(scene, manifest_objs)

    try:
        ensure_nei_vertex_groups(
            context,
            target="ALL",
            clear_existing=True,
            apply_to_centroids=True,
            apply_to_tetra=True,
        )
    except Exception as e:
        print("[WARN] auto vertex-group sync failed:", e)

    try:
        apply_vg_material_policy(context, target="ALL")
    except Exception as e:
        print("[WARN] VG material policy sync failed:", e)

    if global_tetra_mode:
        try:
            sync_tetra_display_from_flat(context, target="ALL")
        except Exception as e:
            print("[WARN] tetra display sync failed:", e)

    try:
        refresh_spin_targets_if_present(
            context,
            target_names=("Flo_X", "Flo_Y", "Flo_XY", "Flo_X_tetra", "Flo_Y_tetra", "Flo_XY_tetra"),
        )
    except Exception as e:
        print("[WARN] spin refresh after build failed:", e)

    return {'FINISHED'}


