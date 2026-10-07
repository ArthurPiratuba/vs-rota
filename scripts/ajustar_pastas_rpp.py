#!/usr/bin/env python3
"""Corrige pastas: NAME, cor PEAKCOL da region, sem NOTES nas tracks-pasta."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import TRACK_NAME_PATTERN, extract_track_blocks, parse_quoted_name  # noqa: E402
from unificar_reaper import (  # noqa: E402
    emit_folder_children,
    is_folder_parent,
    is_monitor_track,
    load_region_peakcols,
    make_folder_track,
    rename_folder_track,
    replace_all_track_blocks,
)

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP
REGION_DATA = WORKSPACE / "data" / "region_markers.json"
TRACK_ORDER_IN_NAME = re.compile(r"^(\d+)\s*-\s*")


def load_song_names() -> dict[int, str]:
    data = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    return {entry["index"]: entry["name"] for entry in data["starts"]}


def track_order(track_text: str) -> int | None:
    match = TRACK_NAME_PATTERN.search(track_text)
    if not match:
        return None
    name = parse_quoted_name(match.group(2).strip())
    m = TRACK_ORDER_IN_NAME.match(name)
    return int(m.group(1)) if m else None


def group_audio_indices(blocks: list[str]) -> list[tuple[int, list[int]]]:
    groups: list[tuple[int, list[int]]] = []
    current_order: int | None = None
    current: list[int] = []

    for idx, block in enumerate(blocks):
        if is_folder_parent(block) or is_monitor_track(block):
            continue
        order = track_order(block)
        if order is None:
            continue
        if current_order is None:
            current_order = order
        elif order != current_order:
            groups.append((current_order, current))
            current = []
            current_order = order
        current.append(idx)

    if current:
        groups.append((current_order, current))
    return groups


def build_output(
    blocks: list[str],
    song_names: dict[int, str],
    region_peakcols: dict[int, int],
) -> list[str]:
    groups = group_audio_indices(blocks)
    if len(groups) != 23:
        raise ValueError(f"Esperadas 23 musicas, encontrados {len(groups)} grupos.")

    output: list[str] = []
    fixed = 0
    created = 0

    for order, indices in groups:
        if order not in song_names:
            raise ValueError(f"Musica {order} sem nome em region_markers.json.")
        peakcol = region_peakcols.get(order)
        if peakcol is None:
            raise ValueError(f"Musica {order} sem cor de region em region_markers.json.")

        if indices[0] > 0 and is_folder_parent(blocks[indices[0] - 1]):
            output.append(
                rename_folder_track(
                    blocks[indices[0] - 1], order, song_names[order], peakcol
                )
            )
            fixed += 1
        else:
            output.append(make_folder_track(order, song_names[order], peakcol))
            created += 1

        monitor_idx = next((i for i in indices if is_monitor_track(blocks[i])), None)
        audio_indices = [i for i in indices if not is_monitor_track(blocks[i])]
        output.extend(
            emit_folder_children(
                blocks, order, audio_indices, peakcol, monitor_idx
            )
        )

    return output, fixed, created


def main() -> None:
    rpp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RPP
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_track_blocks(text)
    song_names = load_song_names()
    region_peakcols = load_region_peakcols()
    if len(region_peakcols) < 23:
        raise SystemExit("region_markers.json sem cores canonicas para as 23 regions.")
    new_blocks, fixed, created = build_output(blocks, song_names, region_peakcols)

    dest = backup_rpp(rpp_path)
    print(f"Backup: {dest}")

    output = replace_all_track_blocks(text, new_blocks)
    notes_in_folders = sum(
        1 for block in new_blocks if is_folder_parent(block) and "<NOTES" in block
    )
    if notes_in_folders:
        raise SystemExit(f"Ainda restam {notes_in_folders} pastas com <NOTES>.")

    rpp_path.write_text(output, encoding="utf-8")
    print(
        f"{rpp_path.name}: {len(blocks)} tracks | "
        f"{fixed} pastas, {created} criadas | + MONITOR amarelo 1o em cada pasta"
    )


if __name__ == "__main__":
    main()
