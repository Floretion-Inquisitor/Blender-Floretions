from __future__ import annotations

from typing import Dict, Any

import numpy as np

from floretion import Floretion
from lib.triangleize_utils.centroid_distance import get_basevec_coords



def _oct_string_for_base(base_dec: int, order: int) -> str:
    """
    Converte il base vector in stringa ottale di lunghezza fissa = ordine (padding left a zero).
    """
    return format(int(base_dec), "o").rjust(order, "0")


def sample_floretion(flo: Floretion, ignore_zero: bool = True) -> Dict[str, Any]:
    """
    Estrae info geometriche basilari da una Floretion.

    Restituisce un dict con:
      - 'base_decs' : np.ndarray[int]
      - 'coeffs'    : np.ndarray[float]
      - 'coords'    : np.ndarray[ [x,y], ... ]  (equilatero)
      - 'dists'     : np.ndarray[float]
      - 'indices'   : np.ndarray[int]
      - 'order'     : int
      - 'oct_strings': np.ndarray[str]
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

    coords = np.zeros((n, 2), dtype=float)
    dists = np.zeros(n, dtype=float)
    oct_strings = np.empty(n, dtype=object)

    for i, dec in enumerate(base_decs):
        oct_str = _oct_string_for_base(dec, order)
        oct_strings[i] = oct_str
        x, y = get_basevec_coords(oct_str)
        coords[i, 0] = x
        coords[i, 1] = y
        dists[i] = (x**2 + y**2) ** 0.5

    return {
        "base_decs": base_decs,
        "coeffs": coeffs,
        "coords": coords,
        "dists": dists,
        "indices": indices,
        "order": order,
        "oct_strings": oct_strings,
    }
