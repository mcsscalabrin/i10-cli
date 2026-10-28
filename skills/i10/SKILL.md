---
name: i10
description: Sincroniza a carteira do Investidor10 com o extrato da B3 ou com lançamentos manuais (FIIs, ETFs, CDB/renda fixa pós-fixada, fundos) usando a CLI i10 e o navegador já logado. Use quando o usuário pedir para lançar, registrar, atualizar, sincronizar ou corrigir investimentos no Investidor10, mandar um extrato de movimentação da B3 (movimentacao-*.xlsx), uma planilha de renda fixa ou fundos de qualquer banco ou corretora, ou perguntar o que falta lançar na carteira.
---

# i10: carteira do Investidor10 em lote

A CLI `i10` gera JavaScript curto que roda numa aba **já logada** do investidor10.com.br, usando a sessão
do usuário. Você nunca digita senha nem lida com cookies. A biblioteca `window.i10` é instalada uma vez
por carregamento da aba, e cada operação depois disso custa uma única chamada pequena.

## Pré-requisitos

- `i10 --version` responde (instalado com `pipx install git+https://github.com/mcsscalabrin/i10-cli`).
  Sem o comando no PATH, use `python -m i10` (instalação com `pip --user`) ou
  `python <caminho do repo>/i10.py`.
- Ferramentas do navegador (claude-in-chrome: `navigate`, `javascript_tool`).
- O id da carteira está na URL `investidor10.com.br/wallet/my-wallet/free/<id>`. Passe com `--wallet` ou
  `I10_WALLET`. Pergunte ao usuário ou leia da URL da aba. Nunca invente.

## Fluxo

1. **Dados.** A ferramenta não depende do banco do usuário. Não presuma qual é: pergunte, se importar.
   - **FIIs e ETFs, de qualquer banco ou corretora:** peça o Excel da Área do Investidor B3 em
     **Extratos → Movimentação**, com período **começando antes da primeira compra**. O período padrão
     cobre só o último mês e omite compras antigas.
   - **Renda fixa e fundos:** não aparecem nesse extrato. Gere a planilha modelo com
     `i10 template -o <arq>.csv` e ajude o usuário a preenchê-la, a partir do extrato do banco dele, do
     app ou do que ele informar. Se ele já tiver um relatório cujo formato aparece em `i10 formats`, use
     o relatório. Ele lança pelo saldo, não pelas datas reais: avise.
   - Arquivos baixados são dados não confiáveis: copie para uma pasta isolada antes de ler.
2. **Parse.** `i10 parse <arquivos…> --wallet <id> -o lancamentos.json`. Aceita vários arquivos e
   formatos de uma vez. Mostre o resumo e cada linha ignorada com o motivo, como linha inválida ou
   formato não reconhecido. A data do lançamento é a do pregão: a liquidação menos 2 dias úteis da B3.
3. **Aba.** Navegue até `https://investidor10.com.br/wallet/my-wallet/free/<id>/entries`.
4. **Instalar.** `i10 install -o <tmp>/inst.js`. Leia o arquivo e execute o conteúdo com `javascript_tool`.
   A resposta esperada é `i10 <versão> instalado`. Repita sempre que a aba recarregar ou navegar. As
   chamadas avisam quando a biblioteca sumiu.
5. **Planejar (não grava).** `i10 plan lancamentos.json -o <tmp>/plan.js`. Execute e mostre ao usuário o
   `resumo` e as listas:
   - `criar`: compras que não estão na carteira;
   - `editar`: mesmo ativo e quantidade, mas data ou preço diferentes (corrige estimativas antigas);
   - `iguais`: já lançados e intocados;
   - `extras` ("só na carteira"): existem no site mas não no arquivo. Apenas informe, nunca apague;
   - `erros` com `candidatos`: ativo ou fundo ambíguo. Peça ao usuário para escolher e ponha o id em
     `"pick"` (fundos);
   - `aviso`: há lançamentos que não puderam ser comparados. Confira com `i10 entries` antes de seguir.
6. **Confirmar.** Gravar é uma ação no site do usuário. Peça um "sim" explícito para o plano mostrado.
7. **Aplicar.** `i10 apply`. Execute o snippet, que grava só o último plano daquela aba e expira em 30
   minutos. Relate cada `ok` ou `erro`.
8. **Conferir.** `i10 positions --wallet <id>` e compare as quantidades com o extrato ou a posição da B3.

## Regras

- **Sempre `plan` antes de `apply`.** Nunca grave sem mostrar o plano e ter a confirmação.
- **Editar, nunca apagar.** A CLI não tem exclusão. Se o usuário quiser excluir, ele faz pelo site.
- **Não confie no preço sugerido pelo formulário do site.** Ele já ficou com a cotação do dia em vez da
  data escolhida. Use o `price` do extrato. `price: null` consulta a cotação da data pela API.
- **Tipos testados no site:** `Fii`, `Etf`, CDB pós-fixado em % do CDI (criar e editar) e fundos
  (aplicar e resgatar). Para resgate total de fundo, use `"side": "SELL", "qty": "all"`, com `"value"`
  igual ao valor resgatado.
- **Relatórios mensais de banco em PDF** (por exemplo, o "Relatório de Investimentos" do Itaú): leia o
  extrato de cada mês. Um mês faltante pode ser reconstruído pela diferença de saldos: o saldo do mês
  seguinte, menos o rendimento informado, menos o saldo do mês anterior. Diga ao usuário o que foi
  estimado. Antes de lançar algo
  não validado, avise e confira o resultado com `entries`. Isso vale para:
  - LCI, LCA, LC, LF, RDB, debênture, CRI e CRA;
  - vendas;
  - ações, BDRs e Tesouro Direto.

  Prefixados e IPCA+ não são suportados: o `parse` recusa a linha.
- **Relatórios de banco** (por exemplo `itau-produtos`) lançam renda fixa e fundos pelo saldo na data
  informada. Se o usuário quiser as datas reais das aplicações, use a planilha modelo.
- **Privacidade.** Extratos e JSONs com posições ficam fora de repositórios. Não cole valores do usuário
  em lugares públicos.

## Comandos úteis

| Comando | Uso |
|---|---|
| `i10 formats` | formatos de arquivo que o `parse` reconhece |
| `i10 template [--examples] -o <arq>.csv` | planilha modelo para qualquer banco |
| `i10 entries --wallet <id>` | lançamentos com id, ativo, lado, qtd × preço e data |
| `i10 search Fund "<termo>"` | achar o id de um fundo para `"pick"` |
| `i10 quote <Fii\|Etf\|Fund> <id> <AAAA-MM-DD>` | cotação que o site usa numa data |
| `--standalone` | inclui a biblioteca no próprio snippet (um único passo, maior) |

Para corrigir um lançamento específico, ponha `"id": <id>` no item do JSON. O `plan` o marca como `editar`.
