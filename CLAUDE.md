# ROTA DO CHOPP UNIFICADO

Projeto Reaper com os VS (playbacks) do baile, um bloco por region.
`ROTA DO CHOPP UNIFICADO.rpp` é a fonte; os scripts em `scripts/` o editam
como texto. Rode tudo com `python -X utf8` a partir da raiz.

## Antes de gravar

- Feche o **Reaper**: aberto, ele regrava o `.rpp` antigo e prende os mp3.
- Feche o **`.pptx`** do repertório no PowerPoint: aberto, o arquivo fica travado.
- Todo script mostra o plano sem `--aplicar` e só grava com ele. Confira o
  plano com o usuário antes de aplicar.
- Backups: `.rpp` em `Backups/`; `.pptx` e `.pdf` ao lado deles, como
  `-AAAA-MM-DD_HHMMSS.*.bak`.

## Convenções de um bloco

| Onde | Convenção |
|---|---|
| Número | Sequencial. Bloco novo sempre **no fim**, nunca no meio. |
| Region | `MARKER N` com o nome do bloco, 2 s depois do fim do anterior, cor `region_color`. Entra também na playlist S&M e em `data/region_markers.json`. |
| Nome do bloco | Músicas em maiúsculas com acento, separadas por ` - ` (`ALÔ GALERA - ESSA CAMA EU NÃO VENDO`). |
| Pasta | `ABERTO\N - <nome>`, com os mp3 do bloco. |
| Áudios | Nome em maiúsculas, sem a palavra BLOCO (`GAITA` vira `SANFONA`). O click leva o BPM quando se sabe: `CLICK 82.mp3`. |
| Tracks | Pasta `NN - <nome>` (recolhida), depois `NN - MONITOR`, depois click, regência e o resto em ordem alfabética. Nomes: `NN - CLICK 82 - <nome>`, `NN - REGÊNCIA - <nome>`, `NN - <STEM> - <nome>`. |
| Pan | **Click e regência sempre no L**; todo o resto no R. Click a -5 dB, o resto a 0 dB. |
| Cores | Click e regência vermelhos; monitor amarelo; o resto com a cor da region. |
| Grupos | Faders no início do projeto: CLICK, REGÊNCIA, METAIS, TECLAS, CORDAS, PERCUSSÃO. Cada trilha vai a um grupo pelo nome do stem (`scripts/grupos_tracks.py`) e não vai direto ao master. Voz, guia, VS e backing ficam fora. Ajustes manuais em `data/correcoes_grupos.json`. |
| Monitor | Item de vídeo `MONITOR\base.png` com o cartaz no Video processor, gerado do slide. Começa no fim da region anterior e termina 1 ms antes do fim da sua (o último vai até o fim). |
| Slide | `VS BLOCO N` (vermelho) e uma linha por música: nome em branco e tom em vermelho (`??` quando não se sabe; não invente tom). O slide do bloco N é o slide 63+N do `.pptx` (`PPTX` em `scripts/inserir_texto_monitor.py`). |
| PDF | `Repertório Rota do Chopp.pdf`, ao lado do `.pptx`, exportado pelo PowerPoint (COM via `pwsh`). |
| Exports | `EXPORT_REGIONS/NN - <nome>.mp3` em **mono**, 320 kbps; `EXPORT_VIDEOS/NN - <nome>.mp4` com o cartaz. |

Grafia no slide: Título com `e/o/a/de/da/do/na/no/em...` minúsculos. Música que
já existe nos blocos 1-23 usa o mesmo nome e o mesmo texto de slide de lá.

## Fluxos

### Bloco novo

Coloque os mp3 do bloco na raiz do projeto (ou numa pasta, com `--origem`) e:

```
python -X utf8 scripts/adicionar_bloco.py "NOME|Texto do Slide@Tom" [outra música...] --bpm 82
python -X utf8 scripts/adicionar_bloco.py "NOME|Texto do Slide@Tom" --bpm 82 --aplicar
```

Ele cria a pasta, a region, as tracks, o monitor com cartaz, refaz o
roteamento dos grupos, acrescenta o slide no fim do `.pptx`, exporta o PDF, o
mp3 mono e o mp4 do bloco, e apaga os mp3 de entrada (`--manter-origem` para
não apagar). Precisa de exatamente 1 click; regência é opcional.

### Músicas de um bloco

`/add-music` (`.claude/skills/add-music`): acrescenta músicas ao nome de um bloco
(`scripts/adicionar_musicas_bloco.py N "música@Tom"`) ou renomeia uma música em
todos os blocos (`--trocar "VELHO=NOVO"`).

### Exports

```
python -X utf8 scripts/exportar_regions_mp3.py --mono [--from-order N --to-order N]
python -X utf8 scripts/exportar_blocos_video.py [--from-order N --to-order N]
```

### Grupos

`python -X utf8 scripts/grupos_tracks.py aplicar` refaz receives, `GROUP_FLAGS`
e `MAINSEND` de todo o projeto; num projeto já certo não muda nada.
`servir` abre o relatório para corrigir a classificação.

## Conferir depois

- `git diff` do `.rpp`: só o que o fluxo prometeu (no bloco novo, também o fim do
  cartaz do bloco que era o último).
- Slide lido de volta com `linhas_do_slide`; PDF com uma página a mais por bloco novo.
- mp3 em mono (`ffmpeg -i` mostra `mono`) e mp4 em `EXPORT_VIDEOS`.
- `EXPORT_REGIONS` e `EXPORT_VIDEOS` ficam fora do git.
