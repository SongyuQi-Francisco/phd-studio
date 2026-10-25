"""Research maps (研究地图): per-project maps of questions, paths, experiments and results.

See docs/research-map.md. Modules: model (vocabulary, validation), project (files, reading, change
stamp), ops (every write, with its events), registry (extension points), evidence (the core
attachment kind), api (routes), plugins/ (later phases).
"""
from . import evidence  # noqa: F401  (registers the evidence attachment kind)
