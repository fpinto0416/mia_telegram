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


def _msg_executor() -> str:
    arq = _arquivo_mais_recente(PASTA / "saidas" / "ordens_dia", "ordem_dia_*_executor_v5_5.xlsx")
    if arq is None:
        return f"MIA — Ordem do dia {HOJE:%d/%m/%Y}\nNenhum ordem_dia_*.xlsx encontrado em saidas/ordens_dia/."

    resumo = pd.read_excel(arq, sheet_name="resumo")
    r = dict(zip(resumo["metrica"], resumo["valor"]))
    df_ordem = pd.read_excel(arq, sheet_name="ordem")

    linhas = [f"MIA — Ordem do dia {HOJE:%d/%m/%Y}", f"Arquivo: {arq.name}", ""]
    linhas.append(
        f"Compras: {r.get('compras_opcao_live', '?')} | "
        f"Alertas: {r.get('alertas_operacionais_live', '?')} | "
        f"Processados: {r.get('tickers_processados', '?')}"
    )

    idade_dir = r.get("idade_direction_input_dias")
    idade_vol = r.get("idade_volatility_input_dias")
    linhas.append(f"Idade dos modelos (dias) — direction: {idade_dir} | volatility: {idade_vol}")
    try:
        if float(idade_dir) > 20 or float(idade_vol) > 20:
            linhas.append("Aviso: modelos com mais de 20 dias — considere retreinar e subir novos direction_best/volatility_best.")
    except (TypeError, ValueError):
        pass
    linhas.append("")

    if df_ordem.empty:
        linhas.append("Nenhum sinal operacional pós-filtro hoje.")
    else:
        for _, row in df_ordem.head(LIMITE_TICKERS_MSG).iterrows():
            estrategia = row.get("estrategia_preferida", "")
            esp = _fmt(row.get("esperanca_opcao_dp"), 3)
            linhas.append(f"- {row['ticker']} — {row['ordem']} — {estrategia} (esp={esp}dp)")
        if len(df_ordem) > LIMITE_TICKERS_MSG:
            linhas.append(f"... +{len(df_ordem) - LIMITE_TICKERS_MSG} no arquivo completo")

    try:
        avisos = pd.read_excel(arq, sheet_name="avisos_saida")
        avisos = avisos[avisos["aviso_saida"].notna() & (avisos["aviso_saida"].astype(str).str.strip() != "")]
        if not avisos.empty:
            linhas.append("")
            linhas.append("Avisos de saída:")
            for _, row in avisos.head(LIMITE_TICKERS_MSG).iterrows():
                linhas.append(f"- {row['ticker']} — {row['aviso_saida']}")
    except Exception:
        pass

    linhas.append("")
    linhas.append(f"Arquivo completo commitado em saidas/ordens_dia/{arq.name}")
    return "\n".join(linhas)


def _msg_realizados() -> str:
    arq = _arquivo_mais_recente(PASTA / "saidas" / "realizados", "realizados_*.xlsx")
    if arq is None:
        return f"MIA — Realizados {HOJE:%d/%m/%Y}\nNenhum realizados_*.xlsx encontrado em saidas/realizados/."

    agg = pd.read_excel(arq, sheet_name="agregados")
    linhas = [f"MIA — Realizados {HOJE:%d/%m/%Y}", f"Arquivo: {arq.name}", ""]

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
