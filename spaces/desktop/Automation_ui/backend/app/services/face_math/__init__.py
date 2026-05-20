"""Math-based face region-composite swap pipeline.

Phase A: reference capture + landmark extraction
Phase B: region-math compositor (per-region α-mask)
Phase C: motion-drift correction
Phase D: live stream pipeline

See tasks/face_math_pipeline.md for the architecture rationale.
"""
