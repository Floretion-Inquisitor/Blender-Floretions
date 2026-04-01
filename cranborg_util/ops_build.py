# cranborg_util/ops_build.py
#
# Facciata “pubblica” per gli operatori Blender.
# Importa la logica dal core e la gestione scena dal modulo dedicato.

from __future__ import annotations

import bpy
from bpy.types import Operator

from floretion import Floretion

from .ui_props import FloretionMeshSettings
from . import seeds

# centroid distance (firma nuova: keyword-only, niente coeff_mode)
from lib.triangleize_utils.centroid_distance import flo_from_centroid_distance

from .ops_build_core import (
    _build_mesh_triplet,
)
from .ops_build_cache import (
    _cache_set,
    _cache_get,
    _cache_matches_props,
)
class FLORET_MESH_OT_build(Operator):
    bl_idname = "floret_mesh.build"
    bl_label = "Build Floretion Mesh (X, Y, X·Y)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        props: FloretionMeshSettings = scene.floretion_mesh_settings

        props.log_message = ""
        props.z_string = ""

        try:
            order = max(1, int(props.typical_order))
        except Exception:
            order = 1

        try:
            seed_x = seeds.make_seed_from_string(props.x_string, order=order)
        except Exception as e:
            msg = f"Error parsing X (order {order}): {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        try:
            seed_y = seeds.make_seed_from_string(props.y_string, order=order)
        except Exception as e:
            msg = f"Error parsing Y (order {order}): {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        if seed_x.flo_order != seed_y.flo_order:
            msg = (
                f"Order mismatch: X has order {seed_x.flo_order}, "
                f"Y has order {seed_y.flo_order}."
            )
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        flo_x = seed_x
        flo_y = seed_y

        try:
            flo_z = flo_x * flo_y
            
        except Exception as e:
            msg = f"Error computing X*Y: {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        try:
            z_str = flo_z.as_floretion_notation()
        except Exception:
            z_str = "<error in as_floretion_notation()>"

        props.z_string = z_str
        print("Z =", z_str)

        try:
            _cache_set(order=order, x_string=props.x_string, y_string=props.y_string, flo_x=flo_x, flo_y=flo_y, flo_z=flo_z)
        except Exception:
            pass

        return _build_mesh_triplet(context, props, flo_x, flo_y, flo_z, op=self)


class FLORET_MESH_OT_rebuild_cached(Operator):
    """Rebuild mesh/materiali usando l'ultimo X·Y calcolato (cache)."""
    bl_idname = "floret_mesh.rebuild_cached"
    bl_label = "Rebuild Meshes (cached X·Y)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        props: FloretionMeshSettings = scene.floretion_mesh_settings

        try:
            order = max(1, int(props.typical_order))
        except Exception:
            order = 1

        if not _cache_matches_props(props, order):
            return {'CANCELLED'}

        c = _cache_get()
        if not c:
            return {'CANCELLED'}

        flo_x = c["flo_x"]
        flo_y = c["flo_y"]
        flo_z = c["flo_z"]

        if flo_x is None or flo_y is None or flo_z is None:
            return {'CANCELLED'}

        return _build_mesh_triplet(context, props, flo_x, flo_y, flo_z, op=self)


class FLORET_MESH_OT_transform_input(Operator):
    bl_idname = "floret_mesh.transform_input"
    bl_label = "Transform Input Floretion"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(
        items=[("X", "X", ""), ("Y", "Y", ""), ("Z", "Z", "")],
        name="Target",
        description="Which input to transform",
        default="X",
    )

    action: bpy.props.EnumProperty(
        items=[
            ("TRI", "Tri", ""),
            ("ROT", "Rot", ""),
            ("PROJ_STRIP_GROW", "ProjStripGrow", ""),
            ("ROT_TRI", "RotTri", ""),
            ("SQUARE", "Square", "X -> X*X (norm to 2)"),
            ("NOT", "Not", "Replace target with its antifloretion (elementwise_not)"),
            ("COPY_TO_X", "to X", "Copy Z to X"),
            ("COPY_TO_Y", "to Y", "Copy Z to Y"),
            ("CDIST", "CentroidDistance", "Replace with flo_from_centroid_distance"),
            ("BACK", "Back", ""),
            ("FORWARD", "Forward", ""),
        ],
        name="Action",
        description="Transform operation",
        default="TRI",
    )

    def execute(self, context):
        scene = context.scene
        props: FloretionMeshSettings = scene.floretion_mesh_settings

        try:
            order = max(1, int(props.typical_order))
        except Exception:
            order = 1

        def _set_prev_next(new_prev: str, new_next: str):
            if self.target == "X":
                props.x_prev_text = new_prev
                props.x_next_text = new_next
            elif self.target == "Y":
                props.y_prev_text = new_prev
                props.y_next_text = new_next
            else:
                props.z_prev_text = new_prev
                props.z_next_text = new_next

        def _set_target_value(new_text: str):
            if self.target == "X":
                props.x_string = new_text
            elif self.target == "Y":
                props.y_string = new_text
            else:
                props.z_string = new_text

        def _rebuild_for_target():
            try:
                if self.target == "Z":
                    bpy.ops.floret_mesh.rebuild_cached('EXEC_DEFAULT')
                else:
                    bpy.ops.floret_mesh.build('INVOKE_DEFAULT')
            except Exception:
                pass

        def _commit_z_cache_from_text(z_text: str) -> bool:
            if self.target != "Z":
                return True
            try:
                flo_z_new = seeds.make_seed_from_string(z_text, order=order)
            except Exception as e:
                msg = f"Error parsing Z: {e}"
                self.report({'ERROR'}, msg)
                props.log_message = msg
                return False

            c = _cache_get() or {}
            flo_x_cached = c.get("flo_x")
            flo_y_cached = c.get("flo_y")

            try:
                if flo_x_cached is None:
                    flo_x_cached = seeds.make_seed_from_string(props.x_string, order=order)
                if flo_y_cached is None:
                    flo_y_cached = seeds.make_seed_from_string(props.y_string, order=order)
                _cache_set(
                    order=order,
                    x_string=props.x_string,
                    y_string=props.y_string,
                    flo_x=flo_x_cached,
                    flo_y=flo_y_cached,
                    flo_z=flo_z_new,
                )
            except Exception as e:
                msg = f"Error updating cached Z: {e}"
                self.report({'ERROR'}, msg)
                props.log_message = msg
                return False
            return True


        def _set_input_with_history(dst: str, new_text: str):
            if dst == "X":
                old = props.x_string
                props.x_prev_text = old
                props.x_next_text = ""
                props.x_string = new_text
            elif dst == "Y":
                old = props.y_string
                props.y_prev_text = old
                props.y_next_text = ""
                props.y_string = new_text
            else:
                old = props.z_string
                props.z_prev_text = old
                props.z_next_text = ""
                props.z_string = new_text

        if self.target == "X":
            cur = props.x_string
            prev = props.x_prev_text
            nxt = props.x_next_text
        elif self.target == "Y":
            cur = props.y_string
            prev = props.y_prev_text
            nxt = props.y_next_text
        else:
            cur = props.z_string
            prev = props.z_prev_text
            nxt = props.z_next_text

        # History actions
        if self.action == "BACK":
            if prev:
                _set_target_value(prev)
                _set_prev_next("", cur)
                if self.target != "Z" or _commit_z_cache_from_text(prev):
                    _rebuild_for_target()
            return {'FINISHED'}

        if self.action == "FORWARD":
            if nxt:
                _set_target_value(nxt)
                _set_prev_next(cur, "")
                if self.target != "Z" or _commit_z_cache_from_text(nxt):
                    _rebuild_for_target()
            return {'FINISHED'}

        if self.action in {"COPY_TO_X", "COPY_TO_Y"}:
            if self.target != "Z":
                msg = "Le azioni 'to X' / 'to Y' sono disponibili solo nel pannello Z."
                self.report({'ERROR'}, msg)
                props.log_message = msg
                return {'CANCELLED'}
            dst = "X" if self.action == "COPY_TO_X" else "Y"
            try:
                _set_input_with_history(dst, cur)
                bpy.ops.floret_mesh.build('INVOKE_DEFAULT')
            except Exception as e:
                msg = f"Error copying Z to {dst}: {e}"
                self.report({'ERROR'}, msg)
                props.log_message = msg
                return {'CANCELLED'}
            return {'FINISHED'}

        # CDIST non dipende dal testo corrente (cur) — genera un floretion “nuovo” dalla distanza dei centroidi.
        # Questo evita l'errore dopo Clear: non ha senso richiedere un input valido se non viene usato.
        if self.action == "CDIST":
            if self.target == "Z":
                msg = "CentroidDistance è disponibile solo per X e Y."
                self.report({'ERROR'}, msg)
                props.log_message = msg
                return {'CANCELLED'}
            try:
                pct = float(props.cd_pct)

                rel_raw = str(props.cd_relation or "<=").strip()
                rel_map = {
                    "<": "lt",
                    "<=": "le",
                    "≤": "le",
                    ">": "gt",
                    ">=": "ge",
                    "≥": "ge",
                    "=": "equal",
                    "==": "equal",
                    "equal": "equal",
                    "lt": "lt",
                    "le": "le",
                    "gt": "gt",
                    "ge": "ge",
                }
                relation = rel_map.get(rel_raw, "le")

                # coeff: float oppure "dist"
                cm = str(props.cd_coeff_mode or "dist")
                if cm == "dist":
                    coeff = "dist"
                elif cm == "const1":
                    coeff = 1.0
                else:
                    coeff = "dist"

                flo2 = flo_from_centroid_distance(
                    order=order,
                    pct=pct,
                    relation=relation,
                    coeff=coeff,
                )

                try:
                    new_text = flo2.as_floretion_notation()
                except Exception:
                    new_text = cur

                # Save history one-step
                _set_prev_next(cur, "")
                _set_target_value(new_text)
                _rebuild_for_target()

                return {'FINISHED'}

            except Exception as e:
                msg = f"Error applying CDIST to {self.target}: {e}"
                self.report({'ERROR'}, msg)
                props.log_message = msg
                return {'CANCELLED'}

        # Parse current (sempre Floretion)
        try:
            flo = seeds.make_seed_from_string(cur, order=order)
        except Exception as e:
            msg = f"Error parsing {self.target}: {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        # Apply transformations
        try:
            # IMPORTANTISSIMO: usa lo stile statico (come nei tuoi script render_sweep_iter_ops),
            # perché nella tua Floretion queste sono staticmethods.
            if self.action == "TRI":
                flo2 = Floretion.tri(flo)

            elif self.action == "ROT":
                flo2 = Floretion.rotate_coeffs(flo, shift=1)

            elif self.action == "PROJ_STRIP_GROW":
                # alcune versioni vogliono m=1, altre positional
                try:
                    flo2 = Floretion.proj_strip_grow(flo, m=1)
                except TypeError:
                    flo2 = Floretion.proj_strip_grow(flo, 1)

            elif self.action == "ROT_TRI":
                flo2 = Floretion.tri(Floretion.rotate_coeffs(flo, shift=1))

            elif self.action == "SQUARE":
                flo2 = flo * flo
                try:
                    flo2 = Floretion.normalize_coeffs(flo2, 2.0)
                except TypeError:
                    # fallback se la firma differisce
                    flo2 = flo2.normalize_coeffs(2.0)

            elif self.action == "NOT":
                try:
                    from lib.floretion_utils.elementwise_ops import elementwise_not
                except Exception:
                    elementwise_not = None

                if callable(elementwise_not):
                    flo2 = elementwise_not(flo)
                else:
                    fn_not = getattr(flo, "elementwise_not", None)
                    if callable(fn_not):
                        flo2 = fn_not()
                    else:
                        raise RuntimeError("elementwise_not non è disponibile nella libreria floretion corrente.")

            elif self.action == "CDIST":
                pct = float(props.cd_pct)

                # relation in lib.triangleize_utils.centroid_distance è: equal/le/lt/ge/gt
                rel_raw = str(props.cd_relation or "<=").strip()
                rel_map = {
                    "<": "lt",
                    "<=": "le",
                    "≤": "le",
                    ">": "gt",
                    ">=": "ge",
                    "≥": "ge",
                    "=": "equal",
                    "==": "equal",
                    "equal": "equal",
                    "lt": "lt",
                    "le": "le",
                    "gt": "gt",
                    "ge": "ge",
                }
                relation = rel_map.get(rel_raw, "le")

                # coeff: float oppure "dist"
                cm = str(props.cd_coeff_mode or "dist")
                coeff = "dist" if cm == "dist" else 1.0

                flo2 = flo_from_centroid_distance(
                    order=order,
                    pct=pct,
                    relation=relation,
                    coeff=coeff,
                )

            else:
                flo2 = flo

        except Exception as e:
            msg = f"Error applying {self.action} to {self.target}: {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        try:
            new_text = flo2.as_floretion_notation()
        except Exception:
            new_text = cur

        # Save history one-step
        _set_prev_next(cur, "")
        _set_target_value(new_text)

        if self.target != "Z" or _commit_z_cache_from_text(new_text):
            _rebuild_for_target()

        return {'FINISHED'}


class FLORET_MESH_OT_bitwise_op(Operator):
    bl_idname = "floret_mesh.bitwise_op"
    bl_label = "Build Bitwise Z"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(
        items=[
            ("XNOR", "XNOR", "Z = X.elementwise_xnor(Y)"),
            ("XOR",  "XOR",  "Z = X.elementwise_xor(Y)"),
            ("AND",  "AND",  "Z = X.elementwise_and(Y)"),
            ("OR",   "OR",   "Z = X.elementwise_or(Y)"),
            ("NAND", "NAND", "Z = X.elementwise_nand(Y)"),
            ("NOT_X","NOT X","Z = X.elementwise_not()"),
            ("NOT_Y","NOT Y","Z = Y.elementwise_not()"),
        ],
        name="Bitwise action",
        description="Bitwise/abelian operation used to build Z from X and Y",
        default="XOR",
    )

    def execute(self, context):
        scene = context.scene
        props: FloretionMeshSettings = scene.floretion_mesh_settings

        try:
            order = max(1, int(props.typical_order))
        except Exception:
            order = 1

        try:
            flo_x = seeds.make_seed_from_string(props.x_string, order=order)
        except Exception as e:
            msg = f"Error parsing X (order {order}): {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        try:
            flo_y = seeds.make_seed_from_string(props.y_string, order=order)
        except Exception as e:
            msg = f"Error parsing Y (order {order}): {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        if flo_x.flo_order != flo_y.flo_order:
            msg = (
                f"Order mismatch: X has order {flo_x.flo_order}, "
                f"Y has order {flo_y.flo_order}."
            )
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        try:
            from lib.floretion_utils.elementwise_ops import (
                elementwise_xnor,
                elementwise_xor,
                elementwise_and,
                elementwise_or,
                elementwise_not,
                elementwise_nand,
            )
        except Exception:
            elementwise_xnor = elementwise_xor = elementwise_and = None
            elementwise_or = elementwise_not = elementwise_nand = None

        def _fallback_instance_call(name: str, *args):
            fn = getattr(args[0], name, None)
            if callable(fn):
                return fn(*args[1:])
            raise RuntimeError(f"Bitwise op '{name}' is not available in the current floretion base library.")

        try:
            if self.action == "XNOR":
                flo_z = elementwise_xnor(flo_x, flo_y) if callable(elementwise_xnor) else _fallback_instance_call("elementwise_xnor", flo_x, flo_y)
                label = "X.elementwise_xnor(Y)"
            elif self.action == "XOR":
                flo_z = elementwise_xor(flo_x, flo_y) if callable(elementwise_xor) else _fallback_instance_call("elementwise_xor", flo_x, flo_y)
                label = "X.elementwise_xor(Y)"
            elif self.action == "AND":
                flo_z = elementwise_and(flo_x, flo_y) if callable(elementwise_and) else _fallback_instance_call("elementwise_and", flo_x, flo_y)
                label = "X.elementwise_and(Y)"
            elif self.action == "OR":
                flo_z = elementwise_or(flo_x, flo_y) if callable(elementwise_or) else _fallback_instance_call("elementwise_or", flo_x, flo_y)
                label = "X.elementwise_or(Y)"
            elif self.action == "NAND":
                flo_z = elementwise_nand(flo_x, flo_y) if callable(elementwise_nand) else _fallback_instance_call("elementwise_nand", flo_x, flo_y)
                label = "X.elementwise_nand(Y)"
            elif self.action == "NOT_X":
                flo_z = elementwise_not(flo_x) if callable(elementwise_not) else _fallback_instance_call("elementwise_not", flo_x)
                label = "X.elementwise_not()"
            elif self.action == "NOT_Y":
                flo_z = elementwise_not(flo_y) if callable(elementwise_not) else _fallback_instance_call("elementwise_not", flo_y)
                label = "Y.elementwise_not()"
            else:
                raise RuntimeError(f"Unsupported bitwise action: {self.action}")
        except Exception as e:
            msg = f"Error computing bitwise Z ({self.action}): {e}"
            self.report({'ERROR'}, msg)
            props.log_message = msg
            return {'CANCELLED'}

        try:
            z_str = flo_z.as_floretion_notation()
        except Exception:
            z_str = "<error in as_floretion_notation()>"

        try:
            props.z_prev_text = props.z_string
            props.z_next_text = ""
        except Exception:
            pass

        props.z_string = z_str
        props.log_message = f"Built Z via {label}"

        try:
            _cache_set(
                order=order,
                x_string=props.x_string,
                y_string=props.y_string,
                flo_x=flo_x,
                flo_y=flo_y,
                flo_z=flo_z,
            )
        except Exception as e:
            msg = f"Bitwise Z computed, but cache update failed: {e}"
            self.report({'WARNING'}, msg)
            props.log_message = msg

        return _build_mesh_triplet(context, props, flo_x, flo_y, flo_z, op=self)


class FLORET_MESH_OT_select_coeff_range(Operator):
    bl_idname = "floret_mesh.select_coeff_range"
    bl_label = "Select by coeff range"
    bl_options = {'REGISTER', 'UNDO'}

    min_abs: bpy.props.FloatProperty(name="Min abs", default=0.0, min=0.0)
    max_abs: bpy.props.FloatProperty(name="Max abs", default=1.0, min=0.0)

    def execute(self, context):
        obj = context.object
        if obj is None or obj.type != 'MESH':
            return {'CANCELLED'}

        me = obj.data
        attr = me.attributes.get("face_coeff")
        if attr is None:
            return {'CANCELLED'}

        min_abs = float(self.min_abs)
        max_abs = float(self.max_abs)
        if max_abs < min_abs:
            min_abs, max_abs = max_abs, min_abs

        for poly in me.polygons:
            try:
                v = float(attr.data[poly.index].value)
            except Exception:
                v = 0.0
            a = abs(v)
            poly.select = (a >= min_abs and a <= max_abs)

        me.update()
        return {'FINISHED'}
