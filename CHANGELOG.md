# Changelog

## 0.2.0

### Novo
- **`plan` / `apply`**: o `plan` compara o JSON com o que já está na carteira e mostra o que vai *criar*,
  *editar* (mesmo ativo e quantidade com data ou preço diferentes) ou deixar como *igual*, além do que
  existe *só na carteira*. Nada é gravado até o `apply`, que executa o último plano da aba (expira em
  30 min). Rodar o mesmo extrato de novo não duplica mais lançamentos.
- **`install`**: a biblioteca `window.i10` é instalada uma vez por aba. Os comandos seguintes geram
  chamadas de uma linha (`await i10.plan(...)`) em vez de repetir o script inteiro a cada lote.
- **`--standalone`**: inclui a biblioteca no próprio snippet, para colar de uma vez no console.
- **Skill** em `skills/i10/SKILL.md`, para agentes como o Claude Code conduzirem o fluxo
  parse → plan → confirmação → apply → conferência.
- **`entries`** agora mostra tipo, lado e nome do ativo, inclusive de renda fixa.
- **`pyproject.toml`**: instalação com `pipx install git+https://github.com/mcsscalabrin/i10-cli`.
- **Testes** (`python -m unittest discover -s tests`).

### Mudado
- **Feriados da B3** agora são calculados para qualquer ano (Páscoa → Carnaval, Sexta-feira Santa,
  Corpus Christi), em vez da lista fixa de 2026/2027.
- **`js` foi removido**: use `plan` + `apply`. Gravar sempre passa por um plano revisável.

## 0.1.0

- Versão inicial: `parse` (extrato B3, relatório Itaú), `js`, `search`, `quote`, `positions`, `entries`.
