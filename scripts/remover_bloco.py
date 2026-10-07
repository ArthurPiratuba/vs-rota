#!/usr/bin/env python3
"""Tira o ultimo bloco do projeto, o inverso do adicionar_bloco.py.

So o ultimo: os numeros sao sequenciais e nenhum outro bloco muda. Sai:

- region e playlist S&M no .rpp, data/region_markers.json;
- a pasta, o monitor e as tracks "NN - ..." do bloco; os faders de grupo sao
  refeitos por scripts/grupos_tracks.py;
- o cartaz do bloco anterior volta a ir ate o fim da region dele;
- o slide N do .pptx do repertorio, com o PDF exportado de novo;
- a pasta ABERTO\\N - <nome> e EXPORT_REGIONS/EXPORT_VIDEOS do bloco.

Para levar o bloco a outro projeto, copie a pasta dele antes.
Sem --aplicar so mostra o plano. Com o Reaper ou o .pptx aberto nao grava.

    python scripts/remover_bloco.py 35
    python scripts/remover_bloco.py 35 --aplicar
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import grupos_tracks  # noqa: E402
from adicionar_bloco import (  # noqa: E402
    ABERTO,
    EXPORT_REGIONS,
    EXPORT_VIDEOS,
    PLAYLIST_ID_BASE,
    PLAYLIST_LINE,
    REGION_MARKERS,
    RPP,
    SLIDE_TYPE,
    ajustar_monitor_anterior,
    regions,
)
from adicionar_musicas_bloco import PDF, exportar_pdf, pptx_aberto, reaper_aberto, safe_filename  # noqa: E402
from backup_rpp import backup_rpp  # noqa: E402
from inserir_texto_monitor import PPTX, linhas_do_slide, slide_do_bloco  # noqa: E402
from unificar_reaper import extract_track_blocks, get_track_name, replace_all_track_blocks  # noqa: E402


def sem_o_bloco(texto: str, ordem: int, linhas_anterior) -> tuple[str, int]:
    regs = regions(texto)
    blocos = extract_track_blocks(texto)
    if replace_all_track_blocks(texto, blocos) != texto:
        raise SystemExit("Roundtrip das tracks nao e identico. Abortado.")
    prefixo = f"{ordem:02d} - "
    do_bloco = [i for i, b in enumerate(blocos) if get_track_name(b).startswith(prefixo)]
    saem = len(do_bloco)
    if not saem or do_bloco != list(range(len(blocos) - saem, len(blocos))):
        raise SystemExit(f"As tracks do bloco {ordem} nao sao as ultimas do projeto.")
    texto = replace_all_track_blocks(texto, blocos[:-saem])

    texto, n = re.subn(rf"^  MARKER {ordem} .*\n", "", texto, flags=re.MULTILINE)
    if n != 2:
        raise SystemExit(f"Region {ordem}: {n} linhas MARKER.")

    playlist = list(PLAYLIST_LINE.finditer(texto))
    if [int(m.group(2)) for m in playlist] != [PLAYLIST_ID_BASE + i for i in range(1, ordem + 1)]:
        raise SystemExit(f"Playlist S&M nao e 1..{ordem} em ordem.")
    a, b = playlist[-2].end(), playlist[-1].end()
    texto = texto[:a] + texto[b:]

    # O bloco anterior volta a ser o ultimo: o cartaz vai ate o fim da region.
    anterior = ordem - 1
    inicio = regs[anterior - 1]["fim"] if anterior > 1 else regs[anterior]["inicio"]
    texto = ajustar_monitor_anterior(texto, anterior, linhas_anterior, inicio, regs[anterior]["fim"])
    return texto, saem


def pptx_sem_o_slide(numero: int, destino: Path) -> None:
    with zipfile.ZipFile(PPTX) as z:
        nomes = z.namelist()
        existentes = sorted(int(n) for n in re.findall(r"ppt/slides/slide(\d+)\.xml", "\n".join(nomes)))
        if existentes != list(range(1, numero + 1)):
            raise SystemExit(f"O .pptx tem {len(existentes)} slides; o bloco precisa ser o ultimo, o {numero}.")
        rels_slide = z.read(f"ppt/slides/_rels/slide{numero}.xml.rels").decode("utf-8")
        if "notesSlide" in rels_slide:
            raise SystemExit("Slide com anotacoes; nao sei tirar.")
        pres = z.read("ppt/presentation.xml").decode("utf-8")
        rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
        tipos = z.read("[Content_Types].xml").decode("utf-8")
        rel = re.search(rf'<Relationship Id="(rId\d+)" Type="{re.escape(SLIDE_TYPE)}" Target="slides/slide{numero}\.xml"/>', rels)
        if not rel:
            raise SystemExit(f"slide{numero}.xml sem relationship na apresentacao.")
        rels = rels.replace(rel.group(0), "")
        pres, n = re.subn(rf'<p:sldId id="\d+" r:id="{rel.group(1)}"/>', "", pres)
        tipos, m = re.subn(rf'<Override PartName="/ppt/slides/slide{numero}\.xml" [^>]*/>', "", tipos)
        if n != 1 or m != 1:
            raise SystemExit("Slide nao achado em presentation.xml ou [Content_Types].xml.")
        trocas = {"ppt/presentation.xml": pres, "ppt/_rels/presentation.xml.rels": rels, "[Content_Types].xml": tipos}
        fora = {f"ppt/slides/slide{numero}.xml", f"ppt/slides/_rels/slide{numero}.xml.rels"}
        with zipfile.ZipFile(destino, "w") as saida:
            for info in z.infolist():
                if info.filename in fora:
                    continue
                dados = trocas[info.filename].encode("utf-8") if info.filename in trocas else z.read(info)
                saida.writestr(info, dados)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Tira o ultimo bloco do projeto.")
    parser.add_argument("ordem", type=int, help="numero do bloco; tem que ser o ultimo")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isso so mostra o plano")
    args = parser.parse_args()

    if pptx_aberto():
        parser.exit(1, f"Feche {PPTX.name} no PowerPoint antes; aberto ele fica travado.\n")
    texto = RPP.read_text(encoding="utf-8")
    regs = regions(texto)
    ordem = args.ordem
    if ordem != max(regs) or ordem < 2:
        raise SystemExit(f"So o ultimo bloco ({max(regs)}) sai, e o projeto fica com pelo menos um.")
    nome = regs[ordem]["nome"]
    pasta = ABERTO / f"{ordem} - {nome}"
    if not pasta.is_dir():
        raise SystemExit(f"Pasta nao encontrada: {pasta}")

    linhas = linhas_do_slide(PPTX, slide_do_bloco(ordem))
    if linhas[0] != (f"VS BLOCO {ordem}", None):
        raise SystemExit(f"Slide {slide_do_bloco(ordem)} nao e o VS BLOCO {ordem}: {linhas}")
    linhas_anterior = linhas_do_slide(PPTX, slide_do_bloco(ordem - 1))
    novo_texto, saem = sem_o_bloco(texto, ordem, linhas_anterior)
    pptx_tmp = PPTX.with_name(PPTX.stem + ".novo.pptx")
    pptx_sem_o_slide(slide_do_bloco(ordem), pptx_tmp)

    arquivo = f"{ordem:02d} - {safe_filename(nome)}"
    exports = [
        EXPORT_REGIONS / f"{arquivo}.mp3",
        EXPORT_VIDEOS / f"{arquivo}.mp4",
        EXPORT_VIDEOS / "_cartaz" / f"{ordem:02d}.png",
    ]
    print(f"Bloco {ordem}: {nome}")
    print(f"  region {regs[ordem]['inicio']} .. {regs[ordem]['fim']} e playlist S&M")
    print(f"  {saem} tracks ({ordem:02d} - ...); faders de grupo refeitos")
    print(f"  cartaz do bloco {ordem - 1} volta a ir ate {regs[ordem - 1]['fim']}")
    print(f"  slide {slide_do_bloco(ordem)}: {linhas}; pdf refeito")
    print(f"  apaga ABERTO/{pasta.name} ({len(list(pasta.glob('*.mp3')))} mp3)")
    for e in exports:
        if e.exists():
            print(f"  apaga {e.relative_to(e.parents[1])}")

    if not args.aplicar:
        pptx_tmp.unlink()
        print("Plano apenas. Rode com --aplicar para gravar.")
        return
    if reaper_aberto():
        pptx_tmp.unlink()
        raise SystemExit("Feche o Reaper antes: ele regravaria o .rpp antigo.")

    print(f"Backup .rpp: {backup_rpp(RPP)}")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    shutil.copy2(PPTX, PPTX.with_name(f"{PPTX.stem}-{stamp}.pptx.bak"))
    if PDF.exists():
        shutil.copy2(PDF, PDF.with_name(f"{PDF.stem}-{stamp}.pdf.bak"))
    print(f"Backup .pptx e .pdf: {stamp}")

    RPP.write_text(novo_texto, encoding="utf-8", newline="")
    marcadores = json.loads(REGION_MARKERS.read_text(encoding="utf-8"))
    for chave in ("starts", "ends"):
        marcadores[chave] = [m for m in marcadores[chave] if m["index"] != ordem]
    REGION_MARKERS.write_text(json.dumps(marcadores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    grupos_tracks.aplicar(RPP, grupos_tracks.carregar_correcoes())

    final = RPP.read_text(encoding="utf-8")
    if max(regions(final)) != ordem - 1 or any(
        get_track_name(b).startswith(f"{ordem:02d} - ") for b in extract_track_blocks(final)
    ):
        raise SystemExit("O bloco nao saiu inteiro do .rpp.")

    pptx_tmp.replace(PPTX)
    exportar_pdf(PPTX, PDF)
    print(f"PDF: {PDF}")
    shutil.rmtree(pasta)
    for e in exports:
        e.unlink(missing_ok=True)
    print(f"Pronto: bloco {ordem} removido.")


if __name__ == "__main__":
    main()
