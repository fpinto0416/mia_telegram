"""
PROJETO MIA — executor.py v5.5
Gerador de ordens para compra de opções com base nas novas saídas do direction e volatility.

Objetivo:
    Consumir diretamente as abas Executor_Input dos módulos:
        - direction_best*.xlsx
        - volatility_best*.xlsx

    E combinar:
        direction + volatility + filtro de custo/volatilidade realizada + previsão live

Papel correto dos módulos:
    direction_best2.py:
        diagnostica edge direcional por lado:
            direction_model_ok_exec_up
            direction_model_ok_exec_down
            direction_model_ok_exec_side
            classe_executor_direction

    volatility_best.py:
        diagnostica edge de expansão de volatilidade:
            volatility_model_ok_exec
            classe_executor_volatility
            exec_expansion_alerta_gap
            vol_exec_score_adj

    executor.py:
        decide CALL / PUT / NEUTRO usando:
            1. gate direcional por lado;
            2. gate de expansão de volatilidade;
            3. previsão live de direção;
            4. previsão live de expansão de volatilidade;
            5. filtro de custo relativo de volatilidade;
            6. regra de magnitude/threshold do sinal.

Principais mudanças v5.0:
    [E1] Remove dependência do consolidado antigo baseado em sinal_confiavel.
    [E2] Carrega direction/volatility diretamente de Executor_Input.
    [E3] CALL usa direction_model_ok_exec_up.
    [E4] PUT usa direction_model_ok_exec_down.
    [E5] Compra de CALL e compra de PUT exigem volatility_model_ok_exec.
    [E6] Remove exceção antiga: “sinal muito forte opera sem vol expansion”.
         Para compra de opção, expansão de volatilidade é obrigatória.
    [E7] Inclui motivo_bloqueio_executor.
    [E8] Inclui vol_exec_score_adj e exec_expansion_alerta_gap no output.
    [E9] Usa rv_rank_252/ivrank como proxy de custo relativo enquanto IV real não existir.
    [E10] Reconstrói features adicionais do volatility v5.9 e direction v9.x
          para compatibilidade com os pickles atuais.

Correções v5.2:
    [G1] Seleciona apenas arquivos com aba Executor_Input válida.
    [G2] Prefere arquivos do dia e, no volatility, arquivos operacionais.
    [G3] Remove fallback silencioso para aba Resultados.
    [G4] Loga fontes, flags e interseções direction × volatility.

Evolução v5.3:
    [H1] Corrige features live do direction v9.x que estavam ausentes/divergentes.
    [H2] Cria gate_operacional_nivel A/B/C:
         A = direction forte + volatility exec forte;
         B = direction forte + volatility moderada/ranking;
         C = direction forte sem confirmação de volatilidade.
    [H3] Processa níveis A/B, mantendo compra de opção apenas no nível A
         e expondo nível B como alerta operacional.
    [H4] Separa contagem de compra de opção, alerta operacional e neutros.

Evolução v5.4:
    [I1] Consome o volatility v6.0 com score/ranking e thresholds por ativo.
    [I2] Usa expansao forte para compra de opcao e expansao moderada para alerta.
    [I3] Carrega contexto de compressao sem transformar compressao em compra de opcao.

	Evolução v5.5:
	    [J1] Modo executor diário: usa o último Executor_Input válido de treino,
	         sem exigir que direction/volatility rodem no mesmo dia.
	    [J2] Registra data e idade dos artefatos de treino no log e no resumo.
	    [J3] Reconhece volatility v6.1/v6.2 e preserva diagnosticos de benchmark.
"""

import os
import sys
import subprocess
import time
import glob
import re
from pathlib import Path
from datetime import date
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
import joblib

try:
    import options_advisor as _adv
    _ADVISOR_OK = True
except ImportError:
    _ADVISOR_OK = False

try:
    from bcb import sgs as _sgs
    _df_selic = _sgs.get({'selic': 432}, start='2025-01-01')
    SELIC_DIARIA = float(_df_selic['selic'].iloc[-1]) / 100.0
    SELIC_DIARIA = (1 + SELIC_DIARIA) ** (1/252) - 1
except Exception:
    SELIC_DIARIA = 0.000397  # fallback ~10.5% a.a.
CARRY_20 = (1 + SELIC_DIARIA) ** 20 - 1  # retorno livre de risco em 20 pregões


# =============================================================================
# CONFIGURAÇÕES
# =============================================================================

np.random.seed(42)

caminho_completo = os.path.abspath(__file__)
pasta_do_arquivo = os.path.dirname(caminho_completo)

hoje = pd.to_datetime("today").date()
#hoje = pd.to_datetime("2026-07-17").date()

VERSION_EXECUTOR = "v5.5_executor_diario"

# Use data fixa apenas para reprodução/auditoria, se necessário.
# hoje = pd.to_datetime("2026-05-08").date()

DATA_LIMITE_HISTORICO = pd.to_datetime("2000-01-01").date()
DATA_LIMITE_ANALISE = pd.to_datetime("2024-09-26").date()

PERIODO_ALVO = 20
SQRT_H = np.sqrt(PERIODO_ALVO)
# direction_short (horizonte 5d): descartado — sem ganho observado no alinhamento 5d×20d.

# Strike proxy delta 30 usado como referência operacional.
DELTA30_SIGMA_MULT = 0.52

# Payoff delta-30 em unidades de σ√20 (Black-Scholes, quase insensível ao nível de vol).
STRIKE_DELTA30_DP    = 0.57   # distância strike call/put delta-30
PREMIO_DELTA30_DP    = 0.18   # prêmio BS delta-30, 20 pregões
BREAKEVEN_DELTA30_DP = STRIKE_DELTA30_DP + PREMIO_DELTA30_DP  # 0.75

# Estruturas candidatas — strikes e prêmios em DP do horizonte (BS, delta clássico, 20 pregões).
# k_long = strike comprado; k_short = strike vendido (None = naked); custo = prêmio líquido.
ESTRUTURAS_OPC = {
    "naked_30":     dict(k_long=0.57, k_short=None, custo=0.181),
    "naked_45":     dict(k_long=0.17, k_short=None, custo=0.320),
    "spread_35_20": dict(k_long=0.43, k_short=0.89, custo=0.117),
    "spread_45_25": dict(k_long=0.17, k_short=0.72, custo=0.178),
    "spread_45_15": dict(k_long=0.17, k_short=1.04, custo=0.232),
}
FATOR_CUSTO_2_PERNAS = 1.10   # spreads pagam 2 bid-asks; penalidade de execução
ROI_MARGEM_TROCA     = 1.20   # só troca da default se ROI da vencedora superar naked_30 em 20%

# Threshold de entrada em cauda.
TAIL_MULTIPLIER = 1.20
RAZAO_MINIMO = 0.80
RAZAO_MARGINAL = 0.60   # [E1] zona marginal: aprovado se vol_exec_score_adj>0.55 e rv_rank<0.50
RAZAO_SANIDADE_MAX = 3.0  # acima disso razao correlaciona com ACERTO PIOR, não melhor
                           # (M9 01/07: razão 2-5 acertou 6% vs razão 1-2 acertou 50% em CALLs)
                           # Flag + rebaixamento A→B; não veto — gate de esperança faz o corte fino.

# Avisos de saída antecipada
T_RESTANTE_ENCERRAR = 3    # pregões: a partir daqui, theta domina — encerrar opção comprada
ALVO_LUCRO_DP       = 0.30  # realizar quando M_adj > BE + este valor (lucro real, não empate)
DIAS_INVERSAO_TARDIA = 14  # inversão a partir deste pregão é informativa, não saída:
                           # previsão nova cobre [d, d+20] mas restam ≤6 pregões — maioria da
                           # janela prevista é pós-vencimento. Realizados (15/07/26, limpas):
                           # 9 fechadas com inversão d≥14 tiveram M restante médio +0.47 (segurar
                           # foi certo). Inversão d≤13 segue saída plena (sobreposição ≥ 35%).

# Para compra de opção, expansão live é obrigatória.
VOL_EXPANSION_MIN = 1.05
VOL_EXPANSION_CINZA_MIN = 0.98   # [E2] zona cinza: aprovado se vol_exec_score_adj>=0.60
# [E6] "quase nível A": vol_model_ok=1, direção ok, razao forte, vol entre 1.00 e thr_exp_forte.
# Aparece como "Monitorar CALL/PUT" — vigilância, não operação.
VOL_MONITORAR_MIN = 1.00         # expansão real mínima para entrar no "Monitorar"

# Proxy atual de custo relativo de volatilidade.
# Observação: o ivrank atual é baseado em volatilidade realizada, não IV real de opções.
RV_RANK_MAX_COMPRA = 0.70

# Mantido por governança: processar apenas tickers com gates positivos nos dois módulos.
PREFILTRAR_PELOS_EXECUTOR_INPUTS = True

# Nível A continua sendo compra de opção comprada. Nível B entra como alerta/spread/tamanho reduzido.
VOL_MODERADA_TOP_N = 25
# Tickers sem modelo de vol mas com expansion_rank forte o suficiente entram como nivel B (alerta).
# Threshold: top-N por vol_expansion_rank (1 = melhor expansão). Só abre nivel B, nunca A.
RV_RANK_GATE_N = 30
PROCESSAR_NIVEIS_OPERACIONAIS = ("A", "B")

# Se True, falha quando não encontrar os arquivos direction/volatility.
# Se False, roda sem gates, mas isso não é recomendado.
STRICT_EXECUTOR_INPUT = True

# Modo diário: direction/volatility são artefatos de treino/calibração.
# O executor roda todo dia usando o último Executor_Input válido disponível.
MODO_EXECUTOR_DIARIO = True
PREFERIR_ARQUIVOS_DO_DIA = False
AVISAR_ARTEFATO_TREINO_DIAS = 45

# Saída
NOME_SAIDA = f"ordem_dia_{hoje.year:04d}_{hoje.month:02d}_{hoje.day:02d}_executor_v5_5.xlsx"


TICKERS = [
    "BOVA11", "PETR4", "HASH11", "ITUB4", "VALE3", "BBAS3", "BBDC4", "SMAL11",
    "BRAV3", "PRIO3", "WEGE3", "B3SA3", "MGLU3", "EMBJ3", "BPAC11", "PETR3",
    "SUZB3", "IBOV11", "ITSA4", "GGBR4", "USIM5", "LREN3", "BBSE3", "MBRF3",
    "HAPV3", "CMIG4", "MRVE3", "AXIA3", "ABEV3", "CSNA3", "RENT3", "CSAN3",
    "TAEE11", "NATU3", "CYRE3", "RADL3", "JHSF3", "SBSP3", "GOAU4", "COGN3",
    "ASAI3", "RAIZ4", "AZZA3", "EGIE3", "BRAP4", "BRKM5", "DIRR3", "IRBR3",
    "EQTL3", "BOVV11", "BEEF3", "POMO4", "CPLE3", "KLBN11", "ALOS3", "CXSE3",
    "SANB11", "CMIN3", "VBBR3", "BBDC3", "UGPA3", "RAIL3", "NVDC34",
    "SMTO3", "RDOR3", "VAMO3", "AUAU3", "RECV3", "HYPE3", "ENEV3", "MULT3",
    "CSMG3", "PCAR3", "YDUQ3", "SLCE3", "CVCB3", "ISAE4", "TOTS3", "MOTV3",
    "PSSA3", "CEAB3", "AURE3", "JBSS32", "ROXO34", "VIVA3", "SIMH3", "BHIA3",
    "SAPR11", "XPBR31", "GMAT3", "FLRY3", "SBFG3", "EZTC3", "SOJA3", "ECOR3",
    "MOVI3", "VIVT3", "ALPA4", "CURY3", "BRSR6"
]
#TICKERS = ["YDUQ3"]

# =============================================================================
# LOG
# =============================================================================

class Logger:
    def __init__(self, filename: str):
        self.terminal = sys.stdout
        self.log = open(filename, "a", encoding="utf-8")

    def write(self, message: str) -> None:
        self.terminal.write(message)
        self.log.write(message)

    def flush(self) -> None:
        self.terminal.flush()
        self.log.flush()


caminho_log = os.path.join(pasta_do_arquivo, "log_projeto_executor_v5_5.txt")
if os.path.exists(caminho_log):
    os.remove(caminho_log)
sys.stdout = Logger(caminho_log)

print(f"VERSAO EXECUTOR: {VERSION_EXECUTOR}")
print(f"Arquivo em execução: {__file__}")
print(f"Data de referência: {hoje}")




# =============================================================================
# DOWNLOAD DATABASE
# =============================================================================

def _rodar_download_database():
    """Executa download_database.py para atualizar /database/*.xlsx antes de processar."""
    script = Path(pasta_do_arquivo) / "download_database.py"
    if not script.exists():
        print(f"[AVISO] download_database.py não encontrado em {script} — pulando download.")
        return
    print("\n=== Atualizando database via download_database.py ===")
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(pasta_do_arquivo),
    )
    if result.returncode != 0:
        print(f"[AVISO] download_database.py encerrou com código {result.returncode} — continuando com dados existentes.")
    else:
        print("=== Database atualizado com sucesso ===\n")

def _skip_download() -> bool:
    return os.getenv("SKIP_DOWNLOAD", "0").strip() == "1"


def _importar_database_local(ticker: str, data_ref) -> pd.DataFrame:
    """Carrega OHLCV do banco de dados local como fallback quando TradingView falha."""
    arq = Path(pasta_do_arquivo) / "database" / f"{ticker}.xlsx"
    if not arq.exists():
        raise FileNotFoundError(f"Database local não encontrado: {arq}")
    df = pd.read_excel(arq, index_col=0)
    df.index = pd.to_datetime(df.index).date
    df.index.name = "Date"
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    df = df[df.index >= DATA_LIMITE_HISTORICO]
    df = df[df.index <= data_ref]
    if df.empty:
        raise ValueError(f"Database local vazio após filtro de datas para {ticker}")
    return df


def importar_dados(ticker: str, data_ref=hoje, n_bars: int = 3000) -> pd.DataFrame:
    """Carrega dados do banco local (atualizado por download_database.py no início da execução)."""
    return _importar_database_local(ticker, data_ref)


# =============================================================================
# CARREGAMENTO DOS EXECUTOR_INPUTS
# =============================================================================

REQUIRED_DIRECTION_EXEC_COLS = [
    "ticker",
    "direction_model_ok_exec_up",
    "direction_model_ok_exec_down",
    "direction_model_ok_exec_side",
]

REQUIRED_DIRECTION_SHORT_EXEC_COLS = [
    "ticker",
    "direction_model_ok_exec_up",
    "direction_model_ok_exec_down",
]

REQUIRED_VOLATILITY_EXEC_COLS = [
    "ticker",
    "volatility_model_ok_exec",
]


def _date_tokens(data_ref) -> List[str]:
    return [
        f"{data_ref.year:04d}_{data_ref.month:02d}_{data_ref.day:02d}",
        f"{data_ref.year:04d}-{data_ref.month:02d}-{data_ref.day:02d}",
    ]


def _extract_date_score(path: Path) -> int:
    match = re.search(r"(20\d{2})[_-](\d{2})[_-](\d{2})", path.name)
    if match:
        return int(match.group(1) + match.group(2) + match.group(3))
    return 0


def _extract_date_from_name(path: Path):
    match = re.search(r"(20\d{2})[_-](\d{2})[_-](\d{2})", path.name)
    if not match:
        return None
    return pd.to_datetime("-".join(match.groups())).date()


def _artifact_reference_date(path: Path):
    return _extract_date_from_name(path) or pd.to_datetime(path.stat().st_mtime, unit="s").date()


def _artifact_age_days(path: Optional[Path]) -> Optional[int]:
    if path is None:
        return None
    return int((hoje - _artifact_reference_date(path)).days)


def _candidate_files(patterns: List[str], base_dir: str) -> List[Path]:
    arquivos: List[Path] = []
    for pat in patterns:
        arquivos.extend(Path(base_dir).glob(pat))
    return sorted({p for p in arquivos if p.is_file()})


def _read_executor_sheet(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name="Executor_Input")
    except Exception as e:
        raise RuntimeError(f"{path.name} não possui aba Executor_Input legível: {e}") from e


def _missing_required_cols(df: pd.DataFrame, required_cols: List[str]) -> List[str]:
    cols_norm = {str(c).strip().lower() for c in df.columns}
    return [c for c in required_cols if c.lower() not in cols_norm]


def _select_executor_input_file(
    patterns: List[str],
    base_dir: str,
    required_cols: List[str],
    label: str,
    preferred_name_tokens: Optional[List[str]] = None,
) -> Tuple[Optional[Path], Optional[pd.DataFrame]]:
    """
    Seleciona o Excel mais adequado para o executor.

    Critérios:
    1. precisa ter aba Executor_Input;
    2. precisa conter as colunas obrigatórias do módulo;
    3. no modo diário, usa o artefato válido mais recente;
    4. dentro do dia, prioriza tokens operacionais como "operational".
    """
    preferred_name_tokens = preferred_name_tokens or []
    candidates = _candidate_files(patterns, base_dir)
    if not candidates:
        return None, None

    date_tokens = _date_tokens(hoje)
    validos = []
    invalidos = []

    for path in candidates:
        try:
            df = _read_executor_sheet(path)
            missing = _missing_required_cols(df, required_cols)
            if missing:
                invalidos.append(f"{path.name}: faltam colunas {missing}")
                continue
            name_lower = path.name.lower()
            if MODO_EXECUTOR_DIARIO:
                score = (
                    _extract_date_score(path),
                    int(any(tok.lower() in name_lower for tok in preferred_name_tokens)),
                    int(path.stat().st_mtime),
                )
            else:
                score = (
                    int(any(tok in path.name for tok in date_tokens)) if PREFERIR_ARQUIVOS_DO_DIA else 0,
                    int(any(tok.lower() in name_lower for tok in preferred_name_tokens)),
                    _extract_date_score(path),
                    int(path.stat().st_mtime),
                )
            validos.append((score, path, df))
        except Exception as e:
            invalidos.append(f"{path.name}: {e}")

    if not validos:
        detalhes = "\n  - ".join(invalidos) if invalidos else "nenhum detalhe disponível"
        raise RuntimeError(
            f"Nenhum arquivo {label} com Executor_Input válido foi encontrado.\n"
            f"Arquivos avaliados:\n  - {detalhes}"
        )

    validos.sort(key=lambda item: item[0], reverse=True)
    escolhido_score, escolhido_path, escolhido_df = validos[0]

    if invalidos:
        print(f"⚠ Arquivos {label} ignorados por incompatibilidade:")
        for motivo in invalidos:
            print(f"  - {motivo}")

    idade = _artifact_age_days(escolhido_path)
    data_ref = _artifact_reference_date(escolhido_path)

    if MODO_EXECUTOR_DIARIO:
        print(f"✓ Artefato {label} selecionado: {escolhido_path.name}")
        print(f"  Data do artefato {label}: {data_ref} | idade: {idade} dias")
        if idade is not None and idade > AVISAR_ARTEFATO_TREINO_DIAS:
            print(
                f"⚠ Artefato {label} com mais de {AVISAR_ARTEFATO_TREINO_DIAS} dias. "
                "Executor segue rodando, mas recomenda-se retreinar."
            )
    elif escolhido_score[0] == 1:
        print(f"✓ Arquivo {label} do dia encontrado: {escolhido_path.name}")
    else:
        print(f"⚠ Nenhum arquivo {label} válido do dia encontrado. Usando: {escolhido_path.name}")

    return escolhido_path, escolhido_df.copy()


def _as_int_flag(v: Any, default: int = 0) -> int:
    try:
        if pd.isna(v):
            return default
        if isinstance(v, str):
            return int(v.strip().lower() in {"1", "true", "sim", "yes", "y"})
        return int(float(v) != 0)
    except Exception:
        return default


def _as_float(v: Any, default: float = np.nan) -> float:
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _nivel_operacional_lado(direction_ok: pd.Series, vol_forte: pd.Series, vol_moderada: pd.Series) -> pd.Series:
    direction_ok = direction_ok.astype(bool)
    return pd.Series(
        np.where(
            ~direction_ok,
            "",
            np.where(vol_forte.astype(bool), "A", np.where(vol_moderada.astype(bool), "B", "C")),
        ),
        index=direction_ok.index,
    )


def _melhor_nivel_operacional(*levels: Any) -> str:
    prioridade = {"A": 3, "B": 2, "C": 1, "": 0}
    melhor = ""
    melhor_score = 0
    for level in levels:
        level = "" if pd.isna(level) else str(level).strip().upper()
        score = prioridade.get(level, 0)
        if score > melhor_score:
            melhor = level
            melhor_score = score
    return melhor


def _nivel_processavel(level: Any) -> int:
    level = "" if pd.isna(level) else str(level).strip().upper()
    return int(level in PROCESSAR_NIVEIS_OPERACIONAIS)


def carregar_inputs_direction_volatility(base_dir: str) -> Tuple[pd.DataFrame, Path, Path]:
    """
    Carrega diretamente as abas Executor_Input dos módulos.
    Arquivos sem Executor_Input ou sem flags obrigatórias são rejeitados.
    """
    arq_dir, df_dir = _select_executor_input_file(
        [
            "direction_best_*.xlsx",
            "direction_best2_*.xlsx",
            "direction_*best*.xlsx",
        ],
        os.path.join(base_dir, "saidas", "direction"),
        REQUIRED_DIRECTION_EXEC_COLS,
        label="direction",
    )
    arq_vol, df_vol = _select_executor_input_file(
        [
            "volatility_best_*.xlsx",
            "volatility_*best*.xlsx",
        ],
        os.path.join(base_dir, "saidas", "volatility"),
        REQUIRED_VOLATILITY_EXEC_COLS,
        label="volatility",
        preferred_name_tokens=["operational", "executor", "v6_2", "v6_1", "v6_0", "v5_9"],
    )

    if arq_dir is None or arq_vol is None:
        msg = f"Arquivos Executor_Input não encontrados. direction={arq_dir}, volatility={arq_vol}"
        if STRICT_EXECUTOR_INPUT:
            raise FileNotFoundError(msg)
        print("AVISO:", msg)
        return pd.DataFrame(), arq_dir, arq_vol

    if "ticker" not in df_dir.columns or "ticker" not in df_vol.columns:
        raise ValueError("As abas Executor_Input precisam conter coluna ticker.")

    df_dir = df_dir.copy()
    df_vol = df_vol.copy()
    df_dir["ticker"] = df_dir["ticker"].astype(str)
    df_vol["ticker"] = df_vol["ticker"].astype(str)

    dir_cols = [
        "ticker",
        "direction_model_ok_exec_up",
        "direction_model_ok_exec_down",
        "direction_model_ok_exec_side",
        "classe_executor_direction",
        "direction_up_score",
        "direction_down_score",
        "direction_quality_score_adj",
    ]
    vol_cols = [
        "ticker",
        "volatility_model_ok_exec",
        "vol_expansion_ok",
        "vol_expansion_persistente",
        "vol_expansion_forte",
        "vol_expansion_moderada",
        "vol_compression_ok",
        "classe_executor_volatility",
        "exec_expansion_alerta_gap",
        "vol_exec_score_adj",
        "vol_expansion_score",
        "vol_compression_score",
        "vol_operational_score",
        "vol_expansion_rank",
        "vol_expansion_percentile",
        "vol_operational_rank",
        "vol_operational_percentile",
        "thr_pred_exp_moderada",
        "thr_pred_exp_forte",
        "thr_pred_compressao",
        "precision_expansion",
        "recall_expansion",
        "precision_expansion_moderada",
        "recall_expansion_moderada",
        "precision_compression",
        "recall_compression",
        "benchmark_modelo",
        "modelo_supera_benchmark",
        "mae_vs_benchmark",
        "qlike_vs_benchmark",
        "qlike_teste",
        "motivo_bloqueio_volatility",
        "wf_tipo",
        "use_har_fallback",
        "har_disponivel",
        "har_precision_expansion",
        "har_recall_expansion",
    ]

    for c in dir_cols:
        if c not in df_dir.columns:
            df_dir[c] = np.nan
    for c in vol_cols:
        if c not in df_vol.columns:
            df_vol[c] = np.nan

    merged = df_dir[dir_cols].merge(df_vol[vol_cols], on="ticker", how="outer")

    flag_cols = [
        "direction_model_ok_exec_up",
        "direction_model_ok_exec_down",
        "direction_model_ok_exec_side",
        "volatility_model_ok_exec",
        "vol_expansion_ok",
        "vol_expansion_persistente",
        "vol_expansion_forte",
        "vol_expansion_moderada",
        "vol_compression_ok",
        "exec_expansion_alerta_gap",
        "modelo_supera_benchmark",
        "use_har_fallback",
        "har_disponivel",
    ]
    for c in flag_cols:
        merged[c] = merged[c].apply(_as_int_flag)

    score_cols = [
        "direction_up_score",
        "direction_down_score",
        "direction_quality_score_adj",
        "vol_exec_score_adj",
        "vol_expansion_score",
        "vol_compression_score",
        "vol_operational_score",
        "vol_expansion_rank",
        "vol_expansion_percentile",
        "vol_operational_rank",
        "vol_operational_percentile",
        "thr_pred_exp_moderada",
        "thr_pred_exp_forte",
        "thr_pred_compressao",
        "precision_expansion",
        "recall_expansion",
        "precision_expansion_moderada",
        "recall_expansion_moderada",
        "precision_compression",
        "recall_compression",
        "modelo_supera_benchmark",
        "mae_vs_benchmark",
        "qlike_vs_benchmark",
        "qlike_teste",
    ]
    for c in score_cols:
        merged[c] = merged[c].apply(_as_float)

    if merged["vol_expansion_rank"].isna().all():
        merged["vol_expansion_rank"] = merged["vol_expansion_score"].rank(method="min", ascending=False)
    vol_forte = (
        (merged["volatility_model_ok_exec"] == 1) |
        (merged["vol_expansion_forte"] == 1)
    )
    # gate_vol_rv_rank: tickers sem modelo aprovado mas com expansion_rank forte.
    # Abre apenas nivel B (alerta) — nunca entra em vol_forte.
    gate_vol_rv_rank = (
        (merged["volatility_model_ok_exec"] == 0) &
        (merged["vol_expansion_rank"].fillna(999) <= RV_RANK_GATE_N)
    )
    merged["gate_vol_rv_rank"] = gate_vol_rv_rank.astype(int)
    vol_moderada = (
        vol_forte |
        (merged["vol_expansion_moderada"] == 1) |
        (merged["vol_expansion_ok"] == 1) |
        (merged["vol_expansion_persistente"] == 1) |
        (merged["exec_expansion_alerta_gap"] == 1) |
        (merged["vol_expansion_rank"] <= VOL_MODERADA_TOP_N) |
        gate_vol_rv_rank
    )
    merged["vol_moderada_ok"] = vol_moderada.astype(int)
    merged["vol_contexto_compressao"] = (merged["vol_compression_ok"] == 1).astype(int)

    merged["gate_call_model"] = (
        (merged["direction_model_ok_exec_up"] == 1) &
        (merged["volatility_model_ok_exec"] == 1)
    ).astype(int)
    merged["gate_put_model"] = (
        (merged["direction_model_ok_exec_down"] == 1) &
        (merged["volatility_model_ok_exec"] == 1)
    ).astype(int)
    merged["gate_any_model"] = ((merged["gate_call_model"] == 1) | (merged["gate_put_model"] == 1)).astype(int)

    merged["gate_call_nivel"] = _nivel_operacional_lado(
        merged["direction_model_ok_exec_up"] == 1,
        vol_forte,
        vol_moderada,
    )
    merged["gate_put_nivel"] = _nivel_operacional_lado(
        merged["direction_model_ok_exec_down"] == 1,
        vol_forte,
        vol_moderada,
    )
    merged["gate_side_nivel"] = _nivel_operacional_lado(
        merged["direction_model_ok_exec_side"] == 1,
        vol_forte,
        vol_moderada,
    )
    merged["gate_any_nivel"] = merged.apply(
        lambda row: _melhor_nivel_operacional(row["gate_call_nivel"], row["gate_put_nivel"], row["gate_side_nivel"]),
        axis=1,
    )
    merged["gate_processar_executor"] = merged["gate_any_nivel"].apply(_nivel_processavel)

    merged = merged.set_index("ticker", drop=False)

    print(f"✓ Direction Executor_Input: {arq_dir}")
    print(f"✓ Volatility Executor_Input: {arq_vol}")
    print(f"Colunas direction encontradas: {list(df_dir.columns)[:80]}")
    print(f"Colunas volatility encontradas: {list(df_vol.columns)[:80]}")
    print(f"✓ Direction UP flags: {int(merged['direction_model_ok_exec_up'].sum())}")
    print(f"✓ Direction DOWN flags: {int(merged['direction_model_ok_exec_down'].sum())}")
    print(f"✓ Direction SIDE flags: {int(merged['direction_model_ok_exec_side'].sum())}")
    print(f"✓ Volatility EXEC flags: {int(merged['volatility_model_ok_exec'].sum())}")
    print(f"✓ Vol expansion forte: {int(merged['vol_expansion_forte'].sum())}")
    print(f"✓ Vol expansion moderada: {int(merged['vol_expansion_moderada'].sum())}")
    print(f"✓ Vol compressão: {int(merged['vol_compression_ok'].sum())}")
    print(f"✓ Tickers com gate CALL: {int(merged['gate_call_model'].sum())}")
    print(f"✓ Tickers com gate PUT: {int(merged['gate_put_model'].sum())}")
    print(f"✓ Tickers com algum gate operacional: {int(merged['gate_any_model'].sum())}")
    print(f"✓ Nível A: {int((merged['gate_any_nivel'] == 'A').sum())}")
    print(f"✓ Nível B: {int((merged['gate_any_nivel'] == 'B').sum())}")
    print(f"✓ Nível C: {int((merged['gate_any_nivel'] == 'C').sum())}")
    print(f"✓ Tickers processáveis A/B: {int(merged['gate_processar_executor'].sum())}")
    print("Tickers Vol EXEC:", sorted(merged.loc[merged["volatility_model_ok_exec"] == 1, "ticker"].dropna().tolist()))
    print("Tickers Direction UP:", sorted(merged.loc[merged["direction_model_ok_exec_up"] == 1, "ticker"].dropna().tolist()))
    print("Tickers Direction DOWN:", sorted(merged.loc[merged["direction_model_ok_exec_down"] == 1, "ticker"].dropna().tolist()))
    print("Interseção CALL:", sorted(merged.loc[merged["gate_call_model"] == 1, "ticker"].dropna().tolist()))
    print("Interseção PUT:", sorted(merged.loc[merged["gate_put_model"] == 1, "ticker"].dropna().tolist()))
    print("Nível A:", sorted(merged.loc[merged["gate_any_nivel"] == "A", "ticker"].dropna().tolist()))
    print("Nível B:", sorted(merged.loc[merged["gate_any_nivel"] == "B", "ticker"].dropna().tolist()))

    return merged, arq_dir, arq_vol


df_gates, arq_direction_input, arq_volatility_input = carregar_inputs_direction_volatility(pasta_do_arquivo)
data_direction_input = _artifact_reference_date(arq_direction_input) if arq_direction_input is not None else None
data_volatility_input = _artifact_reference_date(arq_volatility_input) if arq_volatility_input is not None else None
idade_direction_input_dias = _artifact_age_days(arq_direction_input)
idade_volatility_input_dias = _artifact_age_days(arq_volatility_input)

# [Fix4] Checagem de partida: direction e volatility devem ser da mesma data.
# Tolerância de 1 dia para crossing de meia-noite (direction captura hoje=D, volatility hoje=D+1
# quando o pipeline cruza a meia-noite — os dados do banco são idênticos nesse caso).
# Só degrada se gap ≥ 2 dias (artefato genuinamente desatualizado).
_gap_artefatos_dias = (
    abs((data_direction_input - data_volatility_input).days)
    if data_direction_input is not None and data_volatility_input is not None
    else 999
)
_datas_artefatos_ok = _gap_artefatos_dias <= 1
DEGRADAR_PARA_ALERTA_B = not _datas_artefatos_ok
if DEGRADAR_PARA_ALERTA_B:
    print(
        f"\n[Fix4] AVISO: datas dos artefatos divergem!"
        f"\n  direction : {data_direction_input}"
        f"\n  volatility: {data_volatility_input}"
        f"\n  Todos os sinais serão rebaixados para Alerta B (sem Compras diretas).\n"
    )
else:
    print(
        f"[Fix4] Artefatos alinhados: direction={data_direction_input}, "
        f"volatility={data_volatility_input}"
    )


# =============================================================================
# FEATURE ENGINEERING
# =============================================================================

def calculate_rsi_classic(delta: pd.Series, periods: int = 14) -> pd.Series:
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / periods, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / periods, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _safe_log(x: pd.Series) -> pd.Series:
    return np.log(x.replace(0, np.nan))


def _load_macro_series() -> Dict[str, pd.DataFrame]:
    macro_tickers = {
        "di": "DI11!",
        "brent": "BRENT",
        "dolar": "DOL1!",
        "spx": "SPX",
        "usyield": "US10Y",
        "ibov": "IBOV",
        "dxy": "DXY",
        "fef": "FEF1!",
        "ccm": "CCM1!",
        "gold": "GOLD",
        "vix": "VIX",
    }
    out = {}
    for key, tk in macro_tickers.items():
        try:
            out[key] = importar_dados(tk, hoje)
            print(f"✓ Macro carregado: {key} ({tk})")
        except Exception as e:
            print(f"⚠ Falha carregando macro {key}/{tk}: {e}")
            out[key] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    return out


macro_series: Dict[str, pd.DataFrame] = {}


def add_macro_features(df: pd.DataFrame) -> pd.DataFrame:
    macro_map = {
        "RSL20_di": "di",
        "RSL20_brent": "brent",
        "RSL20_dolar": "dolar",
        "RSL20_usyield": "usyield",
        "RSL20_fef": "fef",
        "RSL20_ccm": "ccm",
        "RSL20_gold": "gold",
        "RSL20_ibov": "ibov",
        "RSL20_dxy": "dxy",
        "RSL20_spx": "spx",
        "RSL20_vix": "vix",
    }
    feats = {}
    for col, key in macro_map.items():
        ext = macro_series.get(key, pd.DataFrame()).copy()
        if ext.empty or "Close" not in ext.columns:
            feats[col] = pd.Series(np.nan, index=df.index)
            continue
        ext = ext.reindex(df.index, method="ffill")
        mm20 = ext["Close"].rolling(20).mean()
        feats[col] = ext["Close"] / mm20.replace(0, np.nan) - 1
    return pd.concat([df, pd.DataFrame(feats, index=df.index)], axis=1).copy()


def add_volatility_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reconstrói o bloco de features do volatility v5.9.x.
    """
    r = df["Retorno_diario"]
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]
    Volume = df["Volume"]

    feat = {}

    vol_ewma_22 = r.ewm(span=22, adjust=False).std()
    vol_ewma_252 = r.ewm(span=252, adjust=False).std()
    feat["vol_ewma_22"] = vol_ewma_22
    feat["vol_ewma_252"] = vol_ewma_252
    feat["regime_vol"] = vol_ewma_22 / vol_ewma_252.replace(0, np.nan)

    vol_fin = close * Volume
    amihud = (r.abs() / vol_fin.replace(0, np.nan)).rolling(20).mean()
    feat["amihud"] = amihud
    feat["amihud_delta5"] = amihud.pct_change(5)

    for lag in [3, 5, 10]:
        feat[f"ret_acc_{lag}"] = close.pct_change(lag)

    rv_min = vol_ewma_22.rolling(252).min()
    rv_max = vol_ewma_22.rolling(252).max()
    rv_rank_252 = (vol_ewma_22 - rv_min) / (rv_max - rv_min).replace(0, np.nan)
    feat["rv_rank_252"] = rv_rank_252
    feat["ivrank"] = rv_rank_252  # legado: não é IV real
    feat["iv_rv_spread"] = rv_rank_252 / vol_ewma_22.replace(0, np.nan)

    feat["dist_mm252"] = vol_ewma_22 - vol_ewma_22.rolling(252).mean()
    feat["dist_mm126"] = vol_ewma_22 - vol_ewma_22.rolling(126).mean()
    feat["vol_z_252"] = (vol_ewma_22 - vol_ewma_22.rolling(252).mean()) / vol_ewma_22.rolling(252).std()
    feat["vol_mean_reversion_pressure"] = rv_rank_252 - 0.5

    feat["kurt_50"] = r.rolling(50).kurt()
    feat["skew_50"] = r.rolling(50).skew()

    str_series = {}
    for w in [10, 20, 30, 50, 63, 126]:
        str_series[w] = r.rolling(w).std()
        feat[f"str{w}"] = str_series[w]

    vol_of_vol = str_series[20].rolling(20).std()
    feat["vol_of_vol"] = vol_of_vol
    for w in [10, 22, 63]:
        feat[f"vol_of_vol_{w}"] = str_series[20].rolling(w).std()
    feat["vol_of_vol_rank252"] = (
        (vol_of_vol - vol_of_vol.rolling(252).min()) /
        (vol_of_vol.rolling(252).max() - vol_of_vol.rolling(252).min()).replace(0, np.nan)
    )
    feat["vol_of_vol_z252"] = (vol_of_vol - vol_of_vol.rolling(252).mean()) / vol_of_vol.rolling(252).std()
    feat["vol_of_vol_ratio_10_63"] = feat["vol_of_vol_10"] / feat["vol_of_vol_63"].replace(0, np.nan)

    feat["vol_surprise"] = str_series[20] / str_series[50].replace(0, np.nan)

    close_std20 = close.rolling(20).std()
    close_mean20 = close.rolling(20).mean()
    bb_up = 2 * close_std20 + close_mean20
    bb_down = -2 * close_std20 + close_mean20
    bb_width = (bb_up - bb_down) / close_mean20.replace(0, np.nan)
    feat["BB_width"] = bb_width
    feat["BB_width_deriv"] = bb_width.pct_change()
    feat["BB_widthu_up"] = close - bb_up
    feat["BB_widthu_down"] = close - bb_down

    for w in [10, 20, 30]:
        feat[f"vol_std_{w}"] = Volume.rolling(w).std()

    q05 = r.rolling(20).quantile(0.05)
    q95 = r.rolling(20).quantile(0.95)
    feat["q05"] = q05
    feat["q95"] = q95
    feat["tail_ratio_q05_q95"] = q05.abs() / q95.abs().replace(0, np.nan)

    feat["max-min"] = high / low.replace(0, np.nan) - 1
    feat["candle"] = (close - open_) / (high - low).replace(0, np.nan) - 1

    for mm in range(15, 31, 5):
        close_mm = close.rolling(mm).mean()
        vol_mm = vol_ewma_22.rolling(mm).mean()
        feat[f"RSL_{mm}"] = close / close_mm.replace(0, np.nan)
        feat[f"RSL_str{mm}"] = vol_ewma_22 / vol_mm.replace(0, np.nan)

    for p in [14, 21, 50]:
        feat[f"rsi_{p}"] = calculate_rsi_classic(r, periods=p)

    # HAR-RV
    rv_1d = r.abs()
    feat["rv_1d"] = rv_1d
    rv_by_w = {}
    for w in [5, 22, 63]:
        rv_by_w[w] = np.sqrt((r ** 2).rolling(w).mean())
        feat[f"rv_{w}d"] = rv_by_w[w]
        feat[f"log_rv_{w}d"] = _safe_log(rv_by_w[w])
    feat["log_rv_1d"] = _safe_log(rv_1d)
    feat["rv_ratio_5_22"] = rv_by_w[5] / rv_by_w[22].replace(0, np.nan)
    feat["rv_ratio_22_63"] = rv_by_w[22] / rv_by_w[63].replace(0, np.nan)
    feat["rv_slope_5_22"] = rv_by_w[5] - rv_by_w[22]
    feat["rv_slope_22_63"] = rv_by_w[22] - rv_by_w[63]

    feat["rv_term_10_22"] = str_series[10] / str_series[20].replace(0, np.nan)
    feat["rv_term_22_63"] = str_series[20] / str_series[63].replace(0, np.nan)
    feat["rv_term_63_126"] = str_series[63] / str_series[126].replace(0, np.nan)
    feat["vol_curve_slope"] = feat["rv_term_10_22"] - feat["rv_term_22_63"]
    feat["vol_curve_inversion"] = (str_series[10] > str_series[63]).astype(float)

    # Range-based estimators
    log_hl = np.log(high / low.replace(0, np.nan))
    log_co = np.log(close / open_.replace(0, np.nan))
    log_ho = np.log(high / open_.replace(0, np.nan))
    log_lo = np.log(low / open_.replace(0, np.nan))
    log_hc = np.log(high / close.replace(0, np.nan))
    log_lc = np.log(low / close.replace(0, np.nan))

    parkinson_var = (log_hl ** 2) / (4 * np.log(2))
    garman_klass_var = 0.5 * (log_hl ** 2) - (2 * np.log(2) - 1) * (log_co ** 2)
    rogers_satchell_var = log_hc * log_ho + log_lc * log_lo

    feat["parkinson_vol_22"] = np.sqrt(parkinson_var.rolling(22).mean())
    feat["garman_klass_vol_22"] = np.sqrt(garman_klass_var.clip(lower=0).rolling(22).mean())
    feat["rogers_satchell_vol_22"] = np.sqrt(rogers_satchell_var.clip(lower=0).rolling(22).mean())
    feat["parkinson_ratio_5_22"] = np.sqrt(parkinson_var.rolling(5).mean()) / feat["parkinson_vol_22"].replace(0, np.nan)
    feat["garman_klass_ratio_5_22"] = np.sqrt(garman_klass_var.clip(lower=0).rolling(5).mean()) / feat["garman_klass_vol_22"].replace(0, np.nan)

    # Good/bad volatility
    rv_pos = r.clip(lower=0) ** 2
    rv_neg = r.clip(upper=0) ** 2
    feat["rv_pos_20"] = rv_pos.rolling(20).sum()
    feat["rv_neg_20"] = rv_neg.rolling(20).sum()
    feat["bad_vol_share"] = feat["rv_neg_20"] / (feat["rv_pos_20"] + feat["rv_neg_20"]).replace(0, np.nan)
    feat["good_bad_vol_ratio"] = feat["rv_pos_20"] / feat["rv_neg_20"].replace(0, np.nan)
    feat["neg_ret_vol_ewma"] = r.where(r < 0, 0).ewm(span=22, adjust=False).std()
    feat["pos_ret_vol_ewma"] = r.where(r > 0, 0).ewm(span=22, adjust=False).std()

    # Jump/tail proxies
    abs_ret_z = r.abs() / vol_ewma_22.replace(0, np.nan)
    feat["abs_ret_z"] = abs_ret_z
    feat["ret_squared_z"] = (r ** 2) / (vol_ewma_22 ** 2).replace(0, np.nan)
    feat["jump_proxy"] = (abs_ret_z - 2.5).clip(lower=0)
    feat["jump_count_20"] = (abs_ret_z > 2.5).rolling(20).sum()
    feat["downside_jump_count_20"] = (r < -2.0 * vol_ewma_22).rolling(20).sum()
    feat["upside_jump_count_20"] = (r > 2.0 * vol_ewma_22).rolling(20).sum()
    feat["max_abs_ret_20"] = r.abs().rolling(20).max()

    return pd.concat([df, pd.DataFrame(feat, index=df.index)], axis=1).copy()


def add_direction_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Features adicionais usadas em versões recentes do direction.
    Mantém compatibilidade com pickles salvos em modelos_direction.
    """
    r = df["Retorno_diario"]
    close = df["Close"]
    vol_e22 = r.ewm(span=22, adjust=False).std()
    vol_e22_nz = vol_e22.replace(0, np.nan)

    feat = {}

    # Derivadas macro que estavam no executor anterior.
    for w in [10, 20]:
        for nome_macro, colname in [("di", "di"), ("ibov", "ibov")]:
            ext = macro_series.get(nome_macro, pd.DataFrame()).copy()
            if not ext.empty and "Close" in ext.columns:
                ext = ext.reindex(df.index, method="ffill")
                feat[f"deriv{w}_{colname}"] = ext["Close"].rolling(20).mean().pct_change(w)

    ma20 = close.rolling(20).mean()
    feat["deriv_mm100"] = ma20.pct_change(100)

    # Momentum médio prazo.
    mom_12_1 = close.pct_change(252) - close.pct_change(21)
    mom_6_1 = close.pct_change(126) - close.pct_change(21)
    feat["mom_12_1"] = mom_12_1
    feat["mom_6_1"] = mom_6_1

    # Distância max/min 52 semanas.
    max52 = close.rolling(252).max()
    min52 = close.rolling(252).min()
    feat["dist_max52"] = close / max52.replace(0, np.nan) - 1
    feat["dist_min52"] = close / min52.replace(0, np.nan) - 1
    feat["mom_6_1_z"] = mom_6_1 / (vol_e22_nz * np.sqrt(126))
    feat["mom_12_1_z"] = mom_12_1 / (vol_e22_nz * np.sqrt(252))
    feat["dist_max52_z"] = (close - max52) / (max52.replace(0, np.nan) * vol_e22_nz)
    feat["dist_min52_z"] = (close - min52) / (min52.replace(0, np.nan) * vol_e22_nz)
    feat["mom_6_1_rank252"] = mom_6_1.rolling(252).rank(pct=True)
    feat["mom_12_1_rank252"] = mom_12_1.rolling(252).rank(pct=True)
    feat["dist_max52_rank252"] = (close / max52.replace(0, np.nan)).rolling(252).rank(pct=True)

    # Mudança do proxy de rank de vol realizada.
    feat["ivrank_chg5"] = df["ivrank"].diff(5)
    feat["ivrank_chg20"] = df["ivrank"].diff(20)

    # ADX / DI signal.
    high, low, close_s = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close_s.shift()).abs(),
        (low - close_s.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_14 = tr.ewm(span=14, adjust=False).mean()

    dm_plus_raw = (high - high.shift()).clip(lower=0)
    dm_minus_raw = (low.shift() - low).clip(lower=0)
    dm_plus = np.where(dm_plus_raw > dm_minus_raw, dm_plus_raw, 0.0)
    dm_minus = np.where(dm_minus_raw > dm_plus_raw, dm_minus_raw, 0.0)

    di_plus = 100 * pd.Series(dm_plus, index=df.index).ewm(span=14, adjust=False).mean() / atr_14
    di_minus = 100 * pd.Series(dm_minus, index=df.index).ewm(span=14, adjust=False).mean() / atr_14
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-9)
    feat["adx"] = dx.ewm(span=14, adjust=False).mean()
    feat["di_signal"] = di_plus - di_minus

    feat["ma_cross_20_50"] = close.rolling(20).mean() / close.rolling(50).mean().replace(0, np.nan) - 1
    feat["ma_cross_50_200"] = close.rolling(50).mean() / close.rolling(200).mean().replace(0, np.nan) - 1

    # Beta dinâmico contra IBOV.
    ibov = macro_series.get("ibov", pd.DataFrame()).copy()
    if not ibov.empty and "Close" in ibov.columns:
        ibov = ibov.reindex(df.index, method="ffill")
        ret_ibov = ibov["Close"].pct_change()
        cov_roll = r.rolling(60).cov(ret_ibov)
        var_roll = ret_ibov.rolling(60).var()
        feat["beta_60"] = cov_roll / var_roll.replace(0, np.nan)
        feat["beta_chg"] = feat["beta_60"].diff(20)

    feat["rev_1w"] = close.pct_change(5)
    feat["rev_1m"] = close.pct_change(21)

    # Features v9.x alinhadas ao direction_best2.py.
    rsl20 = close / ma20.replace(0, np.nan) - 1
    ret_d_z = r / vol_e22_nz
    hv10 = r.rolling(10).std()
    hv63 = r.rolling(63).std()
    feat["ret20_z"] = close.pct_change(20) / (vol_e22_nz * SQRT_H)
    feat["rsl20_z"] = rsl20 / (vol_e22_nz * SQRT_H)
    feat["ma20_slope_z"] = ma20.pct_change(20) / (vol_e22_nz * SQRT_H)
    feat["tail_pressure_up"] = ret_d_z.clip(lower=0).rolling(20).sum() / SQRT_H
    feat["tail_pressure_down"] = ret_d_z.clip(upper=0).abs().rolling(20).sum() / SQRT_H
    feat["vol_compression_10_63"] = hv10 / hv63.replace(0, np.nan)
    feat["hv10_pct252"] = hv10.rolling(252).rank(pct=True)

    feat["tail_up_volcomp"] = feat["tail_pressure_up"] * feat["vol_compression_10_63"]
    feat["tail_down_volcomp"] = feat["tail_pressure_down"] * feat["vol_compression_10_63"]

    # Bill Williams Trading Chaos — espelha direction_best2.py Bloco K e L
    _bw_h = df["High"]
    _bw_l = df["Low"]
    feat["bw_rev_down"] = _bw_h.shift(2) / (
        (_bw_h + _bw_h.shift(1) + _bw_h.shift(3) + _bw_h.shift(4)) / 4
    ).replace(0, np.nan)
    feat["bw_rev_up"] = _bw_l.shift(2) / (
        (_bw_l + _bw_l.shift(1) + _bw_l.shift(3) + _bw_l.shift(4)) / 4
    ).replace(0, np.nan)

    _bw_med = (_bw_h + _bw_l) / 2
    _bw_jaw   = _bw_med.ewm(alpha=1/13, adjust=False).mean().shift(8)
    _bw_teeth = _bw_med.ewm(alpha=1/8,  adjust=False).mean().shift(5)
    _bw_lips  = _bw_med.ewm(alpha=1/5,  adjust=False).mean().shift(3)
    _bw_cv = (close * vol_e22_nz).replace(0, np.nan)
    feat["alligator_spread_z"] = (_bw_lips - _bw_jaw) / _bw_cv
    feat["alligator_aligned"]  = np.where(
        (_bw_lips > _bw_teeth) & (_bw_teeth > _bw_jaw),  1.0,
        np.where((_bw_jaw > _bw_teeth) & (_bw_teeth > _bw_lips), -1.0, 0.0)
    )
    _bw_ao = _bw_med.rolling(5).mean() - _bw_med.rolling(34).mean()
    feat["ao_z"] = _bw_ao / _bw_cv
    feat["ac_z"] = (_bw_ao - _bw_ao.rolling(5).mean()) / _bw_cv

    # Relativas ao IBOV.
    ibov_rsl = macro_series["ibov"].reindex(df.index, method="ffill")["Close"]
    ibov_rsl20 = ibov_rsl / ibov_rsl.rolling(20).mean().replace(0, np.nan) - 1
    feat["rel_rsl20"] = rsl20 - ibov_rsl20
    feat["rel_mom20"] = close.pct_change(20) - macro_series["ibov"].reindex(df.index, method="ffill")["Close"].pct_change(20)
    feat["rel_mom60"] = close.pct_change(60) - macro_series["ibov"].reindex(df.index, method="ffill")["Close"].pct_change(60)
    feat["rel_accel"] = feat["rel_mom20"] - feat["rel_mom60"]

    return pd.concat([df, pd.DataFrame(feat, index=df.index)], axis=1).copy()


def construir_features_executor(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Retorno_diario"] = df["Close"].pct_change()
    df = add_macro_features(df)
    df = add_volatility_features(df)
    df = add_direction_features(df)

    df["Retorno_20"] = df["Close"].pct_change(20)
    df["Alvo"] = df["Retorno_20"].shift(-PERIODO_ALVO)

    # HAR features (espelha _har_feature_frame do volatility_best.py)
    _vol = df["vol_ewma_22"].astype(float)
    _ret = df["Retorno_diario"].astype(float)
    _regime = df["regime_vol"].astype(float) if "regime_vol" in df.columns else (
        _vol / _vol.rolling(252, min_periods=60).mean()
    )
    df["har_vol_d"] = _vol
    df["har_vol_w"] = _vol.rolling(5, min_periods=3).mean()
    df["har_vol_m"] = _vol.rolling(22, min_periods=10).mean()
    df["har_regime"] = _regime
    df["har_abs_ret_5"] = _ret.abs().rolling(5, min_periods=3).mean()
    df["har_neg_ret_5"] = _ret.clip(upper=0).abs().rolling(5, min_periods=3).sum()

    df = df.replace([np.inf, -np.inf], np.nan)
    return df.copy()


def garantir_features(df: pd.DataFrame, features: List[str], ticker: str, bloco: str) -> pd.DataFrame:
    missing = [f for f in features if f not in df.columns]
    if missing:
        print(f"⚠ [{ticker}] {bloco}: {len(missing)} features ausentes. Preenchendo com 0. Amostra: {missing[:20]}")
        for f in missing:
            df[f] = 0.0
    return df


# =============================================================================
# MODELOS E SINAL
# =============================================================================


def _estruturas_permitidas(vol_model_tipo: str, rv_rank: float) -> set:
    """
    Camada 1 (risco): quais estruturas o contexto permite, ANTES da esperança.
    Naked vetado quando: vol via HAR fallback, ou IV cara (rv_rank>=0.60).
    Spreads sempre permitidos (custo limitado, sem convexidade vendida).

    NÃO veta naked por vol ser "moderada" em vez de "forte": isso é nível
    operacional (decidido pelos gates A/B), não risco. Exigir vol_forte aqui
    proibia naked em todo sinal B (ex.: TAEE11 naked_45=0.82 → spread=0.07).
    Hipóteses a calibrar com realizados (M9) — rv_rank e HAR.
    """
    permitidas = {"spread_35_20", "spread_45_25", "spread_45_15"}
    naked_ok = (
        str(vol_model_tipo).upper() in ("ML", "RV_RANK")   # previsão confiável
        and rv_rank < 0.60                                  # IV não-cara
    )
    if naked_ok:
        permitidas |= {"naked_30", "naked_45"}
    return permitidas


def avaliar_estruturas_opc(
    M_ops: "pd.Series",
    vol_model_tipo: str = "ML",
    rv_rank: float = 0.5,
    vol_forte: int = 0,
) -> "tuple[str, dict, str, str]":
    """
    Avalia payoff terminal das 4 estruturas sobre M_ops (DP).
    Retorna (estrutura_otima, detalhes, estruturas_permitidas_str, estrutura_vetada_por).

    Duas camadas:
      1. Elegibilidade por risco (_estruturas_permitidas) — veta naked em contextos frágeis.
      2. Ranking por ROI entre as elegíveis com E−SE>0 (anti winner's curse).
    As 4 esperanças individuais ficam sempre em detalhes para auditoria.
    """
    detalhes = {}
    n = len(M_ops)
    for nome, e in ESTRUTURAS_OPC.items():
        custo_ef = e["custo"] * (FATOR_CUSTO_2_PERNAS if e["k_short"] is not None else 1.0)
        pay = np.maximum(M_ops - e["k_long"], 0.0)
        if e["k_short"] is not None:
            pay = pay - np.maximum(M_ops - e["k_short"], 0.0)
        pay = pay - custo_ef
        E  = float(pay.mean()) if n > 0 else -custo_ef
        SE = float(pay.std() / np.sqrt(n)) if n > 1 else float("inf")
        detalhes[nome] = dict(E=round(E, 4), SE=round(SE, 4),
                              roi=round(E / custo_ef, 4), custo_ef=round(custo_ef, 4))

    permitidas  = _estruturas_permitidas(vol_model_tipo, rv_rank)
    perm_str    = ",".join(sorted(permitidas))

    # melhor sem camada de risco (para detectar veto)
    validas_raw = {k: v for k, v in detalhes.items() if v["E"] - v["SE"] > 0}
    melhor_raw  = max(validas_raw, key=lambda k: validas_raw[k]["roi"]) if validas_raw else None

    validas = {k: v for k, v in validas_raw.items() if k in permitidas}
    if not validas:
        # distingue: veto por risco (tinha candidata mas foi bloqueada) vs sem edge
        vetada_por = "risco" if (melhor_raw and melhor_raw not in permitidas) else "esperanca"
        return "nenhuma", detalhes, perm_str, vetada_por

    # ── ETAPA 1: melhor DENTRO de cada família, por ESPERANÇA absoluta ──
    # Famílias têm perfil de risco equivalente; sistema é limitado por
    # oportunidade, não capital → maximizar DP extraído do sinal.
    FAMILIAS = {
        "naked":  ["naked_30", "naked_45"],
        "spread": ["spread_35_20", "spread_45_25", "spread_45_15"],
    }
    campeao_familia = {}
    for fam, membros in FAMILIAS.items():
        elegiveis = {k: validas[k] for k in membros if k in validas}
        if elegiveis:
            campeao_familia[fam] = max(elegiveis, key=lambda k: elegiveis[k]["E"])

    if not campeao_familia:
        vetada_por = "risco" if (melhor_raw and melhor_raw not in permitidas) else "esperanca"
        return "nenhuma", detalhes, perm_str, vetada_por

    # ── ETAPA 2: entre campeões de família, escolher por ROI ──
    # naked vs spread: ROI respeita custo de capital e execução (2 pernas).
    # Margem protege naked como default: só migra para spread se ROI do spread
    # superar o do naked em ROI_MARGEM_TROCA.
    if "naked" in campeao_familia and "spread" in campeao_familia:
        cand_naked  = campeao_familia["naked"]
        cand_spread = campeao_familia["spread"]
        roi_naked   = validas[cand_naked]["roi"]
        roi_spread  = validas[cand_spread]["roi"]
        if roi_naked > 0 and roi_spread < ROI_MARGEM_TROCA * roi_naked:
            melhor = cand_naked
        else:
            melhor = cand_spread if roi_spread > roi_naked else cand_naked
    else:
        melhor = next(iter(campeao_familia.values()))

    vetada_por = ""
    if melhor_raw and melhor_raw not in permitidas and melhor_raw != melhor:
        vetada_por = "risco"

    return melhor, detalhes, perm_str, vetada_por

def carregar_modelos_ticker(ticker: str):
    base = Path(pasta_do_arquivo)

    arq_vol = base / "modelos_volatility" / f"model_vol_{ticker}.pkl"
    arq_vol_features = base / "modelos_volatility" / f"features_vol_{ticker}.pkl"
    arq_vol_scaler = base / "modelos_volatility" / f"scaler_vol_{ticker}.pkl"
    arq_vol_scaler_features = base / "modelos_volatility" / f"features_scaler_vol_{ticker}.pkl"

    arq_dir = base / "modelos_direction" / f"model_direc_{ticker}.pkl"
    arq_dir_features = base / "modelos_direction" / f"features_direc_{ticker}.pkl"
    arq_dir_scaler = base / "modelos_direction" / f"scaler_direc_{ticker}.pkl"
    arq_dir_scaler_features = base / "modelos_direction" / f"features_scaler_direc_{ticker}.pkl"

    required = [
        arq_vol, arq_vol_features, arq_vol_scaler, arq_vol_scaler_features,
        arq_dir, arq_dir_features, arq_dir_scaler, arq_dir_scaler_features,
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Arquivos de modelo ausentes para {ticker}: {missing}")

    modelo_vol = joblib.load(arq_vol)
    features_vol = joblib.load(arq_vol_features)
    scaler_vol = joblib.load(arq_vol_scaler)
    features_vol_scaler = joblib.load(arq_vol_scaler_features)

    modelo_dir = joblib.load(arq_dir)
    features_dir = joblib.load(arq_dir_features)
    scaler_dir = joblib.load(arq_dir_scaler)
    features_dir_scaler = joblib.load(arq_dir_scaler_features)

    # HAR fallback (opcional — só existe quando ML não bateu benchmark no treino)
    arq_har_model = base / "modelos_volatility" / f"model_vol_har_{ticker}.pkl"
    arq_har_scaler = base / "modelos_volatility" / f"scaler_vol_har_{ticker}.pkl"
    arq_har_features = base / "modelos_volatility" / f"features_vol_har_{ticker}.pkl"
    modelo_vol_har = joblib.load(arq_har_model) if arq_har_model.exists() else None
    scaler_vol_har = joblib.load(arq_har_scaler) if arq_har_scaler.exists() else None
    features_vol_har = (
        list(joblib.load(arq_har_features)) if arq_har_features.exists()
        else ["har_vol_d", "har_vol_w", "har_vol_m", "har_regime", "har_abs_ret_5", "har_neg_ret_5"]
    )

    return {
        "modelo_vol": modelo_vol,
        "features_vol": list(features_vol),
        "scaler_vol": scaler_vol,
        "features_vol_scaler": list(features_vol_scaler),
        "modelo_dir": modelo_dir,
        "features_dir": list(features_dir),
        "scaler_dir": scaler_dir,
        "features_dir_scaler": list(features_dir_scaler),
        "modelo_vol_har": modelo_vol_har,
        "scaler_vol_har": scaler_vol_har,
        "features_vol_har": features_vol_har,
    }


def calcular_motivo_bloqueio_executor(
    lado_live: int,
    gate_call_model: int,
    gate_put_model: int,
    vol_model_ok: int,
    vol_expanding_live: bool,
    rv_rank_ok: bool,
    razao_sinal: float,
    ordem: str,
    motivo_volatility: str,
) -> str:
    if ordem != "Neutro":
        if str(ordem).startswith("Alerta"):
            return "alerta_executor_nivel_B"
        if str(ordem).startswith("Monitorar"):
            return "monitorar_e6_quase_nivel_a"
        return "aprovado_executor_nivel_A"

    if lado_live == 0:
        return "sem_direcao_live"
    if razao_sinal < RAZAO_MARGINAL:
        return "razao_sinal_baixa"
    if razao_sinal < RAZAO_MINIMO:
        return "razao_sinal_marginal_sem_condicoes"

    if lado_live == 1 and gate_call_model != 1:
        if vol_model_ok != 1:
            return f"volatility_gate_bloqueado:{motivo_volatility}"
        return "direction_gate_call_bloqueado"

    if lado_live == -1 and gate_put_model != 1:
        if vol_model_ok != 1:
            return f"volatility_gate_bloqueado:{motivo_volatility}"
        return "direction_gate_put_bloqueado"

    if not vol_expanding_live:
        return "vol_live_sem_expansao"
    if not rv_rank_ok:
        return "rv_rank_alto_proxy_custo_vol"

    return "bloqueio_nao_classificado"


def gerar_prediction_executor(df: pd.DataFrame, gates: pd.Series) -> pd.DataFrame:
    """
    Regra final para compra de opção:
        CALL:
            direction_model_ok_exec_up = 1
            volatility_model_ok_exec = 1
            direc_prediction > 0
            magnitude >= RAZAO_MINIMO
            vol_prediction > 1.05
            rv_rank_252/ivrank < limite

        PUT:
            direction_model_ok_exec_down = 1
            volatility_model_ok_exec = 1
            direc_prediction < 0
            magnitude >= RAZAO_MINIMO
            vol_prediction > 1.05
            rv_rank_252/ivrank < limite

    Não existe mais exceção “sinal muito forte opera sem vol expansion”.
    No v5.3, prediction_operacional também marca nível B como alerta, mas
    prediction continua restrito ao nível A para compra de opção.
    """
    out = df.copy()

    threshold_base = SQRT_H * out["vol_ewma_22"] * DELTA30_SIGMA_MULT
    threshold_tail = threshold_base * TAIL_MULTIPLIER
    razao_sinal    = out["direc_prediction"].abs() / threshold_tail.replace(0, np.nan)
    razao_suspeita = (razao_sinal > RAZAO_SANIDADE_MAX).astype(int)

    thr_exp_moderada = _as_float(gates.get("thr_pred_exp_moderada", np.nan), np.nan)
    thr_exp_forte = _as_float(gates.get("thr_pred_exp_forte", np.nan), np.nan)
    thr_compressao = _as_float(gates.get("thr_pred_compressao", np.nan), np.nan)

    if not np.isfinite(thr_exp_moderada):
        thr_exp_moderada = VOL_EXPANSION_MIN
    if not np.isfinite(thr_exp_forte):
        thr_exp_forte = VOL_EXPANSION_MIN
    if not np.isfinite(thr_compressao):
        thr_compressao = 0.97

    thr_exp_moderada = max(1.00, float(thr_exp_moderada))
    thr_exp_forte = max(VOL_EXPANSION_MIN, float(thr_exp_forte))

    vol_expanding_moderada = out["vol_prediction"] >= thr_exp_moderada
    vol_expanding_forte = out["vol_prediction"] >= thr_exp_forte
    vol_compression_live = out["vol_prediction"] <= thr_compressao
    rv_rank_col = "rv_rank_252" if "rv_rank_252" in out.columns else "ivrank"
    rv_rank_ok = out[rv_rank_col] < RV_RANK_MAX_COMPRA

    # [E2] zona cinza de vol: 0.98 <= pred < thr_forte, aprovado se vol_exec_score_adj >= 0.60
    vol_exec_score_adj_gate = _as_float(gates.get("vol_exec_score_adj", np.nan), 0.0)
    vol_cinza = (out["vol_prediction"] >= VOL_EXPANSION_CINZA_MIN) & (~vol_expanding_forte)
    vol_expanding_efetivo = vol_expanding_forte | (vol_cinza & (vol_exec_score_adj_gate >= 0.60))

    # [E1] zona marginal de sinal: 0.60 <= razao < 0.80, aprovado se score_adj>0.55 e rv_rank<0.50
    rv_rank_marginal = out[rv_rank_col] < 0.50
    razao_forte = razao_sinal >= RAZAO_MINIMO
    razao_marginal_aprovado = (
        (razao_sinal >= RAZAO_MARGINAL) & (razao_sinal < RAZAO_MINIMO) &
        (vol_exec_score_adj_gate > 0.55) & rv_rank_marginal
    )
    razao_ok = razao_forte | razao_marginal_aprovado

    gate_call = _as_int_flag(gates.get("gate_call_model", 0))
    gate_put = _as_int_flag(gates.get("gate_put_model", 0))
    gate_call_nivel = str(gates.get("gate_call_nivel", "")).strip().upper()
    gate_put_nivel = str(gates.get("gate_put_nivel", "")).strip().upper()
    gate_call_operacional = gate_call_nivel in PROCESSAR_NIVEIS_OPERACIONAIS
    gate_put_operacional = gate_put_nivel in PROCESSAR_NIVEIS_OPERACIONAIS

    call_base_cond = (
        (out["direc_prediction"] > 0) & razao_ok & rv_rank_ok
    )
    put_base_cond = (
        (out["direc_prediction"] < 0) & razao_ok & rv_rank_ok
    )
    call_live_cond = call_base_cond & vol_expanding_efetivo
    put_live_cond = put_base_cond & vol_expanding_efetivo
    call_vol_live = vol_expanding_efetivo if gate_call_nivel == "A" else vol_expanding_moderada
    put_vol_live = vol_expanding_efetivo if gate_put_nivel == "A" else vol_expanding_moderada
    call_live_operacional_cond = call_base_cond & call_vol_live
    put_live_operacional_cond = put_base_cond & put_vol_live
    call_cond = (gate_call == 1) & call_live_cond
    put_cond = (gate_put == 1) & put_live_cond
    call_operacional_cond = gate_call_operacional & call_live_operacional_cond
    put_operacional_cond = gate_put_operacional & put_live_operacional_cond

    out["threshold_base"] = threshold_base
    out["threshold_tail"] = threshold_tail
    out["razao_sinal"] = razao_sinal
    out["forca_sinal"] = np.where(  # [E1]
        razao_sinal >= RAZAO_MINIMO, "forte",
        np.where(razao_sinal >= RAZAO_MARGINAL, "marginal", "fraco")
    )
    out["vol_threshold_exp_moderada"] = thr_exp_moderada
    out["vol_threshold_exp_forte"] = thr_exp_forte
    out["vol_threshold_compressao"] = thr_compressao
    out["vol_expanding_moderada_live"] = vol_expanding_moderada.astype(int)
    out["vol_expanding_forte_live"] = vol_expanding_forte.astype(int)
    out["vol_compression_live"] = vol_compression_live.astype(int)
    out["vol_expanding_live"] = vol_expanding_moderada.astype(int)
    out["vol_live_classe"] = np.where(  # [E2]
        vol_expanding_forte, "expansao",
        np.where(vol_cinza, "neutro_zona_cinza", "compressao")
    )
    out["rv_rank_ok"] = rv_rank_ok.astype(int)
    out["prediction"] = np.where(call_cond, 1, np.where(put_cond, -1, 0))
    out["prediction_operacional"] = np.where(
        call_operacional_cond,
        1,
        np.where(put_operacional_cond, -1, 0),
    )
    out["prediction_nivel"] = np.where(
        out["prediction_operacional"] > 0,
        gate_call_nivel,
        np.where(out["prediction_operacional"] < 0, gate_put_nivel, ""),
    )

    # Razão suspeita: rebaixa nível A→B (flag, não veto — gate de esperança faz o corte fino).
    out["razao_suspeita"] = razao_suspeita
    out["prediction_nivel"] = np.where(
        (out["prediction_nivel"] == "A") & (razao_suspeita == 1),
        "B",
        out["prediction_nivel"],
    )

    return out


# =============================================================================
# LOOP PRINCIPAL
# =============================================================================

# Pipeline roda apenas em execução direta (python executor.py).
# Import como biblioteca (ex.: realizados.py) carrega só funções/constantes.
if __name__ == "__main__":
    if not _skip_download():
        _rodar_download_database()
    df_final_rows: List[Dict[str, Any]] = []
    diagnostico_rows: List[Dict[str, Any]] = []

    if PREFILTRAR_PELOS_EXECUTOR_INPUTS and not df_gates.empty:
        tickers_processar = [
            t for t in TICKERS
            if t in df_gates.index and _as_int_flag(df_gates.loc[t, "gate_processar_executor"]) == 1
        ]
        print(f"Tickers a processar após níveis {PROCESSAR_NIVEIS_OPERACIONAIS}: {len(tickers_processar)}")
        print(tickers_processar)
    else:
        tickers_processar = list(TICKERS)
        print(f"Tickers a processar sem pré-filtro: {len(tickers_processar)}")

    if tickers_processar:
        macro_series = _load_macro_series()
    else:
        print("Sem gates operacionais; pulando carga de macros e processamento live.")

    for ticker in tickers_processar:
        try:
            if ticker not in df_gates.index:
                diagnostico_rows.append({
                    "ticker": ticker,
                    "status": "bloqueado",
                    "motivo": "ticker_ausente_executor_inputs",
                })
                continue

            gates = df_gates.loc[ticker]

            print("\n" + "=" * 90)
            print(f"PROCESSANDO {ticker}")
            print(
                f"Gates: CALL={gates.get('gate_call_model')} | PUT={gates.get('gate_put_model')} | "
                f"Nível={gates.get('gate_any_nivel')} | "
                f"VolExec={gates.get('volatility_model_ok_exec')} | "
                f"DirClasse={gates.get('classe_executor_direction')} | "
                f"VolClasse={gates.get('classe_executor_volatility')}"
            )

            df = importar_dados(ticker, hoje)
            df = construir_features_executor(df)

            # Janela operacional mantida do executor antigo.
            df = df[df.index >= DATA_LIMITE_ANALISE]
            df = df[df.index <= hoje]
            df = df.replace([np.inf, -np.inf], np.nan).copy()

            modelos = carregar_modelos_ticker(ticker)

            # Compatibilidade com features do scaler/modelo.
            required_all = (
                modelos["features_vol_scaler"] + modelos["features_vol"] +
                modelos["features_dir_scaler"] + modelos["features_dir"]
            )
            df = garantir_features(df, sorted(set(required_all)), ticker, "features_modelos")

            df_model = df.dropna(subset=list(set(required_all))).copy()
            if len(df_model) < 30:
                raise ValueError(f"Histórico operacional insuficiente após dropna: {len(df_model)} linhas")

            vol_scaled = pd.DataFrame(
                modelos["scaler_vol"].transform(df_model[modelos["features_vol_scaler"]]),
                columns=modelos["features_vol_scaler"],
                index=df_model.index,
            )
            dir_scaled = pd.DataFrame(
                modelos["scaler_dir"].transform(df_model[modelos["features_dir_scaler"]]),
                columns=modelos["features_dir_scaler"],
                index=df_model.index,
            )

            use_har = _as_int_flag(gates.get("use_har_fallback", 0))
            use_rv_rank_gate = _as_int_flag(gates.get("gate_vol_rv_rank", 0))
            if use_har == 1 and modelos["modelo_vol_har"] is not None and modelos["scaler_vol_har"] is not None:
                har_feats = modelos["features_vol_har"]
                df_har = garantir_features(df_model.copy(), har_feats, ticker, "har_features")
                valid_har = df_har[har_feats].notna().all(axis=1)
                df_model["vol_prediction"] = np.nan
                if valid_har.any():
                    har_scaled = modelos["scaler_vol_har"].transform(df_har.loc[valid_har, har_feats])
                    df_model.loc[valid_har, "vol_prediction"] = modelos["modelo_vol_har"].predict(har_scaled)
                df_model["vol_prediction"] = df_model["vol_prediction"].fillna(1.0)
                df_model["vol_model_tipo"] = "HAR"
                print(f"  [{ticker}] vol_prediction via HAR fallback")
            elif use_rv_rank_gate == 1:
                # Sem modelo aprovado mas expansion_rank forte: usa ML pkl disponível
                # para live prediction, rotulado como RV_RANK para rastreabilidade.
                df_model["vol_prediction"] = modelos["modelo_vol"].predict(vol_scaled[modelos["features_vol"]])
                df_model["vol_model_tipo"] = "RV_RANK"
                print(f"  [{ticker}] vol_prediction via RV_RANK gate (sem_modelo + expansion_rank forte)")
            else:
                df_model["vol_prediction"] = modelos["modelo_vol"].predict(vol_scaled[modelos["features_vol"]])
                df_model["vol_model_tipo"] = "ML"
            df_model["vol_prediction"] = df_model["vol_prediction"].clip(0.20, 5.0)
            df_model["direc_prediction"] = modelos["modelo_dir"].predict(dir_scaled[modelos["features_dir"]])

            df_model = gerar_prediction_executor(df_model, gates)

            # Métricas históricas desde DATA_LIMITE_ANALISE.
            # No v5.3, nível B é alerta operacional, então as métricas acompanham
            # prediction_operacional. prediction segue restrito a compra de opção nível A.
            pred_metrica = df_model["prediction_operacional"]
            valid_ops = pred_metrica != 0
            acertos = ((pred_metrica * df_model["Alvo"]) > 0).sum()
            erros = ((pred_metrica * df_model["Alvo"]) < 0).sum()
            numero_operacoes = int(acertos + erros)

            # Alvo = Close.pct_change(20).shift(-20): retorno tradável de liquidação.
            # Distinto do alvo suavizado MA20 usado no treino do direction (nunca alterar aquele).
            vol_ops = df_model["vol_ewma_22"]

            resultado_em_dp = (pred_metrica * df_model["Alvo"]) / (vol_ops * SQRT_H)
            ops_mask  = pred_metrica != 0
            # M_ops: retorno direcional em DP; usa Alvo (Close T+20) — liquidação real.
            M_ops_bruto = (np.sign(pred_metrica) * df_model["Alvo"])[ops_mask] / (vol_ops[ops_mask] * SQRT_H)
            # Ajuste de carry: strike das opções é fixado no forward F = S×(1+carry_20).
            # CALL: carry prejudica (precisa subir mais que o forward) → M_adj = M - carry_dp
            # PUT:  carry beneficia (forward acima do spot encurta distância ao strike put) → M_adj = M + carry_dp
            # Equivalente: M_adj = M - side × carry_dp, onde side = sign(pred) e carry_dp = CARRY_20 / (vol × √20)
            _carry_dp     = CARRY_20 / (vol_ops[ops_mask] * SQRT_H)
            _side         = np.sign(pred_metrica)[ops_mask]
            # Convexidade lognormal: sob BS, k_put = k_call - σ√T para o mesmo delta absoluto.
            # k_long está calibrado para CALL; PUTs precisam de σ√T extra em M para usar o mesmo k.
            _convexity_dp = vol_ops[ops_mask] * SQRT_H
            M_ops = M_ops_bruto - _side * _carry_dp + np.where(_side < 0, _convexity_dp, 0.0)
            lucro_ops = np.maximum(M_ops - STRIKE_DELTA30_DP, 0) - PREMIO_DELTA30_DP
            # gmdp: payoff de opção condicional a acerto direcional no Close (M_ops > 0).
            mediana_ganho_em_dp = float(lucro_ops[M_ops > 0].median()) if (M_ops > 0).any() else -PREMIO_DELTA30_DP
            mediana_perda_em_dp = M_ops[M_ops < 0].median() if (M_ops < 0).any() else 0
            resultados_operacoes = pred_metrica * df_model["Alvo"]
            mediana_ganho = resultados_operacoes[resultados_operacoes > 0].median() if (resultados_operacoes > 0).any() else 0

            taxa_acertos = acertos / numero_operacoes if numero_operacoes > 0 else 0
            tax_acerto_opc      = float((M_ops > BREAKEVEN_DELTA30_DP).mean()) if ops_mask.any() else 0.0
            ganho_medio_liq_opc = float((M_ops[M_ops > BREAKEVEN_DELTA30_DP] - BREAKEVEN_DELTA30_DP).mean()) if (M_ops > BREAKEVEN_DELTA30_DP).any() else 0.0
            esperanca_opc       = float(lucro_ops.mean()) if ops_mask.any() else -PREMIO_DELTA30_DP
            # S-C: erro padrão da esperança — exposição ao ruído amostral.
            se_esperanca_opc    = float(lucro_ops.std() / np.sqrt(len(lucro_ops))) if len(lucro_ops) > 1 else np.inf
            esperanca_ganho     = esperanca_opc

            last = df_model.iloc[-1]

            frequencia_operacao = numero_operacoes / len(df_model) if len(df_model) > 0 else 0

            # gmdp_direction: payoff de opção usando só sinal direcional (sem filtro vol).
            pred_dir_only = np.sign(df_model["direc_prediction"]).replace(0, np.nan).dropna()
            alvo_dir = df_model["Alvo"].reindex(pred_dir_only.index)
            vol_dir  = df_model["vol_ewma_22"].reindex(pred_dir_only.index)
            res_dir_dp     = (pred_dir_only * alvo_dir) / (vol_dir * SQRT_H)
            lucro_dir_dp   = np.maximum(res_dir_dp - STRIKE_DELTA30_DP, 0) - PREMIO_DELTA30_DP
            gmdp_direction = float(lucro_dir_dp[res_dir_dp > 0].median()) if (res_dir_dp > 0).any() else -PREMIO_DELTA30_DP
            numero_operacoes_direction = int((pred_dir_only * alvo_dir).notna().sum())

            # gmdp_ivrank_baixo: direction puro filtrado por rv_rank_252 < 0.50
            # (opções baratas — exclui momentos de vol alta onde prêmio é caro).
            rv_rank_col = "rv_rank_252" if "rv_rank_252" in df_model.columns else "ivrank"
            mask_iv_baixo = pred_dir_only.index.isin(df_model.index[df_model[rv_rank_col] < 0.50])
            pred_dir_iv   = pred_dir_only[mask_iv_baixo]
            alvo_dir_iv   = df_model["Alvo"].reindex(pred_dir_iv.index)
            vol_dir_iv    = df_model["vol_ewma_22"].reindex(pred_dir_iv.index)
            res_dir_iv_dp   = (pred_dir_iv * alvo_dir_iv) / (vol_dir_iv * SQRT_H)
            lucro_iv_dp     = np.maximum(res_dir_iv_dp - STRIKE_DELTA30_DP, 0) - PREMIO_DELTA30_DP
            gmdp_ivrank_baixo = float(lucro_iv_dp[res_dir_iv_dp > 0].median()) if (res_dir_iv_dp > 0).any() else -PREMIO_DELTA30_DP
            numero_operacoes_ivrank = int((pred_dir_iv * alvo_dir_iv).notna().sum())

            last_signal = int(last["prediction"])
            last_signal_operacional = int(last.get("prediction_operacional", last_signal))
            nivel_live = str(last.get("prediction_nivel", "")).strip().upper()

            if last_signal == 1:
                ordem = "Compra CALL"
            elif last_signal == -1:
                ordem = "Compra PUT"
            elif last_signal_operacional == 1 and nivel_live == "B":
                ordem = "Alerta CALL"
            elif last_signal_operacional == -1 and nivel_live == "B":
                ordem = "Alerta PUT"
            else:
                # [E6] "Monitorar": vol_model_ok=1, direção ok, razao forte, vol entre 1.00 e thr_forte.
                # Não é operação — apenas vigilância para candidatos quase nível A.
                vol_pred_live = float(last.get("vol_prediction", 0.0))
                thr_forte_live = float(last.get("vol_threshold_exp_forte", VOL_EXPANSION_MIN))
                razao_live = float(last["razao_sinal"]) if pd.notna(last.get("razao_sinal")) else 0.0
                vol_model_ok_live = int(_as_int_flag(gates.get("volatility_model_ok_exec", 0)))
                dir_up_ok  = int(_as_int_flag(gates.get("direction_model_ok_exec_up", 0)))
                dir_dn_ok  = int(_as_int_flag(gates.get("direction_model_ok_exec_down", 0)))
                dir_pred   = float(last.get("direc_prediction", 0.0))
                _quase_a_cond = (
                    vol_model_ok_live == 1
                    and razao_live >= RAZAO_MINIMO
                    and VOL_MONITORAR_MIN <= vol_pred_live < thr_forte_live
                )
                if _quase_a_cond and dir_up_ok == 1 and dir_pred > 0:
                    ordem = "Monitorar CALL"
                elif _quase_a_cond and dir_dn_ok == 1 and dir_pred < 0:
                    ordem = "Monitorar PUT"
                else:
                    ordem = "Neutro"

            # [Fix4] Rebaixa Compra → Alerta quando artefatos são de datas diferentes.
            if DEGRADAR_PARA_ALERTA_B and ordem == "Compra CALL":
                ordem = "Alerta CALL"
            elif DEGRADAR_PARA_ALERTA_B and ordem == "Compra PUT":
                ordem = "Alerta PUT"

            # Razão suspeita: rebaixa Compra → Alerta (nível A → B).
            _razao_suspeita_live = int(last.get("razao_suspeita", 0))
            if _razao_suspeita_live and ordem == "Compra CALL":
                ordem = "Alerta CALL"
            elif _razao_suspeita_live and ordem == "Compra PUT":
                ordem = "Alerta PUT"

            sinal_target = last_signal if last_signal != 0 else last_signal_operacional
            if sinal_target == 1:
                target = last["Close"] * (1 + DELTA30_SIGMA_MULT * last["vol_ewma_22"] * SQRT_H)
            elif sinal_target == -1:
                target = last["Close"] * (1 - DELTA30_SIGMA_MULT * last["vol_ewma_22"] * SQRT_H)
            else:
                target = 0.0

            lado_live = 1 if last["direc_prediction"] > 0 else (-1 if last["direc_prediction"] < 0 else 0)
            nivel_gate_any = str(gates.get("gate_any_nivel", "")).strip().upper()
            vol_live_para_motivo = (
                bool(last["vol_expanding_forte_live"]) if nivel_gate_any == "A"
                else bool(last["vol_expanding_moderada_live"])
            )
            motivo_executor = calcular_motivo_bloqueio_executor(
                lado_live=lado_live,
                gate_call_model=_as_int_flag(gates.get("gate_call_model", 0)),
                gate_put_model=_as_int_flag(gates.get("gate_put_model", 0)),
                vol_model_ok=_as_int_flag(gates.get("volatility_model_ok_exec", 0)),
                vol_expanding_live=vol_live_para_motivo,
                rv_rank_ok=bool(last["rv_rank_ok"]),
                razao_sinal=float(last["razao_sinal"]) if pd.notna(last["razao_sinal"]) else 0.0,
                ordem=ordem,
                motivo_volatility=str(gates.get("motivo_bloqueio_volatility", "")),
            )

            vol_prediction_atual = float(last["vol_prediction"])
            variacao_vol_prevista = (vol_prediction_atual - 1.0) * 100
            rv_rank_atual = float(last.get("rv_rank_252", last.get("ivrank", np.nan)))

            # T1c: seleção empírica de estrutura de opção.
            _vol_tipo_estr = str(last.get("vol_model_tipo", gates.get("vol_model_tipo", "ML")))
            _rv_rank_estr  = float(rv_rank_atual) if pd.notna(rv_rank_atual) else 0.5
            _vol_forte_estr = int(last.get("vol_expanding_forte_live", 0))
            if ops_mask.any():
                estrutura_otima, estr_det, estr_perm_str, estr_vetada_por = avaliar_estruturas_opc(
                    M_ops, _vol_tipo_estr, _rv_rank_estr, _vol_forte_estr
                )
            else:
                estrutura_otima, estr_det, estr_perm_str, estr_vetada_por = "nenhuma", {}, "", "esperanca"
            esp_estr   = estr_det.get(estrutura_otima, {}).get("E", -PREMIO_DELTA30_DP) if estrutura_otima != "nenhuma" else -PREMIO_DELTA30_DP
            roi_estr   = estr_det.get(estrutura_otima, {}).get("roi", 0.0)
            esp_n30    = estr_det.get("naked_30",     {}).get("E", np.nan)
            esp_n45    = estr_det.get("naked_45",     {}).get("E", np.nan)
            esp_s3520  = estr_det.get("spread_35_20", {}).get("E", np.nan)
            esp_s4525  = estr_det.get("spread_45_25", {}).get("E", np.nan)
            esp_s4515  = estr_det.get("spread_45_15", {}).get("E", np.nan)

            print(f"Resultados para {ticker}:")
            print(f"Ordem: {ordem}")
            print(f"Nível live: {nivel_live or gates.get('gate_any_nivel')}")
            print(f"Motivo executor: {motivo_executor}")
            print(f"Número de operações históricas: {numero_operacoes}")
            print(f"Taxa de acertos: {taxa_acertos*100:.1f}%")
            print(f"GMDP: {mediana_ganho_em_dp:.2f}")
            print(f"Esperança ganho DP: {esperanca_ganho:.2f}")
            print(f"Direção live: {last['direc_prediction']:.6f}")
            print(f"Razão sinal live: {last['razao_sinal']:.2f}")
            print(f"Vol previsão ratio live: {vol_prediction_atual:.3f} ({variacao_vol_prevista:+.1f}%)")
            print(
                f"Vol thresholds mod/forte/comp: "
                f"{last['vol_threshold_exp_moderada']:.3f} / {last['vol_threshold_exp_forte']:.3f} / "
                f"{last['vol_threshold_compressao']:.3f}"
            )
            print(f"rv_rank_252/ivrank proxy: {rv_rank_atual:.2f}")
            print(f"Target: {target:.2f}")

            df_final_rows.append({
                "data": df_model.index[-1],
                "ticker": ticker,
                "ordem": ordem,
                "direction": "long" if last["direc_prediction"] > 0 else ("short" if last["direc_prediction"] < 0 else ""),
                "executor_signal_ok": int(last_signal_operacional != 0),
                "opcao_compra_ok": int(last_signal != 0),
                "operacao_nivel_live": ordem if str(ordem).startswith("Monitorar") else nivel_live,
                "motivo_bloqueio_executor": motivo_executor,
                "numero_operacoes": numero_operacoes,
                "frequencia_operacao": round(float(frequencia_operacao), 4),
                "tx_acerto": round(float(taxa_acertos), 4),
                "tax_acerto_opc": round(tax_acerto_opc, 4),
                "ganho_medio_liq_opc": round(ganho_medio_liq_opc, 4),
                "gmdp": round(float(mediana_ganho_em_dp), 4),
                "gmdp_direction": round(gmdp_direction, 4),
                "numero_operacoes_direction": numero_operacoes_direction,
                "gmdp_ivrank_baixo": round(gmdp_ivrank_baixo, 4),
                "numero_operacoes_ivrank": numero_operacoes_ivrank,
                "esperanca_opcao_dp": round(float(esperanca_ganho), 4),
                "se_esperanca_opc": round(se_esperanca_opc, 4) if np.isfinite(se_esperanca_opc) else np.nan,
                "estrutura_otima": estrutura_otima,
                "estruturas_permitidas": estr_perm_str,
                "estrutura_vetada_por": estr_vetada_por,
                "esperanca_estrutura_dp": round(float(esp_estr), 4),
                "roi_estrutura": round(float(roi_estr), 4),
                "esp_naked_30": round(float(esp_n30), 4) if pd.notna(esp_n30) else np.nan,
                "esp_naked_45": round(float(esp_n45), 4) if pd.notna(esp_n45) else np.nan,
                "esp_spread_35_20": round(float(esp_s3520), 4) if pd.notna(esp_s3520) else np.nan,
                "esp_spread_45_25": round(float(esp_s4525), 4) if pd.notna(esp_s4525) else np.nan,
                "esp_spread_45_15": round(float(esp_s4515), 4) if pd.notna(esp_s4515) else np.nan,
                "volatilidade": round(float(last["vol_ewma_22"]), 4),
                "rv_rank_252": round(float(rv_rank_atual), 4) if pd.notna(rv_rank_atual) else np.nan,
                "close": round(float(last["Close"]), 2),
                "target": round(float(target), 2),
                "ganho_total": round(float(esperanca_ganho * frequencia_operacao), 4),
                "direc_prediction": round(float(last["direc_prediction"]), 6),
                "vol_prediction_ratio": round(float(vol_prediction_atual), 6),
                "var_vol_prevista_pct": round(float(variacao_vol_prevista), 2),
                "vol_expanding_live": int(last["vol_expanding_live"]),
                "vol_expanding_moderada_live": int(last["vol_expanding_moderada_live"]),
                "vol_expanding_forte_live": int(last["vol_expanding_forte_live"]),
                "vol_compression_live": int(last["vol_compression_live"]),
                "vol_threshold_exp_moderada": round(float(last["vol_threshold_exp_moderada"]), 6),
                "vol_threshold_exp_forte": round(float(last["vol_threshold_exp_forte"]), 6),
                "vol_threshold_compressao": round(float(last["vol_threshold_compressao"]), 6),
                "rv_rank_ok": int(last["rv_rank_ok"]),
                "prediction_operacional": int(last_signal_operacional),
                "threshold_tail": round(float(last["threshold_tail"]), 6),
                "razao_sinal": round(float(last["razao_sinal"]), 4) if pd.notna(last["razao_sinal"]) else np.nan,
                "razao_suspeita": int(last.get("razao_suspeita", 0)),
                "rebaixado_razao_suspeita": int(_razao_suspeita_live and ordem in ("Alerta CALL", "Alerta PUT")),
                "forca_sinal": str(last.get("forca_sinal", "fraco")),
                "vol_live_classe": str(last.get("vol_live_classe", "compressao")),
                "gate_call_model": int(gates.get("gate_call_model", 0)),
                "gate_put_model": int(gates.get("gate_put_model", 0)),
                "gate_call_nivel": gates.get("gate_call_nivel", ""),
                "gate_put_nivel": gates.get("gate_put_nivel", ""),
                "gate_any_nivel": gates.get("gate_any_nivel", ""),
                "gate_processar_executor": int(gates.get("gate_processar_executor", 0)),
                "vol_moderada_ok": int(gates.get("vol_moderada_ok", 0)),
                "vol_contexto_compressao": int(gates.get("vol_contexto_compressao", 0)),
                "vol_expansion_forte": int(gates.get("vol_expansion_forte", 0)),
                "vol_expansion_moderada": int(gates.get("vol_expansion_moderada", 0)),
                "vol_compression_ok": int(gates.get("vol_compression_ok", 0)),
                "vol_expansion_rank": gates.get("vol_expansion_rank", np.nan),
                "vol_operational_rank": gates.get("vol_operational_rank", np.nan),
                "vol_operational_percentile": gates.get("vol_operational_percentile", np.nan),
                "direction_model_ok_exec_up": int(gates.get("direction_model_ok_exec_up", 0)),
                "direction_model_ok_exec_down": int(gates.get("direction_model_ok_exec_down", 0)),
                "volatility_model_ok_exec": int(gates.get("volatility_model_ok_exec", 0)),
                "classe_executor_direction": gates.get("classe_executor_direction", ""),
                "classe_executor_volatility": gates.get("classe_executor_volatility", ""),
                "direction_up_score": gates.get("direction_up_score", np.nan),
                "direction_down_score": gates.get("direction_down_score", np.nan),
                "direction_quality_score_adj": gates.get("direction_quality_score_adj", np.nan),
                "vol_exec_score_adj": gates.get("vol_exec_score_adj", np.nan),
                "vol_expansion_score": gates.get("vol_expansion_score", np.nan),
                "vol_compression_score": gates.get("vol_compression_score", np.nan),
                "vol_operational_score": gates.get("vol_operational_score", np.nan),
                "precision_expansion": gates.get("precision_expansion", np.nan),
                "recall_expansion": gates.get("recall_expansion", np.nan),
                "precision_expansion_moderada": gates.get("precision_expansion_moderada", np.nan),
                "recall_expansion_moderada": gates.get("recall_expansion_moderada", np.nan),
                "precision_compression": gates.get("precision_compression", np.nan),
                "recall_compression": gates.get("recall_compression", np.nan),
                "exec_expansion_alerta_gap": int(gates.get("exec_expansion_alerta_gap", 0)),
                "motivo_bloqueio_volatility": gates.get("motivo_bloqueio_volatility", ""),
                "wf_tipo": gates.get("wf_tipo", ""),
                "vol_model_tipo": str(last.get("vol_model_tipo", gates.get("vol_model_tipo", "ML"))),
                "use_har_fallback": int(gates.get("use_har_fallback", 0)),
                "har_precision_expansion": gates.get("har_precision_expansion", np.nan),
                "gate_vol_rv_rank": int(gates.get("gate_vol_rv_rank", 0)),
            })

            diagnostico_rows.append({
                "ticker": ticker,
                "status": "processado",
                "motivo": motivo_executor,
                "ordem": ordem,
                "gate_call_model": int(gates.get("gate_call_model", 0)),
                "gate_put_model": int(gates.get("gate_put_model", 0)),
                "gate_any_nivel": gates.get("gate_any_nivel", ""),
                "volatility_model_ok_exec": int(gates.get("volatility_model_ok_exec", 0)),
            })

        except Exception as e:
            print(f"Erro ao processar {ticker}: {e}")
            diagnostico_rows.append({
                "ticker": ticker,
                "status": "erro",
                "motivo": str(e),
            })


    # =============================================================================
    # AVISOS DE SAÍDA ANTECIPADA
    # =============================================================================

    def _extrair_data_arquivo(path: Path):
        import re
        m = re.search(r"(\d{4})_(\d{2})_(\d{2})", path.stem)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return None


    def calcular_avisos_saida(df_atual: pd.DataFrame, base_dir: str) -> pd.DataFrame:
        """
        Detecta posições abertas (sinais dos últimos 20 pregões) e emite aviso de saída.

        Avisos possíveis (por prioridade):
          REALIZAR LUCRO      — M_adj >= breakeven + ALVO_LUCRO_DP (lucro real)
          ENCERRAR (vencimento) — T_restante <= T_RESTANTE_ENCERRAR (theta domina)
          NO LUCRO (segurar)  — breakeven <= M_adj < alvo (trailing stop)
          SINAL INVERTIDO     — direc_prediction virou de lado com razao >= RAZAO_MARGINAL
                                e dias_decorridos < DIAS_INVERSAO_TARDIA
          SINAL INVERTIDO (fim horizonte) — inversão tardia (d >= DIAS_INVERSAO_TARDIA):
                                previsão nova é majoritariamente pós-vencimento — informativo
          SINAL FRACO (reverter) — razao < RAZAO_MARGINAL e M_atual < 0 (ativo foi contra)
          SINAL FRACO (segurar)  — razao < RAZAO_MARGINAL e M_atual >= 0 (movimento OK, modelo ruidoso)
          MANTER              — posição dentro do esperado
        """
        pasta = Path(base_dir)
        arqs = sorted(
            (pasta / "saidas" / "ordens_dia").glob("ordem_dia_*executor_v5_5.xlsx"),
            key=lambda p: _extrair_data_arquivo(p) or date.min,
        )
        # Excluir o arquivo de hoje (ainda não existe ou seria o run atual)
        arqs = [a for a in arqs if _extrair_data_arquivo(a) != hoje]
        arqs = arqs[-20:]  # janela máxima de 20 pregões

        posicoes = []
        for arq in arqs:
            data_entrada = _extrair_data_arquivo(arq)
            if data_entrada is None:
                continue
            try:
                df_ord = pd.read_excel(arq, sheet_name="ordem")
            except Exception:
                continue
            sinais = df_ord[df_ord["ordem"].str.contains("Alerta|Compra", na=False) &
                            ~df_ord["ordem"].str.contains("VETADA", na=False)]
            for _, row in sinais.iterrows():
                posicoes.append({
                    "ticker":        str(row["ticker"]),
                    "data_entrada":  data_entrada,
                    "lado":          1 if "CALL" in str(row["ordem"]) else -1,
                    "ordem_entrada": str(row["ordem"]),
                    "estrutura_entrada": str(row.get("estrutura_otima", "naked_30")),
                })

        if not posicoes:
            return pd.DataFrame()

        # Avisos de saída emitidos no run anterior — evita "MANTER" após saída já recomendada.
        # Se ontem dissemos "sair" e hoje não há nova entrada, a posição foi encerrada.
        _AVISOS_TIPO_SAIDA = {
            "REALIZAR LUCRO", "ENCERRAR (vencimento)", "SINAL INVERTIDO", "SINAL FRACO (reverter)"
        }
        _exit_ontem = {}  # ticker → data_entrada (date) da posição que recebeu saída ontem
        if arqs:
            try:
                df_av_ontem = pd.read_excel(arqs[-1], sheet_name="avisos_saida")
                for _, _r in df_av_ontem.iterrows():
                    if str(_r.get("aviso_saida", "")) in _AVISOS_TIPO_SAIDA:
                        _tk = str(_r.get("ticker", ""))
                        _de = _r.get("data_entrada")
                        if _tk and pd.notna(_de):
                            _exit_ontem[_tk] = pd.Timestamp(_de).date()
            except Exception:
                pass

        # Manter apenas o sinal mais recente por ticker.
        # Se o ticker deu sinal no dia 1, saiu no dia 10 e deu novo sinal no dia 15,
        # o sinal do dia 1 refere-se a uma posição já encerrada — descartamos.
        posicoes_recentes: dict = {}
        for p in posicoes:
            tk = p["ticker"]
            if tk not in posicoes_recentes or p["data_entrada"] > posicoes_recentes[tk]["data_entrada"]:
                posicoes_recentes[tk] = p
        posicoes = list(posicoes_recentes.values())

        # Índice do df_atual para lookup rápido
        df_idx = df_atual.set_index("ticker") if "ticker" in df_atual.columns else df_atual

        avisos = []
        for pos in posicoes:
            ticker       = pos["ticker"]
            data_entrada = pos["data_entrada"]
            lado         = pos["lado"]
            estrutura    = pos["estrutura_entrada"]

            if ticker not in df_idx.index:
                continue
            row_atual = df_idx.loc[ticker]
            if isinstance(row_atual, pd.DataFrame):
                row_atual = row_atual.iloc[0]

            close_atual = float(row_atual.get("close", np.nan))
            vol_atual   = float(row_atual.get("volatilidade", np.nan))
            if not np.isfinite(close_atual) or not np.isfinite(vol_atual) or vol_atual == 0:
                continue

            # Close na data de entrada
            db_path = pasta / "database" / f"{ticker}.xlsx"
            if not db_path.exists():
                continue
            try:
                df_db = pd.read_excel(db_path, index_col=0, parse_dates=True)
                ts_entrada = pd.Timestamp(data_entrada)
                if ts_entrada in df_db.index:
                    close_entrada = float(df_db.loc[ts_entrada, "Close"])
                else:
                    idx = df_db.index.get_indexer([ts_entrada], method="nearest")[0]
                    close_entrada = float(df_db["Close"].iloc[idx])
                # Pregões decorridos desde a entrada
                dias_decorridos = int((df_db.index >= ts_entrada).sum()) - 1
            except Exception:
                continue

            dias_decorridos = max(dias_decorridos, 0)
            T_restante      = max(PERIODO_ALVO - dias_decorridos, 1)

            # M desde a entrada (com carry e convexidade — mesmo critério do backtest)
            ret_atual       = (close_atual - close_entrada) / close_entrada
            M_bruto         = lado * ret_atual / (vol_atual * SQRT_H)
            carry_dp        = CARRY_20 / (vol_atual * SQRT_H)
            convexity_dp    = vol_atual * SQRT_H if lado < 0 else 0.0
            M_adj           = M_bruto - lado * carry_dp + convexity_dp

            # Parâmetros da estrutura
            estr = estrutura if estrutura in ESTRUTURAS_OPC else "naked_30"
            k_long = ESTRUTURAS_OPC[estr]["k_long"]
            custo  = ESTRUTURAS_OPC[estr]["custo"] * (FATOR_CUSTO_2_PERNAS if ESTRUTURAS_OPC[estr]["k_short"] is not None else 1.0)
            breakeven   = k_long + custo
            lucro_atual = max(M_adj - k_long, 0.0) - custo

            # Estado do modelo hoje
            pred_atual  = float(row_atual.get("direc_prediction", 0.0))
            razao_atual = float(row_atual.get("razao_sinal", 0.0)) if pd.notna(row_atual.get("razao_sinal")) else 0.0
            # Inversão só conta se a previsão contrária for significativa (razão forte).
            # Ruído com razão < RAZAO_MARGINAL (ALPA4=0.24, MRVE3=0.44) não é inversão real.
            _previsao_significativa = razao_atual >= RAZAO_MARGINAL
            sinal_invertido = _previsao_significativa and (
                (lado > 0 and pred_atual < 0) or (lado < 0 and pred_atual > 0)
            )

            alvo_realizacao = breakeven + ALVO_LUCRO_DP
            if M_adj >= alvo_realizacao:
                aviso   = "REALIZAR LUCRO"
                detalhe = f"M={M_adj:.2f} ≥ BE+alvo={alvo_realizacao:.2f} | lucro≈{lucro_atual:+.2f}DP"
            elif M_adj >= breakeven:
                aviso   = "NO LUCRO (segurar)"
                detalhe = f"M={M_adj:.2f} ≥ BE={breakeven:.2f} mas < alvo | lucro≈{lucro_atual:+.2f}DP — trailing"
            elif T_restante <= T_RESTANTE_ENCERRAR:
                aviso   = "ENCERRAR (vencimento)"
                detalhe = f"{T_restante}d restantes — theta domina | M={M_adj:.2f} | lucro≈{lucro_atual:+.2f}DP"
            elif sinal_invertido:
                if dias_decorridos >= DIAS_INVERSAO_TARDIA:
                    aviso   = "SINAL INVERTIDO (fim horizonte)"
                    detalhe = (f"Modelo virou {'PUT' if lado > 0 else 'CALL'} mas restam {T_restante}d — "
                               f"previsão cobre 20d, maioria pós-vencimento | M={M_adj:.2f} | razao={razao_atual:.2f}")
                else:
                    aviso   = "SINAL INVERTIDO"
                    detalhe = f"Modelo virou {'PUT' if lado > 0 else 'CALL'} | M={M_adj:.2f} | razao={razao_atual:.2f}"
            elif razao_atual < RAZAO_MARGINAL:
                if M_adj < 0:
                    aviso   = "SINAL FRACO (reverter)"
                    detalhe = f"razao={razao_atual:.2f} < {RAZAO_MARGINAL} | M={M_adj:.2f} — ativo foi contra, tese falhou"
                else:
                    aviso   = "SINAL FRACO (segurar)"
                    detalhe = f"razao={razao_atual:.2f} < {RAZAO_MARGINAL} | M={M_adj:.2f} — movimento OK, modelo ruidoso"
            else:
                aviso   = "MANTER"
                detalhe = f"M={M_adj:.2f} | razao={razao_atual:.2f} | {dias_decorridos}/{PERIODO_ALVO}d"

            # Se ontem recomendamos saída para esta MESMA posição (mesma data_entrada)
            # e hoje não houve nova entrada, a posição foi encerrada — MANTER não faz sentido.
            if aviso == "MANTER" and ticker in _exit_ontem and _exit_ontem[ticker] == data_entrada:
                aviso   = "AGUARDAR NOVA ENTRADA"
                detalhe = f"Saída recomendada no run anterior — posição encerrada, sem novo sinal hoje"

            avisos.append({
                "ticker":               ticker,
                "data_entrada":         data_entrada,
                "ordem_entrada":        pos["ordem_entrada"],
                "estrutura":            estr,
                "dias_decorridos":      dias_decorridos,
                "T_restante_pregoes":   T_restante,
                "close_entrada":        round(close_entrada, 2),
                "close_atual":          round(close_atual, 2),
                "ret_desde_entrada_pct": round(ret_atual * 100, 2),
                "M_atual_adj":          round(M_adj, 3),
                "breakeven_dp":         round(breakeven, 3),
                "lucro_atual_dp":       round(lucro_atual, 3),
                "sinal_invertido":      int(sinal_invertido),
                "razao_sinal_atual":    round(razao_atual, 3),
                "direc_prediction_atual": round(pred_atual, 4),
                "aviso_saida":          aviso,
                "detalhe":              detalhe,
            })

        if not avisos:
            return pd.DataFrame()

        _ordem_aviso = ["REALIZAR LUCRO", "ENCERRAR (vencimento)", "NO LUCRO (segurar)", "SINAL INVERTIDO", "SINAL FRACO (reverter)", "SINAL INVERTIDO (fim horizonte)", "SINAL FRACO (segurar)", "MANTER", "AGUARDAR NOVA ENTRADA"]
        df_av = pd.DataFrame(avisos)
        df_av["_ord"] = pd.Categorical(df_av["aviso_saida"], categories=_ordem_aviso, ordered=True)
        return df_av.sort_values(["_ord", "ticker"]).drop(columns="_ord").reset_index(drop=True)


    # =============================================================================
    # EXPORTAÇÃO
    # =============================================================================

    df_final = pd.DataFrame(df_final_rows)
    df_diagnostico = pd.DataFrame(diagnostico_rows)

    # [E3] score_combinado: ranking unificado direction × vol × custo × sinal
    if not df_final.empty:
        dir_score = df_final.get("direction_quality_score_adj", pd.Series(np.nan, index=df_final.index))
        if dir_score.isna().all():
            # fallback: max(up_score, down_score) quando direction_quality_score_adj ausente
            up = pd.to_numeric(df_final.get("direction_up_score", pd.Series(np.nan, index=df_final.index)), errors="coerce")
            dn = pd.to_numeric(df_final.get("direction_down_score", pd.Series(np.nan, index=df_final.index)), errors="coerce")
            dir_score = up.fillna(0).combine(dn.fillna(0), max)
        vol_score = pd.to_numeric(df_final.get("vol_exec_score_adj", pd.Series(np.nan, index=df_final.index)), errors="coerce").fillna(0)
        rv = pd.to_numeric(df_final.get("rv_rank_252", pd.Series(0.5, index=df_final.index)), errors="coerce").fillna(0.5)
        razao = pd.to_numeric(df_final.get("razao_sinal", pd.Series(np.nan, index=df_final.index)), errors="coerce").fillna(0).clip(0, 2.0)
        df_final["score_combinado"] = (
            dir_score.fillna(0) * 0.30 +
            vol_score * 0.30 +
            (1 - rv) * 0.20 +
            (razao / 2.0) * 0.20
        ).round(4)
        # score_acionavel: Compras/Alertas no topo; Monitorar no meio (multiplicador 0.5);
        # Neutros no fundo (multiplicador 0).
        ativo_mask    = df_final["ordem"].isin(["Compra CALL", "Compra PUT", "Alerta CALL", "Alerta PUT"])
        monitorar_mask = df_final["ordem"].isin(["Monitorar CALL", "Monitorar PUT"])
        df_final["score_acionavel"] = (
            df_final["score_combinado"] * (ativo_mask.astype(float) + monitorar_mask.astype(float) * 0.5)
        ).round(4)

    # [E4] funil_diagnostico: status de cada ticker do universo em cada gate
    funil_rows = []
    for tk in TICKERS:
        row: Dict[str, Any] = {"ticker": tk}
        if not df_gates.empty and tk in df_gates.index:
            g = df_gates.loc[tk]
            row["gate_call_model"] = int(_as_int_flag(g.get("gate_call_model", 0)))
            row["gate_put_model"] = int(_as_int_flag(g.get("gate_put_model", 0)))
            row["volatility_model_ok_exec"] = int(_as_int_flag(g.get("volatility_model_ok_exec", 0)))
            row["gate_processar_executor"] = int(_as_int_flag(g.get("gate_processar_executor", 0)))
            row["classe_executor_direction"] = g.get("classe_executor_direction", "")
            row["classe_executor_volatility"] = g.get("classe_executor_volatility", "")
            row["motivo_bloqueio_volatility"] = g.get("motivo_bloqueio_volatility", "")
        else:
            row["gate_call_model"] = 0
            row["gate_put_model"] = 0
            row["volatility_model_ok_exec"] = 0
            row["gate_processar_executor"] = 0
            row["classe_executor_direction"] = "ausente_no_excel"
            row["classe_executor_volatility"] = "ausente_no_excel"
            row["motivo_bloqueio_volatility"] = "ticker_ausente_executor_inputs"
        # Live gates: preenchidos se ticker foi processado
        if not df_final.empty and tk in df_final["ticker"].values:
            live = df_final[df_final["ticker"] == tk].iloc[0]
            row["razao_sinal_ok"] = int(live.get("forca_sinal", "fraco") in ("forte", "marginal"))
            row["vol_live_ok"] = int(live.get("vol_live_classe", "compressao") in ("expansao", "neutro_zona_cinza"))
            row["rv_rank_ok"] = int(live.get("rv_rank_ok", 0))
            row["final_ordem"] = str(live.get("ordem", "Neutro"))
            row["motivo_bloqueio_executor"] = str(live.get("motivo_bloqueio_executor", ""))
            row["score_combinado"] = live.get("score_combinado", np.nan)
            row["forca_sinal"] = str(live.get("forca_sinal", ""))
            row["vol_live_classe"] = str(live.get("vol_live_classe", ""))
        else:
            row["razao_sinal_ok"] = np.nan
            row["vol_live_ok"] = np.nan
            row["rv_rank_ok"] = np.nan
            row["final_ordem"] = "nao_processado"
            row["motivo_bloqueio_executor"] = ""
            row["score_combinado"] = np.nan
            row["forca_sinal"] = ""
            row["vol_live_classe"] = ""
        funil_rows.append(row)
    df_funil = pd.DataFrame(funil_rows)

    if df_final.empty:
        print("Nenhum ticker processado com sucesso.")
        df_ordem = pd.DataFrame()
    else:
        # Filtro final de qualidade para exibir ordens do dia.
        # O sinal já passou pelos gates. Aqui mantemos filtros de avaliação histórica.
        # Gate único de esperança: substitui tx_acerto + gmdp separados.
        # tx_acerto=54% + gmdp=1.04 tem esperança maior que tx=62% + gmdp=0.75.
        # O par se compensa — usar esperanca_opcao_dp como critério unificado.
        _sinal_ok   = df_final["executor_signal_ok"] == 1
        _monitorar  = df_final["ordem"].isin(["Monitorar CALL", "Monitorar PUT"])
        # Gate S-C: esperança corrigida pelo ruído amostral.
        # Condição: (esp >= 0.05 E esp - SE > 0) OU (esp >= 0.05 E n_ops >= 50).
        # Com n pequeno, esp >= 0.05 pode ser sorte; exigir esp > SE força margem sobre o ruído.
        _esp_liquida = df_final["esperanca_opcao_dp"] - df_final["se_esperanca_opc"].fillna(np.inf)
        _gate_hist  = (
            (df_final["esperanca_opcao_dp"] >= 0.05) &
            (df_final["numero_operacoes"] >= 15) &
            ((_esp_liquida > 0) | (df_final["numero_operacoes"] >= 50))
        )
        _aprovados = df_final[(_sinal_ok | _monitorar) & _gate_hist].copy()
        _aprovados["veto_gate_historico"] = 0
        _aprovados["motivo_veto"] = ""

        _vetados = df_final[(_sinal_ok | _monitorar) & ~_gate_hist].copy()
        _vetados["veto_gate_historico"] = 1
        _vetados["motivo_veto"] = "vetado_esperanca_historica"
        _vetados["ordem"] = _vetados["ordem"].apply(
            lambda o: o if str(o).endswith("(VETADA)") else f"{o} (VETADA)"
        )

        df_ordem = pd.concat([_aprovados, _vetados], ignore_index=True)

        # [E3] Ranking por score_acionavel (ativos primeiro) depois score_combinado
        if "score_combinado" not in df_ordem.columns:
            df_ordem["score_combinado"] = np.nan
        if "score_acionavel" not in df_ordem.columns:
            df_ordem["score_acionavel"] = np.nan
        df_ordem = df_ordem.sort_values(
            by=["veto_gate_historico", "score_acionavel", "score_combinado", "vol_exec_score_adj", "razao_sinal", "esperanca_opcao_dp"],
            ascending=[True, False, False, False, False, False],
            na_position="last",
        )

    gates_export = df_gates.reset_index(drop=True).copy() if not df_gates.empty else pd.DataFrame()
    if not gates_export.empty:
        nivel_rank = {"A": 3, "B": 2, "C": 1, "": 0}
        gates_export["_nivel_rank"] = gates_export["gate_any_nivel"].map(nivel_rank).fillna(0)
        gates_export = gates_export.sort_values(
            ["_nivel_rank", "gate_any_model", "gate_call_model", "gate_put_model"],
            ascending=False,
        ).drop(columns=["_nivel_rank"])

    if df_final.empty:
        sinais_live_total = 0
        compras_opcao_live = 0
        alertas_operacionais_live = 0
        compras_call_live = 0
        compras_put_live = 0
        alertas_call_live = 0
        alertas_put_live = 0
        monitorar_call_live = 0
        monitorar_put_live  = 0
        neutros_processados = 0
    else:
        sinais_live_total = int((df_final["ordem"] != "Neutro").sum())
        compras_opcao_live = int(df_final["opcao_compra_ok"].sum()) if "opcao_compra_ok" in df_final.columns else 0
        alertas_operacionais_live = int(df_final["ordem"].astype(str).str.startswith("Alerta").sum())
        compras_call_live = int((df_final["ordem"] == "Compra CALL").sum())
        compras_put_live = int((df_final["ordem"] == "Compra PUT").sum())
        alertas_call_live = int((df_final["ordem"] == "Alerta CALL").sum())
        alertas_put_live = int((df_final["ordem"] == "Alerta PUT").sum())
        monitorar_call_live = int((df_final["ordem"] == "Monitorar CALL").sum())
        monitorar_put_live  = int((df_final["ordem"] == "Monitorar PUT").sum())
        neutros_processados = int((df_final["ordem"] == "Neutro").sum())

    _total_lado = compras_call_live + compras_put_live + alertas_call_live + alertas_put_live
    _total_call = compras_call_live + alertas_call_live
    _pct_call   = round(_total_call / _total_lado, 3) if _total_lado > 0 else np.nan
    _alerta_concentracao = int(
        pd.notna(_pct_call) and _total_lado >= 5 and (_pct_call > 0.75 or _pct_call < 0.25)
    )

    resumo = pd.DataFrame({
        "metrica": [
            "versao_executor",
            "modo_executor_diario",
            "arquivo_direction_input",
            "data_direction_input",
            "idade_direction_input_dias",
            "arquivo_volatility_input",
            "data_volatility_input",
            "idade_volatility_input_dias",
            "tickers_gates_total",
            "tickers_gate_call_model",
            "tickers_gate_put_model",
            "tickers_gate_any_model",
            "tickers_nivel_A",
            "tickers_nivel_B",
            "tickers_nivel_C",
            "tickers_processaveis_A_B",
            "tickers_processados",
            "linhas_aba_ordem_pos_filtro",
            "sinais_live_total",
            "compras_opcao_live",
            "alertas_operacionais_live",
            "compras_call_live",
            "compras_put_live",
            "alertas_call_live",
            "alertas_put_live",
            "monitorar_call_live",
            "monitorar_put_live",
            "neutros_processados",
            "degradado_artefatos_desalinhados",
            "pct_sinais_call",
            "concentracao_direcional_alerta",
        ],
        "valor": [
            VERSION_EXECUTOR,
            int(MODO_EXECUTOR_DIARIO),
            str(arq_direction_input),
            str(data_direction_input),
            idade_direction_input_dias,
            str(arq_volatility_input),
            str(data_volatility_input),
            idade_volatility_input_dias,
            len(df_gates) if not df_gates.empty else 0,
            int(df_gates["gate_call_model"].sum()) if not df_gates.empty else 0,
            int(df_gates["gate_put_model"].sum()) if not df_gates.empty else 0,
            int(df_gates["gate_any_model"].sum()) if not df_gates.empty else 0,
            int((df_gates["gate_any_nivel"] == "A").sum()) if not df_gates.empty else 0,
            int((df_gates["gate_any_nivel"] == "B").sum()) if not df_gates.empty else 0,
            int((df_gates["gate_any_nivel"] == "C").sum()) if not df_gates.empty else 0,
            int(df_gates["gate_processar_executor"].sum()) if not df_gates.empty else 0,
            len(df_final),
            len(df_ordem),
            sinais_live_total,
            compras_opcao_live,
            alertas_operacionais_live,
            compras_call_live,
            compras_put_live,
            alertas_call_live,
            alertas_put_live,
            monitorar_call_live,
            monitorar_put_live,
            neutros_processados,
            int(DEGRADAR_PARA_ALERTA_B),
            _pct_call,
            _alerta_concentracao,
        ],
    })

    nome_saida = os.path.join(pasta_do_arquivo, "saidas", "ordens_dia", NOME_SAIDA)
    os.makedirs(os.path.dirname(nome_saida), exist_ok=True)

    # Options advisor — aplica recomendações de estratégia aos sinais operacionais
    df_estrategias = pd.DataFrame()
    if _ADVISOR_OK and not df_final.empty:
        df_final = _adv.aplicar_advisor_ao_dataframe(df_final)
        if not df_ordem.empty:
            df_ordem = _adv.aplicar_advisor_ao_dataframe(df_ordem)
            # Vetados não devem exibir recomendação operacional — sobrescreve após advisor.
            mask_veto = df_ordem["veto_gate_historico"] == 1
            df_ordem.loc[mask_veto, "estrategia_preferida"]   = "VETADA — esperança histórica insuficiente"
            df_ordem.loc[mask_veto, "estrategia_agressiva"]   = ""
            df_ordem.loc[mask_veto, "estrategia_conservadora"] = ""
            df_ordem.loc[mask_veto, "confianca_recomendacao"] = "vetada"
            df_ordem.loc[mask_veto, "logica"]                 = "Sinal vetado pelo gate de esperança histórica — não operar."
        # Aba dedicada: apenas tickers com sinal operacional
        # estrategias_opcoes deve refletir exatamente os tickers da aba ordem (pós-gate)
        _cols_estrategia = ["ticker", "ordem", "rv_rank_252", "vol_prediction_ratio",
                            "forca_sinal", "esperanca_opcao_dp", "confianca_recomendacao",
                            "estrategia_preferida", "condicao_entrada",
                            "alvo_realizacao", "stop_loss", "gestao_nota",
                            "estrategia_agressiva", "estrategia_conservadora", "evitar",
                            "delta_alvo", "logica"]
        _cols_estrategia = [c for c in _cols_estrategia if c in df_ordem.columns]
        df_estrategias = df_ordem[_cols_estrategia].copy()
        # Colunas de volatilidade para comparação com IV de mercado
        if "volatilidade" in df_ordem.columns:
            _vol_d = df_ordem["volatilidade"]
            df_estrategias["vol_diaria"] = _vol_d.values
            df_estrategias["vol_anualizada_pct"] = (_vol_d.values * np.sqrt(252) * 100).round(1)
            _vpr = df_estrategias["vol_prediction_ratio"] if "vol_prediction_ratio" in df_estrategias.columns else 1.0
            df_estrategias["vol_prevista_anualizada_pct"] = (_vol_d.values * _vpr.values * np.sqrt(252) * 100).round(1)
            df_estrategias["nota_iv"] = "Se IV_mercado < vol_anualizada_pct → opção barata (compra favorável)"

    _ordem_cat = ["Compra CALL", "Compra PUT", "Alerta CALL", "Alerta PUT",
                  "Monitorar CALL", "Monitorar PUT"]
    def _sort_ordem(df):
        df = df.assign(_ord_key=pd.Categorical(df["ordem"], categories=_ordem_cat, ordered=True))
        sort_cols = ["_ord_key"] + (["score_acionavel"] if "score_acionavel" in df.columns else [])
        asc = [True] + ([False] if "score_acionavel" in df.columns else [])
        return df.sort_values(sort_cols, ascending=asc).drop(columns="_ord_key")

    df_ordem      = _sort_ordem(df_ordem)
    df_estrategias = _sort_ordem(df_estrategias) if not df_estrategias.empty else df_estrategias

    # Inserir estrategia_preferida logo após "ordem" na aba ordem
    if "estrategia_preferida" in df_ordem.columns:
        _cols = list(df_ordem.columns)
        _cols.remove("estrategia_preferida")
        _idx = _cols.index("ordem") + 1
        _cols.insert(_idx, "estrategia_preferida")
        df_ordem = df_ordem[_cols]

    df_avisos = calcular_avisos_saida(df_final, pasta_do_arquivo)

    # Mover aviso_saida para logo após ticker na aba avisos_saida
    if not df_avisos.empty and "aviso_saida" in df_avisos.columns:
        _av_cols = list(df_avisos.columns)
        _av_cols.remove("aviso_saida")
        _av_cols.insert(_av_cols.index("ticker") + 1, "aviso_saida")
        df_avisos = df_avisos[_av_cols]

    # Carimba versao do executor por linha -> realizados.py lê versao gravada
    # (sheet "todos") em vez de inferir por data. Ver realizados.py:157-161.
    df_final = df_final.copy()
    df_final.insert(0, "versao_executor", VERSION_EXECUTOR)
    if not df_ordem.empty:
        df_ordem = df_ordem.copy()
        df_ordem.insert(0, "versao_executor", VERSION_EXECUTOR)

    with pd.ExcelWriter(nome_saida) as writer:
        df_ordem.to_excel(writer, sheet_name="ordem", index=False)
        if not df_avisos.empty:
            df_avisos.to_excel(writer, sheet_name="avisos_saida", index=False)
        if not df_estrategias.empty:
            df_estrategias.to_excel(writer, sheet_name="estrategias_opcoes", index=False)
        df_final.to_excel(writer, sheet_name="todos", index=False)
        gates_export.to_excel(writer, sheet_name="gates_direction_vol", index=False)
        df_diagnostico.to_excel(writer, sheet_name="diagnostico", index=False)
        df_funil.to_excel(writer, sheet_name="funil_diagnostico", index=False)
        resumo.to_excel(writer, sheet_name="resumo", index=False)

    print("\n" + "=" * 90)
    print(f"Excel salvo: {nome_saida}")
    print(f"Linhas na aba ordem pós-filtro: {len(df_ordem)}")
    print(f"Compras de opção live: {compras_opcao_live}")
    print(f"Alertas operacionais live: {alertas_operacionais_live}")
    print(f"Tickers processados: {len(df_final)}")
    _aba_est = ", estrategias_opcoes" if not df_estrategias.empty else ""
    _aba_av  = ", avisos_saida" if not df_avisos.empty else ""
    print(f"Abas: ordem, todos, gates_direction_vol, diagnostico, funil_diagnostico, resumo{_aba_est}{_aba_av}")
    if not df_avisos.empty:
        _av_counts = df_avisos["aviso_saida"].value_counts()
        def _c(k): return int(_av_counts.get(k, 0))
        print(
            f"Avisos saída: REALIZAR={_c('REALIZAR LUCRO')} | "
            f"ENCERRAR={_c('ENCERRAR (vencimento)')} | "
            f"NO LUCRO={_c('NO LUCRO (segurar)')} | "
            f"INVERTIDO={_c('SINAL INVERTIDO')} | "
            f"FRACO_REV={_c('SINAL FRACO (reverter)')} | "
            f"FRACO_SEG={_c('SINAL FRACO (segurar)')} | "
            f"MANTER={_c('MANTER')} | "
            f"AGUARDAR={_c('AGUARDAR NOVA ENTRADA')}"
        )
