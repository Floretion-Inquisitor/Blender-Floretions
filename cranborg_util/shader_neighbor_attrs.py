# cranborg_util/shader_neighbor_attrs.py
from __future__ import annotations

import bmesh
from bisect import bisect_left, bisect_right

# -----------------------------
# Layer names (coerenti col resto dell'add-on)
# -----------------------------
FACE_EDGES = "neighbors_edges"
FACE_VERTS = "neighbors_verts"
FACE_BOTH  = "neighbors_edges_and_verts"

CORNER_EDGES = "color_edges"
CORNER_VERTS = "color_verts"
CORNER_BOTH  = "color_edges_and_verts"

# Base vector coeff (signed) + statistiche globali
FACE_BASE          = "base_coeff"          # signed per face
FACE_BASE_ABS      = "base_coeff_abs"      # abs(base_coeff) per face
FACE_BASE_MIN      = "base_coeff_min"      # min globale (ripetuto su ogni face)
FACE_BASE_MAX      = "base_coeff_max"      # max globale (ripetuto su ogni face)
FACE_BASE_ABS_MAX  = "base_coeff_abs_max"  # max globale di abs (ripetuto su ogni face)

# Nuovo: versioni quantile-based
FACE_BASE_Q        = "base_coeff_q"        # signed quantile in [-1..1]
FACE_BASE_ABS_Q    = "base_coeff_abs_q"    # abs quantile in [0..1]

# (facoltativo ma utile per compat con altri pezzi)
FACE_BASE_DEC = "base_dec"

# -----------------------------
# Coeff filter
# -----------------------------
EPS_ACTIVE = 1e-12

# “stesso coeff” se abs(delta) <= max(COEFF_ABS_TOL, COEFF_REL_TOL * max_abs_coeff)
COEFF_ABS_TOL = 1e-4
COEFF_REL_TOL = 1e-3

# Palette leggibile (RGBA 0..1): 0..7+
_PALETTE = [
    (0.10, 0.10, 0.10, 1.0),  # 0
    (0.10, 0.25, 0.95, 1.0),  # 1
    (0.10, 0.75, 0.85, 1.0),  # 2
    (0.15, 0.85, 0.20, 1.0),  # 3
    (0.95, 0.85, 0.15, 1.0),  # 4
    (0.95, 0.55, 0.10, 1.0),  # 5
    (0.95, 0.15, 0.15, 1.0),  # 6
    (0.80, 0.15, 0.85, 1.0),  # 7+
]


def _layer_loops_color(bm: bmesh.types.BMesh):
    """Compat: Blender ha cambiato nome tra versioni."""
    try:
        return bm.loops.layers.color
    except Exception:
        pass
    try:
        return bm.loops.layers.float_color
    except Exception:
        return None


def _palette_color(count: int):
    idx = int(count)
    if idx < 0:
        idx = 0
    if idx >= len(_PALETTE):
        idx = len(_PALETTE) - 1
    return _PALETTE[idx]


def _coeff_key(c: float, tol: float) -> int | None:
    """Quantizzazione robusta: coeff simili -> stessa chiave."""
    if abs(c) <= EPS_ACTIVE:
        return None
    if tol <= 0.0:
        tol = COEFF_ABS_TOL
    return int(round(float(c) / float(tol)))


def _compute_abs_quantiles(coeff_signed: list[float], eps: float = EPS_ACTIVE):
    """
    Restituisce due liste:
      - abs_q in [0..1]
      - signed_q in [-1..1]
    basate sul rank empirico di abs(coeff).

    Le facce con coeff ~ 0 ricevono 0.
    """
    n = len(coeff_signed)
    abs_q = [0.0] * n
    signed_q = [0.0] * n
    abs_vals = [abs(float(c)) for c in coeff_signed if abs(float(c)) > eps]

    if not abs_vals:
        return abs_q, signed_q

    sorted_abs = sorted(abs_vals)
    count = len(sorted_abs)

    for i, c in enumerate(coeff_signed):
        a = abs(float(c))
        if a <= eps:
            abs_q[i] = 0.0
            signed_q[i] = 0.0
            continue

        if count == 1:
            q = 1.0
        else:
            left = bisect_left(sorted_abs, a)
            right = bisect_right(sorted_abs, a)
            midrank = 0.5 * (left + right - 1)
            q = midrank / float(count - 1)

        q = max(0.0, min(1.0, q))
        abs_q[i] = q
        signed_q[i] = q if c >= 0.0 else -q

    return abs_q, signed_q


def compute_neighbor_counts_bmesh(
    bm: bmesh.types.BMesh,
    face_coeffs: list[float],
    mode_id: str | None = None,
    *,
    eps: float = EPS_ACTIVE,
    coeff_abs_tol: float = COEFF_ABS_TOL,
    coeff_rel_tol: float = COEFF_REL_TOL,
):
    """
    Conta i vicini per faccia usando SOLO la topologia canonica.

    Differenza importante rispetto alla versione precedente:
      - un vicino viene contato se è topologicamente adiacente
        e ha coefficiente non nullo
      - NON richiediamo più coeff_j ~ coeff_i

    Questo evita pattern anisotropi/“a strisce” quando la floretion viene
    costruita da trasformazioni come centroid-distance: i gruppi vicini devono
    riflettere la geometria locale, non classi di coefficiente.
    """
    bm.faces.ensure_lookup_table()
    n = len(bm.faces)
    if n == 0:
        return {"edges": [], "verts": [], "both": [], "eps": eps}

    coeffs = [0.0] * n
    for i in range(min(n, len(face_coeffs))):
        try:
            coeffs[i] = float(face_coeffs[i])
        except Exception:
            coeffs[i] = 0.0

    abs_c = [abs(v) for v in coeffs]
    max_abs = max(abs_c) if abs_c else 0.0
    if max_abs <= eps:
        max_abs = 1.0

    edges_counts = [0] * n
    verts_counts = [0] * n
    both_counts  = [0] * n

    active = [abs(coeffs[i]) > eps for i in range(n)]

    for i, f in enumerate(bm.faces):
        if not active[i]:
            continue

        edge_set = set()
        for e in f.edges:
            for nf in e.link_faces:
                if nf is f:
                    continue
                j = getattr(nf, "index", -1)
                if 0 <= j < n and active[j]:
                    edge_set.add(j)

        vert_set = set()
        for v in f.verts:
            for nf in v.link_faces:
                if nf is f:
                    continue
                j = getattr(nf, "index", -1)
                if 0 <= j < n and active[j]:
                    vert_set.add(j)

        verts_only = vert_set.difference(edge_set)
        both = vert_set.union(edge_set)

        edges_counts[i] = len(edge_set)
        verts_counts[i] = len(verts_only)
        both_counts[i]  = len(both)

    return {"edges": edges_counts, "verts": verts_counts, "both": both_counts, "eps": eps}


def write_neighbor_bmesh_layers(
    bm: bmesh.types.BMesh,
    counts: dict,
    face_coeffs: list[float] | None = None,
    *,
    face_base_decs: list[int] | None = None,
    face_edges_name: str = FACE_EDGES,
    face_verts_name: str = FACE_VERTS,
    face_both_name: str  = FACE_BOTH,
    face_base_name: str  = FACE_BASE,
    face_base_abs_name: str = FACE_BASE_ABS,
    face_base_min_name: str = FACE_BASE_MIN,
    face_base_max_name: str = FACE_BASE_MAX,
    face_base_abs_max_name: str = FACE_BASE_ABS_MAX,
    face_base_q_name: str = FACE_BASE_Q,
    face_base_abs_q_name: str = FACE_BASE_ABS_Q,
    corner_edges_name: str = CORNER_EDGES,
    corner_verts_name: str = CORNER_VERTS,
    corner_both_name: str  = CORNER_BOTH,
    base_dec_name: str = FACE_BASE_DEC,
):
    """Scrive layer su BMesh PRIMA di bm.to_mesh(me)."""
    bm.faces.ensure_lookup_table()
    n = len(bm.faces)
    if n == 0:
        return

    edges = counts.get("edges") or [0] * n
    verts = counts.get("verts") or [0] * n
    both  = counts.get("both")  or [0] * n
    eps = float(counts.get("eps") or EPS_ACTIVE)

    lay_f = bm.faces.layers.float
    le  = lay_f.get(face_edges_name) or lay_f.new(face_edges_name)
    lv  = lay_f.get(face_verts_name) or lay_f.new(face_verts_name)
    lb  = lay_f.get(face_both_name)  or lay_f.new(face_both_name)

    lbc      = lay_f.get(face_base_name)         or lay_f.new(face_base_name)
    lbc_abs  = lay_f.get(face_base_abs_name)     or lay_f.new(face_base_abs_name)
    lbc_min  = lay_f.get(face_base_min_name)     or lay_f.new(face_base_min_name)
    lbc_max  = lay_f.get(face_base_max_name)     or lay_f.new(face_base_max_name)
    lbc_amax = lay_f.get(face_base_abs_max_name) or lay_f.new(face_base_abs_max_name)
    lbc_q    = lay_f.get(face_base_q_name)       or lay_f.new(face_base_q_name)
    lbc_absq = lay_f.get(face_base_abs_q_name)   or lay_f.new(face_base_abs_q_name)

    lay_bd = None
    if face_base_decs is not None:
        lay_i = bm.faces.layers.int
        lay_bd = lay_i.get(base_dec_name) or lay_i.new(base_dec_name)

    loop_layers = _layer_loops_color(bm)
    if loop_layers is None:
        loop_edges = loop_verts = loop_both = None
    else:
        loop_edges = loop_layers.get(corner_edges_name) or loop_layers.new(corner_edges_name)
        loop_verts = loop_layers.get(corner_verts_name) or loop_layers.new(corner_verts_name)
        loop_both  = loop_layers.get(corner_both_name)  or loop_layers.new(corner_both_name)

    coeff_signed = [0.0] * n
    if face_coeffs:
        for i in range(min(n, len(face_coeffs))):
            try:
                coeff_signed[i] = float(face_coeffs[i])
            except Exception:
                coeff_signed[i] = 0.0

    active_coeffs = [c for c in coeff_signed if abs(c) > eps]
    if active_coeffs:
        coeff_min = min(active_coeffs)
        coeff_max = max(active_coeffs)
        abs_max = max(abs(c) for c in active_coeffs)
    else:
        coeff_min = 0.0
        coeff_max = 0.0
        abs_max = 0.0

    abs_q, signed_q = _compute_abs_quantiles(coeff_signed, eps=eps)

    for i, f in enumerate(bm.faces):
        e = int(edges[i]) if i < len(edges) else 0
        v = int(verts[i]) if i < len(verts) else 0
        b = int(both[i])  if i < len(both)  else 0

        f[le] = float(e)
        f[lv] = float(v)
        f[lb] = float(b)

        c = float(coeff_signed[i]) if i < len(coeff_signed) else 0.0
        f[lbc] = c
        f[lbc_abs] = float(abs(c))
        f[lbc_min] = float(coeff_min)
        f[lbc_max] = float(coeff_max)
        f[lbc_amax] = float(abs_max)
        f[lbc_q] = float(signed_q[i])
        f[lbc_absq] = float(abs_q[i])

        if lay_bd is not None and face_base_decs is not None:
            try:
                f[lay_bd] = int(face_base_decs[i]) if i < len(face_base_decs) else 0
            except Exception:
                f[lay_bd] = 0

        if loop_edges is not None:
            ce = _palette_color(e)
            cv = _palette_color(v)
            cb = _palette_color(b)
            for loop in f.loops:
                loop[loop_edges] = ce
                loop[loop_verts] = cv
                loop[loop_both]  = cb
