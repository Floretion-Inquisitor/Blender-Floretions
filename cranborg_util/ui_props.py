# cranborg_util/ui_props.py

from __future__ import annotations

import bpy
from bpy.props import (
    StringProperty,
    IntProperty,
    EnumProperty,
    FloatProperty,
    BoolProperty,
    FloatVectorProperty
)

from . import seeds


# ---------------------------------------------------------------------
# Camera (LookAt + Lens) — NON triggera rebuild
# ---------------------------------------------------------------------

def _update_camera_settings(self, context):
    """Aggiorna LookAt e Lens della camera senza spostarla e senza rebuild."""
    try:
        from . import camera_ops
        scene = context.scene if context and getattr(context, "scene", None) else bpy.context.scene
        camera_ops.apply_camera_from_props(scene)
    except Exception:
        # Non vogliamo rompere l'UI se la camera non esiste o in fase di register
        pass



# -------------------------------------------------------------------
# Helper interno: rebuild "pesante" (ricalcola X·Y) — SOLO quando serve
# -------------------------------------------------------------------

def _trigger_rebuild_safe():
    """Richiama l’operatore di build senza far esplodere Blender in caso di errore."""
    import bpy
    try:
        bpy.ops.floret_mesh.build('INVOKE_DEFAULT')
    except Exception as e:
        print("[floretion_triangle_mesh] auto rebuild failed:", e)


# -------------------------------------------------------------------
# Helper interno: rebuild "leggero" (usa cache, NON ricalcola X·Y)
# -------------------------------------------------------------------

_LIGHT_REBUILD_SCHEDULED = False

def _trigger_cached_rebuild_safe():
    """Richiama un rebuild mesh-only (se cache disponibile), coalescendo gli update.

    Questo evita la botta da ~30s quando tocchi solo colore/spacing/altezza.
    """
    global _LIGHT_REBUILD_SCHEDULED
    if _LIGHT_REBUILD_SCHEDULED:
        return
    _LIGHT_REBUILD_SCHEDULED = True

    def _run():
        global _LIGHT_REBUILD_SCHEDULED
        _LIGHT_REBUILD_SCHEDULED = False
        try:
            # rebuild_cached deve esistere in ops_build.py
            bpy.ops.floret_mesh.rebuild_cached('EXEC_DEFAULT')
        except Exception as e:
            print("[floretion_triangle_mesh] cached rebuild failed:", e)
        return None  # one-shot timer

    try:
        bpy.app.timers.register(_run, first_interval=0.05)
    except Exception:
        # fallback: prova immediato (meglio di niente)
        try:
            _run()
        except Exception:
            pass


def _update_mesh_settings(self, context):
    """Callback per le proprietà di Mesh Construction (NO recompute X·Y).

    Idea: questi slider/dropdown devono solo aggiornare la *visualizzazione*
    (mesh/materiali) usando la cache dell'ultimo X·Y calcolato, se esiste.
    """
    _trigger_cached_rebuild_safe()


# -------------------------------------------------------------------
# Callbacks di update
# -------------------------------------------------------------------

def _update_typical_order(self, context):
    """
    Quando cambia l'ordine:
    - resetta X e Y all'unit (1 e.e)
    - resetta i nomi typical (unit)
    - pulisce Z e log
    """
    order = max(1, int(self.typical_order))

    try:
        unit_flo = seeds.make_typical_seed(order, "unit")
        unit_str = unit_flo.as_floretion_notation()
    except Exception as e:
        unit_str = f"1{'e' * order}"
        self.log_message = f"Error building unit floretion for order {order}: {e}"

    self.x_string = unit_str
    self.y_string = unit_str
    self.z_string = ""

    self.typical_name_x = "unit"
    self.typical_name_y = "unit"

    self.x_prev_text = ""
    self.x_next_text = ""
    self.y_prev_text = ""
    self.y_next_text = ""

    _trigger_rebuild_safe()


def _update_typical_x(self, context):
    """
    Quando l'utente cambia il dropdown "select typical floretion X":
    - prende il typical di quell'ordine
    - aggiorna immediatamente il textbox X
    - resetta la history X
    """
    order = max(1, int(self.typical_order))
    name = self.typical_name_x or "unit"
    try:
        flo = seeds.make_typical_seed(order, name)
        self.x_string = flo.as_floretion_notation()
        self.x_prev_text = ""
        self.x_next_text = ""
    except Exception as e:
        self.log_message = f"Error getting typical X ({name}, order {order}): {e}"
    _trigger_rebuild_safe()


def _update_typical_y(self, context):
    """
    Idem per Y.
    """
    order = max(1, int(self.typical_order))
    name = self.typical_name_y or "unit"
    try:
        flo = seeds.make_typical_seed(order, name)
        self.y_string = flo.as_floretion_notation()
        self.y_prev_text = ""
        self.y_next_text = ""
    except Exception as e:
        self.log_message = f"Error getting typical Y ({name}, order {order}): {e}"
    _trigger_rebuild_safe()


# -------------------------------------------------------------------
# Property Group per le impostazioni del pannello
# -------------------------------------------------------------------

class FloretionMeshSettings(bpy.types.PropertyGroup):

    # include / escludi coeff = 0
    full_grid: BoolProperty(
        name="Full grid",
        description=(
            "If enabled, include base vectors with coeff = 0 "
            "(no gaps); if disabled, only non-zero coeffs"
        ),
        default=True,
        update=_update_mesh_settings,
    )

    include_labels: BoolProperty(
        name="Include labels",
        description="Show simple viewport labels (X / Y / X·Y). Not rendered.",
        default=False,
        update=_update_mesh_settings,
    )

    # Ordine unico per X e Y
    typical_order: IntProperty(
        name="Order",
        description="Floretion order (shared for X and Y)",
        default=2,
        min=1,
        max=10,
        update=_update_typical_order,
    )

    # Typical dropdown X / Y
    typical_name_x: EnumProperty(
        name="Typical X",
        description="Pick a typical floretion X for this order",
        items=[
            ("unit", "unit", ""),
            ("axis-I", "axis-I", ""),
            ("axis-J", "axis-J", ""),
            ("axis-K", "axis-K", ""),
            ("axis-IJ", "axis-IJ", ""),
            ("axis-JK", "axis-JK", ""),
            ("axis-KI", "axis-KI", ""),
            ("axis-IJK", "axis-IJK", ""),
            ("sierpinski-E", "sierpinski-E", ""),
            ("sierpinski-I", "sierpinski-I", ""),
            ("sierpinski-J", "sierpinski-J", ""),
            ("sierpinski-K", "sierpinski-K", ""),
        ],
        default="unit",
        update=_update_typical_x,
    )

    typical_name_y: EnumProperty(
        name="Typical Y",
        description="Pick a typical floretion Y for this order",
        items=[
            ("unit", "unit", ""),
            ("axis-I", "axis-I", ""),
            ("axis-J", "axis-J", ""),
            ("axis-K", "axis-K", ""),
            ("axis-IJ", "axis-IJ", ""),
            ("axis-JK", "axis-JK", ""),
            ("axis-KI", "axis-KI", ""),
            ("axis-IJK", "axis-IJK", ""),
            ("sierpinski-E", "sierpinski-E", ""),
            ("sierpinski-I", "sierpinski-I", ""),
            ("sierpinski-J", "sierpinski-J", ""),
            ("sierpinski-K", "sierpinski-K", ""),
        ],
        default="unit",
        update=_update_typical_y,
    )
    
    ui_color_x: FloatVectorProperty(
        name="X Color",
        description="Colore di riferimento per la sezione X",
        subtype='COLOR',
        size=3,
        default=(1.0, 0.2, 0.2),  # rosso-arancio
        min=0.0,
        max=1.0,
    )

    ui_color_y: FloatVectorProperty(
        name="Y Color",
        description="Colore di riferimento per la sezione Y",
        subtype='COLOR',
        size=3,
        default=(0.2, 1.0, 0.2),  # verde
        min=0.0,
        max=1.0,
    )

    ui_color_z: FloatVectorProperty(
        name="Z Color",
        description="Colore di riferimento per la sezione Z",
        subtype='COLOR',
        size=3,
        default=(0.2, 0.3, 1.0),  # blu
        min=0.0,
        max=1.0,
    )

    # Testo X / Y / Z
    x_string: StringProperty(
        name="X",
        description="Floretion X (can use Cn(.), Cp(.), Cb(.))",
        default="1ee",
    )

    y_string: StringProperty(
        name="Y",
        description="Floretion Y (can use Cn(.), Cp(.), Cb(.))",
        default="1ee",
    )

    z_string: StringProperty(
        name="Z = X·Y",
        description="Result floretion Z = X*Y",
        default="",
    )

    # History (undo / redo 1-step) per X e Y
    x_prev_text: StringProperty(
        name="X previous",
        description="Previous X text for Back",
        default="",
    )
    x_next_text: StringProperty(
        name="X next",
        description="Next X text for Forward",
        default="",
    )
    y_prev_text: StringProperty(
        name="Y previous",
        description="Previous Y text for Back",
        default="",
    )
    y_next_text: StringProperty(
        name="Y next",
        description="Next Y text for Forward",
        default="",
    )

    # Parametri di costruzione mesh
    spacing: StringProperty(
        name="Spacing",
        description="Distance between X, Y and Z meshes",
        default="6.0",
        update=_update_mesh_settings,
    )
 

    show_centroids: BoolProperty(
        name="Show centroids",
        description="Mostra i centroidi (helper) in viewport",
        default=False,
        update=_update_mesh_settings,
    )

    show_curve: BoolProperty(
        name="Show curve",
        description="Mostra la curva (helper) in viewport",
        default=False,
        update=_update_mesh_settings,
    )


    height_mode: EnumProperty(
        name="Height mode",
        description="How to map floretion coefficients to Z heights",
        items=[
            ("flat", "Flat", "No Z displacement"),
            ("coeff", "Coeff", "Height depends on coefficient sign/magnitude"),
            ("index", "Index", "Height depends on base vector index"),
        ],
        default="flat",
        update=_update_mesh_settings,
    )

    max_height: StringProperty(
        name="Max height",
        description="Maximum absolute height for coeff/index mapping",
        default="2.0",
        update=_update_mesh_settings,
    )

    # Colore (notare che i valori sono in stile ColorAdapter: ABS_HSV, ecc.)
    color_mode: EnumProperty(
        name="Color mode",
        description="Color mapping for coefficients",
        items=[
            # legacy IDs (adapter maps these to base coloring modes)
            ('ABS_HSV',          "Abs HSV",          ""),
            ('LOG_HSV',          "Log HSV",          ""),
            ('DIVERGING',        "Diverging",        ""),
            ('GRAY',             "Gray",             ""),
            ('BANDED',           "Banded",           ""),
            ('LEGACY',           "Legacy",           ""),

            # newer modes from lib.triangleize_utils.coloring (if your adapter supports them)
            ('PASTEL',           "Pastel",           ""),
            ('PASTEL_DIVERGING', "Pastel Diverging", ""),
            ('COOLWARM',         "Coolwarm",         ""),
            ('HEAT',             "Heat",             ""),
            ('NEON',             "Neon",             ""),
            ('SAT_ONLY',         "Sat Only",         ""),
            ('DISTANCE_HSV',     "Distance HSV",     ""),
            ('BANDED_PASTEL',    "Banded Pastel",    ""),
            ('INK',              "Ink",              ""),

            # neighbors (topologico): colore in base al numero di tile non-zero adiacenti
            ('NEIGH_EDGE_HUE',    "Neighbors (Edges)",       "Color by count of edge-neighbors (non-zero tiles)"),
            ('NEIGH_VERT_HUE',    "Neighbors (Vertices)",    "Color by count of vertex-only neighbors (share a vertex but not an edge)"),
            ('NEIGH_EDGE_SAT',    "Neighbors (Edges+Verts)", "Color by count of neighbors sharing an edge OR a vertex (non-zero tiles)"),
        ],
        default='ABS_HSV',
        update=_update_mesh_settings,
    )

    # Emission e spessore "skyline"
    emission_strength: FloatProperty(
        name="Emission",
        description="Emission strength multiplier for the triangles material",
        default=20.0,
        min=0.0,
        soft_max=200.0,
        update=_update_mesh_settings,
    )

    extrusion_depth: FloatProperty(
        name="Extrusion depth",
        description="Thickness for skyline-style extrusion (Solidify modifier)",
        default=0.0,
        min=0.0,
        soft_max=10.0,
        update=_update_mesh_settings,
    )

    # Centroid distance builder params (X/Y)
    cd_relation: EnumProperty(
        name="Relation",
        description="Relation for flo_from_centroid_distance",
        items=[
            ("<",  "<",  "Keep coeff where dist < pct"),
            (">",  ">",  "Keep coeff where dist > pct"),
        ],
        default="<",
    )

    cd_pct: FloatProperty(
        name="pct",
        description="Percent of max centroid distance",
        default=50.0,
        min=0.0,
        max=100.0,
    )

    cd_coeff_mode: EnumProperty(
        name="coeff mode",
        description="Coefficient assignment for flo_from_centroid_distance",
        items=[
            ("dist",   "dist",   "coeff = normalized distance"),
            ("const1", "1.0",    "coeff = 1.0"),
        ],
        default="dist",
    )


    # --------------------------------------------------
    # Camera: LookAt + Lens (non tocca la mesh)
    # --------------------------------------------------

    camera_lookat: EnumProperty(
        name="LookAt",
        description="Fissa la direzione della camera verso il centro di X / Y / X·Y senza spostare la camera",
        items=[
            ("NONE", "None", "Nessun vincolo LookAt"),
            ("X",    "X",    "Guarda il centro di Flo_X"),
            ("Y",    "Y",    "Guarda il centro di Flo_Y"),
            ("XY",   "X·Y",  "Guarda il centro di Flo_XY"),
            ("ALL",  "All",  "Guarda il centro combinato X/Y/X·Y"),
        ],
        default="XY",
        update=_update_camera_settings,
    )

    camera_use_ortho: BoolProperty(
        name="Orthographic",
        description="Se attivo usa camera ORTHO; altrimenti PERSPECTIVE",
        default=True,
        update=_update_camera_settings,
    )

    camera_ortho_scale: FloatProperty(
        name="Ortho Scale",
        description="Orthographic scale (usato quando Orthographic è attivo)",
        default=10.0,
        min=0.001,
        soft_max=1000.0,
        update=_update_camera_settings,
    )

    camera_focal_length: FloatProperty(
        name="Focal Length (mm)",
        description="Focal length in mm (usato quando Orthographic è disattivo)",
        default=50.0,
        min=1.0,
        soft_max=300.0,
        update=_update_camera_settings,
    )
    
    
    # --------------------------------------------------
    # Vertex Groups (Glow Masks) da NEI_BOTH
    # --------------------------------------------------
    vg_target: EnumProperty(
        name="VG Target",
        description="Su quali oggetti Flo_* creare i vertex groups",
        items=[
            ('ALL', "All", ""),
            ('X',   "X",   "Flo_X"),
            ('Y',   "Y",   "Flo_Y"),
            ('XY',  "X·Y", "Flo_XY"),
        ],
        default='X',
    )

    vg_clear_existing: BoolProperty(
        name="Clear existing",
        description="Rimuove prima i vertex groups FLO_NEI_* e pulisce eventuali FLO_BALL_* legacy",
        default=True,
    )

    

 


    # --------------------------------------------------
    # Weight Paint -> Bake (MVP)
    # --------------------------------------------------
    wp_max_coeff: FloatProperty(
        name="Max coeff",
        description="Coeff massimo corrispondente a peso 1.0 (MVP: solo positivo)",
        default=2.0,
        min=0.0,
        max=10.0,
    )

    wp_threshold: FloatProperty(
        name="Threshold",
        description="Sotto questa soglia il coeff viene ignorato (ripulisce il bake)",
        default=0.02,
        min=0.0,
        max=10.0,
    )

 

    # Log di errore / info
    log_message: StringProperty(
        name="Log",
        description="Messages and errors from the Floretion triangle mesh builder",
        default="",
    )
