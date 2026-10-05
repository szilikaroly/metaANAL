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
                if not I18N_KEY_RE.match(k):
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
    css_parts = []
    for name in css_order(src):
        text = read_text(src / name)
        violations += lint_css(name, text)
        css_parts.append("/* --- %s --- */\n%s" % (name, text if dev else compact_source(text)))
    js_parts = []
    for name in js_order(src, dev):
        text = read_text(src / name)
        violations += lint_js(name, text)
        if not dev:
            text = strip_dev(text, name)
        else:
            strip_dev(text, name)      # csak a jelölők ellenőrzése
        js_parts.append("/* --- %s --- */\n%s" % (name, text if dev else compact_source(text)))
    if violations:
        raise BuildError("lint-hibák (%d):\n  %s" % (len(violations), "\n  ".join(violations)))
    i18n = load_i18n(src)
    data_tags = ['<script type="application/json" id="ma-i18n" nonce="%s">%s</script>' % (NONCE, json_for_script(i18n))]
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


# ================================================================ KIEGÉSZÍTÉS (elemzés-ágens, 2026-10-05)
# compact_source — a termék-build mérethatára (≤ 450 KB) miatt: a lint UTÁN, modulonként elhagyja a
# behúzást, az üres sorokat, a sor eleji // és a sor elején kezdődő /* … */ megjegyzéseket. Csak
# sor-alapú (nincs tokenizálás): a sorok sorrendje és tartalma egyébként változatlan, így a kimenet
# determinisztikus marad. Biztonsági feltétel: sablonliterál (`) vagy sorvégi \ (sorfolytatás) esetén a
# modul változatlan marad. A modul-fejlécek („/* --- név --- */”) megmaradnak. A --dev build nem tömörít.
def compact_source(text):
    if "`" in text or re.search(r"\\\n", text):
        return text
    out = []
    in_block = False
    for line in text.split("\n"):
        s = line.strip()
        if in_block:
            end = s.find("*/")
            if end < 0:
                continue
            in_block = False
            s = s[end + 2:].strip()
            if s:
                out.append(s)
            continue
        if not s or (s.startswith("//") and "*/" not in s):
            continue
        if s.startswith("/*"):
            end = s.find("*/", 2)
            if end < 0:
                in_block = True
                continue
            s = s[end + 2:].strip()
            if not s:
                continue
        out.append(s)
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
