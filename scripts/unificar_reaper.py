#!/usr/bin/env python3
"""Unifica os 23 projetos Reaper de ABERTO/ em um unico .rpp.

Nota: ABERTO/ contem apenas audio. Regenerar exige os .rpp individuais
(backup) ou editar o ROTA DO CHOPP UNIFICADO.rpp diretamente.
"""

from __future__ import annotations

import colorsys
import glob
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
ABERTO = WORKSPACE / "ABERTO"
OUTPUT = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"
GAP_SECONDS = 2.0
def rgb_to_peakcol(r: int, g: int, b: int) -> int:
    return (r + 256 * g + 65536 * b) | 0x1000000


PEAKCOL_RED = 25198847
PEAKCOL_GREEN = rgb_to_peakcol(128, 255, 128)  # RGB 128,255,128
PEAKCOL_BLUE = 33551872   # BGR: R=0, G=100, B=255
PEAKCOL_PURPLE = 28591792  # BGR: R=160, G=64, B=200
PEAKCOL_PATTERN = re.compile(r"^(\s*PEAKCOL )(\d+)(.*)$", re.MULTILINE)
GUID_PATTERN = re.compile(
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
)
FILE_PATTERN = re.compile(r'^(\s*FILE )"(.*?)"(.*)$', re.MULTILINE)
NAME_LINE_PATTERN = re.compile(r"^(\s*NAME )(.+)$", re.MULTILINE)
POSITION_PATTERN = re.compile(r"^(\s*POSITION )(\d+(?:\.\d+)?)(.*)$", re.MULTILINE)
LENGTH_PATTERN = re.compile(r"^(\s*LENGTH )(\d+(?:\.\d+)?)(.*)$", re.MULTILINE)


@dataclass
class SongProject:
    order: int
    folder_name: str
    song_name: str
    rpp_path: Path
    project_dir: Path
    tracks: list[str]
    duration: float


def new_guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def strip_song_number(folder_name: str) -> str:
    match = re.match(r"^\d+\s*-\s*(.+)$", folder_name)
    return match.group(1).strip() if match else folder_name.strip()


def parse_quoted_name(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def format_rpp_name(name: str) -> str:
    if re.search(r'[\s"]', name):
        escaped = name.replace('"', '\\"')
        return f'"{escaped}"'
    return name


def to_reaper_path(path: Path) -> str:
    return str(path.resolve()).replace("/", "\\")


def replace_guids(text: str) -> str:
    return GUID_PATTERN.sub(lambda _: new_guid(), text)


def extract_track_blocks(rpp_text: str) -> list[str]:
    """Extrai blocos <TRACK ... > usando aninhamento por linhas < e >."""
    tracks: list[str] = []
    lines = rpp_text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if not re.match(r"^\s*<TRACK\b", lines[i]):
            i += 1
            continue

        start = i
        depth = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"^\s*<", line):
                depth += 1
            elif re.match(r"^\s*>\s*$", line):
                depth -= 1
                if depth == 0:
                    tracks.append("".join(lines[start : i + 1]))
                    i += 1
                    break
            i += 1
        else:
            break
    return tracks


def get_track_name(track_text: str) -> str:
    match = NAME_LINE_PATTERN.search(track_text)
    if not match:
        return "Track"
    return parse_quoted_name(match.group(2).strip())


def get_item_times(track_text: str) -> tuple[float, float]:
    pos_match = POSITION_PATTERN.search(track_text)
    len_match = LENGTH_PATTERN.search(track_text)
    position = float(pos_match.group(2)) if pos_match else 0.0
    length = float(len_match.group(2)) if len_match else 0.0
    return position, length


def is_click_regencia_name(track_name: str) -> bool:
    suffix = track_name.rsplit(" - ", 1)[-1] if " - " in track_name else track_name
    if re.match(r"^CLICK\b", suffix, re.IGNORECASE):
        return True
    if re.search(r"REG[EÊ]N", suffix, re.IGNORECASE):
        return True
    if re.search(r"CONTAGEM", suffix, re.IGNORECASE) and re.search(
        r"REG[EÊ]N", suffix, re.IGNORECASE
    ):
        return True
    return False


def transform_track(
    track_text: str,
    song_name: str,
    project_dir: Path,
    time_offset: float,
) -> str:
    text = replace_guids(track_text)

    old_name = get_track_name(track_text)
    new_name = f"{song_name} - {old_name}"
    text = NAME_LINE_PATTERN.sub(
        lambda m: f"{m.group(1)}{format_rpp_name(new_name)}",
        text,
        count=1,
    )

    def offset_position(match: re.Match[str]) -> str:
        new_pos = float(match.group(2)) + time_offset
        return f"{match.group(1)}{new_pos}{match.group(3)}"

    text = POSITION_PATTERN.sub(offset_position, text, count=1)

    def absolutize_file(match: re.Match[str]) -> str:
        rel_path = match.group(2)
        if os.path.isabs(rel_path):
            abs_path = rel_path
        else:
            abs_path = to_reaper_path(project_dir / rel_path)
        return f'{match.group(1)}"{abs_path}"{match.group(3)}'

    text = FILE_PATTERN.sub(absolutize_file, text)

    peak_match = PEAKCOL_PATTERN.search(text)
    if peak_match:
        new_val = PEAKCOL_RED if is_click_regencia_name(new_name) else PEAKCOL_GREEN
        text = PEAKCOL_PATTERN.sub(f"\\g<1>{new_val}\\g<3>", text, count=1)

    return text


def compute_project_duration(tracks: list[str]) -> float:
    max_end = 0.0
    for track in tracks:
        position, length = get_item_times(track)
        max_end = max(max_end, position + length)
    return max_end


def region_color(index: int, total: int) -> int:
    hue = (index - 1) / total
    red, green, blue = colorsys.hls_to_rgb(hue, 0.52, 0.82)
    r, g, b = int(red * 255), int(green * 255), int(blue * 255)
    return (r + 256 * g + 65536 * b) | 0x1000000


def format_region_lines(
    index: int,
    start: float,
    end: float,
    name: str,
    total_regions: int,
) -> str:
    guid = new_guid()
    quoted = format_rpp_name(name)
    color = region_color(index, total_regions)
    start_line = (
        f"  MARKER {index} {start} {quoted} 1 {color} 0 1 B {guid} 0\n"
    )
    end_line = f'  MARKER {index} {end} "" 1\n'
    return start_line + end_line


def discover_projects() -> list[SongProject]:
    pattern = str(ABERTO / "*" / "*.rpp")
    projects: list[SongProject] = []

    for rpp_path in glob.glob(pattern):
        path = Path(rpp_path)
        if "Backups" in path.parts:
            continue

        folder_name = path.parent.name
        order_match = re.match(r"^(\d+)\s*-", folder_name)
        if not order_match:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        tracks = extract_track_blocks(text)
        if not tracks:
            continue

        projects.append(
            SongProject(
                order=int(order_match.group(1)),
                folder_name=folder_name,
                song_name=strip_song_number(folder_name),
                rpp_path=path,
                project_dir=path.parent,
                tracks=tracks,
                duration=compute_project_duration(tracks),
            )
        )

    projects.sort(key=lambda p: p.order)
    return projects


def build_project_header(template_text: str) -> str:
    match = re.search(r"(<REAPER_PROJECT[\s\S]*?<PROJBAY\s*\n\s*>)", template_text)
    if not match:
        raise ValueError("Cabecalho do projeto template nao encontrado.")

    header = match.group(1)
    header = re.sub(
        r"^<REAPER_PROJECT .*$",
        f'<REAPER_PROJECT 0.1 "7.73/win64" {int(time.time())} 0',
        header,
        count=1,
        flags=re.MULTILINE,
    )
    header = re.sub(r"^  RENDER_FILE .*$", '  RENDER_FILE ""', header, flags=re.MULTILINE)
    header = re.sub(r"^  CURSOR .*$", "  CURSOR 0", header, flags=re.MULTILINE)
    header = re.sub(r"^  SELECTION .*$", "  SELECTION 0 0", header, flags=re.MULTILINE)
    header = re.sub(r"^  SELECTION2 .*$", "  SELECTION2 0 0", header, flags=re.MULTILINE)
    return header


def build_unified_project(projects: list[SongProject], template_text: str) -> str:
    parts = [build_project_header(template_text)]
    region_lines: list[str] = []

    time_offset = 0.0
    for idx, project in enumerate(projects, start=1):
        start = time_offset
        end = start + project.duration

        for track in project.tracks:
            parts.append(
                transform_track(track, project.song_name, project.project_dir, time_offset)
            )

        region_lines.append(
            format_region_lines(idx, start, end, project.song_name, len(projects))
        )
        time_offset = end + GAP_SECONDS

    parts.extend(region_lines)
    parts.append(">\n")
    return "\n".join(parts)


def validate_output(text: str, projects: list[SongProject]) -> None:
    expected_tracks = sum(len(p.tracks) for p in projects)
    track_count = len(re.findall(r"^\s*<TRACK ", text, re.MULTILINE))
    item_count = len(re.findall(r"^\s*<ITEM", text, re.MULTILINE))
    marker_count = len(re.findall(r"^\s*MARKER ", text, re.MULTILINE))
    region_count = len(re.findall(r"^\s*MARKER \d+ [\d.]+ .+ 1 0 1 R \{", text, re.MULTILINE))

    if track_count != expected_tracks:
        raise ValueError(f"Esperado {expected_tracks} tracks, gerado {track_count}.")
    if item_count != expected_tracks:
        raise ValueError(f"Esperado {expected_tracks} items, gerado {item_count}.")
    if marker_count != len(projects) * 2:
        raise ValueError(
            f"Esperado {len(projects) * 2} linhas MARKER, gerado {marker_count}."
        )

    for track_block in extract_track_blocks(text):
        items = len(re.findall(r"^\s*<ITEM", track_block, re.MULTILINE))
        if items != 1:
            raise ValueError(f"Track com {items} items (esperado 1).")

    print(
        f"Validacao OK: {track_count} tracks, {item_count} items, "
        f"{region_count} regions."
    )


def main() -> None:
    projects = discover_projects()
    if len(projects) != 23:
        raise SystemExit(f"Esperados 23 projetos, encontrados {len(projects)}.")

    template_text = projects[0].rpp_path.read_text(encoding="utf-8", errors="replace")
    unified = build_unified_project(projects, template_text)
    validate_output(unified, projects)

    OUTPUT.write_text(unified, encoding="utf-8")
    print(f"Projeto unificado salvo em: {OUTPUT}")

    total_tracks = sum(len(p.tracks) for p in projects)
    total_duration = sum(p.duration for p in projects) + GAP_SECONDS * (len(projects) - 1)
    print(f"Musicas: {len(projects)} | Tracks: {total_tracks} | Duracao total: {total_duration:.2f}s")


if __name__ == "__main__":
    main()
