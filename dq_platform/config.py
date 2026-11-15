"""Project config: connections, assets, checks, lineage, ERP packs."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml

PACKS_DIR = Path(__file__).parent / "packs"
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate_env(value):
    """Replace ${VAR} / ${VAR:-default} so secrets live in the environment, not YAML."""
    if isinstance(value, str):
        def sub(m):
            var, default = m.group(1), m.group(2)
            if var in os.environ:
                return os.environ[var]
            if default is not None:
                return default
            raise KeyError(f"environment variable {var} is not set")
        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env(v) for v in value]
    return value


def parse_duration(text) -> timedelta:
    if isinstance(text, (int, float)):
        return timedelta(seconds=text)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*", str(text))
    if not m:
        raise ValueError(f"bad duration {text!r}; use e.g. 30m, 6h, 2d, 1w")
    n, unit = float(m.group(1)), m.group(2)
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    return timedelta(**{units[unit]: n})


@dataclass
class Asset:
    name: str
    connection: str
    table: str
    description: str = ""
    owner: str = ""
    tags: list = field(default_factory=list)
    filter: str | None = None          # optional WHERE clause, e.g. partition pruning
    checks: list = field(default_factory=list)


@dataclass
class Project:
    name: str
    root: Path
    connections: dict
    assets: dict
    lineage: list
    transforms_dir: Path | None
    store_path: Path
    settings: dict


def _fill(template, mapping: dict):
    """Substitute {field} placeholders in a pack check with the ERP's column names."""
    if isinstance(template, str):
        def sub(m):
            key = m.group(1)
            if key not in mapping:
                raise KeyError(f"pack field {key!r} has no column mapping")
            return mapping[key]
        return re.sub(r"\{([a-z_][a-z0-9_]*)\}", sub, template)
    if isinstance(template, dict):
        return {k: _fill(v, mapping) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, mapping) for v in template]
    return template


def load_pack(name: str, extra_dirs=()) -> dict:
    for d in (*extra_dirs, PACKS_DIR):
        p = Path(d) / f"{name}.yml"
        if p.exists():
            return yaml.safe_load(p.read_text())
    raise FileNotFoundError(f"pack {name!r} not found")


def expand_pack(asset_cfg: dict, extra_dirs=()) -> tuple[list, str | None]:
    pack = load_pack(asset_cfg["pack"], extra_dirs)
    mapping = {}
    flavor = asset_cfg.get("mapping")
    if flavor:
        if flavor not in pack.get("mappings", {}):
            raise KeyError(f"pack {asset_cfg['pack']!r} has no mapping {flavor!r}; "
                           f"available: {sorted(pack.get('mappings', {}))}")
        mapping.update(pack["mappings"][flavor])
    mapping.update(asset_cfg.get("columns", {}))
    skipped = [f for f in pack.get("fields", []) if f not in mapping]
    checks = []
    for chk in pack.get("checks", []):
        needed = set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", yaml.safe_dump(chk)))
        if needed & set(skipped):
            continue  # this ERP doesn't carry that field; skip rather than fail
        c = _fill(copy.deepcopy(chk), mapping)
        c.setdefault("id", f"{asset_cfg['pack']}.{chk.get('id', chk['type'])}")
        checks.append(c)
    default_table = pack.get("tables", {}).get(flavor) if flavor else None
    return checks, default_table


def load_project(path: str | os.PathLike) -> Project:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text()) or {}
    root = path.parent
    pack_dirs = [root / d for d in raw.get("pack_dirs", [])]

    connections = {}
    for name, cfg in (raw.get("connections") or {}).items():
        cfg = interpolate_env(cfg)
        for key in ("path",):
            if key in cfg and cfg.get("type") in ("sqlite", "files") and cfg[key] != ":memory:":
                cfg[key] = str((root / cfg[key]).resolve()) if not Path(cfg[key]).is_absolute() else cfg[key]
        connections[name] = cfg

    assets = {}
    for name, cfg in (raw.get("assets") or {}).items():
        checks = list(cfg.get("checks", []))
        table = cfg.get("table")
        if cfg.get("pack"):
            pack_checks, pack_table = expand_pack(cfg, pack_dirs)
            checks = pack_checks + checks
            table = table or pack_table
        if not table:
            raise ValueError(f"asset {name!r}: `table` is required")
        if cfg.get("connection") not in connections:
            raise ValueError(f"asset {name!r}: unknown connection {cfg.get('connection')!r}")
        assets[name] = Asset(
            name=name, connection=cfg["connection"], table=table,
            description=cfg.get("description", ""), owner=cfg.get("owner", ""),
            tags=cfg.get("tags", []), filter=cfg.get("filter"), checks=checks,
        )

    for chk in raw.get("checks", []) or []:  # cross-asset checks (reconciliation etc.)
        target = chk.get("asset") or chk.get("source")
        if target not in assets:
            raise ValueError(f"check {chk.get('id')!r}: unknown asset {target!r}")
        assets[target].checks.append(chk)

    transforms = raw.get("transforms_dir")
    store = raw.get("store", ".dq/results.db")
    return Project(
        name=raw.get("project", path.stem), root=root, connections=connections,
        assets=assets, lineage=raw.get("lineage", []) or [],
        transforms_dir=(root / transforms) if transforms else None,
        store_path=(root / store) if not Path(store).is_absolute() else Path(store),
        settings=raw.get("settings", {}) or {},
    )
