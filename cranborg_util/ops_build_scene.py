# cranborg_util/ops_build_scene.py

from __future__ import annotations

import bpy
from bpy.types import Operator

# ---------------------------------------------------------------------------
# Gestione SCENA / COLLECTION / MANIFEST
#
# “Cosa farei a mano in Blender?”
#   1) Creerei una Collection dedicata (tipo: Outliner → Right click → New Collection)
#      chiamata "Floretion Triangle Mesh" e la terrei sotto la Scene.
#   2) Metterei TUTTI gli oggetti generati dall’add-on dentro quella collection
#      (drag&drop / Move to Collection), così sono facili da trovare e pulire.
#   3) Per riconoscerli senza ambiguità, metterei un “tag” (custom property) su ogni oggetto
#      creato dall’add-on (Object Properties → Custom Properties).
#   4) Salverei in qualche posto “persistente” lo stato atteso (qui: su Scene come ID-property)
#      per poter capire se la scena è “mezza rotta” e serve un reset.
#   5) Se serve, creerei oggetti di testo in viewport (Text/FONT objects) come “etichette”
#      e li renderei non selezionabili e non renderizzabili.
# ---------------------------------------------------------------------------

MANAGED_COLLECTION_NAME = "Floretion Triangle Mesh"

# Tag “questa roba è gestita dall’add-on”
# A mano: Object → Custom Properties → aggiungi chiave "floret_mesh_managed" = True
_MANAGED_TAG = "floret_mesh_managed"

# Ruolo dell’oggetto (“LABEL”, ecc.), utile per debug/filtri
# A mano: aggiungi chiave "floret_mesh_role" = "LABEL" / "CENTROID" / ecc.
_MANAGED_ROLE = "floret_mesh_role"

# “Manifest” salvato sulla Scene (ID properties): lista di oggetti che l’add-on si aspetta esistano.
# A mano: non lo faresti davvero (scomodo), ma concettualmente è come una checklist persistente.
SCENE_MANIFEST_KEY = "floret_mesh_manifest"
SCENE_MANIFEST_VER = 1


def ensure_object_in_collection(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    """Link robusto: aggiunge obj a collection se non c'è già.

    Manuale in Blender:
      - Outliner: trascini l’oggetto dentro la collection (o Right click → Move to Collection).
      - Qui lo fa via API, ma “robusto” perché Blender ha cambiato più volte come funziona
        la membership sulle bpy_prop_collection.
    """
    if obj is None or collection is None:
        return

    # 1) prova membership (object)
    # A mano: “vedo se l’oggetto è già dentro la collection”
    try:
        if obj in collection.objects:
            return
    except Exception:
        pass

    # 2) prova membership (name) — alcune versioni lo supportano
    # A mano: “cerco l’oggetto per nome nella lista”
    try:
        if obj.name in collection.objects:
            return
    except Exception:
        pass

    # 3) link “best effort”
    # A mano: “se non c’è, lo metto dentro”
    try:
        collection.objects.link(obj)
    except Exception:
        pass


def ensure_floretion_collection(context) -> bpy.types.Collection:
    """Assicura che esista una Collection dedicata e che sia linkata alla Scene.

    Manuale in Blender:
      - Outliner → New Collection → rinomina in "Floretion Triangle Mesh"
      - Se la collection esiste ma non è sotto la Scene corrente, la “linki” sotto la root.
    """
    scene = getattr(context, "scene", None) or bpy.context.scene

    # Cerca la collection per nome nell’intero .blend
    coll = bpy.data.collections.get(MANAGED_COLLECTION_NAME)
    if coll is None:
        # Se non esiste, la crea
        coll = bpy.data.collections.new(MANAGED_COLLECTION_NAME)

    # link della collection sotto la root collection della scena (se non è già linkata)
    # Manuale: “metti la collection sotto Scene Collection”
    try:
        if coll not in scene.collection.children:
            scene.collection.children.link(coll)
    except Exception:
        # fallback name-based
        # Manuale: “controllo i nomi delle child collections”
        try:
            child_names = [c.name for c in scene.collection.children]
            if coll.name not in child_names:
                scene.collection.children.link(coll)
        except Exception:
            pass

    return coll


def _is_managed_obj(obj: bpy.types.Object) -> bool:
    """Riconosce oggetti gestiti dall'add-on.

    Manuale in Blender:
      - In Outliner filtreresti per prefisso "Flo_" oppure per custom property.
      - Questo helper fa proprio quello: decide se un oggetto è “nostro” oppure no.

    IMPORTANTISSIMO:
      - se è una CAMERA => NON è "managed"
        (perché una camera potresti volerla tenere, e soprattutto non deve triggerare reset).
    """
    if obj is None:
        return False

    # Regola hard: non toccare MAI le camere vere
    try:
        if obj.type == "CAMERA":
            return False
    except Exception:
        pass

    # 1) Se ha il tag custom, è managed
    try:
        if bool(obj.get(_MANAGED_TAG)):
            return True
    except Exception:
        pass

    # 2) Fallback per naming convention (legacy / scene sporche / import)
    # Manuale: “se si chiama Flo_ qualcosa, allora è roba dell’add-on”
    nm = str(getattr(obj, "name", "") or "")
    if nm.startswith("Flo_"):
        return True
    if nm.startswith("FloCam") or nm in {"FloCam"}:
        # Se per qualche ragione una camera fosse MESH/EMPTY con nome FloCam,
        # la consideriamo managed; se è CAMERA è già esclusa sopra.
        return True
    if nm.startswith("Flo_Label_") or nm in {"Flo_Label_X", "Flo_Label_Y", "Flo_Label_XY"}:
        return True
    return False


def _safe_remove_object(obj: bpy.types.Object) -> None:
    """Rimuove un oggetto in modo “non fragile”.

    Manuale in Blender:
      - selezioni l’oggetto → X → Delete
      - se qualcosa va storto (locked / errori), almeno lo nascondi.
    """
    if obj is None:
        return
    try:
        bpy.data.objects.remove(obj, do_unlink=True)  # delete + unlink da tutte le collections
    except Exception:
        # fallback: almeno nascondilo (così non rompe la viewport)
        try:
            obj.hide_set(True)
        except Exception:
            pass


def _ensure_unique_obj_data(obj: bpy.types.Object) -> None:
    """Se obj.data è condiviso (users > 1), copialo prima di scriverci sopra.

    Manuale in Blender:
      - Object Data Properties → vedi il numerino (es. “2”) accanto al datablock Mesh/Curve
      - premi il numerino per fare “Make Single User”
      - motivo: se modifichi il datablock condiviso, ti cambiano più oggetti insieme (bug “swap”/clone).
    """
    if obj is None:
        return
    data = getattr(obj, "data", None)
    if data is None:
        return
    try:
        if getattr(data, "users", 0) > 1:
            obj.data = data.copy()
    except Exception:
        pass


def _scene_manifest_get(scene) -> dict | None:
    """Legge il manifest dalla Scene e verifica versione/forma.

    Manuale in Blender:
      - questo è come avere una “nota” salvata nella scena con la lista
        di oggetti creati e il loro tipo.
    """
    try:
        m = scene.get(SCENE_MANIFEST_KEY)
    except Exception:
        return None
    if not isinstance(m, dict):
        return None
    if int(m.get("v", 0) or 0) != SCENE_MANIFEST_VER:
        return None
    return m


def _scene_manifest_set(scene, objects: list[bpy.types.Object]) -> None:
    """Scrive sulla Scene l’elenco degli oggetti attesi (name + type)."""
    payload = {"v": int(SCENE_MANIFEST_VER), "objects": []}
    for o in objects:
        if o is None:
            continue
        try:
            payload["objects"].append({"name": str(o.name), "type": str(o.type)})
        except Exception:
            continue
    try:
        # ID-property su Scene: persiste nel .blend
        scene[SCENE_MANIFEST_KEY] = payload
    except Exception as e:
        print("[floretion_triangle_mesh] manifest store failed:", e)


def _scene_manifest_clear(scene) -> None:
    """Cancella il manifest dalla Scene."""
    try:
        if SCENE_MANIFEST_KEY in scene:
            del scene[SCENE_MANIFEST_KEY]
    except Exception:
        pass


def _manifest_needs_reset(context, flo_coll: bpy.types.Collection) -> tuple[bool, str]:
    """Ritorna (True, reason) se lo stato risulta parziale/corrotto.

    Manuale in Blender (intuizione):
      - Se vedo “pezzi” della costruzione (centroidi/curve/mesh) ma non tutti,
        o con tipi sbagliati, o con datablock condivisi a caso → conviene cancellare e rifare.
      - Qui formalizziamo quella decisione con controlli ripetibili.
    """
    scene = getattr(context, "scene", None) or bpy.context.scene
    m = _scene_manifest_get(scene)

    # Se non c'è manifest ma vediamo roba Flo_* in giro, meglio ripulire.
    # Manuale: “vedo oggetti Flo_ ma non so che versione/asset siano → pulizia totale.”
    if m is None:
        try:
            for obj in bpy.data.objects:
                if _is_managed_obj(obj):
                    return True, "Manifest mancante ma oggetti Flo_* presenti (stato legacy/parziale)."
        except Exception:
            pass
        return False, ""

    # Se c'è manifest, controlla che ogni oggetto atteso esista e abbia il tipo giusto.
    expected = m.get("objects", []) or []
    for e in expected:
        try:
            n = str(e.get("name", ""))
            t = str(e.get("type", ""))
        except Exception:
            continue
        if not n:
            continue
        obj = bpy.data.objects.get(n)
        if obj is None:
            # Manuale: “manca un pezzo della costruzione → reset”
            return True, f"Oggetto mancante: {n}"
        if t and obj.type != t:
            # Manuale: “c’è un oggetto con quel nome ma non è del tipo giusto”
            return True, f"Tipo diverso per {n}: expected {t}, got {obj.type}"

    # Check datablock condivisi fra centroids (bug “swap”)
    # Manuale: “se X_cent e Y_cent condividono lo stesso Curve/Mesh datablock, editare uno rompe l’altro”
    def _shared(names: list[str]) -> tuple[bool, str]:
        seen = {}
        for n in names:
            o = bpy.data.objects.get(n)
            if o is None:
                continue
            d = getattr(o, "data", None)
            if d is None:
                continue
            # key stabile (pointer) per capire se due oggetti puntano allo stesso datablock
            key = d.as_pointer() if hasattr(d, "as_pointer") else id(d)
            if key in seen and seen[key] != n:
                return True, f"Datablock condiviso fra {seen[key]} e {n}"
            seen[key] = n
        return False, ""

    bad, why = _shared(["Flo_X_cent", "Flo_Y_cent", "Flo_XY_cent"])
    if bad:
        return True, why

    return False, ""


def _hard_reset_floretion_objects(
    context,
    flo_coll: bpy.types.Collection,
    op: Operator | None = None,
    *,
    reason: str = "",
) -> None:
    """Reset “duro”: cancella tutto ciò che è managed e pulisce datablock orfani.

    Manuale in Blender:
      1) Outliner: apri la collection "Floretion Triangle Mesh" → seleziona tutto → Delete
      2) Cerca eventuali Flo_* rimasti in altre collection → Delete
      3) (opzionale) File → Clean Up → Purge All (per rimuovere datablock orfani),
         ma qui lo facciamo mirato a "Flo_*".
    """
    scene = getattr(context, "scene", None) or bpy.context.scene
    _scene_manifest_clear(scene)

    # Rimuovi oggetti in collection dedicata
    try:
        for obj in list(flo_coll.objects):
            if _is_managed_obj(obj):
                _safe_remove_object(obj)
    except Exception:
        pass

    # Rimuovi oggetti Flo_* sparsi
    try:
        for obj in list(bpy.data.objects):
            if _is_managed_obj(obj):
                _safe_remove_object(obj)
    except Exception:
        pass

    # Pulisci datablock orfani "Flo_*"
    # Manuale: “Purge” ma filtrato.
    try:
        for me in list(bpy.data.meshes):
            if getattr(me, "users", 0) == 0 and str(me.name).startswith("Flo_"):
                try:
                    bpy.data.meshes.remove(me)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        for cu in list(bpy.data.curves):
            if getattr(cu, "users", 0) == 0 and str(cu.name).startswith("Flo_"):
                try:
                    bpy.data.curves.remove(cu)
                except Exception:
                    pass
    except Exception:
        pass

    msg = "[floretion_triangle_mesh] Reset automatico: " + (reason or "stato parziale/corrotto.")
    print(msg)
    if op is not None:
        try:
            op.report({'INFO'}, msg)
        except Exception:
            pass


def _ensure_viewport_label(
    *,
    collection: bpy.types.Collection,
    enabled: bool,
    name: str,
    text: str,
    location: tuple[float, float, float],
) -> bpy.types.Object | None:
    """Label semplice: oggetto FONT con hide_render=True.

    Manuale in Blender:
      - Add → Text
      - Object Data (Text) → Body = "X" / "Y" / "XY"
      - sposti il testo in una posizione precisa
      - lo metti nella collection dell’add-on
      - lo rendi:
          * non renderizzabile (Object Properties → Visibility → Ray Visibility / o hide_render)
          * non selezionabile (Outliner: “disable selection” / o hide_select)
    """
    obj = bpy.data.objects.get(name)
    data_name = f"{name}_Data"

    # Se la label è disabilitata: rimuovi oggetto + eventuale Curve datablock orfano.
    # Manuale: cancelli il Text e poi fai purge (o lasci che Blender lo “orphanizzi”).
    if not enabled:
        if obj is not None:
            _safe_remove_object(obj)
        cu = bpy.data.curves.get(data_name)
        if cu is not None and getattr(cu, "users", 0) == 0:
            try:
                bpy.data.curves.remove(cu)
            except Exception:
                pass
        return None

    # Se esiste ma non è FONT -> ricrea
    # Manuale: “c’è qualcosa con quel nome ma è un altro tipo → delete e rifai Add → Text”
    if obj is not None and obj.type != "FONT":
        _safe_remove_object(obj)
        obj = None

    if obj is None:
        # Crea datablock Curve di tipo FONT (Text in Blender)
        cu = bpy.data.curves.new(data_name, type="FONT")
        try:
            cu.body = str(text)  # testo visualizzato
        except Exception:
            pass
        # Crea l’oggetto “contenitore” e lo linka alla collection
        obj = bpy.data.objects.new(name, cu)
        try:
            ensure_object_in_collection(obj, collection)
        except Exception:
            pass
    else:
        # Esiste già: assicurati che sia nella collection e aggiorna testo
        ensure_object_in_collection(obj, collection)
        try:
            if obj.data and hasattr(obj.data, "body"):
                obj.data.body = str(text)
        except Exception:
            pass

    # viewport-only: non deve finire nel render finale
    try:
        obj.hide_render = True
    except Exception:
        pass

    # non selezionabile: evita che l’utente lo sposti/rompa involontariamente
    try:
        obj.hide_select = True
    except Exception:
        pass

    # posizionamento nello spazio
    try:
        obj.location = (float(location[0]), float(location[1]), float(location[2]))
    except Exception:
        pass

    # tagga come managed + ruolo
    try:
        obj[_MANAGED_TAG] = True
        obj[_MANAGED_ROLE] = "LABEL"
    except Exception:
        pass

    return obj


# ---------------------------------------------------------------------------
# Helper visibility: Flo_*_cent / Flo_*_curve
# ---------------------------------------------------------------------------

def apply_helper_visibility(scene: bpy.types.Scene, props) -> None:
    """Applica visibilità viewport/render per gli helper curve/cent.

    Manuale in Blender:
      - Outliner: icona “occhio” (viewport) e “camera” (render) sugli oggetti helper.
      - Qui facciamo la stessa cosa con hide_viewport/hide_render.
    """
    show_cent = bool(getattr(props, "show_centroids", False))
    show_curve = bool(getattr(props, "show_curve", False))

    for prefix in ("Flo_X", "Flo_Y", "Flo_XY"):
        o_cent = bpy.data.objects.get(f"{prefix}_cent")
        if o_cent is not None:
            try:
                o_cent.hide_viewport = (not show_cent)
            except Exception:
                pass
            try:
                o_cent.hide_render = (not show_cent)
            except Exception:
                pass

        o_curve = bpy.data.objects.get(f"{prefix}_curve")
        if o_curve is not None:
            try:
                o_curve.hide_viewport = (not show_curve)
            except Exception:
                pass
            try:
                o_curve.hide_render = (not show_curve)
            except Exception:
                pass

