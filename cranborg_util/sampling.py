from __future__ import annotations

from typing import Dict, Any

import math
import numpy as np

from floretion import Floretion
from lib.triangleize_utils.centroid_distance import get_basevec_coords


def _oct_string_for_base(base_dec: int, order: int) -> str:
    """
    Converte il base vector in stringa ottale di lunghezza fissa = ordine (padding left a zero).
    """
    return format(int(base_dec), "o").rjust(order, "0")


def _tetra_raw_pos_from_oct_string(base_vector_oct: str, *, start_sign: float = 1.0) -> np.ndarray:
    """
    Mapping 3D "tetraedrico" dei base vectors.

    Convenzione:
      - si parte dall'apice inferiore di un tetraedro regolare capovolto
      - 1 / 2 / 4 puntano ai tre vertici della base
      - 7 non muove, ma inverte il segno del passo successivo
      - il passo si dimezza a ogni cifra

    Restituisce coordinate RAW (non ancora scalate in base a max_height).
    """
    oct_str = str(base_vector_oct).strip()

    side = 1.0
    h_tri = (math.sqrt(3.0) / 2.0) * side
    h_tet = math.sqrt(2.0 / 3.0) * side

    apex = np.array([0.0, 0.0, 0.0], dtype=float)
    v_i = np.array([-side / 2.0, -h_tri / 3.0, h_tet], dtype=float)
    v_j = np.array([+side / 2.0, -h_tri / 3.0, h_tet], dtype=float)
    v_k = np.array([0.0, +2.0 * h_tri / 3.0, h_tet], dtype=float)

    dirs = {}
    for digit, vert in (("1", v_i), ("2", v_j), ("4", v_k)):
        vec = vert - apex
        norm = float(np.linalg.norm(vec))
        if norm <= 1e-12:
            raise ValueError(f"Direzione tetraedrica degenerata per cifra {digit}")
        dirs[digit] = vec / norm

    pos = apex.copy()
    step = 1.0
    sign_step = float(start_sign)

    for digit in oct_str:
        if digit == "7":
            sign_step *= -1.0
        elif digit in dirs:
            pos += dirs[digit] * step * sign_step
        else:
            raise ValueError(f"Cifra ottale non valida: {digit}")

        step /= 2.0

    return pos


def tetra_coords_scaled_to_max_height(
    coords_raw: np.ndarray,
    max_height: float,
) -> np.ndarray:
    """
    Scala uniformemente le coordinate tetraedriche in modo che il massimo
    valore assoluto di Z diventi `max_height`.

    Nota:
      - usiamo una scala UNIFORME anche su X/Y, così non deformiamo la geometria;
      - se max_height <= 0, il risultato collassa a 0.
    """
    arr = np.asarray(coords_raw, dtype=float)
    if arr.size == 0:
        return arr.reshape((0, 3))

    if arr.ndim != 2 or arr.shape[1] < 3:
        raise ValueError("coords_raw deve avere shape (N, 3)")

    try:
        max_h = float(max_height)
    except Exception:
        max_h = 0.0

    if max_h <= 1e-12:
        return np.zeros((arr.shape[0], 3), dtype=float)

    max_abs_z = float(np.max(np.abs(arr[:, 2])))
    if max_abs_z <= 1e-12:
        scale = 0.0
    else:
        scale = max_h / max_abs_z

    return arr[:, :3] * float(scale)


def sample_floretion(flo: Floretion, ignore_zero: bool = True) -> Dict[str, Any]:
    """
    Estrae info geometriche basilari da una Floretion.

    Restituisce un dict con:
      - 'base_decs'       : np.ndarray[int]
      - 'coeffs'          : np.ndarray[float]
      - 'coords'          : np.ndarray[[x,y], ...]   (planare / equilatero legacy)
      - 'coords2d'        : alias esplicito di coords
      - 'coords_tetra_raw': np.ndarray[[x,y,z], ...] (tetraedrico RAW, non scalato)
      - 'dists'           : np.ndarray[float]        (distanza planare legacy)
      - 'indices'         : np.ndarray[int]
      - 'order'           : int
      - 'oct_strings'     : np.ndarray[str]
    """
    coeffs = np.asarray(flo.coeff_vec_all, dtype=float)
    base_decs = np.asarray(flo.base_vec_dec_all, dtype=int)
    order = int(flo.flo_order)

    if ignore_zero:
        mask = np.abs(coeffs) > np.finfo(float).eps
        coeffs = coeffs[mask]
        base_decs = base_decs[mask]

    n = len(coeffs)
    indices = np.arange(n, dtype=int)

    coords2d = np.zeros((n, 2), dtype=float)
    coords_tetra_raw = np.zeros((n, 3), dtype=float)
    dists = np.zeros(n, dtype=float)
    oct_strings = np.empty(n, dtype=object)

    for i, dec in enumerate(base_decs):
        oct_str = _oct_string_for_base(dec, order)
        oct_strings[i] = oct_str

        x, y = get_basevec_coords(oct_str)
        coords2d[i, 0] = x
        coords2d[i, 1] = y
        dists[i] = float((x**2 + y**2) ** 0.5)

        coords_tetra_raw[i, :] = _tetra_raw_pos_from_oct_string(oct_str, start_sign=1.0)

    return {
        "base_decs": base_decs,
        "coeffs": coeffs,
        "coords": coords2d,
        "coords2d": coords2d,
        "coords_tetra_raw": coords_tetra_raw,
        "dists": dists,
        "indices": indices,
        "order": order,
        "oct_strings": oct_strings,
    }
