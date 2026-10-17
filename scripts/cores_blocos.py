#!/usr/bin/env python3
"""Recolore os blocos com as cores do adicionar_bloco.py.

Loops e clicks ficam sempre com a cor do tipo (vaneira, marcha, click). Blocos de
musica so mudam a partir de --de, com a paleta variada (sem vermelho). Muda a cor
da region, da track-pasta e das tracks de audio que tinham a cor da region; click,
regencia (vermelhos) e monitor (amarelo) ficam como estao. Atualiza tambem
data/region_markers.json.

Sem --aplicar so mostra o plano. Com o Reaper aberto nao grava.

    python scripts/cores_blocos.py --de 24
    python scripts/cores_blocos.py --de 24 --aplicar
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from adicionar_bloco import (  # noqa: E402
    MARKER_INICIO,
    REGION_MARKERS,
    RPP,
    TRACK_DO_BLOCO,
    cor_do_bloco,
    cores_das_regions,
    regions,
)
from adicionar_musicas_bloco import reaper_aberto  # noqa: E402
from backup_rpp import backup_rpp  # noqa: E402
from inserir_texto_monitor import sem_repertorio  # noqa: E402
from unificar_reaper import extract_track_blocks, replace_all_track_blocks  # noqa: E402


def hexa(cor: int) -> str:
    cor &= 0xFFFFFF
    return f"#{cor & 255:02x}{(cor >> 8) & 255:02x}{cor >> 16:02x}"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Recolore blocos de musica (a partir de --de) e loops/clicks.")
    parser.add_argument("--de", type=int, help="primeiro bloco de musica a recolorir (sem isso, so loops e clicks)")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isso so mostra o plano")
    args = parser.parse_args()

    texto = RPP.read_text(encoding="utf-8")
    regs = regions(texto)
    cores = cores_das_regions(texto)
    trocas: dict[int, tuple[int, int]] = {}
    for n, reg in sorted(regs.items()):
        if not sem_repertorio(reg["nome"]) and (args.de is None or n < args.de):
            continue
        nova = cor_do_bloco(n, reg["nome"], regs, cores)
        if nova != cores[n]:
            trocas[n] = (cores[n], nova)
            print(f"  {n:2d} {hexa(cores[n])} -> {hexa(nova)}  {reg['nome']}")
    if not trocas:
        print("Nada a mudar.")
        return

    def marker(m: re.Match) -> str:
        n = int(m.group(2))
        if n not in trocas or not m.group(3):
            return m.group(0)
        return f"{m.group(1)}{trocas[n][1]}{m.group(5)}"

    novo = MARKER_INICIO.sub(marker, texto)
    tracks = []
    mudadas = 0
    for track in extract_track_blocks(novo):
        m = TRACK_DO_BLOCO.search(track)
        n = int(m.group(1)) if m else 0
        if n in trocas:
            velha, nova = trocas[n]
            track, k = re.subn(rf"^    PEAKCOL {velha}$", f"    PEAKCOL {nova}", track, count=1, flags=re.MULTILINE)
            mudadas += k
        tracks.append(track)
    novo = replace_all_track_blocks(novo, tracks)
    if cores_das_regions(novo) != {**cores, **{n: c[1] for n, c in trocas.items()}}:
        raise SystemExit("Cores das regions nao ficaram como o plano.")
    print(f"{len(trocas)} regions e {mudadas} tracks")

    if not args.aplicar:
        print("Plano apenas. Rode com --aplicar para gravar.")
        return
    if reaper_aberto():
        raise SystemExit("Feche o Reaper antes: ele regravaria o .rpp antigo.")
    print(f"Backup .rpp: {backup_rpp(RPP)}")
    RPP.write_text(novo, encoding="utf-8")
    marcadores = json.loads(REGION_MARKERS.read_text(encoding="utf-8"))
    for item in marcadores["starts"]:
        if item["index"] in trocas:
            velha, nova = trocas[item["index"]]
            item["tail"] = re.sub(rf"^(\d+) {velha} ", rf"\g<1> {nova} ", item["tail"])
    REGION_MARKERS.write_text(json.dumps(marcadores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Pronto.")


if __name__ == "__main__":
    main()
