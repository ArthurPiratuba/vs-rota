#!/usr/bin/env python3
"""Seis faders de grupo no início do projeto, na ordem do show.

Click, Regência, Metais, Teclas, Cordas, Percussão.
Cada fader é o barramento da família: volume, pan, mute, solo e medidor.
A trilha continua na pasta da música e o áudio passa pelo grupo uma vez só.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_rpp import backup_rpp  # noqa: E402
from renomear_tracks_rpp import extract_track_blocks, format_rpp_name  # noqa: E402
from unificar_reaper import (  # noqa: E402
    PEAKCOL_BLUE,
    get_track_name,
    is_folder_parent,
    new_guid,
    replace_all_track_blocks,
    rgb_to_peakcol,
)

WORKSPACE = Path(__file__).resolve().parent.parent
RPP = WORKSPACE / "ROTA DO CHOPP UNIFICADO.rpp"
CLASSIFICACAO_PATH = WORKSPACE / "data" / "grupos_classificacao.json"
CORRECOES_PATH = WORKSPACE / "data" / "correcoes_grupos.json"
HTML_PATH = WORKSPACE / "relatorio" / "grupos.html"

MUTE_MASTER_INDEX = 4
MUTE_SLAVE_INDEX = 5
SOLO_MASTER_INDEX = 6
SOLO_SLAVE_INDEX = 7
GROUP_FLAGS_PATTERN = re.compile(r"^    GROUP_FLAGS .+\n", re.MULTILINE)
AUXRECV_PATTERN = re.compile(r"^    AUXRECV .+\n", re.MULTILINE)
MAINSEND_PATTERN = re.compile(r"^    MAINSEND \d+", re.MULTILINE)
MIDIOUT_PATTERN = re.compile(r"^    MIDIOUT ", re.MULTILINE)
VU_PATTERN = re.compile(r"^    VU .+\n", re.MULTILINE)
PEAKCOL_PATTERN = re.compile(r"^(\s*PEAKCOL )(\d+)(.*)$", re.MULTILINE)
MONITOR_PATTERN = re.compile(r"^\d{2} - MONITOR$")

# Ordem dos faders, da esquerda para a direita.
GRUPOS = (
    {
        "id": "click",
        "nome": "Click",
        "master": "CLICK",
        "bit": 1,
        "cor": "#ff3030",
        "peakcol": rgb_to_peakcol(255, 48, 48),
    },
    {
        "id": "regencia",
        "nome": "Regência",
        "master": "REGÊNCIA",
        "bit": 2,
        "cor": "#3d7dff",
        "peakcol": PEAKCOL_BLUE,
    },
    {
        "id": "metais",
        "nome": "Metais",
        "master": "METAIS",
        "bit": 4,
        "cor": "#e6a817",
        "peakcol": rgb_to_peakcol(255, 176, 32),
    },
    {
        "id": "teclas",
        "nome": "Teclas",
        "master": "TECLAS",
        "bit": 8,
        "cor": "#b450dc",
        "peakcol": rgb_to_peakcol(176, 80, 220),
    },
    {
        "id": "cordas",
        "nome": "Cordas",
        "master": "CORDAS",
        "bit": 16,
        "cor": "#2ea85a",
        "peakcol": rgb_to_peakcol(46, 168, 90),
    },
    {
        "id": "percussao",
        "nome": "Percussão",
        "master": "PERCUSSÃO",
        "bit": 32,
        "cor": "#ff6e1e",
        "peakcol": rgb_to_peakcol(255, 110, 30),
    },
)
GRUPO_POR_ID = {grupo["id"]: grupo for grupo in GRUPOS}
MASTER_NOMES = {grupo["master"] for grupo in GRUPOS}
OPCOES_GRUPO = [{"id": "fora", "nome": "Fora", "cor": "#8a8175"}] + [
    {"id": grupo["id"], "nome": grupo["nome"], "cor": grupo["cor"]} for grupo in GRUPOS
]

# Palavra no stem, já sem acento. A ordem das famílias importa.
FAMILIAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "metais",
        (
            "METAIS",
            "SAX",
            "TROMPETE",
            "TROMBONE",
            "TUBA",
            "FLAUTA",
            "FLAUTIM",
            "SOPRO",
            "CLARINET",
            "TROMPA",
            "BOMBARDINO",
        ),
    ),
    (
        "teclas",
        (
            "TECLAD",
            "PIANO",
            "KEYS",
            "SANFONA",
            "ACORDEON",
            "ACORDEAO",
            "GAITA",
            "ORGAO",
            "SINTET",
            "SYNTH",
            "RHODES",
            "MARIMBA",
            "CARRILH",
            "EFEITO",
            "BELLS",
            "ASSOBIO",
        ),
    ),
    (
        "cordas",
        (
            "GUITARR",
            "VIOLINO",
            "VIOLAO",
            "VIOLOES",
            "VIOLA",
            "CAVACO",
            "CAVAQ",
            "STRING",
            "BAIXO",
            "BASS",
            "CELLO",
            "UKULEL",
        ),
    ),
    (
        "percussao",
        (
            "PERCUSS",
            "SURDO",
            "BATERIA",
            "PANDEIRO",
            "TIMBAL",
            "ZABUMBA",
            "TRIANGUL",
            "CONGA",
            "REPIQUE",
            "AGOGO",
            "TAMBOR",
            "SHAKER",
            "GANZA",
            "CUICA",
            "CAIXA",
        ),
    ),
)
MOTIVO_FAMILIA = {
    "metais": "Sopro ou metais, pelo nome.",
    "teclas": "Tecla ou efeito, pelo nome.",
    "cordas": "Corda, pelo nome.",
    "percussao": "Percussão, pelo nome.",
}
REVISAR_SE_CONTEM = {"ASSOBIO": "Assobio entrou em Teclas porque o grupo inclui efeitos."}
MOTIVO_EXTRA = {
    "BASS": "Baixo entrou em Cordas.",
    "BAIXO": "Baixo entrou em Cordas.",
}


@dataclass
class Faixa:
    trilha: str
    bloco: str
    stem: str
    grupo: str | None
    revisar: bool
    motivo: str

    def to_json(self) -> dict:
        grupo = GRUPO_POR_ID.get(self.grupo or "")
        return {
            "trilha": self.trilha,
            "bloco": self.bloco,
            "stem": self.stem,
            "grupo": self.grupo,
            "grupo_nome": grupo["nome"] if grupo else "Fora",
            "revisar": self.revisar,
            "motivo": self.motivo,
        }


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text.upper()


def ambiguous(folded: str) -> str | None:
    if folded.startswith("BACKING"):
        return "Backing é um mix pronto, não um naipe."
    if folded == "VS" or folded.startswith("VS ") or folded.startswith("VS-"):
        return "VS é guia ou mix de referência, não um naipe."
    if "STRINGS E KEYS" in folded or "STRING E KEYS" in folded:
        return "O stem mistura cordas e teclas."
    if "SOPRO E SANFONA" in folded or "SANFONA E SOPRO" in folded:
        return "O stem mistura sopro e sanfona."
    if folded.startswith("GUIA"):
        return "Guia não é um naipe."
    if folded in {"VOZ", "SEGUNDA VOZ"}:
        return "Voz não entra em grupo de instrumento."
    if folded == "FANTASIA" or folded.startswith("BASE FANTASIA"):
        return "Fantasia pode ser pad, efeito ou outro naipe. Ficou de fora até você definir."
    return None


def classificar_stem(stem: str, papel: str | None) -> tuple[str | None, bool, str]:
    if papel == "click":
        return "click", False, "Click de todas as músicas."
    if papel == "regencia":
        return "regencia", False, "Regência de todas as músicas."
    folded = fold(stem)
    if folded == "MONITOR":
        return None, False, "Monitor de cifra. Não entra em grupo."
    duvida = ambiguous(folded)
    if duvida:
        return None, True, duvida
    for grupo_id, chaves in FAMILIAS:
        for chave in chaves:
            if chave in folded:
                motivo = MOTIVO_EXTRA.get(chave, MOTIVO_FAMILIA[grupo_id])
                revisar = False
                for marca, nota in REVISAR_SE_CONTEM.items():
                    if marca in folded:
                        revisar = True
                        motivo = nota
                return grupo_id, revisar, motivo
    return None, True, "O nome não diz a família. Ficou de fora."


def papel_da_trilha(name: str) -> str | None:
    from renomear_tracks_rpp import role_from_track_name

    return role_from_track_name(name)


def stem_da_trilha(name: str, bloco: str) -> str:
    if MONITOR_PATTERN.match(name):
        return "MONITOR"
    song = bloco.split(" - ", 1)[1]
    order = bloco.split(" - ", 1)[0]
    body = name
    for suffix in (" - CLICK", " - REGÊNCIA"):
        if body.endswith(suffix):
            body = body[: -len(suffix)]
    tail = " - " + song
    if body.endswith(tail):
        body = body[: -len(tail)]
    prefix = order + " - "
    if body.startswith(prefix):
        body = body[len(prefix) :]
    return body or name


def listar_faixas(blocks: list[str]) -> list[Faixa]:
    faixas: list[Faixa] = []
    bloco = ""
    for block in blocks:
        name = get_track_name(block)
        if name in MASTER_NOMES:
            continue
        if is_folder_parent(block):
            bloco = name
            continue
        if not bloco:
            continue
        papel = papel_da_trilha(name)
        stem = stem_da_trilha(name, bloco)
        grupo, revisar, motivo = classificar_stem(stem, papel)
        faixas.append(
            Faixa(
                trilha=name,
                bloco=bloco,
                stem=stem,
                grupo=grupo,
                revisar=revisar,
                motivo=motivo,
            )
        )
    return faixas


def aplicar_correcoes(faixas: list[Faixa], correcoes: list[dict]) -> list[Faixa]:
    por_trilha = {item["trilha"]: item.get("grupo") for item in correcoes}
    ajustadas: list[Faixa] = []
    for faixa in faixas:
        if faixa.trilha not in por_trilha:
            ajustadas.append(faixa)
            continue
        grupo = por_trilha[faixa.trilha]
        if grupo in (None, "", "fora"):
            grupo = None
        elif grupo not in GRUPO_POR_ID:
            raise SystemExit(f"Grupo desconhecido em correcoes: {grupo}")
        motivo = faixa.motivo
        if grupo != faixa.grupo:
            nome = GRUPO_POR_ID[grupo]["nome"] if grupo else "Fora"
            motivo = f"Definido no relatório: {nome}."
        ajustadas.append(
            Faixa(
                trilha=faixa.trilha,
                bloco=faixa.bloco,
                stem=faixa.stem,
                grupo=grupo,
                revisar=False if grupo != faixa.grupo else faixa.revisar,
                motivo=motivo,
            )
        )
    return ajustadas


def format_group_flags(bit: int) -> str:
    values = [0] * (SOLO_MASTER_INDEX + 1)
    values[MUTE_MASTER_INDEX] = bit
    values[SOLO_MASTER_INDEX] = bit
    return " ".join(str(value) for value in values)


def format_slave_flags(bit: int) -> str:
    values = [0] * (SOLO_SLAVE_INDEX + 1)
    values[MUTE_SLAVE_INDEX] = bit
    values[SOLO_SLAVE_INDEX] = bit
    return " ".join(str(value) for value in values)


def set_mainsend(block: str, enabled: int) -> str:
    if not MAINSEND_PATTERN.search(block):
        raise ValueError(f"Track sem MAINSEND: {get_track_name(block)}")
    return MAINSEND_PATTERN.sub(f"    MAINSEND {enabled}", block, count=1)


def set_receives(block: str, sources: list[int]) -> str:
    block = AUXRECV_PATTERN.sub("", block)
    if not sources:
        return block
    lines = "".join(
        f"    AUXRECV {index} 0 1 0 0 0 0 0 0 -1:U 0 -1 ''\n" for index in sources
    )
    # O Reaper grava os AUXRECV antes do MIDIOUT; na mesma ordem o diff fica limpo.
    if MIDIOUT_PATTERN.search(block):
        return MIDIOUT_PATTERN.sub(lambda m: lines + m.group(0), block, count=1)
    if not MAINSEND_PATTERN.search(block):
        raise ValueError(f"Track sem MAINSEND: {get_track_name(block)}")
    return MAINSEND_PATTERN.sub(lines + "    MAINSEND 1", block, count=1)


def set_group_flags(block: str, flags: str) -> str:
    line = f"    GROUP_FLAGS {flags}\n"
    if GROUP_FLAGS_PATTERN.search(block):
        return GROUP_FLAGS_PATTERN.sub(line, block, count=1)
    if not VU_PATTERN.search(block):
        raise ValueError(f"Track sem VU: {get_track_name(block)}")
    return VU_PATTERN.sub(lambda match: match.group(0) + line, block, count=1)


def clear_group_flags(block: str) -> str:
    return GROUP_FLAGS_PATTERN.sub("", block)


def set_peakcol(block: str, peakcol: int) -> str:
    if PEAKCOL_PATTERN.search(block):
        return PEAKCOL_PATTERN.sub(lambda m: f"{m.group(1)}{peakcol}{m.group(3)}", block, count=1)
    return block


def make_vca_master(grupo: dict) -> str:
    guid = new_guid()
    flags = format_group_flags(grupo["bit"])
    name = format_rpp_name(grupo["master"])
    return (
        f"  <TRACK {guid}\n"
        f"    NAME {name}\n"
        f"    PEAKCOL {grupo['peakcol']}\n"
        f"    BEAT -1\n"
        f"    AUTOMODE 0\n"
        f"    PANLAWFLAGS 3\n"
        f"    VOLPAN 1 0 -1 -1 1\n"
        f"    MUTESOLO 0 0 0\n"
        f"    IPHASE 0\n"
        f"    PLAYOFFS 0 1\n"
        f"    ISBUS 0 0\n"
        f"    BUSCOMP 0 0 0 0 0\n"
        f"    SHOWINMIX 1 0.6667 0.5 1 0.5 0 0 0 0\n"
        f"    FIXEDLANES 9 0 0 0 0\n"
        f"    LANEREC -1 -1 -1 0\n"
        f"    SEL 0\n"
        f"    REC 0 0 1 0 0 0 0 0\n"
        f"    VU 64\n"
        f"    GROUP_FLAGS {flags}\n"
        f"    TRACKHEIGHT 0 0 0 0 0 0 0\n"
        f"    INQ 0 0 0 0.5 100 0 0 100\n"
        f"    NCHAN 2\n"
        f"    FX 1\n"
        f"    TRACKID {guid}\n"
        f"    PERF 0\n"
        f"    MIDIOUT -1\n"
        f"    MAINSEND 1 0\n"
        f"  >\n"
    )


def refresh_master(block: str | None, grupo: dict) -> str:
    if block is None:
        return make_vca_master(grupo)
    block = set_peakcol(block, grupo["peakcol"])
    return set_group_flags(block, format_group_flags(grupo["bit"]))


def gravar_projeto(rpp_path: Path, faixas: list[Faixa]) -> None:
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    blocks = extract_track_blocks(text)
    por_nome = {faixa.trilha: faixa for faixa in faixas}
    existentes = {
        get_track_name(block): block
        for block in blocks
        if get_track_name(block) in MASTER_NOMES
    }
    novos = [refresh_master(existentes.get(grupo["master"]), grupo) for grupo in GRUPOS]
    for block in blocks:
        name = get_track_name(block)
        if name in MASTER_NOMES or is_folder_parent(block):
            if is_folder_parent(block):
                novos.append(clear_group_flags(block))
            continue
        faixa = por_nome.get(name)
        if faixa is None or not faixa.grupo:
            novos.append(set_mainsend(clear_group_flags(block), 1))
            continue
        bit = GRUPO_POR_ID[faixa.grupo]["bit"]
        novos.append(set_mainsend(set_group_flags(block, format_slave_flags(bit)), 0))

    por_indice = {get_track_name(block): index for index, block in enumerate(novos)}
    for grupo in GRUPOS:
        fontes = [
            por_indice[faixa.trilha]
            for faixa in faixas
            if faixa.grupo == grupo["id"] and faixa.trilha in por_indice
        ]
        master_index = por_indice[grupo["master"]]
        novos[master_index] = set_receives(novos[master_index], fontes)

    dest = backup_rpp(rpp_path)
    print(f"Backup: {dest}")
    rpp_path.write_text(replace_all_track_blocks(text, novos), encoding="utf-8")


def blocos_json(faixas: list[Faixa]) -> list[dict]:
    ordem: list[str] = []
    por_bloco: dict[str, list[dict]] = {}
    for faixa in faixas:
        if faixa.bloco not in por_bloco:
            ordem.append(faixa.bloco)
            por_bloco[faixa.bloco] = []
        por_bloco[faixa.bloco].append(faixa.to_json())
    return [{"bloco": bloco, "faixas": por_bloco[bloco]} for bloco in ordem]


def contagens(faixas: list[Faixa]) -> dict[str, int]:
    totais = {grupo["id"]: 0 for grupo in GRUPOS}
    totais["fora"] = 0
    totais["revisar"] = 0
    totais["monitor"] = 0
    for faixa in faixas:
        if faixa.stem == "MONITOR":
            totais["monitor"] += 1
            continue
        totais[faixa.grupo or "fora"] += 1
        if faixa.revisar:
            totais["revisar"] += 1
    return totais


def salvar_classificacao(faixas: list[Faixa]) -> None:
    CLASSIFICACAO_PATH.parent.mkdir(exist_ok=True)
    payload = {
        "atualizado": datetime.now().isoformat(timespec="seconds"),
        "grupos": [
            {"id": grupo["id"], "nome": grupo["nome"], "master": grupo["master"], "cor": grupo["cor"]}
            for grupo in GRUPOS
        ],
        "contagens": contagens(faixas),
        "blocos": blocos_json(faixas),
    }
    CLASSIFICACAO_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_html(faixas: list[Faixa]) -> str:
    payload = {
        "opcoes": OPCOES_GRUPO,
        "blocos": blocos_json(faixas),
        "contagens": contagens(faixas),
    }
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return HTML_TEMPLATE.replace("/*__DATA__*/", data)


def gravar_html(faixas: list[Faixa]) -> None:
    HTML_PATH.parent.mkdir(exist_ok=True)
    HTML_PATH.write_text(render_html(faixas), encoding="utf-8")


def carregar_correcoes() -> list[dict]:
    if not CORRECOES_PATH.exists():
        return []
    payload = json.loads(CORRECOES_PATH.read_text(encoding="utf-8"))
    correcoes = payload.get("correcoes", payload if isinstance(payload, list) else [])
    if not isinstance(correcoes, list):
        raise SystemExit("correcoes_grupos.json sem a lista 'correcoes'.")
    return correcoes


def aplicar(rpp_path: Path, correcoes: list[dict] | None = None) -> list[Faixa]:
    text = rpp_path.read_text(encoding="utf-8", errors="replace")
    faixas = listar_faixas(extract_track_blocks(text))
    if correcoes:
        faixas = aplicar_correcoes(faixas, correcoes)
    gravar_projeto(rpp_path, faixas)
    salvar_classificacao(faixas)
    gravar_html(faixas)
    totais = contagens(faixas)
    partes = [f"{totais[grupo['id']]} {grupo['nome']}" for grupo in GRUPOS]
    print(", ".join(partes))
    print(f"Fora: {totais['fora']}. Para revisar: {totais['revisar']}.")
    print(f"Relatório: {HTML_PATH}")
    return faixas


def servir(port: int = 8765) -> None:
    html_path = HTML_PATH
    correcoes_path = CORRECOES_PATH
    rpp_path = RPP

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print(fmt % args)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] not in ("/", "/grupos.html"):
                self.send_error(404)
                return
            body = html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/correcoes":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            correcoes_path.parent.mkdir(exist_ok=True)
            correcoes_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            aplicar(rpp_path, payload.get("correcoes", []))
            body = json.dumps({"ok": True, "arquivo": str(correcoes_path)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Relatório em http://127.0.0.1:{port}")
    server.serve_forever()


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grupos da Rota do Chopp</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 "Segoe UI", sans-serif; background: #f4f0e8; color: #241c14; }
  header { position: sticky; top: 0; z-index: 2; background: #241c14; color: #f4f0e8; padding: 16px 22px 14px; }
  header h1 { margin: 0 0 4px; font-size: 20px; font-weight: 650; }
  header p { margin: 0; color: #d9cfc2; font-size: 13px; }
  .totais { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .chip { border: 0; border-radius: 999px; padding: 4px 10px; color: white; font: inherit; font-size: 13px; cursor: pointer; }
  main { max-width: 980px; margin: 0 auto; padding: 18px 16px 80px; }
  section { background: white; border-radius: 12px; margin: 0 0 14px; overflow: hidden; box-shadow: 0 1px 0 rgba(36,28,20,.08); }
  h2 { margin: 0; padding: 12px 14px; font-size: 15px; background: #efe8dc; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px 14px; vertical-align: top; border-top: 1px solid #efe8dc; }
  th { font-size: 12px; letter-spacing: .04em; text-transform: uppercase; color: #6d6256; font-weight: 650; }
  .stem { font-weight: 650; }
  .full { display: block; color: #6d6256; font-size: 12px; font-weight: 400; }
  .motivo { color: #6d6256; font-size: 13px; }
  tr.revisar { background: #fff6e8; }
  .badge { display: inline-block; min-width: 74px; text-align: center; border-radius: 999px; padding: 2px 8px; color: white; font-size: 12px; font-weight: 650; }
  .botoes { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .botoes button { border: 1px solid #d9cfc2; background: white; border-radius: 999px; padding: 3px 8px; font: inherit; font-size: 12px; cursor: pointer; }
  .botoes button.ativo { color: white; border-color: transparent; }
  .mudar { border: 0; background: transparent; color: #6d6256; text-decoration: underline; cursor: pointer; font: inherit; font-size: 12px; padding: 0; }
  footer { position: sticky; bottom: 0; display: flex; gap: 12px; align-items: center; padding: 12px 16px; background: #fffaf3; border-top: 1px solid #e4d9c8; }
  footer button { background: #241c14; color: white; border: 0; border-radius: 8px; padding: 8px 14px; font: inherit; cursor: pointer; }
  #status { font-size: 13px; color: #6d6256; }
  .escondido { display: none; }
</style>
</head>
<body>
<header>
  <h1>Grupos por bloco</h1>
  <p>Seis faders, nesta ordem: Click, Regência, Metais, Teclas, Cordas, Percussão. O que não fecha em uma família fica de fora até você escolher.</p>
  <div class="totais" id="totais"></div>
</header>
<main id="lista"></main>
<footer>
  <button id="salvar" type="button">Salvar correções no projeto</button>
  <span id="status"></span>
</footer>
<script>
const DADOS = /*__DATA__*/;
const escolhido = new Map();
const abertos = new Set();

function cor(id) {
  const op = DADOS.opcoes.find((item) => item.id === id);
  return op ? op.cor : "#8a8175";
}
function nomeGrupo(id) {
  const op = DADOS.opcoes.find((item) => item.id === (id || "fora"));
  return op ? op.nome : "Fora";
}

function desenharTotais() {
  const host = document.getElementById("totais");
  host.innerHTML = "";
  for (const op of DADOS.opcoes) {
    if (op.id === "fora") continue;
    const n = DADOS.contagens[op.id] || 0;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.background = op.cor;
    chip.textContent = op.nome + " " + n;
    host.appendChild(chip);
  }
  const fora = document.createElement("span");
  fora.className = "chip";
  fora.style.background = "#8a8175";
  fora.textContent = "Sem naipe " + (DADOS.contagens.fora || 0);
  host.appendChild(fora);
  const rev = document.createElement("span");
  rev.className = "chip";
  rev.style.background = "#9a6b2f";
  rev.textContent = "Revisar " + (DADOS.contagens.revisar || 0);
  host.appendChild(rev);
}

function botoes(faixa, host) {
  const barra = document.createElement("div");
  barra.className = "botoes";
  const atual = escolhido.get(faixa.trilha) || faixa.grupo || "fora";
  for (const op of DADOS.opcoes) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.textContent = op.nome;
    if (op.id === atual) {
      botao.className = "ativo";
      botao.style.background = op.cor;
    }
    botao.addEventListener("click", () => {
      escolhido.set(faixa.trilha, op.id);
      desenhar();
    });
    barra.appendChild(botao);
  }
  host.appendChild(barra);
}

function desenhar() {
  const lista = document.getElementById("lista");
  lista.innerHTML = "";
  for (const bloco of DADOS.blocos) {
    const section = document.createElement("section");
    const titulo = document.createElement("h2");
    titulo.textContent = bloco.bloco;
    section.appendChild(titulo);
    const tabela = document.createElement("table");
    tabela.innerHTML = "<thead><tr><th>Trilha neste bloco</th><th>Grupo</th><th>Por quê</th></tr></thead>";
    const corpo = document.createElement("tbody");
    for (const faixa of bloco.faixas) {
      const tr = document.createElement("tr");
      if (faixa.revisar) tr.className = "revisar";
      const grupoId = escolhido.get(faixa.trilha) || faixa.grupo || "fora";
      const c1 = document.createElement("td");
      c1.innerHTML = '<span class="stem"></span><span class="full"></span>';
      c1.querySelector(".stem").textContent = faixa.stem;
      c1.querySelector(".full").textContent = faixa.trilha;
      const c2 = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.style.background = cor(grupoId);
      badge.textContent = nomeGrupo(grupoId);
      c2.appendChild(badge);
      const mostra = faixa.revisar || abertos.has(faixa.trilha);
      if (mostra) botoes(faixa, c2);
      else {
        const mudar = document.createElement("button");
        mudar.className = "mudar";
        mudar.type = "button";
        mudar.textContent = "mudar";
        mudar.addEventListener("click", () => {
          abertos.add(faixa.trilha);
          desenhar();
        });
        c2.appendChild(document.createElement("br"));
        c2.appendChild(mudar);
      }
      const c3 = document.createElement("td");
      c3.className = "motivo";
      c3.textContent = faixa.motivo;
      tr.append(c1, c2, c3);
      corpo.appendChild(tr);
    }
    tabela.appendChild(corpo);
    section.appendChild(tabela);
    lista.appendChild(section);
  }
}

function correcoes() {
  const lista = [];
  for (const bloco of DADOS.blocos) {
    for (const faixa of bloco.faixas) {
      const grupo = escolhido.get(faixa.trilha);
      const mudou = grupo && grupo !== (faixa.grupo || "fora");
      if (!faixa.revisar && !mudou) continue;
      lista.push({
        trilha: faixa.trilha,
        bloco: faixa.bloco,
        stem: faixa.stem,
        grupo: grupo || faixa.grupo || "fora",
        antes: faixa.grupo
      });
    }
  }
  return { atualizado: new Date().toISOString(), correcoes: lista };
}

async function salvar() {
  const status = document.getElementById("status");
  const payload = correcoes();
  const texto = JSON.stringify(payload, null, 2);
  try {
    const resposta = await fetch("/correcoes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: texto
    });
    if (!resposta.ok) throw new Error(await resposta.text());
    status.textContent = "Arquivo gravado e projeto atualizado. Recarregando.";
    setTimeout(() => location.reload(), 400);
    return;
  } catch (erro) {
    const blob = new Blob([texto], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "correcoes_grupos.json";
    link.click();
    status.textContent = "Baixou correcoes_grupos.json. Coloque em data/ e rode o script de aplicar.";
  }
}

desenharTotais();
desenhar();
document.getElementById("salvar").addEventListener("click", salvar);
</script>
</body>
</html>
"""


def main() -> None:
    comando = sys.argv[1] if len(sys.argv) > 1 else "aplicar"
    if comando == "servir":
        if not HTML_PATH.exists():
            aplicar(RPP, carregar_correcoes())
        servir()
        return
    if comando == "aplicar":
        aplicar(RPP, carregar_correcoes())
        return
    raise SystemExit("Uso: python scripts/grupos_tracks.py [aplicar|servir]")


if __name__ == "__main__":
    main()
