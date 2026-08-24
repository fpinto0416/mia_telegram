# Projeto Ações — Análise Fundamentalista

Subprojeto dedicado a montar, dia a dia, uma base histórica de dados
fundamentalistas (Yahoo Finance) das ações do **IBrA** (Índice Brasil Amplo —
carteira teórica oficial da B3, cobre ~99% do valor de mercado negociado na
bolsa). Por enquanto o objetivo é só **coletar e acumular histórico**; a
análise em cima disso vem depois, quando já houver uma série de dias/semanas
armazenada.

## Estrutura

```
acoes_fundamentalista/
├── scripts/
│   ├── update_tickers_ibra.py   # baixa a composição do IBrA direto da B3
│   ├── download_fundamentals.py # coleta diária de fundamentos (yfinance)
│   ├── gerar_panorama.py        # gera o painel HTML a partir dos CSVs do dia
│   └── _template_panorama.html  # template (CSS/JS) usado por gerar_panorama.py
├── tickers/
│   └── ibra_composicao.csv      # universo de tickers (atualizado ~mensalmente)
└── dados/
    ├── snapshot_diario.csv      # 1 linha/ticker/dia: P/L, P/VP, ROE, DY, margens...
    ├── analyst_insights.csv     # 1 linha/ticker/dia: preço-alvo (low/mean/high), recomendação
    ├── analyst_insights/        # por ticker: histórico de upgrades/downgrades e tendência de recomendação
    ├── financeiro/               # por ticker: DRE, balanço e fluxo de caixa (anual e trimestral)
    └── panorama_de_alvos.html    # painel do último dia coletado (ver seção abaixo)
```

## O que é coletado todo dia

1. **Snapshot de indicadores** (`dados/snapshot_diario.csv`): P/L, P/L
   forward, P/VP, PSR, EV/EBITDA, ROE, ROA, margens (bruta/líquida/EBITDA),
   dividend yield, payout, dívida/patrimônio, LPA, beta, market cap, volume
   médio, setor/indústria.

2. **Analyst insights / preço-alvo** (`dados/analyst_insights.csv`) — é o
   foco principal do projeto: preço-alvo mínimo, médio, mediano e máximo dos
   analistas (`Ticker.analyst_price_targets` do yfinance), upside implícito
   sobre o preço atual, recomendação consolidada (`recommendationKey`) e
   número de analistas cobrindo o papel. Complementado por
   `dados/analyst_insights/{ticker}_upgrades.csv` (histórico de
   upgrades/downgrades por casa de análise) e
   `{ticker}_recommendations.csv` (tendência buy/hold/sell por período).

3. **Demonstrações financeiras** (`dados/financeiro/`): DRE, balanço
   patrimonial e fluxo de caixa, anual e trimestral, um CSV por ticker —
   sobrescritos a cada run (mudam pouco de um dia pro outro, mas assim nunca
   ficam desatualizados).

`snapshot_diario.csv` e `analyst_insights.csv` são séries históricas que só
crescem: cada run acrescenta a linha do dia (e substitui a linha do dia, se
o job rodar de novo na mesma data) — depois de algumas semanas já dá pra
olhar evolução de indicador e de preço-alvo ao longo do tempo.

## Universo de tickers (IBrA)

A composição do IBrA muda pouco (a B3 rebalanceia a cada quadrimestre:
jan/mai/set), então `update_tickers_ibra.py` só bate na B3 se
`tickers/ibra_composicao.csv` não existir ou tiver mais de 25 dias — nas
outras execuções ele reaproveita o CSV já salvo.
`download_fundamentals.py` chama essa checagem sozinho, então não precisa
rodar os dois scripts na mão em sequência.

Forçar atualização manual da lista de tickers:

```bash
FORCE_UPDATE=1 python acoes_fundamentalista/scripts/update_tickers_ibra.py
```

(o workflow também aceita isso via `workflow_dispatch` → "Forcar atualizacao
da lista de tickers do IBrA").

> Nota: a B3 não expõe um CSV estático da carteira — o próprio site oficial
> carrega a tabela via um endpoint JSON interno, que é o que este script
> consulta (mesmo endpoint, sem autenticação). Se a B3 mudar esse endpoint no
> futuro, é só ajustar `_monta_url`/`B3_ENDPOINT` em `update_tickers_ibra.py`.

## Painel do dia (`panorama_de_alvos.html`)

`gerar_panorama.py` lê os CSVs do dia mais recente e monta um painel HTML
estático (sem dependência externa, abre em qualquer navegador) com:

- **Top 10 mais abaixo do preço-alvo mínimo** e **Top 10 mais abaixo do
  preço-alvo médio** — ranking por `(target − preço_atual) / preço_atual`,
  com uma barra mostrando a faixa low→mean→high dos analistas e onde o
  preço de hoje cai nela.
- **Consultar um ativo** — um seletor (dropdown) com todos os tickers
  coletados no dia; ao escolher um, mostra o preço-alvo e todos os
  indicadores fundamentalistas daquele ativo (P/L, P/VP, ROE, margens,
  dividend yield, dívida/patrimônio, beta etc.).

O arquivo é sobrescrito a cada run e fica versionado em
`dados/panorama_de_alvos.html` — dá pra abrir direto do repositório (ou
baixar) sem precisar rodar nada.

## Automação

`.github/workflows/acoes_fundamentalista_diario.yml` roda todo dia útil às
19:00 (horário de Brasília), depois do fechamento da B3: coleta os
fundamentos (`download_fundamentals.py`), gera o painel
(`gerar_panorama.py`) e faz commit + push de `dados/` e `tickers/` de volta
pro repositório. Também pode ser disparado manualmente pela aba Actions.

Rodar localmente:

```bash
pip install -r requirements.txt
python acoes_fundamentalista/scripts/download_fundamentals.py
python acoes_fundamentalista/scripts/gerar_panorama.py
```

## Próximos passos (não implementados ainda)

Com o histórico acumulado, os passos naturais seguintes são notebooks/
scripts de análise em cima de `dados/snapshot_diario.csv` e
`dados/analyst_insights.csv` — screening por múltiplos, ranking por upside
de preço-alvo, cruzamento com o `saidas/` do projeto de opções etc.
