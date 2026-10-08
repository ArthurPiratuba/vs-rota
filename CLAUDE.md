# VS (ROTA DO CHOPP)

Projetos Reaper com os VS (playbacks) do baile, um bloco por region. Os
scripts em `scripts/` editam o `.rpp` como texto e servem a todos os projetos;
cada projeto é uma pasta com tudo o que é dele:

| Projeto | Pasta | Repertório (pptx/pdf dos VS) |
|---|---|---|
| Show | `VS - ROTA/VS - ROTA.rpp` | `O:\OneDrive\Trabalho\Banda\Rota do Chopp\REPERTÓRIO\Repertório Rota do Chopp VS.pptx` |
| Laboratório | `VS - LAB/VS - LAB.rpp` | `VS - LAB/REPERTÓRIO/Repertório VS LAB.pptx` |

Dentro de cada pasta: `projeto.json` (nome, `.rpp` e `pptx`), `ABERTO`,
`MONITOR`, `data`, `relatorio`, `Backups`, `EXPORT_REGIONS`, `EXPORT_VIDEOS`.
Nenhum projeto lê nada do outro: copiar a pasta basta para levá-lo, e um projeto
novo nasce de uma cópia. Os caminhos no `.rpp` são relativos à pasta.

Rode tudo com `python -X utf8` e diga o projeto com `--projeto "VS - LAB"`
(nome de pasta da raiz ou caminho), ou rode de dentro da pasta dele
(`scripts/projeto.py`). Os exemplos abaixo valem para os dois.

## Antes de gravar

- Feche o **Reaper**: aberto, ele regrava o `.rpp` antigo e prende os mp3.
- Feche o `.pptx` de repertório do projeto no PowerPoint: aberto, o arquivo fica travado.
- Todo script mostra o plano sem `--aplicar` e só grava com ele. Confira o
  plano com o usuário antes de aplicar.
- Backups: `.rpp` em `<projeto>/Backups/`; `.pptx` e `.pdf` ao lado deles, como
  `-AAAA-MM-DD_HHMMSS.*.bak`.

## Repertório (PowerPoint)

- O `pptx` do `projeto.json` tem os blocos VS, um slide por bloco, na ordem,
  a partir do slide `slide_do_bloco_1` (padrão 1). É o único que os scripts editam;
  o PDF fica ao lado.
- No ROTA o slide 1 é a capa (`EXPORT_VIDEOS/THUMB PLAYLIST.jpg`, a foto da
  playlist) e o VS BLOCO N é o slide N+1 (`"slide_do_bloco_1": 2`). No LAB não há capa.
- `Repertório Rota do Chopp.pptx` / `.pdf` em `O:\OneDrive\...\REPERTÓRIO`: os
  blocos sem VS. **Nunca abra, edite, exporte nem faça backup dele**; é do usuário.
  A pasta `REPERTÓRIO/` da raiz é uma cópia antiga; os scripts não a usam.

## Convenções de um bloco

| Onde | Convenção |
|---|---|
| Número | Sequencial. Bloco novo sempre **no fim**, nunca no meio. |
| Region | `MARKER N` com o nome do bloco, 2 s depois do fim do anterior (o bloco 1 em 0), cor `region_color`. Entra também na playlist S&M e em `data/region_markers.json`. |
| Nome do bloco | Músicas em maiúsculas com acento, separadas por ` - ` (`ALÔ GALERA - ESSA CAMA EU NÃO VENDO`). Loop leva o BPM: `LOOP DE VANEIRA 82 BPM`. |
| Pasta | `ABERTO\N - <nome>`, com os mp3 do bloco. |
| Áudios | Nome em maiúsculas, sem a palavra BLOCO (`GAITA` vira `SANFONA`, `GUITA` vira `GUITARRA`, `BATERA` vira `BATERIA`). O click leva o BPM quando se sabe: `CLICK 82.mp3`. Contagem e regência separadas viram um só `REGÊNCIA E CONTAGEM.mp3`. |
| Tracks | Pasta `NN - <nome>` (recolhida), depois `NN - MONITOR`, depois click, regência e o resto em ordem alfabética. Nomes: `NN - CLICK 82 - <nome>`, `NN - REGÊNCIA - <nome>`, `NN - <STEM> - <nome>`. |
| Pan | **Click e regência sempre no L**; todo o resto no R. Click a -5 dB, o resto a 0 dB. |
| Cores | Click e regência vermelhos; monitor amarelo; o resto com a cor da region. |
| Grupos | Faders no início do projeto: CLICK, REGÊNCIA, METAIS, TECLAS, CORDAS, PERCUSSÃO. Cada trilha vai a um grupo pelo nome do stem (`scripts/grupos_tracks.py`) e não vai direto ao master. Voz, guia, VS e backing ficam fora. Ajustes manuais em `data/correcoes_grupos.json`. |
| Mute | Guia com o nome da música fica mutada quando o usuário pede; trilha mutada não entra nos exports. |
| Monitor | Item de vídeo `MONITOR\base.png` com o cartaz no Video processor, gerado do slide. Começa no fim da region anterior (o bloco 1 em 0) e termina 1 ms antes do fim da sua (o último vai até o fim). |
| Slide | `VS BLOCO N` (vermelho) e uma linha por música: nome em branco e tom em vermelho (`??` quando não se sabe; não invente tom). Em loop o BPM vai no lugar do tom. |
| PDF | Ao lado do `.pptx`, exportado pelo PowerPoint (COM via `pwsh`). |
| Exports | `EXPORT_REGIONS/NN - <nome>.mp3` em **mono**, 320 kbps; `EXPORT_VIDEOS/NN - <nome>.mp4` com o cartaz. |

Grafia no slide: Título com `e/o/a/de/da/do/na/no/em...` minúsculos. Música que
já existe nos blocos 1-23 do projeto usa o mesmo nome e o mesmo texto de slide de lá.

## Fluxos

### Bloco novo

Coloque os mp3 do bloco na pasta do projeto (ou noutra pasta, com `--origem`) e:

```
python -X utf8 scripts/adicionar_bloco.py --projeto "VS - ROTA" "NOME|Texto do Slide@Tom" [outra música...] --bpm 82
python -X utf8 scripts/adicionar_bloco.py --projeto "VS - ROTA" "NOME|Texto do Slide@Tom" --bpm 82 --aplicar
```

Ele cria a pasta, a region, as tracks, o monitor com cartaz, refaz o
roteamento dos grupos, acrescenta o slide no fim do `.pptx`, exporta o PDF, o
mp3 mono e o mp4 do bloco, e apaga os mp3 de entrada (`--manter-origem` para
não apagar). Precisa de exatamente 1 click; regência é opcional. Para mutar uma
trilha, aplique com `--sem-video`, troque `MUTESOLO 0 0 0` por `MUTESOLO 1 0 0`
na track e refaça os dois exports do bloco.

### Tirar o último bloco

```
python -X utf8 scripts/remover_bloco.py --projeto "VS - ROTA" N [--aplicar]
```

Tira region, playlist, tracks, slide, pasta e exports do bloco N (só o último)
e devolve ao anterior o cartaz até o fim. Para levar o bloco a outro projeto,
copie `ABERTO\N - <nome>` antes e use-a como `--origem` no `adicionar_bloco.py`
de lá (com `--manter-origem`).

### Músicas de um bloco

`/add-music` (`.claude/skills/add-music`): acrescenta músicas ao nome de um bloco
(`scripts/adicionar_musicas_bloco.py N "música@Tom"`) ou renomeia uma música em
todos os blocos (`--trocar "VELHO=NOVO"`).

### Exports

```
python -X utf8 scripts/exportar_regions_mp3.py --projeto "VS - ROTA" --mono [--from-order N --to-order N]
python -X utf8 scripts/exportar_blocos_video.py --projeto "VS - ROTA" [--from-order N --to-order N]
```

### Grupos

`python -X utf8 scripts/grupos_tracks.py --projeto "VS - ROTA" aplicar` refaz receives,
`GROUP_FLAGS` e `MAINSEND` de todo o projeto; num projeto já certo não muda nada.
`servir` abre o relatório para corrigir a classificação.

## Conferir depois

- `git diff` do `.rpp`: só o que o fluxo prometeu (no bloco novo, também o fim do
  cartaz do bloco que era o último).
- Slide lido de volta com `linhas_do_slide`; PDF com uma página por bloco (mais a capa, no ROTA).
- mp3 em mono (`ffmpeg -i` mostra `mono`; o ffmpeg é o do `imageio_ffmpeg`) e mp4 em `EXPORT_VIDEOS`.
- `EXPORT_REGIONS` e `EXPORT_VIDEOS` ficam fora do git.
