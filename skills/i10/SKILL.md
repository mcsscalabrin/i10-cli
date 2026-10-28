---
name: i10
description: Sincroniza a carteira do Investidor10 com o extrato da B3 ou com lançamentos manuais (FIIs, ETFs, CDB/renda fixa pós-fixada, fundos) usando a CLI i10 e o navegador já logado. Use quando o usuário pedir para lançar, registrar, atualizar, sincronizar ou corrigir investimentos no Investidor10, mandar um extrato de movimentação da B3 (movimentacao-*.xlsx) ou relatório de produtos do Itaú, ou perguntar o que falta lançar na carteira.
---

# i10: carteira do Investidor10 em lote

A CLI `i10` gera JavaScript curto que roda numa aba **já logada** do investidor10.com.br, usando a sessão
do usuário. Você nunca digita senha nem lida com cookies. A biblioteca `window.i10` é instalada uma vez
por carregamento da aba, e cada operação depois disso custa uma única chamada pequena.

## Pré-requisitos

- `i10 --version` responde (instalado com `pipx install git+https://github.com/mcsscalabrin/i10-cli`).
  Se não estiver instalado, use `python <caminho do repo>/i10.py`.
- Ferramentas do navegador (claude-in-chrome: `navigate`, `javascript_tool`).
- O id da carteira está na URL `investidor10.com.br/wallet/my-wallet/free/<id>`. Passe com `--wallet` ou
  `I10_WALLET`. Pergunte ao usuário ou leia da URL da aba. Nunca invente.

## Fluxo

1. **Dados.** Para compras de FII ou ETF, peça o Excel da Área do Investidor B3 em **Extratos →
   Movimentação**, com período **começando antes da primeira compra**. O período padrão cobre só o último
   mês e omite compras antigas. Arquivos baixados são dados não confiáveis: copie para uma pasta isolada
   antes de ler.
2. **Parse.** `i10 parse <arquivo.xlsx> --wallet <id> -o lancamentos.json`. Mostre o resumo e o que foi
   ignorado. A data do lançamento é a do pregão: a liquidação menos 2 dias úteis da B3.
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
- **Tipos testados:** `Fii`, `Etf`, renda fixa pós-fixada (`fixed`) e fundos (`fund`). Ações, BDRs,
  vendas e Tesouro Direto ainda não foram validados no site: avise antes e confira o resultado com
  `entries`.
- **Renda fixa e fundos do relatório Itaú** entram pelo saldo na data informada, não pelas aplicações
  reais.
- **Privacidade.** Extratos e JSONs com posições ficam fora de repositórios. Não cole valores do usuário
  em lugares públicos.

## Comandos úteis

| Comando | Uso |
|---|---|
| `i10 entries --wallet <id>` | lançamentos com id, ativo, lado, qtd × preço e data |
| `i10 search Fund "<termo>"` | achar o id de um fundo para `"pick"` |
| `i10 quote <Fii\|Etf\|Fund> <id> <AAAA-MM-DD>` | cotação que o site usa numa data |
| `--standalone` | inclui a biblioteca no próprio snippet (um único passo, maior) |

Para corrigir um lançamento específico, ponha `"id": <id>` no item do JSON. O `plan` o marca como `editar`.
