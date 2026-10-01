#!/usr/bin/env python3
"""Cria faders VCA no inicio do projeto: um para todos os CLICK, outro para REGÊNCIA.

O fader fica em 0 dB e nao altera o volume atual. Ele soma um ganho extra por cima
do fader de cada trilha. O mute do grupo muta todas as trilhas da categoria.
As trilhas continuam dentro das pastas das musicas.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import extract_track_blocks, format_rpp_name  # noqa: E402
from unificar_reaper import (  # noqa: E402
    PEAKCOL_BLUE,
    get_track_name,
    new_guid,
    replace_all_track_blocks,
    rgb_to_peakcol,
)

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"

# Ordem do chunk GROUP_FLAGS no Reaper 7.74 (grupos 1-32).
# Mute master/slave acendem o mute de cada trilha.
# VCA master/slave ajustam o volume sem mover o fader individual.
# Zeros no fim sao omitidos.
MUTE_MASTER_INDEX = 4
MUTE_SLAVE_INDEX = 5
VCA_MASTER_INDEX = 20
VCA_SLAVE_INDEX = 21
GROUP_FLAGS_PATTERN = re.compile(r"^    GROUP_FLAGS .+\n", re.MULTILINE)
VU_PATTERN = re.compile(r"^    VU .+\n", re.MULTILINE)


@dataclass(frozen=True)
class Category:
    master_name: str
    suffix: str
    group_bit: int
    peakcol: int


CATEGORIES = (
    Category("GRUPO CLICK", " - CLICK", 1, rgb_to_peakcol(255, 48, 48)),
    Category("GRUPO REGÊNCIA", " - REGÊNCIA", 2, PEAKCOL_BLUE),
)
MASTER_NAMES = {cat.master_name for cat in CATEGORIES}


def format_group_flags(master_bit: int = 0, slave_bit: int = 0) -> str:
    values = [0] * (VCA_SLAVE_INDEX + 1)
    if master_bit:
        values[MUTE_MASTER_INDEX] = master_bit
        values[VCA_MASTER_INDEX] = master_bit
    if slave_bit:
        values[MUTE_SLAVE_INDEX] = slave_bit
        values[VCA_SLAVE_INDEX] = slave_bit
    while values and values[-1] == 0:
        values.pop()
    if not values:
        raise ValueError("GROUP_FLAGS vazio.")
    return " ".join(str(value) for value in values)


def set_group_flags(block: str, flags: str) -> str:
    line = f"    GROUP_FLAGS {flags}\n"
    if GROUP_FLAGS_PATTERN.search(block):
        return GROUP_FLAGS_PATTERN.sub(line, block, count=1)
    if not VU_PATTERN.search(block):
        raise ValueError(f"Track sem VU: {get_track_name(block)}")
    return VU_PATTERN.sub(lambda match: match.group(0) + line, block, count=1)


def make_vca_master(category: Category) -> str:
    guid = new_guid()
    flags = format_group_flags(master_bit=category.group_bit)
    name = format_rpp_name(category.master_name)
    return (
        f"  <TRACK {guid}\n"
        f"    NAME {name}\n"
        f"    PEAKCOL {category.peakcol}\n"
        f"    BEAT -1\n"
        f"    AUTOMODE 0\n"
        f"    PANLAWFLAGS 3\n"
        f"    VOLPAN 1 0 -1 -1 1\n"
        f"    MUTESOLO 0 0 0\n"
        f"    IPHASE 0\n"
        f"    PLAYOFFS 0 1\n"
        f"    ISBUS 0 0\n"
        f"    BUSCOMP 0 0 0 0 0\n"
        f"    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0\n"
        f"    FIXEDLANES 9 0 0 0 0\n"
        f"    LANEREC -1 -1 -1 0\n"
        f"    SEL 0\n"
        f"    REC 0 0 1 0 0 0 0 0\n"
        f"    VU 64\n"
        f"    GROUP_FLAGS {flags}\n"
        f"    TRACKHEIGHT 0 0 0 0 0 0 0\n"
        f"    INQ 0 0 0 0.5 100 0 0 100\n"
        f"    NCHAN 2\n"
        f"    FX 1\n"
        f"    TRACKID {guid}\n"
        f"    PERF 0\n"
        f"    MIDIOUT -1\n"
        f"    MAINSEND 1 0\n"
        f"  >\n"
    )


def category_for(name: str) -> Category | None:
    if name in MASTER_NAMES:
        return None
    for category in CATEGORIES:
        if name.endswith(category.suffix):
            return category
    return None


def main() -> None:
    rpp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RPP
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_track_blocks(text)

    counts = {category.master_name: 0 for category in CATEGORIES}
    new_blocks: list[str] = [make_vca_master(category) for category in CATEGORIES]
    for block in blocks:
        name = get_track_name(block)
        if name in MASTER_NAMES:
            continue
        category = category_for(name)
        if category is None:
            if GROUP_FLAGS_PATTERN.search(block):
                raise SystemExit(f"Track fora dos grupos ja tem GROUP_FLAGS: {name}")
            new_blocks.append(block)
            continue
        flags = format_group_flags(slave_bit=category.group_bit)
        new_blocks.append(set_group_flags(block, flags))
        counts[category.master_name] += 1

    missing = [name for name, count in counts.items() if count == 0]
    if missing:
        raise SystemExit(f"Nenhuma track para: {', '.join(missing)}")

    dest = backup_rpp(rpp_path)
    print(f"Backup: {dest}")
    rpp_path.write_text(replace_all_track_blocks(text, new_blocks), encoding="utf-8")

    summary = ", ".join(f"{count} {name}" for name, count in counts.items())
    print(f"{rpp_path.name}: faders no inicio, {summary}")


if __name__ == "__main__":
    main()
