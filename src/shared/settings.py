"""Defaults + overrides: a component's defaults TOML (next to its code, every setting written out), with an
experiment's table of the same shape on top. Tables merge key by key; anything else is replaced. A key the defaults
don't have is an error, so a misspelt override can't be silently ignored."""
import tomllib
from pathlib import Path


def merge(base: dict, overrides: dict, where: str = "") -> dict:
    out = dict(base)
    for key, value in overrides.items():
        if key not in base:
            raise KeyError(f"unknown setting {where}{key} (the defaults have: {', '.join(base)})")
        out[key] = merge(base[key], value, f"{where}{key}.") if isinstance(value, dict) and isinstance(base[key], dict) \
            else value
    return out


def merged(defaults: Path, overrides: dict, where: str = "") -> dict:
    with open(defaults, "rb") as f:
        return merge(tomllib.load(f), overrides, where)
