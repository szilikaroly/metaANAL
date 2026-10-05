#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A MA-munkapad felületének buildje (terv 2.3, 3.3, 6.8, 7.1 T6/T9, 7.6).

A ``web/src/**`` forrásait rögzített sorrendben egyetlen önálló HTML-fájlba fűzi a
``web/src/index.template.html`` sablon alapján. Csak Python 3 stdlib; a kimenet determinisztikus
(nincs időbélyeg, rendezett fájllista, LF sorvégek).

Kimenet:
  python3 ma_gui/web/build_gui.py            → ma_gui/web/dist/index.html      (termék; ≤ 450 KB)
  python3 ma_gui/web/build_gui.py --dev      → ma_gui/web/dist/index.dev.html  (+ fixture-ök és fixture-háttér)
  python3 ma_gui/web/build_gui.py --check    → 1-es kilépés, ha a dist/index.html nem naprakész
  python3 ma_gui/web/build_gui.py --list     → a modulok sorrendje

A sablon ``{{CSP_NONCE}}`` helyeit a szerver válaszonként cseréli a CSP-nonce-ra; a build ezeket
érintetlenül hagyja. Minden ``<script>`` és ``<style>`` tag nonce-ot visz.

A termék-build a lint után veszteségmentesen tömörít (minify_js, minify_css, pack_i18n — lásd a fájl
végén); a --dev build az olvasható forrást adja. Az i18n mindkét módban a tömörített („lz1”) alakban
kerül a lapba, így a kicsomagoló (i18n.js) mindkettőben fut.

Modulsorrend (JS): dom, geom, i18n, store, api, [dev/fixture_backend + dev/*.js — csak --dev],
ui, selftest, app, components/*.js, plots/*.js, screens/*.js (ábécérendben), boot.
CSS: css/tokens.css, css/base.css, majd a többi css/*.css ábécérendben.
i18n: i18n/{hu,en}.json + i18n/{hu,en}/*.json (a két nyelv kulcskészlete azonos kell legyen).

A build-lint (assert) tiltja többek közt: HTML-sztring DOM-ba írását, eval-t, szám-formázást
(toFixed/toPrecision/Intl.NumberFormat/toLocaleString), Math.*-t a geom.js-en kívül, böngészőtárolót
az engedett modulokon kívül, IndexedDB-t, külső URL-t (kivéve az SVG-névteret a dom.js-ben),
ES2020+ szintaxist (?. és ??), console.log-ot. Lásd: JS_RULES.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
SRC = WEB / "src"
FIXTURES = WEB / "fixtures"
DIST = WEB / "dist"
PROD_OUT = DIST / "index.html"
DEV_OUT = DIST / "index.dev.html"
TEMPLATE = "index.template.html"

MAX_BYTES = 450 * 1024
NONCE = "{{CSP_NONCE}}"
SVG_NS = "http://www.w3.org/2000/svg"

CORE_HEAD = ["dom.js", "geom.js", "i18n.js", "store.js", "api.js"]
DEV_FIRST = ["dev/fixture_backend.js"]
CORE_MID = ["ui.js", "selftest.js", "app.js"]
GLOB_DIRS = ["components", "plots", "screens"]
TAIL = ["boot.js"]
CSS_HEAD = ["css/tokens.css", "css/base.css"]
LANGS = ("hu", "en")
TEMPLATE_KEYS = ("CSP_NONCE", "CSS", "JS", "DATA", "BUILD_ID")

DEV_BLOCK_RE = re.compile(r"/\*<dev>\*/.*?/\*</dev>\*/", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
I18N_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
I18N_PARAM_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


class BuildError(Exception):
    pass


# (minta, magyarázat, engedett fájlok vagy None). A minta a forrásszövegre fut (kommentekre is).
JS_RULES = [
    (r"innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment|DOMParser|\.srcdoc\b",
     "HTML-sztring nem kerülhet a DOM-ba (T6); használd a MA.dom.h()-t", None),
    (r"document\.write", "document.write tiltott", None),
    (r"\beval\s*\(|new\s+Function\s*\(|set(?:Timeout|Interval)\s*\(\s*['\"`]",
     "kódfuttatás sztringből tiltott (CSP)", None),
    (r"toFixed|toPrecision|Intl\.NumberFormat|toLocaleString",
     "számformázás tiltott: minden számszöveg a motor display_text-je (4.0, 6.8)", None),
    (r"\bMath\.", "Math.* csak a geom.js-ben (6.8)", {"geom.js"}),
    (r"\blocalStorage\b", "localStorage csak a store.js-ben (MA.prefs, 'mag.pref.*')", {"store.js", "selftest.js"}),
    (r"\bsessionStorage\b", "sessionStorage csak az api.js-ben (token)", {"api.js", "selftest.js"}),
    (r"\bsetItem\s*\(", "böngészőtároló írása csak a store.js / api.js-ben", {"store.js", "api.js"}),
    (r"indexedDB|openDatabase|caches\.open|serviceWorker|document\.cookie\s*=",
     "IndexedDB / Cache / service worker / süti tiltott (7.6)", None),
    (r"\bfetch\s*\(", "hálózat csak az api.js-en át", {"api.js"}),
    (r"XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|\bimport\s*\(",
     "más hálózati / modulbetöltő API tiltott", None),
    (r"https?://", "külső URL tiltott (T9)", None),          # az SVG-névtér kivétel, lásd lint_js
    (r"(?i)javascript:", "javascript: séma tiltott", None),
    (r"\?\.(?![0-9])|\?\?", "ES2020-szintaxis (?. ??) tiltott — ES2019", None),
    (r"(?m)^\s*(?:import|export)\s", "ES-modul szintaxis tiltott (egyetlen inline szkript)", None),
    (r"console\.log|\bdebugger\b", "console.log / debugger nem kerülhet a buildbe", None),
    (r"setAttribute\(\s*['\"](?:style|on[a-z]+)['\"]|\.cssText|\.style\s*=",
     "style-/eseménykezelő-attribútum tiltott (CSP); használd: styles: {…} / addEventListener", None),
    (r"(?i)</script|<!--", "'</script' / '<!--' nem szerepelhet szkriptben", None),
    (r"\{\{[A-Z_]+\}\}", "sablon-helyőrző nem szerepelhet forrásban", None),
]
CSS_RULES = [
    (r"(?i)url\s*\(", "url() tiltott (nincs külső erőforrás; CSP)"),
    (r"(?i)@import", "@import tiltott"),
    (r"(?i)expression\s*\(", "CSS expression tiltott"),
    (r"https?://", "külső URL tiltott"),
    (r"(?i)</style", "'</style' nem szerepelhet"),
    (r"\{\{[A-Z_]+\}\}", "sablon-helyőrző nem szerepelhet forrásban"),
]


# ---------------------------------------------------------------- beolvasás
def read_text(path):
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise BuildError("nem olvasható: %s (%s)" % (path, e))
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BuildError("nem UTF-8: %s" % path)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


def _no_dup_pairs(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise BuildError("ismétlődő JSON-kulcs: %r" % k)
        obj[k] = v
    return obj


def read_json(path):
    try:
        return json.loads(read_text(path), object_pairs_hook=_no_dup_pairs)
    except json.JSONDecodeError as e:
        raise BuildError("érvénytelen JSON: %s (%s)" % (path, e))
    except BuildError as e:
        raise BuildError("%s: %s" % (path, e))


def rel(path, base):
    return path.relative_to(base).as_posix()


# ---------------------------------------------------------------- sorrend
def js_order(src=SRC, dev=False):
    """A JS-modulok sorrendje (relatív utak). Minden src/**.js-nek benne kell lennie."""
    order = list(CORE_HEAD)
    if dev:
        order += DEV_FIRST
        order += sorted(rel(p, src) for p in (src / "dev").glob("*.js") if rel(p, src) not in DEV_FIRST)
    order += CORE_MID
    for d in GLOB_DIRS:
        order += sorted(rel(p, src) for p in (src / d).glob("*.js"))
    order += TAIL
    for name in order:
        if not (src / name).is_file():
            raise BuildError("hiányzó modul: src/%s" % name)
    known = set(order) | set(rel(p, src) for p in (src / "dev").glob("*.js"))
    stray = sorted(rel(p, src) for p in src.rglob("*.js") if rel(p, src) not in known)
    if stray:
        raise BuildError("ismeretlen helyen lévő JS (a build nem tudja besorolni): %s — tedd a "
                         "components/, plots/, screens/ vagy dev/ mappába" % ", ".join(stray))
    return order


def css_order(src=SRC):
    order = list(CSS_HEAD) + sorted(rel(p, src) for p in (src / "css").glob("*.css") if rel(p, src) not in CSS_HEAD)
    stray = sorted(rel(p, src) for p in src.rglob("*.css") if rel(p, src) not in order)
    if stray:
        raise BuildError("CSS csak a src/css/ mappában lehet: %s" % ", ".join(stray))
    for name in order:
        if not (src / name).is_file():
            raise BuildError("hiányzó stíluslap: src/%s" % name)
    return order


# ---------------------------------------------------------------- lint
def strip_dev(text, name):
    opens, closes = text.count("/*<dev>*/"), text.count("/*</dev>*/")
    if opens != closes:
        raise BuildError("%s: kiegyensúlyozatlan /*<dev>*/ … /*</dev>*/ jelölők" % name)
    return DEV_BLOCK_RE.sub("", text)


def lint_js(name, text):
    """Szabálysértések listája egy JS-forrásra (üres lista = tiszta)."""
    out = []
    base = name.split("/")[-1]
    for pattern, why, allowed in JS_RULES:
        if allowed is not None and base in allowed and "/" not in name:
            continue
        for m in re.finditer(pattern, text):
            if pattern == r"https?://":
                if name == "dom.js" and text.startswith(SVG_NS, m.start()):
                    continue
            line = text.count("\n", 0, m.start()) + 1
            out.append("%s:%d: %s (%r)" % (name, line, why, m.group(0)))
    if "'use strict'" not in text:
        out.append("%s: hiányzik a 'use strict' (IIFE-ben)" % name)
    return out


def lint_css(name, text):
    out = []
    for pattern, why in CSS_RULES:
        for m in re.finditer(pattern, text):
            line = text.count("\n", 0, m.start()) + 1
            out.append("%s:%d: %s (%r)" % (name, line, why, m.group(0)))
    return out


# ---------------------------------------------------------------- i18n
def load_i18n(src=SRC):
    """{'hu': {...}, 'en': {...}} — alap + töredékek; azonos kulcskészlet és paraméterek."""
    base = src / "i18n"
    frag_sets = {}
    dicts = {}
    for lang in LANGS:
        merged = {}
        origin = {}
        files = [base / ("%s.json" % lang)] + sorted((base / lang).glob("*.json"))
        frag_sets[lang] = sorted(p.name for p in (base / lang).glob("*.json"))
        for f in files:
            if not f.is_file():
                raise BuildError("hiányzó szótár: %s" % f)
            data = read_json(f)
            if not isinstance(data, dict):
                raise BuildError("%s: a szótár lapos objektum legyen" % f)
            for k, v in data.items():
                if not I18N_KEY_RE.match(k) or "" in k.split("."):
                    raise BuildError("%s: érvénytelen kulcs: %r" % (f, k))
                if not isinstance(v, str):
                    raise BuildError("%s: a(z) %r értéke nem sztring" % (f, k))
                if k in merged:
                    raise BuildError("i18n: a(z) %r kulcs két fájlban is szerepel (%s, %s)" % (k, origin[k], f.name))
                if re.search(r"https?://", v):
                    raise BuildError("%s: külső URL a(z) %r szövegben" % (f, k))
                merged[k] = v
                origin[k] = f.name
        dicts[lang] = merged
    if frag_sets["hu"] != frag_sets["en"]:
        raise BuildError("i18n: a hu/ és en/ töredékfájlok eltérnek: %s vs %s" % (frag_sets["hu"], frag_sets["en"]))
    hu, en = dicts["hu"], dicts["en"]
    only_hu = sorted(set(hu) - set(en))
    only_en = sorted(set(en) - set(hu))
    if only_hu or only_en:
        raise BuildError("i18n: eltérő kulcskészlet — csak HU: %s; csak EN: %s" % (only_hu[:20], only_en[:20]))
    for k in sorted(hu):
        if set(I18N_PARAM_RE.findall(hu[k])) != set(I18N_PARAM_RE.findall(en[k])):
            raise BuildError("i18n: a(z) %r kulcs paraméterei eltérnek a két nyelvben" % k)
    return {lang: dict(sorted(dicts[lang].items())) for lang in LANGS}


# ---------------------------------------------------------------- fixture-ök (csak --dev)
def load_fixtures(fixtures=FIXTURES):
    out = {}
    if not fixtures.is_dir():
        return out
    for f in sorted(fixtures.glob("*.json")):
        data = read_json(f)
        routes = data.get("routes") if isinstance(data, dict) else None
        if not isinstance(routes, list) or not routes:
            raise BuildError("%s: 'routes' tömb kell" % f.name)
        for i, r in enumerate(routes):
            where = "%s routes[%d]" % (f.name, i)
            if not isinstance(r, dict) or r.get("method", "GET").upper() not in ("GET", "POST", "PUT"):
                raise BuildError("%s: érvénytelen method" % where)
            if not isinstance(r.get("path"), str) or not r["path"].startswith("/api/"):
                raise BuildError("%s: a path /api/… legyen" % where)
            steps = r.get("sequence") if "sequence" in r else [r]
            if not isinstance(steps, list) or not steps:
                raise BuildError("%s: üres sequence" % where)
            for s in steps:
                env = s.get("envelope") if isinstance(s, dict) else None
                if not isinstance(env, dict) or not isinstance(env.get("ok"), bool):
                    raise BuildError("%s: hiányzó vagy érvénytelen envelope" % where)
                if env["ok"] and not all(k in env for k in ("schema", "data", "warnings", "meta")):
                    raise BuildError("%s: a sikeres boríték kulcsai: ok, schema, data, warnings, meta (4.2)" % where)
                if not env["ok"] and not (isinstance(env.get("error"), dict) and
                                          all(k in env["error"] for k in ("code", "http", "message"))):
                    raise BuildError("%s: a hiba-boríték kulcsai: error{code, http, message} (4.2)" % where)
        out[f.name] = data
    return out


# ---------------------------------------------------------------- összeállítás
def json_for_script(obj):
    """JSON <script type="application/json">-be: a '<' \\u003c-ként (nem zárhatja le a taget)."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _fill(template, values):
    def sub(m):
        key = m.group(1)
        if key not in TEMPLATE_KEYS:
            raise BuildError("ismeretlen sablon-helyőrző: {{%s}}" % key)
        if key == "CSP_NONCE":
            return m.group(0)
        return values[key]
    return PLACEHOLDER_RE.sub(sub, template)


def check_template(template):
    for key in ("CSS", "JS", "DATA", "BUILD_ID"):
        if template.count("{{%s}}" % key) != 1:
            raise BuildError("a sablonban pontosan egy {{%s}} kell" % key)
    if re.search(r"<[^>]+\son[a-z]+\s*=", template, re.I):
        raise BuildError("a sablonban nem lehet inline eseménykezelő")
    if re.search(r"<[^>]+\sstyle\s*=", template, re.I):
        raise BuildError("a sablonban nem lehet style-attribútum (CSP)")
    for m in re.finditer(r"<link\b[^>]*>", template, re.I):
        if m.group(0) != '<link rel="icon" href="data:,">':
            raise BuildError("a sablonban csak a data:-favicon <link> engedett")
    if re.search(r"<script\b[^>]*\ssrc\s*=", template, re.I):
        raise BuildError("külső szkript tiltott")


def check_output(html, dev):
    """Végső ellenőrzések a kész HTML-en (assert-ek)."""
    problems = []
    # a <script>/<style> elemek (a tartalmuk átugrásával): mindegyik nyitó tag nonce-ot visz
    block_re = re.compile(r"<(script|style)\b([^>]*)>(.*?)</\1\s*>", re.S | re.I)
    for m in block_re.finditer(html):
        if 'nonce="%s"' % NONCE not in m.group(2):
            problems.append("nonce nélküli <%s%s>" % (m.group(1), m.group(2)[:60]))
    if re.search(r"<(?:script|style)\b", block_re.sub("", html), re.I):
        problems.append("lezáratlan vagy szabálytalan <script>/<style> elem")
    for m in re.finditer(r"https?://[^\s\"'<>)]*", html):
        if m.group(0) != SVG_NS:
            problems.append("külső URL a kimenetben: %s" % m.group(0)[:80])
    for pat in (r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", r"toFixed|toPrecision|Intl\.NumberFormat"):
        if re.search(pat, html):
            problems.append("tiltott API a kimenetben: %s" % pat)
    left = sorted(set(PLACEHOLDER_RE.findall(html)) - {"CSP_NONCE"})
    if left:
        problems.append("kitöltetlen helyőrző: %s" % left)
    if not dev:
        low = html.lower()
        for bad in ("fixture", "ma.dev", "/*<dev>", "__devsettransport"):
            if bad in low:
                problems.append("fejlesztői kód / fixture a termék-buildben: %r" % bad)
        size = len(html.encode("utf-8"))
        if size > MAX_BYTES:
            problems.append("a termék-build túl nagy: %d bájt > %d" % (size, MAX_BYTES))
    if problems:
        raise BuildError("a kimenet ellenőrzése sikertelen:\n  " + "\n  ".join(problems))


def build(dev=False, src=SRC, fixtures=FIXTURES):
    """A teljes HTML sztringként (determinisztikus). BuildError minden szabálysértésre."""
    src = Path(src)
    template = read_text(src / TEMPLATE)
    check_template(template)
    violations = []
    css_src = []
    for name in css_order(src):
        text = read_text(src / name)
        violations += lint_css(name, text)
        css_src.append((name, text))
    js_src = []
    for name in js_order(src, dev):
        text = read_text(src / name)
        violations += lint_js(name, text)
        stripped = strip_dev(text, name)       # a jelölők ellenőrzése mindkét módban
        js_src.append((name, text if dev else stripped))
    if violations:
        raise BuildError("lint-hibák (%d):\n  %s" % (len(violations), "\n  ".join(violations)))
    # a termék-build tömörít (a lint UTÁN, a forráson futott); a --dev változatlan forrást ad
    css_parts = ["/* --- %s --- */\n%s" % (name, text if dev else minify_css(text)) for name, text in css_src]
    js_parts = []
    for name, text in js_src:
        if not dev:
            try:
                text = minify_js(text)
            except MinifyError as e:
                raise BuildError("%s: a tömörítés sikertelen: %s" % (name, e))
        js_parts.append("/* --- %s --- */\n%s" % (name, text))
    i18n = load_i18n(src)
    data_tags = ['<script type="text/plain" id="ma-i18n" data-enc="lz1" nonce="%s">%s</script>' % (NONCE, pack_i18n(i18n))]
    if dev:
        data_tags.append('<script type="application/json" id="ma-fixtures" nonce="%s">%s</script>'
                         % (NONCE, json_for_script(load_fixtures(fixtures))))
    mode = "dev" if dev else "prod"
    digest = hashlib.sha256()
    digest.update(mode.encode())
    for part in css_parts + js_parts + data_tags:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    build_id = "%s-%s" % (mode, digest.hexdigest()[:16])
    prologue = "/* MA-munkapad %s */\nwindow.MA = { BUILD: { id: '%s', mode: '%s' } };\n" % (build_id, build_id, mode)
    html = _fill(template, {
        "CSS": "\n".join(css_parts).rstrip("\n"),
        "JS": (prologue + "\n".join(js_parts)).rstrip("\n"),
        "DATA": "\n".join(data_tags),
        "BUILD_ID": build_id,
    })
    check_output(html, dev)
    return html


def write(html, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)


def main(argv=None):
    ap = argparse.ArgumentParser(description="MA-munkapad felület-build (egy önálló HTML).")
    ap.add_argument("--dev", action="store_true", help="fejlesztői build fixture-ökkel (?fixtures=1) → dist/index.dev.html")
    ap.add_argument("--check", action="store_true", help="ellenőrzi, hogy a dist/index.html naprakész-e (nem ír)")
    ap.add_argument("--out", help="kimeneti fájl (alap: dist/index.html vagy dist/index.dev.html)")
    ap.add_argument("--list", action="store_true", help="a modulok sorrendjének kiírása")
    args = ap.parse_args(argv)
    try:
        if args.list:
            print("\n".join(css_order() + js_order(dev=args.dev)))
            return 0
        if args.check:
            html = build(dev=False)
            target = Path(args.out) if args.out else PROD_OUT
            if not target.is_file() or target.read_text(encoding="utf-8") != html:
                print("ELAVULT: %s — futtasd: python3 ma_gui/web/build_gui.py" % target, file=sys.stderr)
                return 1
            print("naprakész: %s (%d bájt)" % (target, len(html.encode("utf-8"))))
            return 0
        html = build(dev=args.dev)
        out = Path(args.out) if args.out else (DEV_OUT if args.dev else PROD_OUT)
        write(html, out)
        print("%s: %d bájt (%s)" % (out, len(html.encode("utf-8")), "dev" if args.dev else "termék"))
        return 0
    except BuildError as e:
        print("BUILD HIBA: %s" % e, file=sys.stderr)
        return 2


# ================================================================ termék-tömörítés (≤ 450 KB, 2.3)
# A termék-build a lint UTÁN tömörít; a --dev build az eredeti forrást adja (olvasható, hibakereséshez).
# Mindhárom lépés determinisztikus, csak stdlib, és veszteségmentes:
#   minify_js   — tokenszintű: megjegyzések és fölös szóközök/sortörések nélkül; a sortörés ott marad,
#                 ahol az ASI (automatikus pontosvessző) számíthat. Az idézőjeles, azonosító-alakú
#                 objektumkulcs idézőjel nélkül kerül ki ({'class': x} → {class:x}); a névtelen
#                 függvénykifejezés nyílfüggvény lesz, ahol a jelentés biztosan azonos (_js_arrowify).
#                 Nevet NEM cserél. Az egyenértékűséget a tests/gui/ui/test_minify.py ellenőrzi
#                 (espree-AST összevetés + a nyílfüggvény-feltételek: tests/gui/ui/minify_check.js).
#   minify_css  — megjegyzések és fölös szóközök nélkül (a ':' ELŐTTI szóköz marad: '.a :hover').
#   pack_i18n   — a két szótár egy fává ({"a":{"b":["hu","en"]}}), majd szöveges LZ77 („lz1”); a
#                 kicsomagolás az i18n.js-ben van (unpackI18n), a Python-oldali párja: unpack_i18n.
class MinifyError(BuildError):
    pass


_JS_PUNCT_RE = re.compile("|".join(re.escape(p) for p in sorted(
    ">>>= ... === !== **= <<= >>= >>> => == != <= >= += -= *= /= %= &= |= ^= ++ -- << >> ** && || "
    "{ } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = .".split(), key=len, reverse=True)))
_JS_NUM_RE = re.compile(r"0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_JS_IDENT_RE = re.compile(r"(?:[A-Za-z_$]|[^\x00-\x7f])(?:[\w$]|[^\x00-\x7f])*")
_JS_PLAIN_KEY_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_JS_WS = " \t\v\f\u00a0\ufeff"
_JS_NL = "\n\r\u2028\u2029"
_JS_DIGITS = "0123456789"
_JS_IDENT_CHAR = re.compile(r"[\w$\\]|[^\x00-\x7f]")
# kulcsszavak, amelyek után '/' reguláris kifejezést nyit (nem osztást)
_JS_BEFORE_EXPR = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw",
                   "case", "do", "else", "yield", "await"}
# korlátozott produkciók: utánuk a sortörés jelentést hordoz (return\nx ≠ return x)
_JS_RESTRICTED = {"return", "break", "continue", "throw", "yield", "async"}
# kulcsszavak, amelyek után utasítás nem érhet véget → a sortörés elhagyható
_JS_WORD_CONT = {"var", "let", "const", "typeof", "new", "delete", "void", "in", "instanceof", "else", "do",
                 "case", "function", "if", "for", "while", "switch", "catch", "try", "finally", "with",
                 "class", "extends"}
# írásjelek, amelyek után utasítás nem érhet véget
_JS_A_CONT = set("{ ( [ , ; : ? . ... => = += -= *= /= %= **= <<= >>= >>>= &= |= ^= == === != !== < > <= >= "
                 "+ - * / % ** << >> >>> & | ^ && || ! ~".split())
# írásjelek, amelyekkel utasítás nem kezdődhet (előttük nincs ASI; a '}' előtt mindig van)
_JS_B_CONT = set(") ] } , ; : ? . = += -= *= /= %= **= <<= >>= >>>= &= |= ^= == === != !== < > <= >= "
                 "* % ** << >> >>> & | ^ && ||".split())


def _js_tokens(text):
    """Tokenlista: (fajta, szöveg, előtte_sortörés); fajta: word, num, str, tmpl, regex, punct."""
    toks = []
    i, n = 0, len(text)
    nl = False
    braces = []            # '{' vagy 'tmpl' (sablonliterál ${…} helyettesítése)
    while i < n:
        c = text[i]
        if c in _JS_WS:
            i += 1
            continue
        if c in _JS_NL:
            nl = True
            i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] not in _JS_NL:
                i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise MinifyError("lezáratlan /* megjegyzés (%d)" % i)
            if any(ch in text[i:j] for ch in _JS_NL):
                nl = True
            i = j + 2
            continue
        if c in "'\"":
            j = i + 1
            while True:
                if j >= n or text[j] in "\n\r":                 # U+2028/2029 ES2019 óta megengedett
                    raise MinifyError("lezáratlan sztring (%d)" % i)
                if text[j] == "\\":
                    j += 2                                  # escape (a sorfolytatás \⏎ is)
                    continue
                if text[j] == c:
                    break
                j += 1
            toks.append(("str", text[i:j + 1], nl))
            i = j + 1
        elif c == "`" or (c == "}" and braces and braces[-1] == "tmpl"):
            if c == "}":
                braces.pop()
            j = i + 1
            while True:
                if j >= n:
                    raise MinifyError("lezáratlan sablonliterál (%d)" % i)
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "`":
                    j += 1
                    break
                if text.startswith("${", j):
                    j += 2
                    braces.append("tmpl")
                    break
                j += 1
            toks.append(("tmpl", text[i:j], nl))
            i = j
        elif c in _JS_DIGITS or (c == "." and i + 1 < n and text[i + 1] in _JS_DIGITS):
            m = _JS_NUM_RE.match(text, i)
            toks.append(("num", m.group(0), nl))
            i = m.end()
        elif c == "/" and _js_regex_allowed(toks):
            j = i + 1
            in_class = False
            while True:
                if j >= n or text[j] in _JS_NL:
                    raise MinifyError("lezáratlan reguláris kifejezés (%d)" % i)
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and _JS_IDENT_CHAR.match(text[j]):
                j += 1
            toks.append(("regex", text[i:j], nl))
            i = j
        else:
            m = _JS_IDENT_RE.match(text, i)
            if m:
                toks.append(("word", m.group(0), nl))
            else:
                m = _JS_PUNCT_RE.match(text, i)
                if not m:
                    raise MinifyError("ismeretlen karakter: %r (%d)" % (c, i))
                if m.group(0) == "{":
                    braces.append("{")
                elif m.group(0) == "}" and braces:
                    braces.pop()
                toks.append(("punct", m.group(0), nl))
            i = m.end()
        nl = False
    return toks


def _js_regex_allowed(toks):
    """A '/' reguláris kifejezést nyit-e (az előző jelentős token alapján)."""
    if not toks:
        return True
    kind, val, _ = toks[-1]
    if kind == "punct":
        return val not in (")", "]")
    if kind == "tmpl":
        return val.endswith("${")
    if kind == "word":
        if len(toks) > 1 and toks[-2][1] == "." and toks[-2][0] == "punct":
            return False                                    # tulajdonságnév: x.return / 2
        return val in _JS_BEFORE_EXPR
    return False


def _js_need_space(a, b):
    at, bt = a[1], b[1]
    if _JS_IDENT_CHAR.match(at[-1]) and _JS_IDENT_CHAR.match(bt[0]):
        return True                                         # két szó / szám / regex-zászló
    if a[0] == "num" and bt[0] == ".":
        return True                                         # 1 .toString()
    if at[-1] in "+-" and bt[0] == at[-1]:
        return True                                         # a + +b, a - --b
    if at[-1] == "/" and bt[0] in "/*":
        return True                                         # nem nyílhat megjegyzés
    return False


def _js_need_newline(prev2, a, b):
    """Megmaradjon-e az eredeti sortörés a és b között (ASI-biztonság)."""
    is_prop = prev2 is not None and prev2[0] == "punct" and prev2[1] == "."
    if a[0] == "word" and a[1] in _JS_RESTRICTED and not is_prop:
        return True
    if b[0] == "punct" and b[1] in ("++", "--"):
        return True
    if a[0] == "punct" and a[1] in _JS_A_CONT:
        return False
    if a[0] == "word" and a[1] in _JS_WORD_CONT and not is_prop:
        return False
    if b[0] == "punct" and b[1] in _JS_B_CONT:
        return False
    if b[0] == "word" and b[1] in ("else", "catch", "finally") and a == ("punct", "}", a[2]):
        return False
    return True


_ARROW_PREV = {"(", ",", "=", ":", "?", "[", "return"}
_ARROW_NEXT = {")", ",", ";", "]", "}", ":"}
_ARROW_BLOCKERS = {"this", "arguments", "super", "yield", "await"}
# beépített konstruktorok: ha a modulban csak ezek állnak 'new' után, egyetlen saját függvénykifejezést
# sem hívnak konstruktorként (a nyílfüggvény nem konstruálható) — különben a modul nem alakul át
_NEW_OK = {"Error", "TypeError", "RangeError", "SyntaxError", "Promise", "Date", "RegExp", "Array", "Object",
           "Map", "Set", "WeakMap", "WeakSet", "ArrayBuffer", "DataView", "Uint8Array", "Uint16Array",
           "Uint32Array", "Int32Array", "Float64Array", "URL", "URLSearchParams", "AbortController",
           "BroadcastChannel", "Blob", "File", "FileReader", "FormData", "Headers", "Request", "Response",
           "Event", "CustomEvent", "KeyboardEvent", "MouseEvent", "FocusEvent", "ClipboardEvent", "DataTransfer",
           "MutationObserver", "ResizeObserver", "IntersectionObserver", "TextEncoder", "TextDecoder", "Image"}


def _js_match(toks, i, opener, closer):
    """Az i-edik nyitó tokenhez tartozó záró indexe (sablon-helyettesítéseket is számolva)."""
    depth = 0
    for j in range(i, len(toks)):
        kind, val = toks[j][0], toks[j][1]
        if kind == "tmpl":
            if val.startswith("}"):
                depth -= 1
            if val.endswith("${"):
                depth += 1
        elif kind == "punct" and val == opener:
            depth += 1
        elif kind == "punct" and val == closer:
            depth -= 1
            if depth == 0:
                return j
    raise MinifyError("pár nélküli %r" % opener)


def _js_only_builtin_new(toks):
    """Minden 'new' operandusa beépített konstruktor (_NEW_OK, ill. window._NEW_OK)?"""
    n = len(toks)
    for k in range(n):
        if toks[k][:2] != ("word", "new") or (k > 0 and toks[k - 1][:2] == ("punct", ".")):
            continue
        chain = []
        j = k + 1
        while j < n and toks[j][0] == "word":
            chain.append(toks[j][1])
            if j + 1 < n and toks[j + 1][:2] == ("punct", "."):
                j += 2
            else:
                break
        if not ((len(chain) == 1 and chain[0] in _NEW_OK) or
                (len(chain) == 2 and chain[0] == "window" and chain[1] in _NEW_OK)):
            return False
    return True


def _js_arrowify(toks):
    """Névtelen függvénykifejezés → nyílfüggvény, ahol a jelentés biztosan azonos:
    function (a, b) { … } → (a,b)=>{…}; egyetlen egyszerű paraméternél a=>{…}.
    Feltételek: (1) a modulban 'new' után csak beépített konstruktor áll (saját függvényt senki nem hív
    konstruktorként; különben a modul változatlan); (2) kifejezés-pozíció, amelyben a nyílfüggvény is
    megállhat (_ARROW_PREV előtt, _ARROW_NEXT után — pl. nem '||' operandusa és nem '.bind(…)' tárgya);
    (3) a paraméterekben és a törzsben (beágyazva is) nincs this / arguments / super / yield / await /
    new.target. A tests/gui/ui/minify_check.js ezt AST-szinten is ellenőrzi."""
    n = len(toks)
    if not _js_only_builtin_new(toks):
        return toks
    out = []
    i = 0
    while i < n:
        tok = toks[i]
        if (tok[0] == "word" and tok[1] == "function" and i + 1 < n and toks[i + 1][:2] == ("punct", "(")
                and out and out[-1][1] in _ARROW_PREV and out[-1][0] in ("punct", "word")
                and not (out[-1][1] == "(" and len(out) > 1 and out[-2][1] == "new")):
            close_p = _js_match(toks, i + 1, "(", ")")
            if close_p + 1 < n and toks[close_p + 1][:2] == ("punct", "{"):
                close_b = _js_match(toks, close_p + 1, "{", "}")
                after = toks[close_b + 1] if close_b + 1 < n else None
                inner = toks[i + 1:close_b + 1]
                if (after is not None and after[0] == "punct" and after[1] in _ARROW_NEXT and not after[2]
                        and not any(t[0] == "word" and t[1] in _ARROW_BLOCKERS for t in inner)
                        and not any(inner[k][:2] == ("word", "new") and inner[k + 1][1] == "." for k in range(len(inner) - 1))
                        and not (out[-1][1] == "return" and tok[2])):
                    params = toks[i + 2:close_p]
                    if len(params) == 1 and params[0][0] == "word":
                        out.append(("word", params[0][1], tok[2]))
                    else:
                        out.append(("punct", "(", tok[2]))
                        out.extend(params)
                        out.append(("punct", ")", False))
                    out.append(("punct", "=>", False))
                    i = close_p + 1                      # a törzs tokenjei változatlanul jönnek
                    continue
        out.append(tok)
        i += 1
    return out


def minify_js(text):
    """Veszteségmentes JS-tömörítés (lásd fent). MinifyError, ha a forrás nem tokenizálható."""
    toks = _js_arrowify(_js_tokens(text))
    for k in range(1, len(toks) - 1):                       # {'class': x} → {class:x}
        kind, val, nl = toks[k]
        if (kind == "str" and toks[k - 1][0] == "punct" and toks[k - 1][1] in ("{", ",")
                and toks[k + 1][:2] == ("punct", ":") and _JS_PLAIN_KEY_RE.match(val[1:-1])):
            toks[k] = ("word", val[1:-1], nl)
    out = []
    prev2 = prev = None
    for tok in toks:
        if prev is not None:
            if tok[2] and _js_need_newline(prev2, prev, tok):
                out.append("\n")
            elif _js_need_space(prev, tok):
                out.append(" ")
        out.append(tok[1])
        prev2, prev = prev, tok
    return "".join(out) + "\n"


_CSS_TOKEN_RE = re.compile(r"""/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|\s+|[^\s"'/]+|/""", re.S)


def minify_css(text):
    """Megjegyzések és fölös szóközök nélküli CSS. A ':' előtti szóköz marad ('.a :hover' ≠ '.a:hover')."""
    res = []
    for m in _CSS_TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.startswith("/*") or tok[0].isspace():
            if res and res[-1] != " " and (res[-1][0] in "'\"" or res[-1][-1] not in "{};,:"):
                res.append(" ")
            continue
        if tok[0] in "'\"":
            res.append(tok)
            continue
        if res and res[-1] == " " and tok[0] in "{};,":
            res.pop()
        if tok[0] == "}" and res and res[-1][0] not in "'\"" and res[-1].endswith(";"):
            res[-1] = res[-1][:-1]                          # 'a:b;}' → 'a:b}'
            if not res[-1]:
                res.pop()
        res.append(re.sub(r";+\}", "}", tok))
    return "".join(res).strip() + "\n"


# ---- i18n-csomag („lz1”): fa + szöveges LZ77. Formátum (a kicsomagoló az i18n.js-ben):
#   • a JSON-szöveg csak BMP-karaktert tartalmaz (az asztrális karakter \\uXXXX\\uXXXX escape-ként
#     kerül bele), így a Python-index = a JS UTF-16-indexe;
#   • '~~' = egy '~' karakter; '~' + 2 jegy (eltolás-1) + 1 jegy (hossz-LZ_MIN) = visszahivatkozás
#     (a jegyek az LZ_ALPHABET-ből; átfedő másolás megengedett); minden más karakter szó szerint.
LZ_ALPHABET = "".join(chr(c) for c in range(0x21, 0x7f) if chr(c) not in "<\\\"'`~")
LZ_MIN = 5
_LZ_N = len(LZ_ALPHABET)
_LZ_WINDOW = _LZ_N * _LZ_N
_LZ_MAX = LZ_MIN + _LZ_N - 1
_LZ_DEPTH = 128


def i18n_tree(dicts):
    """{'hu': {k: v}, 'en': {k: v}} → fa: a pontokkal tagolt kulcs útvonal, a levél [hu, en]; ha egy kulcs
    egyben egy másik előtagja is, a saját szövege az alfa "" kulcsán áll."""
    tree = {}
    hu, en = dicts["hu"], dicts["en"]
    for key in sorted(hu):
        parts = key.split(".")
        node = tree
        for p in parts[:-1]:
            nxt = node.get(p)
            if isinstance(nxt, list):
                nxt = node[p] = {"": nxt}
            elif nxt is None:
                nxt = node[p] = {}
            node = nxt
        leaf = [hu[key], en[key]]
        if isinstance(node.get(parts[-1]), dict):
            node[parts[-1]][""] = leaf
        else:
            node[parts[-1]] = leaf
    return tree


def _i18n_untree(tree, prefix, out):
    for k, v in tree.items():
        key = prefix if k == "" else (prefix + "." + k if prefix else k)
        if isinstance(v, list):
            out["hu"][key], out["en"][key] = v[0], v[1]
        else:
            _i18n_untree(v, key, out)
    return out


def _bmp_json(obj):
    text = json_for_script(obj)
    return "".join(c if ord(c) < 0x10000 else
                   "\\u%04x\\u%04x" % (0xD800 + ((ord(c) - 0x10000) >> 10), 0xDC00 + ((ord(c) - 0x10000) & 0x3FF))
                   for c in text)


def lz_encode(s):
    """Determinisztikus mohó LZ77 (hash-lánc, egylépéses lusta illesztés) a fenti formátumban."""
    n = len(s)
    out = []
    chains = {}

    def find(i):
        best = off = 0
        cand = chains.get(s[i:i + 3]) if i + 3 <= n else None
        if not cand:
            return 0, 0
        lim = min(_LZ_MAX, n - i)
        for j in reversed(cand[-_LZ_DEPTH:]):
            if i - j > _LZ_WINDOW:
                break
            k = 0
            while k < lim and s[j + k] == s[i + k]:
                k += 1
            if k > best:
                best, off = k, i - j
                if k == lim:
                    break
        return best, off

    def insert(k):
        if k + 3 <= n:
            chains.setdefault(s[k:k + 3], []).append(k)

    def literal(k):
        out.append("~~" if s[k] == "~" else s[k])
        insert(k)

    i = 0
    while i < n:
        length, off = find(i)
        if length >= LZ_MIN and i + 1 < n and find(i + 1)[0] > length + 1:
            literal(i)
            i += 1
            continue
        if length >= LZ_MIN:
            o = off - 1
            out.append("~" + LZ_ALPHABET[o // _LZ_N] + LZ_ALPHABET[o % _LZ_N] + LZ_ALPHABET[length - LZ_MIN])
            for k in range(i, i + length):
                insert(k)
            i += length
        else:
            literal(i)
            i += 1
    return "".join(out)


def lz_decode(z):
    s = []
    i, n = 0, len(z)
    while i < n:
        c = z[i]
        if c != "~":
            s.append(c)
            i += 1
        elif z[i + 1] == "~":
            s.append("~")
            i += 2
        else:
            off = LZ_ALPHABET.index(z[i + 1]) * _LZ_N + LZ_ALPHABET.index(z[i + 2]) + 1
            start = len(s) - off
            for k in range(LZ_ALPHABET.index(z[i + 3]) + LZ_MIN):
                s.append(s[start + k])
            i += 4
    return "".join(s)


def pack_i18n(dicts):
    """A szótárak a <script type="text/plain" id="ma-i18n" data-enc="lz1"> tartalmaként."""
    text = lz_encode(_bmp_json(i18n_tree(dicts)))
    if "<" in text:
        raise BuildError("i18n-csomag: '<' a kimenetben")       # a json_for_script \\u003c-ként írja
    return text


def unpack_i18n(text):
    """pack_i18n inverze (tesztekhez): {'hu': {...}, 'en': {...}}."""
    return _i18n_untree(json.loads(lz_decode(text)), "", {"hu": {}, "en": {}})

if __name__ == "__main__":
    sys.exit(main())
