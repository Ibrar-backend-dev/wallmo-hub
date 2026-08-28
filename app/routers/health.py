"""GET /healthz  - liveness, no DB touch.
   GET /readyz   - readiness, SELECT 1 against the pool.
"""
