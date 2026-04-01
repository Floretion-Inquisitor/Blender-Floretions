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


_VG_UI_PALETTE = {
    "0":  (0.10, 0.10, 0.10, 1.0),
    "1":  (0.10, 0.25, 0.95, 1.0),
    "2":  (0.10, 0.75, 0.85, 1.0),
    "3":  (0.15, 0.85, 0.20, 1.0),
    "4":  (0.95, 0.85, 0.15, 1.0),
    "5":  (0.95, 0.55, 0.10, 1.0),
    "6":  (0.95, 0.15, 0.15, 1.0),
    "7p": (0.80, 0.15, 0.85, 1.0),
}


# -------------------------------------------------------------------
# Guardie anti-cascata update
# -------------------------------------------------------------------

_BULK_UPDATES = 0


def _bulk_updates_on() -> None:
    global _BULK_UPDATES
    _BULK_UPDATES += 1


def _bulk_updates_off() -> None:
    global _BULK_UPDATES
    _BULK_UPDATES = max(0, int(_BULK_UPDATES) - 1)


def _is_bulk_updating() -> bool:
    return bool(_BULK_UPDATES > 0)


# ---------------------------------------------------------------------
# Camera (LookAt + Lens) — NON triggera rebuild
# ---------------------------------------------------------------------

def _update_camera_settings(self, context):
    """Aggiorna LookAt e Lens della camera senza spostarla e senza rebuild."""
    if _is_bulk_updating():
        return
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


_COLOR_RECOLOR_SCHEDULED = False


def _trigger_recolor_only_safe():
    """Aggiorna solo i colori/materiale usando la cache, senza rifare la mesh."""
    global _COLOR_RECOLOR_SCHEDULED
    if _COLOR_RECOLOR_SCHEDULED:
        return
    _COLOR_RECOLOR_SCHEDULED = True

    def _run():
        global _COLOR_RECOLOR_SCHEDULED
        _COLOR_RECOLOR_SCHEDULED = False
        try:
            from .ops_build_core import refresh_colors_from_cache
            scene = getattr(bpy.context, "scene", None)
            props = getattr(scene, "floretion_mesh_settings", None) if scene is not None else None
            ok = False
            if props is not None:
                ok = bool(refresh_colors_from_cache(bpy.context, props))
            if not ok:
                _trigger_cached_rebuild_safe()
        except Exception as e:
            print("[floretion_triangle_mesh] recolor-only failed, fallback rebuild:", e)
            _trigger_cached_rebuild_safe()
        return None

    try:
        bpy.app.timers.register(_run, first_interval=0.03)
    except Exception:
        try:
            _run()
        except Exception:
            pass


def _update_color_mode(self, context):
    """Cambio Color mode: prova un refresh colori/materiale senza rebuild mesh."""
    if _is_bulk_updating():
        return
    _trigger_recolor_only_safe()


def _update_mesh_settings(self, context):
    """Callback per le proprietà di Mesh Construction (NO recompute X·Y).

    Idea: questi slider/dropdown devono solo aggiornare la *visualizzazione*
    (mesh/materiali) usando la cache dell'ultimo X·Y calcolato, se esiste.
    """
    if _is_bulk_updating():
        return
    _trigger_cached_rebuild_safe()


def _update_vg_materials_toggle(self, context):
    """Compat legacy.

    Il display dell'add-on non usa più l'auto-assegnamento dei materiali VG:
    i colori neighbor/quantile passano dal materiale base + attributi shader.
    Manteniamo quindi questo toggle come no-op morbido e facciamo solo un
    refresh della mesh per ripulire eventuali vecchi material_index.
    """
    if _is_bulk_updating():
        return
    _trigger_cached_rebuild_safe()



# -------------------------------------------------------------------
# Callbacks di update
# -------------------------------------------------------------------

def _reset_mask_offsets_to_default(props) -> None:
    for nm in (
        "mask_bin_0", "mask_bin_1", "mask_bin_2", "mask_bin_3",
        "mask_bin_4", "mask_bin_5", "mask_bin_6", "mask_bin_7p",
    ):
        try:
            setattr(props, nm, 0.0)
        except Exception:
            pass


def _reset_vg_wall_modes_to_default(props) -> None:
    for nm in (
        "vg_wall_0", "vg_wall_1", "vg_wall_2", "vg_wall_3",
        "vg_wall_4", "vg_wall_5", "vg_wall_6", "vg_wall_7p",
    ):
        try:
            setattr(props, nm, False)
        except Exception:
            pass


def _reset_standard_materials_for_new_order() -> None:
    try:
        from .shader_neighbor_nodes import reset_floretion_materials_and_nodes
        reset_floretion_materials_and_nodes()
    except Exception as e:
        print("[floretion_triangle_mesh] material reset on order change failed:", e)


def _update_typical_order(self, context):
    """
    Quando cambia l'ordine:
    - resetta X e Y all'unit (1 e.e)
    - resetta i nomi typical (unit)
    - pulisce Z e log
    - resetta i controlli costosi/di extend ai default
    """
    order = max(1, int(self.typical_order))

    try:
        unit_flo = seeds.make_typical_seed(order, "unit")
        unit_str = unit_flo.as_floretion_notation()
    except Exception as e:
        unit_str = f"1{'e' * order}"
        self.log_message = f"Error building unit floretion for order {order}: {e}"

    _bulk_updates_on()
    try:
        self.x_string = unit_str
        self.y_string = unit_str
        self.z_string = ""

        self.typical_name_x = "unit"
        self.typical_name_y = "unit"

        self.x_prev_text = ""
        self.x_next_text = ""
        self.y_prev_text = ""
        self.y_next_text = ""
        try:
            self.z_prev_text = ""
            self.z_next_text = ""
        except Exception:
            pass

        try:
            self.extend_level = "1"
        except Exception:
            pass

        for nm, val in (
            ("extend_mesh", False),
            ("extend_cent", False),
            ("extend_curve", False),
            ("show_centroids", False),
            ("show_curve", False),
            ("use_tetrahedral", False),
        ):
            try:
                setattr(self, nm, val)
            except Exception:
                pass

        try:
            self.extrusion_depth = 0.0
        except Exception:
            pass

        try:
            self.tile_area_scaling_mode = "none"
        except Exception:
            pass

        _reset_mask_offsets_to_default(self)
        _reset_vg_wall_modes_to_default(self)
    finally:
        _bulk_updates_off()

    _reset_standard_materials_for_new_order()
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

    z_prev_text: StringProperty(
        name="Z previous",
        description="Previous Z text for Back",
        default="",
    )
    z_next_text: StringProperty(
        name="Z next",
        description="Next Z text for Forward",
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

    extend_level: EnumProperty(
        name="Extend",
        description="Quante iterazioni di mirror outward applicare",
        items=[
            ("1", "1", "Una iterazione"),
            ("2", "2", "Due iterazioni"),
        ],
        default="1",
        update=_update_mesh_settings,
    )

    extend_mesh: BoolProperty(
        name="Extend Flo_mesh",
        description="Estende la mesh Flo_* con copie specchiate sui lati",
        default=False,
        update=_update_mesh_settings,
    )

    extend_cent: BoolProperty(
        name="Extend Flo_cent",
        description="Estende anche Flo_*_cent",
        default=False,
        update=_update_mesh_settings,
    )

    extend_curve: BoolProperty(
        name="Extend Flo_curve",
        description="Estende anche Flo_*_curve",
        default=False,
        update=_update_mesh_settings,
    )


    use_tetrahedral: BoolProperty(
        name="Tetrahedral",
        description="Crea anche la rappresentazione tetraedrica separata (Flo_*_tetra), mantenendo Flo_X / Flo_Y / Flo_X·Y come riferimento piatto",
        default=False,
        update=_update_mesh_settings,
    )

    vg_create_materials: BoolProperty(
        name="Create materials",
        description="Crea/rimuove automaticamente i materiali dei Vertex Groups nella sezione Vertex Group Extrusion",
        default=False,
        update=_update_vg_materials_toggle,
    )


    mask_bin_0: FloatProperty(
        name="0",
        description="Offset lineare per FLO_NEI_NEI_BOTH_0",
        default=0.0,
        min=-2.0,
        max=2.0,
        soft_min=-2.0,
        soft_max=2.0,
        update=_update_mesh_settings,
    )
    mask_bin_1: FloatProperty(name="1", description="Offset lineare per FLO_NEI_NEI_BOTH_1", default=0.0, min=-2.0, max=2.0, soft_min=-2.0, soft_max=2.0, update=_update_mesh_settings)
    mask_bin_2: FloatProperty(name="2", description="Offset lineare per FLO_NEI_NEI_BOTH_2", default=0.0, min=-2.0, max=2.0, soft_min=-2.0, soft_max=2.0, update=_update_mesh_settings)
    mask_bin_3: FloatProperty(name="3", description="Offset lineare per FLO_NEI_NEI_BOTH_3", default=0.0, min=-2.0, max=2.0, soft_min=-2.0, soft_max=2.0, update=_update_mesh_settings)
    mask_bin_4: FloatProperty(name="4", description="Offset lineare per FLO_NEI_NEI_BOTH_4", default=0.0, min=-2.0, max=2.0, soft_min=-2.0, soft_max=2.0, update=_update_mesh_settings)
    mask_bin_5: FloatProperty(name="5", description="Offset lineare per FLO_NEI_NEI_BOTH_5", default=0.0, min=-2.0, max=2.0, soft_min=-2.0, soft_max=2.0, update=_update_mesh_settings)
    mask_bin_6: FloatProperty(name="6", description="Offset lineare per FLO_NEI_NEI_BOTH_6", default=0.0, min=-2.0, max=2.0, soft_min=-2.0, soft_max=2.0, update=_update_mesh_settings)
    mask_bin_7p: FloatProperty(
        name="7+",
        description="Offset lineare per FLO_NEI_NEI_BOTH_7P",
        default=0.0,
        min=-2.0,
        max=2.0,
        soft_min=-2.0,
        soft_max=2.0,
        update=_update_mesh_settings,
    )


    vg_wall_0: BoolProperty(name="Walls 0", description="Se attivo, il VG 0 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_1: BoolProperty(name="Walls 1", description="Se attivo, il VG 1 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_2: BoolProperty(name="Walls 2", description="Se attivo, il VG 2 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_3: BoolProperty(name="Walls 3", description="Se attivo, il VG 3 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_4: BoolProperty(name="Walls 4", description="Se attivo, il VG 4 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_5: BoolProperty(name="Walls 5", description="Se attivo, il VG 5 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_6: BoolProperty(name="Walls 6", description="Se attivo, il VG 6 crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)
    vg_wall_7p: BoolProperty(name="Walls 7+", description="Se attivo, il VG 7+ crea pareti laterali invece di lasciare un foro", default=False, update=_update_mesh_settings)

    vg_color_0: FloatVectorProperty(name="VG 0 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["0"], min=0.0, max=1.0)
    vg_color_1: FloatVectorProperty(name="VG 1 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["1"], min=0.0, max=1.0)
    vg_color_2: FloatVectorProperty(name="VG 2 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["2"], min=0.0, max=1.0)
    vg_color_3: FloatVectorProperty(name="VG 3 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["3"], min=0.0, max=1.0)
    vg_color_4: FloatVectorProperty(name="VG 4 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["4"], min=0.0, max=1.0)
    vg_color_5: FloatVectorProperty(name="VG 5 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["5"], min=0.0, max=1.0)
    vg_color_6: FloatVectorProperty(name="VG 6 Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["6"], min=0.0, max=1.0)
    vg_color_7p: FloatVectorProperty(name="VG 7+ Color", subtype='COLOR', size=4, default=_VG_UI_PALETTE["7p"], min=0.0, max=1.0)


    create_vertex_groups: BoolProperty(
        name="Vertex groups",
        description="Vertex groups based on neighboring tiles",
        default=False,
        update=_update_mesh_settings,
    )
    
    
    height_mode: EnumProperty(
        name="Height mode",
        description="How to map floretion coefficients to tile heights",
        items=[
            ("flat", "Flat", "No height displacement"),
            ("coeff", "Coeff", "Height depends on coefficient sign/magnitude"),
            ("index", "Index", "Height depends on base vector index"),
        ],
        default="flat",
        update=_update_mesh_settings,
    )

    max_height: StringProperty(
        name="Max height",
        description="Maximum absolute height / tetra scale",
        default="2.0",
        update=_update_mesh_settings,
    )

    coeff_height_scale_mode: EnumProperty(
        name="Coeff height scale",
        description=(
            "Per Height=Coeff: i coefficienti vengono sempre normalizzati a max abs = 1, poi trasformati e clippati. "
            "Linear usa il coefficiente normalizzato; Log comprime gli outlier con sign(c) * log10(1+|c|)."
        ),
        items=[
            ("linear", "Linear", "Usa il coefficiente trasformato linearmente"),
            ("log", "Log", "Usa sign(c) * log10(1+|c|) per comprimere gli outlier"),
        ],
        default="linear",
        update=_update_mesh_settings,
    )

    coeff_height_clip: FloatProperty(
        name="Coeff height clip",
        description=(
            "Clamp finale del coefficiente usato per l'altezza dopo trasformazione/normalizzazione. "
            "1.0 = dentro [-1,1]. Utile per evitare esplosioni del viewport."
        ),
        default=1.0,
        min=0.05,
        soft_min=0.1,
        soft_max=2.0,
        max=10.0,
        update=_update_mesh_settings,
    )

    tile_area_scaling_mode: EnumProperty(
        name="Tile area scaling (relative)",
        description="Ridimensiona ogni tile attorno al proprio centroid mantenendo fermo il centro; i neighbor colors restano basati sulla geometria canonica",
        items=[
            ("none", "None", "Non scalare l'area dei tile"),
            ("coeff_abs", "Scale tile area by abs(coeff)", "Area relativa = abs(coeff); il lato scala come sqrt(abs(coeff))"),
            ("coeff_log", "Scale tile area by log(abs(coeff))", "Per abs(coeff) < 1 coincide col lineare; oltre 1 usa 1 + log10(abs(coeff))"),
        ],
        default="none",
        update=_update_mesh_settings,
    )

    tetra_coeff_radial_mode: EnumProperty(
        name="Tetra radial coeff scaling",
        description=(
            "Solo per Tetrahedral + Height=Coeff: moltiplica radialmente la distanza del tile dal tile unitario "
            "ee...e. 'Linear' usa direttamente il coefficiente (flip se negativo). "
            "'Log' coincide col lineare per abs(coeff) < 1 e usa sign(coeff) * (1 + log10(abs(coeff))) oltre 1."
        ),
        items=[
            ("none", "None", "Nessuna moltiplicazione radiale extra"),
            ("coeff", "Scale distance by coeff", "Scala la distanza dal tile unitario con il coefficiente"),
            ("coeff_log", "Scale distance by log(abs(coeff))", "Per abs(coeff) < 1 coincide col lineare; oltre 1 usa 1 + log10(abs(coeff)) con segno"),
        ],
        default="coeff",
        update=_update_mesh_settings,
    )

    tetra_coeff_radial_amount: FloatProperty(
        name="Tetra radial amount",
        description=(
            "Interpolazione 0..1 verso la scala radiale coeff-based. "
            "0 = nessun effetto, 1 = effetto completo. È animabile con keyframe."
        ),
        default=1.0,
        min=0.0,
        max=1.0,
        soft_min=0.0,
        soft_max=1.0,
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
            ('QUANTILE_2',       "Quantile 2 colors", "Divide abs(coeff) into 2 quantile buckets and color via shader palette"),
            ('QUANTILE_4',       "Quantile 4 colors", "Divide abs(coeff) into 4 quantile buckets and color via shader palette"),
            ('QUANTILE_8',       "Quantile 8 colors", "Divide abs(coeff) into 8 quantile buckets and color via shader palette"),

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
        update=_update_color_mode,
    )

    # Nuova interpretazione del pannello: famiglia colore + sottomodo.
    # Manteniamo anche `color_mode` legacy per compatibilità interna / file vecchi.
    color_family: EnumProperty(
        name="Color family",
        description="High-level color family used by the add-on panel",
        items=[
            ('STATIC',   "Static",         "Static coefficient-based palettes"),
            ('NEIGHBOR', "Neighbor based", "Topological neighbor-count based colors"),
            ('QUANTILE', "Quantile based", "Quantile palettes based on abs(coeff)"),
        ],
        default='STATIC',
        update=_update_color_mode,
    )

    static_color_mode: EnumProperty(
        name="Static colors",
        description="Static coefficient-based palette",
        items=[
            ('ABS_HSV',          "Abs HSV",          ""),
            ('LOG_HSV',          "Log HSV",          ""),
            ('DIVERGING',        "Diverging",        ""),
            ('GRAY',             "Gray",             ""),
            ('BANDED',           "Banded",           ""),
            ('LEGACY',           "Legacy",           ""),
            ('PASTEL',           "Pastel",           ""),
            ('PASTEL_DIVERGING', "Pastel Diverging", ""),
            ('COOLWARM',         "Coolwarm",         ""),
            ('HEAT',             "Heat",             ""),
            ('NEON',             "Neon",             ""),
            ('SAT_ONLY',         "Sat Only",         ""),
            ('DISTANCE_HSV',     "Distance HSV",     ""),
            ('BANDED_PASTEL',    "Banded Pastel",    ""),
            ('INK',              "Ink",              ""),
        ],
        default='ABS_HSV',
        update=_update_color_mode,
    )

    neighbor_color_mode: EnumProperty(
        name="Neighbor colors",
        description="Neighbor-based palette",
        items=[
            ('NEIGH_EDGE_HUE', "Edges",        "Color by count of edge-neighbors"),
            ('NEIGH_VERT_HUE', "Vertices",     "Color by count of vertex-only neighbors"),
            ('NEIGH_EDGE_SAT', "Edges+Verts",  "Color by count of edge OR vertex neighbors"),
        ],
        default='NEIGH_EDGE_SAT',
        update=_update_color_mode,
    )

    quantile_color_mode: EnumProperty(
        name="Quantile colors",
        description="Quantile palette based on abs(coeff)",
        items=[
            ('QUANTILE_2', "Quantile 2", ""),
            ('QUANTILE_4', "Quantile 4", ""),
            ('QUANTILE_8', "Quantile 8", ""),
        ],
        default='QUANTILE_8',
        update=_update_color_mode,
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
        description="Fissa la direzione della camera verso il centro di X / Y / X·Y / tetra senza spostare la camera",
        items=[
            ("NONE",   "None",   "Nessun vincolo LookAt"),
            ("X",      "X",      "Guarda il centro di Flo_X"),
            ("Y",      "Y",      "Guarda il centro di Flo_Y"),
            ("XY",     "X·Y",    "Guarda il centro di Flo_XY"),
            ("ALL",    "All",    "Guarda il centro combinato X/Y/X·Y"),
            ("X_TET",  "X_tet",  "Guarda il centro di Flo_X_tetra"),
            ("Y_TET",  "Y_tet",  "Guarda il centro di Flo_Y_tetra"),
            ("XY_TET", "XY_tet", "Guarda il centro di Flo_XY_tetra"),
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

    
    # vg_target: EnumProperty(
    #     name="VG Target",
    #     description="Su quali oggetti Flo_* creare i vertex groups",
    #     items=[
    #         ('ALL', "All", ""),
    #         ('X',   "X",   "Flo_X"),
    #         ('Y',   "Y",   "Flo_Y"),
    #         ('XY',  "X·Y", "Flo_XY"),
    #     ],
    #     default='X',
    # )

    # vg_clear_existing: BoolProperty(
    #     name="Clear existing",
    #     description="Rimuove prima i vertex groups FLO_NEI_* e pulisce eventuali FLO_BALL_* legacy",
    #     default=True,
    # )

    

 


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
