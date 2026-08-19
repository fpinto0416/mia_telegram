"""
MIA — Realizados: ponte previsão → payoff real
================================================
Cruza sinais históricos dos ordem_dia_* com preços posteriores.
Calcula payoff real de cada sinal após 20 PREGÕES (não dias corridos).

Uso:
    python realizados.py

Output:
    realizados_YYYY_MM_DD.xlsx  (abas: realizados, agregados, agregados_magnitude)
"""

import re
import sys
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from executor import _importar_database_local, ESTRUTURAS_OPC, FATOR_CUSTO_2_PERNAS, CARRY_20

PASTA  = Path(__file__).parent
HOJE   = date.today()
SAIDA  = PASTA / "saidas" / "realizados" / f"realizados_{HOJE:%Y_%m_%d}.xlsx"
SAIDA.parent.mkdir(parents=True, exist_ok=True)
SQRT_H = np.sqrt(20)

# ── Inferência de versão + flag de contaminação ──────────────────────────────
# 09/06 é a fronteira MAIS IMPORTANTE: bug rel_rsl20 corrigido nessa data.
# Antes disso, direc_prediction estava inflado para CALL em todos os tickers
# (feature rel_rsl20 sem o -1). Sinais pré-09/06 = previsão direcional INVÁLIDA.
# Rótulo CONTAMINADO no nome da versão é intencional — auto-documenta na planilha.
DATA_FIX_RSL = date(2026, 6, 9)  # 1ª leitura limpa pós-correção rel_rsl20

_MARCOS_VERSAO = [
    (date(2026, 6, 13), "v5.5_pos_estruturas"),      # +seleção estruturas +elegibilidade
    (date(2026, 6, 12), "v5.4_pos_gate_esp"),        # +gate esperança +métricas opção
    (date(2026, 6, 10), "v5.3_pos_r1r2"),            # +gate vol 2 caminhos +recalibração R2
    (date(2026, 6,  9), "v5.2_pos_rslfix"),          # bug rel_rsl20 CORRIGIDO — 1ª leitura limpa
    (date(2026, 1,  1), "v5.0_CONTAMINADO_rslfix"),  # pré-bug: direc_prediction inválido
]

def _inferir_versao(data_sinal: date) -> str:
    for marco, nome in _MARCOS_VERSAO:
        if data_sinal >= marco:
            return nome
    return "desconhecida"


# ── Descoberta de arquivos ordem_dia ─────────────────────────────────────────

def _extrair_data(path: Path):
    m = re.search(r"(\d{4})_(\d{2})_(\d{2})", path.stem)
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _descobrir_arquivos() -> list[tuple[date, Path]]:
    pares = [(d, p) for p in sorted((PASTA / "saidas" / "ordens_dia").glob("ordem_dia_*executor_v5_5.xlsx"))
             if (d := _extrair_data(p))]
    return sorted(pares)


# ── Carga de preços com cache ─────────────────────────────────────────────────
# C1: normaliza índice para DatetimeIndex (executor devolve datetime.date)

_cache_precos: dict[str, pd.DataFrame] = {}

def _precos(ticker: str) -> pd.DataFrame:
    if ticker not in _cache_precos:
        try:
            df = _importar_database_local(ticker, data_ref=HOJE)
            if df is not None and not df.empty:
                df = df.sort_index()
                df.index = pd.to_datetime(df.index)   # date → Timestamp
                _cache_precos[ticker] = df
        except Exception:
            pass
    return _cache_precos.get(ticker, pd.DataFrame())


# ── Payoff de estrutura sobre M escalar ──────────────────────────────────────

def _payoff_estrutura(M: float, nome: str) -> float:
    e = ESTRUTURAS_OPC[nome]
    custo_ef = e["custo"] * (FATOR_CUSTO_2_PERNAS if e["k_short"] is not None else 1.0)
    pay = max(M - e["k_long"], 0.0)
    if e["k_short"] is not None:
        pay -= max(M - e["k_short"], 0.0)
    return round(pay - custo_ef, 4)


def _profit_factor(retornos: pd.Series) -> float:
    """soma dos ganhos / |soma das perdas| -- mesma convenção usada em
    api_OMQS/hilo (>1 = ganhos pesam mais que perdas). NaN é ignorado
    (dropna) em vez de contar como perda -- sinais abertos/sem estrutura
    não devem puxar o denominador pra baixo."""
    r = pd.Series(retornos).dropna()
    ganhos = r[r > 0].sum()
    perdas = r[r <= 0].sum()
    return round(float(ganhos / abs(perdas)), 3) if perdas != 0 else float("nan")


# ── Processa um sinal ─────────────────────────────────────────────────────────

def _processar_sinal(row: pd.Series, data_sinal: date) -> dict | None:
    ticker = str(row.get("ticker", ""))
    ordem  = str(row.get("ordem", ""))
    lado   = 1 if "CALL" in ordem else -1

    df_p = _precos(ticker)
    if df_p.empty:
        return None

    idx_pregoes = df_p.index
    ts_sinal    = pd.Timestamp(data_sinal)
    pos_sinal   = idx_pregoes.searchsorted(ts_sinal)

    # C5a: validar que a data do sinal é pregão na base
    data_eh_pregao = int(pos_sinal < len(idx_pregoes) and idx_pregoes[pos_sinal] == ts_sinal)
    if pos_sinal >= len(idx_pregoes):
        return None
    # se não for pregão exato, avança para o próximo (e registra)
    if not data_eh_pregao:
        if pos_sinal >= len(idx_pregoes):
            return None

    close_t = float(df_p["Close"].iloc[pos_sinal])
    ret_d   = df_p["Close"].pct_change()
    vol_t   = float(ret_d.ewm(span=22, adjust=False).std().iloc[pos_sinal])
    if vol_t == 0 or np.isnan(vol_t):
        return None

    pos_t20 = pos_sinal + 20
    # C2: só é fechado quando 20 pregões existem na base
    status            = "fechado" if pos_t20 < len(idx_pregoes) else "aberto"
    pregoes_decorridos = 20 if status == "fechado" else (len(idx_pregoes) - 1 - pos_sinal)

    # C2: payoffs só para sinais fechados; abertos recebem NaN
    if status == "fechado":
        close_t20 = float(df_p["Close"].iloc[pos_t20])
        ret_real  = close_t20 / close_t - 1
        # Ajuste de carry: mesmo critério do executor (forward adjustment por CARRY_20)
        _carry_dp_real     = CARRY_20 / (vol_t * SQRT_H)
        _convexity_dp_real = vol_t * SQRT_H if lado < 0 else 0.0
        M_real    = round(lado * ret_real / (vol_t * SQRT_H) - lado * _carry_dp_real + _convexity_dp_real, 4)
        payoffs   = {f"payoff_{n}": _payoff_estrutura(M_real, n) for n in ESTRUTURAS_OPC}
        # C3: lucro por estrutura derivado do próprio payoff (break-even correto por estrutura)
        lucros    = {f"lucro_{n}": int(payoffs[f"payoff_{n}"] > 0) for n in ESTRUTURAS_OPC}
        acerto_direcao = int(M_real > 0)
        # retorno da AÇÃO pura na direção do sinal (sem estrutura de opção
        # nenhuma por cima) -- só o carry/convexity do M_real são específicos
        # de opção; retorno_acao é literalmente lado * variação de preço.
        retorno_acao   = round(lado * ret_real, 4)
        acerto_acao    = int(retorno_acao > 0)
        close_t20_out  = round(close_t20, 2)
        M_parcial      = np.nan
    else:
        close_t20      = np.nan
        M_real         = np.nan
        payoffs        = {f"payoff_{n}": np.nan for n in ESTRUTURAS_OPC}
        lucros         = {f"lucro_{n}": np.nan for n in ESTRUTURAS_OPC}
        acerto_direcao = np.nan
        retorno_acao   = np.nan
        acerto_acao    = np.nan
        close_t20_out  = np.nan
        close_parcial  = float(df_p["Close"].iloc[-1])
        M_parcial      = round(lado * (close_parcial / close_t - 1) / (vol_t * SQRT_H), 4)

    # C4: marca se a linha tem esperança in-sample para filtrar correlação
    tem_esp = int("esperanca_opcao_dp" in row.index and pd.notna(row.get("esperanca_opcao_dp")))

    versao_gravada = str(row.get("versao_executor", "") or "")
    if versao_gravada:
        versao_out, versao_inferida = versao_gravada, 0
    else:
        versao_out, versao_inferida = _inferir_versao(data_sinal), 1
    contaminado_rsl = int(data_sinal < DATA_FIX_RSL)

    return {
        "data_sinal":            data_sinal,
        "ticker":                ticker,
        "ordem":                 ordem,
        "operacao_nivel_live":   str(row.get("operacao_nivel_live", "")),
        "razao_sinal":           row.get("razao_sinal", np.nan),
        "vol_prediction_ratio":  row.get("vol_prediction_ratio", np.nan),
        "esperanca_opcao_dp":    row.get("esperanca_opcao_dp", np.nan),
        "tem_esperanca_insample": tem_esp,
        "versao":                versao_out,
        "versao_inferida":       versao_inferida,
        "contaminado_rsl":       contaminado_rsl,
        "veto_gate_historico":   int(row.get("veto_gate_historico", 0)),
        "estrutura_otima":       str(row.get("estrutura_otima", "")),
        "data_sinal_eh_pregao":  data_eh_pregao,
        "close_t":               round(close_t, 2),
        "close_t20":             close_t20_out,
        "vol_t":                 round(vol_t, 6),
        "pregoes_decorridos":    pregoes_decorridos,
        "status":                status,
        "M_real":                M_real,
        "M_parcial":             M_parcial,
        "acerto_direcao":        acerto_direcao,
        "retorno_acao":          retorno_acao,
        "acerto_acao":           acerto_acao,
        **payoffs,
        **lucros,
    }


# ── Agregado por magnitude do movimento + estrutura de fato recomendada ──────
# (só roda sobre df_fech_limpo — mesma base que os demais agregados)

N_MIN_MAGNITUDE = 15  # amostra mínima por terço pra não reportar ruído como sinal


def _agregar_magnitude_e_estrutura(df_fech_limpo: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    if df_fech_limpo.empty:
        return pd.DataFrame(linhas)

    # ── corte 1: terços por |M_real| — acerto/payoff é melhor nos movimentos
    # grandes (objetivo do projeto) ou pior? ──────────────────────────────────
    sub = df_fech_limpo.dropna(subset=["M_real"]).copy()
    if len(sub) >= 3 * N_MIN_MAGNITUDE:
        sub["abs_M"] = sub["M_real"].abs()
        sub["terco_magnitude"] = pd.qcut(sub["abs_M"], 3, labels=["pequeno", "medio", "grande"])
        for terco, grp in sub.groupby("terco_magnitude", observed=True):
            ret_acao_medio = round(float(grp["retorno_acao"].mean()), 4)
            pf_acao        = _profit_factor(grp["retorno_acao"])
            for nome in ESTRUTURAS_OPC:
                linhas.append({
                    "grupo":              f"magnitude_{terco}",
                    "estrutura":          nome,
                    "n":                  len(grp),
                    "acerto_direcao":     round(float(grp["acerto_direcao"].mean()), 3),
                    "lucro_estrutura":    round(float(grp[f"lucro_{nome}"].mean()), 3),
                    "payoff_medio":       round(float(grp[f"payoff_{nome}"].mean()), 4),
                    "med_abs_M":          round(float(grp["abs_M"].median()), 4),
                    "pf_estrutura":       _profit_factor(grp[f"payoff_{nome}"]),
                    "retorno_acao_medio": ret_acao_medio,
                    "pf_acao":            pf_acao,
                })
    else:
        linhas.append({
            "grupo": "magnitude_amostra_insuficiente", "estrutura": "-",
            "n": len(sub), "acerto_direcao": np.nan, "lucro_estrutura": np.nan,
            "payoff_medio": np.nan, "med_abs_M": np.nan,
        })

    # ── corte 2: estrutura de fato recomendada pelo executor (estrutura_otima),
    # não uma fixa arbitrária — exclui sinais "nenhuma"/NaN (executor disse não
    # operar) ─────────────────────────────────────────────────────────────────
    valida = df_fech_limpo["estrutura_otima"].isin(ESTRUTURAS_OPC.keys())
    df_rec = df_fech_limpo[valida].copy()
    if not df_rec.empty:
        df_rec["payoff_escolhida"] = df_rec.apply(lambda r: r[f"payoff_{r['estrutura_otima']}"], axis=1)
        df_rec["lucro_escolhida"]  = df_rec.apply(lambda r: r[f"lucro_{r['estrutura_otima']}"], axis=1)

        linhas.append({
            "grupo": "estrutura_recomendada_geral", "estrutura": "(por sinal)",
            "n":                  len(df_rec),
            "acerto_direcao":     round(float(df_rec["acerto_direcao"].mean()), 3),
            "lucro_estrutura":    round(float(df_rec["lucro_escolhida"].mean()), 3),
            "payoff_medio":       round(float(df_rec["payoff_escolhida"].mean()), 4),
            "med_abs_M":          np.nan,
            "pf_estrutura":       _profit_factor(df_rec["payoff_escolhida"]),
            "retorno_acao_medio": round(float(df_rec["retorno_acao"].mean()), 4),
            "pf_acao":            _profit_factor(df_rec["retorno_acao"]),
        })
        for estrutura, grp in df_rec.groupby("estrutura_otima"):
            linhas.append({
                "grupo": "estrutura_recomendada_por_tipo", "estrutura": estrutura,
                "n":                  len(grp),
                "acerto_direcao":     round(float(grp["acerto_direcao"].mean()), 3),
                "lucro_estrutura":    round(float(grp["lucro_escolhida"].mean()), 3),
                "payoff_medio":       round(float(grp["payoff_escolhida"].mean()), 4),
                "med_abs_M":          np.nan,
                "pf_estrutura":       _profit_factor(grp["payoff_escolhida"]),
                "retorno_acao_medio": round(float(grp["retorno_acao"].mean()), 4),
                "pf_acao":            _profit_factor(grp["retorno_acao"]),
            })

    n_sem_estrutura = int((~valida).sum())
    linhas.append({
        "grupo": "sem_estrutura_recomendada_nenhuma_ou_na", "estrutura": "-",
        "n": n_sem_estrutura, "acerto_direcao": np.nan,
        "lucro_estrutura": np.nan, "payoff_medio": np.nan, "med_abs_M": np.nan,
    })

    return pd.DataFrame(linhas)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    arquivos = _descobrir_arquivos()
    if not arquivos:
        print("Nenhum arquivo ordem_dia encontrado.")
        return

    print(f"Encontrados {len(arquivos)} arquivos. Processando...")
    rows = []

    for data_sinal, path in arquivos:
        try:
            df_todos = pd.read_excel(path, sheet_name="todos")
        except Exception as e:
            print(f"  {path.name}: erro ao ler — {e}")
            continue

        mask_sinal = df_todos["ordem"].str.contains("Alerta|Compra|Monitorar", na=False)
        for _, row in df_todos[mask_sinal].iterrows():
            r = _processar_sinal(row, data_sinal)
            if r:
                rows.append(r)

    if not rows:
        print("Nenhum sinal processável encontrado.")
        return

    df_real = pd.DataFrame(rows).sort_values(["data_sinal", "ticker"]).reset_index(drop=True)
    df_fech = df_real[df_real["status"] == "fechado"]
    n_aberto = (df_real["status"] == "aberto").sum()

    print(f"\nTotal sinais: {len(df_real)}  |  fechados: {len(df_fech)}  |  abertos: {n_aberto}")
    print("Nota: N inclui sinais repetidos do mesmo ticker em dias distintos — observações não independentes.")

    # ── Split: limpos (pós-fix rel_rsl20 ≥09/06) vs contaminados ─────────────
    if "contaminado_rsl" in df_fech.columns:
        df_fech_limpo = df_fech[df_fech["contaminado_rsl"] == 0]
        df_fech_cont  = df_fech[df_fech["contaminado_rsl"] == 1]
    else:
        df_fech_limpo = df_fech
        df_fech_cont  = df_fech.iloc[:0]

    print(f"Fechados LIMPOS (pós-fix rel_rsl20 ≥09/06): {len(df_fech_limpo)}")
    print(f"Fechados CONTAMINADOS (pré-09/06, excluídos das médias): {len(df_fech_cont)}")
    if len(df_fech_limpo):
        print(f"Acerto direção (limpos): {df_fech_limpo['acerto_direcao'].mean():.1%}")
        print(f"Payoff naked_30 médio (limpos): {df_fech_limpo['payoff_naked_30'].mean():.4f}")
        print(f"Lucro naked_30 (payoff>0, limpos): {df_fech_limpo['lucro_naked_30'].mean():.1%}")
        print(f"Retorno médio da AÇÃO na direção do sinal (limpos): {df_fech_limpo['retorno_acao'].mean():.2%}")
        print(f"PF ação (limpos): {_profit_factor(df_fech_limpo['retorno_acao']):.2f}  |  "
              f"PF opção naked_30 (limpos): {_profit_factor(df_fech_limpo['payoff_naked_30']):.2f}")

    # ── Aba agregados (todos os _agg rodam sobre df_fech_limpo) ──────────────
    agg_rows = []

    def _agg(label: str, mask: pd.Series):
        sub = df_fech_limpo[mask]
        if sub.empty:
            return
        ret_acao_medio = round(float(sub["retorno_acao"].mean()), 4)
        pf_acao        = _profit_factor(sub["retorno_acao"])
        for nome in ESTRUTURAS_OPC:
            agg_rows.append({
                "grupo":              label,
                "estrutura":          nome,
                "n":                  len(sub),
                "acerto_direcao":     round(float(sub["acerto_direcao"].mean()), 3),
                "lucro_estrutura":    round(float(sub[f"lucro_{nome}"].mean()), 3),
                "payoff_medio":       round(float(sub[f"payoff_{nome}"].mean()), 4),
                "payoff_mediano":     round(float(sub[f"payoff_{nome}"].median()), 4),
                "pf_estrutura":       _profit_factor(sub[f"payoff_{nome}"]),
                "retorno_acao_medio": ret_acao_medio,
                "pf_acao":            pf_acao,
            })

    # por nível (sobre df_fech_limpo)
    for nivel in ["A", "B", "Monitorar", "vetado"]:
        if nivel == "vetado":
            m = df_fech_limpo["veto_gate_historico"] == 1
        elif nivel == "Monitorar":
            m = df_fech_limpo["ordem"].str.startswith("Monitorar")
        else:
            m = df_fech_limpo["operacao_nivel_live"] == nivel
        _agg(f"nivel_{nivel}", m)

    # por faixa razao_sinal (sobre df_fech_limpo)
    razao = pd.to_numeric(df_fech_limpo["razao_sinal"], errors="coerce")
    for label, lo, hi in [("<1", 0, 1), ("1-2", 1, 2), ("2-5", 2, 5), (">5", 5, 999)]:
        _agg(f"razao_{label}", (razao >= lo) & (razao < hi))

    # por versão × razão (sobre df_fech_limpo — versões CONTAMINADAS excluídas por construção)
    if "versao" in df_fech_limpo.columns:
        for versao in sorted(df_fech_limpo["versao"].unique()):
            sub_v = df_fech_limpo["versao"] == versao
            _agg(f"versao_{versao}", sub_v)
            for label, lo, hi in [("<1", 0, 1), ("1-2", 1, 2), ("2-5", 2, 5), (">5", 5, 999)]:
                _agg(f"versao_{versao}_razao_{label}", sub_v & (razao >= lo) & (razao < hi))

    # Contaminados: único grupo de inspeção, explicitamente rotulado fora das conclusões
    for nome in ESTRUTURAS_OPC:
        if not df_fech_cont.empty:
            agg_rows.append({
                "grupo":              "CONTAMINADO_rel_rsl20_NAO_USAR_P_CONCLUSAO",
                "estrutura":          nome,
                "n":                  len(df_fech_cont),
                "acerto_direcao":     round(float(df_fech_cont["acerto_direcao"].mean()), 3),
                "lucro_estrutura":    round(float(df_fech_cont[f"lucro_{nome}"].mean()), 3),
                "payoff_medio":       round(float(df_fech_cont[f"payoff_{nome}"].mean()), 4),
                "payoff_mediano":     round(float(df_fech_cont[f"payoff_{nome}"].median()), 4),
                "pf_estrutura":       _profit_factor(df_fech_cont[f"payoff_{nome}"]),
                "retorno_acao_medio": round(float(df_fech_cont["retorno_acao"].mean()), 4),
                "pf_acao":            _profit_factor(df_fech_cont["retorno_acao"]),
            })

    df_agg = pd.DataFrame(agg_rows)

    # ── Aba agregados_magnitude: acerto/payoff condicionado à magnitude do
    # movimento e à estrutura de fato recomendada pelo executor ──────────────
    # Motivação (11/08): acerto_direcao trata um movimento de 0,02dp igual a um
    # de 2,0dp — mas o edge do MIA só existe se o movimento for grande o
    # suficiente pra vencer o custo da opção. Terços por |M_real| mostram se o
    # modelo acerta MAIS nos movimentos grandes (objetivo do projeto) ou menos.
    # Achado inicial (196 sinais, run 11/08): acerto cai de ~48% (terço pequeno)
    # pra 23% (terço grande) — pior que o acaso justo onde deveria ser melhor.
    df_agg_mag = _agregar_magnitude_e_estrutura(df_fech_limpo)
    if not df_agg_mag.empty:
        terco_grande = df_agg_mag[(df_agg_mag["grupo"] == "magnitude_grande")
                                   & (df_agg_mag["estrutura"] == "naked_30")]
        if not terco_grande.empty:
            print(f"\nAcerto no terço de movimentos GRANDES (naked_30): "
                  f"{terco_grande['acerto_direcao'].iloc[0]:.1%} "
                  f"(N={int(terco_grande['n'].iloc[0])}) — comparar com ~50% de acaso.")
        geral_rec = df_agg_mag[df_agg_mag["grupo"] == "estrutura_recomendada_geral"]
        if not geral_rec.empty:
            print(f"Payoff usando a estrutura de fato recomendada por sinal (estrutura_otima): "
                  f"{geral_rec['payoff_medio'].iloc[0]:.4f}  |  "
                  f"acerto: {geral_rec['acerto_direcao'].iloc[0]:.1%}  |  "
                  f"PF opção: {geral_rec['pf_estrutura'].iloc[0]:.2f}  |  "
                  f"PF ação: {geral_rec['pf_acao'].iloc[0]:.2f}  "
                  f"(N={int(geral_rec['n'].iloc[0])})")

    # Parte D: resumo de versões e linhas inferidas
    if "versao_inferida" in df_fech.columns:
        n_inf = int(df_fech["versao_inferida"].sum())
        print(f"Linhas com versão inferida (não registrada): {n_inf}/{len(df_fech)} — "
              f"agregados por versão anteriores a 13/06 são aproximados.")
        vc = df_fech["versao"].value_counts()
        for v, n in vc.items():
            print(f"  {v}: {n} sinais")

    # C4: correlação in-sample×realizado — apenas sinais LIMPOS (pós-09/06)
    df_corr = df_fech_limpo[df_fech_limpo["tem_esperanca_insample"] == 1][["esperanca_opcao_dp", "payoff_naked_30"]].dropna()
    if len(df_corr) >= 15:
        corr = df_corr.corr().iloc[0, 1]
        print(f"\nCorrelação in-sample×realizado: {corr:.3f} (N={len(df_corr)})")
        df_agg = pd.concat([df_agg, pd.DataFrame([{
            "grupo": "correlacao_insample_vs_realizado", "estrutura": "naked_30",
            "n": len(df_corr), "acerto_direcao": np.nan,
            "lucro_estrutura": np.nan, "payoff_medio": corr, "payoff_mediano": np.nan,
        }])], ignore_index=True)
    else:
        print(f"\nCorrelação in-sample×realizado: amostra insuficiente (N={len(df_corr)} < 15) — aguardar safra nova.")

    # ── Salva Excel ───────────────────────────────────────────────────────────
    with pd.ExcelWriter(SAIDA, engine="openpyxl") as writer:
        df_real.to_excel(writer, sheet_name="realizados", index=False)
        df_agg.to_excel(writer, sheet_name="agregados", index=False)
        df_agg_mag.to_excel(writer, sheet_name="agregados_magnitude", index=False)

    print(f"\nSalvo em: {SAIDA}")


if __name__ == "__main__":
    main()
