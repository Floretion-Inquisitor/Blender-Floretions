from __future__ import annotations

from dataclasses import dataclass

from floretion import Floretion

@dataclass
class PipelineConfig:
    apply_tri: bool = False
    tri_times: int = 1

    apply_rot: bool = False
    rot_shift: int = 1

    # hook futuri
    apply_grow: bool = False
    apply_proj: bool = False

def apply_pipeline(seed: Floretion, cfg: PipelineConfig) -> Floretion:
    """
    Applica una pipeline semplice di trasformazioni Floretion-based.
    Tutto qui rimane indipendente da Blender.
    """
    f = seed

    if cfg.apply_tri:
        for _ in range(max(cfg.tri_times, 1)):
            f = Floretion.tri(f)

    if cfg.apply_grow:
        f = Floretion.grow_flo(f)

    if cfg.apply_proj:
        # per ora usiamo proj semplice; in futuro puoi usare proj_strip_grow
        f = Floretion.proj(f)

    if cfg.apply_rot:
        shift = cfg.rot_shift
        f = Floretion.rotate_coeffs(f, shift=shift)

    return f
