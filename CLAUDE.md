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

## Estágio 3 recalibrado em preço real de B3 (04/09/2026)

O estágio de precificação usava uma escada Black-Scholes **simétrica**
(strike delta-30 a 0,57 dp dos dois lados, prêmio 0,18), mais um "ajuste
de convexidade" de `+σ√20` aplicado só na PUT pra compensar o skew.
Medição contra COTAHIST real (2,5M contratos com delta/IV; delta 15-45,
15-30 du, 2023-01 a 2026-08, 8 papéis líquidos) mostrou que o prêmio
estava perto do certo, mas o **strike simétrico não**: a call delta-30
real fica a 0,794 dp e a put delta-30 a 0,379 dp — gap de skew de 0,415
dp contra os ~0,079 dp que a convexidade corrigia (≈5x menor).

Agora `executor.py` tem `ESTRUTURAS_OPC_LADO[+1|-1]` (escada empírica por
lado) e o helper `k_custo_lado(nome, lado)`; a convexidade foi aposentada
(com strike por lado, mantê-la contaria o skew duas vezes).
`ESTRUTURAS_OPC` continua existindo apontando pra tabela CALL, só pra
iterar nomes de estrutura. `realizados.py` usa a mesma escada
(`_payoff_estrutura(M, nome, lado)`).

**Efeito**: o modelo antigo superestimava a perna CALL. Nos 294 sinais
fechados limpos, payoff médio da carteira caiu de +0,065 pra +0,037 e o
PF de 1,47 pra 1,23 — o edge medido antes estava ~2x otimista. Isso muda
`esperanca_opcao_dp`, que alimenta o **gate** — ou seja, muda quais sinais
saem no Telegram, não só o relatório.

### Correção de 05/09/2026 — o prêmio estava superestimado

A escada de 04/09 acertou os strikes mas **superestimava o prêmio em 8-21% nos
12 pontos**. Defeito de método, não de amostra: ela selecionava contratos por
delta e tirava a mediana de `strike_dp` e a de `premio_dp` separadamente, e duas
medianas marginais não formam um par que esteja sobre a curva k→prêmio. A versão
atual seleciona **por k** e mede a mediana do prêmio naquele k, sobre 755 mil
cotações OTM de 15-30 du em 82 tickers (14-24 mil observações por ponto).
Restringir aos mesmos 8 papéis líquidos de antes dá o mesmo resultado, e por ano
também é estável — não era mix de ticker nem regime.

CALL k=0,794: 0,194 → **0,162**. PUT k=0,379: 0,226 → **0,202**.

**Efeito no gate** (A/B no mesmo dia, 45 tickers): esperança mediana +0,0452 →
+0,0764 e o gate passa a aprovar 25 em vez de 21 tickers — entram GOAU4, ISAE4,
PSSA3 e TOTS3, nenhum sai. 8 tickers mudam de estrutura recomendada. **Sai mais
sinal no Telegram**, porque o custo estava inflado e o gate rejeitava candidatos
com base nele.

**Isso é correção de medição, não ganho de edge.** O payoff medido melhora ~80%
(naked_30 de +0,036 para +0,065 dp), mas o benchmark de moeda — payoff de lado
aleatório nos mesmos ativos e datas, agora impresso pelo `realizados.py` — subiu
junto, e a contribuição do sinal direcional segue em +0,0005 dp.

### Pisos de comparação no `realizados.py`

Toda rodada agora imprime dois pisos antes de qualquer número absoluto, e grava
as colunas que os sustentam (`M_mercado`, `payoff_moeda`, `lado_trivial`,
`lado_modelo`):

- **moeda** — payoff de lado aleatório nos mesmos ativos e datas;
- **trivial** — concordância com `sign(close_t − MA20_t)`, regra de uma linha
  que sozinha acerta ~84% do alvo que o modelo de direção prevê.

Existem porque o sistema vinha sendo lido contra 50% (direção) e contra zero
(payoff), referências que qualquer coisa bate. Contra os pisos certos, o modelo
empata. Se um desses blocos voltar a mostrar empate, não há o que operar,
por melhores que pareçam os números absolutos.

**Ao mexer aqui:** os **strikes** (k) ainda vêm da calibração por delta sobre
os 8 papéis com opção líquida (BOVA11, PETR4, PETR3, VALE3, ITUB4, BBAS3,
BBDC4, SMAL11), feita no `vol_implicita.db`, que **não mora neste repo** (fica
em `/app/volatilidade_implicita`, não versionado). Os **custos** vêm da medição
de 05/09 sobre 82 tickers, a partir da base Parquet gerada por
`construir_base_opcoes.py` no repo principal (COTAHIST → `saidas/opcoes/`, fora
do git por tamanho). A máquina de parsing do COTAHIST é a do repo público
`opcoes-sinal-diario` (`src/cotahist.py`, `src/black76.py`, `src/forward.py`).

Limite que fica: o COTAHIST só registra série que **negociou** no dia, então não
dá pra distinguir "não listada" de "listada sem negócio" — toda medição de
prêmio aqui é condicional a séries que negociaram.

Diagnóstico completo (perna CALL negativa em 8 das 9 semanas, benchmark
correto do realizado, por que mudar delta ou usar spread não resolve) está
no CLAUDE.md do repo principal privado.

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

**Sequência (27/08): a rodada de 26/08 acabou disparando sozinha, ~3h30
atrasada — e essa colidiu com o backfill manual.** `mia_diario.yml`
finalmente rodou via `on: schedule` (não manual) em 27/08 ~00:59 UTC,
calculou os sinais normal (executor.py, 6min), mas o `git push` final
falhou com `! [rejected] ... (fetch first)` porque o backfill manual das
21:30 BRT já tinha avançado `main` — a rodada inteira (119 arquivos) se
perdeu, mesma classe de bug já visto no `acoes_fundamentalista`. **Sem
perda real de sinal** (o backfill já tinha os dados corretos do dia), mas
o workflow ficou exposto a esse risco toda vez que dispara atrasado.
Corrigido: `git fetch origin main && git rebase origin/main` antes do
`git push` em `mia_diario.yml` — mesmo padrão do `acoes_fundamentalista`.

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

## Roda em runner próprio (não GitHub-hosted) desde 28/08

`mia_diario.yml` usa `runs-on: [self-hosted, self-hosted-mia_telegram]` —
migrado depois de 2 dias seguidos (27-28/08) de atraso grave na fila
compartilhada do GitHub Actions (afetou vários repos do usuário, ver
`omqs_futuros_5tf/CLAUDE.md` pro relato completo do incidente que motivou
a migração). Roda numa VPS dedicada (DigitalOcean, mesma máquina de
`api_OMQS`, `api_OMQS_futuros`, `acoes_fundamentalista` e
`opcoes-sinal-diario` — cada um com seu próprio diretório/serviço/label).
Secrets continuam nos GitHub Secrets deste repo, normal. Se o runner
sumir do ar, ver a seção de troubleshooting no `CLAUDE.md` do
`omqs_futuros_5tf` (mesmo procedimento pra qualquer um dos runners dessa
VPS).

**31/08:** `on:schedule` removido de `mia_diario.yml` (disparo nativo do
GitHub provou ser fonte de risco em outros repos da VPS, não rede de
segurança — ver `omqs_futuros_5tf/CLAUDE.md`). `workflow_dispatch`
continua disponível. Como o gatilho real agora é só o cron da VPS, o
horário não vive mais no yml — fica só em `/etc/cron.d/gh-triggers` na
VPS (ver nota abaixo pra horário atual). No mesmo dia,
`download_database.py` falhou 3x seguidas (step que normalmente leva
~5min travou 11-21min e foi morto/SIGKILL) — suspeita de VPS de 1GB
apertada no horário de fechamento (18:30-19:00 BRT, onde vários jobs se
sobrepunham).

**01/09: movido de 18:30 pra 19:20 BRT, pedido do usuário.** Antes
disparava no MESMO minuto que `api_OMQS` (18:30 BRT) — colisão direta de
horário, provável contribuinte pro travamento de 31/08. Com
`acoes_fundamentalista` também saindo desse horário (movido pra 03:00
BRT), o cluster de fechamento ficou mais enxuto: api_OMQS (18:30) →
monitor do omqs_futuros_5tf (18:39) → api_OMQS_futuros (18:50) →
**mia_telegram (19:20, ~30min de folga depois do último)**. Não resolve
por si só a causa raiz (suspeita de RAM da VPS) mas reduz a chance de
2 jobs pesados começarem exatamente juntos.

## Estrutura

Ver `README.md`, seção "Arquivos", pra lista completa.
