"""
Selic meta (SGS 432) com nova tentativa e valor reserva.

Em 22/10/2026 o run diário caiu inteiro porque a API do Banco Central
devolveu 502 na importação do options_advisor. Agora:

1. tenta o SGS até 4 vezes (espera 5, 15 e 30 s entre as tentativas);
2. se conseguir, grava o valor em saidas/selic_cache.json (o workflow já
   commita saidas/, então o cache sobrevive ao runner);
3. se não conseguir, usa o último valor do cache;
4. sem cache, usa SELIC_RESERVA_AA e avisa no log.

O valor é a taxa ANUAL em fração (0.1375 = 13,75% a.a.).
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "saidas" / "selic_cache.json"
SELIC_RESERVA_AA = 0.1375  # Selic meta em 29/10/2026; só usada sem SGS e sem cache
ESPERAS = (5, 15, 30)

_valor = None


def selic_anual() -> float:
    global _valor
    if _valor is not None:
        return _valor
    from bcb import sgs

    ultimo_erro = None
    for i in range(len(ESPERAS) + 1):
        try:
            df = sgs.get({"selic": 432}, last=5)
            v = float(df["selic"].dropna().iloc[-1]) / 100.0
            try:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps({"data": date.today().isoformat(), "selic_aa": v}))
            except OSError:
                pass
            _valor = v
            return v
        except Exception as e:  # SGSError, timeout, rede
            ultimo_erro = e
            if i < len(ESPERAS):
                print(f"AVISO: Selic (SGS 432) falhou ({e}); nova tentativa em {ESPERAS[i]}s")
                time.sleep(ESPERAS[i])

    try:
        c = json.loads(CACHE.read_text())
        _valor = float(c["selic_aa"])
        print(f"AVISO: SGS indisponível ({ultimo_erro}); usando Selic do cache de {c['data']}: {_valor:.4f}")
    except Exception:
        _valor = SELIC_RESERVA_AA
        print(f"AVISO: SGS indisponível ({ultimo_erro}) e sem cache; usando Selic reserva {_valor:.4f}")
    return _valor


def selic_diaria() -> float:
    return (1 + selic_anual()) ** (1 / 252) - 1
