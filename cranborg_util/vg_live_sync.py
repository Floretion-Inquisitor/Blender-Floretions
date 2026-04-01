# cranborg_util/vg_live_sync.py
from __future__ import annotations

import bmesh
import bpy
from bpy.app.handlers import persistent

ATTR_NAME_BOTH = "neighbors_edges_and_verts"
SNAPSHOT_PROP = "floret_vg_base_mesh"
SNAPSHOT_PREFIX = "__FLO_VG_BASE__"
TARGET_OBJECTS = ("Flo_X", "Flo_Y", "Flo_XY")
MASK_PROP_NAMES = (
    "mask_bin_0", "mask_bin_1", "mask_bin_2", "mask_bin_3",
    "mask_bin_4", "mask_bin_5", "mask_bin_6", "mask_bin_7p",
)
WALL_PROP_NAMES = (
    "vg_wall_0", "vg_wall_1", "vg_wall_2", "vg_wall_3",
    "vg_wall_4", "vg_wall_5", "vg_wall_6", "vg_wall_7p",
)

_SYNC_RUNNING = False
_SYNC_SCHEDULED = False
_LAST_SYNC_SIG = None


def _mask_bin_offsets_from_props(props) -> list[float]:
    vals = []
    for nm in MASK_PROP_NAMES:
        try:
            vals.append(float(getattr(props, nm, 0.0)))
        except Exception:
            vals.append(0.0)
    while len(vals) < 8:
        vals.append(0.0)
    return vals[:8]


def _mask_bin_wall_flags_from_props(props) -> list[bool]:
    vals = []
    for nm in WALL_PROP_NAMES:
        try:
            vals.append(bool(getattr(props, nm, False)))
        except Exception:
            vals.append(False)
    while len(vals) < 8:
        vals.append(False)
    return vals[:8]


def _get_face_attr_bins(me: bpy.types.Mesh, attr_name: str) -> list[int] | None:
    attr = me.attributes.get(attr_name)
    if attr is None or attr.domain != "FACE":
        return None

    n = len(me.polygons)
    out = [0] * n
    for i in range(n):
        try:
            v = int(round(float(attr.data[i].value)))
        except Exception:
            v = 0
        out[i] = max(0, min(7, v))
    return out


def _copy_mesh_geometry(dst_me: bpy.types.Mesh, src_me: bpy.types.Mesh) -> None:
    bm = bmesh.new()
    try:
        bm.from_mesh(src_me)
        try:
            dst_me.clear_geometry()
        except Exception:
            pass
        bm.to_mesh(dst_me)
        dst_me.update()
        try:
            dst_me.update_gpu_tag()
        except Exception:
            pass
    finally:
        try:
            bm.free()
        except Exception:
            pass


def store_base_mesh_snapshot_for_object(obj: bpy.types.Object | None) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        return False

    snap_name = str(obj.get(SNAPSHOT_PROP, "") or "").strip()
    if not snap_name:
        snap_name = f"{SNAPSHOT_PREFIX}{obj.name}"
        try:
            obj[SNAPSHOT_PROP] = snap_name
        except Exception:
            pass

    snap_me = bpy.data.meshes.get(snap_name)
    if snap_me is None:
        snap_me = bpy.data.meshes.new(snap_name)

    try:
        snap_me.use_fake_user = True
    except Exception:
        pass

    _copy_mesh_geometry(snap_me, obj.data)
    return True


def _restore_base_mesh_for_object(obj: bpy.types.Object) -> bool:
    if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
        return False

    snap_name = str(obj.get(SNAPSHOT_PROP, "") or "").strip()
    if not snap_name:
        return False

    snap_me = bpy.data.meshes.get(snap_name)
    if snap_me is None:
        return False

    if getattr(obj.data, "is_editmode", False):
        return False

    _copy_mesh_geometry(obj.data, snap_me)
    return True


def _apply_vg_extrusions_to_mesh(me: bpy.types.Mesh, face_bins: list[int], offsets: list[float], wall_flags: list[bool]) -> None:
    """Stessa logica del build core, ma senza ricreare X*Y."""
    if me is None:
        return

    has_any_offset = any(abs(float(v)) > 1e-9 for v in (offsets or []))
    if not has_any_offset:
        return

    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()

        def _bin_of_face_index(fi: int) -> int:
            try:
                b = int(round(float(face_bins[fi])))
            except Exception:
                b = 0
            return max(0, min(7, b))

        faces_snapshot = list(bm.faces)
        for b in range(min(8, len(offsets))):
            dz = float(offsets[b])
            if abs(dz) <= 1e-12 or not (b < len(wall_flags) and wall_flags[b]):
                continue

            region_faces = []
            for fi, f in enumerate(faces_snapshot):
                if fi >= len(face_bins):
                    continue
                if _bin_of_face_index(fi) == b and f.is_valid:
                    region_faces.append(f)

            if not region_faces:
                continue

            try:
                ret = bmesh.ops.extrude_face_region(bm, geom=region_faces)
                geom_ex = ret.get("geom", [])
                verts_ex = [g for g in geom_ex if isinstance(g, bmesh.types.BMVert)]
                if verts_ex:
                    bmesh.ops.translate(bm, verts=verts_ex, vec=(0.0, 0.0, dz))
            except Exception as e:
                print(f"[WARN] wall extrusion failed for VG {b}: {e}")

        faces_snapshot = list(bm.faces)
        for b in range(min(8, len(offsets))):
            dz = float(offsets[b])
            if abs(dz) <= 1e-12 or (b < len(wall_flags) and wall_flags[b]):
                continue

            region_faces = []
            for fi, f in enumerate(faces_snapshot):
                if fi >= len(face_bins):
                    continue
                if _bin_of_face_index(fi) == b and f.is_valid:
                    region_faces.append(f)

            if not region_faces:
                continue

            try:
                boundary_edges = []
                region_face_set = set(region_faces)
                for f in region_faces:
                    for e in f.edges:
                        linked = [lf for lf in e.link_faces if lf in region_face_set and lf.is_valid]
                        if len(linked) == 1:
                            boundary_edges.append(e)

                if boundary_edges:
                    ret_edges = bmesh.ops.duplicate(bm, geom=list(dict.fromkeys(boundary_edges)))
                    dup_edges = [g for g in ret_edges.get("geom", []) if isinstance(g, bmesh.types.BMEdge)]
                    dup_verts = [g for g in ret_edges.get("geom", []) if isinstance(g, bmesh.types.BMVert)]
                    for e in dup_edges:
                        try:
                            e.smooth = False
                        except Exception:
                            pass
                    for v in dup_verts:
                        try:
                            v.select = False
                        except Exception:
                            pass

                geom = list(region_faces)
                for f in region_faces:
                    geom.extend(list(f.edges))
                    geom.extend(list(f.verts))
                ret = bmesh.ops.duplicate(bm, geom=list(dict.fromkeys(geom)))
                geom_dup = ret.get("geom", [])
                dup_verts = [g for g in geom_dup if isinstance(g, bmesh.types.BMVert)]
                valid_orig = [f for f in region_faces if f.is_valid]

                if dup_verts:
                    bmesh.ops.translate(bm, verts=dup_verts, vec=(0.0, 0.0, dz))
                if valid_orig:
                    bmesh.ops.delete(bm, geom=valid_orig, context='FACES')

            except Exception as e:
                print(f"[WARN] hole extrusion failed for VG {b}: {e}")

        bm.to_mesh(me)
        me.update()
        try:
            me.update_gpu_tag()
        except Exception:
            pass
    finally:
        try:
            bm.free()
        except Exception:
            pass


def _iter_target_mesh_objects(scene: bpy.types.Scene | None):
    if scene is None:
        return
    for name in TARGET_OBJECTS:
        obj = scene.objects.get(name)
        if obj is None:
            obj = bpy.data.objects.get(name)
        if obj is None or getattr(obj, "type", None) != "MESH" or getattr(obj, "data", None) is None:
            continue
        yield obj


def sync_vg_live_offsets(*, scene: bpy.types.Scene | None = None, force: bool = False) -> bool:
    global _SYNC_RUNNING, _LAST_SYNC_SIG

    if _SYNC_RUNNING:
        return False

    scene = scene or getattr(bpy.context, "scene", None)
    if scene is None:
        return False

    props = getattr(scene, "floretion_mesh_settings", None)
    if props is None:
        return False

    offsets = _mask_bin_offsets_from_props(props)
    wall_flags = _mask_bin_wall_flags_from_props(props)
    sig = (
        int(scene.as_pointer()),
        int(getattr(scene, "frame_current", 0)),
        tuple(round(float(v), 6) for v in offsets),
        tuple(bool(v) for v in wall_flags),
    )
    if (not force) and sig == _LAST_SYNC_SIG:
        return False

    changed = False
    _SYNC_RUNNING = True
    try:
        for obj in _iter_target_mesh_objects(scene):
            if getattr(obj, "mode", "OBJECT") == "EDIT":
                continue
            if not _restore_base_mesh_for_object(obj):
                continue

            face_bins = _get_face_attr_bins(obj.data, ATTR_NAME_BOTH)
            if face_bins:
                _apply_vg_extrusions_to_mesh(obj.data, face_bins, offsets, wall_flags)

            try:
                obj.data.update()
            except Exception:
                pass
            changed = True

        _LAST_SYNC_SIG = sig
        return changed
    finally:
        _SYNC_RUNNING = False


def request_live_sync(*, force: bool = False) -> None:
    global _SYNC_SCHEDULED

    if _SYNC_SCHEDULED:
        return
    _SYNC_SCHEDULED = True

    def _run():
        global _SYNC_SCHEDULED
        _SYNC_SCHEDULED = False
        try:
            sync_vg_live_offsets(force=force)
        except Exception as e:
            print("[floretion_triangle_mesh] live VG timer sync failed:", e)
        return None

    try:
        bpy.app.timers.register(_run, first_interval=0.02)
    except Exception:
        try:
            _run()
        except Exception:
            pass


@persistent
def _frame_change_post(scene, depsgraph):
    try:
        sync_vg_live_offsets(scene=scene, force=False)
    except Exception as e:
        print("[floretion_triangle_mesh] frame VG sync failed:", e)


def register_vg_live_sync_handlers() -> None:
    handlers = bpy.app.handlers.frame_change_post
    if _frame_change_post not in handlers:
        handlers.append(_frame_change_post)


def unregister_vg_live_sync_handlers() -> None:
    handlers = bpy.app.handlers.frame_change_post
    while _frame_change_post in handlers:
        handlers.remove(_frame_change_post)
