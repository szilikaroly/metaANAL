# -*- coding: utf-8 -*-
"""Stdlib JSON-Schema-részhalmaz validátor (2020-12 szerinti jelentéssel) a munkapad kérés- és
válaszsémáihoz és a szk.* szerződésekhez (terv 3.1, 4.0).

Támogatott kulcsszavak: type (lista is; az integer és a number nem fogad el bool-t, az 1.0 egész),
required, enum, const (JSON-egyenlőséggel: true ≠ 1), pattern (re.search; ECMA-közeli: ASCII
\\d/\\w, a '$' csak a szöveg legvégén illeszkedik), properties, additionalProperties (bool vagy
séma), items, oneOf (pontosan egy ág), $ref (helyi '#', '#/$defs/…' és regisztrált $id, pl.
'urn:szk:contract:common:1#/$defs/sha256'), minimum/maximum, exclusiveMinimum/exclusiveMaximum,
minLength/maxLength (kódpontban), minItems/maxItems, valamint a true/false séma.

A csak annotációs kulcsszavakat (title, description, examples, default, format, $comment, $defs …)
figyelmen kívül hagyja; minden más kulcsszóra SchemaError-t dob, hogy egy nem támogatott megkötés
ne menjen át csendben. A hibaüzenet JSON-pointer útvonalat és a séma elvárását tartalmazza, a
vizsgált értéket soha (T10).
"""
import json
import re
from collections import namedtuple
from pathlib import Path
from urllib.parse import unquote

SUPPORTED_KEYWORDS = frozenset([
    "type", "required", "enum", "const", "pattern", "properties", "additionalProperties", "items",
    "oneOf", "$ref", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
    "maxLength", "minItems", "maxItems",
])
ANNOTATION_KEYWORDS = frozenset([
    "$schema", "$id", "$comment", "$defs", "definitions", "title", "description", "default",
    "examples", "format", "deprecated", "readOnly", "writeOnly", "contentMediaType", "contentEncoding",
])
TYPE_NAMES = ("null", "boolean", "object", "array", "number", "integer", "string")
MAX_ERRORS = 500
MAX_DEPTH = 100

_NUMERIC_KW = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
_COUNT_KW = ("minLength", "maxLength", "minItems", "maxItems")
_INF = float("inf")


class SchemaError(ValueError):
    """Hibás vagy nem támogatott séma (nem a vizsgált adat hibája)."""


class ValidationError(namedtuple("ValidationError", "path keyword message")):
    """Egy validálási hiba: JSON-pointer (gyökér = ''), kulcsszó, magyar üzenet."""
    __slots__ = ()

    def __str__(self):
        return "%s: %s" % (self.path or "/", self.message)


class _Stop(Exception):
    pass


# ---------------------------------------------------------------------------
# segédek
# ---------------------------------------------------------------------------

def _escape(part):
    return str(part).replace("~", "~0").replace("/", "~1")


def json_pointer(parts):
    """Útvonal-szakaszok → RFC 6901 JSON-pointer (a gyökér '')."""
    return "".join("/" + _escape(p) for p in parts)


def resolve_pointer(doc, pointer):
    """JSON-pointer feloldása a dokumentumban; hiányzó célra SchemaError."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise SchemaError("érvénytelen JSON-pointer: %s" % pointer)
    node = doc
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and part.isascii() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise SchemaError("a $ref célja nem található: %s" % pointer)
    return node


def _norm_id(value):
    if not isinstance(value, str):
        return None
    return value[:-1] if value.endswith("#") else value


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _finite(x):
    return not isinstance(x, float) or not (x != x or x in (_INF, -_INF))


def _is_type(inst, name):
    if name == "null":
        return inst is None
    if name == "boolean":
        return isinstance(inst, bool)
    if name == "string":
        return isinstance(inst, str)
    if name == "object":
        return isinstance(inst, dict)
    if name == "array":
        return isinstance(inst, (list, tuple))
    if name == "number":
        return _is_number(inst) and _finite(inst)
    if name == "integer":
        if isinstance(inst, bool):
            return False
        if isinstance(inst, int):
            return True
        return isinstance(inst, float) and _finite(inst) and inst.is_integer()
    return False


def _type_name(inst):
    if inst is None:
        return "null"
    if isinstance(inst, bool):
        return "boolean"
    if isinstance(inst, int):
        return "integer"
    if isinstance(inst, float):
        return "number" if _finite(inst) else "nem véges szám (NaN/Infinity)"
    if isinstance(inst, str):
        return "string"
    if isinstance(inst, (list, tuple)):
        return "array"
    if isinstance(inst, dict):
        return "object"
    return "nem JSON-típus (%s)" % type(inst).__name__


def json_equal(a, b):
    """JSON-egyenlőség: a bool nem szám (true ≠ 1), az 1 és az 1.0 egyenlő."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if a is None or b is None:
        return a is None and b is None
    if _is_number(a) and _is_number(b):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return False


def _show(value, limit=60):
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ECMA-262 → Python: a '$' (karakterosztályon kívül) csak a szöveg végén illeszkedjen, mert a
# Python '$'-ja a záró soremelés előtt is illeszkedik ("…\n" különben átmenne).
def _ecma_to_python(pattern):
    out = []
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "$":
            out.append(r"\Z")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


_PATTERN_CACHE = {}


def _compile(pattern):
    rx = _PATTERN_CACHE.get(pattern)
    if rx is None:
        try:
            rx = re.compile(_ecma_to_python(pattern), re.ASCII)
        except re.error:
            raise SchemaError("érvénytelen reguláris kifejezés a sémában: %s" % pattern) from None
        if len(_PATTERN_CACHE) > 512:
            _PATTERN_CACHE.clear()
        _PATTERN_CACHE[pattern] = rx
    return rx


def _keyword_problem(key, value):
    """A kulcsszó értékének formai hibája (None = rendben)."""
    if key == "type":
        names = [value] if isinstance(value, str) else value
        if not isinstance(names, list) or not names or not all(isinstance(t, str) and t in TYPE_NAMES
                                                                for t in names):
            return "a 'type' értéke ismert típusnév vagy azok nem üres listája lehet"
    elif key == "required":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            return "a 'required' szövegek listája lehet"
    elif key == "enum":
        if not isinstance(value, list) or not value:
            return "az 'enum' nem üres lista lehet"
    elif key == "pattern":
        if not isinstance(value, str):
            return "a 'pattern' szöveg lehet"
        try:
            _compile(value)
        except SchemaError as e:
            return str(e)
    elif key == "properties":
        if not isinstance(value, dict):
            return "a 'properties' objektum lehet"
        for name, sub in value.items():
            if not isinstance(sub, (dict, bool)):
                return "a 'properties/%s' nem séma" % name
    elif key in ("additionalProperties", "items"):
        if isinstance(value, list):
            return "a '%s' tömb alakja (tuple-validálás) nem támogatott" % key
        if not isinstance(value, (dict, bool)):
            return "a '%s' séma vagy logikai érték lehet" % key
    elif key == "oneOf":
        if not isinstance(value, list) or not value or not all(isinstance(s, (dict, bool)) for s in value):
            return "a 'oneOf' sémák nem üres listája lehet"
    elif key == "$ref":
        if not isinstance(value, str):
            return "a '$ref' szöveg lehet"
    elif key in _NUMERIC_KW:
        if not _is_number(value) or not _finite(value):
            return "a '%s' véges szám lehet" % key
    elif key in _COUNT_KW:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "a '%s' nemnegatív egész lehet" % key
    return None


# ---------------------------------------------------------------------------
# $ref-feloldás és regisztráció
# ---------------------------------------------------------------------------

def _resolve_ref(registry, ref, root):
    base, _, frag = ref.partition("#")
    if base:
        doc = registry.get(base)
        if doc is None and isinstance(root, dict) and _norm_id(root.get("$id")) == base:
            doc = root
        if doc is None:
            raise SchemaError("ismeretlen $ref-cél: %s" % ref)
    else:
        doc = root
    if not frag:
        return doc, doc
    frag = unquote(frag)
    if not frag.startswith("/"):
        raise SchemaError("csak JSON-pointer fragmentum támogatott: %s" % ref)
    return resolve_pointer(doc, frag), doc


def register(registry, schema):
    """Séma felvétele a regiszterbe a ``$id``-je alapján (a záró '#' nélkül) → az id."""
    if not isinstance(schema, dict):
        raise SchemaError("csak objektum-séma regisztrálható")
    sid = _norm_id(schema.get("$id"))
    if not sid:
        raise SchemaError("a regisztrálandó sémának nincs $id-je")
    existing = registry.get(sid)
    if existing is not None and existing is not schema and not json_equal(existing, schema):
        raise SchemaError("eltérő séma ugyanazzal az $id-vel: %s" % sid)
    registry[sid] = schema
    return sid


def build_registry(schemas):
    """Sémák (lista vagy {bármi: séma} dict) → {$id: séma} regiszter."""
    registry = {}
    items = schemas.values() if isinstance(schemas, dict) else schemas
    for schema in items:
        register(registry, schema)
    return registry


def load_schema_dir(directory, registry=None, pattern="*.schema.json"):
    """Egy mappa ``*.schema.json`` fájljainak beolvasása és regisztrálása (névsorrendben)."""
    registry = {} if registry is None else registry
    for path in sorted(Path(directory).glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            register(registry, json.load(fh))
    return registry


# ---------------------------------------------------------------------------
# validálás
# ---------------------------------------------------------------------------

class _Run:
    def __init__(self, registry, limit, active_refs=None, checked=None):
        self.registry = registry
        self.limit = limit
        self.errors = []
        self.active_refs = set() if active_refs is None else active_refs
        self.checked = set() if checked is None else checked

    def sub(self, limit):
        return _Run(self.registry, limit, self.active_refs, self.checked)

    def add(self, path, keyword, message):
        self.errors.append(ValidationError(json_pointer(path), keyword, message))
        if self.limit is not None and len(self.errors) >= self.limit:
            raise _Stop()


def _check_keywords(run, schema):
    if id(schema) in run.checked:
        return
    unknown = sorted(k for k in schema if k not in SUPPORTED_KEYWORDS and k not in ANNOTATION_KEYWORDS)
    if unknown:
        raise SchemaError("nem támogatott séma-kulcsszó: %s" % ", ".join(unknown))
    for key in schema:
        if key in SUPPORTED_KEYWORDS:
            problem = _keyword_problem(key, schema[key])
            if problem:
                raise SchemaError(problem)
    run.checked.add(id(schema))


def _branch_valid(run, inst, schema, path, root, depth):
    sub = run.sub(1)
    try:
        _validate(sub, inst, schema, path, root, depth)
    except _Stop:
        return False
    return not sub.errors


def _validate(run, inst, schema, path, root, depth):
    if schema is True:
        return
    if schema is False:
        run.add(path, "false", "itt semmilyen érték nem engedett")
        return
    if not isinstance(schema, dict):
        raise SchemaError("a séma csak objektum vagy logikai érték lehet")
    if depth > MAX_DEPTH:
        run.add(path, "depth", "túl mély szerkezet (legfeljebb %d szint)" % MAX_DEPTH)
        return
    _check_keywords(run, schema)

    ref = schema.get("$ref")
    if ref is not None:
        target, target_root = _resolve_ref(run.registry, ref, root)
        key = (id(target), path)
        if key in run.active_refs:
            raise SchemaError("körkörös $ref: %s" % ref)
        run.active_refs.add(key)
        try:
            _validate(run, inst, target, path, target_root, depth + 1)
        finally:
            run.active_refs.discard(key)

    if "type" in schema:
        t = schema["type"]
        names = [t] if isinstance(t, str) else t
        if not any(_is_type(inst, name) for name in names):
            run.add(path, "type", "típushiba: várt %s, kapott %s" % (" vagy ".join(names), _type_name(inst)))

    if "enum" in schema:
        allowed = schema["enum"]
        if not any(json_equal(inst, v) for v in allowed):
            shown = ", ".join(_show(v, 40) for v in allowed[:10]) + (", …" if len(allowed) > 10 else "")
            run.add(path, "enum", "nem engedett érték; engedett: %s" % shown)

    if "const" in schema and not json_equal(inst, schema["const"]):
        run.add(path, "const", "nem egyezik a rögzített értékkel (%s)" % _show(schema["const"]))

    if isinstance(inst, str):
        if "pattern" in schema and not _compile(schema["pattern"]).search(inst):
            run.add(path, "pattern", "nem illeszkedik a mintára: %s" % schema["pattern"])
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            run.add(path, "maxLength", "hosszabb, mint %d karakter" % schema["maxLength"])
        if "minLength" in schema and len(inst) < schema["minLength"]:
            run.add(path, "minLength", "rövidebb, mint %d karakter" % schema["minLength"])

    if _is_number(inst) and _finite(inst):
        if "minimum" in schema and inst < schema["minimum"]:
            run.add(path, "minimum", "kisebb a megengedett legkisebb értéknél (%s)" % _show(schema["minimum"]))
        if "maximum" in schema and inst > schema["maximum"]:
            run.add(path, "maximum", "nagyobb a megengedett legnagyobb értéknél (%s)" % _show(schema["maximum"]))
        if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
            run.add(path, "exclusiveMinimum", "nem nagyobb, mint %s" % _show(schema["exclusiveMinimum"]))
        if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
            run.add(path, "exclusiveMaximum", "nem kisebb, mint %s" % _show(schema["exclusiveMaximum"]))

    if isinstance(inst, dict):
        for name in schema.get("required", ()):
            if name not in inst:
                run.add(path, "required", "hiányzó kötelező mező: %s" % name)
        props = schema.get("properties") or {}
        for name, sub in props.items():
            if name in inst:
                _validate(run, inst[name], sub, path + (name,), root, depth + 1)
        if "additionalProperties" in schema:
            extra = schema["additionalProperties"]
            if extra is not True:
                for name in inst:
                    if name in props:
                        continue
                    if extra is False:
                        run.add(path + (name,), "additionalProperties", "nem engedett mező")
                    else:
                        _validate(run, inst[name], extra, path + (name,), root, depth + 1)

    if isinstance(inst, (list, tuple)):
        if "items" in schema:
            items = schema["items"]
            for i, item in enumerate(inst):
                _validate(run, item, items, path + (i,), root, depth + 1)
        if "minItems" in schema and len(inst) < schema["minItems"]:
            run.add(path, "minItems", "kevesebb, mint %d elem" % schema["minItems"])
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            run.add(path, "maxItems", "több, mint %d elem" % schema["maxItems"])

    if "oneOf" in schema:
        branches = schema["oneOf"]
        matched = [i for i, sub in enumerate(branches) if _branch_valid(run, inst, sub, path, root, depth + 1)]
        if not matched:
            run.add(path, "oneOf", "egyik oneOf-ág sem illeszkedik (%d ág)" % len(branches))
            _explain_closest(run, inst, branches, path, root, depth + 1)
        elif len(matched) > 1:
            run.add(path, "oneOf", "több oneOf-ág is illeszkedik (%s); pontosan egy kellene"
                    % ", ".join("%d." % i for i in matched))


def _explain_closest(run, inst, branches, path, root, depth):
    """Ha egyértelműen egy ág áll a legközelebb (legkevesebb hiba), annak hibáit is közli."""
    counts = []
    for i, sub in enumerate(branches):
        probe = run.sub(20)
        try:
            _validate(probe, inst, sub, path, root, depth)
        except _Stop:
            pass
        counts.append((len(probe.errors), i, probe.errors))
    counts.sort(key=lambda c: (c[0], c[1]))
    if len(counts) > 1 and counts[0][0] == counts[1][0]:
        return
    _, idx, errs = counts[0]
    for e in errs[:5]:
        run.add(tuple(_split_pointer(e.path)), e.keyword, "[oneOf %d. ág] %s" % (idx, e.message))


def _split_pointer(pointer):
    if not pointer:
        return []
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]


def validate_detailed(instance, schema, registry=None, limit=MAX_ERRORS):
    """Validálás → ValidationError-lista (üres = érvényes). ``limit`` után egy záró
    'limit' bejegyzés jelzi, hogy további hibák elmaradtak. Hibás sémára SchemaError."""
    run = _Run(registry or {}, limit)
    try:
        _validate(run, instance, schema, (), schema, 0)
    except _Stop:
        run.errors.append(ValidationError("", "limit", "további hibák elhagyva (legfeljebb %d)" % limit))
    return run.errors


def validate(instance, schema, registry=None, limit=MAX_ERRORS):
    """Validálás → hibaszövegek listája ('<json-pointer>: <üzenet>'; üres = érvényes)."""
    return [str(e) for e in validate_detailed(instance, schema, registry, limit)]


def is_valid(instance, schema, registry=None):
    run = _Run(registry or {}, 1)
    try:
        _validate(run, instance, schema, (), schema, 0)
    except _Stop:
        return False
    return not run.errors


def check_schema(schema, registry=None):
    """A séma statikus ellenőrzése (kulcsszavak, értékformák, minták, $ref-célok, $defs is)
    → hibaszövegek listája ('#/<séma-pointer>: <üzenet>'; üres = rendben)."""
    registry = registry or {}
    problems = []
    seen = set()

    def walk(node, ptr, root):
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            problems.append("%s: a séma csak objektum vagy logikai érték lehet" % ptr)
            return
        if id(node) in seen:
            return
        seen.add(id(node))
        for key, value in node.items():
            here = "%s/%s" % (ptr, _escape(key))
            if key in ("$defs", "definitions"):
                if not isinstance(value, dict):
                    problems.append("%s: objektum lehet" % here)
                    continue
                for name, sub in value.items():
                    walk(sub, "%s/%s" % (here, _escape(name)), root)
                continue
            if key in ANNOTATION_KEYWORDS:
                continue
            if key not in SUPPORTED_KEYWORDS:
                problems.append("%s: nem támogatott séma-kulcsszó" % here)
                continue
            problem = _keyword_problem(key, value)
            if problem:
                problems.append("%s: %s" % (here, problem))
                continue
            if key == "properties":
                for name, sub in value.items():
                    walk(sub, "%s/%s" % (here, _escape(name)), root)
            elif key in ("additionalProperties", "items"):
                walk(value, here, root)
            elif key == "oneOf":
                for i, sub in enumerate(value):
                    walk(sub, "%s/%d" % (here, i), root)
            elif key == "$ref":
                try:
                    _resolve_ref(registry, value, root)
                except SchemaError as e:
                    problems.append("%s: %s" % (here, e))

    walk(schema, "#", schema)
    return problems
