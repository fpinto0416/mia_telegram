# mia_telegram

Rascunho dos workflows de automação do **Projeto MIA** (executor diário + notificação no
Telegram + geração do xlsx de realizados).

**Status: os arquivos abaixo são referência — o pipeline de fato roda dentro do repositório
[`opcoes`](https://github.com/fpinto0416/opcoes)**, porque é lá que vivem `executor.py`,
`download_database.py`, `realizados.py`, os modelos treinados (`modelos_direction/`,
`modelos_volatility/`) e o `database/`. Um workflow de Actions só consegue rodar esses
scripts se eles estiverem no mesmo checkout — por isso este repositório sozinho não executa
o pipeline.

## Conteúdo

- `.github/workflows/mia_diario.yml` — executor diário (09:00 e 18:30 horário de Brasília,
  seg-sex, + disparo manual), commita `database/` e `saidas/` de volta no repo e manda o
  resumo do dia no Telegram.
- `.github/workflows/mia_realizados.yml` — geração sob demanda (disparo manual) do
  `saidas/realizados/*.xlsx` para acompanhar a performance realizada.
- `telegram_notify.py` — lê o xlsx mais recente (`ordem_dia_*` ou `realizados_*`) e envia o
  resumo em texto no Telegram. Requer `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` como secrets.

## Para ativar

Copie `.github/workflows/` e `telegram_notify.py` para dentro do repositório `opcoes` e
cadastre lá os secrets `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.
