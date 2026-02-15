# cranborg_util/ui_panels.py

from __future__ import annotations

import bpy

from .ui_props import FloretionMeshSettings


class FLORET_MESH_PT_panel(bpy.types.Panel):
    bl_label = "Floretion Triangle Mesh"
    bl_idname = "FLORET_MESH_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Floretion"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props: FloretionMeshSettings = scene.floretion_mesh_settings

        # --------------------------------------------------
        # ORDER (X e Y condividono lo stesso ordine)
        # --------------------------------------------------
        box_order = layout.box()
        box_order.label(text="Order (shared for X and Y):")
        box_order.prop(props, "typical_order", text="Order")

        # --------------------------------------------------
        # FLORETION X
        # --------------------------------------------------
        box_x = layout.box()

        # header X con swatch colore
        row_head_x = box_x.row(align=True)
        row_head_x.prop(props, "ui_color_x", text="")
        row_head_x.label(text="Floretion X")

        # Typical X + input stringa
        row = box_x.row()
        row.prop(props, "typical_name_x", text="Typical X")

        box_x.prop(props, "x_string", text="Input X")

        # Bottoni X ops
        row_ops_x = box_x.row(align=True)
        row_ops_x.label(text="X ops:")

        op = row_ops_x.operator(
            "floret_mesh.transform_input",
            text="Tri",
        )
        op.target = "X"
        op.action = "TRI"

        op = row_ops_x.operator(
            "floret_mesh.transform_input",
            text="Rot",
        )
        op.target = "X"
        op.action = "ROT"

        op = row_ops_x.operator(
            "floret_mesh.transform_input",
            text="ProjStripGrow",
        )
        op.target = "X"
        op.action = "PROJ_STRIP_GROW"

        op = row_ops_x.operator(
            "floret_mesh.transform_input",
            text="Rot-Tri",
        )
        op.target = "X"
        op.action = "ROT_TRI"

        op = row_ops_x.operator(
            "floret_mesh.transform_input",
            text="Square",
        )
        op.target = "X"
        op.action = "SQUARE"


        # Clear X (non fa rebuild/calcoli)
        op_clear = row_ops_x.operator("floret_mesh.clear_input", text="", icon="TRASH")
        if op_clear is not None:
            op_clear.target = "X"
        # History X (Back / Forward)
        row_hist_x = box_x.row(align=True)
        row_hist_x.label(text="History:")

        op = row_hist_x.operator(
            "floret_mesh.transform_input",
            text="Back",
        )
        op.target = "X"
        op.action = "BACK"

        op = row_hist_x.operator(
            "floret_mesh.transform_input",
            text="Forward",
        )
        op.target = "X"
        op.action = "FORWARD"

        # --- Centroid distance -> X ---
        box_cd_x = box_x.box()
        box_cd_x.label(text="Centroid distance → X (flo_from_centroid_distance)")

        row_cd_x = box_cd_x.row(align=True)
        row_cd_x.prop(props, "cd_relation", text="Relation")
        row_cd_x.prop(props, "cd_pct", text="% of max dist")
        row_cd_x.prop(props, "cd_coeff_mode", text="coeff")

        op = box_cd_x.operator("floret_mesh.transform_input", text="Apply to X")
        op.target = "X"
        op.action = "CDIST"

        # --------------------------------------------------
        # FLORETION Y
        # --------------------------------------------------
        box_y = layout.box()

        row_head_y = box_y.row(align=True)
        row_head_y.prop(props, "ui_color_y", text="")
        row_head_y.label(text="Floretion Y")

        row = box_y.row()
        row.prop(props, "typical_name_y", text="Typical Y")

        box_y.prop(props, "y_string", text="Input Y")

        # Bottoni Y ops
        row_ops_y = box_y.row(align=True)
        row_ops_y.label(text="Y ops:")

        op = row_ops_y.operator("floret_mesh.transform_input", text="Tri")
        op.target = "Y"
        op.action = "TRI"

        op = row_ops_y.operator("floret_mesh.transform_input", text="Rot")
        op.target = "Y"
        op.action = "ROT"

        op = row_ops_y.operator("floret_mesh.transform_input", text="ProjStripGrow")
        op.target = "Y"
        op.action = "PROJ_STRIP_GROW"

        op = row_ops_y.operator("floret_mesh.transform_input", text="Rot-Tri")
        op.target = "Y"
        op.action = "ROT_TRI"

        op = row_ops_y.operator("floret_mesh.transform_input", text="Square")
        op.target = "Y"
        op.action = "SQUARE"


        # Clear Y (non fa rebuild/calcoli)
        op_clear = row_ops_y.operator("floret_mesh.clear_input", text="", icon="TRASH")
        if op_clear is not None:
            op_clear.target = "Y"
        # History Y
        row_hist_y = box_y.row(align=True)
        row_hist_y.label(text="History:")

        op = row_hist_y.operator("floret_mesh.transform_input", text="Back")
        op.target = "Y"
        op.action = "BACK"

        op = row_hist_y.operator("floret_mesh.transform_input", text="Forward")
        op.target = "Y"
        op.action = "FORWARD"

        # --- Centroid distance -> Y ---
        box_cd_y = box_y.box()
        box_cd_y.label(text="Centroid distance → Y (flo_from_centroid_distance)")

        row_cd_y = box_cd_y.row(align=True)
        row_cd_y.prop(props, "cd_relation", text="Relation")
        row_cd_y.prop(props, "cd_pct", text="% of max dist")
        row_cd_y.prop(props, "cd_coeff_mode", text="coeff")

        op = box_cd_y.operator("floret_mesh.transform_input", text="Apply to Y")
        op.target = "Y"
        op.action = "CDIST"

        # --------------------------------------------------
        # BUILD (Mesh construction / X·Y)
        # --------------------------------------------------
        box_build = layout.box()
        box_build.label(text="Mesh Construction / X·Y")

        row = box_build.row(align=True)
        row.prop(props, "spacing", text="Spacing")
        row.prop(props, "height_mode", text="Height")

        # Max height su riga dedicata + un filo di spazio prima dei checkbox (più leggibile)
        box_build.prop(props, "max_height", text="Max height")
        try:
            box_build.separator(factor=0.35)
        except Exception:
            box_build.separator()

        row2 = box_build.row(align=False)
        row2.prop(props, "full_grid", text="Full grid (include coeff = 0)")

        row2b = box_build.row(align=True)
        row2b.prop(props, "show_centroids", text="Show centroids")
        row2b.prop(props, "show_curve", text="Show curve")

        try:
            row2.separator(factor=0.7)
        except Exception:
            pass
        row2.prop(props, "include_labels", text="Include labels")

        try:
            box_build.separator(factor=0.35)
        except Exception:
            box_build.separator()

        row3 = box_build.row(align=True)
        row3.prop(props, "color_mode", text="Color mode")

        row4 = box_build.row(align=True)
        row4.prop(props, "emission_strength", text="Emission")
        row4.prop(props, "extrusion_depth", text="Extrusion")

        box_build.operator(
            "floret_mesh.rebuild_cached",
            text="Rebuild Meshes (cached)",
        )

        box_build.operator(
            "floret_mesh.build",
            text="Calculate X·Y and Build Meshes",
        )

        # --------------------------------------------------
        # RISULTATO Z
        # --------------------------------------------------
        box_z = layout.box()
        row_head_z = box_z.row(align=True)
        row_head_z.prop(props, "ui_color_z", text="")
        row_head_z.label(text="Result Z = X·Y")

        box_z.prop(props, "z_string", text="")


        # ----------------------------------------
        # VERTEX GROUPS (Glow Masks) da NEI_BOTH
        # ----------------------------------------
        box_vg = layout.box()
        box_vg.label(text="Glow Masks (Vertex Groups)", icon='GROUP_VERTEX')

        row = box_vg.row(align=True)
        row.prop(props, "vg_target", text="Target")
        row.prop(props, "vg_clear_existing", text="Clear")

        row2 = box_vg.row(align=True)
        row2.operator("floret_mesh.make_nei_vertex_groups", text="Create NEI_BOTH groups")
        row2.operator("floret_mesh.remove_floretion_vertex_groups", text="Remove groups", icon='TRASH')



        # ----------------------------------------
        # WEIGHT PAINT -> BAKE (MVP)
        # ----------------------------------------
        box_wp = layout.box()
        box_wp.label(text="Weight Paint → Bake", icon='BRUSH_DATA')
        row_wp = box_wp.row(align=True)
        row_wp.prop(props, "wp_max_coeff")
        row_wp.prop(props, "wp_threshold")

        row_hint = box_wp.row()
        row_hint.label(text="Premi Calculate/Build prima (serve Flo_X/Flo_Y).", icon='INFO')

        col_wp = box_wp.column(align=True)

        row = col_wp.row(align=True)
        op = row.operator("floret_mesh.wp_setup", text="Setup WP X")
        if op is not None:
            op.target = 'X'
        op = row.operator("floret_mesh.wp_bake", text="Bake → X input")
        if op is not None:
            op.target = 'X'

        row = col_wp.row(align=True)
        op = row.operator("floret_mesh.wp_setup", text="Setup WP Y")
        if op is not None:
            op.target = 'Y'
        op = row.operator("floret_mesh.wp_bake", text="Bake → Y input")
        if op is not None:
            op.target = 'Y'


        # --------------------------------------------------
        # LOG
        # --------------------------------------------------
        box_log = layout.box()
        box_log.label(text="Log / Messages")
        box_log.prop(props, "log_message", text="")



class FLORET_MESH_PT_camera(bpy.types.Panel):
    """Camera: LookAt + Lens + preset Top-down."""

    bl_label = "Camera"
    bl_idname = "FLORET_MESH_PT_camera"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Floretion"
    bl_parent_id = "FLORET_MESH_PT_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.floretion_mesh_settings

        # LookAt: NON sposta la camera, solo direzione (TRACK_TO su Empty)
        box = layout.box()
        box.label(text="LookAt:")
        box.prop(props, "camera_lookat", text="")

        # Lens: toggle ortho/persp + valore relativo
        box_lens = layout.box()
        box_lens.label(text="Lens:")
        row = box_lens.row(align=True)
        row.prop(props, "camera_use_ortho", text="Orthographic")

        if bool(props.camera_use_ortho):
            box_lens.prop(props, "camera_ortho_scale", text="Ortho Scale")
        else:
            box_lens.prop(props, "camera_focal_length", text="Focal Length (mm)")

        # Preset top-down (questi POSSONO spostare la camera e metterla in ORTHO)
        col = layout.column(align=True)
        col.label(text="Top-down presets:")

        row = col.row(align=True)
        op = row.operator("floret_mesh.camera_view", text="X")
        if op is not None:
            op.target = 'X'
        op = row.operator("floret_mesh.camera_view", text="Y")
        if op is not None:
            op.target = 'Y'

        row = col.row(align=True)
        op = row.operator("floret_mesh.camera_view", text="X·Y")
        if op is not None:
            op.target = 'XY'
        op = row.operator("floret_mesh.camera_view", text="All")
        if op is not None:
            op.target = 'ALL'

