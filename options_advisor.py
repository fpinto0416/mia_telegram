"""
MIA Options Advisor — Tradutor de Estrutura de Opções
=====================================================
Princípio: a estrutura é decidida UMA vez, no executor (avaliar_estruturas_opc).
Este módulo TRADUZ a estrutura vencedora em parâmetros operacionais.
Não reescolhe estrutura — advisor é tradutor, não decisor.

Output por ticker:
  estrategia_preferida  — label operacional da estrutura selecionada
  estrategia_agressiva  — variação mais ofensiva da MESMA família
  estrategia_conservadora — variação mais defensiva da MESMA família
  evitar                — o que não fazer dado o contexto
  delta_alvo            — delta aproximado da perna comprada
  alternativas          — esp das estruturas não-escolhidas (auditoria)
  logica                — explicação curta + contexto de risco
  confianca_recomendacao — alta / media / baixa / vetada
"""

from __future__ import annotations
from typing import Dict, Any

# ---------------------------------------------------------------------------
# Thresholds internos
# ---------------------------------------------------------------------------
ESPERANCA_FORTE  = 0.20   # quartil superior na escala de payoff líquido
RV_RANK_BARATO   = 0.30
RV_RANK_MODERADO = 0.60

#importar Selic
from bcb import sgs

# O código 432 é a Selic meta/diária
df = sgs.get({'selic': 432}, start='2026-01-01')
selic=df['selic'].iloc[-1] / 100.0
selic_diaria = (1 + selic) ** (1/252) - 1
CARRY_20 = (1 + selic_diaria) ** 20 - 1  # carry livre de risco em 20 pregões


# ---------------------------------------------------------------------------
# Parâmetros operacionais por estrutura × lado
# Variações agressiva/conservadora ficam na MESMA família — não reabre escolha.
# ---------------------------------------------------------------------------
_PARAMS: Dict[str, Dict[str, Dict]] = {
    "naked_30": {
        "CALL": dict(
            label="Call delta~30 naked (OTM ~1.5σ, 20d)",
            delta="~0.30",
            agressiva="Call delta~35 naked (mais ITM, mais prêmio, mesmo prazo)",
            conservadora="Call delta~25 naked (mais OTM, menor prêmio, menor risco)",
            evitar="Venda descoberta de put / short vega (vol em expansão)",
        ),
        "PUT": dict(
            label="Put delta~-30 naked (OTM ~1.5σ, 20d)",
            delta="~0.30",
            agressiva="Put delta~-35 naked (mais ITM, mais prêmio)",
            conservadora="Put delta~-25 naked (mais OTM, menor prêmio)",
            evitar="Venda descoberta de call / ratio spread (vol em expansão)",
        ),
    },
    "naked_45": {
        "CALL": dict(
            label="Call delta~45 naked (quase ATM, ~0.9σ, 20d)",
            delta="~0.45",
            agressiva="Call delta~50 naked (ATM, máximo gamma)",
            conservadora="Call delta~40 naked (ligeiramente mais OTM)",
            evitar="OTM extremo (<delta~20) sem vol prevista muito forte",
        ),
        "PUT": dict(
            label="Put delta~-45 naked (quase ATM, ~0.9σ, 20d)",
            delta="~0.45",
            agressiva="Put delta~-50 naked (ATM, máximo gamma)",
            conservadora="Put delta~-40 naked (ligeiramente mais OTM)",
            evitar="OTM extremo sem vol prevista muito forte",
        ),
    },
    "spread_35_20": {
        "CALL": dict(
            label="Bull spread: compra call delta~35, venda call delta~20",
            delta="~0.35 (perna longa)",
            agressiva="Bull spread mais largo: compra delta~40, venda delta~15",
            conservadora="Bull spread mais estreito: compra delta~35, venda delta~25",
            evitar="Call naked com IV alta; short vega descoberto",
        ),
        "PUT": dict(
            label="Bear spread: compra put delta~-35, venda put delta~-20",
            delta="~0.35 (perna longa)",
            agressiva="Bear spread mais largo: compra delta~-40, venda delta~-15",
            conservadora="Bear spread mais estreito: compra delta~-35, venda delta~-25",
            evitar="Put naked com IV alta; ratio spread",
        ),
    },
    "spread_45_25": {
        "CALL": dict(
            label="Bull spread: compra call delta~45, venda call delta~25",
            delta="~0.45 (perna longa)",
            agressiva="Bull spread mais largo: compra delta~50, venda delta~20",
            conservadora="Bull spread estreito: compra delta~40, venda delta~25",
            evitar="Call naked se rv_rank > 0.60; vega vendido descoberto",
        ),
        "PUT": dict(
            label="Bear spread: compra put delta~-45, venda put delta~-25",
            delta="~0.45 (perna longa)",
            agressiva="Bear spread mais largo: compra delta~-50, venda delta~-20",
            conservadora="Bear spread estreito: compra delta~-40, venda delta~-25",
            evitar="Put naked se rv_rank > 0.60; ratio spread",
        ),
    },
    "spread_45_15": {
        "CALL": dict(
            label="Bull spread largo: compra call delta~45, venda call delta~15",
            delta="~0.45 (perna longa)",
            agressiva="Call naked delta~45 (se rv_rank < 0.60)",
            conservadora="Bull spread estreito: compra delta~45, venda delta~25",
            evitar="Spread ainda mais largo que 45/15; ratio spread",
        ),
        "PUT": dict(
            label="Bear spread largo: compra put delta~-45, venda put delta~-15",
            delta="~0.45 (perna longa)",
            agressiva="Put naked delta~-45 (se rv_rank < 0.60)",
            conservadora="Bear spread estreito: compra delta~-45, venda delta~-25",
            evitar="Spread ainda mais largo que 45/15; ratio spread",
        ),
    },
}

_SEM_RECOMENDACAO: Dict[str, str] = {
    "estrategia_preferida": "",
    "estrategia_agressiva": "",
    "estrategia_conservadora": "",
    "evitar": "",
    "delta_alvo": "",
    "alternativas": "",
    "logica": "Ticker neutro — sem sinal operacional",
    "confianca_recomendacao": "",
    "condicao_entrada": "",
    "alvo_realizacao": "",
    "stop_loss": "",
    "gestao_nota": "",
}

_RECOMENDACAO_VETADA: Dict[str, str] = {
    **_SEM_RECOMENDACAO,
    "estrategia_preferida": "VETADA — esperança histórica insuficiente",
    "logica": "Sinal vetado pelo gate de esperança histórica — não operar.",
    "confianca_recomendacao": "vetada",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confianca(row: Dict[str, Any]) -> str:
    forca      = str(row.get("forca_sinal", "fraco"))
    esperanca  = float(row.get("esperanca_estrutura_dp", row.get("esperanca_opcao_dp", 0.0)) or 0.0)
    use_har    = int(row.get("use_har_fallback", 0))
    vol_tipo   = str(row.get("vol_model_tipo", "ML"))
    pontos = 0
    if forca == "forte":
        pontos += 2
    elif forca == "marginal":
        pontos += 1
    if esperanca >= ESPERANCA_FORTE:
        pontos += 1
    if use_har or vol_tipo == "RV_RANK":
        pontos -= 1
    if pontos >= 3:
        return "alta"
    if pontos >= 1:
        return "media"
    return "baixa"


def _condicao_entrada_iv(estrutura: str, rv_rank: float) -> str:
    eh_spread = "spread" in estrutura
    # Carry 20 pregões em % (ex.: 0.83% a.a. Selic 10.5%)
    carry_pct = round(CARRY_20 * 100, 2)
    base = (
        f"Verificar IV no mercado: entrar só se IV_mercado ≤ vol_prevista_anualizada. "
        f"Se IV > vol_prevista: aguardar ou reduzir tamanho. "
        f"[Taxa livre de risco: {selic_diaria*100:.3f}%/dia | carry 20 pregões: {carry_pct:.2f}%]"
    )
    if eh_spread:
        return (
            "OBRIGATÓRIO: débito ≤ 50% da largura entre strikes "
            "(ideal ≤ 33% para relação lucro/perda 2:1). "
            "Se débito > 50%: aproximar strikes ou aguardar. "
            f"rv_rank={rv_rank:.2f} — {'custo aceitável' if rv_rank < RV_RANK_MODERADO else 'IV cara, trava estreita preferível'}. "
            f"[Carry 20 pregões: {carry_pct:.2f}% — preferir spread bull/bear sobre naked para calls em carry alto]"
        )
    return base


def _saida_gestao_estrutura(estrutura: str, confianca: str, ordem: str) -> Dict[str, str]:
    eh_spread    = "spread" in estrutura
    eh_monitorar = "Monitorar" in ordem
    if eh_monitorar:
        return {
            "alvo_realizacao": "40% do lucro máximo" if eh_spread else "50% do prêmio pago",
            "stop_loss":       "80% do débito pago"  if eh_spread else "30% do prêmio pago",
            "gestao_nota":     "Posição menor — monitorar. Saída rápida se sinal não confirmar em 5 pregões.",
        }
    if eh_spread:
        alvo = "50% do lucro máximo da trava"
        stop = "100% do débito pago"
        nota = "Trava limita upside — sair em 50% do max. Stop = débito total."
        if confianca == "baixa":
            alvo = "40% do lucro máximo da trava"
            stop = "80% do débito pago"
            nota = "Confiança baixa — realizar mais cedo e stop mais apertado."
    else:
        if confianca == "alta":
            alvo, stop, nota = "100% do prêmio pago", "50% do prêmio pago", "Edge forte — aguardar dobrar o prêmio. Stop 50%."
        elif confianca == "media":
            alvo, stop, nota = "80% do prêmio pago", "40% do prêmio pago", "Regra 80/40: realizar 80%, stop 40%."
        else:
            alvo, stop, nota = "60% do prêmio pago", "40% do prêmio pago", "Confiança baixa — alvo conservador."
    return {"alvo_realizacao": alvo, "stop_loss": stop, "gestao_nota": nota}


def _formatar_alternativas(row: Dict[str, Any], estrutura_escolhida: str) -> str:
    nomes = {"esp_naked_30": "naked_30", "esp_naked_45": "naked_45",
             "esp_spread_35_20": "spread_35_20", "esp_spread_45_25": "spread_45_25",
             "esp_spread_45_15": "spread_45_15"}
    partes = []
    for col, nome in nomes.items():
        if nome == estrutura_escolhida:
            continue
        val = row.get(col)
        if val is not None and str(val) not in ("nan", ""):
            try:
                partes.append(f"{nome}: esp={float(val):+.3f}")
            except (ValueError, TypeError):
                pass
    return " | ".join(partes)


def _motivo_nenhuma(row: Dict[str, Any]) -> str:
    vetada_por = str(row.get("estrutura_vetada_por", ""))
    perm       = str(row.get("estruturas_permitidas", ""))
    if vetada_por == "risco":
        return (
            f"Estrutura de maior esperança vetada pela camada de risco "
            f"(vol HAR/fraca ou IV cara). Permitidas: {perm or 'spreads'}. "
            "Nenhuma spread com E−SE>0 — não operar."
        )
    return "Nenhuma estrutura tem esperança acima do ruído amostral (E−SE>0). Não operar."


# ---------------------------------------------------------------------------
# Função principal — TRADUTOR
# ---------------------------------------------------------------------------

def recomendar_estrategia(row: Dict[str, Any]) -> Dict[str, str]:
    """
    Traduz a estrutura_otima decidida pelo executor em parâmetros operacionais.
    Não reescolhe estrutura — apenas traduz e preenche gestão/IV/alternativas.
    """
    ordem = str(row.get("ordem", "Neutro"))
    if ordem == "Neutro" or not ("CALL" in ordem or "PUT" in ordem):
        return _SEM_RECOMENDACAO

    if int(row.get("veto_gate_historico", 0)) == 1:
        return _RECOMENDACAO_VETADA

    lado    = "CALL" if "CALL" in ordem else "PUT"
    estrut  = str(row.get("estrutura_otima", "")) or "nenhuma"
    esp_est = float(row.get("esperanca_estrutura_dp", 0.0) or 0.0)
    rv_rank = float(row.get("rv_rank_252", 0.5) or 0.5)
    use_har = int(row.get("use_har_fallback", 0))
    confianca = _confianca(row)

    if estrut == "nenhuma":
        return {
            **_SEM_RECOMENDACAO,
            "estrategia_preferida": "Sem estrutura com edge positivo — não operar",
            "evitar": "Qualquer compra: nenhuma estrutura supera o ruído amostral",
            "confianca_recomendacao": "baixa",
            "logica": _motivo_nenhuma(row),
        }

    params = _PARAMS[estrut][lado]
    har_nota = " (vol via HAR — naked vetado por risco)" if use_har else ""
    eh_monitorar = "Monitorar" in ordem
    logica = (
        f"[Empírico] {params['label']} — esp_estrutura={esp_est:.3f} DP. "
        f"Selecionada por esperança dentro da família, ROI entre famílias, "
        f"respeitando o contexto de risco{har_nota}."
        + (" [MONITORAR] Aguardar vol_prediction cruzar threshold forte antes de entrar." if eh_monitorar else "")
    )
    gestao = _saida_gestao_estrutura(estrut, confianca, ordem)

    return {
        "estrategia_preferida":    params["label"],
        "estrategia_agressiva":    params["agressiva"],
        "estrategia_conservadora": params["conservadora"],
        "evitar":                  params["evitar"],
        "delta_alvo":              params["delta"],
        "alternativas":            _formatar_alternativas(row, estrut),
        "logica":                  logica,
        "confianca_recomendacao":  confianca,
        "condicao_entrada":        _condicao_entrada_iv(estrut, rv_rank),
        "alvo_realizacao":         gestao["alvo_realizacao"],
        "stop_loss":               gestao["stop_loss"],
        "gestao_nota":             gestao["gestao_nota"],
    }


# ---------------------------------------------------------------------------
# Aplicar ao DataFrame
# ---------------------------------------------------------------------------

def aplicar_advisor_ao_dataframe(df):
    import pandas as pd
    if df.empty:
        for c in ["estrategia_preferida", "estrategia_agressiva", "estrategia_conservadora",
                  "evitar", "delta_alvo", "alternativas", "logica", "confianca_recomendacao"]:
            df[c] = ""
        return df
    recomendacoes = df.apply(lambda row: recomendar_estrategia(row.to_dict()), axis=1)
    rec_df = pd.DataFrame(list(recomendacoes))
    for col in rec_df.columns:
        df[col] = rec_df[col].values
    return df


# ---------------------------------------------------------------------------
# CLI — teste standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    CENARIOS = [
        dict(ticker="PETR4", ordem="Alerta CALL", rv_rank_252=0.22,
             vol_expanding_forte_live=1, forca_sinal="forte", esperanca_opcao_dp=0.18,
             use_har_fallback=0, vol_model_tipo="ML", razao_sinal=1.45,
             estrutura_otima="naked_30", esperanca_estrutura_dp=0.18,
             esp_naked_30=0.18, esp_naked_45=0.12, esp_spread_35_20=0.06, esp_spread_45_25=0.09,
             estrutura_vetada_por="", estruturas_permitidas="naked_30,naked_45,spread_35_20,spread_45_25",
             veto_gate_historico=0),
        dict(ticker="VALE3-HAR", ordem="Alerta PUT", rv_rank_252=0.45,
             vol_expanding_forte_live=1, forca_sinal="forte", esperanca_opcao_dp=0.12,
             use_har_fallback=1, vol_model_tipo="HAR", razao_sinal=1.10,
             estrutura_otima="spread_45_25", esperanca_estrutura_dp=0.08,
             esp_naked_30=0.11, esp_naked_45=0.15, esp_spread_35_20=0.04, esp_spread_45_25=0.08,
             estrutura_vetada_por="risco", estruturas_permitidas="spread_35_20,spread_45_25",
             veto_gate_historico=0),
        dict(ticker="MRVE3", ordem="Compra PUT (VETADA)", rv_rank_252=0.35,
             vol_expanding_forte_live=1, forca_sinal="forte", esperanca_opcao_dp=0.03,
             use_har_fallback=0, vol_model_tipo="ML", razao_sinal=1.20,
             estrutura_otima="nenhuma", esperanca_estrutura_dp=-0.18,
             esp_naked_30=0.03, esp_naked_45=0.09, esp_spread_35_20=-0.02, esp_spread_45_25=0.01,
             estrutura_vetada_por="esperanca", estruturas_permitidas="naked_30,naked_45,spread_35_20,spread_45_25",
             veto_gate_historico=1),
    ]

    print("=" * 80)
    print("MIA OPTIONS ADVISOR — Teste de cenários")
    print("=" * 80)
    for c in CENARIOS:
        rec = recomendar_estrategia(c)
        print(f"\n{'─'*70}")
        print(f"Ticker: {c['ticker']} | Ordem: {c['ordem']}")
        print(f"  Preferida:    {rec['estrategia_preferida']}")
        print(f"  Agressiva:    {rec['estrategia_agressiva']}")
        print(f"  Conservadora: {rec['estrategia_conservadora']}")
        print(f"  Alternativas: {rec.get('alternativas', '')}")
        print(f"  Confiança:    {rec['confianca_recomendacao']}")
        print(f"  Lógica:       {rec['logica'][:100]}...")
