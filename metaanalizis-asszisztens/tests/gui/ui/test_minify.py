# -*- coding: utf-8 -*-
"""A termék-build tömörítésének tesztjei (ma_gui/web/build_gui.py: minify_js, minify_css, pack_i18n).

A tömörítés veszteségmentes kell legyen: a JS-modulok AST-je (espree, ES2019) a tömörítés előtt és után
azonos (tests/gui/ui/minify_check.js), a CSS csak szóközt és megjegyzést veszít, az i18n-csomag
oda-vissza azonos szótárakat ad.

    python3 tests/gui/ui/test_minify.py
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "ma_gui" / "web"
SRC = WEB / "src"
CHECKER = Path(__file__).resolve().parent / "minify_check.js"


def _load_build():
    spec = importlib.util.spec_from_file_location("ma_gui_build_gui_min", str(WEB / "build_gui.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bg = _load_build()
mj = bg.minify_js


class MinifyJsTests(unittest.TestCase):
    def test_comments_and_whitespace_dropped(self):
        src = "/* fej */\n(function () {\n  'use strict';\n  // sor\n  var a = 1; // vég\n  return a;\n})();\n"
        self.assertEqual(mj(src), "(()=>{'use strict';var a=1;return a})();\n")

    def test_strings_keep_comment_like_text(self):
        self.assertEqual(mj("var s = '/* x */ // y';"), "var s='/* x */ // y';\n")
        self.assertEqual(mj('var s = "a\\"b";'), 'var s="a\\"b";\n')

    def test_restricted_productions_keep_newline(self):
        self.assertEqual(mj("function f() { return\n  1; }"), "function f(){return\n1}\n")
        self.assertEqual(mj("a\n++b"), "a\n++b\n")
        self.assertEqual(mj("for (;;) { break\nfoo; }"), "for(;;){break\nfoo}\n")

    def test_asi_sensitive_newline_kept(self):
        self.assertEqual(mj("var a = 1\nvar b = 2\n"), "var a=1\nvar b=2\n")
        self.assertEqual(mj("x = y\n(z)"), "x=y\n(z)\n")
        self.assertEqual(mj("a = b\n.c()"), "a=b.c()\n")         # a '.' folytatás: nincs ASI

    def test_operators_do_not_merge(self):
        self.assertEqual(mj("x = a + +b - -c + ++d;"), "x=a+ +b- -c+ ++d;\n")
        self.assertEqual(mj("x = 1 .toString();"), "x=1 .toString();\n")
        self.assertEqual(mj("x = a / b / c;"), "x=a/b/c;\n")

    def test_regex_literals(self):
        self.assertEqual(mj("var r = /a\\/b[/]c/g.test(s);"), "var r=/a\\/b[/]c/g.test(s);\n")
        self.assertEqual(mj("function f(s) { return /x+/i.test(s); }"), "function f(s){return/x+/i.test(s)}\n")
        self.assertEqual(mj("x = a.split(/,\\s*/)"), "x=a.split(/,\\s*/)\n")

    def test_line_separator_allowed_in_string(self):
        self.assertEqual(mj("var s = 'a\u2028b';"), "var s='a\u2028b';\n")

    def test_template_literals(self):
        src = "var t = `a ${ {x: 1}.x } b\n  c`;"
        self.assertEqual(mj(src), "var t=`a ${{x:1}.x} b\n  c`;\n")

    def test_plain_quoted_keys_unquoted(self):
        self.assertEqual(mj("h('div', { 'class': a, 'aria-label': b, '0': c, 'x$1': d });"),
                         "h('div',{class:a,'aria-label':b,'0':c,x$1:d});\n")
        self.assertEqual(mj("x = c ? 'a' : 'b';"), "x=c?'a':'b';\n")   # feltételes kifejezés nem kulcs
        self.assertEqual(mj("switch (k) { case 'a': break; }"), "switch(k){case'a':break}\n")

    def test_else_after_block(self):
        self.assertEqual(mj("if (a) {\n  b();\n}\nelse {\n  c();\n}"), "if(a){b()}else{c()}\n")

    def test_multiline_comment_counts_as_newline(self):
        self.assertEqual(mj("a /*\n*/ b"), "a\nb\n")

    def test_arrowify_where_meaning_is_kept(self):
        self.assertEqual(mj("x.map(function (a) { return a + 1; });"), "x.map(a=>a+1);\n")
        self.assertEqual(mj("f(function (a, b) { g(); }, 1);"), "f((a,b)=>{g()},1);\n")
        self.assertEqual(mj("var o = { k: function () { return new Error('x'); } };"), "var o={k:()=>new Error('x')};\n")
        self.assertEqual(mj("y = new window.BroadcastChannel('c'); z = [function () {}];"), "y=new window.BroadcastChannel('c');z=[()=>{}];\n")

    def test_arrowify_skipped_when_meaning_could_change(self):
        for src in ("var f = function () { return this; };",           # saját this
                    "var f = function () { return arguments.length; };",
                    "var f = function () { return new.target; };",
                    "var g = function g() {};",                       # névvel ellátott
                    "q = a || function () {};",                       # '||' operandusa: a nyíl ott szintaxishiba
                    "r = function () {}.bind(null);",                 # tagkifejezés tárgya
                    "var F = function () {}; var o = new F();",       # saját konstruktor a modulban
                    "t = new (function () {})();"):
            self.assertNotIn("=>", mj(src), src)

    def test_last_semicolon_before_brace_dropped(self):
        """A '}' előtti ';' elhagyható (ASI — az AST azonos), de az üres utasítás marad."""
        self.assertEqual(mj("function f(){a();b();}"), "function f(){a();b()}\n")
        for src in ("if(a){;}", "function g(){if(a);}", "function h(){for(;;);}", "function m(){if(a)b();else;}",
                    "function k(){do{a();}while(b);}"):
            self.assertIn(";}", mj(src), src)

    def test_var_statements_merged(self):
        """var a=1;var b=2; → var a=1,b=2; (a minify_check.js AST-szinten azonosnak veszi)."""
        self.assertEqual(mj("var a = 1; var b = 2; var c = function () { var d = 1; var e = 2; }; f();"),
                         "var a=1,b=2,c=()=>{var d=1,e=2};f();\n")
        self.assertEqual(mj("for (var i = 0; i < 3; i++) { var q = 1; var r = 2; }"), "for(var i=0;i<3;i++){var q=1,r=2}\n")
        self.assertEqual(mj("var x = f(a,\n b); var y = 1;"), "var x=f(a,b),y=1;\n")

    def test_var_merge_skipped_when_meaning_could_change(self):
        for src in ("if (x) var a = 1; var b = 2;",                    # az if törzse csak az első var
                    "var a = 1\nfoo(); var b = 2;",                    # ASI-határ az utasításban
                    "var x = a ||\n b; var y = 1;",
                    "switch (x) { case 1: var a = 1; var b = 2; }"):
            self.assertNotIn(",b=", mj(src).replace(",y=", ",b="), src)

    def test_short_literals(self):
        """true → !0, false → !1, undefined → void 0 (a minify_check.js AST-szinten azonosnak veszi)."""
        self.assertEqual(mj("a = true; b = false; c = x === undefined;"), "a=!0;b=!1;c=x===void 0;\n")
        self.assertEqual(mj("f(a ? true : false); return true;"), "f(a?!0:!1);return!0;\n")
        for src in ("o = {true: 1};", "x.true = 1;", "y = true.toString();", "z = undefined[k];", "w = obj.undefined;"):
            self.assertNotIn("!0", mj(src), src)
            self.assertNotIn("void 0", mj(src), src)

    def test_concise_arrow_body(self):
        """{ return kif; } → kif, ahol a jelentés azonos (a minify_check.js AST-szinten is ellenőrzi)."""
        self.assertEqual(mj("f(function () { return {a: 1}; });"), "f(()=>({a:1}));\n")       # objektumliterál: zárójel
        self.assertEqual(mj("g(function (x) { return function (y) { return x + y; }; });"), "g(x=>y=>x+y);\n")
        self.assertEqual(mj("v = c ? function () { return 1; } : 2;"), "v=c?()=>1:2;\n")
        self.assertEqual(mj("o = { k: function () { return a ? b : c; }, z: 1 };"), "o={k:()=>a?b:c,z:1};\n")
        self.assertEqual(mj("f(function () { return `a${b, c}`; });"), "f(()=>`a${b,c}`);\n")

    def test_concise_arrow_skipped_when_meaning_could_change(self):
        self.assertEqual(mj("f(function () { return a, b; });"), "f(()=>{return a,b});\n")    # vesszőoperátor
        self.assertEqual(mj("f(function () { return\n a; });"), "f(()=>{return\na});\n")     # ASI: return;
        self.assertEqual(mj("f(function () { return; });"), "f(()=>{return});\n")
        self.assertEqual(mj("f(function () { if (a) { return 1; } return 2; });"), "f(()=>{if(a){return 1}return 2});\n")
        self.assertEqual(mj("var f = function () { return this.x; };"), "var f=function(){return this.x};\n")

    def test_unterminated_input_rejected(self):
        for bad in ("var s = 'abc", "/* x", "var r = /abc", "var t = `abc"):
            with self.assertRaises(bg.MinifyError):
                mj(bad)


class MinifyCssTests(unittest.TestCase):
    def test_basic(self):
        css = "/* fej */\n.a :hover { color : red ; }\n@media (max-width: 600px) {\n  .b , .c { margin: 0 auto; }\n}\n"
        self.assertEqual(bg.minify_css(css), ".a :hover{color :red}@media (max-width:600px){.b,.c{margin:0 auto}}\n")

    def test_strings_and_calc_kept(self):
        css = '.a::before { content: "x ; } /* y */"; width: calc(100% - 2px); }\n'
        self.assertEqual(bg.minify_css(css), '.a::before{content:"x ; } /* y */";width:calc(100% - 2px)}\n')


class PackI18nTests(unittest.TestCase):
    def test_roundtrip_real_dictionaries(self):
        d = bg.load_i18n(SRC)
        packed = bg.pack_i18n(d)
        self.assertNotIn("<", packed)
        self.assertEqual(bg.unpack_i18n(packed), d)
        self.assertLess(len(packed.encode("utf-8")), len(bg.json_for_script(d).encode("utf-8")) * 0.6)

    def test_roundtrip_edge_cases(self):
        d = {"hu": {"a": "~~ tilde ~", "a.b": "x" * 300, "a.b.c": "𝑥 asztrális 😀 é", "z": "<b>&</b>"},
             "en": {"a": "~", "a.b": "xy" * 150, "a.b.c": "𝑥 astral 😀", "z": "</script><!--"}}
        packed = bg.pack_i18n(d)
        self.assertNotIn("<", packed)
        self.assertEqual(bg.unpack_i18n(packed), d)

    def test_lz_roundtrip_overlapping(self):
        for s in ("", "a", "abcabcabcabcabcabc", "~" * 40, "ab~cd" * 100, "".join(chr(0x100 + i % 50) for i in range(5000))):
            self.assertEqual(bg.lz_decode(bg.lz_encode(s)), s)

    def test_js_decoder_alphabet_matches(self):
        text = (SRC / "i18n.js").read_text(encoding="utf-8")
        self.assertIn("var LZ_ALPHABET = '%s';" % bg.LZ_ALPHABET, text)
        self.assertIn("var LZ_MIN = %d;" % bg.LZ_MIN, text)


@unittest.skipIf(shutil.which("node") is None, "node nincs telepítve")
class AstEquivalenceTests(unittest.TestCase):
    def test_every_product_module_keeps_its_ast(self):
        items = []
        for name in bg.js_order(SRC, dev=False):
            orig = bg.strip_dev(bg.read_text(SRC / name), name)
            items.append({"name": name, "orig": orig, "min": mj(orig)})
        r = subprocess.run(["node", str(CHECKER)], input=json.dumps(items), capture_output=True, text=True)
        if r.returncode == 3:
            self.skipTest(r.stdout.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK %d" % len(items), r.stdout)

    def test_product_script_parses_as_es2019(self):
        html = bg.build(dev=False)
        import re
        scripts = re.findall(r'<script nonce="\{\{CSP_NONCE\}\}">(.*?)</script>', html, re.S)
        self.assertEqual(len(scripts), 1)
        r = subprocess.run(["node", str(CHECKER)], input=json.dumps([{"name": "prod", "orig": scripts[0], "min": scripts[0]}]),
                           capture_output=True, text=True)
        if r.returncode == 3:
            self.skipTest(r.stdout.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
