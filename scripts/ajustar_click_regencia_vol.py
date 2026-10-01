#!/usr/bin/env python3
"""CLICK -5 dB; REGÊNCIA no volume padrao (0 dB) no .rpp unificado."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import extract_track_blocks  # noqa: E402
from unificar_reaper import (  # noqa: E402
    CLICK_VOLUME_DB_OFFSET,
    DEFAULT_TRACK_VOLUME,
    apply_click_volume,
    get_track_name,
    replace_all_track_blocks,
    set_track_volume,
)

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"


def main() -> None:
    rpp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RPP
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_track_blocks(text)

    click_n = 0
    reg_n = 0
    new_blocks: list[str] = []
    for block in blocks:
        name = get_track_name(block)
        if name.endswith(" - CLICK"):
            new_blocks.append(apply_click_volume(block, name))
            click_n += 1
        elif name.endswith(" - REGÊNCIA"):
            new_blocks.append(set_track_volume(block, DEFAULT_TRACK_VOLUME))
            reg_n += 1
        else:
            new_blocks.append(block)

    if click_n == 0 and reg_n == 0:
        raise SystemExit("Nenhuma track CLICK ou REGÊNCIA encontrada.")

    dest = backup_rpp(rpp_path)
    print(f"Backup: {dest}")

    rpp_path.write_text(replace_all_track_blocks(text, new_blocks), encoding="utf-8")
    print(
        f"{rpp_path.name}: {click_n} CLICK ({CLICK_VOLUME_DB_OFFSET:+.0f} dB), "
        f"{reg_n} REGÊNCIA (0 dB padrao)"
    )


if __name__ == "__main__":
    main()
