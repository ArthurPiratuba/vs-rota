#!/usr/bin/env python3
"""Qual projeto Reaper os scripts editam.

Cada projeto e uma pasta com um projeto.json ao lado do .rpp, e tudo dele
fica la dentro: ABERTO, MONITOR, data, relatorio, Backups, EXPORT_*. Os
scripts sao um so, na pasta scripts/ da raiz; copiar a pasta de um projeto
basta para leva-lo.

    {"nome": "VS - ROTA", "rpp": "VS - ROTA.rpp", "pptx": "REPERTÓRIO/Repertório Rota do Chopp VS.pptx", "slide_do_bloco_1": 2}

"pptx" e o repertorio dos blocos VS, relativo a pasta do projeto ou absoluto;
o PDF fica ao lado, com o mesmo nome. "slide_do_bloco_1" (padrao 1) e o slide
do VS BLOCO 1; os outros seguem em ordem. Com capa no slide 1, use 2.

O projeto vem, nesta ordem, de:
- --projeto <nome ou pasta> na linha de comando (tirado do sys.argv aqui);
- a variavel VS_PROJETO (os subprocessos herdam o projeto por ela);
- a pasta atual, ou uma acima dela, com projeto.json.
O nome e o de uma pasta da raiz: --projeto "VS - LAB".
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = "projeto.json"


def projetos() -> list[Path]:
    return sorted(p.parent for p in RAIZ.glob(f"*/{CONFIG}"))


def _tirar_do_argv() -> str | None:
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg == "--projeto" and i + 1 < len(sys.argv):
            valor = sys.argv[i + 1]
            del sys.argv[i : i + 2]
            return valor
        if arg.startswith("--projeto="):
            del sys.argv[i]
            return arg.split("=", 1)[1]
    return None


def _achar(valor: str) -> Path:
    for candidato in (Path(valor), RAIZ / valor):
        if (candidato / CONFIG).is_file():
            return candidato.resolve()
    raise SystemExit(f"Projeto nao encontrado: {valor}. Projetos: {', '.join(p.name for p in projetos())}")


def _resolver() -> Path:
    valor = _tirar_do_argv() or os.environ.get("VS_PROJETO")
    if valor:
        return _achar(valor)
    for pasta in (Path.cwd(), *Path.cwd().parents):
        if (pasta / CONFIG).is_file():
            return pasta.resolve()
    raise SystemExit(
        "Diga o projeto: --projeto <nome> (ou VS_PROJETO, ou rode de dentro da pasta dele). "
        f"Projetos: {', '.join(p.name for p in projetos())}"
    )


WORKSPACE = _resolver()
os.environ["VS_PROJETO"] = str(WORKSPACE)
_config = json.loads((WORKSPACE / CONFIG).read_text(encoding="utf-8"))
NOME: str = _config["nome"]
RPP = WORKSPACE / _config["rpp"]
PPTX = (WORKSPACE / _config["pptx"]).resolve()
SLIDE_DO_BLOCO_1 = int(_config.get("slide_do_bloco_1", 1))
