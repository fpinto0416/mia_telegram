'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Agosto 2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

Atualiza a lista de tickers do IBrA (Índice Brasil Amplo) direto da B3,
usada como universo de ações para o projeto de análise fundamentalista.

A B3 não publica um CSV estático da carteira — o site oficial
(b3.com.br/.../indice-brasil-amplo-ibra-composicao-da-carteira.htm) monta a
tabela via chamada JS a um endpoint JSON público (o mesmo endpoint que
alimenta a tabela na página, sem autenticação). É esse endpoint que este
script consulta, paginando até trazer a carteira inteira.

A carteira do IBrA é rebalanceada pela B3 a cada quadrimestre (jan/mai/set),
então não precisa (nem deve) ser atualizada todo dia — por padrão este
script só refaz o download se o CSV local não existir ou tiver mais de
`IDADE_MAXIMA_DIAS` dias. Use FORCE_UPDATE=1 para forçar.
------------------------------------------------------------
'''

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

PASTA_DESTE_ARQUIVO = os.path.dirname(os.path.abspath(__file__))
PASTA_TICKERS = os.path.join(os.path.dirname(PASTA_DESTE_ARQUIVO), "tickers")
CAMINHO_CSV = os.path.join(PASTA_TICKERS, "ibra_composicao.csv")

IDADE_MAXIMA_DIAS = 25  # a carteira só muda a cada ~4 meses; 25 dias é suficiente

B3_ENDPOINT = "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/{params}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.b3.com.br/",
}


def _monta_url(index: str, page_number: int, page_size: int = 120) -> str:
    params = {
        "language": "pt-br",
        "pageNumber": page_number,
        "pageSize": page_size,
        "index": index,
        "segment": "1",
    }
    b64 = base64.b64encode(json.dumps(params).encode()).decode()
    return B3_ENDPOINT.format(params=b64)


def baixar_composicao_ibra(index: str = "IBRA", tentativas: int = 5) -> pd.DataFrame:
    """Baixa a carteira teórica do índice informado (default IBrA) direto da B3."""
    linhas = []
    page_number = 1
    total_pages = 1

    while page_number <= total_pages:
        url = _monta_url(index, page_number)
        tentativa = 0
        while tentativa < tentativas:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                tentativa += 1
                print(f"Erro ao baixar página {page_number} do {index}: {e}")
                if tentativa < tentativas:
                    print("Tentando novamente em 10 segundos...")
                    time.sleep(10)
                else:
                    raise

        resultados = data.get("results", [])
        for item in resultados:
            linhas.append(
                {
                    "ticker": item.get("cod"),
                    "nome": item.get("asset"),
                    "tipo": item.get("type"),
                    "participacao_pct": item.get("part"),
                    "theoricalQty": item.get("theoricalQty"),
                }
            )

        page_info = data.get("page", {})
        total_pages = page_info.get("totalPages", 1) or 1
        page_number += 1
        time.sleep(1)  # não martelar o endpoint da B3

    df = pd.DataFrame(linhas).drop_duplicates(subset="ticker").reset_index(drop=True)
    df = df[df["ticker"].notna() & (df["ticker"] != "")]
    df["ticker_yf"] = df["ticker"] + ".SA"
    df["data_atualizacao"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return df[["ticker", "ticker_yf", "nome", "tipo", "participacao_pct", "data_atualizacao"]]


def csv_esta_desatualizado() -> bool:
    if os.environ.get("FORCE_UPDATE") == "1":
        return True
    if not os.path.exists(CAMINHO_CSV):
        return True
    mtime = datetime.fromtimestamp(os.path.getmtime(CAMINHO_CSV), tz=timezone.utc)
    return datetime.now(timezone.utc) - mtime > timedelta(days=IDADE_MAXIMA_DIAS)


def garantir_tickers_atualizados() -> pd.DataFrame:
    """Usado por download_fundamentals.py: só bate na B3 se o CSV estiver velho/ausente."""
    if csv_esta_desatualizado():
        print("Lista de tickers do IBrA ausente ou desatualizada — baixando da B3...")
        atualizar_e_salvar()
    else:
        print(f"Lista de tickers do IBrA em {CAMINHO_CSV} ainda está atual, reaproveitando.")
    return pd.read_csv(CAMINHO_CSV)


def atualizar_e_salvar() -> pd.DataFrame:
    os.makedirs(PASTA_TICKERS, exist_ok=True)
    df = baixar_composicao_ibra("IBRA")
    df.to_csv(CAMINHO_CSV, index=False)
    print(f"{len(df)} tickers do IBrA salvos em {CAMINHO_CSV}")
    return df


if __name__ == "__main__":
    atualizar_e_salvar()
