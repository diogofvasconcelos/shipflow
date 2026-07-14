"""Pydantic request/response models, one module per feature.

Wire format only — no behavior. Response schemas are explicit allowlists:
sensitive columns (e.g. ML tokens) are excluded by construction, never by
remembering to omit them.
"""
