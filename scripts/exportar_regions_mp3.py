#!/usr/bin/env python3
"""Exporta cada region do projeto unificado como MP3 mono, 320 kbps.

As trilhas de cada bloco sao as mesmas do video (exportar_blocos_video.py):
item no lugar certo da region, volume da track, do item e do barramento do
grupo, tracks e itens mudos fora. Cada trilha entra com esquerdo e direito
somados num canal so.

    python scripts/exportar_regions_mp3.py --mono                 # todos
    python scripts/exportar_regions_mp3.py --mono --faltando      # so os que nao existem
    python scripts/exportar_regions_mp3.py --mono --from-order 1 --to-order 23
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from exportar_blocos_video import (  # noqa: E402
    FFMPEG,
    RPP,
    extract_track_blocks,
    filtro_do_mix,
    parse_regions,
    safe_filename,
    stems_do_bloco,
    volumes_dos_grupos,
)

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
OUTPUT_DIR = WORKSPACE / "EXPORT_REGIONS"


def exportar_mp3(stems, destino: Path, duracao: float) -> None:
    script = destino.with_suffix(".filter.txt")
    script.write_text(filtro_do_mix(stems), encoding="utf-8")
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        # O filtro do video comeca as trilhas na entrada 1 (a 0 e o cartaz).
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        *[arg for stem in stems for arg in ("-i", str(stem.path))],
        "-filter_complex_script",
        str(script),
        "-map",
        "[aout]",
        "-t",
        f"{duracao:.6f}",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "320k",
        str(destino),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    script.unlink(missing_ok=True)
    if result.returncode != 0:
        destino.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or f"ffmpeg falhou em {destino.name}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Exporta cada bloco como MP3 mono.")
    parser.add_argument("--mono", action="store_true", help="aceito por compatibilidade; a saida e sempre mono")
    parser.add_argument("--from-order", type=int, default=None, help="primeiro bloco, inclusive")
    parser.add_argument("--to-order", type=int, default=None, help="ultimo bloco, inclusive")
    parser.add_argument("--faltando", action="store_true", help="so os blocos sem mp3 em EXPORT_REGIONS")
    args = parser.parse_args()

    text = RPP.read_text(encoding="utf-8")
    regions = parse_regions(text)
    if args.from_order is not None:
        regions = [r for r in regions if r.index >= args.from_order]
    if args.to_order is not None:
        regions = [r for r in regions if r.index <= args.to_order]
    OUTPUT_DIR.mkdir(exist_ok=True)
    if args.faltando:
        regions = [
            r for r in regions
            if not (OUTPUT_DIR / f"{r.index:02d} - {safe_filename(r.name)}.mp3").exists()
        ]
    if not regions:
        raise SystemExit("Nenhum bloco para exportar.")

    blocks = extract_track_blocks(text)
    grupos = volumes_dos_grupos(blocks)
    total = len(regions)
    for posicao, region in enumerate(regions, start=1):
        stems = stems_do_bloco(blocks, region, grupos)
        destino = OUTPUT_DIR / f"{region.index:02d} - {safe_filename(region.name)}.mp3"
        print(f"[{posicao:02d}/{total:02d}] {region.name} ({len(stems)} trilhas, mono)...", flush=True)
        exportar_mp3(stems, destino, region.duration)
        tamanho = destino.stat().st_size / (1024 * 1024)
        print(f"       -> {destino.name} ({tamanho:.1f} MB)", flush=True)

    print(f"Concluido: {total} arquivos em {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
