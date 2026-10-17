#!/usr/bin/env python3
"""Monta o repertorio completo: todos os slides de outro repertorio (o ao vivo,
sem VS) e depois os slides VS do projeto, com a capa deles onde comecam os
blocos VS. Com "capa" no projeto.json, essa imagem ocupa o slide 1, no lugar
da capa do outro repertorio.

O que entra vem do "completo" do projeto.json (veja scripts/projeto.py). O
PowerPoint copia os slides (COM via pwsh) e grava o .pptx e o .pdf completos;
os dois repertorios de origem so sao lidos. O adicionar_bloco.py, o
remover_bloco.py e o adicionar_musicas_bloco.py chamam isto sempre que refazem
o PDF dos VS.

    python scripts/montar_repertorio_completo.py --projeto "VS - ROTA"
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import projeto  # noqa: E402


def contar_slides(pptx: Path) -> int:
    with zipfile.ZipFile(pptx) as z:
        return sum(1 for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))


def montar_completo(antes: Path, vs: Path, destino: Path, capa: Path | None = None) -> int:
    """Grava destino (.pptx) e o .pdf ao lado; devolve o numero de slides."""
    n_antes = contar_slides(antes)
    n_vs = contar_slides(vs)
    if capa is not None and not capa.exists():
        raise SystemExit(f"Capa do completo nao encontrada: {capa}")
    # A capa nova entra como slide 1, em branco com a imagem na pagina toda, e a antiga sai.
    trocar_capa = (
        "$s=$p.Slides.Add(1,12);"
        f"$null=$s.Shapes.AddPicture('{capa}',0,-1,0,0,$p.PageSetup.SlideWidth,$p.PageSetup.SlideHeight);"
        "$p.Slides(2).Delete();"
        if capa is not None
        else ""
    )
    pdf = destino.with_suffix(".pdf")
    tmp_pptx = destino.with_name(destino.stem + ".novo.pptx")
    tmp_pdf = destino.with_name(destino.stem + ".novo.pdf")
    for t in (tmp_pptx, tmp_pdf):
        t.unlink(missing_ok=True)
    # Abre o de "antes" so para leitura e grava como outro arquivo: a origem nao muda.
    script = (
        "$ErrorActionPreference='Stop';"
        "$app=New-Object -ComObject PowerPoint.Application;"
        f"$p=$app.Presentations.Open('{antes}',-1,0,0);"
        "try{"
        f"$null=$p.Slides.InsertFromFile('{vs}',{n_antes},1,{n_vs});"
        f"{trocar_capa}"
        f"$p.SaveAs('{tmp_pptx}',24);"
        f"$p.SaveCopyAs('{tmp_pdf}',32)"
        "}finally{$p.Close()};"
        "if($app.Presentations.Count -eq 0){$app.Quit()}"
    )
    shell = shutil.which("pwsh") or "powershell"
    subprocess.run([shell, "-NoProfile", "-Command", script], check=True)
    esperado = n_antes + n_vs
    if not tmp_pptx.exists() or contar_slides(tmp_pptx) != esperado:
        raise SystemExit(f"PowerPoint nao gerou o completo com {esperado} slides.")
    if not tmp_pdf.exists() or tmp_pdf.stat().st_size == 0:
        raise SystemExit("PowerPoint nao gerou o PDF completo.")
    tmp_pptx.replace(destino)
    tmp_pdf.replace(pdf)
    return esperado


def atualizar_completo() -> None:
    """Refaz o completo do projeto, se ele tiver um."""
    if projeto.COMPLETO is None:
        return
    n = montar_completo(projeto.ANTES_DO_COMPLETO, projeto.PPTX, projeto.COMPLETO, projeto.CAPA_DO_COMPLETO)
    print(f"Completo ({n} slides): {projeto.COMPLETO} e .pdf")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if projeto.COMPLETO is None:
        raise SystemExit(f"{projeto.NOME} nao tem \"completo\" no projeto.json.")
    atualizar_completo()


if __name__ == "__main__":
    main()
