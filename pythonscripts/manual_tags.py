"""Preserve tag fields that an editor set by hand.

A source YAML lists hand-set fields under `manual_tags`, e.g.
`manual_tags: [description_tags, anova_tags]`. runall.py snapshots those
fields before the analysis scripts run and restores them after each one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _listed(data: dict) -> list[str]:
    value = data.get("manual_tags")
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def is_manual(data: dict, field: str) -> bool:
    return field in _listed(data)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def snapshot(sources_dir: Path) -> dict[str, dict[str, Any]]:
    snap: dict[str, dict[str, Any]] = {}
    for path in sorted(sources_dir.glob("*.yml")):
        data = _load(path)
        fields = {f: data[f] for f in _listed(data) if f in data}
        if fields:
            snap[path.name] = fields
    return snap


def restore(sources_dir: Path, snap: dict[str, dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for name, fields in snap.items():
        path = sources_dir / name
        if not path.exists():
            continue
        data = _load(path)
        if all(data.get(k) == v for k, v in fields.items()):
            continue
        data.update(fields)
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")
        changed.append(name)
    return changed
