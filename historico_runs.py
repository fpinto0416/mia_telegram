"""
MIA — Histórico de Runs
=======================
Consolida todos os arquivos ordem_dia_*.xlsx em uma série temporal.
Gera historico_runs_YYYY_MM_DD.xlsx com 3 abas:
  - resumo_diario    : uma linha por dia (funil + contagens)
  - sinais_historico : uma linha por ticker×dia com sinal operacional
  - persistencia     : tickers que aparecem em N+ dias consecutivos

Uso: python historico_runs.py
"""

import os
import re
from pathlib import Path
from datetime import datetime, date

import numpy as np
import pandas as pd

PASTA = Path(__file__).parent
HOJE  = date.today()
NOME_SAIDA = PASTA / "saidas" / "historico_runs" / f"historico_runs_{HOJE:%Y_%m_%d}.xlsx"
NOME_SAIDA.parent.mkdir(parents=True, exist_ok=True)

COLS_RESUMO = [
    "data", "arquivo",
    "tickers_processados", "n_alertas", "n_compras", "n_monitorar", "n_neutros",
    "linhas_aba_ordem",
    "esp_mediana_sinais", "esp_max_sinais",
    "rv_rank_mediano_sinais",
    "todos_call", "todos_put", "pct_call_10d",
    "motivo_top1", "motivo_top1_n",
    "motivo_top2", "motivo_top2_n",
]

COLS_SINAL = [
    "data", "ticker", "ordem", "operacao_nivel_live",
    "esperanca_opcao_dp", "rv_rank_252", "vol_prediction_ratio",
    "razao_sinal", "forca_sinal", "score_acionavel", "score_combinado",
    "motivo_bloqueio_executor", "vol_model_tipo",
    "estrategia_preferida", "confianca_recomendacao",
]


def _extrair_data(path: Path):
    """Extrai data do nome do arquivo."""
    m = re.search(r"(\d{4})_(\d{2})_(\d{2})", path.stem)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _safe_get(df, col, default=np.nan):
    return df[col] if col in df.columns else default


def processar_arquivo(path: Path):
    data = _extrair_data(path)
    if data is None:
        return None, None

    try:
        todos  = pd.read_excel(path, sheet_name="todos")
        ordem  = pd.read_excel(path, sheet_name="ordem")
    except Exception as e:
        print(f"  Erro ao ler {path.name}: {e}")
        return None, None

    todos["data"] = pd.Timestamp(data)

    # ── Resumo diário ──────────────────────────────────────────────────────
    sinais_mask   = todos["ordem"].str.contains("Alerta|Compra",   na=False)
    monit_mask    = todos["ordem"].str.contains("Monitorar",        na=False)
    neutro_mask   = todos["ordem"] == "Neutro"

    sinais_df     = todos[sinais_mask]
    n_alertas     = sinais_df["ordem"].str.startswith("Alerta").sum()
    n_compras     = sinais_df["ordem"].str.startswith("Compra").sum()
    n_monit       = monit_mask.sum()
    n_neutros     = neutro_mask.sum()

    # compatibilidade: coluna renomeada de esperanca_ganho_dp → esperanca_opcao_dp
    _esp_col = "esperanca_opcao_dp" if "esperanca_opcao_dp" in todos.columns else "esperanca_ganho_dp"
    esp_med = sinais_df[_esp_col].median() if not sinais_df.empty and _esp_col in sinais_df.columns else np.nan
    esp_max = sinais_df[_esp_col].max()    if not sinais_df.empty and _esp_col in sinais_df.columns else np.nan
    rv_med  = sinais_df["rv_rank_252"].median()        if "rv_rank_252" in sinais_df.columns and not sinais_df.empty else np.nan

    todos_call = sinais_df["ordem"].str.contains("CALL", na=False).sum()
    todos_put  = sinais_df["ordem"].str.contains("PUT",  na=False).sum()

    motivos = todos["motivo_bloqueio_executor"].value_counts()
    m1, m1n = (motivos.index[0], int(motivos.iloc[0])) if len(motivos) > 0 else ("", 0)
    m2, m2n = (motivos.index[1], int(motivos.iloc[1])) if len(motivos) > 1 else ("", 0)

    resumo_row = {
        "data": pd.Timestamp(data),
        "arquivo": path.name,
        "tickers_processados": len(todos),
        "n_alertas": int(n_alertas),
        "n_compras": int(n_compras),
        "n_monitorar": int(n_monit),
        "n_neutros": int(n_neutros),
        "linhas_aba_ordem": len(ordem),
        "esp_mediana_sinais": round(float(esp_med), 3) if pd.notna(esp_med) else np.nan,
        "esp_max_sinais": round(float(esp_max), 3)     if pd.notna(esp_max) else np.nan,
        "rv_rank_mediano_sinais": round(float(rv_med), 3) if pd.notna(rv_med) else np.nan,
        "todos_call": int(todos_call),
        "todos_put": int(todos_put),
        "motivo_top1": m1, "motivo_top1_n": m1n,
        "motivo_top2": m2, "motivo_top2_n": m2n,
    }

    # ── Sinais individuais ─────────────────────────────────────────────────
    sinais_todos = todos[sinais_mask | monit_mask].copy()
    sinais_todos["data"] = pd.Timestamp(data)
    cols_disponiveis = [c for c in COLS_SINAL if c in sinais_todos.columns]
    sinais_out = sinais_todos[cols_disponiveis].copy()

    return resumo_row, sinais_out


def calcular_persistencia(df_sinais: pd.DataFrame) -> pd.DataFrame:
    """Tickers que aparecem como sinal em múltiplos dias."""
    if df_sinais.empty:
        return pd.DataFrame()

    _esp_col_p = "esperanca_opcao_dp" if "esperanca_opcao_dp" in df_sinais.columns else "esperanca_ganho_dp"
    agg = (
        df_sinais
        .groupby("ticker")
        .agg(
            n_dias=("data", "nunique"),
            primeira_aparicao=("data", "min"),
            ultima_aparicao=("data", "max"),
            ordens=("ordem", lambda x: ", ".join(sorted(set(x)))),
            esp_media=(_esp_col_p, "mean"),
            esp_max=(_esp_col_p, "max"),
        )
        .reset_index()
        .sort_values("n_dias", ascending=False)
    )
    agg["esp_media"] = agg["esp_media"].round(3)
    agg["esp_max"]   = agg["esp_max"].round(3)
    return agg


# ── Main ───────────────────────────────────────────────────────────────────
arquivos = sorted(
    [p for p in (PASTA / "saidas" / "ordens_dia").glob("ordem_dia_*executor_v5_5.xlsx")],
    key=lambda p: _extrair_data(p) or date.min
)

print(f"Arquivos encontrados: {len(arquivos)}")

resumos   = []
sinais_ls = []

for arq in arquivos:
    data = _extrair_data(arq)
    print(f"  Processando {arq.name} ({data})")
    res, sig = processar_arquivo(arq)
    if res:
        resumos.append(res)
    if sig is not None and not sig.empty:
        sinais_ls.append(sig)

df_resumo  = pd.DataFrame(resumos)
df_sinais  = pd.concat(sinais_ls, ignore_index=True) if sinais_ls else pd.DataFrame()
df_persist = calcular_persistencia(df_sinais)

# ── Janela móvel 10d CALL/PUT ──────────────────────────────────────────────
if not df_resumo.empty and {"todos_call", "todos_put"}.issubset(df_resumo.columns):
    _call_10d  = df_resumo["todos_call"].rolling(10, min_periods=1).sum()
    _total_10d = (df_resumo["todos_call"] + df_resumo["todos_put"]).rolling(10, min_periods=1).sum()
    df_resumo["pct_call_10d"] = (_call_10d / _total_10d.replace(0, np.nan)).round(3)

# ── Salvar ─────────────────────────────────────────────────────────────────
with pd.ExcelWriter(NOME_SAIDA, engine="openpyxl") as writer:
    df_resumo.to_excel(writer,  sheet_name="resumo_diario",    index=False)
    df_sinais.to_excel(writer,  sheet_name="sinais_historico", index=False)
    df_persist.to_excel(writer, sheet_name="persistencia",     index=False)

print(f"\nSalvo: {NOME_SAIDA}")
print(f"Dias processados: {len(df_resumo)}")
print(f"Sinais históricos: {len(df_sinais)}")
print()

# ── Resumo no terminal ─────────────────────────────────────────────────────
print("=" * 70)
print("SÉRIE TEMPORAL — RESUMO")
print("=" * 70)
if not df_resumo.empty:
    for _, r in df_resumo.iterrows():
        d = r["data"].strftime("%Y-%m-%d")
        _pct_str = f" pct_call_10d={r['pct_call_10d']:.0%}" if pd.notna(r.get("pct_call_10d")) else ""
        print(
            f"{d} | ordem={int(r['linhas_aba_ordem']):2d} | "
            f"alertas={int(r['n_alertas']):2d} | monit={int(r['n_monitorar']):2d} | "
            f"esp_med={r['esp_mediana_sinais']:.2f} | "
            f"CALL={int(r['todos_call'])} PUT={int(r['todos_put'])}{_pct_str}"
        )

print()
print("=" * 70)
print("TICKERS MAIS PERSISTENTES (≥ 2 dias)")
print("=" * 70)
if not df_persist.empty:
    for _, r in df_persist[df_persist["n_dias"] >= 2].iterrows():
        print(
            f"{r['ticker']:8s} | {int(r['n_dias'])} dias | "
            f"esp_med={r['esp_media']:.2f} | esp_max={r['esp_max']:.2f} | "
            f"ordens: {r['ordens']}"
        )
