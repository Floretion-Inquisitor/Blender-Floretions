# cranborg_util/mesh_build.py

from __future__ import annotations

from typing import Any, Dict, Tuple, List

import math
import numpy as np
import bpy


# ---------------------------------------------------------------------------
# Utility: create node links safely
# ---------------------------------------------------------------------------

def ensure_link(out_socket, in_socket):
    """
    Crea un link tra due socket di nodi, se entrambi non sono None.
    Rimuove eventuali link precedenti che entrano in in_socket.
    """
    if out_socket is None or in_socket is None:
        return

    nt = in_socket.id_data
    links = nt.links

    # rimuovi eventuali link esistenti verso in_socket
    for link in list(links):
        if link.to_socket == in_socket:
            links.remove(link)

    # crea il nuovo link
    links.new(out_socket, in_socket)


# ---------------------------------------------------------------------------
# Geometria di base del triangolo
# ---------------------------------------------------------------------------

def calculate_orientation(oct_str: str) -> str:
    """
    Copia della logica che usi in Triangleize:
    conta le cifre 1,2,4 e decide 'up'/'down' in base a parità e ordine.
    """
    count = sum(1 for d in oct_str if d in "124")
    order = len(oct_str)
    if count % 2 == 0:
        return "up" if order % 2 == 0 else "down"
    return "down" if order % 2 == 0 else "up"


def build_triangle_verts(
    cx: float,
    cy: float,
    size: float,
    orientation: str,
):
    half_base = math.sin(math.pi / 3.0) * size

    if orientation == "up":
        # punta verso +Y (in alto) in Blender
        v1 = (cx, cy + size)
        v2 = (cx - half_base, cy - size / 2.0)
        v3 = (cx + half_base, cy - size / 2.0)
    else:
        # punta verso -Y (in basso) in Blender
        v1 = (cx, cy - size)
        v2 = (cx - half_base, cy + size / 2.0)
        v3 = (cx + half_base, cy + size / 2.0)

    return v1, v2, v3



def bgr_to_rgba01(color_bgr: np.ndarray, brightness01: float) -> Tuple[float, float, float, float]:
    """
    Converte un colore BGR [0..255] + brightness [0..1] in RGBA [0..1].
    """
    b, g, r = color_bgr
    r01 = min(1.0, max(0.0, (r / 255.0) * brightness01))
    g01 = min(1.0, max(0.0, (g / 255.0) * brightness01))
    b01 = min(1.0, max(0.0, (b / 255.0) * brightness01))
    return (r01, g01, b01, 1.0)


# ---------------------------------------------------------------------------
# Costruzione geometria (triangoli / centroidi) + estrusione opzionale
# ---------------------------------------------------------------------------
def build_geometry(
    samples: Dict[str, Any],
    colors_bgr: np.ndarray,
    brightness: np.ndarray,
    *,
    global_scale: float,
    tri_size: float,
    z_mode: str = "FLAT",            # FLAT | COEFF_SIGNED
    z_coeff_scale: float = 1.0,
    plot_mode: str = "TRIANGLES",    # TRIANGLES | CENTROIDS
    extrusion_depth: float = 0.0,    # >0 => estrusione in -Z
    coeffs_for_flags: np.ndarray | None = None,
) -> Tuple[
    List[Tuple[float, float, float]],
    List[Tuple[int, int, int]],
    List[Tuple[float, float, float, float]],
    List[Tuple[float, float, float]],
    List[float],
    List[int]
]:
    """
    Costruisce:

      - verts       : lista di (x,y,z)
      - faces       : lista di (i0,i1,i2) (vuota in CENTROIDS)
      - face_colors : lista di RGBA per faccia (triangles) o per punto (centroids)
      - centroids   : lista di (x,y,z) – utile per instancing futuro
      - face_coeffs : lista di coeff per ogni faccia (o punto in CENTROIDS),
                      basata su coeffs_for_flags (coeff "reali" della floretion)
    """
    coeffs_geom = samples["coeffs"]
    coords = samples["coords"]
    dists = samples["dists"]
    oct_strings = samples["oct_strings"]

    # coeff "reali" per decidere zero/non-zero e per assegnare ai face-attrs
    if coeffs_for_flags is None:
        coeffs_flag = coeffs_geom
    else:
        coeffs_flag = coeffs_for_flags

    n = len(coeffs_geom)
    if n == 0:
        return [], [], [], [], []

    # normalizzazioni per z-mode (basato su coeffs_geom, che può essere idx_norm)
    max_abs_coeff = float(np.max(np.abs(coeffs_geom))) if np.any(coeffs_geom) else 1.0

    verts: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []
    face_colors: List[Tuple[float, float, float, float]] = []
    centroids: List[Tuple[float, float, float]] = []
    face_coeffs: List[float] = []
    face_base_decs: List[int] = []

    # base size ridotto per ordini alti
    order = int(samples["order"])
    base_size = tri_size * (0.5 ** max(order - 1, 0))
    #base_size = (tri_size * global_scale) * (1.3 * (0.5 ** order))

    use_extrude = (extrusion_depth > 0.0 and plot_mode == "TRIANGLES")
    extrude = float(extrusion_depth)

    for i in range(n):
        coeff_z = float(coeffs_geom[i])
        coeff_flag = float(coeffs_flag[i])
        (x, y) = coords[i]
        color_bgr = colors_bgr[i]
        bright = float(brightness[i])
        oct_str = str(oct_strings[i])
        try:
            base_dec = int(oct_str, 8)
        except Exception:
            base_dec = 0

        # posizione base in 2D
        cx = float(x) * global_scale
        cy = float(y) * global_scale

        # z-mode
        if z_mode == "COEFF_SIGNED" and max_abs_coeff > 0:
            z = (coeff_z / max_abs_coeff) * z_coeff_scale
        else:
            z = 0.0

        # centroid (per uso futuro / instancing)
        centroid_z = z if not use_extrude else (z - extrude * 0.5)
        centroids.append((cx, cy, centroid_z))

        # colore RGBA da BGR + brightness
        rgba = bgr_to_rgba01(color_bgr, bright)
        # NOTA: non forziamo più alpha=0 per coeff=0 qui.
        # La distinzione zero/non-zero viene fatta via material_index
        # e tramite l'attributo 'face_coeff'.

        if plot_mode == "CENTROIDS":
            verts.append((cx, cy, z))
            faces.append(())
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)
            continue

        # TRIANGLES
        orientation = calculate_orientation(oct_str)
        v1_2d, v2_2d, v3_2d = build_triangle_verts(cx, cy, base_size, orientation)

        if not use_extrude:
            v1 = (v1_2d[0], v1_2d[1], z)
            v2 = (v2_2d[0], v2_2d[1], z)
            v3 = (v3_2d[0], v3_2d[1], z)

            i0 = len(verts)
            verts.extend([v1, v2, v3])
            faces.append((i0, i0 + 1, i0 + 2))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)
        else:
            # estrusione: prisma triangolare
            z_top = z
            z_bot = z - extrude

            v1t = (v1_2d[0], v1_2d[1], z_top)
            v2t = (v2_2d[0], v2_2d[1], z_top)
            v3t = (v3_2d[0], v3_2d[1], z_top)

            v1b = (v1_2d[0], v1_2d[1], z_bot)
            v2b = (v2_2d[0], v2_2d[1], z_bot)
            v3b = (v3_2d[0], v3_2d[1], z_bot)

            i0 = len(verts)
            verts.extend([v1t, v2t, v3t, v1b, v2b, v3b])
            t0, t1, t2 = i0, i0 + 1, i0 + 2
            b0, b1, b2 = i0 + 3, i0 + 4, i0 + 5

            # top
            faces.append((t0, t1, t2))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            # bottom
            faces.append((b2, b1, b0))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            # lati (3 quad -> 6 triangoli)
            faces.append((t0, t1, b1))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            faces.append((t0, b1, b0))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            faces.append((t1, t2, b2))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            faces.append((t1, b2, b1))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            faces.append((t2, t0, b0))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

            faces.append((t2, b0, b2))
            face_base_decs.append(int(base_dec))
            face_colors.append(rgba)
            face_coeffs.append(coeff_flag)

    return verts, faces, face_colors, centroids, face_coeffs, face_base_decs



# ---------------------------------------------------------------------------
# Materiale: usa il layer vertex color "floretion_color" + emissione
# ---------------------------------------------------------------------------
def ensure_floretion_material() -> bpy.types.Material:
    """
    Crea (o riusa) il materiale principale per i triangoli delle floretions.

    - Se esiste già un materiale chiamato "FloretionMaterial", NON tocchiamo
      la sua struttura nodale (così non perdi i tweak).
    - Se non esiste, lo creiamo con:
        VertexColor "floretion_color" -> Principled Base Color + Emission
    - Lasciamo che l'intensità di emissione venga gestita da ops_build
      (Emission Strength viene aggiornato lì ogni volta).
    """
    mat_name = "FloretionMaterial"
    mat = bpy.data.materials.get(mat_name)

    if mat is None:
        # --- creazione iniziale, solo qui costruiamo i nodi ---
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True

        nt = mat.node_tree
        nodes = nt.nodes
        links = nt.links

        # pulizia minima: teniamo solo Output/Principled se già presenti
        for n in list(nodes):
            if n.type not in {'OUTPUT_MATERIAL', 'BSDF_PRINCIPLED'}:
                nodes.remove(n)

        # Output
        out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if out is None:
            out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)

        # Principled
        bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)

        # Nodo VertexColor / Attribute per "floretion_color"
        vc = None
        for n in nodes:
            if n.type in {'VERTEX_COLOR', 'ATTRIBUTE'}:
                vc = n
                break

        if vc is None:
            # prova prima VertexColor, poi Attribute come fallback
            try:
                vc = nodes.new("ShaderNodeVertexColor")
                vc.layer_name = "floretion_color"
            except Exception:
                vc = nodes.new("ShaderNodeAttribute")
                vc.attribute_name = "floretion_color"
        else:
            if hasattr(vc, "layer_name"):
                vc.layer_name = "floretion_color"
            if hasattr(vc, "attribute_name"):
                vc.attribute_name = "floretion_color"

        vc.location = (-300, 0)

        # Base Color
        color_out = vc.outputs.get("Color")
        base_color_in = bsdf.inputs.get("Base Color")
        if color_out is not None and base_color_in is not None:
            ensure_link(color_out, base_color_in)

        # Emission / Emission Color
        emission_in = bsdf.inputs.get("Emission") or bsdf.inputs.get("Emission Color")
        if color_out is not None and emission_in is not None:
            ensure_link(color_out, emission_in)

        # diamo un valore di default ragionevole a Emission Strength,
        # ma poi verrà sovrascritto da ops_build in base allo slider UI
        es_input = bsdf.inputs.get("Emission Strength")
        if es_input is not None and es_input.default_value == 0.0:
            es_input.default_value = 1.0

    # proprietà di rendering: settiamo solo se esistono (per compatibilità)
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True

    if hasattr(mat, "blend_method"):
        mat.blend_method = 'OPAQUE'
    if hasattr(mat, "shadow_method"):
        mat.shadow_method = 'OPAQUE'

    return mat




def ensure_floretion_zero_material() -> bpy.types.Material:
    """
    Materiale per i triangoli con coeff = 0.

    - Nome fisso: "FloretionCoeffZeroMaterial"
    - Se già esiste, NON distruggiamo la struttura dei nodi, ma
      ci assicuriamo che:
        * ci sia un Principled
        * Alpha del Principled sia 1.0 (opaco di base)
        * blend_method = BLEND
        * shadow_method = NONE
    - Se non esiste, lo creiamo nero, ma opaco (Alpha = 1.0).
      L'utente potrà poi cambiarlo a mano.
    """
    mat_name = "FloretionCoeffZeroMaterial"
    mat = bpy.data.materials.get(mat_name)

    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True

        nt = mat.node_tree
        nodes = nt.nodes

        # pulizia minima: teniamo solo Output/Principled se presenti
        for n in list(nodes):
            if n.type not in {'OUTPUT_MATERIAL', 'BSDF_PRINCIPLED'}:
                nodes.remove(n)

        out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if out is None:
            out = nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)

        bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)

        # nero, opaco (non trasparente di default)
        base_col = bsdf.inputs.get("Base Color")
        alpha_in = bsdf.inputs.get("Alpha")
        if base_col is not None:
            base_col.default_value = (0.0, 0.0, 0.0, 1.0)
        if alpha_in is not None:
            alpha_in.default_value = 1.0  # <-- importante

        # link BSDF -> Surface
        bsdf_out = bsdf.outputs.get("BSDF")
        surf_in = out.inputs.get("Surface")
        if bsdf_out is not None and surf_in is not None:
            ensure_link(bsdf_out, surf_in)

    # --- Parte che viene SEMPRE eseguita, anche se il materiale già esiste ---

    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True

    # proviamo a trovare un Principled esistente
    bsdf = None
    if mat.node_tree is not None:
        for n in mat.node_tree.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                bsdf = n
                break

    if bsdf is not None:
        # forza Alpha a 1.0 se è ~0 (caso vecchie versioni)
        alpha_in = bsdf.inputs.get("Alpha")
        if alpha_in is not None:
            if alpha_in.default_value < 0.999:
                alpha_in.default_value = 1.0

    # trasparenza / ombre: vogliamo che questo materiale non faccia ombre
    if hasattr(mat, "blend_method"):
        mat.blend_method = 'BLEND'
    if hasattr(mat, "shadow_method"):
        mat.shadow_method = 'NONE'

    return mat


def apply_material_preset(mat: bpy.types.Material, preset: str, *, is_zero: bool = False):
    """
    Applica un preset "soft" ad un materiale esistente.

    Non distrugge la struttura nodale: cerca un Principled BSDF e
    modifica solo alcuni input + blend_method / shadow_method.

    preset:
      - DEFAULT, GLASS, PLASTIC, MARBLE, CUSTOM
      - per i materiali zero: HIDDEN ha un comportamento speciale.
    """
    if mat is None:
        return
    if not getattr(mat, "use_nodes", False):
        return
    if mat.node_tree is None:
        return

    nodes = mat.node_tree.nodes
    bsdf = None
    for n in nodes:
        if n.type == 'BSDF_PRINCIPLED':
            bsdf = n
            break
    if bsdf is None:
        return

    inputs = bsdf.inputs

    def set_input(name: str, value):
        sock = inputs.get(name)
        if sock is not None:
            sock.default_value = value

    # Caso CUSTOM: non tocchiamo nulla
    if preset == "CUSTOM":
        return

    # Caso speciale: materiale zero completamente nascosto
    if is_zero and preset == "HIDDEN":
        set_input("Alpha", 0.0)
        if hasattr(mat, "blend_method"):
            mat.blend_method = 'BLEND'
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = 'NONE'
        return

    # Per tutti gli altri preset, assicuriamoci che Alpha sia 1.0
    alpha_in = inputs.get("Alpha")
    if alpha_in is not None and alpha_in.default_value < 0.999:
        alpha_in.default_value = 1.0

    # ---------- Preset base ----------

    if preset == "DEFAULT":
        # equivalente a quello che avevi prima:
        # vertex color + emission, niente ombre
        set_input("Metallic", 0.0)
        set_input("Roughness", 0.4)
        set_input("Transmission", 0.0)
        if hasattr(mat, "blend_method"):
            mat.blend_method = 'BLEND'
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = 'NONE'

    elif preset == "GLASS":
        set_input("Metallic", 0.0)
        set_input("Roughness", 0.05)
        set_input("Transmission", 1.0)
        set_input("IOR", 1.45)
        if hasattr(mat, "blend_method"):
            mat.blend_method = 'BLEND'
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = 'HASHED'

    elif preset == "PLASTIC":
        set_input("Metallic", 0.0)
        set_input("Roughness", 0.25)
        set_input("Specular", 0.5)
        set_input("Clearcoat", 0.3)
        set_input("Transmission", 0.0)
        if hasattr(mat, "blend_method"):
            mat.blend_method = 'OPAQUE'
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = 'OPAQUE'

    elif preset == "MARBLE":
        # per ora solo un materiale più "soft", senza nodi extra
        set_input("Metallic", 0.0)
        set_input("Roughness", 0.6)
        set_input("Specular", 0.2)
        set_input("Transmission", 0.0)
        if hasattr(mat, "blend_method"):
            mat.blend_method = 'OPAQUE'
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = 'OPAQUE'
