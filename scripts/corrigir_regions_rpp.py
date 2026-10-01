#!/usr/bin/env python3
"""Restaura cores e formato Reaper das regions (MARKER) no .rpp unificado.

Cores canonicas em data/region_markers.json (extraidas do git HEAD).
Nao zera cores existentes: reaplica o tail salvo por indice da region.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"
REGION_DATA = WORKSPACE / "data" / "region_markers.json"

REGION_START = re.compile(
    r'^(\s*MARKER (\d+) ([\d.]+) "([^"]*)") .+$'
)
REGION_END = re.compile(
    r'^(\s*MARKER (\d+) ([\d.]+) "") .+$'
)


def load_canonical() -> tuple[dict[int, str], dict[int, str]]:
    data = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    start_tails = {item["index"]: item["tail"] for item in data["starts"]}
    end_tails = {item["index"]: item["tail"] for item in data["ends"]}
    return start_tails, end_tails


def restore_markers(text: str, start_tails: dict[int, str], end_tails: dict[int, str]) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    fixed = 0

    for line in lines:
        stripped = line.rstrip("\n")
        start_match = REGION_START.match(stripped)
        if start_match and start_match.group(4):
            index = int(start_match.group(2))
            pos = start_match.group(3)
            name = start_match.group(4)
            tail = start_tails.get(index)
            if tail is None:
                raise ValueError(f"Region {index} sem cor em {REGION_DATA.name}")
            out.append(f'  MARKER {index} {pos} "{name}" {tail}\n')
            fixed += 1
            continue

        end_match = REGION_END.match(stripped)
        if end_match:
            index = int(end_match.group(2))
            pos = end_match.group(3)
            tail = end_tails.get(index, "1")
            out.append(f'  MARKER {index} {pos} "" {tail}\n')
            fixed += 1
            continue

        out.append(line)

    return "".join(out), fixed


def main() -> None:
    rpp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RPP
    if not REGION_DATA.exists():
        raise SystemExit(
            f"Arquivo {REGION_DATA} ausente. "
            "Extraia do git: git show HEAD:\"ROTA DO CHOPP UNIFICADO.rpp\""
        )

    start_tails, end_tails = load_canonical()
    if rpp_path.exists():
        dest = backup_rpp(rpp_path)
        print(f"Backup: {dest}")
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    updated, fixed = restore_markers(text, start_tails, end_tails)
    rpp_path.write_text(updated, encoding="utf-8")
    print(f"OK: {fixed} linhas MARKER restauradas em {rpp_path.name}")


if __name__ == "__main__":
    main()
