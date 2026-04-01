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

# Guardie / opzioni anti-jerk:
# - Per oggetti animati/deformati via Geometry Nodes (es. spin VG), il bbox center cambia continuamente.
# - Se il LookAt usa il bbox center, la camera rincorre quel centro deformato e comincia a "saltare".
# - Per i target principali usiamo quindi un centro STABILE basato sull'origine degli oggetti.
# - Il bbox resta utile solo per stimare ortho_scale e distanza del preset top-down.
_USE_STABLE_ORIGIN_CENTER_DEFAULT = True
_CENTER_EPS = 1.0e-8

# Fix centratura top-down:
# quando l'utente usa esplicitamente il preset top-down, vuole che la camera sia
# centrata VISIVAMENTE sul bbox del target, non necessariamente sull'origine dell'oggetto.
# Manteniamo quindi una flag di scena "nascosta" che forza il bbox center per il LookAt
# nei preset top-down, senza cambiare la UI.
_SCN_KEY_FORCE_BBOX_LOOKAT = "_flo_cam_force_bbox_lookat"


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

    mins = mathutils.Vector((float("inf"), float("inf"), float("inf")))
    maxs = mathutils.Vector((float("-inf"), float("-inf"), float("-inf")))
    any_ok = False

    for o in objs:
        try:
            corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
        except Exception:
            continue
        for v in corners:
            mins.x = min(mins.x, v.x)
            mins.y = min(mins.y, v.y)
            mins.z = min(mins.z, v.z)
            maxs.x = max(maxs.x, v.x)
            maxs.y = max(maxs.y, v.y)
            maxs.z = max(maxs.z, v.z)
            any_ok = True

    if not any_ok:
        return mathutils.Vector((0.0, 0.0, 0.0))
    return (mins + maxs) * 0.5


def _world_bbox_max_dim(objs: list[bpy.types.Object]) -> float:
    """Massima dimensione bbox combinata in world space."""
    if not objs:
        return 10.0

    mins = mathutils.Vector((float("inf"), float("inf"), float("inf")))
    maxs = mathutils.Vector((float("-inf"), float("-inf"), float("-inf")))
    any_ok = False

    for o in objs:
        try:
            corners = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
        except Exception:
            continue
        for v in corners:
            mins.x = min(mins.x, v.x)
            mins.y = min(mins.y, v.y)
            mins.z = min(mins.z, v.z)
            maxs.x = max(maxs.x, v.x)
            maxs.y = max(maxs.y, v.y)
            maxs.z = max(maxs.z, v.z)
            any_ok = True

    if not any_ok:
        return 10.0

    dims = maxs - mins
    return max(float(dims.x), float(dims.y), float(dims.z), 0.001)


def _resolve_names(*candidates: str) -> list[bpy.types.Object]:
    objs = []
    for nm in candidates:
        o = bpy.data.objects.get(nm)
        if o is not None:
            objs.append(o)
    return objs


def _get_target_objects(scene: bpy.types.Scene, lookat: str) -> list[bpy.types.Object]:
    """Mappa LookAt -> lista oggetti, con supporto tetra + fallback legacy."""
    if lookat == "X":
        return _resolve_names("Flo_X")
    if lookat == "Y":
        return _resolve_names("Flo_Y")
    if lookat == "XY":
        return _resolve_names("Flo_XY")
    if lookat == "ALL":
        objs = []
        objs.extend(_resolve_names("Flo_X"))
        objs.extend(_resolve_names("Flo_Y"))
        objs.extend(_resolve_names("Flo_XY"))
        return objs
    if lookat == "X_TET":
        return _resolve_names("Flo_X_tetra", "Flo_X_tet")
    if lookat == "Y_TET":
        return _resolve_names("Flo_Y_tetra", "Flo_Y_tet")
    if lookat == "XY_TET":
        return _resolve_names("Flo_XY_tetra", "Flo_XY_tet")
    return []


def _get_object_world_origin(obj: bpy.types.Object) -> mathutils.Vector:
    try:
        return obj.matrix_world.translation.copy()
    except Exception:
        try:
            return mathutils.Vector(obj.location)
        except Exception:
            return mathutils.Vector((0.0, 0.0, 0.0))


def _average_world_origins(objs: list[bpy.types.Object]) -> mathutils.Vector:
    if not objs:
        return mathutils.Vector((0.0, 0.0, 0.0))
    acc = mathutils.Vector((0.0, 0.0, 0.0))
    n = 0
    for o in objs:
        try:
            acc += _get_object_world_origin(o)
            n += 1
        except Exception:
            pass
    if n <= 0:
        return mathutils.Vector((0.0, 0.0, 0.0))
    return acc / float(n)


def _force_bbox_lookat(scene: bpy.types.Scene) -> bool:
    """True se il preset top-down ha chiesto esplicitamente centratura su bbox."""
    try:
        return bool(scene.get(_SCN_KEY_FORCE_BBOX_LOOKAT, False))
    except Exception:
        return False


def _set_force_bbox_lookat(scene: bpy.types.Scene, enabled: bool) -> None:
    try:
        if enabled:
            scene[_SCN_KEY_FORCE_BBOX_LOOKAT] = True
        else:
            if _SCN_KEY_FORCE_BBOX_LOOKAT in scene:
                del scene[_SCN_KEY_FORCE_BBOX_LOOKAT]
    except Exception:
        pass


def _should_use_stable_origin_center(scene: bpy.types.Scene, lookat: str, objs: list[bpy.types.Object]) -> bool:
    """Per target animati/deformati preferiamo un centro stabile basato sull'origine oggetto.

    Questo evita jerk/flipping quando il bbox cambia per spin / Geometry Nodes / deformazioni.

    Eccezione importante:
      - Se l'utente ha usato esplicitamente il preset top-down, vogliamo una centratura
        visiva corretta. In quel caso forziamo il bbox center.
    """
    if not objs:
        return False

    if _force_bbox_lookat(scene):
        return False

    # Per i target principali, flat o tetra, usare l'origine è molto più stabile del bbox.
    if lookat in {"X", "Y", "XY", "ALL", "X_TET", "Y_TET", "XY_TET"}:
        return _USE_STABLE_ORIGIN_CENTER_DEFAULT
    return False


def _get_target_center(scene: bpy.types.Scene, lookat: str, objs: list[bpy.types.Object]) -> mathutils.Vector:
    if _should_use_stable_origin_center(scene, lookat, objs):
        return _average_world_origins(objs)
    return _world_bbox_center(objs)


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
        _set_force_bbox_lookat(scene, False)
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
    center = _get_target_center(scene, lookat, objs) if objs else mathutils.Vector((0.0, 0.0, 0.0))

    try:
        if (empty.location - center).length > _CENTER_EPS:
            empty.location = center
    except Exception:
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
      - Se la camera sta guardando X/Y/XY/X_TET/Y_TET/XY_TET, vogliamo che continui a farlo
        senza dover ripremere i bottoni nel pannello Camera.

    Nota anti-jerk:
      - Per target deformati/animati via GN usiamo il centro basato sull'origine oggetto,
        non sul bbox. Così gli update del depsgraph non fanno "saltare" la camera.
      - Ma se il preset top-down ha forzato il bbox center, manteniamo quella scelta
        anche dopo i rebuild: è più importante la centratura visiva corretta.
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

    # Filtra: se nel depsgraph non ci sono update su oggetti interessanti, non facciamo nulla.
    dirty = False
    try:
        watched = {
            "Flo_X", "Flo_Y", "Flo_XY",
            "Flo_X_tetra", "Flo_Y_tetra", "Flo_XY_tetra",
            "Flo_X_tet", "Flo_Y_tet", "Flo_XY_tet",
        }
        for upd in getattr(depsgraph, "updates", []):
            id_ = getattr(upd, "id", None)
            if isinstance(id_, bpy.types.Object):
                nm = str(getattr(id_, "name", "") or "")
                if nm in watched or nm.startswith("Flo_"):
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
            ('X_TET', 'X_tet', 'Top-down su Flo_X_tetra'),
            ('Y_TET', 'Y_tet', 'Top-down su Flo_Y_tetra'),
            ('XY_TET', 'XY_tet', 'Top-down su Flo_XY_tetra'),
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

        # FIX:
        # Per il preset top-down vogliamo la centratura visiva corretta del contenuto.
        # Usiamo quindi il bbox center e diciamo anche al LookAt di continuare a usare il bbox
        # finché il preset resta attivo.
        bbox_center = _world_bbox_center(objs)
        max_dim = _world_bbox_max_dim(objs)

        # imposta ORTHO + scala basata sul bbox e setta lookat
        if props is not None:
            try:
                props.camera_use_ortho = True
                props.camera_ortho_scale = max_dim * 1.10
                props.camera_lookat = self.target
            except Exception:
                pass

        _set_force_bbox_lookat(scene, True)

        # Applica solo la lente prima di muovere la camera.
        apply_lens_from_props(scene)

        # Posiziona/riallinea esplicitamente target + track-to sul bbox center.
        empty = ensure_lookat_empty(scene)
        try:
            empty.location = bbox_center
        except Exception:
            pass
        _ensure_track_to_constraint(cam_obj, empty)

        # distanza (in ORTHO non cambia inquadratura, ma evita clipping)
        dist = max_dim * 2.0 + 1.0
        _topdown_position(cam_obj, bbox_center, dist)

        # Riapplica lookat una volta sola: ora userà ancora bbox center grazie alla flag di scena.
        apply_lookat_from_props(scene)

        return {'FINISHED'}
