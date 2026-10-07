#!/usr/bin/env python3
"""Exporta cada bloco como video Full HD leve, com audio mono de um canal.

O cartaz e o mesmo texto do monitor. Cada trilha entra inteira, o lado
esquerdo e o direito juntos, num unico canal. A imagem e estatica, em 1920x1080.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import imageio_ffmpeg
except ImportError:
    print("Instale: pip install imageio-ffmpeg", file=sys.stderr)
    raise

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from inserir_texto_monitor import (  # noqa: E402
    PPTX,
    TAMANHO_PADRAO,
    linhas_do_slide,
    slide_do_bloco,
)

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"
OUTPUT_DIR = WORKSPACE / "EXPORT_VIDEOS"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONTE = Path(r"C:\Windows\Fonts\arial.ttf")
LARGURA = 1920
ALTURA = 1080
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aif", ".aiff"}
GRUPO_POR_BIT = {
    1: "CLICK",
    2: "REGÊNCIA",
    4: "METAIS",
    8: "TECLAS",
    16: "CORDAS",
    32: "PERCUSSÃO",
}


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
    start: float
    length: float
    offset: float


def parse_regions(text: str) -> list[Region]:
    pattern = re.compile(r'^\s*MARKER (\d+) ([\d.]+) "([^"]*)" \d+ \d+ 1 R', re.MULTILINE)
    starts = {int(m.group(1)): (float(m.group(2)), m.group(3)) for m in pattern.finditer(text)}
    regions: list[Region] = []
    for idx in sorted(starts):
        start, name = starts[idx]
        end_match = re.search(rf'^\s*MARKER {idx} ([\d.]+) ""', text, re.MULTILINE)
        if not end_match:
            raise ValueError(f"Fim da region {idx} nao encontrado.")
        regions.append(Region(index=idx, start=start, end=float(end_match.group(1)), name=name))
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


def track_name(block: str) -> str:
    # O Reaper grava sem aspas o nome sem espaco (os barramentos: NAME CLICK).
    match = re.search(r'^    NAME (?:"(.*)"|(\S+))$', block, re.MULTILINE)
    return (match.group(1) or match.group(2)) if match else ""


def track_header(block: str) -> str:
    item = block.find("    <ITEM\n")
    return block[:item] if item >= 0 else block


def track_volume(block: str) -> float:
    match = re.search(r"^    VOLPAN ([\d.eE+-]+)", track_header(block), re.MULTILINE)
    return float(match.group(1)) if match else 1.0


def track_muted(block: str) -> bool:
    match = re.search(r"^    MUTESOLO (\d+)", track_header(block), re.MULTILINE)
    return match is not None and match.group(1) != "0"


def grupo_do_slave(block: str) -> str | None:
    match = re.search(r"^    GROUP_FLAGS (.+)$", track_header(block), re.MULTILINE)
    if not match:
        return None
    parts = [int(float(x)) for x in match.group(1).split()]
    if len(parts) <= 5 or parts[5] == 0:
        return None
    return GRUPO_POR_BIT.get(parts[5])


def volumes_dos_grupos(blocks: list[str]) -> dict[str, float]:
    volumes: dict[str, float] = {}
    for block in blocks:
        name = track_name(block)
        if name in GRUPO_POR_BIT.values():
            volumes[name] = 0.0 if track_muted(block) else track_volume(block)
    return volumes


def iter_items(block: str) -> list[str]:
    lines = block.splitlines()
    items: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i] == "    <ITEM":
            j = i + 1
            while j < len(lines) and lines[j] != "    >":
                j += 1
            items.append("\n".join(lines[i : j + 1]))
            i = j + 1
        else:
            i += 1
    return items


def item_arquivo(item: str) -> Path | None:
    match = re.search(r'FILE "([^"]+)"', item)
    if not match:
        return None
    path = (WORKSPACE / match.group(1).replace("\\", "/")).resolve()
    if path.suffix.lower() not in AUDIO_EXT or not path.exists():
        return None
    return path


def stems_do_bloco(
    blocks: list[str],
    region: Region,
    grupos: dict[str, float],
) -> list[Stem]:
    prefix = f"{region.index:02d} - "
    stems: list[Stem] = []
    for block in blocks:
        name = track_name(block)
        if not name.startswith(prefix) or name.endswith("MONITOR"):
            continue
        if track_muted(block):
            continue
        grupo = grupo_do_slave(block)
        ganho = track_volume(block) * grupos.get(grupo or "", 1.0)
        for item in iter_items(block):
            if re.search(r"^      MUTE ([1-9])", item, re.MULTILINE):
                continue
            path = item_arquivo(item)
            if path is None:
                continue
            position = float(re.search(r"^      POSITION ([\d.eE+-]+)", item, re.MULTILINE).group(1))
            length = float(re.search(r"^      LENGTH ([\d.eE+-]+)", item, re.MULTILINE).group(1))
            offset = float(re.search(r"^      SOFFS ([\d.eE+-]+)", item, re.MULTILINE).group(1))
            item_vol = float(re.search(r"^      VOLPAN ([\d.eE+-]+)", item, re.MULTILINE).group(1))
            rate_match = re.search(r"^      PLAYRATE ([\d.eE+-]+)", item, re.MULTILINE)
            rate = float(rate_match.group(1)) if rate_match else 1.0
            inicio = max(position, region.start)
            fim = min(position + length, region.end)
            if fim - inicio <= 0.01:
                continue
            stems.append(
                Stem(
                    path=path,
                    volume=ganho * item_vol,
                    start=inicio - region.start,
                    length=(fim - inicio) * rate,
                    offset=offset + (inicio - position) * rate,
                )
            )
    if not stems:
        raise ValueError(f"Bloco {region.index} sem audio.")
    return stems


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "-", name)
    return re.sub(r"\s+", " ", cleaned).strip(" .")


def _fonte(tamanho: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTE), max(8, int(round(tamanho))))


def _largura(fonte: ImageFont.FreeTypeFont, texto: str) -> float:
    return fonte.getlength(texto)


def desenhar_cartaz(linhas: list[tuple[str, str | None]], destino: Path) -> None:
    titulos = [texto for texto, tom in linhas if tom is None]
    pares = [(texto, tom) for texto, tom in linhas if tom is not None]
    titulo = titulos[0] if titulos else "VS"
    px = ALTURA * TAMANHO_PADRAO
    fonte = _fonte(px)
    espaco = _largura(fonte, " ")
    mais_larga = _largura(fonte, titulo)
    for musica, tom in pares:
        largura = _largura(fonte, musica)
        if tom:
            largura += espaco + _largura(fonte, tom)
        mais_larga = max(mais_larga, largura)
    n = 1 + len(pares)
    escala = 1.0
    if mais_larga > LARGURA * 0.92:
        escala = (LARGURA * 0.92) / mais_larga
    if px * 1.35 * n * escala > ALTURA * 0.88:
        escala = min(escala, (ALTURA * 0.88) / (px * 1.35 * n))
    px *= escala
    fonte = _fonte(px)
    espaco = _largura(fonte, " ")
    passo = px * 1.35
    imagem = Image.new("RGB", (LARGURA, ALTURA), (0, 0, 0))
    draw = ImageDraw.Draw(imagem)
    y = (ALTURA - passo * n) * 0.5
    vermelho = (255, 0, 0)
    branco = (255, 255, 255)

    def desenhar(texto: str, x: float, cor: tuple[int, int, int]) -> None:
        draw.text((x, y), texto, font=fonte, fill=cor)

    tw = _largura(fonte, titulo)
    desenhar(titulo, (LARGURA - tw) * 0.5, vermelho)
    y += passo
    for musica, tom in pares:
        lw = _largura(fonte, musica)
        if tom:
            rw = _largura(fonte, tom)
            x = (LARGURA - lw - espaco - rw) * 0.5
            desenhar(musica, x, branco)
            desenhar(tom, x + lw + espaco, vermelho)
        else:
            desenhar(musica, (LARGURA - lw) * 0.5, branco)
        y += passo
    destino.parent.mkdir(parents=True, exist_ok=True)
    imagem.save(destino, "PNG", optimize=True)


def filtro_do_mix(stems: list[Stem]) -> str:
    partes: list[str] = []
    rotulos: list[str] = []
    for i, stem in enumerate(stems):
        atraso = int(round(stem.start * 1000))
        partes.append(
            f"[{i + 1}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=start={stem.offset:.6f}:duration={stem.length:.6f},asetpts=PTS-STARTPTS,"
            f"volume={stem.volume:.6f},"
            f"pan=mono|c0=0.5*FL+0.5*FR,"
            f"adelay={atraso}[s{i}]"
        )
        rotulos.append(f"[s{i}]")
    if len(rotulos) == 1:
        soma = rotulos[0]
    else:
        partes.append(
            f"{''.join(rotulos)}amix=inputs={len(rotulos)}:duration=longest:"
            "dropout_transition=0:normalize=0[mix]"
        )
        soma = "[mix]"
    partes.append(
        f"{soma}alimiter=limit=0.98,aformat=channel_layouts=mono[aout]"
    )
    return ";".join(partes)


def exportar_bloco(region: Region, stems: list[Stem], cartaz: Path, destino: Path) -> None:
    script = destino.with_suffix(".filter.txt")
    script.write_text(filtro_do_mix(stems), encoding="utf-8")
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-framerate",
        "2",
        "-i",
        str(cartaz),
        *[arg for stem in stems for arg in ("-i", str(stem.path))],
        "-filter_complex_script",
        str(script),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-t",
        f"{region.duration:.6f}",
        "-r",
        "2",
        "-s",
        f"{LARGURA}x{ALTURA}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "stillimage",
        "-crf",
        "32",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(destino),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    script.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffmpeg falhou em {destino.name}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Exporta cada bloco como video Full HD.")
    parser.add_argument("--from-order", type=int, default=1)
    parser.add_argument("--to-order", type=int, default=32)
    args = parser.parse_args()

    text = RPP.read_text(encoding="utf-8")
    regions = [r for r in parse_regions(text) if args.from_order <= r.index <= args.to_order]
    if not regions:
        raise SystemExit("Nenhum bloco no intervalo.")
    blocks = extract_track_blocks(text)
    grupos = volumes_dos_grupos(blocks)
    OUTPUT_DIR.mkdir(exist_ok=True)
    cartazes = OUTPUT_DIR / "_cartaz"
    total = len(regions)

    for posicao, region in enumerate(regions, start=1):
        stems = stems_do_bloco(blocks, region, grupos)
        linhas = linhas_do_slide(PPTX, slide_do_bloco(region.index))
        cartaz = cartazes / f"{region.index:02d}.png"
        desenhar_cartaz(linhas, cartaz)
        destino = OUTPUT_DIR / f"{region.index:02d} - {safe_filename(region.name)}.mp4"
        print(f"[{posicao:02d}/{total:02d}] {region.name} ({len(stems)} trilhas)...", flush=True)
        exportar_bloco(region, stems, cartaz, destino)
        tamanho = destino.stat().st_size / (1024 * 1024)
        print(f"       -> {destino.name} ({tamanho:.1f} MB)", flush=True)

    print(f"Concluido: {total} videos em {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
