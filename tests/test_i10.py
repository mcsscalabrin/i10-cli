import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import i10  # noqa: E402


def make_xlsx(path, sheet, rows):
    """Planilha minima (inlineStr) no formato que read_xlsx entende."""
    def cell(v):
        v = str(v).replace("&", "&amp;").replace("<", "&lt;")
        return f'<c t="inlineStr"><is><t>{v}</t></is></c>'
    body = "".join("<row>" + "".join(cell(v) for v in r) + "</row>" for r in rows)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rns}><sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")


B3_HEADER = ["Entrada/Saída", "Data", "Movimentação", "Produto", "Instituição", "Quantidade",
             "Preço unitário", "Valor da Operação"]


class Holidays(unittest.TestCase):
    def test_easter(self):
        self.assertEqual(i10.easter(2024), date(2024, 3, 31))
        self.assertEqual(i10.easter(2026), date(2026, 4, 5))
        self.assertEqual(i10.easter(2027), date(2027, 3, 28))

    def test_moving_holidays_2026(self):
        h = i10.b3_holidays(2026)
        for d in (date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3), date(2026, 6, 4)):
            self.assertIn(d, h)

    def test_trade_date_skips_holiday(self):
        # liquidou quarta 09/09/2026; 07/09 (Independencia) nao tem pregao
        self.assertEqual(i10.minus_business_days(date(2026, 9, 9), 2), date(2026, 9, 4))
        # liquidou segunda 19/10/2026 -> pregao quinta 15/10
        self.assertEqual(i10.minus_business_days(date(2026, 10, 19), 2), date(2026, 10, 15))
        # Carnaval 2025 (03 e 04/03): liquidou 06/03 -> pregao 28/02
        self.assertEqual(i10.minus_business_days(date(2025, 3, 6), 2), date(2025, 2, 28))


class ParseB3(unittest.TestCase):
    def test_movimentacao(self):
        rows = [B3_HEADER,
                ["Credito", "23/10/2026", "Rendimento", "XPML11 - XP MALLS FDO INV IMOB FII", "CORRETORA", "5", "0.9", "4.5"],
                ["Credito", "09/09/2026", "Transferência - Liquidação", "XPML11 - XP MALLS FDO INV IMOB FII", "CORRETORA", "3", "106.44", "319.32"],
                ["Debito", "19/10/2026", "Transferência - Liquidação", "BOVA11 - ISHARES BOVESPA FUNDO DE INDICE", "CORRETORA", "2", "128.99", "257.98"],
                ["Credito", "19/10/2026", "Transferência - Liquidação", "PETR4 - PETROBRAS PN", "CORRETORA", "10", "38.5", "385"]]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "movimentacao.xlsx")
            make_xlsx(p, "Movimentação", rows)
            entries, skipped = i10.parse_files([p], "2026-10-28")
        self.assertEqual(entries, [
            {"kind": "ticker", "type": "Fii", "symbol": "XPML11", "qty": 3.0, "date": "2026-09-04", "price": 106.44},
            {"kind": "ticker", "type": "Etf", "symbol": "BOVA11", "qty": 2.0, "date": "2026-10-15", "price": 128.99, "side": "SELL"},
        ])
        self.assertEqual(len(skipped), 1)
        self.assertIn("PETR4", skipped[0])


class ParseItau(unittest.TestCase):
    def test_produtos(self):
        rows = [["Produto", "Categoria", "Saldo Investido (R$)"],
                ["XPTO11", "Ações", "100"],
                ["CDB-DI", "CDB, Renda Fixa e Inv. Estruturados", "1000.5"],
                ["Fundo Y", "Fundo de Investimento", "500"],
                ["Cofrinhos", "Cofrinhos", "0"]]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "produtos.xlsx")
            make_xlsx(p, "produtos-contratados", rows)
            entries, skipped = i10.parse_files([p], "2026-10-28")
        self.assertEqual([e["kind"] for e in entries], ["fixed", "fund"])
        self.assertEqual(entries[0]["value"], 1000.5)
        self.assertEqual(len(skipped), 1)


class Template(unittest.TestCase):
    def parse_csv(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "carteira.csv")
            Path(p).write_text(text, encoding="utf-8")
            return i10.parse_files([p], "2026-10-28")

    def test_csv_semicolon_and_decimal_comma(self):
        entries, skipped = self.parse_csv(
            "tipo;ativo;data;quantidade;preco;valor;emissor;indexador;taxa;liquidez_diaria;vencimento;lado\n"
            "FII;xpml11;01/06/2026;3;105,20;;;;;;;compra\n"
            "ETF;BOVA11;2026-07-27;2;;;;;;;;venda\n"
            "CDB;;10/08/2026;;;1.000,50;Banco X;CDI;102;sim;;\n"
            "LCI;;15/08/2026;;;5.000,00;Banco Y;;95;nao;15/08/2028;\n"
            "FUNDO;GLOBAL DINAMICO RF LP;01/09/2026;;;500,00;;;;;;\n")
        self.assertEqual(skipped, [])
        self.assertEqual(entries[0], {"kind": "ticker", "type": "Fii", "symbol": "XPML11", "qty": 3.0,
                                      "date": "2026-06-01", "price": 105.2})
        self.assertEqual(entries[1]["side"], "SELL")
        self.assertIsNone(entries[1]["price"])
        self.assertEqual(entries[2], {"kind": "fixed", "emitter": "Banco X", "title": "CDB", "indexer": "CDI",
                                      "pct": 102.0, "value": 1000.5, "date": "2026-08-10", "daily": True, "due": None})
        self.assertEqual(entries[3]["due"], "2028-08-15")
        self.assertFalse(entries[3]["daily"])
        self.assertEqual(entries[4], {"kind": "fund", "search": "GLOBAL DINAMICO RF LP", "value": 500.0,
                                      "date": "2026-09-01"})

    def test_comma_separated_with_dot_decimals(self):
        entries, skipped = self.parse_csv("tipo,ativo,data,quantidade,preco,valor\nFII,XPML11,01/06/2026,3,105.20,\n")
        self.assertEqual(skipped, [])
        self.assertEqual(entries[0]["price"], 105.2)

    def test_invalid_lines_are_reported_not_imported(self):
        entries, skipped = self.parse_csv(
            "tipo;ativo;data;quantidade;preco;valor;emissor;indexador;taxa;liquidez_diaria;vencimento\n"
            "CDB;;10/08/2026;;;1000;Banco X;IPCA;6;nao;10/08/2030\n"
            "CDB;;10/08/2026;;;1000;Banco X;CDI;100;nao;\n"
            "ACAO;PETR4;10/08/2026;10;38;;;;;;\n"
            "FII;XPML11;32/13/2026;1;100;;;;;;\n")
        self.assertEqual(entries, [])
        self.assertEqual(len(skipped), 4)
        for msg, expected in zip(skipped, ["IPCA", "vencimento", "desconhecido", "data invalida"]):
            self.assertIn(expected, msg)

    def test_xlsx_template(self):
        rows = [i10.TEMPLATE_COLUMNS, ["FII", "XPML11", "01/06/2026", "3", "105,20", "", "", "", "", "", "", ""]]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "carteira.xlsx")
            make_xlsx(p, "Planilha1", rows)
            entries, skipped = i10.parse_files([p], "2026-10-28")
        self.assertEqual([e["symbol"] for e in entries], ["XPML11"])

    def test_template_examples_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "modelo.csv")
            r = subprocess.run([sys.executable, str(ROOT / "i10.py"), "template", "--examples", "-o", p],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            entries, skipped = i10.parse_files([p], "2026-10-28")
        self.assertEqual(skipped, [])
        self.assertEqual(len(entries), len(i10.TEMPLATE_EXAMPLES))

    def test_unknown_file_is_reported(self):
        entries, skipped = self.parse_csv("foo;bar\n1;2\n")
        self.assertEqual(entries, [])
        self.assertIn("formato nao reconhecido", skipped[0])


class Snippets(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, I10_WALLET="1234567")
        r = subprocess.run([sys.executable, str(ROOT / "i10.py"), *args], capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_calls_are_short_and_guarded(self):
        out = self.run_cli("apply")
        self.assertIn("await i10.apply()", out)
        self.assertIn(f"i10.version === '{i10.__version__}'", out)
        self.assertLess(len(out), 300)

    def test_plan_embeds_entries(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.json")
            Path(p).write_text(json.dumps({"wallet": 1234567, "entries": [{"kind": "ticker", "symbol": "XPML11"}]}))
            out = self.run_cli("plan", p)
        self.assertIn("await i10.plan(1234567, [", out)
        self.assertIn("XPML11", out)

    def test_standalone_includes_runner(self):
        out = self.run_cli("positions", "--standalone")
        self.assertTrue(out.startswith("window.i10 = "))
        self.assertIn("await i10.positions(1234567", out)

    def test_missing_wallet_is_an_error(self):
        env = {k: v for k, v in os.environ.items() if k != "I10_WALLET"}
        r = subprocess.run([sys.executable, str(ROOT / "i10.py"), "entries"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("I10_WALLET", r.stderr)


if __name__ == "__main__":
    unittest.main()
