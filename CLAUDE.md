# CLAUDE.md — mia_telegram

Orientação rápida pra qualquer sessão do Claude Code que abrir este repo.
Detalhes completos ficam no `README.md` — leia ele antes de qualquer
mudança maior.

## O que é

Pipeline diário de sinais de opções (**Projeto MIA**) pra ~95 tickers B3.
3 estágios encadeados, cada um consumindo modelo já treinado (não treina
nada aqui — isso é caro, roda num repo principal privado):

1. **Direção** (`modelos_direction/`) — decide CALL/PUT/Neutro.
2. **Volatilidade** (`modelos_volatility/`) — decide se a expansão de vol
   prevista compensa comprar opção.
3. **Precificação** (`options_advisor.py`) — escolhe a estrutura
   (strike/custo) dado que os dois gates acima liberaram.

`executor.py` roda os 3 estágios, `realizados.py` cruza sinais passados
com o preço 20 pregões depois (horizonte **fixo**, diferente dos sistemas
position-based como Hilo/OMQS) e separa a métrica de cada estágio — um
`acerto_direcao` ruim não dizia se a culpa era da direção, da vol ou do
preço da opção, então não dá pra julgar o pipeline só por ele.

## Automação — 2 workflows

- `.github/workflows/mia_diario.yml` — 09:00 e 18:30 BRT (seg-sex):
  `download_database.py` → `executor.py` (`SKIP_DOWNLOAD=1`) →
  `historico_runs.py` → commit/push → `telegram_notify.py executor`.
- `.github/workflows/mia_realizados.yml` — só disparo manual: roda
  `realizados.py`, commita `saidas/realizados/*.xlsx`, manda resumo de
  performance no Telegram.

## Incidente 26/08: cron do GitHub Actions sumiu (não é bug daqui)

`mia_diario.yml` (18:30 BRT) não disparou em 26/08 — não atrasou, não
falhou, sumiu da fila do `on: schedule` inteiro (`gh run list` sem run pro
dia). É por isso que a notificação diária no Telegram não chegou naquele
dia. Backfillado via `workflow_dispatch` manual ~21:30 BRT do mesmo dia —
mas antes de rodar `realizados.py`/`git pull` depois de um backfill assim,
cuidado com arquivo local sujo: `log_projeto_executor_v5_5.txt` é
regravado a cada run local e pode conflitar com o commit do workflow no
`git pull` (`git checkout -- log_projeto_executor_v5_5.txt` antes de
puxar). **Mesmo incidente em pelo menos +4 workflows de outros repos do
usuário na mesma janela ~21:30-22:11 UTC** (hilo, api_OMQS,
api_OMQS_futuros, acoes_fundamentalista) — forte indício de falha da fila
do GitHub Actions, não bug de código. Se os números do card "MIA"
parecerem defasados ou o Telegram não chegar, checar `gh run list` por um
dia útil inteiro ausente antes de investigar código.

## `omqs_boxplot/` aqui é cópia obsoleta — não usar

Existe uma pasta `omqs_boxplot/` neste repo (script gerador do box plot do
card OMQS do Painel de Sinais), mas ela **saiu de uso em 26/08**: o
utilitário foi copiado pra dentro do `api_OMQS` (onde os dados que ele lê
realmente moram — `saidas/resultados/*.xlsx` + `memoria_ordens.csv` são
do `api_OMQS`, não deste repo). A cópia aqui não foi apagada (ficou
esquecida), mas a versão canônica agora é
`api_OMQS/omqs_boxplot/gerar_boxplot_omqs.py`. Não editar a cópia daqui.

## Sobre ser público

Repo público de propósito (roda de graça no GitHub Actions — repo privado
tem cota limitada). Modelos treinados (`.pkl`) e histórico de sinais ficam
visíveis; nenhuma credencial real fica no código, só nos secrets do
GitHub. Ver `README.md`, seção "Nota sobre ser público".

## Projeto Ações Fundamentalista — mudou de repo

Não fica mais aqui. Virou repo próprio em 25/08/2026:
`github.com/fpinto0416/acoes_fundamentalista` (nasceu como subpasta deste
repo, código idêntico, só mudou o endereço). Histórico anterior à mudança
continua acessível via `git log -- acoes_fundamentalista`.

## Painel de Sinais (artefato compartilhado)

Os números de `realizados.py` (aba `agregados`/`agregados_magnitude` do
xlsx, mais o bloco impresso no console "Detalhamento por estágio")
alimentam à mão o card "MIA" do artefato **Painel de Sinais**
(`https://claude.ai/code/artifact/ac976eca-35a4-4ff2-ba69-67b1863c29c9`),
que também agrega hilo, api_OMQS (diário + 4h), opcoes-sinal-diario e
api_OMQS_futuros. É HTML estático — os números são colados à mão a cada
atualização, seguindo o comentário HTML antes do card.

## Estrutura

Ver `README.md`, seção "Arquivos", pra lista completa.
