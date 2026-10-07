#!/usr/bin/env python3
"""Anexa blocos de musica dos zips wetransfer_*.zip ao .rpp unificado.

Os blocos 1-23 nao mudam de numero, pasta, posicao nem cor.
Os zips entram no fim da timeline, numerados 24-32, em ordem alfabetica
entre si, com o mesmo gap de 2s dos blocos atuais.

Dry-run por padrao. --apply extrai o audio, grava o .rpp e atualiza
data/region_markers.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import (  # noqa: E402
    build_track_name,
    classify_role,
    format_rpp_name,
    stem_display,
)
from unificar_reaper import (  # noqa: E402
    CLICK_VOLUME_LINEAR,
    DEFAULT_TRACK_VOLUME,
    GAP_SECONDS,
    PEAKCOL_RED,
    count_named_regions,
    extract_track_blocks,
    make_folder_track,
    make_monitor_track,
    new_guid,
    region_color,
    replace_all_track_blocks,
)

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP
ABERTO = WORKSPACE / "ABERTO"
REGION_DATA = WORKSPACE / "data" / "region_markers.json"
BACKUPS = WORKSPACE / "Backups"

FIRST_ORDER = 24
TOTAL_REGIONS = 32
REGION_23_NAME = "VAGABUNDO - AGARRA AGARRA - VIRA E MEXE"
REGION_23_END = 10609.704384495468
ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
PLAYLIST_ID_BASE = 1073741824

TITLE_ACCENTS = {
    "ALO GALERA": "ALÔ GALERA",
}

EXPECTED_TITLES = [
    "ALÔ GALERA",
    "BARRIL DE CHOPP",
    "BEIJA BEIJA",
    "CAMISA MANCHADA",
    "CORONEL BOOGEY MARCHA DO COELHO",
    "DAI QUE MIORO",
    "HEYO HEYO",
    "NOVA IORQUE",
    "PONTO CHICK",
]

ZIP_NAME = re.compile(
    r"^wetransfer_(.+?)_(\d{4}-\d{2}-\d{2})_(\d+)\.zip$",
    re.IGNORECASE,
)
FILE_PATH_PATTERN = re.compile(r'^\s*FILE "(.*?)"', re.MULTILINE)
MARKER_END_23 = re.compile(r'^  MARKER 23 ([\d.]+) "" .*\n', re.MULTILINE)
MARKER_START_23 = re.compile(r'^  MARKER 23 [\d.]+ "([^"]+)" .*\n', re.MULTILINE)
TRACK_NAME_PATTERN = re.compile(r'^    NAME (.+)$', re.MULTILINE)
HEADER_VOLPAN = re.compile(r"^    VOLPAN ([\d.eE+-]+) ([\d.eE+-]+) ", re.MULTILINE)
ISBUS_LINE = re.compile(r"^    ISBUS (-?\d+) (-?\d+)\s*$", re.MULTILINE)
ITEM_LINE = re.compile(r"^    <ITEM\b", re.MULTILINE)
PLAYLIST_LINE = re.compile(r"^(\s+)(1073741\d+) 1\n$")

# MPEG1 / MPEG2 / MPEG2.5, Layer III.
_BITRATE_L3 = {
    3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    0: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_SAMPLE_RATE = {
    3: [44100, 48000, 32000],
    2: [22050, 24000, 16000],
    0: [11025, 12000, 8000],
}


@dataclass
class StemPlan:
    zip_member: str
    filename: str
    role: str | None
    duration: float


@dataclass
class BlockPlan:
    order: int
    song_name: str
    zip_path: Path
    folder_name: str
    stems: list[StemPlan]
    start: float
    end: float
    peakcol: int


def alpha_key(text: str) -> str:
    folded = unicodedata.normalize("NFD", text.upper())
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")


def format_time(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


def block_title_from_zip(zip_name: str) -> str:
    match = ZIP_NAME.match(zip_name)
    if not match:
        raise SystemExit(f"Nome de zip fora do padrao: {zip_name}")
    slug = match.group(1)
    if slug.lower().startswith("bloco-"):
        slug = slug[6:]
    title = re.sub(r"\s+", " ", slug.replace("-", " ")).strip().upper()
    if not title:
        raise SystemExit(f"Zip sem nome de bloco: {zip_name}")
    return TITLE_ACCENTS.get(title, title)


def normalize_audio_filename(name: str) -> str:
    base = Path(name).name
    if not base.lower().endswith(".mp3"):
        raise SystemExit(f"Stem nao e mp3: {name}")
    stem = base[:-4].upper()
    stem = re.sub(r"\s+", " ", stem).strip()
    stem = re.sub(r"\bBLOCO\b", " ", stem)
    stem = stem.replace("CONTEGEM", "CONTAGEM")
    stem = stem.replace("REGENCIA", "REGÊNCIA")
    stem = re.sub(r"\bGAITAS\b", "SANFONAS", stem)
    stem = re.sub(r"\bGAITA\b", "SANFONA", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        raise SystemExit(f"Nome de stem vazio depois da normalizacao: {name}")
    return f"{stem}.mp3"


def role_rank(role: str | None) -> int:
    if role == "CLICK":
        return 0
    if role == "REGÊNCIA":
        return 1
    return 2


def _id3v2_skip(data: bytes) -> int:
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = (
        ((data[6] & 0x7F) << 21)
        | ((data[7] & 0x7F) << 14)
        | ((data[8] & 0x7F) << 7)
        | (data[9] & 0x7F)
    )
    footer = 10 if data[5] & 0x10 else 0
    return 10 + size + footer


def _frame_at(data: bytes, offset: int, end: int) -> tuple[int, int, int] | None:
    if offset + 4 > end:
        return None
    b1, b2, b3 = data[offset + 1], data[offset + 2], data[offset + 3]
    if data[offset] != 0xFF or (b1 & 0xE0) != 0xE0:
        return None
    version = (b1 >> 3) & 0x03
    layer = (b1 >> 1) & 0x03
    bitrate_idx = (b2 >> 4) & 0x0F
    sr_idx = (b2 >> 2) & 0x03
    padding = (b2 >> 1) & 0x01
    if version == 1 or layer != 1 or bitrate_idx in (0, 15) or sr_idx == 3:
        return None
    bitrate = _BITRATE_L3[version][bitrate_idx] * 1000
    sample_rate = _SAMPLE_RATE[version][sr_idx]
    if version == 3:
        frame_len = int(144 * bitrate / sample_rate) + padding
        samples = 1152
    else:
        frame_len = int(72 * bitrate / sample_rate) + padding
        samples = 576
    if frame_len < 4 or offset + frame_len > end:
        return None
    nxt = offset + frame_len
    if nxt + 1 < end and not (data[nxt] == 0xFF and (data[nxt + 1] & 0xE0) == 0xE0):
        # Lixo curto depois do ultimo frame e normal. No meio do arquivo, descarta sync falso.
        if end - nxt > frame_len:
            return None
    return frame_len, samples, sample_rate


def mp3_duration(data: bytes) -> float:
    offset = _id3v2_skip(data)
    end = len(data)
    if end >= 128 and data[end - 128 : end - 125] == b"TAG":
        end -= 128
    samples = 0
    sample_rate: int | None = None
    guard = 0
    while offset + 4 <= end:
        frame = _frame_at(data, offset, end)
        if frame is None:
            offset += 1
            guard += 1
            if guard > 65536:
                break
            continue
        guard = 0
        frame_len, frame_samples, rate = frame
        if sample_rate is None:
            sample_rate = rate
        elif rate != sample_rate:
            raise SystemExit("MP3 com sample rate mudando no meio do arquivo.")
        samples += frame_samples
        offset += frame_len
    if sample_rate is None or samples <= 0:
        raise SystemExit("Nao foi possivel ler a duracao do MP3.")
    duration = samples / sample_rate
    if not 1.0 <= duration <= 3600.0:
        raise SystemExit(f"Duracao de MP3 fora do esperado: {duration:.3f}s")
    return duration


def load_stems(zip_path: Path) -> list[StemPlan]:
    stems: list[StemPlan] = []
    seen: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            base = Path(info.filename).name
            if not base or base.startswith(".") or base.startswith("__"):
                continue
            filename = normalize_audio_filename(base)
            if filename in seen:
                raise SystemExit(
                    f"Nomes colidem em {zip_path.name}: {seen[filename]!r} e {base!r} "
                    f"viram {filename}"
                )
            seen[filename] = base
            duration = mp3_duration(archive.read(info))
            stems.append(
                StemPlan(
                    zip_member=info.filename,
                    filename=filename,
                    role=classify_role(filename, ""),
                    duration=duration,
                )
            )
    if not stems:
        raise SystemExit(f"Zip sem mp3: {zip_path.name}")
    clicks = [stem for stem in stems if stem.role == "CLICK"]
    regs = [stem for stem in stems if stem.role == "REGÊNCIA"]
    if len(clicks) != 1 or len(regs) != 1:
        raise SystemExit(
            f"{zip_path.name}: esperado 1 CLICK e 1 REGÊNCIA, "
            f"achados {len(clicks)} e {len(regs)}."
        )
    stems.sort(key=lambda stem: (role_rank(stem.role), alpha_key(stem_display(stem.filename))))
    return stems


def discover_blocks(timeline_end: float) -> list[BlockPlan]:
    zips = sorted(WORKSPACE.glob("wetransfer_*.zip"))
    if not zips:
        raise SystemExit("Nenhum wetransfer_*.zip na raiz do projeto.")
    titled = [(block_title_from_zip(path.name), path) for path in zips]
    titles = [title for title, _ in titled]
    if sorted(titles, key=alpha_key) != EXPECTED_TITLES:
        raise SystemExit(
            "Os zips nao batem com os 9 blocos esperados.\n"
            f"  achados: {titles}\n"
            f"  esperados: {EXPECTED_TITLES}"
        )
    titled.sort(key=lambda item: alpha_key(item[0]))
    blocks: list[BlockPlan] = []
    cursor = timeline_end + GAP_SECONDS
    for offset, (title, path) in enumerate(titled):
        order = FIRST_ORDER + offset
        stems = load_stems(path)
        duration = max(stem.duration for stem in stems)
        start = cursor
        end = start + duration
        blocks.append(
            BlockPlan(
                order=order,
                song_name=title,
                zip_path=path,
                folder_name=f"{order} - {title}",
                stems=stems,
                start=start,
                end=end,
                peakcol=region_color(order, TOTAL_REGIONS),
            )
        )
        cursor = end + GAP_SECONDS
    return blocks


def parse_timeline_end(text: str) -> float:
    start = MARKER_START_23.search(text)
    end = MARKER_END_23.search(text)
    if not start or not end:
        raise SystemExit("Region 23 nao encontrada no .rpp.")
    if start.group(1) != REGION_23_NAME:
        raise SystemExit(f"Region 23 inesperada: {start.group(1)!r}")
    end_time = float(end.group(1))
    if abs(end_time - REGION_23_END) > 1e-6:
        raise SystemExit(f"Fim da region 23 inesperado: {end_time}")
    if count_named_regions(text) != 23:
        raise SystemExit("O .rpp nao esta com exatamente 23 regions nomeadas.")
    indexes = [int(value) for value in re.findall(r"^  MARKER (\d+) ", text, re.MULTILINE)]
    if max(indexes) != 23:
        raise SystemExit(f"Ja existe marker acima de 23 (max {max(indexes)}).")
    return end_time


def marker_lines_until(text: str, max_index: int = 23) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if not line.startswith("  MARKER "):
            continue
        index = int(line.split()[1])
        if index <= max_index:
            lines.append(line)
    return lines


def file_paths(text: str) -> list[str]:
    return FILE_PATH_PATTERN.findall(text)


def disk_path(rpp_path: str) -> Path:
    return WORKSPACE / rpp_path.replace("\\", "/")


def rpp_audio_path(folder_name: str, filename: str) -> str:
    return f"ABERTO\\{folder_name}\\{filename}"


def pan_and_volume(role: str | None) -> tuple[float, float]:
    if role == "CLICK":
        return -1.0, CLICK_VOLUME_LINEAR
    if role == "REGÊNCIA":
        return -1.0, DEFAULT_TRACK_VOLUME
    return 1.0, DEFAULT_TRACK_VOLUME


def make_audio_track(
    block: BlockPlan,
    stem: StemPlan,
    *,
    iid: int,
    last_in_folder: bool,
) -> str:
    guid = new_guid()
    iguid = new_guid()
    item_guid = new_guid()
    pan, volume = pan_and_volume(stem.role)
    peakcol = PEAKCOL_RED if stem.role in ("CLICK", "REGÊNCIA") else block.peakcol
    isbus = "2 -1" if last_in_folder else "0 0"
    track_name = build_track_name(block.order, stem.filename, block.song_name, stem.role)
    item_name = stem_display(stem.filename)
    rpp_file = rpp_audio_path(block.folder_name, stem.filename)
    return (
        f"  <TRACK {guid}\n"
        f"    NAME {format_rpp_name(track_name)}\n"
        f"    PEAKCOL {peakcol}\n"
        f"    BEAT -1\n"
        f"    AUTOMODE 0\n"
        f"    PANLAWFLAGS 3\n"
        f"    VOLPAN {volume:g} {pan:g} -1 -1 1\n"
        f"    MUTESOLO 0 0 0\n"
        f"    IPHASE 0\n"
        f"    PLAYOFFS 0 1\n"
        f"    ISBUS {isbus}\n"
        f"    BUSCOMP 0 0 0 0 0\n"
        f"    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0\n"
        f"    FIXEDLANES 9 0 0 0 0\n"
        f"    LANEREC -1 -1 -1 0\n"
        f"    SEL 0\n"
        f"    REC 0 0 1 0 0 0 0 0\n"
        f"    VU 64\n"
        f"    TRACKHEIGHT 0 1 0 0 0 0 0\n"
        f"    INQ 0 0 0 0.5 100 0 0 100\n"
        f"    NCHAN 2\n"
        f"    FX 1\n"
        f"    TRACKID {guid}\n"
        f"    PERF 0\n"
        f"    MIDIOUT -1\n"
        f"    MAINSEND 1 0\n"
        f"    <ITEM\n"
        f"      POSITION {format_time(block.start)}\n"
        f"      SNAPOFFS 0\n"
        f"      LENGTH {format_time(stem.duration)}\n"
        f"      LOOP 1\n"
        f"      ALLTAKES 0\n"
        f"      FADEIN 1 0 0 1 0 0 0\n"
        f"      FADEOUT 1 0 0 1 0 0 0\n"
        f"      MUTE 0 0\n"
        f"      SEL 0\n"
        f"      IGUID {iguid}\n"
        f"      IID {iid}\n"
        f"      NAME {format_rpp_name(item_name)}\n"
        f"      VOLPAN 1 0 1 -1\n"
        f"      SOFFS 0\n"
        f"      PLAYRATE 1 1 0 -1 0 0.0025\n"
        f"      CHANMODE 0\n"
        f"      GUID {item_guid}\n"
        f"      <SOURCE MP3\n"
        f'        FILE "{rpp_file}" 1\n'
        f"      >\n"
        f"    >\n"
        f"  >\n"
    )


def next_iid(text: str) -> int:
    found = [int(value) for value in re.findall(r"^      IID (\d+)\s*$", text, re.MULTILINE)]
    if not found:
        raise SystemExit("Nenhum IID no .rpp.")
    return max(found) + 1


def build_new_tracks(text: str, blocks: list[BlockPlan]) -> list[str]:
    iid = next_iid(text)
    tracks: list[str] = []
    for block in blocks:
        tracks.append(make_folder_track(block.order, block.song_name, block.peakcol))
        tracks.append(make_monitor_track(block.order))
        for index, stem in enumerate(block.stems):
            tracks.append(
                make_audio_track(
                    block,
                    stem,
                    iid=iid,
                    last_in_folder=index == len(block.stems) - 1,
                )
            )
            iid += 1
    return tracks


def marker_block(blocks: list[BlockPlan]) -> str:
    lines: list[str] = []
    for block in blocks:
        quoted = format_rpp_name(block.song_name)
        lines.append(
            f"  MARKER {block.order} {format_time(block.start)} {quoted} "
            f"1 {block.peakcol} 1 R {ZERO_GUID} 0 1\n"
        )
        lines.append(f'  MARKER {block.order} {format_time(block.end)} "" 1\n')
    return "".join(lines)


def insert_markers(text: str, block: str) -> str:
    matches = list(MARKER_END_23.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"Marker de fim da region 23: {len(matches)} ocorrencias.")
    end = matches[0].end()
    return text[:end] + block + text[end:]


def extend_region_playlist(text: str, orders: list[int]) -> str:
    lines = text.splitlines(keepends=True)
    indexes = [i for i, line in enumerate(lines) if PLAYLIST_LINE.match(line)]
    if len(indexes) != 23:
        raise SystemExit(
            f"Playlist S&M de regions: esperadas 23 linhas, achadas {len(indexes)}."
        )
    if indexes != list(range(indexes[0], indexes[0] + 23)):
        raise SystemExit("Playlist S&M de regions nao esta em um bloco continuo.")
    ids = [int(lines[i].split()[0]) for i in indexes]
    expected = [PLAYLIST_ID_BASE + number for number in range(1, 24)]
    if ids != expected:
        raise SystemExit("IDs da playlist S&M nao sao as regions 1-23.")
    indent = PLAYLIST_LINE.match(lines[indexes[-1]]).group(1)  # type: ignore[union-attr]
    extra = [f"{indent}{PLAYLIST_ID_BASE + order} 1\n" for order in orders]
    insert_at = indexes[-1] + 1
    return "".join(lines[:insert_at] + extra + lines[insert_at:])


def assemble(text: str, blocks: list[BlockPlan]) -> str:
    if replace_all_track_blocks(text, extract_track_blocks(text)) != text:
        raise SystemExit("Roundtrip das tracks existentes nao e identico. Abortado.")
    tracks = extract_track_blocks(text) + build_new_tracks(text, blocks)
    updated = replace_all_track_blocks(text, tracks)
    updated = insert_markers(updated, marker_block(blocks))
    updated = extend_region_playlist(updated, [block.order for block in blocks])
    return updated


def track_name(block: str) -> str:
    match = TRACK_NAME_PATTERN.search(block)
    if not match:
        return ""
    return match.group(1).strip().strip('"')


def header_volpan(block: str) -> tuple[float, float] | None:
    item_at = block.find("\n    <ITEM\n")
    header = block if item_at < 0 else block[:item_at]
    match = HEADER_VOLPAN.search(header)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def validate(
    text: str,
    blocks: list[BlockPlan],
    original: str,
    *,
    check_new_files: bool,
    already_missing: set[str],
) -> None:
    old_markers = marker_lines_until(original)
    new_markers = marker_lines_until(text)
    if new_markers != old_markers:
        raise SystemExit("Markers 1-23 foram alterados.")

    old_files = file_paths(original)
    new_files = file_paths(text)
    if new_files[: len(old_files)] != old_files:
        raise SystemExit("FILE dos blocos 1-23 mudou.")

    if count_named_regions(text) != TOTAL_REGIONS:
        raise SystemExit(
            f"Esperadas {TOTAL_REGIONS} regions, achadas {count_named_regions(text)}."
        )

    rebuilt = extract_track_blocks(text)
    original_count = len(extract_track_blocks(original))
    expected_new = sum(2 + len(block.stems) for block in blocks)
    if len(rebuilt) != original_count + expected_new:
        raise SystemExit(
            f"Tracks: esperadas {original_count + expected_new}, achadas {len(rebuilt)}."
        )

    missing_old = [
        path
        for path in old_files
        if path not in already_missing and not disk_path(path).is_file()
    ]
    if missing_old:
        raise SystemExit(
            "FILE antigo sumiu do disco:\n  " + "\n  ".join(missing_old[:8])
        )

    click_token = f"{CLICK_VOLUME_LINEAR:g}"
    by_order: dict[int, list[str]] = {block.order: [] for block in blocks}
    for track in rebuilt[original_count:]:
        name = track_name(track)
        order = int(name.split(" - ", 1)[0])
        by_order[order].append(track)
        items = len(ITEM_LINE.findall(track))
        files = FILE_PATH_PATTERN.findall(track)
        is_folder = "\n    ISBUS 1 1\n" in track
        is_monitor = name.endswith(" - MONITOR") or name.split(" - ", 1)[-1] == "MONITOR"
        if is_folder or is_monitor:
            if items != 0 or files:
                raise SystemExit(f"Pasta ou MONITOR com audio: {name}")
            continue
        if items != 1 or len(files) != 1:
            raise SystemExit(f"Track de audio sem exatamente 1 item: {name}")
        volpan = header_volpan(track)
        if volpan is None:
            raise SystemExit(f"Track sem VOLPAN: {name}")
        volume, pan = volpan
        if name.endswith(" - CLICK"):
            if f"{volume:g}" != click_token or pan != -1.0:
                raise SystemExit(f"CLICK fora do padrao: {name} vol={volume} pan={pan}")
        elif name.endswith(" - REGÊNCIA"):
            if volume != 1.0 or pan != -1.0:
                raise SystemExit(f"REGÊNCIA fora do padrao: {name} vol={volume} pan={pan}")
        else:
            if volume != 1.0 or pan != 1.0:
                raise SystemExit(f"Stem musical fora do padrao: {name} vol={volume} pan={pan}")
        if check_new_files and not disk_path(files[0]).is_file():
            raise SystemExit(f"MP3 novo ausente: {files[0]}")

    for block in blocks:
        group = by_order[block.order]
        if len(group) != 2 + len(block.stems):
            raise SystemExit(f"Bloco {block.order} com {len(group)} tracks.")
        isbus = [ISBUS_LINE.search(track) for track in group]
        if any(match is None for match in isbus):
            raise SystemExit(f"Bloco {block.order} sem ISBUS.")
        pairs = [(match.group(1), match.group(2)) for match in isbus if match]
        if pairs[0] != ("1", "1") or pairs[1] != ("0", "0"):
            raise SystemExit(f"Pasta/MONITOR do bloco {block.order} com ISBUS errado.")
        if pairs[-1] != ("2", "-1"):
            raise SystemExit(f"Ultima faixa do bloco {block.order} nao fecha a pasta.")
        if any(pair != ("0", "0") for pair in pairs[1:-1]):
            raise SystemExit(f"Faixa do meio do bloco {block.order} com ISBUS errado.")

    expected_new_files = [
        rpp_audio_path(block.folder_name, stem.filename)
        for block in blocks
        for stem in block.stems
    ]
    if new_files[len(old_files) :] != expected_new_files:
        raise SystemExit("FILE novos nao estao na ordem dos stems.")

    playlist_ids = [
        int(line.split()[0])
        for line in text.splitlines(keepends=True)
        if PLAYLIST_LINE.match(line)
    ]
    expected_ids = [PLAYLIST_ID_BASE + number for number in range(1, TOTAL_REGIONS + 1)]
    if playlist_ids != expected_ids:
        raise SystemExit("Playlist S&M nao ficou com as regions 1-32 em ordem.")


def print_plan(blocks: list[BlockPlan]) -> None:
    print("=== Blocos novos (fim da timeline) ===")
    for block in blocks:
        left = sum(1 for stem in block.stems if stem.role in ("CLICK", "REGÊNCIA"))
        right = len(block.stems) - left
        print(
            f"\n{block.order:02d} {block.song_name}"
            f"  [{format_time(block.start)} .. {format_time(block.end)}]"
            f"  {len(block.stems)} stems  L={left} R={right}"
        )
        print(f"  pasta: ABERTO/{block.folder_name}")
        print(f"  zip:   {block.zip_path.name}")
        for stem in block.stems:
            pan, volume = pan_and_volume(stem.role)
            side = "L" if pan < 0 else "R"
            db = "-5 dB" if stem.role == "CLICK" else "0 dB"
            role = f" [{stem.role}]" if stem.role else ""
            print(
                f"    {side} {db:5}  {format_time(stem.duration):>10}s  "
                f"{stem.filename}{role}"
            )


def preflight_folders(blocks: list[BlockPlan]) -> None:
    for block in blocks:
        folder = ABERTO / block.folder_name
        if folder.exists():
            raise SystemExit(f"Pasta ja existe, nada foi gravado: {folder}")


def extract_blocks(blocks: list[BlockPlan]) -> list[Path]:
    created: list[Path] = []
    try:
        for block in blocks:
            folder = ABERTO / block.folder_name
            folder.mkdir()
            created.append(folder)
            with zipfile.ZipFile(block.zip_path) as archive:
                for stem in block.stems:
                    target = folder / stem.filename
                    target.write_bytes(archive.read(stem.zip_member))
    except BaseException:
        for folder in created:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    return created


def remove_folders(folders: list[Path]) -> None:
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)


def backup_region_markers() -> Path:
    BACKUPS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUPS / f"region_markers-{stamp}.json"
    shutil.copy2(REGION_DATA, dest)
    return dest


def update_region_markers(blocks: list[BlockPlan]) -> None:
    data = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    existing = {entry["index"] for entry in data["starts"]}
    if existing & {block.order for block in blocks}:
        raise SystemExit("region_markers.json ja tem indice >= 24.")
    for block in blocks:
        data["starts"].append(
            {
                "index": block.order,
                "start": format_time(block.start),
                "name": block.song_name,
                "tail": f"1 {block.peakcol} 1 R {ZERO_GUID} 0 1",
            }
        )
        data["ends"].append(
            {
                "index": block.order,
                "end": format_time(block.end),
                "tail": "1",
            }
        )
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with REGION_DATA.open("w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(payload)


def write_rpp(text: str) -> None:
    with RPP.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Anexa os blocos dos zips wetransfer ao projeto Reaper."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Extrai os mp3 e grava o .rpp (padrao: dry-run)",
    )
    args = parser.parse_args()

    if not RPP.is_file():
        raise SystemExit(f".rpp nao encontrado: {RPP}")
    if not REGION_DATA.is_file():
        raise SystemExit(f"JSON nao encontrado: {REGION_DATA}")
    if not ABERTO.is_dir():
        raise SystemExit(f"ABERTO/ nao encontrado: {ABERTO}")

    original = RPP.read_text(encoding="utf-8")
    timeline_end = parse_timeline_end(original)
    blocks = discover_blocks(timeline_end)
    preflight_folders(blocks)
    already_missing = {path for path in file_paths(original) if not disk_path(path).is_file()}
    if already_missing:
        print(
            "FILE ja ausente antes desta alteracao (a linha no .rpp e preservada):"
        )
        for path in sorted(already_missing):
            print(f"  {path}")

    updated = assemble(original, blocks)
    validate(
        updated,
        blocks,
        original,
        check_new_files=False,
        already_missing=already_missing,
    )
    print_plan(blocks)

    if not args.apply:
        print("\n[dry-run] Nenhuma alteracao. Use --apply para extrair e gravar.")
        return

    print("\n=== Aplicando ===")
    created = extract_blocks(blocks)
    try:
        validate(
            updated,
            blocks,
            original,
            check_new_files=True,
            already_missing=already_missing,
        )
    except BaseException:
        remove_folders(created)
        raise

    rpp_backup = backup_rpp(RPP)
    json_backup = backup_region_markers()
    print(f"Backup rpp:  {rpp_backup}")
    print(f"Backup json: {json_backup}")

    regions_before = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    write_rpp(updated)
    update_region_markers(blocks)

    written = RPP.read_text(encoding="utf-8")
    if written != updated:
        raise SystemExit("O .rpp gravado nao confere com o texto validado.")
    validate(
        written,
        blocks,
        original,
        check_new_files=True,
        already_missing=already_missing,
    )
    saved = json.loads(REGION_DATA.read_text(encoding="utf-8"))
    if saved["starts"][:23] != regions_before["starts"]:
        raise SystemExit("region_markers.json alterou as regions 1-23.")
    if saved["ends"][:23] != regions_before["ends"]:
        raise SystemExit("region_markers.json alterou os fins 1-23.")
    if [entry["name"] for entry in saved["starts"][-9:]] != EXPECTED_TITLES:
        raise SystemExit("region_markers.json nao ficou com os 9 nomes novos.")
    print(
        f"\nGravado: {len(blocks)} blocos, "
        f"{sum(len(block.stems) for block in blocks)} stems, "
        f"regions {FIRST_ORDER}-{TOTAL_REGIONS}."
    )


if __name__ == "__main__":
    main()
