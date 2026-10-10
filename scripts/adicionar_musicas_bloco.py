#!/usr/bin/env python3
"""Acrescenta musicas na descricao de um bloco, depois da que ja esta la,
ou renomeia uma musica em todos os blocos que a tem (--trocar).

So muda o nome do bloco. Nao mexe em tempo, volume, item, cor nem audio.
O nome novo e "<nome atual> - <musica 2> - <musica 3>..." e chega em todo
lugar onde o nome do bloco aparece:

- region (MARKER) e nomes das tracks "N - ... - <nome>" no .rpp;
- caminhos FILE "ABERTO\\N - <nome>\\..." e a propria pasta em ABERTO;
- data/region_markers.json, data/grupos_classificacao.json, relatorio/grupos.html;
- slide do bloco no PowerPoint do repertorio e o cartaz da track N - MONITOR;
- o PDF do repertorio, exportado de novo pelo PowerPoint;
- EXPORT_REGIONS/NN - <nome>.mp3 (renomeado; exportado em mono se faltar) e EXPORT_VIDEOS (refeito,
  porque o cartaz fica gravado no video).

Grafia: uma musica que ja existe nos blocos de referencia (1 a 23) usa o
mesmo nome do projeto e o mesmo texto do slide. Ex.: "essa cama nao vendo"
vira "CAMA NÃO VENDO" no projeto e "Essa Cama Não Vendo" no slide, como no
bloco 11. Musica nova entra como foi passada, em maiusculas; para escolher o
texto do slide use "NOME|Texto do Slide". Tom: "NOME@Mib"; sem tom fica "??".

Sem --aplicar so mostra o plano. Com o Reaper ou o .pptx aberto nao grava.

    python scripts/adicionar_musicas_bloco.py 24 "essa cama nao vendo" "FLASHBACK@Re"
    python scripts/adicionar_musicas_bloco.py 24 "essa cama nao vendo" --aplicar
    python scripts/adicionar_musicas_bloco.py 29 "SENTA NO COLINHO DO PAI" --posicao 2
    python scripts/adicionar_musicas_bloco.py --trocar "CAMA NÃO VENDO=ESSA CAMA EU NÃO VENDO"
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape, unescape

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from montar_repertorio_completo import atualizar_completo  # noqa: E402
from inserir_texto_monitor import (  # noqa: E402
    PPTX,
    codigo_do_cartaz,
    linhas_do_slide,
    slide_do_bloco,
)

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP
ABERTO = WORKSPACE / "ABERTO"
REGION_MARKERS = WORKSPACE / "data" / "region_markers.json"
TEXTOS_COM_NOMES = [
    WORKSPACE / "data" / "grupos_classificacao.json",
    WORKSPACE / "relatorio" / "grupos.html",
]
EXPORT_REGIONS = WORKSPACE / "EXPORT_REGIONS"
EXPORT_VIDEOS = WORKSPACE / "EXPORT_VIDEOS"
PDF = PPTX.with_suffix(".pdf")
ULTIMO_BLOCO_REFERENCIA = 23
TOM_DESCONHECIDO = "??"
MINUSCULAS = {"e", "o", "a", "os", "as", "de", "da", "do", "das", "dos", "na", "no", "nas", "nos"}
MARKER_RE = re.compile(r'^  MARKER (\d+) ([0-9.]+) "(.*)"(.*)$', re.MULTILINE)
STRING_JSON_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
CODE_RE = re.compile(r"^          <CODE\n(?:            \|.*\n)*          >\n", re.MULTILINE)
VERMELHO = '<a:solidFill><a:srgbClr val="FF0000"/></a:solidFill>'


@dataclass
class Musica:
    projeto: str
    slide: str
    tom: str


def chave(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]", "", sem_acento.upper())


def texto_de_slide(nome: str) -> str:
    palavras = nome.lower().split()
    return " ".join(
        p if i and p in MINUSCULAS else p[:1].upper() + p[1:] for i, p in enumerate(palavras)
    )


def referencias(texto_rpp: str) -> dict[str, Musica]:
    """Musicas dos blocos 1-23: nome no projeto e texto no slide, na mesma ordem."""
    achadas: dict[str, Musica] = {}
    for match in MARKER_RE.finditer(texto_rpp):
        ordem, nome = int(match.group(1)), match.group(3)
        if not nome or ordem > ULTIMO_BLOCO_REFERENCIA:
            continue
        no_projeto = nome.split(" - ")
        no_slide = [(t, tom) for t, tom in linhas_do_slide(PPTX, slide_do_bloco(ordem)) if tom]
        if len(no_projeto) != len(no_slide):
            continue
        for projeto, (slide, _) in zip(no_projeto, no_slide):
            musica = Musica(projeto, slide, TOM_DESCONHECIDO)
            achadas.setdefault(chave(projeto), musica)
            achadas.setdefault(chave(slide), musica)
    return achadas


def interpretar(pedido: str, conhecidas: dict[str, Musica]) -> Musica:
    pedido, _, tom = pedido.partition("@")
    nome, _, slide = pedido.partition("|")
    nome, slide, tom = nome.strip(), slide.strip(), tom.strip() or TOM_DESCONHECIDO
    ref = conhecidas.get(chave(nome))
    if ref and not slide:
        return Musica(ref.projeto, ref.slide, tom)
    projeto = re.sub(r"\s+", " ", nome.upper())
    if " - " in projeto or '"' in projeto or "\\" in projeto:
        raise SystemExit(f"Nome invalido: {nome!r}")
    return Musica(projeto, slide or texto_de_slide(nome), tom)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "-", name)
    return re.sub(r"\s+", " ", cleaned).strip(" .")


class Renomeador:
    """Troca o nome do bloco so onde ele aparece com o numero do bloco."""

    def __init__(self, ordem: int, antigo: str, novo: str) -> None:
        self.prefixos = {f"{ordem} - ", f"{ordem:02d} - "}
        self.antigo, self.novo = antigo, novo

    def nome(self, texto: str) -> str:
        for prefixo in self.prefixos:
            if texto == prefixo + self.antigo:
                return prefixo + self.novo
            if texto.startswith(prefixo) and texto.endswith(" - " + self.antigo):
                return texto[: -len(self.antigo)] + self.novo
        return texto

    def caminho(self, texto: str) -> str:
        for prefixo in self.prefixos:
            for sep in ("\\\\", "\\", "/"):
                velho = f"ABERTO{sep}{prefixo}{self.antigo}{sep}"
                texto = texto.replace(velho, f"ABERTO{sep}{prefixo}{self.novo}{sep}")
        return texto

    def string(self, texto: str) -> str:
        return self.caminho(self.nome(texto))


def novo_rpp(texto: str, ordem: int, ren: Renomeador, linhas_cartaz) -> tuple[str, dict[str, int]]:
    contagem = {"region": 0, "tracks": 0, "arquivos": 0, "cartaz": 0}

    def marker(m: re.Match) -> str:
        if int(m.group(1)) != ordem or m.group(3) != ren.antigo:
            return m.group(0)
        contagem["region"] += 1
        return f'  MARKER {m.group(1)} {m.group(2)} "{ren.novo}"{m.group(4)}'

    texto = MARKER_RE.sub(marker, texto)

    def track(m: re.Match) -> str:
        novo = ren.nome(m.group(2))
        if novo == m.group(2):
            return m.group(0)
        contagem["tracks"] += 1
        return f'{m.group(1)}"{novo}"'

    texto = re.sub(r'^(    NAME )"(.*)"$', track, texto, flags=re.MULTILINE)

    def arquivo(m: re.Match) -> str:
        novo = ren.caminho(m.group(2))
        if novo == m.group(2):
            return m.group(0)
        contagem["arquivos"] += 1
        return f'{m.group(1)}"{novo}"'

    texto = re.sub(r'^(\s+FILE )"(.*)"', arquivo, texto, flags=re.MULTILINE)

    # Cartaz da track NN - MONITOR: mesmo intervalo, texto novo.
    inicio_track = texto.find(f'\n    NAME "{ordem:02d} - MONITOR"\n')
    if inicio_track < 0:
        raise SystemExit(f"Track {ordem:02d} - MONITOR nao encontrada.")
    fim_track = texto.find("\n  <TRACK", inicio_track)
    fim_track = len(texto) if fim_track < 0 else fim_track
    trecho = texto[inicio_track:fim_track]
    codigos = list(CODE_RE.finditer(trecho))
    if len(codigos) != 1:
        raise SystemExit(f"Cartaz do bloco {ordem}: esperava 1 CODE, achei {len(codigos)}.")
    codigo = codigos[0].group(0)
    ini = re.search(r"\|ini=([0-9.]+);", codigo).group(1)
    fim = re.search(r"\|fim=([0-9.]+);", codigo).group(1)
    corpo = ["          <CODE"]
    corpo += [f"            |{linha}" for linha in codigo_do_cartaz(linhas_cartaz, ini, fim).splitlines()]
    corpo.append("          >")
    trecho = trecho.replace(codigo, "\n".join(corpo) + "\n")
    contagem["cartaz"] = 1
    texto = texto[:inicio_track] + trecho + texto[fim_track:]

    sobra = [
        linha.strip()
        for linha in texto.splitlines()
        if any(f"{p}{ren.antigo}\"" in linha or f"{p}{ren.antigo}\\" in linha for p in ren.prefixos)
    ]
    if sobra:
        raise SystemExit("Nome antigo ainda no .rpp:\n  " + "\n  ".join(sobra[:10]))
    return texto, contagem


def cartaz_atual_confere(texto: str, ordem: int) -> bool:
    """O CODE de hoje e o que o gerador faz com o slide de hoje? Senao, nao toco."""
    inicio = texto.find(f'\n    NAME "{ordem:02d} - MONITOR"\n')
    fim = texto.find("\n  <TRACK", inicio)
    codigo = CODE_RE.search(texto, inicio, fim if fim > 0 else len(texto)).group(0)
    ini = re.search(r"\|ini=([0-9.]+);", codigo).group(1)
    fim_c = re.search(r"\|fim=([0-9.]+);", codigo).group(1)
    linhas = [l[len("            |") :] for l in codigo.splitlines()[1:-1]]
    gerado = codigo_do_cartaz(linhas_do_slide(PPTX, slide_do_bloco(ordem)), ini, fim_c)
    return "\n".join(linhas) + "\n" == gerado


def run_xml(texto: str, vermelho: bool) -> str:
    cor = VERMELHO if vermelho else ""
    props = f'<a:rPr lang="pt-BR" sz="{{sz}}" dirty="0">{cor}</a:rPr>' if cor else '<a:rPr lang="pt-BR" sz="{sz}" dirty="0"/>'
    return f"<a:r>{props}<a:t>{escape(texto)}</a:t></a:r>"


def novo_slide(xml: str, musicas: list[Musica], posicao: int | None = None) -> str:
    """Cada musica entra como as do bloco 23: quebra, nome branco, tom vermelho.

    Com posicao (1 = primeira linha de musica), entram nessa linha e as de
    depois descem; sem ela, vao para o fim.
    """
    corpo_ini = xml.find("<p:txBody>")
    corpo_fim = xml.find("</p:txBody>", corpo_ini)
    paragrafos = list(re.finditer(r"<a:p>.*?</a:p>", xml[corpo_ini:corpo_fim], re.DOTALL))
    if len(paragrafos) < 2:
        raise SystemExit("Slide sem paragrafo de musicas.")
    p = paragrafos[-1]
    texto_p = p.group(0)
    sz = re.findall(r'<a:rPr lang="pt-BR" sz="(\d+)"', texto_p)
    sz = sz[-1] if sz else "7200"
    quebra = f'<a:br><a:rPr lang="pt-BR" sz="{sz}" dirty="0"/></a:br>'
    linhas = [
        run_xml(m.slide + " ", False).replace("{sz}", sz) + run_xml(m.tom, True).replace("{sz}", sz)
        for m in musicas
    ]
    quebras = [m.start() for m in re.finditer(r"<a:br>", texto_p)]
    if posicao is None or posicao > len(quebras) + 1:
        corte = texto_p.find("<a:endParaRPr")
        corte = corte if corte >= 0 else texto_p.rfind("</a:p>")
        extra = "".join(quebra + linha for linha in linhas)
    elif posicao == 1:
        corte = texto_p.find("<a:r>")
        extra = "".join(linha + quebra for linha in linhas)
    else:
        corte = quebras[posicao - 2]
        extra = "".join(quebra + linha for linha in linhas)
    novo_p = texto_p[:corte] + extra + texto_p[corte:]
    ini = corpo_ini + p.start()
    return xml[:ini] + novo_p + xml[ini + len(texto_p) :]


def trocar_no_slide(xml: str, velho: str, novo: str) -> str:
    """Troca o texto branco de uma musica, mantendo a formatacao do run."""
    run_re = re.compile(
        r"(<a:r><a:rPr[^>]*?(?:/>|>(?:(?!</a:rPr>).)*</a:rPr>)<a:t>)([^<]*)(</a:t></a:r>)",
        re.DOTALL,
    )
    achados = [
        m for m in run_re.finditer(xml)
        if "FF0000" not in m.group(1) and unescape(m.group(2)).strip() == velho
    ]
    if len(achados) != 1:
        raise SystemExit(f"Slide: esperava 1 run com {velho!r}, achei {len(achados)}.")
    m = achados[0]
    texto = unescape(m.group(2))
    sobra = texto[len(texto.rstrip()) :]
    return xml[: m.start(2)] + escape(novo + sobra) + xml[m.end(2) :]


def gravar_pptx(origem: Path, membros: dict[str, str], destino: Path) -> None:
    with zipfile.ZipFile(origem) as entrada, zipfile.ZipFile(destino, "w") as saida:
        for info in entrada.infolist():
            if info.filename in membros:
                dados = membros[info.filename].encode("utf-8")
            else:
                dados = entrada.read(info)
            saida.writestr(info, dados)


def trocar_strings(texto: str, ren: Renomeador) -> tuple[str, int]:
    trocas = 0

    def troca(m: re.Match) -> str:
        nonlocal trocas
        novo = ren.string(m.group(1))
        if novo == m.group(1):
            return m.group(0)
        trocas += 1
        return f'"{novo}"'

    return STRING_JSON_RE.sub(troca, texto), trocas


def pptx_aberto() -> bool:
    """Aberto no PowerPoint o arquivo fica travado; nem sempre aparece o ~$<nome>."""
    if (PPTX.parent / f"~${PPTX.name}").exists():
        return True
    try:
        with open(PPTX, "r+b"):
            return False
    except PermissionError:
        return True


def backup_do_repertorio(arquivo: Path, stamp: str) -> Path:
    """Caminho do backup de um .pptx/.pdf de repertorio, na pasta Backups ao lado dele."""
    pasta = arquivo.parent / "Backups"
    pasta.mkdir(exist_ok=True)
    return pasta / f"{arquivo.stem}-{stamp}{arquivo.suffix}.bak"


def exportar_pdf(pptx: Path, pdf: Path) -> None:
    """Exporta pelo PowerPoint, como o PDF original: todos os slides, 1 por pagina."""
    tmp = pdf.with_name(pdf.stem + ".novo.pdf")
    tmp.unlink(missing_ok=True)
    script = (
        "$ErrorActionPreference='Stop';"
        "$app=New-Object -ComObject PowerPoint.Application;"
        f"$p=$app.Presentations.Open('{pptx}',-1,0,0);"
        f"try{{$p.SaveAs('{tmp}',32)}}finally{{$p.Close()}};"
        "if($app.Presentations.Count -eq 0){$app.Quit()}"
    )
    # O Windows PowerShell 5 aqui nao enxerga Presentations pelo COM; o pwsh sim.
    shell = shutil.which("pwsh") or "powershell"
    subprocess.run([shell, "-NoProfile", "-Command", script], check=True)
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise SystemExit("PowerPoint nao gerou o PDF.")
    tmp.replace(pdf)


def reaper_aberto() -> bool:
    if sys.platform != "win32":
        return False
    saida = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
    return "reaper.exe" in saida.lower()


@dataclass
class Mudanca:
    ordem: int
    antigo: str
    novo: str
    musicas_slide: list[str]
    descricao: list[str]
    slide: Callable[[str], str]


def nome_do_bloco(texto_rpp: str, ordem: int) -> str:
    nome = next(
        (m.group(3) for m in MARKER_RE.finditer(texto_rpp) if int(m.group(1)) == ordem and m.group(3)),
        None,
    )
    if nome is None:
        raise SystemExit(f"Bloco {ordem} nao existe no projeto.")
    return nome


def mudanca_acrescentar(
    texto_rpp: str, ordem: int, pedidos: list[str], conhecidas, posicao: int | None = None
) -> Mudanca:
    antigo = nome_do_bloco(texto_rpp, ordem)
    musicas = [interpretar(p, conhecidas) for p in pedidos]
    ja_tem = {chave(n) for n in antigo.split(" - ")}
    repetidas = [m.projeto for m in musicas if chave(m.projeto) in ja_tem]
    if repetidas:
        raise SystemExit(f"Ja estao no bloco {ordem}: {', '.join(repetidas)}")
    partes = antigo.split(" - ")
    if posicao is not None and not 1 <= posicao <= len(partes) + 1:
        raise SystemExit(f"--posicao vai de 1 a {len(partes) + 1} no bloco {ordem}.")
    em = len(partes) if posicao is None else posicao - 1
    novo = " - ".join(partes[:em] + [m.projeto for m in musicas] + partes[em:])
    return Mudanca(
        ordem,
        antigo,
        novo,
        [m.slide for m in musicas],
        [f"+ projeto {m.projeto!r} | slide {m.slide!r} | tom {m.tom}" for m in musicas],
        lambda xml: novo_slide(xml, musicas, posicao),
    )


def mudancas_trocar(texto_rpp: str, pedido: str, blocos: list[int], conhecidas) -> list[Mudanca]:
    """Renomeia uma musica em todo bloco que a tem (ou so nos blocos dados)."""
    velho, sep, novo_txt = pedido.partition("=")
    if not sep:
        raise SystemExit('--trocar espera "NOME ATUAL=NOME NOVO" ou "NOME ATUAL=NOME NOVO|Texto do Slide".')
    velho = velho.strip()
    nova = interpretar(novo_txt, {})
    ref = conhecidas.get(chave(velho))
    candidatos = {chave(velho)} | ({chave(ref.slide)} if ref else set())
    mudancas: list[Mudanca] = []
    for match in MARKER_RE.finditer(texto_rpp):
        ordem, nome = int(match.group(1)), match.group(3)
        if not nome or (blocos and ordem not in blocos):
            continue
        partes = nome.split(" - ")
        pos = [i for i, parte in enumerate(partes) if chave(parte) == chave(velho)]
        if not pos:
            continue
        no_slide = [t for t, tom in linhas_do_slide(PPTX, slide_do_bloco(ordem)) if tom]
        velho_slide = [t for t in no_slide if chave(t) in candidatos]
        if len(velho_slide) != 1 and len(no_slide) == len(partes):
            velho_slide = [no_slide[pos[0]]]
        if len(velho_slide) != 1:
            raise SystemExit(f"Bloco {ordem}: nao achei {velho!r} no slide {no_slide}.")
        antigo_slide = velho_slide[0]
        partes[pos[0]] = nova.projeto
        mudancas.append(
            Mudanca(
                ordem,
                nome,
                " - ".join(partes),
                [nova.slide],
                [f"~ projeto {velho!r} -> {nova.projeto!r} | slide {antigo_slide!r} -> {nova.slide!r}"],
                lambda xml, a=antigo_slide: trocar_no_slide(xml, a, nova.slide),
            )
        )
    if not mudancas:
        raise SystemExit(f"Nenhum bloco tem {velho!r}.")
    return mudancas


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Acrescenta ou renomeia musicas na descricao dos blocos.")
    parser.add_argument("bloco", type=int, nargs="?")
    parser.add_argument("musicas", nargs="*", help='"NOME", "NOME@Tom" ou "NOME|Texto do Slide@Tom"')
    parser.add_argument("--trocar", help='"NOME ATUAL=NOME NOVO[|Texto do Slide]" em todo bloco que a tem')
    parser.add_argument("--posicao", type=int, help="as musicas entram nessa posicao do bloco (1 = primeira); padrao: no fim")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isso so mostra o plano")
    parser.add_argument("--sem-video", action="store_true", help="nao refaz o mp4 dos blocos")
    args = parser.parse_args()

    if pptx_aberto():
        parser.exit(1, f"Feche {PPTX.name} no PowerPoint antes; aberto ele fica travado.\n")
    texto_rpp = RPP.read_text(encoding="utf-8")
    conhecidas = referencias(texto_rpp)
    if args.trocar:
        if args.musicas:
            parser.error("use --trocar ou musicas para acrescentar, nao os dois")
        blocos = [args.bloco] if args.bloco else []
        mudancas = mudancas_trocar(texto_rpp, args.trocar, blocos, conhecidas)
    else:
        if args.bloco is None or not args.musicas:
            parser.error("informe o bloco e as musicas, ou --trocar")
        mudancas = [mudanca_acrescentar(texto_rpp, args.bloco, args.musicas, conhecidas, args.posicao)]

    membros: dict[str, str] = {}
    with zipfile.ZipFile(PPTX) as z:
        for mud in mudancas:
            membro = f"ppt/slides/slide{slide_do_bloco(mud.ordem)}.xml"
            membros[membro] = mud.slide(z.read(membro).decode("utf-8"))
    pptx_tmp = PPTX.with_name(PPTX.stem + ".novo.pptx")
    gravar_pptx(PPTX, membros, pptx_tmp)

    def abortar(msg: str) -> None:
        pptx_tmp.unlink(missing_ok=True)
        raise SystemExit(msg)

    marcadores = json.loads(REGION_MARKERS.read_text(encoding="utf-8"))
    textos = {c: c.read_text(encoding="utf-8") for c in TEXTOS_COM_NOMES if c.exists()}
    pastas: list[tuple[Path, Path]] = []
    mp3s: list[tuple[Path, Path]] = []
    mp4s: list[Path] = []
    rpp_novo = texto_rpp
    for mud in mudancas:
        ordem, antigo, novo = mud.ordem, mud.antigo, mud.novo
        ren = Renomeador(ordem, antigo, novo)
        pasta_velha = ABERTO / f"{ordem} - {antigo}"
        pasta_nova = ABERTO / f"{ordem} - {novo}"
        if not pasta_velha.is_dir():
            abortar(f"Pasta nao encontrada: {pasta_velha}")
        if pasta_nova.exists():
            abortar(f"Pasta destino ja existe: {pasta_nova}")
        if not cartaz_atual_confere(texto_rpp, ordem):
            abortar(f"Cartaz do bloco {ordem} no .rpp nao bate com o slide atual; confira antes.")
        linhas = linhas_do_slide(pptx_tmp, slide_do_bloco(ordem))
        no_slide = [t for t, tom in linhas if tom]
        if any(t not in no_slide for t in mud.musicas_slide):
            abortar(f"Slide novo do bloco {ordem} nao leu como esperado: {linhas}")
        rpp_novo, contagem = novo_rpp(rpp_novo, ordem, ren, linhas)
        alvo = [r for r in marcadores["starts"] if r["index"] == ordem and r["name"] == antigo]
        if len(alvo) != 1:
            abortar(f"region_markers.json: bloco {ordem} nao encontrado com o nome {antigo!r}.")
        alvo[0]["name"] = novo
        for caminho in textos:
            textos[caminho], trocas = trocar_strings(textos[caminho], ren)
            contagem[caminho.name] = trocas
        pastas.append((pasta_velha, pasta_nova))
        mp3_velho = EXPORT_REGIONS / f"{ordem:02d} - {safe_filename(antigo)}.mp3"
        if mp3_velho.exists():
            mp3s.append((mp3_velho, EXPORT_REGIONS / f"{ordem:02d} - {safe_filename(novo)}.mp3"))
        mp4s.append(EXPORT_VIDEOS / f"{ordem:02d} - {safe_filename(antigo)}.mp4")

        print(f"Bloco {ordem}")
        print(f"  antes:  {antigo}")
        print(f"  depois: {novo}")
        for linha in mud.descricao:
            print(f"  {linha}")
        print(f"  .rpp: {contagem.pop('region')} region, {contagem.pop('tracks')} tracks, "
              f"{contagem.pop('arquivos')} FILE, {contagem.pop('cartaz')} cartaz")
        for nome_arq, trocas in contagem.items():
            print(f"  {nome_arq}: {trocas} nomes")
        print(f"  pasta: {pasta_velha.name} -> {pasta_nova.name}")
        print(f"  mp3: {'renomeia' if mp3_velho.exists() else 'exporta em mono'}")
        print(f"  slide {slide_do_bloco(ordem)}: {no_slide}")
        print(f"  mp4: {'nao refaz' if args.sem_video else 'refaz'}")
    print("pptx e pdf: refeitos uma vez para todos os blocos")

    if not args.aplicar:
        pptx_tmp.unlink()
        print("Plano apenas. Rode com --aplicar para gravar.")
        return
    if reaper_aberto():
        abortar("Feche o Reaper antes: ele regravaria o .rpp antigo e prende os arquivos.")
    if pptx_aberto():
        abortar(f"Feche {PPTX.name} no PowerPoint antes.")

    print(f"Backup .rpp: {backup_rpp(RPP)}")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_pptx = backup_do_repertorio(PPTX, stamp)
    shutil.copy2(PPTX, backup_pptx)
    print(f"Backup .pptx: {backup_pptx}")
    if PDF.exists():
        backup_pdf = backup_do_repertorio(PDF, stamp)
        shutil.copy2(PDF, backup_pdf)
        print(f"Backup .pdf: {backup_pdf}")

    for velha, nova in pastas:
        velha.rename(nova)
    RPP.write_text(rpp_novo, encoding="utf-8")
    REGION_MARKERS.write_text(json.dumps(marcadores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for caminho, conteudo in textos.items():
        caminho.write_text(conteudo, encoding="utf-8")
    pptx_tmp.replace(PPTX)
    exportar_pdf(PPTX, PDF)
    print(f"PDF: {PDF}")
    atualizar_completo()
    for velho, novo in mp3s:
        velho.rename(novo)
    renomeados = {novo for _, novo in mp3s}
    for mud in mudancas:
        if EXPORT_REGIONS / f"{mud.ordem:02d} - {safe_filename(mud.novo)}.mp3" not in renomeados:
            subprocess.run(
                [sys.executable, str(_SCRIPTS / "exportar_regions_mp3.py"), "--mono",
                 "--from-order", str(mud.ordem), "--to-order", str(mud.ordem)],
                check=True,
            )
    if not args.sem_video:
        for mud, mp4_velho in zip(mudancas, mp4s):
            subprocess.run(
                [sys.executable, str(_SCRIPTS / "exportar_blocos_video.py"),
                 "--from-order", str(mud.ordem), "--to-order", str(mud.ordem)],
                check=True,
            )
            if mp4_velho.exists():
                mp4_velho.unlink()
    print("Pronto.")


if __name__ == "__main__":
    main()
