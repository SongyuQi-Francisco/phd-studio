"""Finding a project or a node by what a person (or an experiment script) types: slug, title, research line;
node id, id prefix, title or a part of it."""
from __future__ import annotations

from typing import Any

from ... import i18n
from .. import project


def find_project(key: str) -> str:
    """The slug of a project given as slug, title or research-line name (any case); KeyError says what is there."""
    slugs = project.slugs()
    key = (key or "").strip()
    if key in slugs:
        return key
    low = key.lower()
    titles = {s: project.summary(project.get(s)) for s in slugs}
    for test in (lambda sm, s: low in {str(v).lower() for v in (sm.get("title"), sm.get("line")) if v},
                 lambda sm, s: s.startswith(low) or any(low in str(v).lower() for v in (sm.get("title"), sm.get("line")) if v)):
        hits = [s for s, sm in titles.items() if low and test(sm, s)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise KeyError(i18n.t("rmap_results.project_many", key=key, slugs=", ".join(hits)))
    raise KeyError(i18n.t("rmap_results.project_none", key=key, slugs=", ".join(slugs) or "-"))


def find_node(m: dict[str, Any], key: str, *, types: tuple[str, ...] | None = None) -> dict[str, Any]:
    """A live node by id, id prefix (4+ characters), exact title or a part of the title (any case)."""
    key = (key or "").strip()
    nodes = [n for n in m["nodes"] if not types or n.get("type") in types]
    low = key.lower()
    for test in (lambda n: n["id"] == key,
                 lambda n: len(low) >= 4 and n["id"].lower().startswith(low),
                 lambda n: n["title"].lower() == low,
                 lambda n: low in n["title"].lower()):
        hits = [n for n in nodes if test(n)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            names = "; ".join(f"{n['id']} {n['title']}" for n in hits[:6])
            raise KeyError(i18n.t("rmap_results.node_many", key=key, nodes=names))
    names = "; ".join(f"{n['id']} {n['title']}" for n in nodes[:8]) or "-"
    raise KeyError(i18n.t("rmap_results.node_none", key=key, nodes=names))
