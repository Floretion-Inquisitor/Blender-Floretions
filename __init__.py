bl_info = {
    "name": "Floretion Triangle Mesh (Cranborg)",
    "author": "flore + contributors",
    "version": (0, 2, 4),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Floretion",
    "description": "Create triangle / centroid meshes from Floretions of any order",
    "category": "Object",
}

import bpy

from .cranborg_util.paths import bootstrap_sys_path
bootstrap_sys_path()

from .cranborg_util.ui_props import FloretionMeshSettings

from .cranborg_util.ops_build import (
    FLORET_MESH_OT_build,
    FLORET_MESH_OT_rebuild_cached,
    FLORET_MESH_OT_transform_input,
    FLORET_MESH_OT_select_coeff_range,
)

from .cranborg_util.ops_clear_inputs import FLORET_MESH_OT_clear_input

from .cranborg_util.camera_ops import (
    FLORET_MESH_OT_camera_view,
)

# ✅ Weight Paint operators (servono per far comparire/funcionare la sezione WP nel pannello)
from .cranborg_util.ops_weightpaint import (
    FLORET_MESH_OT_wp_setup,
    FLORET_MESH_OT_wp_bake,
)

from .cranborg_util.ui_panels import (
    FLORET_MESH_PT_panel,
    FLORET_MESH_PT_camera,
)

from .cranborg_util.ops_vertex_groups import (
    FLORET_MESH_OT_make_nei_vertex_groups,
    FLORET_MESH_OT_remove_floretion_vertex_groups,
) 
 

classes = (
    FloretionMeshSettings,

    # Operators
    FLORET_MESH_OT_build,
    FLORET_MESH_OT_rebuild_cached,
    FLORET_MESH_OT_transform_input,
    FLORET_MESH_OT_select_coeff_range,
    FLORET_MESH_OT_clear_input,
    FLORET_MESH_OT_camera_view,

    # ✅ Weight Paint Operators (MVP)
    FLORET_MESH_OT_wp_setup,
    FLORET_MESH_OT_wp_bake,
    
    # Vertex groups 
    FLORET_MESH_OT_make_nei_vertex_groups,
    FLORET_MESH_OT_remove_floretion_vertex_groups,
    
    # Panels
    FLORET_MESH_PT_panel,
    FLORET_MESH_PT_camera,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.floretion_mesh_settings = bpy.props.PointerProperty(
        type=FloretionMeshSettings
    )


def unregister():
    del bpy.types.Scene.floretion_mesh_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
