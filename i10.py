#!/usr/bin/env python3
"""i10 - CLI para automatizar lancamentos na carteira do Investidor10.

A CLI nao faz login nem guarda cookies. Ela gera um snippet JavaScript compacto
que roda numa aba ja logada do investidor10.com.br (ex.: via claude-in-chrome
javascript_tool, ou colando no console do DevTools). Assim um lote inteiro de
lancamentos custa uma unica chamada, em vez de dezenas de cliques/screenshots.

Comandos:
  parse  <arquivos...> [-o lancamentos.json]   extrato B3 / relatorio Itau -> JSON
  js     <lancamentos.json> [--dry-run]        gera o snippet que grava os lancamentos
  search <Fund|FixedIncome|...> <termo>        gera snippet de busca de ativo
  quote  <ticker_type> <id> [data]             gera snippet de cotacao sugerida
  positions [tipos...]                         gera snippet que lista as posicoes
  entries [grupos...]                          gera snippet que lista lancamentos com id

Um lancamento "ticker" com "id" edita o existente (PUT /entry/update) em vez de criar.

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
"type" do ticker: Fii ou Etf (verificados); price null usa a cotacao do site na data.
"pick" (fundos) pode ser o id do ativo ou um trecho do nome; sem ele o snippet
so grava se a busca retornar exatamente um resultado, senao devolve os candidatos.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

__version__ = "0.1.0"

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


def parse_itau_produtos(path, on_date):
    """Relatorio 'meus produtos' do Itau -> lancamentos de renda fixa e fundos.

    Acoes, ETFs e FIIs ficam de fora: a Integracao B3 do site importa esses
    com data e preco reais.
    """
    rows = read_xlsx(path).get("produtos-contratados", [])
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
            skipped.append(f"{name} ({r[1]}) -> use a Integracao B3")
    return entries, skipped


# Feriados sem pregao na B3: datas fixas + moveis de 2026/2027 (Carnaval, Sexta Santa, Corpus Christi)
_B3_FIXED = {"01-01", "04-21", "05-01", "09-07", "10-12", "11-02", "11-15", "11-20", "12-24", "12-25", "12-31"}
_B3_MOVING = {"2026-02-16", "2026-02-17", "2026-04-03", "2026-06-04",
              "2027-02-08", "2027-02-09", "2027-03-26", "2027-05-27"}


def _is_b3_day(d):
    s = d.isoformat()
    return d.weekday() < 5 and s[5:] not in _B3_FIXED and s not in _B3_MOVING


def _minus_business_days(d, n):
    """Data do pregao a partir da liquidacao (D+2), pulando fins de semana e feriados B3."""
    from datetime import timedelta
    while n:
        d -= timedelta(days=1)
        if _is_b3_day(d):
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
        trade = _minus_business_days(date(int(yy), int(mm), int(dd)), 2)
        e = {"kind": "ticker", "type": kind, "symbol": symbol, "qty": float(r[col["QUANTIDADE"]]),
             "date": trade.isoformat(), "price": float(r[col["PRECO UNITARIO"]])}
        if _norm(r[col["ENTRADA/SAIDA"]]).startswith("DEBITO"):
            e["side"] = "SELL"
        entries.append(e)
    entries.sort(key=lambda e: e["date"])
    return entries, skipped


def cmd_parse(a):
    entries, skipped = [], []
    for p in a.files:
        sheets = read_xlsx(p) if p.lower().endswith(".xlsx") else {}
        first = next(iter(sheets.values()), [[]])
        if "produtos-contratados" in sheets:
            e, s = parse_itau_produtos(p, a.date)
            entries += e
            skipped += s
        elif first and "MOVIMENTACAO" in [_norm(h) for h in first[0]]:
            e, s = parse_b3_movimentacao(first)
            entries += e
            skipped += s
        else:
            skipped.append(f"{Path(p).name}: formato nao suportado pelo parse (ignorado)")
    out = {"wallet": a.wallet, "entries": entries}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(entries)} lancamentos -> {a.out}")
    for s in skipped:
        print("  ignorado:", s, file=sys.stderr)


# --------------------------------------------------------------------------- js

_RUNNER = r"""
const W=__WALLET__,E=__ENTRIES__,DRY=__DRY__,A='__API__';
const br=d=>d.split('-').reverse().join('/');
const j=async(u,o)=>{const r=await fetch(A+u,o);const t=await r.text();try{return [r.status,JSON.parse(t)]}catch(e){return [r.status,t]}};
const base={user_wallet_id:W,source:'MANUAL',custom_bonus_price:0,cost:0,type:'BUY',auto_conversion:1,currency:'BRL',use_custom_bonus_price:false,use_custom_bonus_qty:false};
const out=[];
for(const e of E){
 let body,tag;
 if(e.kind==='fixed'){
  tag=`${e.emitter} ${e.title} ${e.pct}% ${e.indexer} R$${e.value}`;
  body={...base,emitter:e.emitter,investment_type:e.title,rate_type:'POST',indexer:e.indexer,due_date:e.due?br(e.due):null,percentage_cdi:e.pct,ticker_type:'FixedIncome',date:br(e.date),price:e.value,dailyLiquidity:!!e.daily};
 }else if(e.kind==='fund'){
  let [,res]=await j('/search-ticker/Fund?'+new URLSearchParams({term:e.search,_type:'query',q:e.search}));
  res=Array.isArray(res)?res:(res.data||[]);
  let hit=res;
  if(e.pick!=null)hit=res.filter(x=>x.id==e.pick||x.name.toUpperCase().includes(String(e.pick).toUpperCase()));
  if(hit.length!==1){out.push({e:e.search,erro:'ambiguo/nao achado',candidatos:hit.slice(0,8).map(x=>x.id+' '+x.name)});continue}
  const t=hit[0];tag=`${t.name} R$${e.value}`;
  const [,q]=await j('/quotation?'+new URLSearchParams({ticker_type:'Fund',ticker:t.id,date:e.date,type:'BUY',currency:'BRL',ticker_name:'',api_version:'v2'}));
  const price=+q;if(!(price>0)){out.push({e:tag,erro:'sem cotacao',q});continue}
  body={...base,applied:Math.round(e.value*100),ticker:t.id,ticker_type:'Fund',date:br(e.date),price,qty:+(e.value/price).toFixed(8)};
 }else if(e.kind==='ticker'){
  let [,res]=await j(`/search-ticker/${e.type}?`+new URLSearchParams({term:e.symbol,_type:'query',q:e.symbol}));
  res=Array.isArray(res)?res:(res.data||[]);
  const hit=res.filter(x=>x.name.toUpperCase().startsWith(e.symbol.toUpperCase()+' '));
  if(hit.length!==1){out.push({e:e.symbol,erro:'ambiguo/nao achado',candidatos:res.slice(0,8).map(x=>x.id+' '+x.name)});continue}
  const t=hit[0];let price=e.price;
  if(price==null){const [,q]=await j('/quotation?'+new URLSearchParams({ticker_type:e.type,ticker:t.id,date:e.date,type:'BUY',currency:'BRL',ticker_name:'',api_version:'v2'}));price=+q}
  if(!(price>0)){out.push({e:e.symbol,erro:'sem cotacao'});continue}
  tag=`${e.symbol} ${e.qty}x${price} ${e.date}`;
  body={...base,qty:e.qty,cost:e.cost||0,ticker:t.id,ticker_type:e.type,date:br(e.date),price,type:e.side||'BUY'};
 }else{out.push({e,erro:'kind desconhecido'});continue}
 if(e.id)body.id=e.id;
 if(DRY){out.push({ok:'dry',tag,body});continue}
 const [s,r]=await j(e.id?'/entry/update':'/entry/store',{method:e.id?'PUT':'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify(body)});
 out.push(s===200&&r&&r.response?{ok:tag}:{erro:tag,s,r:String(JSON.stringify(r)).slice(0,200)});
}
JSON.stringify(out);
"""


def _snippet(template, **kw):
    s = template
    for k, v in kw.items():
        s = s.replace(f"__{k}__", v)
    # compacta: remove quebras de linha e indentacao
    return "".join(line.strip() for line in s.strip().splitlines())


def cmd_js(a):
    data = json.loads(Path(a.file).read_text(encoding="utf-8-sig"))
    js = _snippet(_RUNNER, WALLET=str(int(data["wallet"])), ENTRIES=json.dumps(data["entries"], ensure_ascii=False),
                  DRY="true" if a.dry_run else "false", API=API)
    if a.out:
        Path(a.out).write_text(js, encoding="utf-8")
        print(f"{len(data['entries'])} lancamentos -> {a.out} ({len(js)} chars)")
    else:
        print(js)


def cmd_search(a):
    q = json.dumps(a.term)
    print(f"(await fetch('{API}/search-ticker/{a.type}?'+new URLSearchParams({{term:{q},_type:'query',q:{q}}}))"
          ".then(r=>r.json())).map?.(x=>x.id+' '+x.name).join('\\n')")


def cmd_quote(a):
    print(f"await fetch('{API}/quotation?'+new URLSearchParams({{ticker_type:'{a.type}',ticker:'{a.id}',"
          f"date:'{a.date}',type:'BUY',currency:'BRL',ticker_name:'',api_version:'v2'}})).then(r=>r.text())")


def cmd_positions(a):
    types = json.dumps(a.types)
    print(f"const o=[];for(const t of {types}){{const r=await fetch('{API}/summary/actives/{a.wallet}/'+t)"
          ".then(r=>r.json());for(const x of (r.data||[]))o.push([t,x.ticker_name||x.name,x.quantity,x.avg_price,"
          "x.equity_total].join(' '))};o.join('\\n')")


def cmd_entries(a):
    # grupos da tela de Lancamentos: 1 renda variavel, 3 renda fixa, 6 fundos
    groups = json.dumps(a.groups)
    print(f"const o=[];for(const g of {groups}){{const r=await fetch(`{API}/entries/{a.wallet}/${{g}}?length=500&start=0&raw=1`)"
          ".then(r=>r.json());for(const x of (r.data||[]))o.push([g,x.id,+x.qty,x.price,(x.date||'').slice(0,10)].join(' '))};"
          "o.join('\\n')")


def _wallet_arg(s):
    env = os.environ.get("I10_WALLET")
    s.add_argument("--wallet", type=int, default=int(env) if env else None,
                   help="id da carteira (padrao: $I10_WALLET)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="i10", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"i10 {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("parse", help="extrato B3 / relatorio Itau -> JSON de lancamentos")
    s.add_argument("files", nargs="+")
    s.add_argument("-o", "--out", default="lancamentos.json")
    _wallet_arg(s)
    s.add_argument("--date", default=date.today().isoformat(), help="data dos lancamentos (AAAA-MM-DD)")
    s.set_defaults(fn=cmd_parse)

    s = sub.add_parser("js", help="gera snippet que grava os lancamentos")
    s.add_argument("file")
    s.add_argument("--dry-run", action="store_true", help="resolve ativos e cotacoes sem gravar")
    s.add_argument("-o", "--out", help="grava o snippet em arquivo (UTF-8) em vez de imprimir")
    s.set_defaults(fn=cmd_js)

    s = sub.add_parser("search", help="gera snippet de busca de ativo")
    s.add_argument("type", help="Fund, FixedIncome, ...")
    s.add_argument("term")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("quote", help="gera snippet de cotacao sugerida")
    s.add_argument("type")
    s.add_argument("id")
    s.add_argument("date", nargs="?", default=date.today().isoformat())
    s.set_defaults(fn=cmd_quote)

    s = sub.add_parser("positions", help="gera snippet que lista posicoes (tipo nome qtd preco_medio total)")
    s.add_argument("types", nargs="*", default=["Fii", "Etf", "Fund", "FixedIncome"])
    _wallet_arg(s)
    s.set_defaults(fn=cmd_positions)

    s = sub.add_parser("entries", help="gera snippet que lista lancamentos com id (grupo id qtd preco data)")
    s.add_argument("groups", nargs="*", type=int, default=[1, 3, 6])
    _wallet_arg(s)
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
