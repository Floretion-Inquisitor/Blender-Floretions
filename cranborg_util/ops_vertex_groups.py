# cranborg_util/ops_vertex_groups.py
from __future__ import annotations

import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, BoolProperty

ATTR_NAME_BOTH = "neighbors_edges_and_verts"  # FACE float (bin 0..7)
ATTR_NAME_BASE_DEC = "base_dec"               # FACE int (id tile)

PREFIX_NEI  = "FLO_NEI_"
PREFIX_BALL = "FLO_BALL_"   # legacy cleanup


def _clamp_bin(v: float) -> int:
    try:
        b = int(round(float(v)))
    except Exception:
        b = 0
    if b < 0:
        b = 0
    if b > 7:
        b = 7
    return b


def _remove_prefixed_groups(obj: bpy.types.Object, prefix: str) -> int:
    removed = 0
    for vg in list(obj.vertex_groups):
        if vg.name.startswith(prefix):
            try:
                obj.vertex_groups.remove(vg)
                removed += 1
            except Exception:
                pass
    return removed


def _ensure_vg(obj: bpy.types.Object, name: str):
    vg = obj.vertex_groups.get(name)
    if vg is None:
        vg = obj.vertex_groups.new(name=name)
    return vg


def _get_face_attr_bins(me: bpy.types.Mesh, attr_name: str) -> list[int] | None:
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return None
    n = len(me.polygons)
    out = [0] * n
    for i in range(n):
        try:
            out[i] = _clamp_bin(attr.data[i].value)
        except Exception:
            out[i] = 0
    return out


def _get_face_attr_ints(me: bpy.types.Mesh, attr_name: str) -> list[int] | None:
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return None
    n = len(me.polygons)
    out = [0] * n
    for i in range(n):
        try:
            out[i] = int(attr.data[i].value)
        except Exception:
            try:
                out[i] = int(round(float(attr.data[i].value)))
            except Exception:
                out[i] = 0
    return out


def _poly_center_local(me: bpy.types.Mesh, poly: bpy.types.MeshPolygon):
    """Centro faccia in local space (fallback se poly.center non disponibile)."""
    try:
        return poly.center  # Blender spesso lo espone già
    except Exception:
        pass

    # fallback: media vertici
    try:
        from mathutils import Vector
        if not poly.vertices:
            return Vector((0.0, 0.0, 0.0))
        acc = Vector((0.0, 0.0, 0.0))
        for vi in poly.vertices:
            acc += me.vertices[vi].co
        return acc / float(len(poly.vertices))
    except Exception:
        return (0.0, 0.0, 0.0)


def _build_base_dec_maps_from_main_faces(
    main_obj: bpy.types.Object,
    face_bins: list[int],
    face_base_decs: list[int],
):
    """
    Ritorna:
      - base_dec -> bin (scegliamo la faccia "top" per evitare che l'estrusione sporchi i bin)
      - base_dec -> center (centro della faccia top, usato per KD fallback sui centroidi)
    """
    me = main_obj.data
    bd_to_bin: dict[int, int] = {}
    bd_to_center = {}
    bd_to_best_z: dict[int, float] = {}

    for fi, poly in enumerate(me.polygons):
        if fi >= len(face_bins) or fi >= len(face_base_decs):
            break
        bd = int(face_base_decs[fi])
        if bd == 0:
            continue
        b = int(face_bins[fi])

        c = _poly_center_local(me, poly)
        try:
            z = float(c[2])
        except Exception:
            z = 0.0

        # scegli il "top face" (z massimo) per quel base_dec
        prev = bd_to_best_z.get(bd, None)
        if prev is None or z > prev:
            bd_to_best_z[bd] = z
            bd_to_bin[bd] = b
            bd_to_center[bd] = c

    return bd_to_bin, bd_to_center


def _create_nei_groups_on_mesh(obj: bpy.types.Object, face_bins: list[int]) -> int:
    """Crea 8 gruppi globali per bin usando i vertici delle facce nel bin."""
    me = obj.data
    bins_to_verts = [set() for _ in range(8)]

    for fi, poly in enumerate(me.polygons):
        if fi >= len(face_bins):
            break
        b = face_bins[fi]
        for vi in poly.vertices:
            bins_to_verts[b].add(vi)

    created = 0
    for b in range(8):
        name = f"{PREFIX_NEI}NEI_BOTH_{b}" if b < 7 else f"{PREFIX_NEI}NEI_BOTH_7P"
        vg = _ensure_vg(obj, name)
        verts = list(bins_to_verts[b])
        if verts:
            vg.add(verts, 1.0, 'REPLACE')
        created += 1

    return created


def _read_point_base_decs_if_present(cent_obj: bpy.types.Object) -> list[int] | None:
    """Se *_cent ha già base_dec su POINT (o fallback FACE), lo leggiamo."""
    me = cent_obj.data
    attr = me.attributes.get(ATTR_NAME_BASE_DEC)
    if attr is None:
        return None

    # preferisci POINT
    if attr.domain == "POINT" and len(attr.data) == len(me.vertices):
        out = []
        for i in range(len(me.vertices)):
            try:
                out.append(int(attr.data[i].value))
            except Exception:
                out.append(0)
        return out

    # fallback: se per qualche motivo è FACE ma coincide
    if attr.domain == "FACE":
        # centroid mesh tipicamente non ha faces; se ne ha e combacia, proviamo
        if len(me.polygons) == len(me.vertices) and len(attr.data) >= len(me.vertices):
            out = []
            for i in range(len(me.vertices)):
                try:
                    out.append(int(attr.data[i].value))
                except Exception:
                    out.append(0)
            return out

    return None


def _create_nei_groups_on_centroids(
    cent_obj: bpy.types.Object,
    *,
    bd_to_bin: dict[int, int],
    bd_to_center: dict[int, object],
) -> int:
    """
    Crea gli stessi 8 gruppi sui centroidi.
    Se *_cent non ha base_dec, usa KDTree su centers della mesh principale.
    """
    me = cent_obj.data
    n = len(me.vertices)
    if n == 0:
        return 0

    bins_to_verts = [[] for _ in range(8)]

    # 1) se abbiamo base_dec su POINT, usiamolo direttamente
    point_bds = _read_point_base_decs_if_present(cent_obj)
    if point_bds is not None:
        for vi, bd in enumerate(point_bds):
            b = bd_to_bin.get(int(bd), 0)
            if b < 0:
                b = 0
            if b > 7:
                b = 7
            bins_to_verts[b].append(vi)
    else:
        # 2) fallback robusto: nearest top-face center (KDTree)
        if not bd_to_center:
            # nessun dato: tutto in 0
            bins_to_verts[0] = list(range(n))
        else:
            try:
                from mathutils.kdtree import KDTree

                bd_items = list(bd_to_center.items())  # [(bd, center), ...]
                kd = KDTree(len(bd_items))
                for idx, (bd, c) in enumerate(bd_items):
                    # c può essere Vector o tuple
                    try:
                        kd.insert((float(c[0]), float(c[1]), float(c[2])), idx)
                    except Exception:
                        kd.insert((0.0, 0.0, 0.0), idx)
                kd.balance()

                for vi in range(n):
                    vco = me.vertices[vi].co
                    co = (float(vco[0]), float(vco[1]), float(vco[2]))
                    _, idx, _ = kd.find(co)
                    bd = int(bd_items[idx][0])
                    b = bd_to_bin.get(bd, 0)
                    if b < 0:
                        b = 0
                    if b > 7:
                        b = 7
                    bins_to_verts[b].append(vi)
            except Exception:
                # fallback finale: tutto in 0
                bins_to_verts[0] = list(range(n))

    created = 0
    for b in range(8):
        name = f"{PREFIX_NEI}NEI_BOTH_{b}" if b < 7 else f"{PREFIX_NEI}NEI_BOTH_7P"
        vg = _ensure_vg(cent_obj, name)
        verts = bins_to_verts[b]
        if verts:
            vg.add(verts, 1.0, 'REPLACE')
        created += 1

    return created


def _targets_from_choice(choice: str) -> list[str]:
    if choice == "X":
        return ["Flo_X"]
    if choice == "Y":
        return ["Flo_Y"]
    if choice == "XY":
        return ["Flo_XY"]
    return ["Flo_X", "Flo_Y", "Flo_XY"]


def _find_scene_object(context, name: str) -> bpy.types.Object | None:
    """Preferisci l'oggetto in scene/viewlayer, non un datablock 'fantasma'."""
    scn = context.scene
    obj = scn.objects.get(name)
    if obj:
        return obj
    obj = bpy.data.objects.get(name)
    return obj


def _find_scene_mesh(context, name: str) -> bpy.types.Object | None:
    obj = _find_scene_object(context, name)
    if obj and obj.type == "MESH":
        return obj
    return None


class FLORET_MESH_OT_make_nei_vertex_groups(Operator):
    bl_idname = "floret_mesh.make_nei_vertex_groups"
    bl_label = "Create NEI_BOTH Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[
            ("ALL", "All", ""),
            ("X",   "X (Flo_X)", ""),
            ("Y",   "Y (Flo_Y)", ""),
            ("XY",  "X·Y (Flo_XY)", ""),
        ],
        default="X",
    )

    clear_existing: BoolProperty(
        name="Clear existing",
        default=True,
        description="Rimuove prima i vertex groups FLO_NEI_* e pulisce eventuali FLO_BALL_* legacy",
    )

    apply_to_centroids: BoolProperty(
        name="Also apply to centroids",
        default=True,
        description="Crea gli stessi gruppi anche su Flo_*_cent",
    )

    def execute(self, context):
        props = getattr(context.scene, "floretion_mesh_settings", None)
        if props is not None:
            try:
                self.target = getattr(props, "vg_target", self.target)
                self.clear_existing = bool(getattr(props, "vg_clear_existing", self.clear_existing))
            except Exception:
                pass

        # assicurati di essere in Object Mode (UI + vertex groups più prevedibili)
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass

        targets = _targets_from_choice(self.target)
        last_obj = None
        any_done = False

        for obj_name in targets:
            obj = _find_scene_mesh(context, obj_name)
            if obj is None:
                self.report({'WARNING'}, f"Skip {obj_name}: non trovato o non è MESH.")
                continue

            me = obj.data
            face_bins = _get_face_attr_bins(me, ATTR_NAME_BOTH)
            if face_bins is None:
                self.report({'WARNING'}, f"{obj_name}: manca attribute FACE '{ATTR_NAME_BOTH}' (fai Build).")
                continue

            # MAIN MESH
            if self.clear_existing:
                _remove_prefixed_groups(obj, PREFIX_NEI)
                _remove_prefixed_groups(obj, PREFIX_BALL)

            created_main = _create_nei_groups_on_mesh(obj, face_bins)

            # CENTROIDS
            created_cent = 0
            cent_obj = None
            if self.apply_to_centroids:
                cent_name = f"{obj_name}_cent"  # es: Flo_X -> Flo_X_cent
                cent_obj = _find_scene_mesh(context, cent_name)

                if cent_obj is None:
                    # niente da fare; non è un errore bloccante
                    pass
                else:
                    face_bds = _get_face_attr_ints(me, ATTR_NAME_BASE_DEC)
                    if face_bds is None:
                        self.report({'WARNING'}, f"{obj_name}: manca attribute FACE '{ATTR_NAME_BASE_DEC}' (serve per centroid groups).")
                    else:
                        bd_to_bin, bd_to_center = _build_base_dec_maps_from_main_faces(obj, face_bins, face_bds)

                        if self.clear_existing:
                            _remove_prefixed_groups(cent_obj, PREFIX_NEI)
                            _remove_prefixed_groups(cent_obj, PREFIX_BALL)

                        created_cent = _create_nei_groups_on_centroids(
                            cent_obj,
                            bd_to_bin=bd_to_bin,
                            bd_to_center=bd_to_center,
                        )

            any_done = True
            last_obj = obj
            if created_cent > 0:
                self.report({'INFO'}, f"{obj_name}: creati {created_main} gruppi NEI su mesh + {created_cent} su centroids. VG mesh={len(obj.vertex_groups)} VG cent={len(cent_obj.vertex_groups) if cent_obj else 0}")
            else:
                self.report({'INFO'}, f"{obj_name}: creati {created_main} gruppi NEI (0..7+). Tot VG={len(obj.vertex_groups)}")

        # Rendilo visibile/subito trovabile: seleziona l'ultimo target lavorato (mesh principale)
        if last_obj is not None:
            try:
                for o in context.selected_objects:
                    o.select_set(False)
                last_obj.select_set(True)
                context.view_layer.objects.active = last_obj
            except Exception:
                pass

        return {'FINISHED'} if any_done else {'CANCELLED'}


class FLORET_MESH_OT_remove_floretion_vertex_groups(Operator):
    bl_idname = "floret_mesh.remove_floretion_vertex_groups"
    bl_label = "Remove FLO_NEI / FLO_BALL Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[
            ("ALL", "All", ""),
            ("X",   "X (Flo_X)", ""),
            ("Y",   "Y (Flo_Y)", ""),
            ("XY",  "X·Y (Flo_XY)", ""),
        ],
        default="ALL",
    )

    remove_from_centroids: BoolProperty(
        name="Also remove from centroids",
        default=True,
        description="Rimuove i gruppi anche da Flo_*_cent",
    )

    def execute(self, context):
        props = getattr(context.scene, "floretion_mesh_settings", None)
        if props is not None:
            try:
                self.target = getattr(props, "vg_target", self.target)
            except Exception:
                pass

        targets = _targets_from_choice(self.target)
        total = 0

        for obj_name in targets:
            obj = _find_scene_mesh(context, obj_name)
            if obj is not None:
                total += _remove_prefixed_groups(obj, PREFIX_NEI)
                total += _remove_prefixed_groups(obj, PREFIX_BALL)

            if self.remove_from_centroids:
                cent_obj = _find_scene_mesh(context, f"{obj_name}_cent")
                if cent_obj is not None:
                    total += _remove_prefixed_groups(cent_obj, PREFIX_NEI)
                    total += _remove_prefixed_groups(cent_obj, PREFIX_BALL)

        self.report({'INFO'}, f"Rimossi {total} vertex groups (FLO_NEI / FLO_BALL).")
        return {'FINISHED'}


classes = (
    FLORET_MESH_OT_make_nei_vertex_groups,
    FLORET_MESH_OT_remove_floretion_vertex_groups,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
