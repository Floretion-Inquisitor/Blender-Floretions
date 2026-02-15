# cranborg_util/ops_weightpaint.py

from __future__ import annotations

import bpy
import bmesh
from bpy.types import Operator
from bpy.props import EnumProperty


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
_DIGIT_TO_BASE = {
    "1": "i",
    "2": "j",
    "4": "k",
    "7": "e",
}

def _find_target_object(target: str) -> bpy.types.Object | None:
    name = f"Flo_{target}"
    return bpy.data.objects.get(name)

def _get_vertex_weight(me: bpy.types.Mesh, vg_index: int, v: bpy.types.MeshVertex) -> float:
    for g in v.groups:
        if g.group == vg_index:
            return float(g.weight)
    return 0.0

def _base_string_from_base_dec(base_dec: int) -> str | None:
    if not base_dec:
        return None
    try:
        oct_str = format(int(base_dec), "o")
    except Exception:
        return None
    out = []
    for ch in oct_str:
        if ch in _DIGIT_TO_BASE:
            out.append(_DIGIT_TO_BASE[ch])
        else:
            return None
    return "".join(out) if out else None


def _ensure_base_dec_attribute_from_bmesh(me: bpy.types.Mesh) -> bool:
    """
    Prova a recuperare base_dec da eventuale layer bmesh legacy e scriverlo come attribute FACE.
    Ritorna True se riesce.
    """
    try:
        bm = bmesh.new()
        bm.from_mesh(me)

        lay_int = bm.faces.layers.int.get("base_dec")
        lay_flt = bm.faces.layers.float.get("base_dec")

        if lay_int is None and lay_flt is None:
            bm.free()
            return False

        # crea attribute FACE se manca
        attr = me.attributes.get("base_dec")
        if attr is None or attr.domain != "FACE":
            try:
                if attr is not None:
                    me.attributes.remove(attr)
            except Exception:
                pass
            attr = me.attributes.new(name="base_dec", type="INT", domain="FACE")

        n = len(me.polygons)
        for i in range(min(n, len(bm.faces))):
            f = bm.faces[i]
            if lay_int is not None:
                val = int(f[lay_int])
            else:
                val = int(round(float(f[lay_flt])))
            attr.data[i].value = val

        bm.free()
        return True
    except Exception:
        try:
            bm.free()
        except Exception:
            pass
        return False


def _bake_coeffs_from_weights(
    obj: bpy.types.Object,
    *,
    vg: bpy.types.VertexGroup,
    max_coeff: float,
    threshold: float,
) -> dict[int, float]:
    """
    Ritorna dict base_dec -> coeff (media su tutte le facce con stesso base_dec).
    Richiede attribute FACE: base_dec (INT) sulla mesh.
    """
    me = obj.data
    n_faces = len(me.polygons)
    if n_faces == 0:
        return {}

    attr = me.attributes.get("base_dec")
    if attr is None or attr.domain != "FACE":
        # tenta recupero legacy
        ok = _ensure_base_dec_attribute_from_bmesh(me)
        if ok:
            attr = me.attributes.get("base_dec")

    if attr is None or attr.domain != "FACE":
        raise RuntimeError(
            "Manca l'attributo FACE 'base_dec' sulla mesh. "
            "Soluzione: premi una volta 'Calculate X·Y and Build Meshes' e riprova."
        )

    base_decs = [int(attr.data[i].value) for i in range(n_faces)]

    # per-vertex weights
    vg_idx = vg.index
    v_w = [0.0] * len(me.vertices)
    for i, v in enumerate(me.vertices):
        w = _get_vertex_weight(me, vg_idx, v)
        v_w[i] = max(0.0, min(1.0, w))

    # accumula coeff per base_dec
    sum_c: dict[int, float] = {}
    cnt_c: dict[int, int] = {}

    for fi, poly in enumerate(me.polygons):
        bd = base_decs[fi]
        if bd == 0:
            continue
        vids = poly.vertices
        if not vids:
            continue

        w_face = sum(v_w[vi] for vi in vids) / float(len(vids))
        coeff = float(w_face) * float(max_coeff)
        if coeff < threshold:
            continue

        sum_c[bd] = sum_c.get(bd, 0.0) + coeff
        cnt_c[bd] = cnt_c.get(bd, 0) + 1

    out: dict[int, float] = {}
    for bd, s in sum_c.items():
        c = s / float(max(1, cnt_c.get(bd, 1)))
        out[bd] = c

    return out


def _format_floretion_string(coeffs_by_base_dec: dict[int, float]) -> str:
    """
    Converte dict base_dec->coeff in string floretion.
    Formato: +a<base> +b<base> ... (con coeff ~1 => omette numero).
    """
    terms = []
    for bd, coeff in coeffs_by_base_dec.items():
        base = _base_string_from_base_dec(bd)
        if not base:
            continue
        c = float(coeff)
        if abs(c - 1.0) < 1e-6:
            terms.append((base, f"+{base}"))
        else:
            terms.append((base, f"+{c:.4g}{base}"))

    terms.sort(key=lambda t: t[0])
    if not terms:
        return "0"
    s = " ".join(t[1] for t in terms)
    return s[1:] if s.startswith("+") else s


# ------------------------------------------------------------
# Operators
# ------------------------------------------------------------
class FLORET_MESH_OT_wp_setup(Operator):
    bl_idname = "floret_mesh.wp_setup"
    bl_label = "Weight Paint Setup (Floretion)"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        items=[("X", "X", ""), ("Y", "Y", "")],
        name="Target",
        default="X",
    )

    def execute(self, context):
        obj = _find_target_object(self.target)
        if obj is None:
            self.report({'ERROR'}, f"Non trovo l'oggetto Flo_{self.target} (serve prima fare Build).")
            return {'CANCELLED'}
        if obj.type != "MESH":
            self.report({'ERROR'}, f"Flo_{self.target} non è una mesh.")
            return {'CANCELLED'}

        # crea/assicura vertex group
        vg_name = "FLO_WP_X" if self.target == "X" else "FLO_WP_Y"
        vg = obj.vertex_groups.get(vg_name)
        if vg is None:
            vg = obj.vertex_groups.new(name=vg_name)

        # seleziona e passa a WEIGHT_PAINT
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        for o in context.selected_objects:
            o.select_set(False)

        obj.select_set(True)
        context.view_layer.objects.active = obj

        try:
            bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        except Exception:
            pass

        return {'FINISHED'}


class FLORET_MESH_OT_wp_bake(Operator):
    bl_idname = "floret_mesh.wp_bake"
    bl_label = "Weight Paint Bake → Input"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        items=[("X", "X", ""), ("Y", "Y", "")],
        name="Target",
        default="X",
    )

    def execute(self, context):
        scene = context.scene
        props = scene.floretion_mesh_settings

        obj = _find_target_object(self.target)
        if obj is None:
            self.report({'ERROR'}, f"Non trovo l'oggetto Flo_{self.target} (serve prima fare Build).")
            return {'CANCELLED'}
        if obj.type != "MESH":
            self.report({'ERROR'}, f"Flo_{self.target} non è una mesh.")
            return {'CANCELLED'}

        vg_name = "FLO_WP_X" if self.target == "X" else "FLO_WP_Y"
        vg = obj.vertex_groups.get(vg_name)
        if vg is None:
            self.report({'ERROR'}, f"Vertex Group '{vg_name}' non trovato. Fai prima Setup WP.")
            return {'CANCELLED'}

        max_coeff = float(getattr(props, "wp_max_coeff", 2.0) or 2.0)
        threshold = float(getattr(props, "wp_threshold", 0.01) or 0.0)

        # salva selection per ripristino “gentile”
        prev_active = context.view_layer.objects.active
        prev_sel = [o for o in context.selected_objects]

        # assicura Object Mode e attivo
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        try:
            coeffs_by_bd = _bake_coeffs_from_weights(obj, vg=vg, max_coeff=max_coeff, threshold=threshold)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            try:
                props.log_message = f"WP Bake {self.target} error: {e}"
            except Exception:
                pass
            return {'CANCELLED'}

        out_str = _format_floretion_string(coeffs_by_bd)

        if self.target == "X":
            props.x_string = out_str
        else:
            props.y_string = out_str

        props.log_message = f"WP Bake {self.target}: scritto in input ({len(coeffs_by_bd)} coeff)."

        # ------------------------------------------------------------
        # AUTO: ricalcola X·Y subito dopo Bake (evita “sparisce e riappare”)
        # ------------------------------------------------------------
        try:
            bpy.ops.floret_mesh.build('EXEC_DEFAULT')
        except Exception:
            # fallback: almeno tenta invoke
            try:
                bpy.ops.floret_mesh.build('INVOKE_DEFAULT')
            except Exception:
                pass

        # ripristina selezione
        try:
            for o in context.selected_objects:
                o.select_set(False)
            for o in prev_sel:
                if o and o.name in bpy.data.objects:
                    o.select_set(True)
            if prev_active and prev_active.name in bpy.data.objects:
                context.view_layer.objects.active = prev_active
        except Exception:
            pass

        return {'FINISHED'}


classes = (
    FLORET_MESH_OT_wp_setup,
    FLORET_MESH_OT_wp_bake,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
