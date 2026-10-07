---
name: add-music
description: Acrescenta musicas na descricao de um bloco VS do projeto Reaper ROTA DO CHOPP UNIFICADO (ex. "24 essa cama nao vendo"). Use quando o usuario disser o numero de um bloco e as musicas que faltam nele, ou pedir para adicionar/completar musicas no nome, region, cartaz ou repertorio de um bloco.
---

# add-music

Completa o nome de um bloco com as musicas que faltam. O nome que ja esta no
bloco e a primeira musica; as novas entram depois, na ordem dada:
`ALÔ GALERA` + "essa cama eu nao vendo" -> `ALÔ GALERA - ESSA CAMA EU NÃO VENDO`.

So muda o nome. Nao mexa em tempo, item, volume, cor, grupo, FX nem no audio.

## Onde o nome do bloco aparece (tudo e atualizado pelo script)

| Lugar | Exemplo |
|---|---|
| Region (`MARKER N`) no `.rpp` | `"ALÔ GALERA - ESSA CAMA EU NÃO VENDO"` |
| Tracks no `.rpp` | `24 - ALÔ GALERA...`, `24 - CLICK - ALÔ GALERA...` |
| Caminhos `FILE` e a pasta | `ABERTO\24 - ALÔ GALERA - ESSA CAMA EU NÃO VENDO\CLICK.mp3` |
| `data/region_markers.json`, `data/grupos_classificacao.json`, `relatorio/grupos.html` | mesmos nomes |
| Slide do bloco no PowerPoint (`PPTX` em `scripts/inserir_texto_monitor.py`, slide 63+N) | linha nova `Essa Cama Eu Não Vendo ??` (nome branco, tom vermelho) |
| PDF do repertorio (`Repertório Rota do Chopp.pdf`, ao lado do `.pptx`) | exportado de novo pelo PowerPoint (COM via `pwsh`), todas as paginas |
| Cartaz da track `NN - MONITOR` (codigo do Video processor) | gerado de novo a partir do slide |
| `EXPORT_REGIONS/NN - <nome>.mp3` | renomeado; se nao existir, exportado **em mono** (`scripts/exportar_regions_mp3.py`, sempre mono, 320 kbps) |
| `EXPORT_VIDEOS/NN - <nome>.mp4` e `_cartaz/NN.png` | refeitos, o cartaz fica gravado no video |

Os audios dentro da pasta (CLICK.mp3, GUIA.mp3...) nao levam o nome do bloco e
nao sao renomeados. `scripts/adicionar_blocos_zip.py` guarda os nomes originais
da importacao e fica como esta.

## Grafia (blocos 1 a 23 sao a referencia)

- Musica que ja existe em 1-23: o script usa o mesmo nome do projeto e o mesmo
  texto do slide. Ex.: "misturinha" -> projeto `MISTURINHA`, slide
  `Misturinha` (bloco 11). Basta passar o nome como o usuario escreveu.
- Musica nova: passe com acento certo, ex. `"VOCÊ NÃO VALE NADA"`. No projeto
  fica em maiusculas; no slide vira Titulo com `e/o/a/de/da/do/na/no...` minusculos.
  Para outro texto no slide: `"NOME|Texto do Slide"`.
- Tom: `"NOME@Mib"`. Sem tom fica `??`, como nos blocos 24-32. Nao invente tom.
- Separador do nome e ` - `; um nome de musica nao pode conter ` - `.

## Passos

1. Confirme com o usuario a lista por bloco, ja normalizada (acentos, grafia
   da referencia), antes de gravar.
2. Peca para **fechar o Reaper** e o **`.pptx` no PowerPoint**. Com o Reaper
   aberto o script nao grava: ele regravaria o `.rpp` antigo e prende os mp3
   da pasta. Com o `.pptx` aberto o PowerPoint trava o arquivo e o script para
   logo no inicio, ate no plano (nem sempre existe o `~$Repertório...pptx`).
3. Para cada bloco, rode o plano e confira:
   ```
   python -X utf8 scripts/adicionar_musicas_bloco.py 24 "essa cama nao vendo" "OUTRA@Re"
   ```
   Esperado para um bloco com K trilhas de audio: 1 region, K+1 tracks (a pasta
   tambem), K FILE, 1 cartaz.
4. Aplique: o mesmo comando com `--aplicar`. Ele faz backup do `.rpp` em
   `Backups/` e do `.pptx` e do `.pdf` ao lado deles (`-AAAA-MM-DD_HHMMSS.*.bak`),
   renomeia a pasta, grava tudo, exporta o PDF e refaz o mp4 do bloco. `--sem-video` pula o mp4 (rode depois
   `python scripts/exportar_blocos_video.py --from-order N --to-order N`).
5. Rodar de novo no mesmo bloco acrescenta mais musicas; musica que ja esta no
   bloco e recusada.

## Renomear uma musica em todos os blocos

Quando o usuario corrigir o nome de uma musica ("ajuste para Essa cama eu nao
vendo em todos os blocos"), use `--trocar` sem numero de bloco. Ele acha cada
bloco cujo nome tem a musica (inclusive os de referencia 1-23), troca so aquele
trecho do nome e o texto branco da linha no slide (o tom fica), e grava tudo
numa passada: um backup, um PDF, um mp4 por bloco afetado.

```
python -X utf8 scripts/adicionar_musicas_bloco.py --trocar "CAMA NÃO VENDO=ESSA CAMA EU NÃO VENDO"
python -X utf8 scripts/adicionar_musicas_bloco.py --trocar "CAMA NÃO VENDO=ESSA CAMA EU NÃO VENDO" --aplicar
```

- O lado esquerdo e o nome como esta no projeto (sem acento tambem acha).
- O texto do slide sai em Titulo (`Essa Cama Eu Não Vendo`); outro texto:
  `"VELHO=NOVO|Texto do Slide"`. Um numero antes (`24 --trocar ...`) limita a
  um bloco.
- Confira no plano a lista de blocos afetados e o slide de cada um antes de
  aplicar.

## Verificacao

- `grep -c '"<nome antigo>"'` no `.rpp` com o prefixo do bloco deve dar 0, e
  `git diff --stat` deve mostrar so o `.rpp`, os dois JSON, o html e a pasta.
- `git diff` do `.rpp`: so linhas `MARKER N`, `NAME`, `FILE` e o `<CODE>` da
  track `NN - MONITOR` do bloco.
- Leia o slide de volta (`linhas_do_slide`) e confira o mp4 novo em `EXPORT_VIDEOS`.
- O PDF novo tem o mesmo numero de paginas do anterior (hoje 95) e a pagina do
  bloco mostra a musica nova. O Windows PowerShell 5 nao acessa o PowerPoint
  pelo COM nesta maquina; o script usa `pwsh`.
