#!/usr/bin/env python3
"""Corrige formato MARKER das regions para o Region Manager do Reaper."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"

REGION_START = re.compile(
    r'^(\s*MARKER (\d+) ([\d.]+) "([^"]*)") .+$'
)
REGION_END = re.compile(
    r'^(\s*MARKER (\d+) ([\d.]+) "") .+$'
)


def new_guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def parse_start_fields(line: str) -> tuple[int, str | None]:
    """Extrai flag de region e cor customizada da linha de inicio."""
    parts = line.split()
    # MARKER idx pos "name" ...
    if len(parts) < 5:
        return 0, None

    tail = parts[4:]  # apos nome (pode ser quoted multi-word - usar regex melhor)
    match = REGION_START.match(line)
    if not match:
        return 0, None

    after_name = line[match.end() :].strip().split()
    if not after_name:
        return 0, None

    region_flag = 0
    color: str | None = None

    # Formato errado gerado pelo script: color 0 1 R guid ...
    if after_name[0].isdigit() and int(after_name[0]) > 1:
        color = after_name[0]
        region_flag = 1
    elif after_name[0] == "1":
        region_flag = 1
        if len(after_name) > 1 and after_name[1].isdigit() and int(after_name[1]) > 16:
            color = after_name[1]

    return region_flag, color


def extract_guid(line: str) -> str | None:
    match = re.search(r"\{[0-9A-Fa-f-]{36}\}", line)
    return match.group(0) if match else None


def format_start(
    index: int,
    start: str,
    name: str,
    color: str | None,
    guid: str,
) -> str:
    color_val = color if color and int(color) > 0 else "0"
    return (
        f'  MARKER {index} {start} "{name}" '
        f"1 {color_val} 0 1 B {guid} 0\n"
    )


def format_end(index: int, end: str) -> str:
    return f'  MARKER {index} {end} "" 1\n'


def fix_markers(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    fixed = 0

    while i < len(lines):
        line = lines[i]
        start_match = REGION_START.match(line.rstrip("\n"))
        if start_match and start_match.group(4):
            index = int(start_match.group(2))
            start = start_match.group(3)
            name = start_match.group(4)
            _, color = parse_start_fields(line.rstrip("\n"))
            guid = extract_guid(line) or new_guid()

            out.append(format_start(index, start, name, color, guid))
            fixed += 1

            if i + 1 < len(lines):
                end_match = REGION_END.match(lines[i + 1].rstrip("\n"))
                if end_match and int(end_match.group(2)) == index:
                    out.append(format_end(index, end_match.group(3)))
                    fixed += 1
                    i += 2
                    continue

            i += 1
            continue

        end_match = REGION_END.match(line.rstrip("\n"))
        if end_match:
            index = int(end_match.group(2))
            out.append(format_end(index, end_match.group(3)))
            fixed += 1
            i += 1
            continue

        out.append(line)
        i += 1

    return "".join(out), fixed


def main() -> None:
    text = RPP.read_text(encoding="utf-8")
    updated, fixed = fix_markers(text)
    RPP.write_text(updated, encoding="utf-8")
    print(f"OK: {fixed} linhas MARKER corrigidas em {RPP}")


if __name__ == "__main__":
    main()
