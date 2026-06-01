#!/usr/bin/env python3
"""Exporta cada region do projeto unificado como MP3 stereo com pan do Reaper."""

from __future__ import annotations

import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import imageio_ffmpeg
except ImportError:
    print("Instale: pip install imageio-ffmpeg", file=sys.stderr)
    raise

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"
OUTPUT_DIR = WORKSPACE / "EXPORT_REGIONS"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class Region:
    index: int
    start: float
    end: float
    name: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Stem:
    path: Path
    volume: float
    pan: float


def strip_song_number(folder_name: str) -> str:
    match = re.match(r"^\d+\s*-\s*(.+)$", folder_name)
    return match.group(1).strip() if match else folder_name.strip()


def discover_songs() -> list[tuple[int, str, Path]]:
    songs: list[tuple[int, str, Path]] = []
    aberto = WORKSPACE / "ABERTO"
    for d in aberto.iterdir():
        if not d.is_dir():
            continue
        order_match = re.match(r"^(\d+)\s*-", d.name)
        if order_match:
            songs.append((int(order_match.group(1)), strip_song_number(d.name), d))
    songs.sort(key=lambda x: x[0])
    return songs


def parse_regions(text: str) -> list[Region]:
    pattern = re.compile(
        r'^\s*MARKER (\d+) ([\d.]+) "([^"]*)" \d+ 0 1 R',
        re.MULTILINE,
    )
    starts = {int(m.group(1)): (float(m.group(2)), m.group(3)) for m in pattern.finditer(text)}

    regions: list[Region] = []
    for idx in sorted(starts):
        start, name = starts[idx]
        end_match = re.search(
            rf'^\s*MARKER {idx} ([\d.]+) ""',
            text,
            re.MULTILINE,
        )
        if not end_match:
            raise ValueError(f"Fim da region {idx} nao encontrado.")
        end = float(end_match.group(1))
        regions.append(Region(index=idx, start=start, end=end, name=name))
    return regions


def extract_track_blocks(text: str) -> list[str]:
    tracks: list[str] = []
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if not re.match(r"^\s*<TRACK\b", lines[i]):
            i += 1
            continue
        start = i
        depth = 0
        while i < len(lines):
            if re.match(r"^\s*<", lines[i]):
                depth += 1
            elif re.match(r"^\s*>\s*$", lines[i]):
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
    match = re.search(r'^\s*NAME "([^"]+)"', track_text, re.MULTILINE)
    return match.group(1) if match else ""


def get_track_header(track_text: str) -> str:
    item_pos = track_text.find("<ITEM")
    return track_text[:item_pos] if item_pos >= 0 else track_text


def get_track_volpan(track_text: str) -> tuple[float, float]:
    match = re.search(
        r"^\s*VOLPAN ([\d.-]+) ([\d.-]+)",
        get_track_header(track_text),
        re.MULTILINE,
    )
    if not match:
        return 1.0, 0.0
    return float(match.group(1)), float(match.group(2))


def get_item_file(track_text: str) -> Path | None:
    match = re.search(r'^\s*FILE "(.+?)"', track_text, re.MULTILINE)
    if not match:
        return None
    rel = match.group(1).replace("\\", "/")
    path = (WORKSPACE / rel).resolve()
    return path if path.exists() else None


def parse_stem(track_text: str) -> Stem | None:
    path = get_item_file(track_text)
    if not path:
        return None
    volume, pan = get_track_volpan(track_text)
    return Stem(path=path, volume=volume, pan=pan)


def group_stems(tracks: list[str], songs: list[tuple[int, str, Path]]) -> dict[str, list[Stem]]:
    grouped: dict[str, list[Stem]] = {}
    idx = 0
    for _, song_name, _ in songs:
        stems: list[Stem] = []
        while idx < len(tracks):
            name = get_track_name(tracks[idx])
            if name.startswith(song_name + " - "):
                stem = parse_stem(tracks[idx])
                if stem:
                    stems.append(stem)
                idx += 1
            else:
                break
        if not stems:
            raise ValueError(f"Nenhum stem encontrado para: {song_name}")
        grouped[song_name] = stems
    if idx != len(tracks):
        raise ValueError(f"Tracks sobrando: {len(tracks) - idx}")
    return grouped


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "-", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned


def pan_gains(pan: float) -> tuple[float, float]:
    """Pan circular (-3 dB) como no Reaper (PANLAWFLAGS 3)."""
    pan = max(-1.0, min(1.0, pan))
    angle = (pan + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def mix_to_mp3(stems: list[Stem], output: Path, duration: float) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []

    for i, stem in enumerate(stems):
        inputs.extend(["-i", str(stem.path)])
        left, right = pan_gains(stem.pan)
        label = f"s{i}"
        filters.append(
            f"[{i}:a]aformat=channel_layouts=stereo,volume={stem.volume},"
            f"pan=stereo|c0={left:.6f}*FL+{left:.6f}*FR|"
            f"c1={right:.6f}*FL+{right:.6f}*FR[{label}]"
        )
        labels.append(f"[{label}]")

    if len(labels) == 1:
        graph = filters[0] + ",aformat=channel_layouts=stereo[aout]"
    else:
        joined = "".join(labels)
        graph = (
            ";".join(filters)
            + f";{joined}amix=inputs={len(labels)}:duration=longest:"
            + "dropout_transition=0:normalize=0,"
            + "aformat=channel_layouts=stereo,alimiter=limit=0.98[aout]"
        )

    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        graph,
        "-map",
        "[aout]",
        "-t",
        f"{duration:.6f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "320k",
        str(output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg falhou")


def main() -> None:
    text = RPP.read_text(encoding="utf-8")
    regions = parse_regions(text)
    songs = discover_songs()
    tracks = extract_track_blocks(text)
    stems_by_song = group_stems(tracks, songs)

    if len(regions) != len(songs):
        raise SystemExit(
            f"Regions ({len(regions)}) e musicas ({len(songs)}) nao batem."
        )

    OUTPUT_DIR.mkdir(exist_ok=True)

    for region, (order, song_name, _) in zip(regions, songs):
        if region.name != song_name:
            raise SystemExit(
                f"Ordem divergente: region {region.index}={region.name!r} "
                f"vs pasta {order}={song_name!r}"
            )

        stems = stems_by_song[song_name]
        left = sum(1 for s in stems if s.pan < 0)
        right = sum(1 for s in stems if s.pan >= 0)
        out_name = f"{order:02d} - {safe_filename(song_name)}.mp3"
        output = OUTPUT_DIR / out_name

        print(
            f"[{region.index:02d}/23] {song_name} "
            f"({len(stems)} stems, L={left} R={right})..."
        )
        mix_to_mp3(stems, output, region.duration)
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"       -> {output.name} ({size_mb:.1f} MB)")

    print(f"\nConcluido: {len(regions)} arquivos em {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
