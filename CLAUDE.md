# CLAUDE.md — mia_telegram

Orientação rápida pra qualquer sessão do Claude Code que abrir este repo.
Detalhes completos (tabela de arquivos, setup dos secrets) ficam no
`README.md` — leia ele antes de qualquer mudança maior.

## O que é

Subprojeto do Projeto MIA — só **executa** o sinal do dia (~95 tickers),
não treina modelo nenhum. Pipeline de 3 estágios por ticker: modelo de
**direção** (CALL/PUT), modelo de **volatilidade** (compensa comprar
opção?), **precificação** (escolhe estrutura/strike/custo). Consome
modelos já treinados (`modelos_direction/`, `modelos_volatility/`) e a
planilha de entrada mais recente de cada módulo — nunca retreina nada
aqui (isso roda no repositório principal, privado, caro — walk-forward por
~95 tickers).

## Cuidado: repo é público

Este repo é público de propósito (rodar de graça no GitHub Actions —
repos privados têm cota limitada de minutos). Código, modelos `.pkl` e
histórico em `saidas/` ficam visíveis publicamente. As credenciais do
TradingView em `download_database.py` são só placeholders (download
funciona anônimo) — **nunca commitar segredo real aqui**, só nos secrets
do GitHub Actions (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

## Não é position-based

Diferente dos outros sistemas do Painel de Sinais (OMQS, Hilo), o MIA
avalia cada sinal num **horizonte fixo de 20 pregões** — não há noção de
"posição aberta" nem duração variável. Todo sinal fechado dura exatamente
20 pregões por construção. Não tentar aplicar aqui as métricas de
"duração média ganho/perda" ou "posições abertas MTM" que fazem sentido
nos repos position-based.

## Automação

- `.github/workflows/mia_diario.yml` — 09:00 e 18:30 BRT, seg-sex:
  `download_database.py` → `executor.py` (`SKIP_DOWNLOAD=1`) →
  `historico_runs.py` → commit/push → `telegram_notify.py executor`.
- `.github/workflows/mia_realizados.yml` — só disparo manual: atualiza
  database, roda `realizados.py` (cruza sinais passados com preço 20
  pregões depois), commita `saidas/realizados/*.xlsx`, notifica Telegram.

`executor.py` sempre pega a planilha de entrada mais recente por data no
nome do arquivo — não precisa apagar as antigas, mas avisa no log/Telegram
quando os artefatos passam de 45 dias, sugerindo retreino (no repo
principal, não aqui).

## Atualizando os modelos

Ver `README.md`, seção "Atualizando os modelos" — é só copiar os `.pkl` e
planilhas mais recentes do repo principal pra cá, sem apagar as antigas.

## Painel de Sinais (artefato compartilhado)

Os números de `realizados.py` (sinais fechados, acerto, payoff, PF, e o
detalhamento por estágio do pipeline) alimentam à mão o card "MIA" do
artefato **Painel de Sinais**
(`https://claude.ai/code/artifact/ac976eca-35a4-4ff2-ba69-67b1863c29c9`),
que também agrega api_OMQS (diário + 4h), hilo, opcoes-sinal-diario e
api_OMQS_futuros. É HTML estático — os números são colados à mão a cada
atualização, seguindo o comentário HTML antes do card.

## Estrutura

Ver `README.md`, seção "Arquivos", pra lista completa.
