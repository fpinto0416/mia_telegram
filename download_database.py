'''
------------------------------------------------------------
Author: Fabio da Costa Pinto
Email: fpinto0416@gmail.com
Created: Abril 2025
Last Update: 02/04/2026
License: Proprietary / Private Use
------------------------------------------------------------
Description:

------------------------------------------------------------
'''

import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
from tvDatafeed import TvDatafeed, Interval

caminho_completo = os.path.abspath(__file__)
pasta_do_arquivo = os.path.dirname(caminho_completo)

pasta = "database"

# cria a pasta se não existir
caminho = os.path.join(pasta_do_arquivo, pasta)
os.makedirs(caminho, exist_ok=True)

hoje = pd.to_datetime("today").date()

username = 'YourTradingViewUsername'
password = 'YourTradingViewPassword'
tv = TvDatafeed(username, password)

######################################
####importar dados do tradingview#####
######################################
def importar_tradingview(ticker, hoje = pd.to_datetime("today").date()):
    tentativa = 0
    while tentativa < 5:  # Tenta duas vezes antes de desistir
        try:
            if  ticker=='US10Y' or ticker=='GOLD' or ticker=='DXY':
                exchange = 'TVC'
            elif ticker=='UKOIL':
                exchange = 'FXCM'
            elif ticker == "BRENT":
                exchange = "ActivTrades"
            elif ticker=='FEF1!':
                exchange = 'SGX'
            elif ticker=='CCM1!'or ticker=='IBOV':
                exchange = 'BMFBOVESPA'
            elif ticker=='SPX':
                exchange = 'SPCFD'
            elif ticker=='VIX':
                exchange = 'CBOE'
            elif ticker=='ARKK':
                exchange = 'CBOE'
            elif ticker=='VIX':
                exchange = 'CBOE'
            elif ticker=='BTCUSD':
                exchange = 'Coinbase'
            elif ticker=='TFLO'or ticker=='GLD':
                exchange = 'NYSEArca'
            elif ticker=='SPY':
                exchange = 'NYSEArca'
            else:
                exchange = 'BMFBOVESPA'

            df = tv.get_hist(ticker, exchange, interval=Interval.in_daily, n_bars=20000)
            df=df.drop(["symbol"], axis=1)
            df = df.sort_values("datetime")
            df.index.name = 'Date'
            df.index = df.index.date
            df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close','volume': 'Volume',})

            # tratar outliers
            ret =df["Close"].pct_change()
            med = ret.median()
            mad = np.median(np.abs(ret - med))
            z_robusto = 0.6745 * (ret - med) / mad
            outlier = np.abs(z_robusto) > 8
            df.loc[outlier, "Close"] = np.nan
            df["Close"] = df["Close"].interpolate(method="linear")

            break  # Sai do loop se a requisição for bem-sucedida
        except Exception as e:
            print(f"Erro ao processar {ticker}: {e}")
            tentativa += 1
            if tentativa < 5:
                print(f"Tentando novamente em 15 segundos...")
                time.sleep(15)
            else:
                print(f"Falha definitiva para {ticker}, passando para o próximo.")
    return df

tickers = [
"BOVA11",
"PETR4",
"HASH11",
"ITUB4",
"VALE3",
"BBAS3",
"BBDC4",
"SMAL11",
"BRAV3",
"PRIO3",
"WEGE3",
"B3SA3",
"MGLU3",
"EMBJ3",
"BPAC11",
"PETR3",
"SUZB3",
"ITSA4",
"GGBR4",
"USIM5",
"LREN3",
"BBSE3",
"MBRF3",
"HAPV3",
"CMIG4",
"MRVE3",
"AXIA3",
"ABEV3",
"CSNA3",
"RENT3",
"CSAN3",
"TAEE11",
"NATU3",
"CYRE3",
"RADL3",
"JHSF3",
"SBSP3",
"GOAU4",
"COGN3",
"ASAI3",
"RAIZ4",
"AZZA3",
"EGIE3",
"BRAP4",
"BRKM5",
"DIRR3",
"IRBR3",
"EQTL3",
"BOVV11",
"BEEF3",
"POMO4",
"CPLE3",
"KLBN11",
"ALOS3",
"CXSE3",
"SANB11",
"CMIN3",
"VBBR3",
"BBDC3",
"UGPA3",
"RAIL3",
"NVDC34",
"SMTO3",
"RDOR3",
"VAMO3",
"AUAU3",
"RECV3",
"HYPE3",
"ENEV3",
"MULT3",
"CSMG3",
"PCAR3",
"YDUQ3",
"SLCE3",
"CVCB3",
"ISAE4",
"TOTS3",
"MOTV3",
"PSSA3",
"CEAB3",
"AURE3",
"JBSS32",
"ROXO34",
"VIVA3",
"SIMH3",
"BHIA3",
"SAPR11",
"XPBR31",
"GMAT3",
"FLRY3",
"SBFG3",
"EZTC3",
"SOJA3",
"ECOR3",
"MOVI3",
"VIVT3",
"ALPA4",
"CURY3"
]
tickers += ['DI11!','DXY', 'BRENT', 'DOL1!','SPX','IBOV','US10Y','GOLD','VIX','FEF1!','CCM1!']
tickers += ['WDO1!','WIN1!']
tickers += ['BTCUSD','ARKK','SPY','TFLO']
tickers += ["ENGI11", "BOAC34", "BRSR6"]

tickers_tradingview=['BTCUSD','WDO1!','WIN1!','DI11!','DXY', 'BRENT', 'DOL1!','SPX','IBOV','US10Y','GOLD','VIX','FEF1!','CCM1!']
for ticker in tickers:
    try:
        if ticker in tickers_tradingview:
            df = importar_tradingview(ticker, hoje)
        else:
            if ticker in ['ARKK', 'SPY', 'TFLO','GLD','IAU']:
                df = yf.download(ticker, start='2000-01-01', end=hoje, progress=False)
            else:
                df = yf.download(ticker+'.SA', start='2000-01-01', end=hoje, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = df.index.date
            df = df[df['Volume'] != 0]

            df_tradingview = importar_tradingview(ticker, hoje)
            df_tradingview = df_tradingview.loc[df_tradingview.index > df.index[-1]]
            # Garante que as colunas fiquem na mesma ordem antes de concatenar
            df_tradingview = df_tradingview[df.columns]

            # Agora a concatenação será perfeita
            df = pd.concat([df, df_tradingview])
        nome_saida = os.path.join(caminho, f"{ticker}.xlsx")
        print(f"Salvando {ticker}")
        df.to_excel(nome_saida)
    except Exception as e:
        print(f"Erro ao processar {ticker}: {e}")
        print(f"Erro {ticker}")
