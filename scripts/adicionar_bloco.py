#!/usr/bin/env python3
"""Acrescenta um bloco novo no fim do projeto, com todas as convencoes.

Os audios (mp3) vem de uma pasta de entrada, por padrao a raiz do projeto.
O bloco entra depois do ultimo, com o numero seguinte e o gap de 2 s:

- pasta ABERTO\\N - <nome> com os audios (nomes em maiusculas, sem a palavra
  BLOCO; com --bpm o CLICK.mp3 vira CLICK <bpm>.mp3);
- region e playlist S&M no .rpp; data/region_markers.json;
- track-pasta (cor da region), N - MONITOR com o cartaz no Video processor e
  uma track por audio: click e regencia primeiro, os outros em ordem alfabetica;
- click e regencia no L, o resto no R; click a -5 dB; cores e nomes como nos
  outros blocos;
- faders de grupo (CLICK, REGÊNCIA, METAIS, TECLAS, CORDAS, PERCUSSÃO) refeitos
  por scripts/grupos_tracks.py, com grupos_classificacao.json e grupos.html;
- slide "VS BLOCO N" no fim do .pptx do repertorio, PDF exportado de novo;
- EXPORT_REGIONS/NN - <nome>.mp3 (mono) e EXPORT_VIDEOS/NN - <nome>.mp4.

Nome e slide seguem a grafia do adicionar_musicas_bloco.py:
"NOME", "NOME@Tom", "NOME|Texto do Slide@Tom". Varias musicas viram
"MUSICA 1 - MUSICA 2". Sem tom fica "??".

Sem --aplicar so mostra o plano. Com o Reaper ou o .pptx aberto nao grava.
Os audios de entrada sao apagados no fim, se tudo deu certo (--manter-origem
para nao apagar).

    python scripts/adicionar_bloco.py "LOOP DE VANEIRA|Loop de Vaneira@82 BPM" --bpm 82
    python scripts/adicionar_bloco.py "LOOP DE VANEIRA|Loop de Vaneira@82 BPM" --bpm 82 --aplicar
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import grupos_tracks  # noqa: E402
from adicionar_blocos_zip import (  # noqa: E402
    BlockPlan,
    StemPlan,
    alpha_key,
    format_time,
    make_audio_track,
    mp3_duration,
    normalize_audio_filename,
    pan_and_volume,
    role_rank,
)
from adicionar_musicas_bloco import (  # noqa: E402
    MARKER_RE,
    PDF,
    VERMELHO,
    exportar_pdf,
    gravar_pptx,
    interpretar,
    pptx_aberto,
    reaper_aberto,
    referencias,
    safe_filename,
)
from backup_rpp import backup_rpp  # noqa: E402
from inserir_texto_monitor import (  # noqa: E402
    PPTX,
    aplicar_na_track,
    chunk_take_fx,
    codigo_do_cartaz,
    formato_numero,
    item_video,
    linhas_do_slide,
    proximo_iid,
    slide_do_bloco,
)
from renomear_tracks_rpp import classify_role, stem_display  # noqa: E402
from unificar_reaper import (  # noqa: E402
    GAP_SECONDS,
    extract_track_blocks,
    format_rpp_name,
    get_track_name,
    make_folder_track,
    make_monitor_track,
    region_color,
    replace_all_track_blocks,
)

WORKSPACE = _SCRIPTS.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"
ABERTO = WORKSPACE / "ABERTO"
REGION_MARKERS = WORKSPACE / "data" / "region_markers.json"
EXPORT_REGIONS = WORKSPACE / "EXPORT_REGIONS"
EXPORT_VIDEOS = WORKSPACE / "EXPORT_VIDEOS"
ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
PLAYLIST_ID_BASE = 1073741824
PLAYLIST_LINE = re.compile(r"^(\s+)(10737\d+) 1$", re.MULTILINE)
CODE_RE = re.compile(r"^          <CODE\n(?:            \|.*\n)*          >\n", re.MULTILINE)
SLIDE_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
SLIDE_CT = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
GRUPO_NOME = {g["id"]: g["master"] for g in grupos_tracks.GRUPOS}


@dataclass
class Entrada:
    origem: Path
    plano: StemPlan


def ler_entradas(pasta: Path, bpm: int | None) -> list[Entrada]:
    arquivos = sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() in (".mp3", ".wav"))
    if not arquivos:
        raise SystemExit(f"Nenhum audio em {pasta}.")
    entradas: list[Entrada] = []
    vistos: dict[str, Path] = {}
    for arquivo in arquivos:
        if arquivo.suffix.lower() != ".mp3":
            raise SystemExit(f"Converta para mp3 antes: {arquivo.name}")
        nome = normalize_audio_filename(arquivo.name)
        papel = classify_role(nome, "")
        if bpm and papel == "CLICK" and not re.search(r"\d", stem_display(nome)):
            nome = f"CLICK {bpm}.mp3"
        if nome in vistos:
            raise SystemExit(f"{vistos[nome].name} e {arquivo.name} viram o mesmo nome: {nome}")
        vistos[nome] = arquivo
        duracao = mp3_duration(arquivo.read_bytes())
        entradas.append(Entrada(arquivo, StemPlan(arquivo.name, nome, papel, duracao)))
    clicks = [e for e in entradas if e.plano.role == "CLICK"]
    regencias = [e for e in entradas if e.plano.role == "REGÊNCIA"]
    if len(clicks) != 1 or len(regencias) > 1:
        raise SystemExit(f"Esperado 1 CLICK e no maximo 1 REGÊNCIA; achados {len(clicks)} e {len(regencias)}.")
    entradas.sort(key=lambda e: (role_rank(e.plano.role), alpha_key(stem_display(e.plano.filename))))
    return entradas


def regions(texto: str) -> dict[int, dict[str, str]]:
    achadas: dict[int, dict[str, str]] = {}
    for m in MARKER_RE.finditer(texto):
        chave = "nome" if m.group(3) else "fim"
        achadas.setdefault(int(m.group(1)), {})[chave] = m.group(3) if m.group(3) else m.group(2)
        if m.group(3):
            achadas[int(m.group(1))]["inicio"] = m.group(2)
    return achadas


def ajustar_monitor_anterior(texto: str, ordem: int, linhas, inicio: str, fim: str) -> str:
    """O cartaz do bloco que era o ultimo passa a terminar 1 ms antes do fim da region."""
    nome = f'\n    NAME "{ordem:02d} - MONITOR"\n'
    ini_track = texto.find(nome)
    if ini_track < 0:
        raise SystemExit(f"Track {ordem:02d} - MONITOR nao encontrada.")
    fim_track = texto.find("\n  <TRACK", ini_track)
    fim_track = len(texto) if fim_track < 0 else fim_track
    trecho = texto[ini_track:fim_track]
    codigos = list(CODE_RE.finditer(trecho))
    if len(codigos) != 1:
        raise SystemExit(f"Cartaz do bloco {ordem}: esperava 1 CODE, achei {len(codigos)}.")
    codigo = codigos[0].group(0)
    if f"|ini={inicio};" not in codigo:
        raise SystemExit(f"Cartaz do bloco {ordem} nao comeca em {inicio}.")
    corpo = ["          <CODE"]
    corpo += [f"            |{l}" for l in codigo_do_cartaz(linhas, inicio, fim).splitlines()]
    corpo.append("          >")
    trecho = trecho.replace(codigo, "\n".join(corpo) + "\n")
    duracao = formato_numero(float(fim) - float(inicio))
    trecho, n = re.subn(r"^(      LENGTH )\S+$", rf"\g<1>{duracao}", trecho, count=1, flags=re.MULTILINE)
    if n != 1:
        raise SystemExit(f"Item do cartaz do bloco {ordem} sem LENGTH.")
    return texto[:ini_track] + trecho + texto[fim_track:]


def novo_slide_xml(modelo: str, ordem: int, musicas) -> str:
    """Mesmo layout do slide anterior: titulo vermelho e uma linha por musica."""
    titulo_re = re.compile(r"(<a:t>)VS BLOCO \d+(</a:t>)")
    if len(titulo_re.findall(modelo)) != 1:
        raise SystemExit("Slide modelo sem titulo VS BLOCO.")
    xml = titulo_re.sub(rf"\g<1>VS BLOCO {ordem}\g<2>", modelo)
    ini = xml.find("<p:txBody>")
    fim = xml.find("</p:txBody>", ini)
    paragrafos = list(re.finditer(r"<a:p>.*?</a:p>", xml[ini:fim], re.DOTALL))
    if len(paragrafos) != 2:
        raise SystemExit("Slide modelo fora do padrao (titulo + musicas).")
    p = paragrafos[1]
    abertura = re.match(r"<a:p>(<a:pPr.*?</a:pPr>)?", p.group(0), re.DOTALL).group(0)
    sz = (re.findall(r'sz="(\d+)"', p.group(0)) or ["7200"])[-1]
    simples = f'<a:rPr lang="pt-BR" sz="{sz}" dirty="0"/>'
    vermelho = f'<a:rPr lang="pt-BR" sz="{sz}" dirty="0">{VERMELHO}</a:rPr>'
    partes = []
    for i, musica in enumerate(musicas):
        if i:
            partes.append(f"<a:br>{simples}</a:br>")
        partes.append(f"<a:r>{simples}<a:t>{escape(musica.slide + ' ')}</a:t></a:r>")
        partes.append(f"<a:r>{vermelho}<a:t>{escape(musica.tom)}</a:t></a:r>")
    novo_p = abertura + "".join(partes) + "</a:p>"
    a, b = ini + p.start(), ini + p.end()
    return xml[:a] + novo_p + xml[b:]


def membros_do_pptx(ordem: int, musicas) -> dict[str, str]:
    numero = slide_do_bloco(ordem)
    with zipfile.ZipFile(PPTX) as z:
        nomes = set(z.namelist())
        existentes = sorted(int(n) for n in re.findall(r"ppt/slides/slide(\d+)\.xml", "\n".join(nomes)))
        if existentes != list(range(1, numero)):
            raise SystemExit(f"O .pptx tem {len(existentes)} slides; o bloco {ordem} precisa ser o slide {numero}.")
        pres = z.read("ppt/presentation.xml").decode("utf-8")
        rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
        tipos = z.read("[Content_Types].xml").decode("utf-8")
        modelo = z.read(f"ppt/slides/slide{numero - 1}.xml").decode("utf-8")
        modelo_rels = z.read(f"ppt/slides/_rels/slide{numero - 1}.xml.rels").decode("utf-8")
    if "notesSlide" in modelo_rels:
        raise SystemExit("Slide modelo com anotacoes; nao sei copiar.")
    rid = max(int(n) for n in re.findall(r'Id="rId(\d+)"', rels)) + 1
    sld_id = max(int(n) for n in re.findall(r'<p:sldId id="(\d+)"', pres)) + 1
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="rId{rid}" Type="{SLIDE_TYPE}" Target="slides/slide{numero}.xml"/></Relationships>',
    )
    pres = pres.replace("</p:sldIdLst>", f'<p:sldId id="{sld_id}" r:id="rId{rid}"/></p:sldIdLst>')
    tipos = tipos.replace(
        "</Types>", f'<Override PartName="/ppt/slides/slide{numero}.xml" ContentType="{SLIDE_CT}"/></Types>'
    )
    return {
        "ppt/presentation.xml": pres,
        "ppt/_rels/presentation.xml.rels": rels,
        "[Content_Types].xml": tipos,
        f"ppt/slides/slide{numero}.xml": novo_slide_xml(modelo, ordem, musicas),
        f"ppt/slides/_rels/slide{numero}.xml.rels": modelo_rels,
    }


def gravar_pptx_com_novos(membros: dict[str, str], destino: Path) -> None:
    existentes = set(zipfile.ZipFile(PPTX).namelist())
    trocas = {k: v for k, v in membros.items() if k in existentes}
    gravar_pptx(PPTX, trocas, destino)
    with zipfile.ZipFile(destino, "a", zipfile.ZIP_DEFLATED) as saida:
        for nome, conteudo in membros.items():
            if nome not in existentes:
                saida.writestr(nome, conteudo.encode("utf-8"))


def montar_rpp(texto: str, bloco: BlockPlan, ultimo: int, fim_ultimo: str, linhas_anterior, linhas_novo) -> str:
    ordem = bloco.order
    # Cartaz do bloco novo: do fim da region anterior ao fim desta.
    inicio_cartaz = fim_ultimo
    fim_cartaz = format_time(bloco.end)
    monitor = aplicar_na_track(
        make_monitor_track(ordem),
        item_video(
            inicio_cartaz,
            formato_numero(bloco.end - float(inicio_cartaz)),
            "MONITOR\\base.png",
            f"VS BLOCO {ordem}",
            proximo_iid(texto),
            chunk_take_fx(codigo_do_cartaz(linhas_novo, inicio_cartaz, fim_cartaz)),
        ),
    )
    regs = regions(texto)
    inicio_anterior = regs[ultimo - 1]["fim"] if ultimo > 1 else regs[ultimo]["inicio"]
    texto = ajustar_monitor_anterior(
        texto, ultimo, linhas_anterior, inicio_anterior, formato_numero(float(fim_ultimo) - 0.001)
    )

    # A pasta fica recolhida, como as outras.
    pasta = make_folder_track(ordem, bloco.song_name, bloco.peakcol).replace(
        "    BUSCOMP 0 0 0 0 0\n", "    BUSCOMP 2 0 0 0 0\n", 1
    )
    iid = proximo_iid(texto) + 1
    tracks = [pasta, monitor]
    for i, stem in enumerate(bloco.stems):
        track = make_audio_track(bloco, stem, iid=iid + i, last_in_folder=i == len(bloco.stems) - 1)
        # Mesma altura das tracks de audio dos outros blocos.
        tracks.append(track.replace("    TRACKHEIGHT 0 1 0 0 0 0 0\n", "    TRACKHEIGHT 0 0 0 0 0 0 0\n", 1))
    existentes = extract_track_blocks(texto)
    if replace_all_track_blocks(texto, existentes) != texto:
        raise SystemExit("Roundtrip das tracks existentes nao e identico. Abortado.")
    texto = replace_all_track_blocks(texto, existentes + tracks)

    fim_re = re.compile(rf'^  MARKER {ultimo} {re.escape(fim_ultimo)} "" .*\n', re.MULTILINE)
    achados = list(fim_re.finditer(texto))
    if len(achados) != 1:
        raise SystemExit(f"Fim da region {ultimo}: {len(achados)} ocorrencias.")
    marcadores = (
        f"  MARKER {ordem} {format_time(bloco.start)} {format_rpp_name(bloco.song_name)} "
        f"1 {bloco.peakcol} 1 R {ZERO_GUID} 0 1\n"
        f'  MARKER {ordem} {format_time(bloco.end)} "" 1\n'
    )
    pos = achados[0].end()
    texto = texto[:pos] + marcadores + texto[pos:]

    playlist = list(PLAYLIST_LINE.finditer(texto))
    ids = [int(m.group(2)) for m in playlist]
    if ids != [PLAYLIST_ID_BASE + n for n in range(1, ultimo + 1)]:
        raise SystemExit(f"Playlist S&M nao e 1..{ultimo} em ordem.")
    ultimo_item = playlist[-1]
    texto = (
        texto[: ultimo_item.end()]
        + f"\n{ultimo_item.group(1)}{PLAYLIST_ID_BASE + ordem} 1"
        + texto[ultimo_item.end():]
    )
    return texto


def validar(texto: str, bloco: BlockPlan) -> None:
    regs = regions(texto)
    if max(regs) != bloco.order or regs[bloco.order]["nome"] != bloco.song_name:
        raise SystemExit("Region nova nao ficou no fim.")
    prefixo = f"{bloco.order:02d} - "
    tracks = [t for t in extract_track_blocks(texto) if get_track_name(t).startswith(prefixo)]
    if len(tracks) != 2 + len(bloco.stems):
        raise SystemExit(f"Bloco {bloco.order} com {len(tracks)} tracks.")
    for track, stem in zip(tracks[2:], bloco.stems):
        pan, volume = pan_and_volume(stem.role)
        cabeca = track.split("\n    <ITEM\n", 1)[0]
        vol, pan_lido = map(float, re.search(r"^    VOLPAN (\S+) (\S+) ", cabeca, re.MULTILINE).groups())
        if abs(vol - volume) > 1e-6 or pan_lido != pan:
            raise SystemExit(f"{get_track_name(track)}: VOLPAN {vol} {pan_lido}, esperado {volume} {pan}.")
        arquivo = re.search(r'FILE "(.*?)"', track).group(1)
        if not (WORKSPACE / arquivo.replace("\\", "/")).is_file():
            raise SystemExit(f"Audio ausente: {arquivo}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Acrescenta um bloco novo no fim do projeto.")
    parser.add_argument("musicas", nargs="+", help='"NOME", "NOME@Tom" ou "NOME|Texto do Slide@Tom"')
    parser.add_argument("--origem", type=Path, default=WORKSPACE, help="pasta com os mp3 (padrao: raiz)")
    parser.add_argument("--bpm", type=int, help="poe o BPM no nome do click: CLICK <bpm>.mp3")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isso so mostra o plano")
    parser.add_argument("--sem-video", action="store_true", help="nao exporta o mp4")
    parser.add_argument("--manter-origem", action="store_true", help="nao apaga os mp3 de entrada")
    args = parser.parse_args()

    if pptx_aberto():
        parser.exit(1, f"Feche {PPTX.name} no PowerPoint antes; aberto ele fica travado.\n")
    texto = RPP.read_text(encoding="utf-8")
    regs = regions(texto)
    ultimo = max(regs)
    ordem = ultimo + 1
    fim_ultimo = regs[ultimo]["fim"]

    musicas = [interpretar(p, referencias(texto)) for p in args.musicas]
    nome = " - ".join(m.projeto for m in musicas)
    pasta = ABERTO / f"{ordem} - {nome}"
    if pasta.exists():
        raise SystemExit(f"Pasta ja existe: {pasta}")

    entradas = ler_entradas(args.origem.resolve(), args.bpm)
    stems = [e.plano for e in entradas]
    inicio = float(fim_ultimo) + GAP_SECONDS
    bloco = BlockPlan(
        order=ordem,
        song_name=nome,
        zip_path=args.origem,
        folder_name=f"{ordem} - {nome}",
        stems=stems,
        start=inicio,
        end=inicio + max(s.duration for s in stems),
        peakcol=region_color(ordem, ordem),
    )

    membros = membros_do_pptx(ordem, musicas)
    pptx_tmp = PPTX.with_name(PPTX.stem + ".novo.pptx")
    gravar_pptx_com_novos(membros, pptx_tmp)
    try:
        linhas_novo = linhas_do_slide(pptx_tmp, slide_do_bloco(ordem))
        linhas_anterior = linhas_do_slide(PPTX, slide_do_bloco(ultimo))
        if linhas_novo[0] != (f"VS BLOCO {ordem}", None) or [t for t, tom in linhas_novo if tom] != [
            m.slide for m in musicas
        ]:
            raise SystemExit(f"Slide novo nao leu como esperado: {linhas_novo}")
        novo_texto = montar_rpp(texto, bloco, ultimo, fim_ultimo, linhas_anterior, linhas_novo)
    except BaseException:
        pptx_tmp.unlink(missing_ok=True)
        raise

    print(f"Bloco {ordem}: {nome}")
    print(f"  region: {format_time(bloco.start)} .. {format_time(bloco.end)} ({bloco.end - bloco.start:.1f} s)")
    print(f"  pasta: ABERTO/{bloco.folder_name}")
    for e in entradas:
        s = e.plano
        pan, volume = pan_and_volume(s.role)
        papel = "click" if s.role == "CLICK" else "regencia" if s.role == "REGÊNCIA" else None
        grupo, _, motivo = grupos_tracks.classificar_stem(stem_display(s.filename), papel)
        print(
            f"    {'L' if pan < 0 else 'R'} {'-5 dB' if s.role == 'CLICK' else ' 0 dB'}  "
            f"{e.origem.name} -> {s.filename}  [{GRUPO_NOME.get(grupo, 'fora: ' + motivo)}]"
        )
    print(f"  slide {slide_do_bloco(ordem)}: {linhas_novo}")
    print(f"  cartaz do bloco {ultimo} passa a terminar 1 ms antes do fim da region")
    print(f"  mp3 mono e {'sem mp4' if args.sem_video else 'mp4'} do bloco {ordem}; pdf refeito")

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

    pasta.mkdir()
    for e in entradas:
        shutil.copy2(e.origem, pasta / e.plano.filename)
    RPP.write_text(novo_texto, encoding="utf-8")
    validar(RPP.read_text(encoding="utf-8"), bloco)

    marcadores = json.loads(REGION_MARKERS.read_text(encoding="utf-8"))
    marcadores["starts"].append(
        {
            "index": ordem,
            "start": format_time(bloco.start),
            "name": nome,
            "tail": f"1 {bloco.peakcol} 1 R {ZERO_GUID} 0 1",
        }
    )
    marcadores["ends"].append({"index": ordem, "end": format_time(bloco.end), "tail": "1"})
    REGION_MARKERS.write_text(json.dumps(marcadores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Faders de grupo: refaz receives, GROUP_FLAGS e MAINSEND de todo o projeto.
    grupos_tracks.aplicar(RPP, grupos_tracks.carregar_correcoes())
    validar(RPP.read_text(encoding="utf-8"), bloco)

    pptx_tmp.replace(PPTX)
    exportar_pdf(PPTX, PDF)
    print(f"PDF: {PDF}")
    faixa = ["--from-order", str(ordem), "--to-order", str(ordem)]
    subprocess.run([sys.executable, str(_SCRIPTS / "exportar_regions_mp3.py"), "--mono", *faixa], check=True)
    if not args.sem_video:
        subprocess.run([sys.executable, str(_SCRIPTS / "exportar_blocos_video.py"), *faixa], check=True)

    if not args.manter_origem:
        for e in entradas:
            e.origem.unlink()
        print(f"Apagados da origem: {', '.join(e.origem.name for e in entradas)}")
    print(f"Pronto: EXPORT_REGIONS e EXPORT_VIDEOS/{ordem:02d} - {safe_filename(nome)}")


if __name__ == "__main__":
    main()
