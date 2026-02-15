# cranborg_util/camera_ops.py

from __future__ import annotations

import bpy
from bpy.types import Operator
from bpy.props import EnumProperty
import mathutils
import time


# NOTA IMPORTANTE:
# Questo Empty serve come target stabile per il TRACK_TO della camera.
# Il reset automatico dell’add-on (ops_build_scene) elimina di default molti oggetti che iniziano con "Flo_".
# Se il target si chiama "Flo_*", durante alcuni rebuild (es. include labels / calcola X*Y) può venire rimosso,
# e la camera perde il vincolo e "guarda nel vuoto".
# Per evitare il problema, usiamo un nome che NON inizia con "Flo_".
OLD_LOOKAT_EMPTY_NAME = "Flo_LookAt"
LOOKAT_EMPTY_NAME = "FloLookAt"
LOOKAT_CONSTRAINT_NAME = "FLO__LookAt"


def _get_scene_props(scene):
    return getattr(scene, "floretion_mesh_settings", None)


def get_active_camera(scene: bpy.types.Scene) -> bpy.types.Object | None:
    """Ritorna la camera attiva se esiste, senza crearne una."""
    cam_obj = getattr(scene, "camera", None)
    if cam_obj and getattr(cam_obj, "type", None) == "CAMERA":
        return cam_obj

    for nm in ("Flo_Cam", "FloCam", "Camera"):
        o = bpy.data.objects.get(nm)
        if o and getattr(o, "type", None) == "CAMERA":
            return o

    for o in bpy.data.objects:
        if getattr(o, "type", None) == "CAMERA":
            return o

    return None


def ensure_active_camera(scene: bpy.types.Scene) -> bpy.types.Object:
    """Trova o crea una camera e la imposta come camera attiva della scena."""
    cam_obj = get_active_camera(scene)
    if cam_obj is not None:
        scene.camera = cam_obj
        return cam_obj

    cam_data = bpy.data.cameras.new("Flo_Cam")
    cam_obj = bpy.data.objects.new("Flo_Cam", cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    cam_obj.location = (0.0, -10.0, 10.0)
    cam_obj.rotation_euler = (1.2, 0.0, 0.0)
    return cam_obj


def ensure_lookat_empty(scene: bpy.types.Scene) -> bpy.types.Object:
    """Crea/riusa un Empty che rappresenta il punto look-at.

    Perché esiste:
      - La camera usa un vincolo TRACK_TO che punta a questo Empty.
      - In questo modo possiamo ricreare/muovere Flo_X/Flo_Y/Flo_XY a piacere senza dover
        puntare la camera direttamente a quegli oggetti (che spesso vengono ricreati).

    Bug che risolve:
      - Alcuni rebuild/reset dell’add-on rimuovono oggetti che iniziano con "Flo_".
        Se il target look-at si chiama "Flo_*", può sparire e la camera smette di seguire.
      - Qui usiamo un nome stabile che NON inizia con "Flo_" e facciamo anche migrazione
        da eventuale vecchio nome (OLD_LOOKAT_EMPTY_NAME).
    """
    def _ensure_linked(obj: bpy.types.Object):
        # Se non è linkato in nessuna collection, lo linkiamo alla root collection di scena.
        try:
            if not getattr(obj, "users_collection", None):
                scene.collection.objects.link(obj)
        except Exception:
            try:
                scene.collection.objects.link(obj)
            except Exception:
                pass

    # 1) preferisci il nome "nuovo"
    empty = bpy.data.objects.get(LOOKAT_EMPTY_NAME)
    if empty and empty.type == "EMPTY":
        _ensure_linked(empty)
        return empty

    # 2) migrazione: se esiste il vecchio Empty, rinominalo e riusalo
    old = bpy.data.objects.get(OLD_LOOKAT_EMPTY_NAME)
    if old and old.type == "EMPTY":
        try:
            old.name = LOOKAT_EMPTY_NAME
        except Exception:
            # se non si può rinominare (collisione, ecc.), lo usiamo comunque
            pass
        _ensure_linked(old)
        return old

    # 3) crea da zero
    empty = bpy.data.objects.new(LOOKAT_EMPTY_NAME, None)
    empty.empty_display_type = 'PLAIN_AXES'
    empty.empty_display_size = 0.25
    _ensure_linked(empty)
    return empty


def _world_bbox_center(objs: list[bpy.types.Object]) -> mathutils.Vector:
    """Centro bbox combinato in world space."""
    if not objs:
        return mathutils.Vector((0.0, 0.0, 0.0))

    min_v = mathutils.Vector((1e18, 1e18, 1e18))
    max_v = mathutils.Vector((-1e18, -1e18, -1e18))

    for obj in objs:
        try:
            bb = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
        except Exception:
            bb = [obj.matrix_world.translation]
        for v in bb:
            min_v.x = min(min_v.x, v.x)
            min_v.y = min(min_v.y, v.y)
            min_v.z = min(min_v.z, v.z)
            max_v.x = max(max_v.x, v.x)
            max_v.y = max(max_v.y, v.y)
            max_v.z = max(max_v.z, v.z)

    return (min_v + max_v) * 0.5


def _world_bbox_max_dim(objs: list[bpy.types.Object]) -> float:
    """Dimensione max bbox combinato (world)."""
    if not objs:
        return 1.0

    min_v = mathutils.Vector((1e18, 1e18, 1e18))
    max_v = mathutils.Vector((-1e18, -1e18, -1e18))

    for obj in objs:
        try:
            bb = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
        except Exception:
            bb = [obj.matrix_world.translation]
        for v in bb:
            min_v.x = min(min_v.x, v.x)
            min_v.y = min(min_v.y, v.y)
            min_v.z = min(min_v.z, v.z)
            max_v.x = max(max_v.x, v.x)
            max_v.y = max(max_v.y, v.y)
            max_v.z = max(max_v.z, v.z)

    size = max_v - min_v
    return max(float(size.x), float(size.y), float(size.z), 1e-6)


def _get_target_objects(scene: bpy.types.Scene, lookat: str) -> list[bpy.types.Object]:
    """Mappa LookAt -> lista oggetti."""
    if lookat == "X":
        names = ["Flo_X"]
    elif lookat == "Y":
        names = ["Flo_Y"]
    elif lookat == "XY":
        names = ["Flo_XY"]
    elif lookat == "ALL":
        names = ["Flo_X", "Flo_Y", "Flo_XY"]
    else:
        return []

    objs = []
    for nm in names:
        o = bpy.data.objects.get(nm)
        if o is not None:
            objs.append(o)
    return objs


def _ensure_track_to_constraint(cam_obj: bpy.types.Object, target_obj: bpy.types.Object) -> bpy.types.Constraint:
    """Aggiunge/aggiorna un TRACK_TO che punta al target."""
    con = cam_obj.constraints.get(LOOKAT_CONSTRAINT_NAME)
    if con is None:
        con = cam_obj.constraints.new(type='TRACK_TO')
        con.name = LOOKAT_CONSTRAINT_NAME

    con.target = target_obj
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    return con


def apply_lens_from_props(scene: bpy.types.Scene):
    """Applica tipo camera + ortho_scale/lens senza muovere la camera.

    Non crea camera: se non esiste, esce silenziosamente.
    """
    props = _get_scene_props(scene)
    if props is None:
        return

    cam_obj = get_active_camera(scene)
    if cam_obj is None:
        return
    cam = cam_obj.data

    if bool(getattr(props, "camera_use_ortho", True)):
        try:
            cam.type = 'ORTHO'
        except Exception:
            pass
        try:
            cam.ortho_scale = float(getattr(props, "camera_ortho_scale", 10.0))
        except Exception:
            pass
    else:
        try:
            cam.type = 'PERSP'
        except Exception:
            pass
        try:
            cam.lens = float(getattr(props, "camera_focal_length", 50.0))
        except Exception:
            pass


def apply_lookat_from_props(scene: bpy.types.Scene):
    """Applica LookAt: muove solo l'Empty e aggiorna TRACK_TO, NON muove la camera.

    Non crea camera: se non esiste, esce silenziosamente.
    """
    props = _get_scene_props(scene)
    if props is None:
        return

    cam_obj = get_active_camera(scene)
    if cam_obj is None:
        return

    lookat = str(getattr(props, "camera_lookat", "XY") or "XY")

    if lookat == "NONE":
        # Se non stiamo usando LookAt, rimuoviamo anche il depsgraph handler.
        _ensure_depsgraph_handler_unregistered()
        con = cam_obj.constraints.get(LOOKAT_CONSTRAINT_NAME)
        if con is not None:
            try:
                cam_obj.constraints.remove(con)
            except Exception:
                pass
        return

    # Registra il depsgraph handler: dopo rebuild vogliamo mantenere il LookAt.
    _ensure_depsgraph_handler_registered()

    empty = ensure_lookat_empty(scene)
    objs = _get_target_objects(scene, lookat)
    center = _world_bbox_center(objs) if objs else mathutils.Vector((0.0, 0.0, 0.0))
    empty.location = center

    _ensure_track_to_constraint(cam_obj, empty)


def apply_camera_from_props(scene: bpy.types.Scene):
    """Entry-point usato dagli update callback: LookAt + Lens."""
    apply_lens_from_props(scene)
    apply_lookat_from_props(scene)

# ---------------------------------------------------------------------------
# Persistenza LookAt (fix bug camera che "guarda nel vuoto" dopo rebuild)
# ---------------------------------------------------------------------------

# Throttling / guard: usiamo ID-properties su Scene per non fare loop durante update.
_SCN_KEY_LAST_UPDATE_T = "_flo_cam_last_update_t"
_SCN_KEY_IN_HANDLER = "_flo_cam_in_handler"


def _flo_cam_depsgraph_update_post(scene, depsgraph):
    """Handler leggero: se cambiano gli oggetti Flo_*, riposiziona il target look-at.

    Motivo:
      - Durante include labels / calcolo X*Y spesso vengono ricreati oggetti e/o label.
      - Se la camera sta guardando X/Y/XY, vogliamo che continui a farlo senza dover
        ripremere i bottoni nel pannello Camera.
    """
    props = _get_scene_props(scene)
    if props is None:
        return

    lookat = str(getattr(props, "camera_lookat", "XY") or "XY")
    if lookat == "NONE":
        return

    # Evita ricorsioni: spostare l'Empty genera a sua volta update.
    try:
        if bool(scene.get(_SCN_KEY_IN_HANDLER, False)):
            return
    except Exception:
        pass

    # Filtra: se nel depsgraph non ci sono aggiornamenti su oggetti "Flo_*" non facciamo nulla.
    dirty = False
    try:
        for upd in getattr(depsgraph, "updates", []):
            id_ = getattr(upd, "id", None)
            if isinstance(id_, bpy.types.Object):
                nm = str(getattr(id_, "name", "") or "")
                if nm in {"Flo_X", "Flo_Y", "Flo_XY"} or nm.startswith("Flo_"):
                    dirty = True
                    break
    except Exception:
        # se non riusciamo a leggere updates, meglio aggiornare comunque (ma con throttle)
        dirty = True

    if not dirty:
        return

    # Throttle: max ~8 aggiornamenti/sec
    now = time.monotonic()
    try:
        last = float(scene.get(_SCN_KEY_LAST_UPDATE_T, 0.0) or 0.0)
    except Exception:
        last = 0.0

    if (now - last) < 0.12:
        return

    try:
        scene[_SCN_KEY_LAST_UPDATE_T] = now
    except Exception:
        pass

    try:
        scene[_SCN_KEY_IN_HANDLER] = True
    except Exception:
        pass

    try:
        # Questo riposiziona l'Empty e ristabilisce il TRACK_TO se serve.
        apply_lookat_from_props(scene)
    finally:
        try:
            scene[_SCN_KEY_IN_HANDLER] = False
        except Exception:
            pass


def _ensure_depsgraph_handler_registered():
    """Registra il handler una sola volta (idempotente)."""
    try:
        handlers = bpy.app.handlers.depsgraph_update_post
        if _flo_cam_depsgraph_update_post not in handlers:
            handlers.append(_flo_cam_depsgraph_update_post)
    except Exception:
        pass


def _ensure_depsgraph_handler_unregistered():
    """Toglie il handler (se presente)."""
    try:
        handlers = bpy.app.handlers.depsgraph_update_post
        if _flo_cam_depsgraph_update_post in handlers:
            handlers.remove(_flo_cam_depsgraph_update_post)
    except Exception:
        pass




def _topdown_position(cam_obj: bpy.types.Object, center: mathutils.Vector, dist: float):
    cam_obj.location = (center.x, center.y, center.z + dist)


class FLORET_MESH_OT_camera_view(Operator):
    """Preset top-down: sposta la camera sopra il target e mette ORTHO.

    Nota: questo È l'unico punto in cui spostiamo la camera di proposito.
    Il LookAt invece NON sposta la camera.
    """

    bl_idname = "floret_mesh.camera_view"
    bl_label = "Camera Top-down View"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[
            ('X', 'X', 'Top-down su Flo_X'),
            ('Y', 'Y', 'Top-down su Flo_Y'),
            ('XY', 'X·Y', 'Top-down su Flo_XY'),
            ('ALL', 'All', 'Top-down su X/Y/X·Y'),
        ],
        default='XY',
    )

    def execute(self, context):
        scene = context.scene
        props = _get_scene_props(scene)

        cam_obj = ensure_active_camera(scene)

        objs = _get_target_objects(scene, self.target)
        if not objs:
            self.report({'WARNING'}, f"Nessun oggetto trovato per target {self.target}")
            return {'CANCELLED'}

        center = _world_bbox_center(objs)
        max_dim = _world_bbox_max_dim(objs)

        # imposta ORTHO + scala basata sul bbox e setta lookat
        if props is not None:
            try:
                props.camera_use_ortho = True
                props.camera_ortho_scale = max_dim * 1.10
                props.camera_lookat = self.target
            except Exception:
                pass

        # applica lens + lookat
        apply_camera_from_props(scene)

        # distanza (in ORTHO non cambia inquadratura, ma evita clipping)
        dist = max_dim * 2.0 + 1.0
        _topdown_position(cam_obj, center, dist)

        # riapplica lookat (nel caso la camera sia stata creata ora)
        apply_lookat_from_props(scene)

        return {'FINISHED'}
