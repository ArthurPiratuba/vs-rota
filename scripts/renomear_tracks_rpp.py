#!/usr/bin/env python3
"""Padroniza NAME das tracks no .rpp unificado.

Click e regência: {NN} - CLICK [BPM] - {bloco}
Demais: {NN} - {stem} - {bloco}
Nao altera nomes de arquivos no disco nem linhas FILE. Para cores das regions:
scripts/corrigir_regions_rpp.py + data/region_markers.json
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP

FILE_PATTERN = re.compile(r'^(\s*FILE )"(.*?)"(.*)$', re.MULTILINE)
# Apenas NAME no nivel da track (4 espacos), nao do <ITEM> (6 espacos).
TRACK_NAME_PATTERN = re.compile(r"^(    NAME )(.+)$", re.MULTILINE)


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


def parse_aberto_file(file_path: str) -> tuple[int, str, str] | None:
    parts = file_path.replace("/", "\\").split("\\")
    try:
        aberto_idx = next(i for i, p in enumerate(parts) if p.upper() == "ABERTO")
    except StopIteration:
        return None
    if aberto_idx + 2 >= len(parts):
        return None
    folder = parts[aberto_idx + 1]
    filename = parts[aberto_idx + 2]
    match = re.match(r"^(\d+)\s*-\s*(.+)$", folder)
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip(), filename


def classify_role(filename: str, old_track_name: str) -> str | None:
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


def stem_display(filename: str) -> str:
    """Stem sem extensao para track NAME. Glue nao entra no nome."""
    stem = filename
    for ext in (".mp3.mpeg", ".mpeg", ".mp3", ".wav"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    stem = stem.upper()
    stem = re.sub(r"[\s\-]*GLUED\b", "", stem)
    return re.sub(r"\s+", " ", stem).strip(" -")


BPM_AFTER_ROLE = re.compile(
    r"(?:CLICK|REGÊNCIA|REGENCIA)\s+(\d{2,3})\b",
    re.IGNORECASE,
)
ROLE_TRACK_PATTERN = re.compile(
    r"^\d{2} - (CLICK|REGÊNCIA)(?: (\d{2,3}))? - .+"
)


def extract_bpm(*texts: str) -> int | None:
    """BPM só quando o número já está colado em CLICK ou REGÊNCIA, entre 70 e 220."""
    for text in texts:
        if not text:
            continue
        match = BPM_AFTER_ROLE.search(text)
        if not match:
            continue
        bpm = int(match.group(1))
        if 70 <= bpm <= 220:
            return bpm
    return None


def format_click_regencia_name(order: int, role: str, song_name: str, bpm: int | None) -> str:
    head = f"{order:02d} - {role}"
    if bpm:
        head += f" {bpm}"
    return f"{head} - {song_name}"


def role_from_track_name(name: str) -> str | None:
    """click/regencia de uma faixa de bloco. Os faders CLICK e REGÊNCIA não entram."""
    match = ROLE_TRACK_PATTERN.match(name)
    if match:
        return "click" if match.group(1) == "CLICK" else "regencia"
    if name.endswith(" - CLICK"):
        return "click"
    if name.endswith(" - REGÊNCIA"):
        return "regencia"
    return None


def build_track_name(order: int, filename: str, song_name: str, role: str | None) -> str:
    if role in ("CLICK", "REGÊNCIA"):
        return format_click_regencia_name(order, role, song_name, extract_bpm(filename))
    parts = [f"{order:02d}", stem_display(filename), song_name]
    return " - ".join(parts)


def extract_track_blocks(rpp_text: str) -> list[str]:
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


ITEM_NAME_PATTERN = re.compile(r"^(      NAME )(.+)$", re.MULTILINE)


def item_label(filename: str, role: str | None, source_name: str) -> str:
    bpm = extract_bpm(filename, source_name)
    if role == "CLICK":
        return f"CLICK {bpm}" if bpm else "CLICK"
    if role == "REGÊNCIA":
        return f"REGÊNCIA {bpm}" if bpm else "REGÊNCIA"
    return stem_display(filename)


def apply_item_label(track_text: str, label: str) -> str:
    rendered = format_rpp_name(label)
    return ITEM_NAME_PATTERN.sub(lambda m: f"{m.group(1)}{rendered}", track_text, count=1)


def rename_track_block(track_text: str) -> tuple[str, bool]:
    file_match = FILE_PATTERN.search(track_text)
    if not file_match:
        return track_text, False

    parsed = parse_aberto_file(file_match.group(2))
    if not parsed:
        return track_text, False

    order, song_name, filename = parsed
    name_match = TRACK_NAME_PATTERN.search(track_text)
    if not name_match:
        return track_text, False

    old_name = parse_quoted_name(name_match.group(2).strip())
    role = classify_role(filename, old_name)
    new_name = build_track_name(order, filename, song_name, role)
    new_text = track_text
    changed = False
    if new_name != old_name:
        new_text = TRACK_NAME_PATTERN.sub(
            lambda m: f"{m.group(1)}{format_rpp_name(new_name)}",
            new_text,
            count=1,
        )
        changed = True

    label = item_label(filename, role, old_name)
    item_match = ITEM_NAME_PATTERN.search(new_text)
    if item_match and parse_quoted_name(item_match.group(2).strip()) != label:
        new_text = apply_item_label(new_text, label)
        changed = True
    return new_text, changed


def replace_track_blocks(rpp_text: str, tracks: list[str]) -> str:
    """Substitui blocos <TRACK> na ordem em que aparecem."""
    result: list[str] = []
    lines = rpp_text.splitlines(keepends=True)
    track_idx = 0
    i = 0
    while i < len(lines):
        if not re.match(r"^\s*<TRACK\b", lines[i]):
            result.append(lines[i])
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
                    result.append(tracks[track_idx])
                    track_idx += 1
                    i += 1
                    break
            i += 1
        else:
            result.extend(lines[start:])
            break
    while i < len(lines):
        result.append(lines[i])
        i += 1
    return "".join(result)


def main() -> None:
    rpp_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RPP
    if rpp_path.exists():
        dest = backup_rpp(rpp_path)
        print(f"Backup: {dest}")
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_track_blocks(text)

    renamed = 0
    new_blocks: list[str] = []
    for block in blocks:
        new_block, changed = rename_track_block(block)
        new_blocks.append(new_block)
        if changed:
            renamed += 1

    output = replace_track_blocks(text, new_blocks)
    rpp_path.write_text(output, encoding="utf-8")
    print(f"{rpp_path.name}: {renamed} tracks renomeadas ({len(blocks)} total)")


if __name__ == "__main__":
    main()
