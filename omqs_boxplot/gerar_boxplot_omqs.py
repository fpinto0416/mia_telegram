'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Agosto 2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

Gera o bloco HTML/SVG do box plot mensal usado no artefato "Painel de
Sinais — MIA · OMQS · Hilo · Sinal Diário · OMQS Futuros" (card "OMQS —
api_OMQS"). Não faz parte de nenhum pipeline automático deste repo — é um
utilitário manual: toda vez que quiser atualizar o box plot desse artefato,
rode este script com os exports mais recentes do api_OMQS.

Entradas (exportadas do repo api_OMQS, não deste repo):
  1. resultados_AAAA_MM_DD.xlsx — precisa das abas 'trades_fechados'
     (ticker, ordem, data_entrada, preco_entrada, data_saida, preco_saida,
     retorno, acerto) e 'posicoes_abertas' (ticker, ordem, data_entrada,
     preco_entrada).
  2. memoria_ordens.csv — snapshot diário de preços por ticker (usa a
     coluna 'close' mais recente por ticker pra marcar a mercado as
     posições abertas).

Metodologia (validada em 24/08/2026 contra os extremos reais do painel:
+36,50% e -13,33% bateram exatos):
  - trades fechados: usa o 'retorno' já calculado no xlsx, direto.
  - posições abertas: marca a mercado com o close mais recente do ticker
    em memoria_ordens.csv.
      VENDA:  retorno = (preco_entrada - close) / preco_entrada
      COMPRA: retorno = (close - preco_entrada) / preco_entrada
  - cada trade (fechado ou aberto) é agrupado pelo mês da SUA DATA DE
    ENTRADA (coorte por mês de entrada, não de saída) — assim fechados e
    abertos do mesmo mês entram na mesma caixa.

Saída: escreve o <div class="boxplot-block">...</div> pronto pra colar no
artefato (mesmas classes CSS já publicadas nele: bp-svg, bp-box, bp-median,
bp-mean, bp-dot-pos/neg/zero, bp-label-extreme, bp-label-median,
bp-axis-month, bp-axis-sub — não precisa mexer no <style> de novo, só
substituir o conteúdo do <div class="boxplot-block">).

Uso:
    python gerar_boxplot_omqs.py resultados_2026_08_24.xlsx memoria_ordens.csv
    python gerar_boxplot_omqs.py resultados_2026_08_24.xlsx memoria_ordens.csv --out bloco.html
------------------------------------------------------------
'''

import argparse
import sys

import numpy as np
import pandas as pd


def carregar_trades(caminho_xlsx: str, caminho_memoria: str) -> pd.DataFrame:
    xl = pd.ExcelFile(caminho_xlsx)
    tf = xl.parse("trades_fechados")
    pa = xl.parse("posicoes_abertas")
    memoria = pd.read_csv(caminho_memoria)

    ultimo_close = memoria.sort_values("pregao_brt").groupby("ticker").last()[["pregao_brt", "close"]]

    pa = pa.merge(ultimo_close, left_on="ticker", right_index=True, how="left")
    sem_preco = pa[pa["close"].isna()]
    if not sem_preco.empty:
        print(
            f"AVISO: {len(sem_preco)} posição(ões) aberta(s) sem preço em "
            f"{caminho_memoria}, vão ficar de fora do box plot: "
            f"{sem_preco['ticker'].tolist()}",
            file=sys.stderr,
        )
        pa = pa.dropna(subset=["close"])

    def mtm(row):
        if row["ordem"] == "VENDA":
            return (row["preco_entrada"] - row["close"]) / row["preco_entrada"]
        return (row["close"] - row["preco_entrada"]) / row["preco_entrada"]

    pa["retorno"] = pa.apply(mtm, axis=1)
    pa["status"] = "aberta_mtm"
    tf["status"] = "fechada"

    combo = pd.concat(
        [tf[["ticker", "ordem", "data_entrada", "retorno", "status"]],
         pa[["ticker", "ordem", "data_entrada", "retorno", "status"]]],
        ignore_index=True,
    )
    combo["data_entrada"] = pd.to_datetime(combo["data_entrada"])
    combo["retorno_pct"] = combo["retorno"] * 100
    return combo


MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def estatisticas_por_mes(combo: pd.DataFrame) -> list[dict]:
    combo = combo.copy()
    combo["periodo"] = combo["data_entrada"].dt.to_period("M")
    meses = []
    for periodo, g in sorted(combo.groupby("periodo")):
        arr = np.sort(g["retorno_pct"].to_numpy())
        q1, med, q3 = np.percentile(arr, [25, 50, 75])
        meses.append({
            "chave": str(periodo),
            "label": f"{MESES_PT[periodo.month]}/{str(periodo.year)[-2:]}",
            "pontos": arr.tolist(),
            "min": float(arr.min()),
            "q1": float(q1),
            "med": float(med),
            "q3": float(q3),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "n": len(arr),
            "acerto": float((arr > 0).mean() * 100),
        })
    return meses


def _nice_bounds(vmin: float, vmax: float) -> tuple[float, float, float]:
    """Arredonda o domínio do eixo Y pra múltiplos "redondos" com folga, e
    escolhe um passo de grade que dê entre 4 e 8 linhas."""
    span = max(vmax - vmin, 1e-6)
    passo_bruto = span / 6
    for passo in (1, 2, 5, 10, 20, 25, 50, 100):
        if passo >= passo_bruto:
            break
    y_min = np.floor((vmin - span * 0.12) / passo) * passo
    y_max = np.ceil((vmax + span * 0.12) / passo) * passo
    return float(y_min), float(y_max), float(passo)


def gerar_svg(meses: list[dict]) -> str:
    todos_pontos = [v for m in meses for v in m["pontos"]]
    y_min, y_max, passo = _nice_bounds(min(todos_pontos), max(todos_pontos))

    n_col = len(meses)
    col_w = 172
    PAD_L, PAD_R, PAD_T, PAD_B = 54, 24, 18, 44
    W = PAD_L + PAD_R + col_w * n_col
    H = 320
    plot_l, plot_r, plot_t, plot_b = PAD_L, W - PAD_R, PAD_T, H - PAD_B

    def y(v):
        return plot_b - (v - y_min) / (y_max - y_min) * (plot_b - plot_t)

    centers = [plot_l + col_w * (i + 0.5) for i in range(n_col)]
    BOX_W, CAP_W = 64, 22
    rng = np.random.default_rng(7)

    partes = []

    grid_val = y_min
    while grid_val <= y_max + 1e-9:
        yy = y(grid_val)
        zero = abs(grid_val) < 1e-9
        stroke = "var(--text-faint)" if zero else "var(--border)"
        sw = 1.4 if zero else 1
        dash = "" if zero else ' stroke-dasharray="2 3"'
        rotulo = "0%" if zero else f"{grid_val:+.0f}%"
        partes.append(
            f'<line x1="{plot_l}" y1="{yy:.1f}" x2="{plot_r}" y2="{yy:.1f}" '
            f'stroke="{stroke}" stroke-width="{sw}"{dash}/>'
        )
        partes.append(
            f'<text x="{plot_l - 10}" y="{yy:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" class="bp-axis">{rotulo}</text>'
        )
        grid_val += passo

    def fmt(v):
        return f"{v:+.2f}".replace(".", ",") + "%"

    for mes, cx in zip(meses, centers):
        pts = mes["pontos"]
        offsets = rng.uniform(-1, 1, size=len(pts)) * (BOX_W * 0.62)

        y_lo, y_q1, y_med, y_q3, y_hi = (
            y(mes["min"]), y(mes["q1"]), y(mes["med"]), y(mes["q3"]), y(mes["max"])
        )

        partes.append(f'<line x1="{cx}" y1="{y_lo:.1f}" x2="{cx}" y2="{y_q1:.1f}" class="bp-whisker"/>')
        partes.append(f'<line x1="{cx}" y1="{y_q3:.1f}" x2="{cx}" y2="{y_hi:.1f}" class="bp-whisker"/>')
        partes.append(f'<line x1="{cx-CAP_W/2}" y1="{y_lo:.1f}" x2="{cx+CAP_W/2}" y2="{y_lo:.1f}" class="bp-cap"/>')
        partes.append(f'<line x1="{cx-CAP_W/2}" y1="{y_hi:.1f}" x2="{cx+CAP_W/2}" y2="{y_hi:.1f}" class="bp-cap"/>')
        partes.append(
            f'<rect x="{cx-BOX_W/2:.1f}" y="{y_q3:.1f}" width="{BOX_W}" '
            f'height="{max(y_q1-y_q3, 1):.1f}" rx="4" class="bp-box"/>'
        )
        partes.append(f'<line x1="{cx-BOX_W/2:.1f}" y1="{y_med:.1f}" x2="{cx+BOX_W/2:.1f}" y2="{y_med:.1f}" class="bp-median"/>')

        for val, off in zip(pts, offsets):
            cls = "bp-dot-pos" if val > 0 else ("bp-dot-neg" if val < 0 else "bp-dot-zero")
            partes.append(f'<circle cx="{cx+off:.1f}" cy="{y(val):.1f}" r="3.4" class="{cls}"/>')

        y_mean, s = y(mes["mean"]), 5.5
        partes.append(
            f'<path d="M {cx} {y_mean-s:.1f} L {cx+s:.1f} {y_mean:.1f} '
            f'L {cx} {y_mean+s:.1f} L {cx-s:.1f} {y_mean:.1f} Z" class="bp-mean"/>'
        )

        partes.append(f'<text x="{cx}" y="{y_lo+16:.1f}" text-anchor="middle" class="bp-label-extreme">{fmt(mes["min"])}</text>')
        partes.append(f'<text x="{cx}" y="{y_hi-8:.1f}" text-anchor="middle" class="bp-label-extreme">{fmt(mes["max"])}</text>')
        partes.append(f'<text x="{cx+BOX_W/2+8:.1f}" y="{y_med+3.5:.1f}" text-anchor="start" class="bp-label-median">{fmt(mes["med"])} med.</text>')

        partes.append(f'<text x="{cx}" y="{plot_b+20:.1f}" text-anchor="middle" class="bp-axis-month">{mes["label"]}</text>')
        partes.append(f'<text x="{cx}" y="{plot_b+34:.1f}" text-anchor="middle" class="bp-axis-sub">N={mes["n"]} · acerto {mes["acerto"]:.0f}%</text>')

    inner = "\n    ".join(partes)
    return (
        f'<svg viewBox="0 0 {W} {H}" class="bp-svg" role="img" '
        f'aria-label="Box plot dos resultados mensais do OMQS">\n    {inner}\n  </svg>'
    )


def gerar_bloco_html(meses: list[dict], periodo_txt: str) -> str:
    svg = gerar_svg(meses)
    return f'''<div class="boxplot-block">
        <div class="boxplot-head">
          <span class="boxplot-title">Distribuição mensal dos resultados (combinado)</span>
          <span class="boxplot-sub">
            Cada ponto é um trade (fechado + aberto MTM), agrupado pelo mês de <b>entrada</b>.
            A caixa vai do P25 ao P75, o traço é a mediana, o losango é a média, os fios vão
            até o melhor e o pior trade do mês. Base: <code>trades_fechados</code> +
            <code>posicoes_abertas</code> marcadas a mercado com o último fechamento
            disponível — dados de {periodo_txt}.
          </span>
        </div>
        <div class="bp-svg-wrap">
          {svg}
        </div>
        <div class="boxplot-legend">
          <span><span class="sw" style="background:var(--pos)"></span>trade positivo</span>
          <span><span class="sw" style="background:var(--neg)"></span>trade negativo</span>
          <span><span class="sw" style="background:var(--accent)"></span>caixa = P25–P75 · losango = média</span>
        </div>
      </div>'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx", help="resultados_AAAA_MM_DD.xlsx exportado do api_OMQS")
    ap.add_argument("memoria_csv", help="memoria_ordens.csv exportado do api_OMQS")
    ap.add_argument("--out", default="boxplot_bloco.html", help="arquivo de saída (default: boxplot_bloco.html)")
    args = ap.parse_args()

    combo = carregar_trades(args.xlsx, args.memoria_csv)
    meses = estatisticas_por_mes(combo)

    print(f"{len(combo)} trades combinados (fechados + abertos MTM) em {len(meses)} mês(es):\n")
    for m in meses:
        print(
            f"  {m['label']:<10} N={m['n']:<3} acerto={m['acerto']:5.1f}%  "
            f"min={m['min']:+7.2f}%  P25={m['q1']:+7.2f}%  med={m['med']:+7.2f}%  "
            f"P75={m['q3']:+7.2f}%  max={m['max']:+7.2f}%  media={m['mean']:+7.2f}%"
        )

    print(
        f"\nExtremos do conjunto todo: melhor trade {max(combo['retorno_pct']):+.2f}% · "
        f"pior trade {min(combo['retorno_pct']):+.2f}%"
        "\n(confira esses dois valores contra 'Ganho máx' e 'Perda mín' do painel — "
        "se baterem, a base de dados está alinhada com o que o card já mostra.)"
    )

    dt_min = combo["data_entrada"].min().strftime("%d/%m/%Y")
    dt_max = combo["data_entrada"].max().strftime("%d/%m/%Y")
    periodo_txt = f"{dt_min} a {dt_max}"

    bloco = gerar_bloco_html(meses, periodo_txt)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(bloco)
    print(f"\nBloco HTML salvo em {args.out} — cole no lugar do <div class=\"boxplot-block\">...</div> existente no artefato.")


if __name__ == "__main__":
    main()
