# Box plot mensal — Painel de Sinais (OMQS)

Utilitário manual para atualizar o box plot mensal do card **"OMQS — api_OMQS"**
no artefato *Painel de Sinais — MIA · OMQS · Hilo · Sinal Diário · OMQS Futuros*.

Não é um pipeline automático — não roda no GitHub Actions, não faz parte do
`acoes_fundamentalista/` nem do resto deste repo. É só um script pra não ter
que recalcular a mão toda vez que o painel for atualizado.

## Quando usar

Toda vez que quiser atualizar os números do card OMQS nesse artefato: exporte
do repositório `api_OMQS` os dois arquivos abaixo (referentes à data mais
recente) e rode o script.

## Uso

```bash
pip install pandas numpy openpyxl
python gerar_boxplot_omqs.py resultados_AAAA_MM_DD.xlsx memoria_ordens.csv
```

Entradas esperadas (exportadas do `api_OMQS`):

- `resultados_AAAA_MM_DD.xlsx` — abas `trades_fechados` e `posicoes_abertas`
- `memoria_ordens.csv` — snapshot diário de preços por ticker (usa o `close`
  mais recente de cada ticker pra marcar a mercado as posições abertas)

Saída: imprime um resumo por mês no terminal (pra conferir contra os
"Ganho máx"/"Perda mín" que já estão no card — se baterem, os dados estão
alinhados) e salva `boxplot_bloco.html` (ou o caminho passado em `--out`) com
o `<div class="boxplot-block">...</div>` pronto.

## Aplicando no artefato

1. Abrir o artefato publicado, copiar o HTML completo.
2. Substituir o `<div class="boxplot-block">...</div>` existente (dentro do
   card OMQS) pelo conteúdo de `boxplot_bloco.html`.
3. Republicar na mesma URL do artefato.

(Ou: mandar os dois arquivos de export + o link do artefato numa conversa com
o Claude Code, que ele faz os passos 1–3.)

## Metodologia

- Trades fechados: usa o `retorno` já calculado no xlsx.
- Posições abertas: marca a mercado com `(preco_entrada − close) / preco_entrada`
  pra VENDA, `(close − preco_entrada) / preco_entrada` pra COMPRA.
- Cada trade é agrupado pelo **mês da data de entrada** (fechados e abertos do
  mesmo mês entram na mesma caixa).
- Validado em 24/08/2026: os extremos calculados (+36,50% / -13,33%) bateram
  exatos com "Ganho máx" / "Perda mín" que já estavam no painel.
