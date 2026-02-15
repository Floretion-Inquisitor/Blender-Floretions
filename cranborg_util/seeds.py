from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple, Any, Optional

import numpy as np

from floretion import Floretion


# -------------------------------------------------------------------
# Assicura che i path data/centers non dipendano dal working directory
# (in Blender il CWD è spesso diverso dal repo).
# -------------------------------------------------------------------
def _ensure_data_env() -> None:
    if os.environ.get("FLORETION_DATA_DIR") or os.environ.get("FLORETION_CENTERS_DIR"):
        return
    try:
        import floretion as _flo_mod
        root = Path(_flo_mod.__file__).resolve().parent  # folder di floretion.py
        data_dir = root / "data"
        os.environ.setdefault("FLORETION_DATA_DIR", str(data_dir))
    except Exception:
        # se non riesce, non bloccare: il loader fallirà con un errore chiaro
        pass


_ensure_data_env()

# import dopo aver settato env
from lib.floretion_utils.floretion_centers import centers_dir  # noqa: E402


# ------------------------------------------------------
#  Centers (per Cn/Cp/Cb) – segment-aware (7/8) + single (1..6)
# ------------------------------------------------------

def _basevec_str_to_dec_and_oct(base_vec: str, order: int) -> Tuple[int, str]:
    """
    Converte 'ijk...' -> ottale (solo {1,2,4,7}) -> dec int + oct zfill(order)
    """
    octal = (
        base_vec.replace("i", "1")
        .replace("j", "2")
        .replace("k", "4")
        .replace("e", "7")
    )
    dec = int(octal, 8)
    oct_z = format(dec, "o").rjust(order, "0")
    return dec, oct_z


def _pick_segment_file(dirp: Path, order: int, base_oct_z: str, storage: str) -> Path:
    """
    Trova il file segmento che contiene base_oct_z, supportando nomi tipo:
      centers_order_{n}_segment_{seg:03d}.{START}-{END}.npy
      centers_order_{n}_segment_{seg:03d}_{START}_{END}.npy
    e fallback legacy (un solo file senza range).
    """
    storage = storage.lower().strip()
    files = sorted(dirp.glob(f"centers_order_{order}_segment_*.{storage}"))
    if not files:
        raise FileNotFoundError(f"[centers] empty/non-existent dir: {dirp}")

    # match con range (accetta '.' o '_' prima dello start, e '-' '_' '.' tra start/end)
    rx = re.compile(
        rf"^centers_order_{order}_segment_(\d{{3}})"
        rf"(?:[._])([1247]{{{order}}})(?:[-._])([1247]{{{order}}})\.{re.escape(storage)}$"
    )

    ranged = []
    for p in files:
        m = rx.match(p.name)
        if m:
            start_oct, end_oct = m.group(2), m.group(3)
            ranged.append((p, start_oct, end_oct))

    # se abbiamo range: scegli quello giusto
    if ranged:
        for p, start_oct, end_oct in ranged:
            if start_oct <= base_oct_z <= end_oct:
                return p
        raise FileNotFoundError(f"[centers] no segment contains {base_oct_z} in {dirp}")

    # fallback: un solo file legacy senza range
    if len(files) == 1:
        return files[0]

    raise RuntimeError(f"[centers] ambiguous segment files in {dirp}: {[p.name for p in files[:10]]} ...")


@lru_cache(maxsize=512)
def _load_centers_map_from_file(path_str: str) -> Dict[str, list[int]]:
    """
    Carica una singola mappa segment (o single-file legacy) e normalizza in:
      dict[str(dec_key)] -> list[int]
    """
    p = Path(path_str)
    if p.suffix.lower() == ".npy":
        obj = np.load(str(p), allow_pickle=True)
        if isinstance(obj, np.ndarray) and obj.dtype == object:
            obj = obj.item()
        if not isinstance(obj, dict):
            raise ValueError(f"[centers] invalid npy dict in {p}")
        return {str(k): list(map(int, v)) for k, v in obj.items()}

    # json
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): list(map(int, v)) for k, v in data.items()}


@lru_cache(maxsize=4096)
def _centers_for_base_cached(order: int, mode: str, base_dec: int, storage: str) -> np.ndarray:
    """
    Ritorna np.ndarray[int] dei centers per un singolo base_dec.
    Funziona sia per orders piccoli (single file) sia per 7/8 (segmenti con range).
    """
    dirp = centers_dir(int(order), str(mode))
    base_oct_z = format(int(base_dec), "o").rjust(int(order), "0")

    segfile = _pick_segment_file(dirp, int(order), base_oct_z, str(storage))
    mapping = _load_centers_map_from_file(str(segfile.resolve()))

    key = str(int(base_dec))
    if key not in mapping:
        # alcuni dump usano chiave ottale
        if base_oct_z in mapping:
            key = base_oct_z
        else:
            raise KeyError(f"[centers] base key not found in {segfile.name}: {base_dec} (oct={base_oct_z})")

    return np.asarray(mapping[key], dtype=int)


def parse_special_commands(input_str: str, order: int) -> Floretion:
    """
    Gestisce:
        Cp(iii), Cn(ijk), Cb(jee)
    usando i centers (npy prima, poi json), segment-aware.
    """
    text = (input_str or "").strip()
    command_match = re.match(r"^(Cp|Cn|Cb)\(([-+\w\.]+)\)$", text)
    if command_match:
        command, base_vec = command_match.groups()

        if len(base_vec) != order:
            raise ValueError(f"Invalid base vector length. Expected length {order}.")

        if not all(c in "ijke.+-0123456789" for c in base_vec):
            raise ValueError("Invalid character in base vector.")

        base_dec, _ = _basevec_str_to_dec_and_oct(base_vec, order)
        mode = {"Cp": "pos", "Cn": "neg", "Cb": "both"}[command]

        # Prova prima NPY, poi JSON
        try:
            vecs = _centers_for_base_cached(order, mode, base_dec, "npy")
        except Exception:
            vecs = _centers_for_base_cached(order, mode, base_dec, "json")

        coeffs = np.ones(len(vecs), dtype=float)
        return Floretion(coeffs, vecs)

    # Nessun comando speciale: parse normale
    if not all(c in "0123456789ijke.+ -" for c in text):
        raise ValueError("Invalid character in floretion string.")
    return Floretion.from_string(text)


def summarize_floretion(flo_str: str) -> str:
    s = flo_str or ""
    if len(s) <= 120:
        return s
    return f"{s[:60]}...{s[-60:]}"


# ------------------------------------------------------
# Typical floretions (unit, axis, sierpinski, ecc.)
# ------------------------------------------------------

def _decimal_to_octal(decimal: int) -> str:
    return format(int(decimal), "o")


def get_typical_floretions(order: int) -> Dict[str, Dict[str, str]]:
    """
    Restituisce:
        name -> {"summary": "...", "full": "..."}
    """
    zero_flo = Floretion.from_string(f"0{'e' * order}")
    unit_flo = Floretion.from_string(f"1{'e' * order}")

    new_coeffs_sierp = []
    new_coeffs_sierp_i: list[float] = []
    new_coeffs_sierp_j: list[float] = []
    new_coeffs_sierp_k: list[float] = []

    axis_i: list[float] = []
    axis_j: list[float] = []
    axis_k: list[float] = []

    for base in zero_flo.base_vec_dec_all:
        base_octal = _decimal_to_octal(base)

        new_coeffs_sierp.append(0.0 if "7" in base_octal else 1.0)
        new_coeffs_sierp_i.append(0.0 if "1" in base_octal else 1.0)
        new_coeffs_sierp_j.append(0.0 if "2" in base_octal else 1.0)
        new_coeffs_sierp_k.append(0.0 if "4" in base_octal else 1.0)

        axis_i.append(0.0 if ("2" in base_octal or "4" in base_octal) else 1.0)
        axis_j.append(0.0 if ("4" in base_octal or "1" in base_octal) else 1.0)
        axis_k.append(0.0 if ("1" in base_octal or "2" in base_octal) else 1.0)

    nv = zero_flo.base_vec_dec_all
    norm_fac = 1.0

    sierp_flo = Floretion(norm_fac * np.array(new_coeffs_sierp), nv, format_type="dec")
    sierp_flo_i = Floretion(norm_fac * np.array(new_coeffs_sierp_i), nv, format_type="dec")
    sierp_flo_j = Floretion(norm_fac * np.array(new_coeffs_sierp_j), nv, format_type="dec")
    sierp_flo_k = Floretion(norm_fac * np.array(new_coeffs_sierp_k), nv, format_type="dec")

    axis_i_f = Floretion(np.array(axis_i), nv, format_type="dec")
    axis_j_f = Floretion(np.array(axis_j), nv, format_type="dec")
    axis_k_f = Floretion(np.array(axis_k), nv, format_type="dec")

    axis_ij = axis_i_f + axis_j_f - unit_flo
    axis_jk = axis_j_f + axis_k_f - unit_flo
    axis_ki = axis_k_f + axis_i_f - unit_flo
    axis_ijk = axis_i_f + axis_j_f + axis_k_f - 2 * unit_flo

    typical_floretions = {
        "unit": unit_flo.as_floretion_notation(),
        "axis-I": axis_i_f.as_floretion_notation(),
        "axis-J": axis_j_f.as_floretion_notation(),
        "axis-K": axis_k_f.as_floretion_notation(),
        "axis-IJ": axis_ij.as_floretion_notation(),
        "axis-JK": axis_jk.as_floretion_notation(),
        "axis-KI": axis_ki.as_floretion_notation(),
        "axis-IJK": axis_ijk.as_floretion_notation(),
        "sierpinski-E": sierp_flo.as_floretion_notation(),
        "sierpinski-I": sierp_flo_i.as_floretion_notation(),
        "sierpinski-J": sierp_flo_j.as_floretion_notation(),
        "sierpinski-K": sierp_flo_k.as_floretion_notation(),
    }

    return {name: {"summary": v, "full": v} for name, v in typical_floretions.items()}


def make_typical_seed(order: int, name: str) -> Floretion:
    floretion_map = get_typical_floretions(order)
    key = name if name in floretion_map else "unit"
    flo_str = floretion_map[key]["full"]
    return Floretion.from_string(flo_str)


def make_seed_from_string(input_str: str, order: int | None = None) -> Floretion:
    """
    Parsing generico:
      - se order è fornito: usa parse_special_commands (capisce Cn/Cp/Cb)
      - altrimenti: semplice Floretion.from_string
    """
    text = (input_str or "").strip()
    if not text:
        raise ValueError("Empty floretion string.")
    if order is not None and order > 0:
        return parse_special_commands(text, order)
    return Floretion.from_string(text)
