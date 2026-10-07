#!/usr/bin/env python3
"""Coloca o cartaz do PowerPoint na track de monitor, como texto do Reaper.

O processador de video nativo desenha o titulo e os tons em vermelho e as
musicas em branco, em Arial, no fundo preto. O tamanho e o mesmo na maioria
dos blocos; so diminui quando a linha ou a pilha nao cabem. O item cobre a
region inteira. Nao grava imagem do cartaz nem renomeia audio.
"""

from __future__ import annotations

import re
import struct
import sys
import zlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import extract_track_blocks, replace_track_blocks  # noqa: E402
from unificar_reaper import get_track_name, new_guid  # noqa: E402

import projeto  # noqa: E402

WORKSPACE = projeto.WORKSPACE
RPP = projeto.RPP
# So os blocos VS: o slide N e o VS BLOCO N. O "Repertório Rota do Chopp.pptx"
# (sem VS) e do usuario e nenhum script o abre.
PPTX = projeto.PPTX
SLIDE_DO_BLOCO_1 = 1
MONITOR_DIR = WORKSPACE / "MONITOR"
BASE_PNG = MONITOR_DIR / "base.png"
FUNDO = (0, 0, 0)
# Arial a 13% da altura. Em 1920x1080 cabe em cerca de 80% dos blocos.
TAMANHO_PADRAO = 0.130
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
MARKER_RE = re.compile(r'^  MARKER (\d+) ([0-9.]+) "(.*)"')
TRACKHEIGHT_RE = re.compile(r"^    TRACKHEIGHT .+$", re.MULTILINE)
FXCHAIN_RE = re.compile(r"\n    <FXCHAIN\n(?:.*\n)*?    >", re.MULTILINE)
ITEM_RE = re.compile(r"\n    <ITEM\n(?:.*\n)*?    >", re.MULTILINE)


def slide_do_bloco(order: int) -> int:
    return SLIDE_DO_BLOCO_1 + order - 1


def eel_string(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def cor_do_run(run: ET.Element) -> str | None:
    props = run.find(A + "rPr")
    if props is None:
        return None
    cor = props.find(".//" + A + "srgbClr")
    if cor is None:
        return None
    return cor.attrib.get("val", "").upper()


def _fechar_linha(partes: list[tuple[str, bool]]) -> tuple[str, str | None] | None:
    if not any(texto.strip() for texto, _ in partes):
        return None
    musica = "".join(texto for texto, vermelho in partes if not vermelho).strip()
    tom = "".join(texto for texto, vermelho in partes if vermelho).strip()
    if not musica and tom:
        return tom, None
    if musica:
        return musica, tom or None
    return None


def linhas_do_slide(pptx: Path, slide_number: int) -> list[tuple[str, str | None]]:
    """Cada linha e (texto branco, tom vermelho). Titulo vem com tom None."""
    with zipfile.ZipFile(pptx) as archive:
        root = ET.fromstring(archive.read(f"ppt/slides/slide{slide_number}.xml"))
    corpo = root.find(".//" + P + "txBody")
    if corpo is None:
        raise SystemExit(f"Slide {slide_number} sem texto.")
    linhas: list[tuple[str, str | None]] = []
    for paragrafo in corpo.findall(A + "p"):
        partes: list[tuple[str, bool]] = []
        for child in paragrafo:
            tag = child.tag.split("}")[-1]
            if tag == "br":
                linha = _fechar_linha(partes)
                if linha:
                    linhas.append(linha)
                partes = []
                continue
            if tag != "r":
                continue
            texto = "".join(node.text or "" for node in child.iter(A + "t"))
            if texto:
                partes.append((texto, cor_do_run(child) == "FF0000"))
        linha = _fechar_linha(partes)
        if linha:
            linhas.append(linha)
    if not linhas:
        raise SystemExit(f"Slide {slide_number} sem linhas.")
    return linhas


def _medir_linha(corpo: list[str], musica: str, tom: str | None) -> None:
    corpo.append(f'#L="{eel_string(musica)}";')
    corpo.append("gfx_str_measure(#L,lw,lh);")
    if tom:
        corpo.append(f'#R="{eel_string(tom)}";')
        corpo.append("gfx_str_measure(#R,rw,rh);")
        corpo.append("linew=lw+spw+rw;")
    else:
        corpo.append("linew=lw;")
    corpo.append("linew>widest ? widest=linew;")


def _desenhar_linha(corpo: list[str], musica: str, tom: str | None) -> None:
    corpo.append(f'#L="{eel_string(musica)}";')
    corpo.append("gfx_str_measure(#L,lw,lh);")
    if tom:
        corpo.append(f'#R="{eel_string(tom)}";')
        corpo.append("gfx_str_measure(#R,rw,rh);")
        corpo.append("x=(project_w-lw-spw-rw)*0.5;")
        corpo.append("gfx_set(1,1,1,1);")
        corpo.append("gfx_str_draw(#L,x,y);")
        corpo.append("gfx_set(1,0,0,1);")
        corpo.append("gfx_str_draw(#R,x+lw+spw,y);")
    else:
        corpo.append("gfx_set(1,1,1,1);")
        corpo.append("gfx_str_draw(#L,(project_w-lw)*0.5,y);")
    corpo.append("y+=step;")


def codigo_do_cartaz(linhas: list[tuple[str, str | None]], inicio: str, fim: str) -> str:
    titulos = [texto for texto, tom in linhas if tom is None]
    pares = [(texto, tom) for texto, tom in linhas if tom is not None]
    titulo = titulos[0] if titulos else "VS"
    corpo = [
        "// Cartaz do bloco, texto editavel",
        'font="Arial";',
        f"ini={inicio};",
        f"fim={fim};",
        "(project_time<ini || project_time>=fim) ? 0 : (",
        "project_wh_valid===0 ? input_info(-2,project_w,project_h);",
        "gfx_a2=0;",
        "gfx_blit(-2,1);",
        "gfx_set(0,0,0,1);",
        "gfx_fillrect(0,0,project_w,project_h);",
        f"px=project_h*{TAMANHO_PADRAO};",
        "gfx_setfont(px,font);",
        '#S=" ";',
        "gfx_str_measure(#S,spw,sph);",
        f'#T="{eel_string(titulo)}";',
        "gfx_str_measure(#T,tw,th);",
        "widest=tw;",
    ]
    for musica, tom in pares:
        _medir_linha(corpo, musica, tom)
    corpo.extend(
        [
            f"n={1 + len(pares)};",
            "step=px*1.35;",
            "total=step*n;",
            "maxw=project_w*0.92;",
            "maxh=project_h*0.88;",
            "scale=1;",
            "widest>maxw ? scale=maxw/widest;",
            "total*scale>maxh ? scale=maxh/total;",
            "px*=scale;",
            "gfx_setfont(px,font);",
            "gfx_str_measure(#S,spw,sph);",
            "gfx_str_measure(#T,tw,th);",
            "step=px*1.35;",
            "y=(project_h-step*n)*0.5;",
            "gfx_set(1,0,0,1);",
            "gfx_str_draw(#T,(project_w-tw)*0.5,y);",
            "y+=step;",
        ]
    )
    for musica, tom in pares:
        _desenhar_linha(corpo, musica, tom)
    corpo.append(");")
    return "\n".join(corpo) + "\n"


def chunk_take_fx(codigo: str) -> str:
    """Efeito no item, nao na track. Fora do item o video nao continua."""
    linhas = ["          <CODE"]
    for linha in codigo.splitlines():
        linhas.append(f"            |{linha}")
    linhas.append("          >")
    linhas.append("          CODEPARM " + " ".join(["0"] * 40))
    miolo = "\n".join(linhas)
    return (
        "      <TAKEFX\n"
        "        SHOW 0\n"
        "        LASTSEL 0\n"
        "        DOCKED 0\n"
        "        BYPASS 0 0 0\n"
        '        <VIDEO_EFFECT "Video processor" ""\n'
        f"{miolo}\n"
        "        >\n"
        '        PRESETNAME "Cartaz VS"\n'
        f"        FXID {new_guid()}\n"
        "        WAK 0 0\n"
        "      >\n"
    )


def png_solido(caminho: Path, cor: tuple[int, int, int], largura: int = 1920, altura: int = 1080) -> None:
    """Quadro Full HD. O texto e desenhado nesse tamanho; um quadro pequeno fica borrado."""
    r, g, b = cor
    cru = b"".join(b"\x00" + bytes((r, g, b)) * largura for _ in range(altura))

    def pedaco(tag: bytes, dados: bytes) -> bytes:
        return (
            struct.pack(">I", len(dados))
            + tag
            + dados
            + struct.pack(">I", zlib.crc32(tag + dados) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + pedaco(b"IHDR", ihdr)
        + pedaco(b"IDAT", zlib.compress(cru, 9))
        + pedaco(b"IEND", b"")
    )
    caminho.parent.mkdir(exist_ok=True)
    caminho.write_bytes(png)


def span_da_region(rpp_text: str, order: int) -> tuple[str, str]:
    inicio = fim = None
    for line in rpp_text.splitlines():
        match = MARKER_RE.match(line)
        if not match or int(match.group(1)) != order:
            continue
        if match.group(3):
            inicio = match.group(2)
        else:
            fim = match.group(2)
    if inicio is None or fim is None:
        raise SystemExit(f"Region {order} sem inicio ou fim no projeto.")
    if inicio == "0":
        return inicio, fim
    duracao = float(fim) - float(inicio)
    return inicio, f"{duracao:.10f}".rstrip("0")


def item_video(
    position: str,
    length: str,
    arquivo: str,
    rotulo: str,
    iid: int,
    take_fx: str = "",
) -> str:
    return (
        "    <ITEM\n"
        f"      POSITION {position}\n"
        "      SNAPOFFS 0\n"
        f"      LENGTH {length}\n"
        "      LOOP 1\n"
        "      ALLTAKES 0\n"
        "      FADEIN 1 0 0 1 0 0 0\n"
        "      FADEOUT 1 0 0 1 0 0 0\n"
        "      MUTE 0 0\n"
        "      SEL 0\n"
        f"      IGUID {new_guid()}\n"
        f"      IID {iid}\n"
        f'      NAME "{rotulo}"\n'
        "      VOLPAN 1 0 1 -1\n"
        "      SOFFS 0\n"
        "      PLAYRATE 1 1 0 -1 0 0.0025\n"
        "      CHANMODE 0\n"
        f"      GUID {new_guid()}\n"
        "      <SOURCE VIDEO\n"
        f'        FILE "{arquivo}"\n'
        "      >\n"
        f"{take_fx}"
        "    >\n"
    )


def proximo_iid(rpp_text: str) -> int:
    ids = [int(n) for n in re.findall(r"^      IID (\d+)$", rpp_text, re.MULTILINE)]
    return max(ids, default=0) + 1


def aplicar_na_track(block: str, item: str, altura: bool = False) -> str:
    block = FXCHAIN_RE.sub("\n", block)
    block = ITEM_RE.sub("\n", block)
    if altura:
        block = TRACKHEIGHT_RE.sub("    TRACKHEIGHT 220 0 0 0 0 0 0", block, count=1)
    fechamento = block.rstrip()
    miolo, _, _ = fechamento.rpartition("\n")
    return miolo + "\n" + item + "  >\n"


def posicao_marker(rpp_text: str, order: int, inicio: bool) -> str | None:
    achado = None
    for line in rpp_text.splitlines():
        match = MARKER_RE.match(line)
        if not match or int(match.group(1)) != order:
            continue
        if bool(match.group(3)) == inicio:
            achado = match.group(2)
    return achado


def formato_numero(valor: float) -> str:
    return f"{valor:.10f}".rstrip("0").rstrip(".") or "0"


def main() -> None:
    png_solido(BASE_PNG, FUNDO)
    texto_rpp = RPP.read_text(encoding="utf-8")
    ordens = sorted(
        {
            int(match.group(1))
            for line in texto_rpp.splitlines()
            if (match := MARKER_RE.match(line)) and match.group(3)
        }
    )
    iid = proximo_iid(texto_rpp)
    itens: dict[str, str] = {}
    resumo: list[str] = []
    for order in ordens:
        linhas = linhas_do_slide(PPTX, slide_do_bloco(order))
        titulo = next(texto for texto, tom in linhas if tom is None)
        if not re.fullmatch(rf"VS BLOCO {order}", titulo, re.IGNORECASE):
            raise SystemExit(f"Slide do bloco {order} inesperado: {titulo!r}")
        # O slide entra no fim do bloco anterior, para no inicio deste o quadro
        # ja ser o certo. Termina 1 ms antes do proximo, senao a track de cima
        # continua por cima no primeiro instante.
        inicio_regiao = posicao_marker(texto_rpp, order, True)
        fim_regiao = posicao_marker(texto_rpp, order, False)
        if inicio_regiao is None or fim_regiao is None:
            raise SystemExit(f"Region {order} sem intervalo.")
        if order == ordens[0]:
            inicio = inicio_regiao
        else:
            inicio = posicao_marker(texto_rpp, order - 1, False)
            if inicio is None:
                raise SystemExit(f"Region {order - 1} sem fim.")
        if order == ordens[-1]:
            fim = fim_regiao
        else:
            fim = formato_numero(float(fim_regiao) - 0.001)
        duracao = formato_numero(float(fim) - float(inicio))
        nome = f"{order:02d} - MONITOR"
        itens[nome] = item_video(
            inicio,
            duracao,
            "MONITOR\\base.png",
            titulo,
            iid,
            chunk_take_fx(codigo_do_cartaz(linhas, inicio, fim)),
        )
        iid += 1
        musicas = sum(1 for _, tom in linhas if tom is not None)
        resumo.append(f"{nome}: {inicio} + {duracao}s, {musicas} musicas")

    blocos = extract_track_blocks(texto_rpp)
    achados: set[str] = set()
    novos = []
    for bloco in blocos:
        nome_bloco = get_track_name(bloco)
        if nome_bloco in itens:
            novos.append(aplicar_na_track(bloco, itens[nome_bloco]))
            achados.add(nome_bloco)
        else:
            novos.append(bloco)
    faltando = sorted(set(itens) - achados)
    if faltando:
        raise SystemExit(f"Tracks sem cartaz: {', '.join(faltando)}")

    backup = backup_rpp(RPP)
    RPP.write_text(replace_track_blocks(texto_rpp, novos), encoding="utf-8")
    print(f"Backup: {backup}")
    print(f"{len(itens)} cartazes")
    for linha in resumo:
        print(linha)


if __name__ == "__main__":
    main()
