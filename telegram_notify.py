"""
MIA — Notificador Telegram
===========================
Lê o Excel gerado no run do dia e envia um resumo em texto no Telegram.
Não substitui o Excel: ele fica commitado no repositório (saidas/...);
esta mensagem é só o resumo para leitura rápida no celular.

Uso:
    python telegram_notify.py executor      # resumo de saidas/ordens_dia/ordem_dia_*.xlsx
    python telegram_notify.py realizados    # resumo de saidas/realizados/realizados_*.xlsx

Requer variáveis de ambiente:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

PASTA = Path(__file__).parent
HOJE = date.today()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

LIMITE_TICKERS_MSG = 20


def _enviar(texto: str) -> None:
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID não configurados no ambiente.")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    # Telegram limita ~4096 caracteres por mensagem — divide em blocos.
    for i in range(0, max(len(texto), 1), 4000):
        pedaco = texto[i:i + 4000]
        if not pedaco:
            continue
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": pedaco, "disable_web_page_preview": True},
            timeout=30,
        )
        resp.raise_for_status()


def _arquivo_mais_recente(pasta: Path, padrao: str) -> Path | None:
    arquivos = sorted(pasta.glob(padrao))
    return arquivos[-1] if arquivos else None


def _fmt(v, casas=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "?"
    if isinstance(v, (int, float)):
        return f"{v:.{casas}f}"
    return str(v)


def _fmt_data(v) -> str:
    ts = pd.to_datetime(v, errors="coerce")
    return ts.strftime("%d/%m/%Y") if pd.notna(ts) else str(v)


def _sem_parenteses_finais(texto: str) -> str:
    """Remove um parenteses no final da string, ex.: 'Call delta~45 naked (quase ATM, ~0.9σ, 20d)' -> 'Call delta~45 naked'."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", str(texto)).strip()


def _msg_executor() -> str:
    arq = _arquivo_mais_recente(PASTA / "saidas" / "ordens_dia", "ordem_dia_*_executor_v5_5.xlsx")
    if arq is None:
        return f"Projeto MIA\nOrdem do dia {HOJE:%d/%m/%Y}\nNenhum ordem_dia_*.xlsx encontrado em saidas/ordens_dia/."

    df_ordem = pd.read_excel(arq, sheet_name="ordem")
    if "veto_gate_historico" in df_ordem.columns:
        df_ordem = df_ordem[df_ordem["veto_gate_historico"] != 1]

    linhas = ["Projeto MIA", f"Ordem do dia — {HOJE:%d/%m/%Y}", ""]
    if df_ordem.empty:
        linhas.append("Nenhum sinal operacional hoje.")
    else:
        for _, row in df_ordem.head(LIMITE_TICKERS_MSG).iterrows():
            estrategia = _sem_parenteses_finais(row.get("estrategia_preferida", ""))
            linhas.append(f"{row['ticker']} | {row['ordem']} | {estrategia}")
        if len(df_ordem) > LIMITE_TICKERS_MSG:
            linhas.append(f"... +{len(df_ordem) - LIMITE_TICKERS_MSG} no arquivo completo")

    try:
        avisos = pd.read_excel(arq, sheet_name="avisos_saida")
        avisos = avisos[avisos["aviso_saida"].notna() & (avisos["aviso_saida"].astype(str).str.strip() != "")]
        avisos = avisos[avisos["aviso_saida"].astype(str).str.strip() != "MANTER"]
        linhas.append("")
        linhas.append("Avisos de saída")
        if avisos.empty:
            linhas.append("Nenhum aviso de saída hoje.")
        else:
            for _, row in avisos.head(LIMITE_TICKERS_MSG).iterrows():
                linhas.append(
                    f"{row['ticker']} | {row['aviso_saida']} | "
                    f"entrada {_fmt_data(row['data_entrada'])} {row['ordem_entrada']}"
                )
            if len(avisos) > LIMITE_TICKERS_MSG:
                linhas.append(f"... +{len(avisos) - LIMITE_TICKERS_MSG} no arquivo completo")
    except Exception:
        pass

    try:
        resumo = pd.read_excel(arq, sheet_name="resumo")
        r = dict(zip(resumo["metrica"], resumo["valor"]))
        idade_dir = r.get("idade_direction_input_dias")
        idade_vol = r.get("idade_volatility_input_dias")
        linhas.append("")
        linhas.append(f"Direction sem rodar há {_fmt(idade_dir, 0)} dias | Volatility sem rodar há {_fmt(idade_vol, 0)} dias")
    except Exception:
        pass

    return "\n".join(linhas)


def _msg_realizados() -> str:
    arq = _arquivo_mais_recente(PASTA / "saidas" / "realizados", "realizados_*.xlsx")
    if arq is None:
        return f"Projeto MIA\nRealizados {HOJE:%d/%m/%Y}\nNenhum realizados_*.xlsx encontrado em saidas/realizados/."

    agg = pd.read_excel(arq, sheet_name="agregados")
    linhas = ["Projeto MIA", f"Realizados {HOJE:%d/%m/%Y}", f"Arquivo: {arq.name}", ""]

    principais = agg[agg["estrutura"] == "naked_30"] if "estrutura" in agg.columns else agg.iloc[0:0]
    for nivel in ["nivel_A", "nivel_B", "nivel_Monitorar", "nivel_vetado"]:
        sub = principais[principais["grupo"] == nivel]
        if sub.empty:
            continue
        row = sub.iloc[0]
        linhas.append(
            f"{nivel} (naked_30) — n={int(row['n'])} | "
            f"acerto={_fmt(row['acerto_direcao'] * 100, 0)}% | "
            f"lucro={_fmt(row['lucro_estrutura'] * 100, 0)}% | "
            f"payoff médio={_fmt(row['payoff_medio'], 3)}"
        )

    if len(linhas) == 3:
        linhas.append("Sem operações fechadas (20 pregões) para agregar ainda.")

    linhas.append("")
    linhas.append(f"Arquivo completo commitado em saidas/realizados/{arq.name}")
    return "\n".join(linhas)


def main() -> None:
    modo = sys.argv[1] if len(sys.argv) > 1 else "executor"
    if modo == "executor":
        texto = _msg_executor()
    elif modo == "realizados":
        texto = _msg_realizados()
    else:
        raise SystemExit(f"Modo desconhecido: {modo!r} (use 'executor' ou 'realizados')")
    print(texto)
    _enviar(texto)


if __name__ == "__main__":
    main()
