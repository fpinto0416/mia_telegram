'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Agosto 2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

Gera o painel HTML "Panorama de alvos do dia" a partir dos dados mais
recentes coletados por download_fundamentals.py:

- Top 10 ações mais abaixo do preço-alvo mínimo (target low) dos analistas
- Top 10 ações mais abaixo do preço-alvo médio (target mean) dos analistas
- Um seletor (dropdown) com todos os tickers coletados no dia, para
  consultar os indicadores fundamentalistas e o preço-alvo de qualquer
  ativo individualmente

Roda depois de download_fundamentals.py (usa só os CSVs já salvos, não bate
na rede) e escreve o resultado em dados/panorama_de_alvos.html, que fica
versionado no repo — sempre reflete o último dia coletado.
------------------------------------------------------------
'''

import html as html_lib
import json
import os
import re
from datetime import datetime

import pandas as pd

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
PASTA_DADOS = os.path.join(PASTA_PROJETO, "dados")
PASTA_TICKERS = os.path.join(PASTA_PROJETO, "tickers")

CSV_SNAPSHOT = os.path.join(PASTA_DADOS, "snapshot_diario.csv")
CSV_ANALYST = os.path.join(PASTA_DADOS, "analyst_insights.csv")
CSV_TICKERS = os.path.join(PASTA_TICKERS, "ibra_composicao.csv")
SAIDA_HTML = os.path.join(PASTA_DADOS, "panorama_de_alvos.html")

REC_MAP = {
    "strong_buy": ("good", "Compra forte"),
    "buy": ("good", "Compra"),
    "hold": ("warning", "Neutro"),
    "sell": ("critical", "Venda"),
    "strong_sell": ("critical", "Venda forte"),
    "underperform": ("critical", "Abaixo do mercado"),
    "none": ("muted", "Sem consenso"),
}

FUNDAMENTOS_LABELS = [
    ("market_cap", "Market cap", "moeda_grande"),
    ("pl", "P/L", "num"),
    ("pl_forward", "P/L forward", "num"),
    ("pvp", "P/VP", "num"),
    ("psr", "PSR", "num"),
    ("ev_ebitda", "EV/EBITDA", "num"),
    ("roe", "ROE", "pct_frac"),
    ("roa", "ROA", "pct_frac"),
    ("margem_liquida", "Margem líquida", "pct_frac"),
    ("margem_bruta", "Margem bruta", "pct_frac"),
    ("margem_ebitda", "Margem EBITDA", "pct_frac"),
    ("dividend_yield", "Dividend yield", "pct_direta"),
    ("payout_ratio", "Payout", "pct_direta"),
    ("divida_patrimonio", "Dívida/Patrimônio", "num"),
    ("lpa", "LPA", "moeda"),
    ("lpa_forward", "LPA forward", "moeda"),
    ("beta", "Beta", "num"),
    ("volume_medio", "Volume médio", "inteiro_grande"),
]


def clean_nome(n, ticker):
    if not isinstance(n, str) or not n.strip():
        return ticker
    return re.sub(r"\s+", " ", n).strip()


def fmt_num(v, casas=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    s = f"{v:,.{casas}f}".replace(",", "§").replace(".", ",").replace("§", ".")
    return s


def fmt_moeda(v):
    return "—" if v is None or pd.isna(v) else f"R$ {fmt_num(v)}"


def fmt_moeda_grande(v):
    if v is None or pd.isna(v):
        return "—"
    if v >= 1e9:
        return f"R$ {fmt_num(v / 1e9, 2)} bi"
    if v >= 1e6:
        return f"R$ {fmt_num(v / 1e6, 2)} mi"
    return f"R$ {fmt_num(v, 0)}"


def fmt_inteiro_grande(v):
    if v is None or pd.isna(v):
        return "—"
    if v >= 1e6:
        return f"{fmt_num(v / 1e6, 2)} mi"
    if v >= 1e3:
        return f"{fmt_num(v / 1e3, 1)} mil"
    return fmt_num(v, 0)


def fmt_pct_direta(v):
    """valor já vem como percentual (ex.: 10.27 = 10,27%)"""
    return "—" if v is None or pd.isna(v) else f"{fmt_num(v)}%"


def fmt_pct_frac(v):
    """valor vem como fração (ex.: 0.184 = 18,4%)"""
    return "—" if v is None or pd.isna(v) else f"{fmt_num(v * 100, 1)}%"


def fmt_pct_signed(v):
    if v is None or pd.isna(v):
        return "—"
    s = fmt_num(v, 1)
    return f"+{s}%" if v >= 0 else f"{s}%"


FMT_FUNCS = {
    "num": fmt_num,
    "moeda": fmt_moeda,
    "moeda_grande": fmt_moeda_grande,
    "inteiro_grande": fmt_inteiro_grande,
    "pct_direta": fmt_pct_direta,
    "pct_frac": fmt_pct_frac,
}


def carregar_dados():
    snap = pd.read_csv(CSV_SNAPSHOT)
    ana = pd.read_csv(CSV_ANALYST)
    data_ref = snap["data"].max()
    snap = snap[snap["data"] == data_ref].copy()
    ana = ana[ana["data"] == data_ref].copy()
    df = snap.merge(ana.drop(columns=["data"]), on="ticker", how="left", suffixes=("", "_an"))
    total_universo = len(pd.read_csv(CSV_TICKERS)) if os.path.exists(CSV_TICKERS) else len(df)
    return df, data_ref, total_universo


def calc_bar_positions(preco, low, mean, high):
    high = high if pd.notna(high) else max(x for x in [mean, low] if pd.notna(x))
    scale_min = min(preco, low) * 0.90
    scale_max = max(high, mean, preco) * 1.06
    span = scale_max - scale_min if scale_max > scale_min else 1

    def pos(v):
        return round(max(0.0, min(100.0, (v - scale_min) / span * 100)), 2)

    price_pos, low_pos, mean_pos, high_pos = pos(preco), pos(low), pos(mean), pos(high)
    range_left = min(low_pos, high_pos)
    range_width = max(abs(high_pos - low_pos), 1.2)
    gap_left = min(price_pos, low_pos)
    gap_width = max(abs(low_pos - price_pos), 0.5)
    return {
        "price_pos": price_pos, "mean_pos": mean_pos,
        "range_left": range_left, "range_width": range_width,
        "gap_left": gap_left, "gap_width": gap_width,
    }


def montar_registro_ativo(r):
    ticker = r["ticker"]
    preco = r.get("preco_atual")
    low, mean, high = r.get("target_low"), r.get("target_mean"), r.get("target_high")

    tem_alvo = pd.notna(preco) and pd.notna(low) and pd.notna(mean)
    bar = calc_bar_positions(float(preco), float(low), float(mean), float(high) if pd.notna(high) else None) if tem_alvo else None

    rec_key = r.get("recommendation_key") if isinstance(r.get("recommendation_key"), str) else "none"
    status, rec_label = REC_MAP.get(rec_key, ("muted", "Sem consenso"))

    fundamentos = []
    for campo, label, tipo in FUNDAMENTOS_LABELS:
        fundamentos.append({"label": label, "valor": FMT_FUNCS[tipo](r.get(campo))})

    dist_low_pct = float((low - preco) / preco * 100) if tem_alvo else None
    dist_mean_pct = float((mean - preco) / preco * 100) if tem_alvo else None

    return {
        "ticker": ticker,
        "nome": clean_nome(r.get("nome"), ticker),
        "setor": r.get("setor") if isinstance(r.get("setor"), str) else "—",
        "industria": r.get("industria") if isinstance(r.get("industria"), str) else "—",
        "preco_fmt": fmt_moeda(preco),
        "rec_status": status,
        "rec_label": rec_label,
        "n_analistas": int(r["num_analistas"]) if pd.notna(r.get("num_analistas")) else None,
        "tem_alvo": bool(tem_alvo),
        "low_fmt": fmt_moeda(low) if tem_alvo else None,
        "mean_fmt": fmt_moeda(mean) if tem_alvo else None,
        "high_fmt": fmt_moeda(high) if pd.notna(high) else None,
        "dist_low_pct": dist_low_pct,
        "dist_mean_pct": dist_mean_pct,
        "dist_low_fmt": fmt_pct_signed(dist_low_pct) if tem_alvo else None,
        "dist_mean_fmt": fmt_pct_signed(dist_mean_pct) if tem_alvo else None,
        "bar": bar,
        "fundamentos": fundamentos,
    }


def row_html(rank, reg, dist_key):
    bar = reg["bar"]
    nome_esc = html_lib.escape(reg["nome"])
    setor_esc = html_lib.escape(reg["setor"])
    return f"""
      <li class="row">
        <div class="row-rank">{rank:02d}</div>
        <div class="row-id">
          <span class="ticker">{reg['ticker']}</span>
          <span class="nome">{nome_esc}</span>
          <span class="setor">{setor_esc}</span>
        </div>
        <div class="row-viz">
          <div class="track" title="Preço {reg['preco_fmt']} · Low {reg['low_fmt']} · Média {reg['mean_fmt']} · High {reg['high_fmt']}">
            <div class="range-fill" style="left:{bar['range_left']}%; width:{bar['range_width']}%;"></div>
            <div class="mean-tick" style="left:{bar['mean_pos']}%;"></div>
            <div class="gap-line" style="left:{bar['gap_left']}%; width:{bar['gap_width']}%;"></div>
            <div class="price-marker" style="left:{bar['price_pos']}%;"></div>
          </div>
          <div class="track-labels">
            <span>{reg['preco_fmt']} hoje</span>
            <span class="track-labels-right">low {reg['low_fmt']} · média {reg['mean_fmt']} · high {reg['high_fmt']}</span>
          </div>
        </div>
        <div class="row-side">
          <span class="pill pill-{reg['rec_status']}">{reg['rec_label']}</span>
          <span class="n-analistas">{(str(reg['n_analistas']) + ' analistas') if reg['n_analistas'] else 'cobertura n/d'}</span>
        </div>
        <div class="row-metric">{reg[dist_key]}</div>
      </li>"""


def gerar():
    df, data_ref, total_universo = carregar_dados()
    registros = {r["ticker"]: montar_registro_ativo(r) for _, r in df.iterrows()}

    com_alvo = {t: r for t, r in registros.items() if r["tem_alvo"]}
    cobertura = len(com_alvo)

    top_low = sorted(com_alvo.values(), key=lambda r: r["dist_low_pct"], reverse=True)[:10]
    top_mean = sorted(com_alvo.values(), key=lambda r: r["dist_mean_pct"], reverse=True)[:10]

    top_low_html = "\n".join(row_html(i + 1, r, "dist_low_fmt") for i, r in enumerate(top_low))
    top_mean_html = "\n".join(row_html(i + 1, r, "dist_mean_fmt") for i, r in enumerate(top_mean))

    dist_medias = sorted(r["dist_mean_pct"] for r in com_alvo.values())
    upside_mediano = dist_medias[len(dist_medias) // 2] if dist_medias else 0
    maior_desconto = max((r["dist_low_pct"] for r in com_alvo.values()), default=0)

    data_ref_fmt = datetime.strptime(data_ref, "%Y-%m-%d").strftime("%d/%m/%Y")
    gerado_em = datetime.now().strftime("%d/%m/%Y às %H:%M")

    tickers_ordenados = sorted(registros.keys())
    options_html = "\n".join(
        f'<option value="{t}">{t} — {html_lib.escape(registros[t]["nome"])}</option>'
        for t in tickers_ordenados
    )
    ticker_default = top_low[0]["ticker"] if top_low else tickers_ordenados[0]

    registros_json = json.dumps(registros, ensure_ascii=False)

    with open(os.path.join(PASTA_SCRIPTS, "_template_panorama.html"), encoding="utf-8") as f:
        template = f.read()

    # Substituição por tokens (não .format) — o template tem CSS/JS cheios de
    # chaves, então escapar {{ }} seria mais frágil que só trocar marcadores únicos.
    substituicoes = {
        "__DATA_REF_FMT__": data_ref_fmt,
        "__GERADO_EM__": gerado_em,
        "__TOTAL_UNIVERSO__": str(total_universo),
        "__COBERTURA__": str(cobertura),
        "__UPSIDE_MEDIANO_FMT__": fmt_pct_signed(upside_mediano),
        "__MAIOR_DESCONTO_FMT__": fmt_pct_signed(maior_desconto),
        "__TOP_LOW_HTML__": top_low_html,
        "__TOP_MEAN_HTML__": top_mean_html,
        "__OPTIONS_HTML__": options_html,
        "__TICKER_DEFAULT__": ticker_default,
        "__REGISTROS_JSON__": registros_json,
    }
    html = template
    for token, valor in substituicoes.items():
        html = html.replace(token, valor)

    os.makedirs(PASTA_DADOS, exist_ok=True)
    with open(SAIDA_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Panorama gerado em {SAIDA_HTML} ({len(registros)} ativos, {cobertura} com preço-alvo)")


if __name__ == "__main__":
    gerar()
