# cranborg_util/ops_clear_inputs.py
from __future__ import annotations

import bpy


class FLORET_MESH_OT_clear_input(bpy.types.Operator):
    """Svuota input X o Y SENZA lanciare rebuild/calcoli."""

    bl_idname = "floret_mesh.clear_input"
    bl_label = "Clear Input"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(
        name="Target",
        items=[
            ("X", "X", "Input X"),
            ("Y", "Y", "Input Y"),
        ],
        default="X",
    )

    def execute(self, context):
        props = getattr(context.scene, "floretion_mesh_settings", None)
        if props is None:
            self.report({'ERROR'}, "floretion_mesh_settings non trovato sulla scena.")
            return {'CANCELLED'}

        if self.target == "X":
            props.x_string = ""
        else:
            props.y_string = ""

        # opzionale: evita confusione sul risultato precedente
        try:
            props.z_string = ""
        except Exception:
            pass

        return {'FINISHED'}
