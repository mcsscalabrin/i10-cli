<p align="center">
  <img src="assets/banner.svg" alt="i10-cli — do extrato da B3 para a sua carteira no Investidor10" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="Licença MIT" src="https://img.shields.io/badge/licen%C3%A7a-MIT-1FA463"></a>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-111413">
  <img alt="Zero dependências" src="https://img.shields.io/badge/depend%C3%AAncias-zero-9AA39F">
  <img alt="Status: experimental" src="https://img.shields.io/badge/status-experimental-F2B544">
</p>

<p align="center">
  <b>Lance compras de FIIs, ETFs, renda fixa e fundos na carteira do <a href="https://investidor10.com.br">Investidor10</a> em lote,<br>
  com as datas e preços reais do extrato gratuito da B3, sem precisar da integração paga.</b>
</p>

---

## Por quê

O gerenciador de carteira do Investidor10 é gratuito, mas importar o histórico automaticamente exige a
Integração B3, que só existe no plano PRO. Sem ela, cada compra precisa ser digitada à mão, com ativo,
data, quantidade e preço, um formulário por vez.

A **Área do Investidor da B3** já entrega esse histórico de graça, num Excel. O `i10-cli` faz a ponte:

1. lê o extrato da B3 (ou o relatório de produtos do Itaú, para renda fixa e fundos);
2. gera um JSON de lançamentos que você pode revisar e editar;
3. compara esse JSON com o que já está na sua carteira e mostra o plano: criar, editar ou deixar igual;
4. grava o plano aprovado de uma vez, na aba do Investidor10 em que você já está logado.

Ele nunca pede sua senha nem guarda cookies: quem autentica é a sessão do seu navegador.

## Recursos

- **Extrato de movimentação da B3**: as compras e vendas ("Transferência - Liquidação") viram lançamentos.
  A data de liquidação é convertida para o dia do pregão (D-2 úteis, pulando fins de semana e feriados da B3).
- **Relatório "meus produtos" do Itaú**: CDB, Cofrinhos e fundos de investimento pelo saldo atual.
- **Tipos suportados**: FIIs, ETFs, renda fixa pós-fixada (% do CDI) e fundos de investimento.
- **Sincronização sem duplicar**: o `plan` reconhece o que já foi lançado. Rodar o mesmo extrato duas vezes
  não cria nada novo, e uma compra lançada com data ou preço estimados vira uma *edição*.
- **Nada é gravado sem plano**: `plan` só lê. `apply` grava exatamente o último plano mostrado.
- **Editar, nunca apagar**: a CLI não exclui lançamentos. O que existe só na carteira é apenas informado.
- **Cotação automática**: se o preço ficar `null`, usa a cotação que o próprio site sugere para a data.
- **Seguro contra ambiguidade**: se a busca de um fundo retorna mais de um resultado, ele fica fora do
  plano e os candidatos voltam para você escolher.
- **Barato para agentes**: a biblioteca é instalada uma vez na aba. Cada operação depois disso é uma
  chamada de uma linha. Uma [skill](#usando-com-agentes-skill) pronta conduz o fluxo.
- **Só biblioteca padrão**: um único arquivo Python, sem dependências.

## Instalação

```bash
pipx install git+https://github.com/mcsscalabrin/i10-cli   # cria o comando i10
i10 --help
```

Ou, sem instalar: `git clone https://github.com/mcsscalabrin/i10-cli.git` e `python i10-cli/i10.py --help`.
Requer Python 3.9 ou superior. Nada mais.

## Início rápido

**1. Baixe o extrato na B3.** Na [Área do Investidor](https://www.investidor.b3.com.br/), vá em
**Extratos → Movimentação**, escolha um período que comece antes da sua primeira compra e exporte em Excel.

**2. Gere os lançamentos.** O id da carteira aparece na URL do Investidor10
(`investidor10.com.br/wallet/my-wallet/free/<id>`).

```bash
export I10_WALLET=1234567          # PowerShell: $env:I10_WALLET = "1234567"
i10 parse movimentacao-2026-10-28.xlsx -o lancamentos.json
```

**3. Revise** o `lancamentos.json`. É texto simples: ajuste, remova ou acrescente itens.

**4. Instale a biblioteca na aba.** Abra a sua carteira no Investidor10, abra o DevTools (F12) e vá em
Console. Cole a saída de `i10 install` e pressione Enter. Repita só se a aba recarregar.

**5. Veja o plano.** Cole a saída de `i10 plan lancamentos.json`. Nada é gravado nesta etapa:

```json
{"criar":["BUY XPML11 3x105.2 2026-06-01"],
 "editar":["BUY BOVA11 2x128.4 2026-07-27 (era 2x127.9 2026-07-15)"],
 "iguais":5, "extras":[], "erros":[],
 "resumo":"1 criar, 1 editar, 5 iguais, 0 so na carteira, 0 erros -> rode i10 apply"}
```

**6. Grave** colando a saída de `i10 apply`, e **confira** com `i10 positions` (tipo, ativo, quantidade,
preço médio, total).

> Prefere colar uma vez só? `--standalone` inclui a biblioteca no próprio snippet do `plan`.

## Formato dos lançamentos

```json
{
  "wallet": 1234567,
  "entries": [
    {"kind": "ticker", "type": "Fii", "symbol": "XPML11", "qty": 3, "date": "2026-06-01", "price": 105.20},
    {"kind": "ticker", "type": "Etf", "symbol": "BOVA11", "qty": 2, "date": "2026-07-27", "price": null},
    {"kind": "fixed", "emitter": "Banco X", "title": "CDB", "indexer": "CDI", "pct": 102,
     "value": 1000.00, "date": "2026-08-10", "daily": true, "due": null},
    {"kind": "fund", "search": "GLOBAL DINAMICO RF LP", "pick": 12345, "value": 500.00, "date": "2026-09-01"}
  ]
}
```

| `kind` | Campos | Observações |
|---|---|---|
| `ticker` | `type` (`Fii`, `Etf`), `symbol`, `qty`, `date`, `price` | `price: null` usa a cotação do site. `"side": "SELL"` lança venda. `"id"` força a edição daquele lançamento. |
| `fixed` | `emitter`, `title` (`CDB`, `LCI`…), `indexer`, `pct`, `value`, `date`, `daily`, `due` | Pós-fixado. `due: null` com `daily: true` = sem vencimento. |
| `fund` | `search`, `value`, `date`, `pick` (opcional) | `pick` é o id ou um trecho do nome do fundo, para resolver ambiguidades. |

Há um exemplo completo em [`examples/lancamentos.exemplo.json`](examples/lancamentos.exemplo.json).

## Comandos

| Comando | O que faz |
|---|---|
| `parse <arquivos…> [-o arq]` | Extrato de movimentação da B3 e/ou relatório Itaú → JSON de lançamentos |
| `install` | Biblioteca `window.i10` para a aba (uma vez por carregamento) |
| `plan <arq>` | Compara o JSON com a carteira: criar / editar / igual / só na carteira. Não grava. |
| `apply` | Grava o último plano daquela aba (expira em 30 min) |
| `search <tipo> <termo>` | Busca ativos (`Fund`, `Fii`, `Etf`) |
| `quote <tipo> <id> [data]` | Cotação sugerida pelo site numa data |
| `positions [tipos…]` | Posições da carteira |
| `entries` | Lançamentos com id, tipo, lado, ativo, quantidade, preço e data |

Todos, exceto `parse`, geram JavaScript e aceitam `-o <arquivo>` e `--standalone`.

## Usando com agentes (skill)

[`skills/i10/SKILL.md`](skills/i10/SKILL.md) ensina um agente (por exemplo, Claude Code com Claude in
Chrome) a conduzir o fluxo inteiro:

1. pedir o extrato certo, inclusive avisando sobre o período padrão de um mês da B3;
2. rodar `parse`, instalar a biblioteca na aba e mostrar o `plan`;
3. pedir sua confirmação antes do `apply`;
4. conferir as posições no final.

Para instalar a skill no Claude Code, copie a pasta para o seu diretório de skills:

```bash
cp -r skills/i10 ~/.claude/skills/        # PowerShell: Copy-Item -Recurse skills\i10 $HOME\.claude\skills\
```

## Como funciona

```text
 Extrato B3 (.xlsx) ─┐
                     ├─► i10 parse ─► lancamentos.json ─► i10 plan ──┐
 Relatório Itaú ─────┘                (você revisa)                  │
                                                                     ▼
                     aba logada do Investidor10:  window.i10  (i10 install, uma vez)
                       plan  → lê a carteira e compara → {criar, editar, iguais, extras, erros}
                       apply → grava só o último plano  → [{"ok": …}, {"erro": …}]
```

O script usa os mesmos endpoints que o formulário "Adicionar Lançamento" do site chama
(`search-ticker`, `quotation`, `entry/store`, `entry/update`, `entries`, `summary/actives`), com a sua
própria sessão. Nenhum dado sai do seu computador para terceiros.

## Privacidade

- Extratos e JSONs com as suas posições estão no `.gitignore`. Não faça commit deles.
- O script gerado contém só os lançamentos do seu JSON. Leia-o antes de colar no console:
  é uma boa prática com qualquer código que você executa no navegador.

## Limitações

- **API não documentada.** Os endpoints foram observados no próprio site e podem mudar sem aviso.
- **Testado com** FIIs, ETFs, CDB pós-fixado e fundos de investimento. Ações, BDRs, vendas, renda fixa
  prefixada e Tesouro Direto ainda não foram validados. Pull requests são bem-vindos.
- **Calendário da B3:** inclui feriados nacionais e móveis de qualquer ano. Feriados municipais de São
  Paulo só fecharam a bolsa até 2021, então compras antigas perto de 25/01 ou 09/07 podem sair com o
  pregão deslocado em um dia.
- **Comparação:** o plano casa lançamentos por ativo, lado e quantidade. Dois lotes iguais do mesmo ativo com datas
  trocadas podem ser casados ao contrário. Confira a lista de *editar* antes do `apply`.
- **Renda fixa e fundos do Itaú** entram pelo saldo atual, porque o extrato de movimentação da B3 não traz
  essas aplicações.

## Aviso legal

Projeto independente e **não oficial**, sem qualquer vínculo com Investidor10, B3 ou Itaú Unibanco.
Use por sua conta e risco, respeitando os termos de uso de cada serviço. Não é recomendação de investimento.

## Contribuindo

Issues e pull requests são bem-vindos, principalmente para:

- novos formatos de extrato (outras corretoras, notas de corretagem);
- novos tipos de ativo (ações, BDRs, Tesouro Direto);
- calendário de feriados da B3 para outros anos.

## Licença

[MIT](LICENSE)
