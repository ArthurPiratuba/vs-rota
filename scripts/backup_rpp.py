#!/usr/bin/env python3
"""Cria backup timestamped de .rpp antes de alteracoes."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
DEFAULT_RPP = projeto.RPP
BACKUPS_DIR = WORKSPACE / "Backups"


def backup_rpp(source: Path | None = None) -> Path:
    src = (source or DEFAULT_RPP).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {src}")

    BACKUPS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUPS_DIR / f"{src.stem}-{stamp}.rpp-bak"
    # Dois backups no mesmo segundo: o segundo nao pode apagar o primeiro.
    n = 2
    while dest.exists():
        dest = BACKUPS_DIR / f"{src.stem}-{stamp}-{n}.rpp-bak"
        n += 1
    shutil.copy2(src, dest)
    return dest


def main() -> None:
    src = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_RPP
    dest = backup_rpp(src)
    print(f"Backup: {dest}")


if __name__ == "__main__":
    main()
