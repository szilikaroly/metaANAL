#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A `metaanalizis` Claude Code-plugin generátora (terv 11. fejezet, 1. döntés: „mindkettő”).

A repó egyszerre munkakönyvtár (a `.claude/` ágensei és skillje) és Claude Code-plugin-marketplace:

  <repo>/.claude-plugin/marketplace.json                  a marketplace; a plugin forrása ./metaanalizis-asszisztens
  metaanalizis-asszisztens/.claude-plugin/plugin.json     a plugin manifesztje
  metaanalizis-asszisztens/agents/*.md                    ← .claude/agents/*.md
  metaanalizis-asszisztens/skills/<név>/…                 ← .claude/skills/<név>/…

Egyetlen igazságforrás a `.claude/` változat; a plugin-változatot ez a szkript állítja elő belőle:
  - útvonalak: `metaanalizis-asszisztens/ma.py` → `"${CLAUDE_PLUGIN_ROOT}/ma.py"` (idézve, mert a telepítési út
    szóközt tartalmazhat), más `metaanalizis-asszisztens/<x>` → `${CLAUDE_PLUGIN_ROOT}/<x>`,
    `.claude/agents|skills/` → `${CLAUDE_PLUGIN_ROOT}/agents|skills/`;
  - az ágensnevek a plugin névterébe kerülnek (`metaanalizis:ma-tervezo` …), a frontmatter `skills:` értékei is
    (`metaanalizis:metaanalizis`); a frontmatter `name:` mezője marad (a névteret a Claude Code teszi elé). A
    `metaanalizis-asszisztens` mappanév az orkesztrátor nevével azonos: a forrásban mappaként mindig perjellel
    (`metaanalizis-asszisztens/`) írd, különben ágensnévként kerül névtérbe;
  - a frontmatterből kimaradnak a plugin-ágensnél figyelmen kívül hagyott mezők (permissionMode, hooks, mcpServers,
    initialPrompt); a YAML-ban kétértelmű értékek (pl. „): ” van bennük) idézőjelbe kerülnek;
  - minden Markdown-fájl a frontmatter után „GENERÁLT FÁJL” fejlécet kap; a skill egy rövid plugin-megjegyzést is;
  - a két manifeszt verziója a `metaelemzes.__version__`.

Plugin-szintű settings.json szándékosan nincs: a plugin abból csak az `agent` és a `subagentStatusLine` kulcsot
olvassa, engedélyt nem adhat (a javasolt engedélylista a TELEPITES.md-ben van).

Futtatás (bármely mappából):
  python metaanalizis-asszisztens/tools/build_plugin.py           előállítás / frissítés
  python metaanalizis-asszisztens/tools/build_plugin.py --check   kilépési kód 1, ha a commitolt fájlok eltérnek
Kilépési kódok: 0 rendben · 1 eltérés (--check) · 2 hibás bemenet.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(HERE))

PLUGIN_DIRNAME = "metaanalizis-asszisztens"
PLUGIN_NAME = "metaanalizis"
MARKETPLACE_NAME = "metaanal"
AUTHOR = "Szili Károly"
REPOSITORY = "https://github.com/szilikaroly/metaANAL"
DISPLAY_NAME = "Metaanalízis-asszisztens"
DESCRIPTION = ("Szisztematikus áttekintés és metaanalízis Claude Code-ban: orkesztrátor és négy alágens (tervező, "
               "ellenőrző, értékelő, Metaheadhunter — meglévő metaanalízisek bányászata), metafor-ral validált "
               "számítási motor (csak Python standard könyvtár), SQL-tudásbázis, projektnapló szakaszkapukkal és "
               "helyi grafikus munkapad.")
MARKETPLACE_DESCRIPTION = ("Szili Károly Claude Code-pluginjai: szisztematikus áttekintés és metaanalízis "
                           "(metaanalizis plugin).")
KEYWORDS = ["meta-analysis", "systematic-review", "metaanalizis", "prisma", "grade", "metafor",
            "evidence-synthesis", "hungarian"]
CATEGORY = "research"

IGNORED_AGENT_KEYS = ("permissionMode", "hooks", "mcpServers", "initialPrompt")
GENERATED_MARK = "GENERÁLT FÁJL"
REGEN_CMD = "python %s/tools/build_plugin.py" % PLUGIN_DIRNAME
# a fejléc a forrást a repó gyökeréhez, a generátort a plugin mappájához képest adja meg (az ellenőrzés így nem
# talál benne át nem írt `metaanalizis-asszisztens/` utat)
HEADER = ("<!-- %s — ne szerkeszd kézzel. Forrás a repóban: %%s; újragenerálás: python tools/build_plugin.py "
          "(a --check jelzi az eltérést). -->" % GENERATED_MARK)

# fájlonkénti szó szerinti cserék: (régi, új) — a régi szövegnek pontosan egyszer szerepelnie kell a forrásban
REPLACEMENTS = {
    ".claude/skills/metaanalizis/SKILL.md": [
        ("## Erőforrások (a repó gyökeréből)", "## Erőforrások (a plugin telepítési mappájából)"),
    ],
}


class BuildError(RuntimeError):
    pass


def _read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def engine_version(repo_root):
    path = os.path.join(repo_root, PLUGIN_DIRNAME, "metaelemzes", "__init__.py")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', _read_text(path), re.M)
    if not m:
        raise BuildError("nem található a __version__: %s" % path)
    return m.group(1)


def _src_dirs(repo_root):
    return os.path.join(repo_root, ".claude", "agents"), os.path.join(repo_root, ".claude", "skills")


def agent_names(repo_root):
    agents_dir = _src_dirs(repo_root)[0]
    if not os.path.isdir(agents_dir):
        raise BuildError("hiányzik az ágensek forrásmappája: %s" % agents_dir)
    return sorted(f[:-3] for f in os.listdir(agents_dir) if f.endswith(".md"))


def skill_names(repo_root):
    skills_dir = _src_dirs(repo_root)[1]
    if not os.path.isdir(skills_dir):
        raise BuildError("hiányzik a skillek forrásmappája: %s" % skills_dir)
    return sorted(d for d in os.listdir(skills_dir) if os.path.isfile(os.path.join(skills_dir, d, "SKILL.md")))


# --------------------------------------------------------------------------- átalakítások

_PKG = re.escape(PLUGIN_DIRNAME)
_MA_PY_RE = re.compile(r"(?<![\w/.\-])%s/ma\.py(?![\w/\-])" % _PKG)
_PKG_PATH_RE = re.compile(r"(?<![\w/.\-])%s/" % _PKG)
_CLAUDE_DIR_RE = re.compile(r"(?<![\w/.\-])\.claude/(agents|skills)/")


def _names_re(names):
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # önálló név: előtte nem szókarakter / „:” / út; utána nem szókarakter, út, „-”, „:” vagy kiterjesztés (.md)
    return re.compile(r"(?<![\w:/.\-])(%s)(?![\w/:\-]|\.\w)" % alt)


def transform_text(text, agents):
    """A Markdown-törzs (és a leírások) plugin-változata: útvonalak és névtérbe tett ágensnevek."""
    text = _MA_PY_RE.sub('"${CLAUDE_PLUGIN_ROOT}/ma.py"', text)
    text = _PKG_PATH_RE.sub("${CLAUDE_PLUGIN_ROOT}/", text)
    text = _CLAUDE_DIR_RE.sub(r"${CLAUDE_PLUGIN_ROOT}/\1/", text)
    if agents:
        text = _names_re(agents).sub(lambda m: "%s:%s" % (PLUGIN_NAME, m.group(1)), text)
    return text


_PLAIN_BAD_START = tuple("-?:,[]{}#&*!|>'\"%@`")


def yaml_scalar(value):
    """Egysoros YAML-skalár: sima, ha egyértelmű, különben JSON-stílusú (YAML-ban is érvényes) idézett alak."""
    if (value and value == value.strip() and not value.startswith(_PLAIN_BAD_START) and ": " not in value
            and " #" not in value and not value.endswith(":")):
        return value
    return json.dumps(value, ensure_ascii=False)


def _unquote(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            raise BuildError("nem értelmezhető idézett frontmatter-érték: %s" % raw)
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    return raw


def split_frontmatter(text, src):
    if not text.startswith("---\n"):
        raise BuildError("a fájl nem frontmatterrel kezdődik: %s" % src)
    end = text.find("\n---\n", 3)
    if end < 0:
        raise BuildError("lezáratlan frontmatter: %s" % src)
    return text[4:end].split("\n"), text[end + 5:]


def _fm_entries(lines, src):
    """[(kulcs, első sor értéke, folytatósorok)] — a felső szintű `kulcs: érték` sorok és a hozzájuk tartozó blokk."""
    out = []
    for ln in lines:
        if ln[:1] in (" ", "\t", "-") or not ln.strip():
            if not out:
                raise BuildError("értelmezhetetlen frontmatter-sor: %r (%s)" % (ln, src))
            out[-1][2].append(ln)
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s(.*))?$", ln)
        if not m:
            raise BuildError("értelmezhetetlen frontmatter-sor: %r (%s)" % (ln, src))
        out.append((m.group(1), m.group(2) or "", []))
    return out


_BLOCK_INDICATORS = ("|", ">", "|-", ">-", "|+", ">+")


def _namespace_skill(item, skills):
    item = item.strip()
    return "%s:%s" % (PLUGIN_NAME, item) if item in skills and ":" not in item else item


def transform_frontmatter(lines, agents, skills, src):
    out = []
    for key, value, cont in _fm_entries(lines, src):
        if key in IGNORED_AGENT_KEYS:
            continue
        if key == "name":
            name = _unquote(value)
            if ":" in name:
                raise BuildError("a name mezőben nem lehet kettőspont: %s (%s)" % (name, src))
            out.append("name: %s" % name)
        elif key == "skills":
            raw = value.strip()
            if raw.startswith("[") and raw.endswith("]"):
                items = [_namespace_skill(_unquote(x), skills) for x in raw[1:-1].split(",") if x.strip()]
                out.append("skills: [%s]" % ", ".join(yaml_scalar(x) for x in items))
            elif raw:
                items = [_namespace_skill(x, skills) for x in _unquote(value).split(",") if x.strip()]
                out.append("skills: %s" % yaml_scalar(", ".join(items)))
            else:
                out.append("skills:")
                for ln in cont:
                    m = re.match(r"^(\s*-\s*)(.*)$", ln)
                    out.append(m.group(1) + _namespace_skill(_unquote(m.group(2)), skills) if m else ln)
            continue
        elif key == "description":
            if value.strip() in _BLOCK_INDICATORS or not value.strip():
                out.append(("description: %s" % value.strip()).rstrip())
            else:
                out.append("description: %s" % yaml_scalar(transform_text(_unquote(value), agents)))
            out.extend(transform_text(ln, agents) for ln in cont)
            continue
        else:
            out.append("%s: %s" % (key, value) if value else "%s:" % key)
        out.extend(cont)
    return out


def _plugin_note(agents):
    names = ", ".join("`%s:%s`" % (PLUGIN_NAME, a) for a in agents)
    return ("> **Plugin-telepítés.** Ez a skill a `%s` Claude Code-plugin része. A motor, a sablonok és a leírások helye "
            "`${CLAUDE_PLUGIN_ROOT}` (frissítéskor cserélődik, ezért oda ne írj); a tudásbázis-adatbázist a motor a "
            "plugin adatmappájában (`${CLAUDE_PLUGIN_DATA}`) tartja, így frissítés után is megmarad. A projektmappákat "
            "a felhasználó munkakönyvtárában hozd létre. Az ágensek: %s.\n" % (PLUGIN_NAME, names))


def transform_markdown(text, rel_src, agents, skills, is_skill=False):
    for old, new in REPLACEMENTS.get(rel_src, ()):
        if text.count(old) != 1:
            raise BuildError("a csere szövege nem pontosan egyszer szerepel (%s): %r — frissítsd a REPLACEMENTS "
                             "táblát" % (rel_src, old))
        text = text.replace(old, new)
    if text.startswith("---\n"):
        fm, body = split_frontmatter(text, rel_src)
        head = "---\n%s\n---\n" % "\n".join(transform_frontmatter(fm, agents, skills, rel_src))
    else:
        head, body = "", text
    body = transform_text(body, agents)
    if is_skill:
        m = re.search(r"^# .*\n", body, re.M)
        if not m:
            raise BuildError("a skillben nincs első szintű címsor: %s" % rel_src)
        body = body[:m.end()] + "\n" + _plugin_note(agents) + body[m.end():]
    return head + HEADER % rel_src + "\n" + body


# --------------------------------------------------------------------------- manifesztek

def _json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def plugin_manifest(version):
    return {
        "name": PLUGIN_NAME,
        "displayName": DISPLAY_NAME,
        "version": version,
        "description": DESCRIPTION,
        "author": {"name": AUTHOR},
        "repository": REPOSITORY,
        "keywords": KEYWORDS,
    }


def marketplace_manifest(version):
    return {
        "name": MARKETPLACE_NAME,
        "description": MARKETPLACE_DESCRIPTION,
        "owner": {"name": AUTHOR},
        "plugins": [{
            "name": PLUGIN_NAME,
            "source": "./" + PLUGIN_DIRNAME,
            "description": DESCRIPTION,
            "version": version,
            "author": {"name": AUTHOR},
            "category": CATEGORY,
        }],
    }


# --------------------------------------------------------------------------- előállítás és összevetés

GENERATED_TREES = ("agents", "skills")


def generate(repo_root=DEFAULT_REPO_ROOT):
    """{a repó gyökeréhez relatív, perjeles út: bájtok} — minden generált fájl."""
    agents, skills = agent_names(repo_root), skill_names(repo_root)
    agents_dir, skills_dir = _src_dirs(repo_root)
    out = {}
    for name in agents:
        rel_src = ".claude/agents/%s.md" % name
        text = _read_text(os.path.join(agents_dir, name + ".md"))
        out["%s/agents/%s.md" % (PLUGIN_DIRNAME, name)] = transform_markdown(text, rel_src, agents, skills)
    for skill in skills:
        base = os.path.join(skills_dir, skill)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), base).replace(os.sep, "/")
                rel_src = ".claude/skills/%s/%s" % (skill, rel)
                dst = "%s/skills/%s/%s" % (PLUGIN_DIRNAME, skill, rel)
                if fn.endswith(".md"):
                    out[dst] = transform_markdown(_read_text(os.path.join(dirpath, fn)), rel_src, agents, skills,
                                                  is_skill=(rel == "SKILL.md"))
                else:
                    out[dst] = _read_bytes(os.path.join(dirpath, fn))
    version = engine_version(repo_root)
    out["%s/.claude-plugin/plugin.json" % PLUGIN_DIRNAME] = _json(plugin_manifest(version))
    out[".claude-plugin/marketplace.json"] = _json(marketplace_manifest(version))
    for rel in list(out):
        if isinstance(out[rel], str):
            out[rel] = out[rel].encode("utf-8")
    _sanity(out)
    return out


def _sanity(files):
    for rel, data in files.items():
        if not rel.endswith(".md"):
            continue
        text = data.decode("utf-8")
        left = re.findall(r"(?<![\w/.\-])%s/\S*" % _PKG, text)
        if left:
            raise BuildError("át nem írt útvonal maradt (%s): %s" % (rel, ", ".join(left[:3])))
        bad = [v for v in re.findall(r'subagent_type\s*=\s*"([^"]+)"', text) if not v.startswith(PLUGIN_NAME + ":")]
        if bad:
            raise BuildError("névtér nélküli subagent_type (%s): %s" % (rel, ", ".join(bad)))


def stale_files(repo_root, files):
    """A generált fák (agents/, skills/) azon fájljai, amelyeket a generátor már nem állít elő."""
    out = []
    for tree in GENERATED_TREES:
        top = os.path.join(repo_root, PLUGIN_DIRNAME, tree)
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), repo_root).replace(os.sep, "/")
                if rel not in files:
                    out.append(rel)
    return sorted(out)


def _norm(data):
    return data.replace(b"\r\n", b"\n")


def check(repo_root=DEFAULT_REPO_ROOT):
    """[(állapot, út)] — állapot: ELTÉR | HIÁNYZIK | FÖLÖSLEGES; üres lista, ha minden naprakész."""
    files = generate(repo_root)
    problems = []
    for rel, data in sorted(files.items()):
        path = os.path.join(repo_root, *rel.split("/"))
        if not os.path.isfile(path):
            problems.append(("HIÁNYZIK", rel))
        elif _norm(_read_bytes(path)) != _norm(data):
            problems.append(("ELTÉR", rel))
    problems.extend(("FÖLÖSLEGES", rel) for rel in stale_files(repo_root, files))
    return problems


def build(repo_root=DEFAULT_REPO_ROOT):
    """Előállítja / frissíti a plugin-fájlokat; [(művelet, út)] a változásokról."""
    files = generate(repo_root)
    changes = []
    for rel, data in sorted(files.items()):
        path = os.path.join(repo_root, *rel.split("/"))
        if os.path.isfile(path) and _norm(_read_bytes(path)) == _norm(data):
            continue
        changes.append(("írva" if os.path.isfile(path) else "új", rel))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    for rel in stale_files(repo_root, files):
        os.remove(os.path.join(repo_root, *rel.split("/")))
        changes.append(("törölve", rel))
    for tree in GENERATED_TREES:
        top = os.path.join(repo_root, PLUGIN_DIRNAME, tree)
        for dirpath, _dirnames, _files in sorted(os.walk(top, topdown=False), reverse=True):
            if dirpath != top and not os.listdir(dirpath):
                os.rmdir(dirpath)
    return changes


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="A metaanalizis Claude Code-plugin fájljainak előállítása a .claude/ "
                                             "változatból.")
    ap.add_argument("--check", action="store_true",
                    help="csak ellenőrzés: kilépési kód 1, ha a generált fájlok eltérnek a mostaniaktól")
    ap.add_argument("--repo-root", default=DEFAULT_REPO_ROOT, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        if a.check:
            problems = check(a.repo_root)
            for status, rel in problems:
                print("%s: %s" % (status, rel))
            if problems:
                print("A plugin-fájlok elavultak — futtasd: %s" % REGEN_CMD, file=sys.stderr)
                return 1
            print("A plugin-fájlok naprakészek.")
            return 0
        changes = build(a.repo_root)
        for what, rel in changes:
            print("%s: %s" % (what, rel))
        print("Kész: %d változás." % len(changes))
        return 0
    except (BuildError, OSError) as exc:
        print("HIBA: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
