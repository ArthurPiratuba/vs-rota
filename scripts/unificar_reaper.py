#!/usr/bin/env python3
"""Unifica os 23 projetos Reaper de ABERTO/ em um unico .rpp.

Nota: ABERTO/ contem apenas audio. Regenerar exige os .rpp individuais
(backup) ou editar o .rpp do projeto diretamente.
"""

from __future__ import annotations

import colorsys
import glob
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import format_rpp_name, stem_display  # noqa: E402

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
ABERTO = WORKSPACE / "ABERTO"
OUTPUT = projeto.RPP
REGION_DATA = WORKSPACE / "data" / "region_markers.json"
GAP_SECONDS = 2.0
def rgb_to_peakcol(r: int, g: int, b: int) -> int:
    return (r + 256 * g + 65536 * b) | 0x1000000


PEAKCOL_RED = 25198847
PEAKCOL_YELLOW = rgb_to_peakcol(255, 255, 0)
PEAKCOL_GREEN = rgb_to_peakcol(128, 255, 128)  # RGB 128,255,128
MONITOR_TRACK_PATTERN = re.compile(r"^\d{2} - MONITOR$")
PEAKCOL_BLUE = 33551872   # BGR: R=0, G=100, B=255
PEAKCOL_PURPLE = 28591792  # BGR: R=160, G=64, B=200
PEAKCOL_PATTERN = re.compile(r"^(\s*PEAKCOL )(\d+)(.*)$", re.MULTILINE)
GUID_PATTERN = re.compile(
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
)
FILE_PATTERN = re.compile(r'^(\s*FILE )"(.*?)"(.*)$', re.MULTILINE)
NAME_LINE_PATTERN = re.compile(r"^(    NAME )(.+)$", re.MULTILINE)
POSITION_PATTERN = re.compile(r"^(\s*POSITION )(\d+(?:\.\d+)?)(.*)$", re.MULTILINE)
LENGTH_PATTERN = re.compile(r"^(\s*LENGTH )(\d+(?:\.\d+)?)(.*)$", re.MULTILINE)
ISBUS_PATTERN = re.compile(r"^(\s*ISBUS )(-?\d+) (-?\d+)(.*)$", re.MULTILINE)
ITEM_PATTERN = re.compile(r"^\s*<ITEM\b", re.MULTILINE)
TRACK_VOLPAN_PATTERN = re.compile(
    r"^(\s*VOLPAN )([\d.-]+)( .+)$", re.MULTILINE
)
DEFAULT_TRACK_VOLUME = 1.0
CLICK_VOLUME_DB_OFFSET = -5.0
CLICK_VOLUME_LINEAR = 10 ** (CLICK_VOLUME_DB_OFFSET / 20.0)


def new_guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def folder_label(order: int, song_name: str) -> str:
    return f"{order:02d} - {song_name}"


def is_folder_parent(block: str) -> bool:
    if ITEM_PATTERN.search(block):
        return False
    match = ISBUS_PATTERN.search(block)
    return match is not None and match.group(2) == "1" and match.group(3) == "1"


def is_monitor_track(block: str) -> bool:
    return MONITOR_TRACK_PATTERN.match(get_track_name(block)) is not None


def monitor_label(order: int) -> str:
    return f"{order:02d} - MONITOR"


def set_isbus(track_text: str, first: int, second: int) -> str:
    return ISBUS_PATTERN.sub(
        lambda m: f"{m.group(1)}{first} {second}{m.group(4)}",
        track_text,
        count=1,
    )


TRACK_NOTES_PATTERN = re.compile(r"\n    <NOTES[\s\S]*?\n    >\n", re.MULTILINE)


def strip_track_notes(track_text: str) -> str:
    return TRACK_NOTES_PATTERN.sub("\n", track_text)


def peakcol_from_region_tail(tail: str) -> int:
    return int(tail.split()[1])


def load_region_peakcols() -> dict[int, int]:
    canonical = load_canonical_region_tails()
    if not canonical:
        return {}
    return {
        index: peakcol_from_region_tail(tail)
        for index, tail in canonical[0].items()
    }


def set_track_peakcol(track_text: str, peakcol: int) -> str:
    if PEAKCOL_PATTERN.search(track_text):
        return PEAKCOL_PATTERN.sub(f"\\g<1>{peakcol}\\g<3>", track_text, count=1)
    return track_text


def make_folder_track(order: int, song_name: str, peakcol: int) -> str:
    """Track-pasta vazia com titulo no NAME (sem NOTES — nao suportado nesta versao do Reaper)."""
    guid = new_guid()
    name_field = format_rpp_name(folder_label(order, song_name))
    return (
        f"  <TRACK {guid}\n"
        f"    NAME {name_field}\n"
        f"    PEAKCOL {peakcol}\n"
        f"    BEAT -1\n"
        f"    AUTOMODE 0\n"
        f"    PANLAWFLAGS 3\n"
        f"    VOLPAN 1 0 -1 -1 1\n"
        f"    MUTESOLO 0 0 0\n"
        f"    IPHASE 0\n"
        f"    PLAYOFFS 0 1\n"
        f"    ISBUS 1 1\n"
        f"    BUSCOMP 0 0 0 0 0\n"
        f"    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0\n"
        f"    FIXEDLANES 9 0 0 0 0\n"
        f"    SEL 0\n"
        f"    REC 0 0 1 0 0 0 0 0\n"
        f"    VU 64\n"
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


def make_monitor_track(order: int) -> str:
    """Track vazia de video/cifras; primeira filha da pasta, sempre amarela."""
    guid = new_guid()
    name_field = format_rpp_name(monitor_label(order))
    return (
        f"  <TRACK {guid}\n"
        f"    NAME {name_field}\n"
        f"    PEAKCOL {PEAKCOL_YELLOW}\n"
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
        f"    SEL 0\n"
        f"    REC 0 0 1 0 0 0 0 0\n"
        f"    VU 64\n"
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


def prepare_monitor_track(block: str, order: int) -> str:
    block = NAME_LINE_PATTERN.sub(
        lambda m: f"{m.group(1)}{format_rpp_name(monitor_label(order))}",
        block,
        count=1,
    )
    block = set_track_peakcol(block, PEAKCOL_YELLOW)
    return apply_folder_child(block, last_in_folder=False)


def emit_folder_children(
    blocks: list[str],
    order: int,
    audio_indices: list[int],
    peakcol: int,
    monitor_idx: int | None,
) -> list[str]:
    out: list[str] = []
    if monitor_idx is not None:
        out.append(prepare_monitor_track(blocks[monitor_idx], order))
    else:
        out.append(make_monitor_track(order))
    for pos, idx in enumerate(audio_indices):
        out.append(
            apply_folder_child(
                blocks[idx],
                last_in_folder=pos == len(audio_indices) - 1,
                block_peakcol=peakcol,
            )
        )
    return out


def rename_folder_track(block: str, order: int, song_name: str, peakcol: int) -> str:
    label = format_rpp_name(folder_label(order, song_name))
    block = strip_track_notes(block)
    block = NAME_LINE_PATTERN.sub(lambda m: f"{m.group(1)}{label}", block, count=1)
    return set_track_peakcol(block, peakcol)


def color_block_track(track_text: str, block_peakcol: int) -> str:
    """Cor da region no bloco; click/regencia e MONITOR mantem PEAKCOL."""
    if is_click_regencia_name(get_track_name(track_text)):
        return track_text
    if is_monitor_track(track_text):
        return set_track_peakcol(track_text, PEAKCOL_YELLOW)
    return set_track_peakcol(track_text, block_peakcol)


def apply_folder_child(
    track_text: str,
    *,
    last_in_folder: bool,
    block_peakcol: int | None = None,
) -> str:
    if last_in_folder:
        text = set_isbus(track_text, 2, -1)
    else:
        text = set_isbus(track_text, 0, 0)
    if block_peakcol is not None:
        text = color_block_track(text, block_peakcol)
    return text


def replace_all_track_blocks(rpp_text: str, tracks: list[str]) -> str:
    lines = rpp_text.splitlines(keepends=True)
    first: int | None = None
    after_last = 0
    i = 0
    while i < len(lines):
        if not re.match(r"^\s*<TRACK\b", lines[i]):
            i += 1
            continue
        if first is None:
            first = i
        depth = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"^\s*<", line):
                depth += 1
            elif re.match(r"^\s*>\s*$", line):
                depth -= 1
                if depth == 0:
                    after_last = i + 1
                    i += 1
                    break
            i += 1
        else:
            break
    if first is None:
        raise ValueError("Nenhum bloco <TRACK> encontrado no .rpp.")
    return "".join(lines[:first] + tracks + lines[after_last:])


@dataclass
class SongProject:
    order: int
    folder_name: str
    song_name: str
    rpp_path: Path
    project_dir: Path
    tracks: list[str]
    duration: float


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


def classify_track_role(filename: str, old_track_name: str) -> str | None:
    if re.search(r"CLICK", filename, re.IGNORECASE):
        return "CLICK"
    if re.search(r"REG[EÊ]N|CONTAGEM", filename, re.IGNORECASE):
        return "REGÊNCIA"
    if " - " in old_track_name:
        suffix = old_track_name.rsplit(" - ", 1)[-1]
        if re.match(r"^CLICK\b", suffix, re.IGNORECASE):
            return "CLICK"
        if re.search(r"REG[EÊ]N|CONTAGEM", suffix, re.IGNORECASE):
            return "REGÊNCIA"
    return None


def build_standard_track_name(
    order: int,
    filename: str,
    song_name: str,
    role: str | None,
) -> str:
    parts = [f"{order:02d}", stem_display(filename), song_name]
    if role:
        parts.append(role)
    return " - ".join(parts)


def get_file_basename(track_text: str) -> str | None:
    match = FILE_PATTERN.search(track_text)
    if not match:
        return None
    return Path(match.group(2)).name


def is_click_regencia_name(track_name: str) -> bool:
    from renomear_tracks_rpp import role_from_track_name

    return role_from_track_name(track_name) is not None


def click_target_volume(track_name: str) -> float | None:
    from renomear_tracks_rpp import role_from_track_name

    if role_from_track_name(track_name) == "click":
        return CLICK_VOLUME_LINEAR
    return None


def set_track_volume(track_text: str, volume: float) -> str:
    """Define o fader da track (primeiro VOLPAN antes de <ITEM>)."""
    item_pos = track_text.find("<ITEM")
    header = track_text[:item_pos] if item_pos >= 0 else track_text
    tail = track_text[item_pos:] if item_pos >= 0 else ""

    def repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{volume:g}{match.group(3)}"

    new_header = TRACK_VOLPAN_PATTERN.sub(repl, header, count=1)
    return new_header + tail


def apply_click_volume(track_text: str, track_name: str | None = None) -> str:
    name = track_name if track_name is not None else get_track_name(track_text)
    target = click_target_volume(name)
    if target is None:
        return track_text
    return set_track_volume(track_text, target)


def transform_track(
    track_text: str,
    song_name: str,
    project_dir: Path,
    time_offset: float,
    order: int,
    *,
    block_peakcol: int | None = None,
) -> str:
    text = replace_guids(track_text)

    old_name = get_track_name(track_text)
    filename = get_file_basename(track_text) or old_name
    role = classify_track_role(filename, old_name)
    new_name = build_standard_track_name(
        order=order,
        filename=filename,
        song_name=song_name,
        role=role,
    )
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
        if is_click_regencia_name(new_name):
            new_val = PEAKCOL_RED
        elif block_peakcol is not None:
            new_val = block_peakcol
        else:
            new_val = PEAKCOL_GREEN
        text = PEAKCOL_PATTERN.sub(f"\\g<1>{new_val}\\g<3>", text, count=1)

    text = apply_click_volume(text, new_name)
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


NAMED_REGION_PATTERN = re.compile(
    r'^\s*MARKER \d+ [\d.]+ "(?!")',
    re.MULTILINE,
)


def count_named_regions(text: str) -> int:
    """Regions com nome. O marcador de fim (`MARKER n pos \"\"`) nao entra."""
    return len(NAMED_REGION_PATTERN.findall(text))


def load_canonical_region_tails() -> tuple[dict[int, str], dict[int, str]] | None:
    if not REGION_DATA.exists():
        return None
    data = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    starts = {item["index"]: item["tail"] for item in data["starts"]}
    ends = {item["index"]: item["tail"] for item in data["ends"]}
    return starts, ends


def format_region_lines(
    index: int,
    start: float,
    end: float,
    name: str,
    total_regions: int,
    start_tails: dict[int, str] | None = None,
    end_tails: dict[int, str] | None = None,
) -> str:
    guid = new_guid()
    quoted = format_rpp_name(name)

    if start_tails and index in start_tails:
        start_tail = start_tails[index]
    else:
        color = region_color(index, total_regions)
        start_tail = f"1 {color} 1 R {guid} 0 1"

    if end_tails and index in end_tails:
        end_tail = end_tails[index]
    else:
        end_tail = "9" if index == total_regions else "1"

    start_line = f"  MARKER {index} {start} {quoted} {start_tail}\n"
    end_line = f'  MARKER {index} {end} "" {end_tail}\n'
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
    canonical = load_canonical_region_tails()
    start_tails = canonical[0] if canonical else None
    end_tails = canonical[1] if canonical else None

    region_peakcols = load_region_peakcols()
    total_regions = len(projects)
    time_offset = 0.0
    for idx, project in enumerate(projects, start=1):
        start = time_offset
        end = start + project.duration

        folder_peakcol = region_peakcols.get(
            project.order, region_color(project.order, total_regions)
        )
        transformed = [
            transform_track(
                track,
                project.song_name,
                project.project_dir,
                time_offset,
                project.order,
                block_peakcol=folder_peakcol,
            )
            for track in project.tracks
        ]
        parts.append(make_folder_track(project.order, project.song_name, folder_peakcol))
        parts.append(make_monitor_track(project.order))
        for t_i, track in enumerate(transformed):
            parts.append(
                apply_folder_child(
                    track,
                    last_in_folder=t_i == len(transformed) - 1,
                    block_peakcol=folder_peakcol,
                )
            )

        region_lines.append(
            format_region_lines(
                idx,
                start,
                end,
                project.song_name,
                len(projects),
                start_tails,
                end_tails,
            )
        )
        time_offset = end + GAP_SECONDS

    parts.extend(region_lines)
    parts.append(">\n")
    return "\n".join(parts)


def validate_output(text: str, projects: list[SongProject]) -> None:
    expected_audio = sum(len(p.tracks) for p in projects)
    expected_tracks = expected_audio + len(projects)
    track_count = len(re.findall(r"^\s*<TRACK ", text, re.MULTILINE))
    item_count = len(re.findall(r"^\s*<ITEM", text, re.MULTILINE))
    folder_count = len(re.findall(r"^\s+ISBUS 1 1\s*$", text, re.MULTILINE))
    marker_count = len(re.findall(r"^\s*MARKER ", text, re.MULTILINE))
    region_count = len(
        re.findall(r"^\s*MARKER \d+ [\d.]+ .+ 1 \d+ 1 R \{", text, re.MULTILINE)
    )

    if track_count != expected_tracks:
        raise ValueError(f"Esperado {expected_tracks} tracks, gerado {track_count}.")
    if folder_count != len(projects):
        raise ValueError(f"Esperado {len(projects)} pastas, gerado {folder_count}.")
    if item_count != expected_audio:
        raise ValueError(f"Esperado {expected_audio} items, gerado {item_count}.")
    if marker_count != len(projects) * 2:
        raise ValueError(
            f"Esperado {len(projects) * 2} linhas MARKER, gerado {marker_count}."
        )

    for track_block in extract_track_blocks(text):
        items = len(re.findall(r"^\s*<ITEM", track_block, re.MULTILINE))
        is_folder = bool(re.search(r"^\s+ISBUS 1 1\s*$", track_block, re.MULTILINE))
        if is_folder:
            if items != 0:
                raise ValueError(f"Pasta com {items} items (esperado 0).")
        elif items != 1:
            raise ValueError(f"Track com {items} items (esperado 1).")

    print(
        f"Validacao OK: {track_count} tracks, {item_count} items, "
        f"{region_count} regions."
    )


def main() -> None:
    projects = discover_projects()
    if len(projects) != 23:
        raise SystemExit(f"Esperados 23 projetos, encontrados {len(projects)}.")

    if OUTPUT.exists():
        existing_regions = count_named_regions(
            OUTPUT.read_text(encoding="utf-8", errors="replace")
        )
        if existing_regions > len(projects):
            raise SystemExit(
                f"Recusado: {OUTPUT.name} tem {existing_regions} regions e "
                f"so {len(projects)} projetos .rpp em ABERTO/. "
                "Regravar apagaria blocos adicionados por fora dos .rpp individuais."
            )

    template_text = projects[0].rpp_path.read_text(encoding="utf-8", errors="replace")
    unified = build_unified_project(projects, template_text)
    validate_output(unified, projects)

    if OUTPUT.exists():
        dest = backup_rpp(OUTPUT)
        print(f"Backup: {dest}")
    OUTPUT.write_text(unified, encoding="utf-8")
    print(f"Projeto unificado salvo em: {OUTPUT}")

    total_tracks = sum(len(p.tracks) for p in projects) + len(projects)
    total_duration = sum(p.duration for p in projects) + GAP_SECONDS * (len(projects) - 1)
    print(f"Musicas: {len(projects)} | Tracks: {total_tracks} | Duracao total: {total_duration:.2f}s")


if __name__ == "__main__":
    main()
