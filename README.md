# mia_telegram

Subprojeto do **Projeto MIA** — executor diário + notificação no Telegram + geração do
xlsx de realizados. Enxuto de propósito: só o necessário para *rodar* o sinal do dia,
não para treinar os modelos.

## O que este repositório NÃO faz

Não treina `direction_best2.py` nem `volatility_best.py` (isso é caro — walk-forward +
backward feature elimination por ~95 tickers, roda no repositório principal, privado).
Este repo só consome:

- os **modelos já treinados** (`modelos_direction/`, `modelos_volatility/`);
- a **planilha de entrada** mais recente de cada módulo (`saidas/direction/*.xlsx`,
  `saidas/volatility/*.xlsx`), que o `executor.py` descobre sozinho pelo nome/data;
- o **preço do dia**, baixado a cada run por `download_database.py`.

## O que roda todo dia

`.github/workflows/mia_diario.yml` — 09:00 e 18:30 (horário de Brasília), seg-sex, ou
disparo manual:

1. `download_database.py` — atualiza `database/*.xlsx` (recriado do zero a cada run —
   não fica versionado aqui para manter o repo leve)
2. `executor.py` (`SKIP_DOWNLOAD=1`) — calcula os sinais do dia usando os modelos e as
   planilhas de entrada já commitadas
3. `historico_runs.py` — consolida `saidas/ordens_dia/*.xlsx` numa série temporal
4. commit + push de volta (`database/`, `saidas/`)
5. `telegram_notify.py executor` — manda o resumo do dia no Telegram

`.github/workflows/mia_realizados.yml` — só disparo manual: atualiza o database, roda
`realizados.py` (cruza sinais passados com o preço 20 pregões depois), commita
`saidas/realizados/*.xlsx` e manda o resumo de performance no Telegram.

## Setup

Cadastrar em Settings → Secrets and variables → Actions:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Atualizando os modelos

Quando retreinar `direction_best2.py`/`volatility_best.py` no projeto principal, copie
para cá:

```bash
cp modelos_direction/*.pkl   <este_repo>/modelos_direction/
cp modelos_volatility/*.pkl  <este_repo>/modelos_volatility/
cp saidas/direction/direction_best_YYYY_MM_DD.xlsx                    <este_repo>/saidas/direction/
cp saidas/volatility/volatility_best_YYYY_MM_DD_v6_2_operational.xlsx <este_repo>/saidas/volatility/
```

Não precisa apagar os antigos — o `executor.py` sempre pega o mais recente por data no
nome do arquivo — mas vale limpar de vez em quando para não acumular peso.
`executor.py` avisa no log (e no resumo do Telegram) quando os artefatos passam de 45
dias, sugerindo retreino.

## Nota sobre ser público

Este repositório ficou público para rodar de graça no GitHub Actions (repos privados
têm cota limitada de minutos). Isso significa que o código, os modelos treinados
(`.pkl`) e o histórico de sinais em `saidas/` ficam visíveis publicamente. As
credenciais do TradingView em `download_database.py` são só placeholders (o download
funciona em modo anônimo) — nenhum segredo real fica no código, só nos secrets do
GitHub.

## Arquivos

| Arquivo | Papel |
|---|---|
| `download_database.py` | Baixa preço OHLCV do dia (yfinance + TradingView) |
| `executor.py` | Decide CALL/PUT/Neutro usando os modelos + preço do dia |
| `options_advisor.py` | Recomenda estratégia de opções por sinal (usado pelo executor) |
| `historico_runs.py` | Consolida `ordem_dia_*.xlsx` numa série temporal |
| `realizados.py` | Cruza sinais passados com o preço realizado 20 pregões depois |
| `telegram_notify.py` | Lê o xlsx do dia e manda o resumo no Telegram |

## Projeto Ações — Análise Fundamentalista

Subprojeto novo, em `acoes_fundamentalista/`, separado do pipeline de opções
acima. Coleta diariamente (via `.github/workflows/acoes_fundamentalista_diario.yml`)
dados fundamentalistas do Yahoo Finance — indicadores, preço-alvo de
analistas, demonstrações financeiras — para o universo de ações do IBrA
(Índice Brasil Amplo). Por enquanto é só coleta/acúmulo de histórico; a
análise em cima disso vem depois. Detalhes em
[`acoes_fundamentalista/README.md`](acoes_fundamentalista/README.md).
