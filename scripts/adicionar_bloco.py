#!/usr/bin/env python3
"""Acrescenta um bloco novo ao projeto, com todas as convencoes.

Loops e clicks (nome comecando com LOOP ou CLICK) ficam sempre no fim. Um bloco
de musica entra logo depois do ultimo bloco de musica e empurra os loops e
clicks do fim: numero +1 e a duracao do bloco novo mais 2 s depois na timeline,
com pasta, tracks e cartaz. Loop ou click novo entra no fim. Os blocos de
musica nunca mudam de numero. Loop e click nao tem slide no repertorio, pdf, mp3
nem mp4: so o cartaz no .rpp.

Os audios (mp3) vem de uma pasta de entrada, por padrao a raiz do projeto.
O bloco entra 2 s depois do anterior:

- pasta ABERTO\\N - <nome> com os audios (nomes em maiusculas, sem a palavra
  BLOCO; com --bpm o CLICK.mp3 vira CLICK <bpm>.mp3);
- region e playlist S&M no .rpp; data/region_markers.json;
- track-pasta (cor da region), N - MONITOR com o cartaz no Video processor e
  uma track por audio: click e regencia primeiro, os outros em ordem alfabetica;
- click e regencia no L, o resto no R; click a -5 dB; cores e nomes como nos
  outros blocos;
- faders de grupo (CLICK, REGÊNCIA, METAIS, TECLAS, CORDAS, PERCUSSÃO) refeitos
  por scripts/grupos_tracks.py, com grupos_classificacao.json e grupos.html;
- slide "VS BLOCO N" em vermelho e o BPM do click em branco, menor, na mesma linha, no .pptx do repertorio,
  PDF exportado de novo;
- EXPORT_REGIONS/NN - <nome>.mp3 (mono) e EXPORT_VIDEOS/NN - <nome>.mp4.
  Com --sem-exports nao refaz o pdf nem exporta mp3 e mp4 (o slide entra no .pptx).

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
    BRANCO,
    VERMELHO,
    backup_do_repertorio,
    exportar_pdf,
    gravar_pptx,
    interpretar,
    pptx_aberto,
    reaper_aberto,
    referencias,
    safe_filename,
)
from backup_rpp import backup_rpp  # noqa: E402
from montar_repertorio_completo import atualizar_completo  # noqa: E402
from inserir_texto_monitor import (  # noqa: E402
    PPTX,
    aplicar_na_track,
    chunk_take_fx,
    codigo_do_cartaz,
    formato_numero,
    item_video,
    linhas_do_cartaz,
    linhas_do_slide,
    proximo_iid,
    sem_repertorio,
    slide_do_bloco,
)
from renomear_tracks_rpp import classify_role, extract_bpm, stem_display  # noqa: E402
from unificar_reaper import (  # noqa: E402
    GAP_SECONDS,
    extract_track_blocks,
    get_track_name,
    make_folder_track,
    make_monitor_track,
    replace_all_track_blocks,
)

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP
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


TRACK_DO_BLOCO = re.compile(r'^    NAME "?(\d{2}) - ', re.MULTILINE)
MARKER_LINHA = re.compile(r"^  MARKER (\d+) (\S+) (.*)$", re.MULTILINE)
# Inicio de region (nome com ou sem aspas): 1 tudo antes da cor, 2 numero, 3 nome, 4 cor, 5 o resto.
MARKER_INICIO = re.compile(r'^(  MARKER (\d+) \S+ "?((?<=")[^"]*(?=")|(?<!")[^\s"]+(?=\s))"? \d+ )(\d+)( .*)$', re.MULTILINE)


# Blocos de musica: cores variadas, sem vermelho (o vermelho e do click e da regencia).
# O numero do bloco escolhe a cor, entao dois blocos seguidos nunca ficam iguais.
PALETA_MUSICA = [
    "#2fa8d8", "#e0a030", "#3cb371", "#c86fd8", "#8c6b4a", "#5b6ee8",
    "#a6c43a", "#48c9b0", "#7a8c2e", "#9b59b6", "#4a7fa8", "#d4a373",
]
# Loops e clicks: uma cor por tipo, igual em todos do tipo.
CORES_DE_LOOP = {"LOOP DE VANEIRA": "#00838f", "LOOP DE MARCHA": "#ff8c00", "CLICK": "#708090"}


def reaper_cor(hexa: str) -> int:
    r, g, b = (int(hexa[i : i + 2], 16) for i in (1, 3, 5))
    return (r + 256 * g + 65536 * b) | 0x1000000


def tipo_de_loop(nome: str) -> str:
    """LOOP DE VANEIRA 82 BPM -> LOOP DE VANEIRA; todo click e CLICK."""
    return "CLICK" if nome.startswith("CLICK") else re.sub(r"\s*\d+\s*BPM$", "", nome)


def cor_do_bloco(ordem: int, nome: str, regs: dict[int, dict[str, str]], cores: dict[int, int]) -> int:
    """Musica pela paleta; loop ou click com a cor do tipo (a dos outros do tipo, se ja houver)."""
    if not sem_repertorio(nome):
        return reaper_cor(PALETA_MUSICA[ordem % len(PALETA_MUSICA)])
    tipo = tipo_de_loop(nome)
    if tipo in CORES_DE_LOOP:
        return reaper_cor(CORES_DE_LOOP[tipo])
    for n, reg in sorted(regs.items()):
        if sem_repertorio(reg.get("nome", "")) and tipo_de_loop(reg["nome"]) == tipo and n in cores:
            return cores[n]
    return reaper_cor(PALETA_MUSICA[ordem % len(PALETA_MUSICA)])


def cores_das_regions(texto: str) -> dict[int, int]:
    return {int(m.group(2)): int(m.group(4)) for m in MARKER_INICIO.finditer(texto) if m.group(3)}


def bpm_do_nome(nome: str) -> int:
    m = re.search(r"(\d+)\s*BPM$", nome)
    return int(m.group(1)) if m else 0


def posicao_do_bloco(regs: dict[int, dict[str, str]], nome: str) -> int:
    """Musica entra antes dos loops e clicks do fim. Loop ou click entra junto dos do
    seu tipo (vaneira, marcha, click), na ordem do BPM; tipo novo, no fim."""
    ordem = max(regs) + 1
    while ordem > 1 and sem_repertorio(regs[ordem - 1]["nome"]):
        ordem -= 1
    if not sem_repertorio(nome):
        return ordem
    tipo, bpm = tipo_de_loop(nome), bpm_do_nome(nome)
    do_tipo = [n for n in range(ordem, max(regs) + 1) if tipo_de_loop(regs[n]["nome"]) == tipo]
    if not do_tipo:
        return max(regs) + 1
    for n in do_tipo:
        if bpm_do_nome(regs[n]["nome"]) > bpm:
            return n
    return do_tipo[-1] + 1


def abrir_espaco(texto: str, ordem: int, delta: float) -> str:
    """Blocos a partir de ordem: numero +1 e delta segundos depois (region, tracks, cartaz)."""

    def mais(valor: str) -> str:
        return format_time(float(valor) + delta)

    def marker(m: re.Match) -> str:
        n = int(m.group(1))
        return m.group(0) if n < ordem else f"  MARKER {n + 1} {mais(m.group(2))} {m.group(3)}"

    texto = MARKER_LINHA.sub(marker, texto)
    tracks = []
    for track in extract_track_blocks(texto):
        m = TRACK_DO_BLOCO.search(track)
        n = int(m.group(1)) if m else 0
        if n >= ordem:
            if re.search(r"^\s+<\w*ENV", track, re.MULTILINE):
                raise SystemExit(f"{get_track_name(track)} tem envelope; nao sei mover.")
            track = TRACK_DO_BLOCO.sub(lambda x: x.group(0).replace(f"{n:02d} - ", f"{n + 1:02d} - "), track, count=1)
            track = track.replace(f'FILE "ABERTO\\{n} - ', f'FILE "ABERTO\\{n + 1} - ')
            track = track.replace(f'NAME "VS BLOCO {n}"', f'NAME "VS BLOCO {n + 1}"')
            track = track.replace(f'|#T="VS BLOCO {n}";', f'|#T="VS BLOCO {n + 1}";')
            track = re.sub(
                r"^(      POSITION )(\S+)$", lambda x: x.group(1) + mais(x.group(2)), track, flags=re.MULTILINE
            )
            track = re.sub(
                r"^(\s+\|(?:ini|fim)=)([\d.]+);$",
                lambda x: x.group(1) + mais(x.group(2)) + ";",
                track,
                flags=re.MULTILINE,
            )
        tracks.append(track)
    return replace_all_track_blocks(texto, tracks)


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


SZ_BPM = "4800"
COR_BPM = "FFFFFF"
_RUN_BPM = re.compile(
    r"<a:r>(?:(?!</a:r>).)*?<a:t>([^<]*BPM)</a:t></a:r>",
    re.DOTALL,
)
_TEXTO_BPM = re.compile(r"^\s*\d.*BPM\s*$")


def _run_bpm(titulo: str, bpm: str) -> str:
    cs = re.search(r"<a:cs [^/]*/>", titulo)
    fonte = cs.group(0) if cs else ""
    sz_titulo = re.search(r'sz="(\d+)"', titulo)
    sz_t = int(sz_titulo.group(1)) if sz_titulo else 8800
    # Sobe o BPM para o meio da altura do nome, em vez de sentar na base.
    baseline = round((sz_t - int(SZ_BPM)) / 2 / int(SZ_BPM) * 100000)
    return (
        f'<a:r><a:rPr lang="pt-BR" sz="{SZ_BPM}" baseline="{baseline}" dirty="0">'
        f'<a:solidFill><a:srgbClr val="{COR_BPM}"/></a:solidFill>'
        f"{fonte}</a:rPr><a:t>{escape('  ' + bpm.strip())}</a:t></a:r>"
    )


def definir_bpm_no_titulo(xml: str, bpm: str | None) -> str:
    """BPM em branco, menor, na mesma linha de VS BLOCO N. Sem bpm, tira."""
    ini = xml.find("<p:txBody>")
    fim = xml.find("</p:txBody>", ini)
    if ini < 0 or fim < 0:
        raise SystemExit("Slide sem texto.")
    paragrafos = list(re.finditer(r"<a:p>.*?</a:p>", xml[ini:fim], re.DOTALL))
    if not paragrafos:
        raise SystemExit("Slide sem titulo.")
    titulo = paragrafos[0]
    trecho = titulo.group(0)
    if "VS BLOCO" not in trecho:
        raise SystemExit("Primeiro paragrafo nao e o titulo do bloco.")
    achado = _RUN_BPM.search(trecho)
    if achado and "VS BLOCO" not in achado.group(1):
        trecho = trecho[: achado.start()] + trecho[achado.end() :]
    if bpm:
        run = _run_bpm(trecho, bpm)
        fim_runs = trecho.find("<a:endParaRPr")
        if fim_runs >= 0:
            trecho = trecho[:fim_runs] + run + trecho[fim_runs:]
        else:
            fecha = trecho.rfind("</a:p>")
            trecho = trecho[:fecha] + run + trecho[fecha:]
    xml = xml[: ini + titulo.start()] + trecho + xml[ini + titulo.end() :]
    fim = xml.find("</p:txBody>", ini)
    paragrafos = list(re.finditer(r"<a:p>.*?</a:p>", xml[ini:fim], re.DOTALL))
    seguinte = paragrafos[1] if len(paragrafos) > 1 else None
    texto_seguinte = "".join(re.findall(r"<a:t>(.*?)</a:t>", seguinte.group(0))) if seguinte else ""
    if seguinte and _TEXTO_BPM.match(texto_seguinte):
        xml = xml[: ini + seguinte.start()] + xml[ini + seguinte.end() :]
    return xml


def novo_slide_xml(modelo: str, ordem: int, musicas, bpm: str | None) -> str:
    """Mesmo layout do slide anterior: titulo vermelho, BPM e uma linha por musica."""
    titulo_re = re.compile(r"(<a:t>)VS BLOCO \d+(</a:t>)")
    if len(titulo_re.findall(modelo)) != 1:
        raise SystemExit("Slide modelo sem titulo VS BLOCO.")
    xml = titulo_re.sub(rf"\g<1>VS BLOCO {ordem}\g<2>", modelo)
    ini = xml.find("<p:txBody>")
    fim = xml.find("</p:txBody>", ini)
    paragrafos = list(re.finditer(r"<a:p>.*?</a:p>", xml[ini:fim], re.DOTALL))
    if len(paragrafos) < 2:
        raise SystemExit("Slide modelo fora do padrao (titulo + musicas).")
    p = paragrafos[-1]
    abertura = re.match(r"<a:p>(<a:pPr.*?</a:pPr>)?", p.group(0), re.DOTALL).group(0)
    sz = (re.findall(r'sz="(\d+)"', p.group(0)) or ["7200"])[-1]
    simples = f'<a:rPr lang="pt-BR" sz="{sz}" dirty="0">{BRANCO}</a:rPr>'
    vermelho = f'<a:rPr lang="pt-BR" sz="{sz}" dirty="0">{VERMELHO}</a:rPr>'
    partes = []
    for i, musica in enumerate(musicas):
        if i:
            partes.append(f"<a:br>{simples}</a:br>")
        partes.append(f"<a:r>{simples}<a:t>{escape(musica.slide + ' ')}</a:t></a:r>")
        partes.append(f"<a:r>{vermelho}<a:t>{escape(musica.tom)}</a:t></a:r>")
    novo_p = abertura + "".join(partes) + "</a:p>"
    a, b = ini + p.start(), ini + p.end()
    return definir_bpm_no_titulo(xml[:a] + novo_p + xml[b:], bpm)


def membros_do_pptx(ordem: int, musicas, bpm: str | None) -> dict[str, str]:
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
        f"ppt/slides/slide{numero}.xml": novo_slide_xml(modelo, ordem, musicas, bpm),
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


def montar_rpp(texto: str, bloco: BlockPlan, linhas_anterior, linhas_novo) -> str:
    """Poe o bloco depois do anterior; com abrir_espaco antes, os seguintes ja estao 1 numero adiante."""
    ordem = bloco.order
    regs = regions(texto)
    ultimo = ordem - 1
    fim_ultimo = regs[ultimo]["fim"]
    no_fim = ordem > max(regs)
    # Cartaz do bloco novo: do fim da region anterior ao fim desta (1 ms antes, se nao for o ultimo).
    inicio_cartaz = fim_ultimo
    fim_cartaz = format_time(bloco.end) if no_fim else formato_numero(bloco.end - 0.001)
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
    if no_fim:
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
    if no_fim:
        antes = len(existentes)
    else:
        seguintes = [i for i, t in enumerate(existentes) if get_track_name(t).startswith(f"{ordem + 1:02d} - ")]
        if not seguintes:
            raise SystemExit(f"Tracks do bloco {ordem + 1} nao encontradas.")
        antes = seguintes[0]
    texto = replace_all_track_blocks(texto, existentes[:antes] + tracks + existentes[antes:])

    fim_re = re.compile(rf'^  MARKER {ultimo} {re.escape(fim_ultimo)} "" .*\n', re.MULTILINE)
    achados = list(fim_re.finditer(texto))
    if len(achados) != 1:
        raise SystemExit(f"Fim da region {ultimo}: {len(achados)} ocorrencias.")
    marcadores = (
        # Sempre entre aspas: os scripts so leem a region com o nome entre aspas.
        f"  MARKER {ordem} {format_time(bloco.start)} \"{bloco.song_name}\" "
        f"1 {bloco.peakcol} 1 R {ZERO_GUID} 0 1\n"
        f'  MARKER {ordem} {format_time(bloco.end)} "" 1\n'
    )
    pos = achados[0].end()
    texto = texto[:pos] + marcadores + texto[pos:]

    playlist = list(PLAYLIST_LINE.finditer(texto))
    ids = [int(m.group(2)) for m in playlist]
    total = len(regs)
    if ids != [PLAYLIST_ID_BASE + n for n in range(1, total + 1)]:
        raise SystemExit(f"Playlist S&M nao e 1..{total} em ordem.")
    # A playlist toca as regions pelo numero, em ordem: basta mais um no fim.
    ultimo_item = playlist[-1]
    texto = (
        texto[: ultimo_item.end()]
        + f"\n{ultimo_item.group(1)}{PLAYLIST_ID_BASE + total + 1} 1"
        + texto[ultimo_item.end():]
    )
    return texto


def validar(texto: str, bloco: BlockPlan) -> None:
    regs = regions(texto)
    if sorted(regs) != list(range(1, len(regs) + 1)) or regs.get(bloco.order, {}).get("nome") != bloco.song_name:
        raise SystemExit(f"Region nova nao ficou no bloco {bloco.order}.")
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
    parser = argparse.ArgumentParser(description="Acrescenta um bloco novo (musica antes dos loops e clicks do fim).")
    parser.add_argument("musicas", nargs="+", help='"NOME", "NOME@Tom" ou "NOME|Texto do Slide@Tom"')
    parser.add_argument("--origem", type=Path, default=WORKSPACE, help="pasta com os mp3 (padrao: raiz)")
    parser.add_argument("--bpm", type=int, help="poe o BPM no nome do click: CLICK <bpm>.mp3")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isso so mostra o plano")
    parser.add_argument("--sem-video", action="store_true", help="nao exporta o mp4")
    parser.add_argument(
        "--sem-exports", action="store_true", help="nao exporta pdf, mp3 nem mp4 (o slide entra no .pptx)"
    )
    parser.add_argument("--manter-origem", action="store_true", help="nao apaga os mp3 de entrada")
    args = parser.parse_args()

    if pptx_aberto():
        parser.exit(1, f"Feche {PPTX.name} no PowerPoint antes; aberto ele fica travado.\n")
    texto = RPP.read_text(encoding="utf-8")
    regs = regions(texto)
    ultimo = max(regs)

    musicas = [interpretar(p, referencias(texto)) for p in args.musicas]
    nome = " - ".join(m.projeto for m in musicas)
    ordem = posicao_do_bloco(regs, nome)
    fim_anterior = regs[ordem - 1]["fim"]
    # Loops e clicks do fim que descem um numero para o bloco novo entrar antes deles.
    empurrados = [(n, regs[n]["nome"]) for n in range(ordem, ultimo + 1)]
    pasta = ABERTO / f"{ordem} - {nome}"
    if pasta.exists():
        raise SystemExit(f"Pasta ja existe: {pasta}")
    for n, nome_n in empurrados:
        if not (ABERTO / f"{n} - {nome_n}").is_dir():
            raise SystemExit(f"Pasta nao encontrada: ABERTO/{n} - {nome_n}")

    entradas = ler_entradas(args.origem.resolve(), args.bpm)
    stems = [e.plano for e in entradas]
    delta = GAP_SECONDS + max(s.duration for s in stems)
    bloco = BlockPlan(
        order=ordem,
        song_name=nome,
        zip_path=args.origem,
        folder_name=f"{ordem} - {nome}",
        stems=stems,
        start=float(fim_anterior) + GAP_SECONDS,
        end=float(fim_anterior) + delta,
        peakcol=cor_do_bloco(ordem, nome, regs, cores_das_regions(texto)),
    )

    # Loop e click nao tem slide, pdf, mp3 nem mp4: o cartaz vem direto das musicas.
    loop = sem_repertorio(nome)
    pptx_tmp = PPTX.with_name(PPTX.stem + ".novo.pptx")
    try:
        if loop:
            linhas_novo = [(f"VS BLOCO {ordem}", None)] + [(m.slide, m.tom) for m in musicas]
        else:
            bpm_click = next(
                (extract_bpm(e.plano.filename) for e in entradas if e.plano.role == "CLICK"),
                None,
            )
            gravar_pptx_com_novos(
                membros_do_pptx(ordem, musicas, f"{bpm_click} BPM" if bpm_click else None),
                pptx_tmp,
            )
            linhas_novo = linhas_do_slide(pptx_tmp, slide_do_bloco(ordem))
            if linhas_novo[0] != (f"VS BLOCO {ordem}", None) or [t for t, tom in linhas_novo if tom] != [
                m.slide for m in musicas
            ]:
                raise SystemExit(f"Slide novo nao leu como esperado: {linhas_novo}")
        linhas_anterior = linhas_do_cartaz(texto, ordem - 1)
        if empurrados:
            texto = abrir_espaco(texto, ordem, delta)
        novo_texto = montar_rpp(texto, bloco, linhas_anterior, linhas_novo)
    except BaseException:
        pptx_tmp.unlink(missing_ok=True)
        raise
    sem_exports = args.sem_exports or loop

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
    if loop:
        print(f"  cartaz (sem slide): {linhas_novo}")
    else:
        print(f"  slide {slide_do_bloco(ordem)}: {linhas_novo}")
    if empurrados:
        print(f"  loops e clicks do fim descem um numero e {delta:.1f} s:")
        for n, nome_n in empurrados:
            print(f"    {n} -> {n + 1}  {nome_n}")
    else:
        print(f"  cartaz do bloco {ordem - 1} passa a terminar 1 ms antes do fim da region")
    if loop:
        print("  loop/click: sem slide, pdf, mp3 e mp4")
    elif args.sem_exports:
        print("  sem pdf, mp3 e mp4: exporte depois")
    else:
        print(f"  mp3 mono e {'sem mp4' if args.sem_video else 'mp4'} do bloco {ordem}; pdf refeito")

    if not args.aplicar:
        pptx_tmp.unlink(missing_ok=True)
        print("Plano apenas. Rode com --aplicar para gravar.")
        return
    if reaper_aberto():
        pptx_tmp.unlink(missing_ok=True)
        raise SystemExit("Feche o Reaper antes: ele regravaria o .rpp antigo.")

    print(f"Backup .rpp: {backup_rpp(RPP)}")
    if not loop:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        shutil.copy2(PPTX, backup_do_repertorio(PPTX, stamp))
        if PDF.exists():
            shutil.copy2(PDF, backup_do_repertorio(PDF, stamp))
        print(f"Backup .pptx e .pdf: {stamp}")

    # Do ultimo para o primeiro, para um nome nunca cair em cima do outro.
    for n, nome_n in reversed(empurrados):
        (ABERTO / f"{n} - {nome_n}").rename(ABERTO / f"{n + 1} - {nome_n}")
    pasta.mkdir()
    for e in entradas:
        shutil.copy2(e.origem, pasta / e.plano.filename)
    RPP.write_text(novo_texto, encoding="utf-8")
    validar(RPP.read_text(encoding="utf-8"), bloco)

    marcadores = json.loads(REGION_MARKERS.read_text(encoding="utf-8"))
    for item in marcadores["starts"]:
        if item["index"] >= ordem:
            item["index"] += 1
            item["start"] = format_time(float(item["start"]) + delta)
    for item in marcadores["ends"]:
        if item["index"] >= ordem:
            item["index"] += 1
            item["end"] = format_time(float(item["end"]) + delta)
    marcadores["starts"].append(
        {
            "index": ordem,
            "start": format_time(bloco.start),
            "name": nome,
            "tail": f"1 {bloco.peakcol} 1 R {ZERO_GUID} 0 1",
        }
    )
    marcadores["ends"].append({"index": ordem, "end": format_time(bloco.end), "tail": "1"})
    marcadores["starts"].sort(key=lambda item: item["index"])
    marcadores["ends"].sort(key=lambda item: item["index"])
    REGION_MARKERS.write_text(json.dumps(marcadores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Faders de grupo: refaz receives, GROUP_FLAGS e MAINSEND de todo o projeto.
    grupos_tracks.aplicar(RPP, grupos_tracks.carregar_correcoes())
    validar(RPP.read_text(encoding="utf-8"), bloco)

    if not loop:
        pptx_tmp.replace(PPTX)
    if not sem_exports:
        exportar_pdf(PPTX, PDF)
        print(f"PDF: {PDF}")
        atualizar_completo()
        faixa = ["--from-order", str(ordem), "--to-order", str(ordem)]
        subprocess.run([sys.executable, str(_SCRIPTS / "exportar_regions_mp3.py"), "--mono", *faixa], check=True)
        if not args.sem_video:
            subprocess.run([sys.executable, str(_SCRIPTS / "exportar_blocos_video.py"), *faixa], check=True)

    if not args.manter_origem:
        for e in entradas:
            e.origem.unlink()
        print(f"Apagados da origem: {', '.join(e.origem.name for e in entradas)}")
    if loop:
        print(f"Pronto: loop/click {ordem}, sem slide nem exports")
    elif args.sem_exports:
        print(f"Pronto, sem exports: falta o pdf e o mp3/mp4 do bloco {ordem}")
    else:
        print(f"Pronto: EXPORT_REGIONS e EXPORT_VIDEOS/{ordem:02d} - {safe_filename(nome)}")


if __name__ == "__main__":
    main()
