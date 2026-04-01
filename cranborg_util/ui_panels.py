# cranborg_util/ui_panels.py

from __future__ import annotations

import bpy

from .ui_props import FloretionMeshSettings


def _draw_spin_vg_legend(box):
    sub = box.box()
    sub.label(text="Spin VGs legend", icon='INFO')
    col = sub.column(align=True)
    col.label(text="Speed Mode: 0=Uniform, 1=VG, 2=Coeff lin, 3=Coeff log")
    col.label(text="Set Position is in the Geometry Nodes modifier")
    col.label(text="On tetra: X / Y / XY chooses the stack target")



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

        row_tools_x = box_x.row(align=True)
        row_tools_x.label(text="Utility:")
        op = row_tools_x.operator("floret_mesh.transform_input", text="Xnot", icon='MOD_BOOLEAN')
        op.target = "X"
        op.action = "NOT"
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

        row_spin_x = box_x.row(align=True)
        op = row_spin_x.operator("floret_mesh.spin_vgs", text="Spin VGs", icon='FORCE_TURBULENCE')
        op.target = "X"
        _draw_spin_vg_legend(box_x)

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

        row_tools_y = box_y.row(align=True)
        row_tools_y.label(text="Utility:")
        op = row_tools_y.operator("floret_mesh.transform_input", text="Ynot", icon='MOD_BOOLEAN')
        op.target = "Y"
        op.action = "NOT"
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

        row_spin_y = box_y.row(align=True)
        op = row_spin_y.operator("floret_mesh.spin_vgs", text="Spin VGs", icon='FORCE_TURBULENCE')
        op.target = "Y"
        _draw_spin_vg_legend(box_y)

        # --------------------------------------------------
        # BITWISE OPS (abelian)
        # --------------------------------------------------
        box_bw = layout.box()
        box_bw.label(text="Bitwise ops (abelian)")

        row_bw1 = box_bw.row(align=True)
        op = row_bw1.operator("floret_mesh.bitwise_op", text="XxnorY")
        op.action = "XNOR"
        op = row_bw1.operator("floret_mesh.bitwise_op", text="XxorY")
        op.action = "XOR"
        op = row_bw1.operator("floret_mesh.bitwise_op", text="XandY")
        op.action = "AND"
        op = row_bw1.operator("floret_mesh.bitwise_op", text="XorY")
        op.action = "OR"

        row_bw2 = box_bw.row(align=True)
        op = row_bw2.operator("floret_mesh.bitwise_op", text="XnandY")
        op.action = "NAND"
        op = row_bw2.operator("floret_mesh.bitwise_op", text="Xnot")
        op.action = "NOT_X"
        op = row_bw2.operator("floret_mesh.bitwise_op", text="Ynot")
        op.action = "NOT_Y"

        # --------------------------------------------------
        # BUILD (Mesh construction / X·Y)
        # --------------------------------------------------
        box_build = layout.box()
        box_build.label(text="Mesh Construction / X·Y", icon='MESH_DATA')

        row_top = box_build.row(align=True)
        row_top.prop(props, "spacing", text="Spacing")
        row_top.prop(props, "height_mode", text="Height")

        row_scale = box_build.row(align=True)
        row_scale.prop(props, "max_height", text="Max height")
        row_scale.prop(props, "tile_area_scaling_mode", text="Tile area scaling")

        if str(props.height_mode or "flat").lower() == "coeff":
            row_coeff_h = box_build.row(align=True)
            row_coeff_h.prop(props, "coeff_height_scale_mode", text="Coeff height")
            row_coeff_h.prop(props, "coeff_height_clip", text="Clip")

        if props.use_tetrahedral and str(props.height_mode or "flat").lower() == "coeff":
            row_tetra_rad = box_build.row(align=True)
            row_tetra_rad.prop(props, "tetra_coeff_radial_mode", text="Tetra radial")
            row_tetra_rad.prop(props, "tetra_coeff_radial_amount", text="Amount")


        try:
            box_build.separator(factor=0.35)
        except Exception:
            box_build.separator()

        row_disp = box_build.row(align=True)
        row_disp.prop(props, "full_grid", text="Full grid")
        row_disp.prop(props, "include_labels", text="Labels")

        row_helpers = box_build.row(align=True)
        row_helpers.prop(props, "use_tetrahedral", text="Tetrahedral")
        row_helpers.prop(props, "show_centroids", text="Show centroids")
        row_helpers.prop(props, "show_curve", text="Show curve")

        try:
            box_build.separator(factor=0.35)
        except Exception:
            box_build.separator()

        row_ext0 = box_build.row(align=True)
        row_ext0.label(text="Extend")
        row_ext0.prop(props, "extend_level", text="Level")

        row_ext1 = box_build.row(align=True)
        row_ext1.prop(props, "extend_mesh", text="Extend Flo_mesh")
        row_ext1.prop(props, "extend_cent", text="Flo_cent")
        row_ext1.prop(props, "extend_curve", text="Flo_curve")

        try:
            box_build.separator(factor=0.35)
        except Exception:
            box_build.separator()

        row3 = box_build.row(align=True)
        row3.prop(props, "color_family", text="Color mode")

        row3b = box_build.row(align=True)
        fam = str(getattr(props, "color_family", "STATIC") or "STATIC")
        if fam == "NEIGHBOR":
            row3b.prop(props, "neighbor_color_mode", text="Neighbor mode")
        elif fam == "QUANTILE":
            row3b.prop(props, "quantile_color_mode", text="Quantile mode")
        else:
            row3b.prop(props, "static_color_mode", text="Static mode")

        row_quant = box_build.row()
        row_quant.label(
            text="Shader groups: Static / Neighbor / Quantile. Quantiles use abs(coeff).",
            icon='SHADING_RENDERED'
        )

        row_hint = box_build.row()
        row_hint.label(
            text="Neighbor counts use canonical tile adjacency, independent of tile scaling.",
            icon='INFO'
        )

        box_mask = box_build.box()
        box_mask.label(text="Vertex Group Extrusion")

        row_mat_info = box_mask.row()
        row_mat_info.label(
            text="VG materials are kept as datablocks; display uses the main shader.",
            icon='INFO'
        )

        row_tools = box_mask.row(align=True)
        row_tools.operator("floret_mesh.reset_vg_extrusion", text="Reset")

        def _vg_row(prop_color, prop_val, prop_walls, label_text):
            row = box_mask.row(align=True)

            sw = row.row(align=True)
            sw.enabled = False
            sw.scale_x = 0.7
            sw.prop(props, prop_color, text="")

            row.prop(props, prop_val, text=label_text)

            # un minimo di aria tra slider e checkbox
            spacer = row.row(align=True)
            spacer.scale_x = 0.35
            spacer.label(text="")

            row.prop(props, prop_walls, text="Walls")

        _vg_row("vg_color_0", "mask_bin_0", "vg_wall_0", "0")
        _vg_row("vg_color_1", "mask_bin_1", "vg_wall_1", "1")
        _vg_row("vg_color_2", "mask_bin_2", "vg_wall_2", "2")
        _vg_row("vg_color_3", "mask_bin_3", "vg_wall_3", "3")
        _vg_row("vg_color_4", "mask_bin_4", "vg_wall_4", "4")
        _vg_row("vg_color_5", "mask_bin_5", "vg_wall_5", "5")
        _vg_row("vg_color_6", "mask_bin_6", "vg_wall_6", "6")
        _vg_row("vg_color_7p", "mask_bin_7p", "vg_wall_7p", "7+")
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

        row_ops_z = box_z.row(align=True)
        row_ops_z.label(text="Z ops:")

        op = row_ops_z.operator("floret_mesh.transform_input", text="Tri")
        op.target = "Z"
        op.action = "TRI"

        op = row_ops_z.operator("floret_mesh.transform_input", text="Rot")
        op.target = "Z"
        op.action = "ROT"

        op = row_ops_z.operator("floret_mesh.transform_input", text="ProjStripGrow")
        op.target = "Z"
        op.action = "PROJ_STRIP_GROW"

        op = row_ops_z.operator("floret_mesh.transform_input", text="Rot-Tri")
        op.target = "Z"
        op.action = "ROT_TRI"

        op = row_ops_z.operator("floret_mesh.transform_input", text="Square")
        op.target = "Z"
        op.action = "SQUARE"

        row_hist_z = box_z.row(align=True)
        row_hist_z.label(text="History:")

        op = row_hist_z.operator("floret_mesh.transform_input", text="Back")
        op.target = "Z"
        op.action = "BACK"

        op = row_hist_z.operator("floret_mesh.transform_input", text="Forward")
        op.target = "Z"
        op.action = "FORWARD"

        row_tools_z = box_z.row(align=True)
        row_tools_z.label(text="Utility:")
        op = row_tools_z.operator("floret_mesh.transform_input", text="Znot", icon='MOD_BOOLEAN')
        op.target = "Z"
        op.action = "NOT"
        op = row_tools_z.operator("floret_mesh.transform_input", text="to X", icon='TRIA_LEFT')
        op.target = "Z"
        op.action = "COPY_TO_X"
        op = row_tools_z.operator("floret_mesh.transform_input", text="to Y", icon='TRIA_RIGHT')
        op.target = "Z"
        op.action = "COPY_TO_Y"

        row_spin_z = box_z.row(align=True)
        op = row_spin_z.operator("floret_mesh.spin_vgs", text="Spin VGs", icon='FORCE_TURBULENCE')
        op.target = "Z"
        _draw_spin_vg_legend(box_z)

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

        row = col.row(align=True)
        op = row.operator("floret_mesh.camera_view", text="X_tet")
        if op is not None:
            op.target = 'X_TET'
        op = row.operator("floret_mesh.camera_view", text="Y_tet")
        if op is not None:
            op.target = 'Y_TET'

        row = col.row(align=True)
        op = row.operator("floret_mesh.camera_view", text="XY_tet")
        if op is not None:
            op.target = 'XY_TET'

