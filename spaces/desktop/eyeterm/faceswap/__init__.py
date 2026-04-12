"""Face-swap subsystem: inswapper + particle-based alignment correction.

Lazy-imported — heavy deps (insightface, onnxruntime) only loaded when used.
"""


def __getattr__(name):
    if name == "FaceSwapper":
        from .swapper import FaceSwapper
        return FaceSwapper
    if name == "ParticleAligner":
        from .aligner import ParticleAligner
        return ParticleAligner
    if name == "ParticleState":
        from .aligner import ParticleState
        return ParticleState
    if name == "ParticleOverlay":
        from .particle_overlay import ParticleOverlay
        return ParticleOverlay
    if name in ("resolve_preset", "list_presets"):
        from . import presets
        return getattr(presets, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FaceSwapper",
    "ParticleAligner",
    "ParticleState",
    "ParticleOverlay",
    "resolve_preset",
    "list_presets",
]
