"""Locate the golden set shipped inside the package.

Example:
    from docassay.builtin import builtin_config_path

    print(builtin_config_path().name)
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "BuiltinError",
    "builtin_config_path",
    "builtin_sets_path",
    "config_for_golden_flag",
]


class BuiltinError(Exception):
    """``--golden`` was not ``builtin``, or the packaged set is not installed.

    Example:
        raise BuiltinError('unknown --golden value "nope". Use --golden builtin.')
    """


def builtin_config_path(origin: Path | None = None) -> Path:
    """Return ``docassay.yaml`` for the packaged public golden set.

    Search ``builtin_golden`` beside this module, then parent directories for
    a checkout's ``golden/docassay.yaml``. ``origin`` is this file unless a
    test passes another path.

    Example:
        config = builtin_config_path()
    """
    here = (Path(__file__) if origin is None else origin).resolve()
    packaged = here.parent / "builtin_golden" / "docassay.yaml"
    if packaged.is_file():
        return packaged
    for parent in here.parents:
        candidate = parent / "golden" / "docassay.yaml"
        if candidate.is_file():
            return candidate
    raise BuiltinError(
        "packaged golden set not found. Reinstall docassay or run from a checkout "
        "that contains golden/docassay.yaml."
    )


def builtin_sets_path(origin: Path | None = None) -> Path:
    """Return the ``sets`` directory next to the packaged config.

    Example:
        folder = builtin_sets_path()
    """
    return builtin_config_path(origin).parent / "sets"


def config_for_golden_flag(config: Path, golden: str | None) -> Path:
    """Return ``config``, or the packaged config when ``golden`` is ``builtin``.

    Example:
        path = config_for_golden_flag(Path("docassay.yaml"), "builtin")
    """
    if golden is None:
        return config
    if golden != "builtin":
        raise BuiltinError(f'unknown --golden value "{golden}". Use --golden builtin.')
    return builtin_config_path()
