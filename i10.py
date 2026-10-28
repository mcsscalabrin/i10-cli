#!/usr/bin/env python3
"""i10 - CLI para automatizar lancamentos na carteira do Investidor10.

A CLI nao faz login nem guarda cookies. Ela gera JavaScript que roda numa aba
ja logada do investidor10.com.br (console do DevTools, ou um agente com
controle do navegador). A biblioteca `window.i10` e instalada uma vez por aba;
depois, cada operacao e uma chamada curta.

Fluxo:
  i10 install                 biblioteca para a aba (uma vez por carregamento)
  i10 plan lancamentos.json   compara com a carteira: criar / editar / igual (nao grava)
  i10 apply                   executa o ultimo plano daquela aba

Comandos:
  parse  <arquivos...> [-o lancamentos.json]   extrato B3 / planilha modelo / relatorios -> JSON
  template [-o modelo.csv] [--examples]        planilha modelo para qualquer banco/corretora
  formats                                      formatos que o parse reconhece
  install                                      biblioteca window.i10
  plan   <lancamentos.json>                    planeja a sincronizacao (somente leitura)
  apply                                        grava o ultimo plano
  search <Fund|Fii|Etf> <termo>                busca ativo
  quote  <tipo> <id> [data]                    cotacao sugerida
  positions [tipos...]                         posicoes da carteira
  entries                                      lancamentos com id, ativo, qtd, preco e data

Todos os comandos que geram JavaScript aceitam -o <arquivo> e --standalone
(inclui a biblioteca no mesmo script, para colar de uma vez no console).

Formato do JSON de lancamentos:
{
  "wallet": 1234567,
  "entries": [
    {"kind": "fixed", "emitter": "Banco X", "title": "CDB", "indexer": "CDI",
     "pct": 100, "value": 1000.00, "date": "2026-10-28", "daily": true,
     "due": null},
    {"kind": "fund", "search": "GLOBAL DINAMICO RF LP", "pick": 12345,
     "value": 500.00, "date": "2026-10-28"},
    {"kind": "ticker", "type": "Fii", "symbol": "XPML11", "qty": 5,
     "date": "2026-07-15", "price": null}
  ]
}
"type" do ticker: Fii ou Etf (verificados); price null usa a cotacao do site na data;
"side": "SELL" para venda; "id" forca a edicao daquele lancamento.
"pick" (fundos) pode ser o id do ativo ou um trecho do nome; sem ele o plano
so aceita a busca se ela retornar exatamente um resultado.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

__version__ = "0.3.0"

API = "/wallet/api/proxy/wallet-app"

# --------------------------------------------------------------------------- parse

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col(ref):
    n = 0
    for c in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(c) - 64
    return n - 1


def read_xlsx(path):
    """Le um .xlsx so com a stdlib. Retorna {aba: [[celulas...], ...]}."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheets = {}
    for sh in wb.find("m:sheets", _NS):
        target = rels[sh.get("{%s}id" % _RNS)].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        rows = []
        for row in ET.fromstring(z.read(target)).iter("{%s}row" % _NS["m"]):
            vals, nxt = {}, 0
            for c in row.findall("m:c", _NS):
                idx = _col(c.get("r")) if c.get("r") else nxt
                nxt = idx + 1
                v = c.find("m:v", _NS)
                t = c.get("t")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter("{%s}t" % _NS["m"]))
                else:
                    val = v.text if v is not None else ""
                vals[idx] = val or ""
            if vals:
                rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
        sheets[sh.get("name")] = rows
    return sheets


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.upper().strip()


def parse_itau_produtos(rows, on_date):
    """Importador especifico: aba 'produtos-contratados' do relatorio do Itau -> renda fixa e fundos pelo saldo.

    Acoes, ETFs e FIIs ficam de fora: o extrato de movimentacao da B3 traz esses
    com data e preco reais. Outros bancos: use a planilha modelo (i10 template).
    """
    entries, skipped = [], []
    for r in rows[1:]:
        name, cat, value = r[0], _norm(r[1]), float(r[-1] or 0)
        if not value:
            continue
        if cat.startswith("FUNDO"):
            entries.append({"kind": "fund", "search": name, "value": value, "date": on_date})
        elif cat.startswith("CDB") or cat.startswith("COFRINHO"):
            emitter = "Itau Cofrinhos" if cat.startswith("COFRINHO") else "Itau"
            entries.append({"kind": "fixed", "emitter": emitter, "title": "CDB", "indexer": "CDI",
                            "pct": 100, "value": value, "date": on_date, "daily": True, "due": None})
        else:
            skipped.append(f"{name} ({r[1]}) -> use o extrato de movimentacao da B3")
    return entries, skipped


def easter(year):
    """Domingo de Pascoa (algoritmo de Meeus/Jones/Butcher, calendario gregoriano)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


# Sem pregao na B3. Feriados municipais de SP (25/01, 09/07) deixaram de fechar a bolsa em 2022.
_B3_FIXED = {"01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-24", "12-25", "12-31"}


def b3_holidays(year):
    p = easter(year)
    moving = {p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2), p + timedelta(days=60)}
    fixed = {date(year, int(md[:2]), int(md[3:])) for md in _B3_FIXED}
    return fixed | moving


def is_b3_day(d):
    return d.weekday() < 5 and d not in b3_holidays(d.year)


def minus_business_days(d, n):
    """Data do pregao a partir da liquidacao (D+n), pulando fins de semana e feriados B3."""
    while n:
        d -= timedelta(days=1)
        if is_b3_day(d):
            n -= 1
    return d


def _ticker_type(product):
    p = _norm(product)
    if "FUNDO DE INDICE" in p or " ETF " in f" {p} ":
        return "Etf"
    if "IMOB" in p or " FII" in p:
        return "Fii"
    return None  # acoes/BDRs: tipo ainda nao mapeado no site


def parse_b3_movimentacao(rows):
    """Extrato de movimentacao da Area do Investidor B3 -> compras/vendas.

    Usa as linhas 'Transferencia - Liquidacao' (credito = compra, debito = venda).
    A data no extrato e a da liquidacao; o lancamento usa o pregao (D-2 uteis).
    """
    entries, skipped = [], []
    hdr = [_norm(h) for h in rows[0]]
    col = {name: hdr.index(name) for name in hdr}
    for r in rows[1:]:
        if _norm(r[col["MOVIMENTACAO"]]) != "TRANSFERENCIA - LIQUIDACAO":
            continue
        product = r[col["PRODUTO"]]
        symbol = product.split(" - ")[0].strip()
        kind = _ticker_type(product)
        if kind is None:
            skipped.append(f"{symbol}: tipo de ativo nao mapeado (acao/BDR?)")
            continue
        dd, mm, yy = r[col["DATA"]].split("/")
        trade = minus_business_days(date(int(yy), int(mm), int(dd)), 2)
        e = {"kind": "ticker", "type": kind, "symbol": symbol, "qty": float(r[col["QUANTIDADE"]]),
             "date": trade.isoformat(), "price": float(r[col["PRECO UNITARIO"]])}
        if _norm(r[col["ENTRADA/SAIDA"]]).startswith("DEBITO"):
            e["side"] = "SELL"
        entries.append(e)
    entries.sort(key=lambda e: e["date"])
    return entries, skipped


# Planilha modelo: o formato universal, para qualquer banco ou corretora.
TEMPLATE_COLUMNS = ["tipo", "ativo", "data", "quantidade", "preco", "valor", "emissor", "indexador",
                    "taxa", "liquidez_diaria", "vencimento", "lado"]
TEMPLATE_EXAMPLES = [
    ["FII", "XPML11", "01/06/2026", "3", "105,20", "", "", "", "", "", "", "compra"],
    ["ETF", "BOVA11", "27/07/2026", "2", "", "", "", "", "", "", "", "compra"],
    ["CDB", "", "10/08/2026", "", "", "1.000,00", "Banco X", "CDI", "102", "sim", "", ""],
    ["LCI", "", "15/08/2026", "", "", "5.000,00", "Banco Y", "CDI", "95", "nao", "15/08/2028", ""],
    ["FUNDO", "GLOBAL DINAMICO RF LP", "01/09/2026", "", "", "500,00", "", "", "", "", "", ""],
]
# Titulos de renda fixa que o formulario do Investidor10 oferece (rotulos exatos do site).
FIXED_TITLES = {"CDB": "CDB", "LCI": "LCI", "LCA": "LCA", "LC": "LC", "LF": "LF", "RDB": "RDB",
                "DEBENTURE": "Debênture", "CRI": "CRI", "CRA": "CRA"}
_YES = {"S", "SIM", "Y", "YES", "TRUE", "1", "X"}


def _num(s):
    """'1.234,56', '1234.56', 'R$ 1.000,00' -> float. Vazio -> None."""
    s = str(s or "").replace("R$", "").replace(" ", "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _iso_date(s):
    s = str(s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    y, mo, d = (m[1], m[2], m[3]) if m else (None, None, None)
    if not m:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            y, mo, d = m[3], m[2], m[1]
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"data invalida '{s}' (use DD/MM/AAAA ou AAAA-MM-DD)") from None


def parse_template(rows):
    """Planilha modelo (i10 template) -> lancamentos. Uma linha por compra, venda ou aplicacao."""
    entries, skipped = [], []
    hdr = [_norm(h).lower() for h in rows[0]]
    for n, r in enumerate(rows[1:], start=2):
        row = {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(hdr)}
        if not any(row.values()):
            continue
        tipo = _norm(row.get("tipo"))
        try:
            when = _iso_date(row.get("data"))
            if tipo in ("FII", "ETF"):
                if not row.get("ativo") or _num(row.get("quantidade")) is None:
                    raise ValueError("FII/ETF precisa de ativo e quantidade")
                e = {"kind": "ticker", "type": tipo.title(), "symbol": _norm(row["ativo"]),
                     "qty": _num(row["quantidade"]), "date": when, "price": _num(row.get("preco"))}
                if _norm(row.get("lado")).startswith("V"):
                    e["side"] = "SELL"
            elif tipo == "FUNDO":
                if not row.get("ativo") or _num(row.get("valor")) is None:
                    raise ValueError("FUNDO precisa de ativo (nome para busca) e valor")
                e = {"kind": "fund", "search": row["ativo"], "value": _num(row["valor"]), "date": when}
            elif tipo in FIXED_TITLES:
                indexer = _norm(row.get("indexador")) or "CDI"
                if indexer != "CDI":
                    raise ValueError(f"indexador {indexer} ainda nao suportado (so pos-fixado em % do CDI)")
                if not row.get("emissor") or _num(row.get("valor")) is None or _num(row.get("taxa")) is None:
                    raise ValueError(f"{tipo} precisa de emissor, valor e taxa (% do CDI)")
                daily = _norm(row.get("liquidez_diaria")) in _YES
                due = _iso_date(row["vencimento"]) if row.get("vencimento") else None
                if not daily and not due:
                    raise ValueError("sem liquidez diaria, informe o vencimento")
                e = {"kind": "fixed", "emitter": row["emissor"], "title": FIXED_TITLES[tipo], "indexer": "CDI",
                     "pct": _num(row["taxa"]), "value": _num(row["valor"]), "date": when, "daily": daily, "due": due}
            else:
                raise ValueError(f"tipo '{row.get('tipo')}' desconhecido (use FII, ETF, FUNDO ou {', '.join(FIXED_TITLES)})")
        except ValueError as err:
            skipped.append(f"linha {n}: {err}")
            continue
        entries.append(e)
    return entries, skipped


def read_csv(path):
    import csv
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(text.splitlines(), dialect)]


def _header(rows):
    return {_norm(h) for h in (rows[0] if rows else [])}


# Importadores, testados em ordem: (nome, descricao, detecta(abas) -> linhas ou None, parse(linhas, data)).
IMPORTERS = [
    ("b3-movimentacao",
     "Area do Investidor B3 > Extratos > Movimentacao (qualquer banco/corretora): FIIs e ETFs com data e preco reais",
     lambda sheets: next((r for r in sheets.values() if {"MOVIMENTACAO", "PRODUTO"} <= _header(r)), None),
     lambda rows, on_date: parse_b3_movimentacao(rows)),
    ("modelo",
     "Planilha modelo do i10 (i10 template), CSV ou XLSX: FIIs, ETFs, renda fixa pos-CDI e fundos de qualquer banco",
     lambda sheets: next((r for r in sheets.values() if {"TIPO", "DATA", "VALOR"} <= _header(r)), None),
     lambda rows, on_date: parse_template(rows)),
    ("itau-produtos",
     "Itau > relatorio 'meus produtos' (.xlsx): renda fixa e fundos pelo saldo na data de --date",
     lambda sheets: sheets.get("produtos-contratados"),
     parse_itau_produtos),
]


def parse_files(paths, on_date):
    entries, skipped = [], []
    for p in paths:
        low = str(p).lower()
        sheets = read_xlsx(p) if low.endswith(".xlsx") else {"csv": read_csv(p)} if low.endswith(".csv") else {}
        for name, _desc, detect, parse in IMPORTERS:
            rows = detect(sheets)
            if rows:
                e, s = parse(rows, on_date)
                entries += e
                skipped += [f"{Path(p).name}: {x}" for x in s]
                break
        else:
            skipped.append(f"{Path(p).name}: formato nao reconhecido (veja i10 formats; use i10 template)")
    entries.sort(key=lambda e: e["date"])
    return entries, skipped


def cmd_formats(a):
    for name, desc, _d, _p in IMPORTERS:
        print(f"{name:16} {desc}")


def cmd_template(a):
    import csv
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(TEMPLATE_COLUMNS)
        if a.examples:
            w.writerows(TEMPLATE_EXAMPLES)
    print(f"modelo -> {a.out}" + (" (com exemplos ficticios: apague antes de usar)" if a.examples else ""))


def cmd_parse(a):
    entries, skipped = parse_files(a.files, a.date)
    out = {"wallet": a.wallet, "entries": entries}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(entries)} lancamentos -> {a.out}")
    for s in skipped:
        print("  ignorado:", s, file=sys.stderr)


# --------------------------------------------------------------------------- biblioteca da aba

RUNNER = r"""
window.i10 = (() => {
  const VERSION = '__VERSION__';
  const A = '__API__';
  const br = (d) => d.split('-').reverse().join('/');
  const iso = (s) => String(s || '').slice(0, 10);
  const list = (r) => Array.isArray(r) ? r : ((r && r.data) || []);
  const j = async (u, o) => {
    const r = await fetch(A + u, o);
    const t = await r.text();
    let b = t;
    try { b = JSON.parse(t); } catch (e) {}
    return [r.status, b];
  };
  const send = (method, path, body) => j(path, {
    method, headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify(body)
  });
  const base = (W) => ({
    user_wallet_id: W, source: 'MANUAL', custom_bonus_price: 0, cost: 0, type: 'BUY', auto_conversion: 1,
    currency: 'BRL', use_custom_bonus_price: false, use_custom_bonus_qty: false
  });
  const search = async (type, term) =>
    list((await j('/search-ticker/' + type + '?' + new URLSearchParams({term, _type: 'query', q: term})))[1]);
  const quote = async (type, id, date) => +(await j('/quotation?' + new URLSearchParams({
    ticker_type: type, ticker: id, date, type: 'BUY', currency: 'BRL', ticker_name: '', api_version: 'v2'
  })))[1];

  async function resolve(W, e) {
    if (e.kind === 'fixed') {
      return {tag: `${e.emitter} ${e.title} ${e.pct}% ${e.indexer} R$${e.value} ${e.date}`, body: {
        ...base(W), emitter: e.emitter, investment_type: e.title, rate_type: 'POST', indexer: e.indexer,
        due_date: e.due ? br(e.due) : null, percentage_cdi: e.pct, ticker_type: 'FixedIncome',
        date: br(e.date), price: e.value, dailyLiquidity: !!e.daily
      }};
    }
    if (e.kind === 'fund') {
      const res = await search('Fund', e.search);
      const hit = e.pick == null ? res : res.filter((x) =>
        x.id == e.pick || x.name.toUpperCase().includes(String(e.pick).toUpperCase()));
      if (hit.length !== 1) {
        return {erro: 'fundo ambiguo/nao achado: ' + e.search, candidatos: hit.slice(0, 8).map((x) => x.id + ' ' + x.name)};
      }
      const t = hit[0];
      const price = await quote('Fund', t.id, e.date);
      if (!(price > 0)) return {erro: 'sem cotacao: ' + t.name + ' ' + e.date};
      return {tag: `${t.name} R$${e.value} ${e.date}`, body: {
        ...base(W), applied: Math.round(e.value * 100), ticker: t.id, ticker_type: 'Fund',
        date: br(e.date), price, qty: +(e.value / price).toFixed(8)
      }};
    }
    if (e.kind === 'ticker') {
      const res = await search(e.type, e.symbol);
      const hit = res.filter((x) => x.name.toUpperCase().startsWith(e.symbol.toUpperCase() + ' '));
      if (hit.length !== 1) {
        return {erro: 'ativo ambiguo/nao achado: ' + e.type + ' ' + e.symbol, candidatos: res.slice(0, 8).map((x) => x.id + ' ' + x.name)};
      }
      const t = hit[0];
      let price = e.price;
      if (price == null) price = await quote(e.type, t.id, e.date);
      if (!(price > 0)) return {erro: 'sem cotacao: ' + e.symbol + ' ' + e.date};
      const side = e.side || 'BUY';
      return {tag: `${side} ${e.symbol} ${e.qty}x${price} ${e.date}`, body: {
        ...base(W), qty: e.qty, cost: e.cost || 0, ticker: t.id, ticker_type: e.type,
        date: br(e.date), price, type: side
      }};
    }
    return {erro: 'kind desconhecido: ' + e.kind};
  }

  const fromBr = (d) => String(d || '').split('/').reverse().join('-');
  const FIXED_GROUP = 3;

  async function existing(W) {
    const rows = [];
    for (const g of [1, 2, 3, 4, 5, 6]) {
      const [s, r] = await j(`/entries/${W}/${g}?length=1000&start=0&raw=1`);
      if (s === 200) for (const x of list(r)) rows.push({g, x});
    }
    const out = [];
    for (let i = 0; i < rows.length; i += 8) {
      const part = await Promise.all(rows.slice(i, i + 8).map(({g, x: row}) =>
        j(`/entries/${W}/0/${row.id}`).then(([s, x]) => {
          if (s === 200 && x && x.id) {
            return {id: x.id, name: x.name, ticker: x.ticker, ticker_type: x.ticker_type, qty: +x.qty, price: +x.price,
              applied: +x.applied, cost: +x.cost || 0, date: iso(x.date), side: x.type || 'BUY'};
          }
          if (g === FIXED_GROUP) {
            return {id: row.id, name: String(row.ticker || ''), ticker_type: 'FixedIncome', qty: +row.qty,
              price: +row.price_raw, applied: +row.total_raw, cost: 0, date: fromBr(row.date),
              side: row.type === 'Venda' ? 'SELL' : 'BUY'};
          }
          return {id: row.id, semDetalhe: true};
        })));
      out.push(...part);
    }
    return out;
  }

  const close = (a, b, tol) => Math.abs(a - b) <= tol;
  const dayDiff = (a, b) => Math.abs(new Date(a) - new Date(b)) / 864e5;

  function match(b, e, pool) {
    const date = e.date;
    if (b.ticker_type === 'FixedIncome') {
      const label = `${b.investment_type} - ${b.emitter} - `.toUpperCase();
      const m = pool.find((x) => x.date === date && x.name.toUpperCase().startsWith(label) &&
        (close(x.price, b.price, 0.01) || close(x.applied, b.price, 0.01)));
      return m ? ['same', m] : ['create', null];
    }
    const same = pool.filter((x) => x.ticker == b.ticker && x.side === b.type);
    if (b.ticker_type === 'Fund') {
      const m = same.find((x) => x.date === date && close(x.qty, b.qty, 1e-4));
      return m ? ['same', m] : ['create', null];
    }
    const exact = same.find((x) => x.date === date && close(x.qty, b.qty, 1e-8) && close(x.price, b.price, 0.005));
    if (exact) return ['same', exact];
    const cand = same.filter((x) => close(x.qty, b.qty, 1e-8)).sort((p, q) => dayDiff(p.date, date) - dayDiff(q.date, date))[0];
    return cand ? ['update', cand] : ['create', null];
  }

  let lastPlan = null;

  async function plan(W, E) {
    const have = await existing(W);
    const used = new Set();
    const ops = [];
    const res = {criar: [], editar: [], iguais: 0, extras: [], erros: [], semDetalhe: 0};
    for (const e of E) {
      const r = await resolve(W, e);
      if (r.erro) { res.erros.push(r); continue; }
      const b = r.body;
      if (e.id) {
        const x = have.find((h) => h.id == e.id);
        if (!x) { res.erros.push({erro: 'id nao encontrado na carteira: ' + e.id}); continue; }
        used.add(x.id);
        b.id = x.id;
        ops.push({op: 'update', tag: r.tag, body: b});
        res.editar.push(r.tag + ` (era ${x.qty}x${x.price} ${x.date})`);
        continue;
      }
      const pool = have.filter((x) => !x.semDetalhe && !used.has(x.id) && x.ticker_type === b.ticker_type);
      const [kind, m] = match(b, e, pool);
      if (m) used.add(m.id);
      if (kind === 'same') { res.iguais++; continue; }
      if (kind === 'update') {
        b.id = m.id;
        if (e.cost == null) b.cost = m.cost;
        ops.push({op: 'update', tag: r.tag, body: b});
        res.editar.push(r.tag + ` (era ${m.qty}x${m.price} ${m.date})`);
      } else {
        ops.push({op: 'create', tag: r.tag, body: b});
        res.criar.push(r.tag);
      }
    }
    const types = new Set(ops.map((o) => o.body.ticker_type).concat(E.map((e) => e.type || (e.kind === 'fund' ? 'Fund' : 'FixedIncome'))));
    for (const x of have) {
      if (x.semDetalhe) res.semDetalhe++;
      else if (!used.has(x.id) && types.has(x.ticker_type)) res.extras.push(`${x.id} ${x.side} ${x.name} ${x.qty}x${x.price} ${x.date}`);
    }
    lastPlan = {W, ops, at: Date.now()};
    if (res.semDetalhe && res.criar.length) {
      res.aviso = `${res.semDetalhe} lancamento(s) da carteira sem detalhe nao entraram na comparacao: ` +
        'confira com i10 entries se algum "criar" ja existe antes do apply';
    }
    res.resumo = `${res.criar.length} criar, ${res.editar.length} editar, ${res.iguais} iguais, ` +
      `${res.extras.length} so na carteira, ${res.erros.length} erros` + (ops.length ? ' -> rode i10 apply' : '');
    return JSON.stringify(res);
  }

  async function apply() {
    if (!lastPlan) return 'nenhum plano nesta aba: rode i10 plan primeiro';
    if (Date.now() - lastPlan.at > 30 * 60 * 1000) { lastPlan = null; return 'plano expirou (30 min): rode i10 plan de novo'; }
    const out = [];
    for (const o of lastPlan.ops) {
      const [s, r] = o.op === 'update' ? await send('PUT', '/entry/update', o.body) : await send('POST', '/entry/store', o.body);
      out.push(s === 200 && r && r.response ? {ok: o.op + ' ' + o.tag} : {erro: o.op + ' ' + o.tag, s, r: String(JSON.stringify(r)).slice(0, 200)});
    }
    lastPlan = null;
    return JSON.stringify(out);
  }

  async function positions(W, types) {
    const o = [];
    for (const t of types) {
      const [, r] = await j(`/summary/actives/${W}/${t}`);
      for (const x of list(r)) o.push([t, x.ticker_name || x.name, +x.quantity, +(+x.avg_price).toFixed(4), x.equity_total].join(' '));
    }
    return o.join('\n');
  }

  async function entries(W) {
    return (await existing(W)).map((x) => x.semDetalhe ? `${x.id} (sem detalhe)` :
      `${x.id} ${x.ticker_type} ${x.side} ${x.name} ${x.qty}x${x.price} ${x.date}`).join('\n');
  }

  const searchText = async (type, term) => (await search(type, term)).map((x) => x.id + ' ' + x.name).join('\n');

  return {version: VERSION, plan, apply, positions, entries, search: searchText, quote};
})();
'i10 ' + i10.version + ' instalado';
"""


def runner():
    return RUNNER.replace("__VERSION__", __version__).replace("__API__", API).strip()


def _call(expr):
    """Chamada curta para a biblioteca, com aviso se ela nao estiver na aba (ou for de outra versao)."""
    v = __version__
    return (f"(window.i10 && i10.version === '{v}') ? {expr} : "
            f"'i10 {v} nao esta instalado nesta aba: rode o snippet de \"i10 install\" (a aba recarregou?)'")


def _emit(a, js):
    if getattr(a, "standalone", False):
        js = runner() + "\n" + js
    if a.out:
        Path(a.out).write_text(js, encoding="utf-8")
        print(f"-> {a.out} ({len(js)} chars)")
    else:
        print(js)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def cmd_install(a):
    _emit(a, runner())


def cmd_plan(a):
    data = _load(a.file)
    _emit(a, _call(f"await i10.plan({int(data['wallet'])}, {json.dumps(data['entries'], ensure_ascii=False)})"))


def cmd_apply(a):
    _emit(a, _call("await i10.apply()"))


def cmd_search(a):
    _emit(a, _call(f"await i10.search({json.dumps(a.type)}, {json.dumps(a.term, ensure_ascii=False)})"))


def cmd_quote(a):
    _emit(a, _call(f"await i10.quote({json.dumps(a.type)}, {json.dumps(a.id)}, {json.dumps(a.date)})"))


def cmd_positions(a):
    _emit(a, _call(f"await i10.positions({a.wallet}, {json.dumps(a.types)})"))


def cmd_entries(a):
    _emit(a, _call(f"await i10.entries({a.wallet})"))


def _wallet_arg(s):
    env = os.environ.get("I10_WALLET")
    s.add_argument("--wallet", type=int, default=int(env) if env else None,
                   help="id da carteira (padrao: $I10_WALLET)")


def _js_args(s):
    s.add_argument("-o", "--out", help="grava o snippet em arquivo (UTF-8) em vez de imprimir")
    s.add_argument("--standalone", action="store_true", help="inclui a biblioteca no mesmo script")


def main(argv=None):
    p = argparse.ArgumentParser(prog="i10", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"i10 {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("formats", help="lista os formatos de arquivo que o parse reconhece")
    s.set_defaults(fn=cmd_formats)

    s = sub.add_parser("template", help="cria a planilha modelo (CSV) para qualquer banco ou corretora")
    s.add_argument("-o", "--out", default="modelo-i10.csv")
    s.add_argument("--examples", action="store_true", help="inclui linhas de exemplo ficticias")
    s.set_defaults(fn=cmd_template)

    s = sub.add_parser("parse", help="extrato B3 / planilha modelo / relatorios suportados -> JSON de lancamentos")
    s.add_argument("files", nargs="+")
    s.add_argument("-o", "--out", default="lancamentos.json")
    _wallet_arg(s)
    s.add_argument("--date", default=date.today().isoformat(), help="data dos lancamentos pelo saldo (AAAA-MM-DD)")
    s.set_defaults(fn=cmd_parse)

    s = sub.add_parser("install", help="biblioteca window.i10 para a aba (uma vez por carregamento)")
    s.add_argument("-o", "--out", help="grava em arquivo (UTF-8) em vez de imprimir")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("plan", help="compara o JSON com a carteira: criar/editar/igual (nao grava)")
    s.add_argument("file")
    _js_args(s)
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("apply", help="grava o ultimo plano feito nesta aba")
    _js_args(s)
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("search", help="busca ativo")
    s.add_argument("type", help="Fund, Fii, Etf")
    s.add_argument("term")
    _js_args(s)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("quote", help="cotacao sugerida pelo site numa data")
    s.add_argument("type")
    s.add_argument("id")
    s.add_argument("date", nargs="?", default=date.today().isoformat())
    _js_args(s)
    s.set_defaults(fn=cmd_quote)

    s = sub.add_parser("positions", help="posicoes (tipo ativo qtd preco_medio total)")
    s.add_argument("types", nargs="*", default=["Fii", "Etf", "Fund", "FixedIncome"])
    _wallet_arg(s)
    _js_args(s)
    s.set_defaults(fn=cmd_positions)

    s = sub.add_parser("entries", help="lancamentos com id (id tipo lado ativo qtd x preco data)")
    _wallet_arg(s)
    _js_args(s)
    s.set_defaults(fn=cmd_entries)

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    a = p.parse_args(argv)
    if hasattr(a, "wallet") and a.wallet is None:
        p.error("informe a carteira com --wallet <id> ou a variavel de ambiente I10_WALLET "
                "(o id aparece na URL: investidor10.com.br/wallet/my-wallet/free/<id>)")
    a.fn(a)


if __name__ == "__main__":
    main()
