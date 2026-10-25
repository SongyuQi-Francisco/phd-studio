"""Research maps (研究地图): per-project maps of questions, paths, experiments and results.

See docs/research-map.md. Modules: model (vocabulary, validation), project (files, reading, change
stamp), ops (every write, with its events), registry (extension points), evidence (the core
attachment kind), proposals (the assistant's changes, applied once confirmed), lines (research
lines: their maps, a first map drafted from a note), canvas (the Obsidian Canvas view), api
(routes), plugins/ (later phases).
"""
from . import canvas, evidence  # noqa: F401  (register the canvas writer and the evidence attachment kind)
