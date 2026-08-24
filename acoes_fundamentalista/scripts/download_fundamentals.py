'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Agosto 2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

Coleta diária de dados fundamentalistas (Yahoo Finance / yfinance) para o
universo de ações do IBrA. Roda uma vez por dia via GitHub Actions e vai
empilhando uma série histórica — é a matéria-prima para a análise
fundamentalista futura.

Para cada ticker, salva três coisas:

1. Snapshot de indicadores do dia (P/L, P/VP, ROE, Dividend Yield, margens,
   dívida/patrimônio etc.) — uma linha por ticker por dia, acumulada em
   `dados/snapshot_diario.csv`.

2. Analyst insights / target price (prioridade do projeto): preço-alvo dos
   analistas (mínimo, médio, mediano, máximo), recomendação consolidada e
   número de analistas — uma linha por ticker por dia, acumulada em
   `dados/analyst_insights.csv`. Também salva, por ticker, o histórico de
   upgrades/downgrades e a tendência de recomendação (buy/hold/sell) em
   `dados/analyst_insights/{ticker}_upgrades.csv` e `..._recommendations.csv`
   (sobrescritos a cada run — o yfinance já devolve o histórico completo).

3. Demonstrações financeiras (income statement, balance sheet, cash flow —
   anual e trimestral), sobrescritas em `dados/financeiro/{ticker}_*.csv` a
   cada run (mudam pouco de um dia para o outro, mas custam pouco a
   sobrescrever e assim nunca ficam desatualizadas).
'''

import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
sys.path.insert(0, PASTA_SCRIPTS)

from update_tickers_ibra import garantir_tickers_atualizados  # noqa: E402

PASTA_DADOS = os.path.join(PASTA_PROJETO, "dados")
PASTA_FINANCEIRO = os.path.join(PASTA_DADOS, "financeiro")
PASTA_INSIGHTS = os.path.join(PASTA_DADOS, "analyst_insights")
CSV_SNAPSHOT = os.path.join(PASTA_DADOS, "snapshot_diario.csv")
CSV_ANALYST = os.path.join(PASTA_DADOS, "analyst_insights.csv")

for pasta in (PASTA_DADOS, PASTA_FINANCEIRO, PASTA_INSIGHTS):
    os.makedirs(pasta, exist_ok=True)

HOJE = datetime.now(timezone.utc).strftime("%Y-%m-%d")
TENTATIVAS = 3
PAUSA_ENTRE_TICKERS = 0.4  # segundos, para não apanhar rate-limit do Yahoo


def _get(d, chave, default=None):
    if d is None:
        return default
    return d.get(chave, default)


def coletar_snapshot(ticker: str, info: dict) -> dict:
    preco_atual = _get(info, "currentPrice") or _get(info, "regularMarketPrice")
    return {
        "data": HOJE,
        "ticker": ticker,
        "nome": _get(info, "shortName"),
        "setor": _get(info, "sector"),
        "industria": _get(info, "industry"),
        "preco_atual": preco_atual,
        "market_cap": _get(info, "marketCap"),
        "pl": _get(info, "trailingPE"),
        "pl_forward": _get(info, "forwardPE"),
        "pvp": _get(info, "priceToBook"),
        "psr": _get(info, "priceToSalesTrailing12Months"),
        "ev_ebitda": _get(info, "enterpriseToEbitda"),
        "roe": _get(info, "returnOnEquity"),
        "roa": _get(info, "returnOnAssets"),
        "margem_liquida": _get(info, "profitMargins"),
        "margem_bruta": _get(info, "grossMargins"),
        "margem_ebitda": _get(info, "ebitdaMargins"),
        "dividend_yield": _get(info, "dividendYield"),
        "payout_ratio": _get(info, "payoutRatio"),
        "divida_patrimonio": _get(info, "debtToEquity"),
        "lpa": _get(info, "trailingEps"),
        "lpa_forward": _get(info, "forwardEps"),
        "beta": _get(info, "beta"),
        "volume_medio": _get(info, "averageVolume"),
    }


def coletar_analyst_insights(ticker: str, info: dict, acao_yf: yf.Ticker) -> dict:
    preco_atual = _get(info, "currentPrice") or _get(info, "regularMarketPrice")

    targets = {}
    try:
        targets = acao_yf.analyst_price_targets or {}
    except Exception as e:
        print(f"  [{ticker}] sem analyst_price_targets: {e}")

    target_mean = targets.get("mean") or _get(info, "targetMeanPrice")
    upside_pct = None
    if preco_atual and target_mean:
        try:
            upside_pct = (target_mean / preco_atual) - 1
        except (TypeError, ZeroDivisionError):
            upside_pct = None

    linha = {
        "data": HOJE,
        "ticker": ticker,
        "preco_atual": targets.get("current") or preco_atual,
        "target_low": targets.get("low") or _get(info, "targetLowPrice"),
        "target_mean": target_mean,
        "target_median": targets.get("median") or _get(info, "targetMedianPrice"),
        "target_high": targets.get("high") or _get(info, "targetHighPrice"),
        "upside_pct": upside_pct,
        "recommendation_key": _get(info, "recommendationKey"),
        "recommendation_mean": _get(info, "recommendationMean"),
        "num_analistas": _get(info, "numberOfAnalystOpinions"),
    }

    # histórico de tendência de recomendação (buy/hold/sell por período) e de
    # upgrades/downgrades — sobrescreve o CSV por ticker com o que o yfinance
    # devolver de mais atual (já vem com o histórico inteiro disponível).
    try:
        rec = acao_yf.recommendations
        if rec is not None and not rec.empty:
            rec.to_csv(os.path.join(PASTA_INSIGHTS, f"{ticker}_recommendations.csv"), index=False)
    except Exception as e:
        print(f"  [{ticker}] sem recommendations: {e}")

    try:
        upg = acao_yf.upgrades_downgrades
        if upg is not None and not upg.empty:
            upg.to_csv(os.path.join(PASTA_INSIGHTS, f"{ticker}_upgrades.csv"))
    except Exception as e:
        print(f"  [{ticker}] sem upgrades_downgrades: {e}")

    return linha


def salvar_financeiro(ticker: str, acao_yf: yf.Ticker) -> None:
    demonstracoes = {
        "income_annual": acao_yf.income_stmt,
        "income_quarterly": acao_yf.quarterly_income_stmt,
        "balance_annual": acao_yf.balance_sheet,
        "balance_quarterly": acao_yf.quarterly_balance_sheet,
        "cashflow_annual": acao_yf.cashflow,
        "cashflow_quarterly": acao_yf.quarterly_cashflow,
    }
    for nome, df in demonstracoes.items():
        try:
            if df is not None and not df.empty:
                df.to_csv(os.path.join(PASTA_FINANCEIRO, f"{ticker}_{nome}.csv"))
        except Exception as e:
            print(f"  [{ticker}] erro salvando {nome}: {e}")


def _atualiza_serie_historica(caminho_csv: str, linhas_novas: list[dict]) -> None:
    """Acrescenta as linhas do dia numa série histórica CSV, sem duplicar
    (se o script rodar de novo no mesmo dia, substitui as linhas de hoje)."""
    df_novo = pd.DataFrame(linhas_novas)
    if df_novo.empty:
        return
    if os.path.exists(caminho_csv):
        df_existente = pd.read_csv(caminho_csv)
        df_existente = df_existente[
            ~((df_existente["data"] == HOJE) & (df_existente["ticker"].isin(df_novo["ticker"])))
        ]
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)
    else:
        df_final = df_novo
    df_final = df_final.sort_values(["ticker", "data"]).reset_index(drop=True)
    df_final.to_csv(caminho_csv, index=False)


def coletar_ticker(ticker: str, ticker_yf: str) -> tuple[dict | None, dict | None]:
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            acao = yf.Ticker(ticker_yf)
            info = acao.info
            if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
                raise ValueError("info vazio ou sem preço — provável ticker inválido/deslistado")

            snapshot = coletar_snapshot(ticker, info)
            insights = coletar_analyst_insights(ticker, info, acao)
            salvar_financeiro(ticker, acao)
            return snapshot, insights
        except Exception as e:
            print(f"[{ticker}] tentativa {tentativa}/{TENTATIVAS} falhou: {e}")
            if tentativa < TENTATIVAS:
                time.sleep(5)
    print(f"[{ticker}] falha definitiva, pulando.")
    return None, None


def main() -> None:
    tickers_df = garantir_tickers_atualizados()
    print(f"Coletando fundamentos de {len(tickers_df)} tickers do IBrA...")

    snapshots, analyst_rows = [], []
    for i, row in tickers_df.iterrows():
        ticker, ticker_yf = row["ticker"], row["ticker_yf"]
        print(f"[{i + 1}/{len(tickers_df)}] {ticker}")
        snapshot, insights = coletar_ticker(ticker, ticker_yf)
        if snapshot:
            snapshots.append(snapshot)
        if insights:
            analyst_rows.append(insights)
        time.sleep(PAUSA_ENTRE_TICKERS)

    _atualiza_serie_historica(CSV_SNAPSHOT, snapshots)
    _atualiza_serie_historica(CSV_ANALYST, analyst_rows)

    print(
        f"Concluído: {len(snapshots)}/{len(tickers_df)} snapshots e "
        f"{len(analyst_rows)}/{len(tickers_df)} registros de analyst insights salvos."
    )


if __name__ == "__main__":
    main()
