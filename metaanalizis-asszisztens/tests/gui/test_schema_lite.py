# -*- coding: utf-8 -*-
"""ma_gui.schema_lite: a JSON-Schema-részhalmaz (terv 3.1) pozitív és negatív esetei, a 4.0 közös
definíciói és a 4.1 kézfogás-séma legalább 3 + 3 példával (8.3)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ma_gui import schema_lite as sl  # noqa: E402
from ma_gui.schema_lite import SchemaError, validate  # noqa: E402

# 4.0 — a terv szövegéből, változatlanul
COMMON = json.loads(r"""
{ "$id": "urn:szk:contract:common:1", "$defs": {
  "i18n":    {"type": "object", "required": ["hu","en"], "properties": {"hu": {"type": "string"}, "en": {"type": "string"}}},
  "num":     {"type": ["number","null"]},
  "relpath": {"type": "string", "pattern": "^(?!/)(?![A-Za-z]:)(?!.*\\.\\.)[^\\\\:]+$"},
  "sha256":  {"type": "string", "pattern": "^[0-9a-f]{64}$"},
  "severity":{"enum": ["error","warning","info"]},
  "kbid":    {"type": "string", "pattern": "^([VPX]\\d{3}|[A-Z][A-Z0-9_]*-[A-Z0-9-]+)$"},
  "row_uid": {"type": "string", "pattern": "^r[0-9a-z]{4,12}$"},
  "display": {"type": "object", "required": ["est","lo","hi"],
              "properties": {"est": {"$ref": "#/$defs/num"}, "lo": {"$ref": "#/$defs/num"}, "hi": {"$ref": "#/$defs/num"}}}
}}
""")

# 4.1 — a terv szövegéből, változatlanul
CAPABILITIES = json.loads(r"""
{ "$id": "urn:szk:contract:capabilities:1", "type": "object",
  "required": ["schema","plugin","version","ok","contracts","commands"],
  "properties": {
    "schema":   {"const": "szk.capabilities/v1"},
    "plugin":   {"type": "string", "examples": ["metaelemzes","validator","figure-forge","composer","presubmit"]},
    "version":  {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+"},
    "python":   {"type": "string"},
    "ok":       {"type": "boolean", "description": "a deklarált parancsok ebben az interpreterben futtathatók"},
    "contracts":{"type": "object", "additionalProperties": {"type": "object", "required": ["dir","sha256"],
                 "properties": {"dir": {"type": "array", "items": {"enum": ["in","out"]}},
                                "sha256": {"$ref": "urn:szk:contract:common:1#/$defs/sha256"}}},
                 "description": "a plugin contracts/ mappájában lévő séma bájt-hash-e — futásidejű sodródás-őr"},
    "commands": {"type": "array", "items": {"type": "object", "required": ["name","argv"],
                 "properties": {"name": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}},
                                "in": {"type": "array"}, "out": {"type": "array"},
                                "needs": {"type": "array", "items": {"type": "string"}}, "available": {"type": "boolean"}}}},
    "requires": {"type": "object", "properties": {"modules": {"type": "array"}, "missing": {"type": "array"}}},
    "known_issues": {"type": "array", "items": {"type": "object", "required": ["id","summary"],
                 "properties": {"id": {"type": "string"}, "summary": {"type": "string"}, "fixed_in": {"type": "string"}}}}
  } }
""")

SHA = "0123456789abcdef" * 4


def reg():
    return sl.build_registry([COMMON, CAPABILITIES])


def ref(name):
    return {"$ref": "urn:szk:contract:common:1#/$defs/%s" % name}


class TypeTests(unittest.TestCase):
    CASES = [
        # (típus, érvényes, érvénytelen)
        ("null", [None], [0, False, "", [], {}]),
        ("boolean", [True, False], [0, 1, None, "true"]),
        ("string", ["", "ő"], [None, 1, b"x", ["a"]]),
        ("object", [{}, {"a": 1}], [[], None, "x", ()]),
        ("array", [[], [1], ()], [{}, "abc", None]),
        ("number", [0, -1, 1.5, 10 ** 30, 1e300], [True, False, None, "1", float("nan"), float("inf")]),
        ("integer", [0, -3, 10 ** 30, 1.0, -2.0], [True, False, 1.5, None, "1", float("nan"), float("-inf")]),
    ]

    def test_types(self):
        for t, good, bad in self.CASES:
            for v in good:
                self.assertEqual(validate(v, {"type": t}), [], (t, v))
            for v in bad:
                errs = validate(v, {"type": t})
                self.assertEqual(len(errs), 1, (t, v))
                self.assertIn("típushiba", errs[0])

    def test_bool_is_not_integer_nor_number(self):
        self.assertNotEqual(validate(True, {"type": "integer"}), [])
        self.assertNotEqual(validate(False, {"type": "number"}), [])
        self.assertEqual(validate(True, {"type": ["integer", "boolean"]}), [])

    def test_type_list(self):
        s = {"type": ["number", "null"]}
        for v in (None, 1, 1.5):
            self.assertEqual(validate(v, s), [])
        for v in ("x", True, [], {}):
            errs = validate(v, s)
            self.assertEqual(len(errs), 1)
            self.assertIn("number vagy null", errs[0])

    def test_non_json_value(self):
        errs = validate(object(), {"type": "string"})
        self.assertIn("nem JSON-típus", errs[0])

    def test_bad_type_keyword(self):
        for bad in ("int", [], ["string", "float"], 5, None):
            with self.assertRaises(SchemaError):
                validate(1, {"type": bad})


class EnumConstTests(unittest.TestCase):
    def test_enum(self):
        s = {"enum": ["error", "warning", "info"]}
        self.assertEqual(validate("info", s), [])
        errs = validate("fatal", s)
        self.assertEqual(len(errs), 1)
        self.assertIn('"error"', errs[0])

    def test_enum_json_equality(self):
        self.assertNotEqual(validate(True, {"enum": [1]}), [])
        self.assertNotEqual(validate(1, {"enum": [True]}), [])
        self.assertNotEqual(validate(False, {"enum": [0]}), [])
        self.assertNotEqual(validate(0, {"enum": [None]}), [])
        self.assertEqual(validate(1, {"enum": [1.0]}), [])
        self.assertEqual(validate([1, {"a": None}], {"enum": [[1, {"a": None}]]}), [])
        self.assertNotEqual(validate([1, {"a": False}], {"enum": [[1, {"a": None}]]}), [])
        self.assertNotEqual(validate({"a": 1, "b": 2}, {"enum": [{"a": 1}]}), [])
        self.assertEqual(validate((1, 2), {"enum": [[1, 2]]}), [])

    def test_const(self):
        s = {"const": "szk.capabilities/v1"}
        self.assertEqual(validate("szk.capabilities/v1", s), [])
        self.assertNotEqual(validate("szk.capabilities/v2", s), [])
        self.assertNotEqual(validate(0, {"const": False}), [])
        self.assertNotEqual(validate(False, {"const": 0}), [])
        self.assertEqual(validate(None, {"const": None}), [])
        self.assertNotEqual(validate(0, {"const": None}), [])
        self.assertEqual(validate(True, {"const": True}), [])

    def test_bad_enum_keyword(self):
        for bad in ([], "a", None):
            with self.assertRaises(SchemaError):
                validate(1, {"enum": bad})


class StringTests(unittest.TestCase):
    def test_pattern_is_search(self):
        self.assertEqual(validate("abc", {"pattern": "b"}), [])
        self.assertNotEqual(validate("abc", {"pattern": "^b"}), [])
        self.assertEqual(validate("1.2.3-beta", {"pattern": "^\\d+\\.\\d+\\.\\d+"}), [])

    def test_dollar_is_end_of_string_only(self):
        s = {"pattern": "^[0-9a-f]{64}$"}
        self.assertEqual(validate(SHA, s), [])
        self.assertNotEqual(validate(SHA + "\n", s), [])
        self.assertEqual(validate("a$b", {"pattern": "^a\\$b$"}), [])
        self.assertEqual(validate("$", {"pattern": "^[$]$"}), [])
        self.assertEqual(validate("ab", {"pattern": "^(a|b)+$"}), [])

    def test_ascii_digit_class(self):
        s = {"pattern": "^[VPX]\\d{3}$"}
        self.assertEqual(validate("V012", s), [])
        self.assertNotEqual(validate("V\u0661\u0662\u0663", s), [])     # arab-indiai számjegyek

    def test_pattern_ignored_for_non_strings(self):
        self.assertEqual(validate(5, {"pattern": "^x$"}), [])

    def test_bad_pattern(self):
        with self.assertRaises(SchemaError):
            validate("x", {"pattern": "("})
        with self.assertRaises(SchemaError):
            validate("x", {"pattern": 5})

    def test_lengths_in_code_points(self):
        s = {"maxLength": 3, "minLength": 2}
        self.assertEqual(validate("őűá", s), [])
        self.assertEqual(validate("😀😀", s), [])
        self.assertIn("hosszabb, mint 3", validate("abcd", s)[0])
        self.assertIn("rövidebb, mint 2", validate("a", s)[0])
        self.assertEqual(validate(12345, s), [])
        for bad in (-1, 1.5, True, "3"):
            with self.assertRaises(SchemaError):
                validate("a", {"maxLength": bad})


class NumberTests(unittest.TestCase):
    def test_bounds(self):
        s = {"minimum": 0, "maximum": 1}
        for v in (0, 0.5, 1, 1.0):
            self.assertEqual(validate(v, s), [])
        self.assertIn("minimum", sl.validate_detailed(-0.001, s)[0].keyword)
        self.assertIn("maximum", sl.validate_detailed(1.001, s)[0].keyword)
        self.assertEqual(validate("5", s), [])
        self.assertEqual(validate(True, {"minimum": 5}), [])       # bool nem szám

    def test_exclusive(self):
        s = {"exclusiveMinimum": 0, "exclusiveMaximum": 1}
        self.assertEqual(validate(0.5, s), [])
        self.assertNotEqual(validate(0, s), [])
        self.assertNotEqual(validate(1, s), [])

    def test_bad_bounds(self):
        for bad in ("0", True, None, float("nan")):
            with self.assertRaises(SchemaError):
                validate(1, {"minimum": bad})


class ObjectTests(unittest.TestCase):
    S = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "integer"}, "b": {"type": "object", "properties": {"c": {"type": "string"}}}},
    }

    def test_required(self):
        self.assertEqual(validate({"a": 1, "b": {}}, self.S), [])
        errs = sl.validate_detailed({"b": {}}, self.S)
        self.assertEqual([(e.path, e.keyword) for e in errs], [("", "required")])
        self.assertIn("hiányzó kötelező mező: a", str(errs[0]))
        self.assertTrue(str(errs[0]).startswith("/: "))
        self.assertEqual(validate([1], {"required": ["a"]}), [])

    def test_nested_paths(self):
        errs = sl.validate_detailed({"a": "x", "b": {"c": 5}}, self.S)
        self.assertEqual(sorted(e.path for e in errs), ["/a", "/b/c"])
        self.assertTrue(any(s.startswith("/b/c: ") for s in validate({"a": 1, "b": {"c": 5}}, self.S)))

    def test_pointer_escaping(self):
        s = {"properties": {"a/b": {"type": "string"}, "m~n": {"type": "string"}}}
        errs = sl.validate_detailed({"a/b": 1, "m~n": 2}, s)
        self.assertEqual(sorted(e.path for e in errs), ["/a~1b", "/m~0n"])
        self.assertEqual(sl.json_pointer(["a/b", "m~n", 3]), "/a~1b/m~0n/3")

    def test_additional_properties_false(self):
        s = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
        self.assertEqual(validate({"a": 1}, s), [])
        errs = sl.validate_detailed({"a": 1, "x": 2, "y": 3}, s)
        self.assertEqual(sorted(e.path for e in errs), ["/x", "/y"])
        self.assertTrue(all(e.keyword == "additionalProperties" for e in errs))

    def test_additional_properties_schema(self):
        s = {"properties": {"n": {"type": "string"}}, "additionalProperties": {"type": "integer"}}
        self.assertEqual(validate({"n": "x", "k1": 1, "k2": 2}, s), [])
        errs = sl.validate_detailed({"n": "x", "k1": "rossz"}, s)
        self.assertEqual([e.path for e in errs], ["/k1"])
        self.assertEqual(validate({"q": "x"}, {"additionalProperties": True}), [])

    def test_properties_ignored_for_non_objects(self):
        self.assertEqual(validate("x", {"required": ["a"], "properties": {"a": {"type": "integer"}}}), [])
        self.assertEqual(validate([{"a": "x"}], {"properties": {"0": {"type": "integer"}}}), [])


class ArrayTests(unittest.TestCase):
    def test_items(self):
        s = {"type": "array", "items": {"type": "string"}}
        self.assertEqual(validate(["a", "b"], s), [])
        errs = sl.validate_detailed(["a", 1, "c", None], s)
        self.assertEqual([e.path for e in errs], ["/1", "/3"])

    def test_items_false_and_true(self):
        self.assertEqual(validate([], {"items": False}), [])
        self.assertNotEqual(validate([1], {"items": False}), [])
        self.assertEqual(validate([1, "x"], {"items": True}), [])

    def test_tuple_items_rejected(self):
        with self.assertRaises(SchemaError):
            validate([1], {"items": [{"type": "integer"}]})

    def test_min_max_items(self):
        s = {"minItems": 1, "maxItems": 2}
        self.assertEqual(validate([1], s), [])
        self.assertIn("kevesebb, mint 1", validate([], s)[0])
        self.assertIn("több, mint 2", validate([1, 2, 3], s)[0])
        self.assertEqual(validate("abc", s), [])


class OneOfTests(unittest.TestCase):
    def test_exactly_one(self):
        s = {"oneOf": [{"type": "integer"}, {"type": "number"}]}
        self.assertEqual(validate(1.5, s), [])                # csak a number
        errs = validate(1, s)                                  # mindkettő → hiba
        self.assertEqual(len(errs), 1)
        self.assertIn("több oneOf-ág is illeszkedik (0., 1.)", errs[0])
        errs = validate("x", s)
        self.assertIn("egyik oneOf-ág sem illeszkedik (2 ág)", errs[0])

    def test_discriminated_union_with_closest_branch(self):
        s = {"oneOf": [
            {"type": "object", "required": ["kind", "n"], "properties": {"kind": {"const": "a"}, "n": {"type": "integer"}}},
            {"type": "object", "required": ["kind", "s"], "properties": {"kind": {"const": "b"}, "s": {"type": "string"}}},
        ]}
        self.assertEqual(validate({"kind": "a", "n": 3}, s), [])
        self.assertEqual(validate({"kind": "b", "s": "x"}, s), [])
        errs = sl.validate_detailed({"kind": "a", "n": "x"}, s)
        self.assertEqual(errs[0].keyword, "oneOf")
        self.assertTrue(any(e.path == "/n" and "[oneOf 0. ág]" in e.message for e in errs), errs)

    def test_ambiguous_closest_branch_not_explained(self):
        s = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        errs = sl.validate_detailed(None, s)
        self.assertEqual(len(errs), 1)

    def test_oneof_with_refs_and_null(self):
        s = {"oneOf": [{"type": "null"}, ref("sha256")]}
        r = reg()
        self.assertEqual(validate(None, s, r), [])
        self.assertEqual(validate(SHA, s, r), [])
        self.assertNotEqual(validate("xyz", s, r), [])

    def test_bad_oneof(self):
        for bad in ([], {"type": "string"}, [5]):
            with self.assertRaises(SchemaError):
                validate(1, {"oneOf": bad})


class RefTests(unittest.TestCase):
    def test_local_defs(self):
        s = {"$defs": {"pos": {"type": "integer", "minimum": 1}},
             "type": "object", "properties": {"n": {"$ref": "#/$defs/pos"}}}
        self.assertEqual(validate({"n": 3}, s), [])
        errs = sl.validate_detailed({"n": 0}, s)
        self.assertEqual([(e.path, e.keyword) for e in errs], [("/n", "minimum")])

    def test_recursive_root_ref(self):
        tree = {"type": "object", "required": ["name"],
                "properties": {"name": {"type": "string"}, "children": {"type": "array", "items": {"$ref": "#"}}}}
        good = {"name": "a", "children": [{"name": "b", "children": [{"name": "c"}]}]}
        self.assertEqual(validate(good, tree), [])
        bad = {"name": "a", "children": [{"name": "b", "children": [{"nev": "c"}]}]}
        self.assertEqual([e.path for e in sl.validate_detailed(bad, tree)], ["/children/0/children/0"])

    def test_registry_ref_with_fragment(self):
        r = reg()
        s = {"type": "object", "properties": {"h": ref("sha256"), "sev": ref("severity"), "uid": ref("row_uid")}}
        self.assertEqual(validate({"h": SHA, "sev": "info", "uid": "r0a1b"}, s, r), [])
        errs = sl.validate_detailed({"h": SHA.upper(), "sev": "fatal", "uid": "R0A1B"}, s, r)
        self.assertEqual(sorted(e.path for e in errs), ["/h", "/sev", "/uid"])

    def test_ref_inside_registered_schema_resolves_there(self):
        r = reg()
        s = {"$defs": {"num": {"type": "string"}},       # ugyanaz a név a hivatkozó sémában — nem ez kell
             "type": "object", "properties": {"d": ref("display")}}
        self.assertEqual(validate({"d": {"est": 0.1, "lo": None, "hi": 2}}, s, r), [])
        errs = sl.validate_detailed({"d": {"est": "0.1", "lo": 1, "hi": 2}}, s, r)
        self.assertEqual([e.path for e in errs], ["/d/est"])

    def test_whole_document_ref(self):
        r = reg()
        doc = {"schema": "szk.capabilities/v1", "plugin": "x", "version": "1.0.0", "ok": True,
               "contracts": {}, "commands": []}
        self.assertEqual(validate(doc, {"$ref": "urn:szk:contract:capabilities:1"}, r), [])
        self.assertEqual(validate(doc, {"$ref": "urn:szk:contract:capabilities:1#"}, r), [])

    def test_ref_to_own_id_without_registry(self):
        s = {"$id": "urn:szk:contract:x:1", "$defs": {"a": {"type": "string"}}, "$ref": "urn:szk:contract:x:1#/$defs/a"}
        self.assertEqual(validate("ok", s), [])
        self.assertNotEqual(validate(1, s), [])

    def test_ref_siblings_also_apply(self):
        s = {"$defs": {"s": {"type": "string"}}, "$ref": "#/$defs/s", "maxLength": 2}
        self.assertEqual(validate("ab", s), [])
        self.assertEqual(len(validate("abc", s)), 1)
        self.assertEqual(len(validate(5, s)), 1)

    def test_pointer_escapes_in_ref(self):
        s = {"$defs": {"a/b": {"type": "integer"}, "c d": {"type": "string"}, "t~x": {"type": "null"}},
             "properties": {"x": {"$ref": "#/$defs/a~1b"}, "y": {"$ref": "#/$defs/c%20d"}, "z": {"$ref": "#/$defs/t~0x"}}}
        self.assertEqual(validate({"x": 1, "y": "s", "z": None}, s), [])
        self.assertEqual(len(validate({"x": "1", "y": 1, "z": 0}, s)), 3)

    def test_unresolvable_refs(self):
        for bad in ("#/$defs/nincs", "urn:szk:contract:nincs:1", "urn:szk:contract:common:1#/$defs/nincs",
                    "#anchor", "http://example.com/s.json"):
            with self.assertRaises(SchemaError):
                validate(1, {"$ref": bad}, reg())
        with self.assertRaises(SchemaError):
            validate(1, {"$ref": 5})

    def test_circular_ref(self):
        s = {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}
        with self.assertRaises(SchemaError):
            validate(1, s)
        with self.assertRaises(SchemaError):
            validate(1, {"$ref": "#"})


class SchemaKeywordTests(unittest.TestCase):
    def test_boolean_schemas(self):
        self.assertEqual(validate(123, True), [])
        self.assertEqual(len(validate(123, False)), 1)
        self.assertEqual(validate({"a": 1}, {"properties": {"a": True}}), [])
        self.assertEqual(len(validate({"a": 1}, {"properties": {"a": False}})), 1)

    def test_annotations_ignored(self):
        s = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:x:1", "title": "T",
             "description": "d", "examples": [1], "default": 0, "$comment": "c", "format": "email",
             "deprecated": False, "type": "integer"}
        self.assertEqual(validate(5, s), [])
        self.assertEqual(validate("nem-email", {"format": "email"}), [])

    def test_unsupported_keywords_raise(self):
        for kw, val in (("anyOf", [{}]), ("allOf", [{}]), ("not", {}), ("uniqueItems", True),
                        ("patternProperties", {}), ("prefixItems", []), ("if", {}), ("multipleOf", 2),
                        ("dependentRequired", {}), ("propertyNames", {}), ("contains", {}), ("$anchor", "a")):
            with self.assertRaises(SchemaError, msg=kw):
                validate(1, {kw: val})

    def test_schema_must_be_object_or_bool(self):
        for bad in (None, 1, "x", []):
            with self.assertRaises(SchemaError):
                validate(1, bad)
        with self.assertRaises(SchemaError):
            validate({"a": 1}, {"properties": {"a": 5}})

    def test_property_named_like_keyword(self):
        s = {"type": "object", "properties": {"type": {"type": "string"}, "title": {"type": "integer"},
                                              "anyOf": {"type": "null"}}}
        self.assertEqual(validate({"type": "x", "title": 1, "anyOf": None}, s), [])
        self.assertEqual(len(validate({"type": 1, "title": "x", "anyOf": 0}, s)), 3)


class CheckSchemaTests(unittest.TestCase):
    def test_contract_schemas_are_clean(self):
        r = reg()
        self.assertEqual(sl.check_schema(COMMON, r), [])
        self.assertEqual(sl.check_schema(CAPABILITIES, r), [])

    def test_problems_reported_with_schema_pointer(self):
        s = {"type": "object", "properties": {"a": {"type": "int"}, "b": {"pattern": "("}, "c": {"anyOf": []},
                                              "d": {"$ref": "#/$defs/nincs"}, "e": {"$ref": "urn:x:9#/a"}},
             "$defs": {"x": {"minItems": -1}, "y": 7}, "items": [{}]}
        probs = sl.check_schema(s)
        joined = "\n".join(probs)
        for where in ("#/properties/a/type", "#/properties/b/pattern", "#/properties/c/anyOf",
                      "#/properties/d/$ref", "#/properties/e/$ref", "#/$defs/x/minItems", "#/$defs/y", "#/items"):
            self.assertIn(where + ":", joined)
        self.assertEqual(len(probs), 8, probs)

    def test_unreached_problems_found_statically(self):
        s = {"oneOf": [{"type": "string"}, {"type": "integer", "uniqueItems": True}]}
        self.assertEqual(validate("x", {"$defs": {"bad": {"uniqueItems": True}}}), [])
        self.assertEqual(len(sl.check_schema(s)), 1)


class RegistryTests(unittest.TestCase):
    def test_build_and_register(self):
        r = sl.build_registry([COMMON, CAPABILITIES])
        self.assertEqual(set(r), {"urn:szk:contract:common:1", "urn:szk:contract:capabilities:1"})
        self.assertEqual(sl.build_registry({"a": COMMON}), {"urn:szk:contract:common:1": COMMON})
        self.assertEqual(sl.register(r, dict(COMMON)), "urn:szk:contract:common:1")      # azonos tartalom: rendben

    def test_trailing_hash_normalized(self):
        r = {}
        sl.register(r, {"$id": "urn:szk:contract:t:1#", "$defs": {"a": {"type": "null"}}})
        self.assertEqual(validate(None, {"$ref": "urn:szk:contract:t:1#/$defs/a"}, r), [])

    def test_conflicts_and_missing_id(self):
        r = sl.build_registry([COMMON])
        with self.assertRaises(SchemaError):
            sl.register(r, {"$id": "urn:szk:contract:common:1", "type": "string"})
        for bad in ({"type": "string"}, {"$id": ""}, {"$id": 5}, []):
            with self.assertRaises(SchemaError):
                sl.register({}, bad)

    def test_load_schema_dir(self):
        tmp = tempfile.mkdtemp(prefix="ma_gui_schema_")
        try:
            for name, doc in (("common.schema.json", COMMON), ("capabilities.schema.json", CAPABILITIES)):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, ensure_ascii=False)
            with open(os.path.join(tmp, "notes.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            r = sl.load_schema_dir(tmp)
            self.assertEqual(set(r), {"urn:szk:contract:common:1", "urn:szk:contract:capabilities:1"})
            self.assertEqual(validate(SHA, ref("sha256"), r), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_pointer(self):
        self.assertEqual(sl.resolve_pointer({"a": [{"b": 1}]}, "/a/0/b"), 1)
        self.assertIs(sl.resolve_pointer(COMMON, ""), COMMON)
        for bad in ("/a/1", "/a/x", "a", "/a/-1", "/a/\u0661"):
            with self.assertRaises(SchemaError):
                sl.resolve_pointer({"a": [1]}, bad)


class CommonDefsTests(unittest.TestCase):
    def setUp(self):
        self.r = reg()

    def ok(self, name, value):
        self.assertEqual(validate(value, ref(name), self.r), [], (name, value))

    def bad(self, name, value):
        self.assertNotEqual(validate(value, ref(name), self.r), [], (name, value))

    def test_relpath(self):
        for v in ("03_adatok/bcg.csv", "a", "05_elemzes/x/r1/forest.svg", "a.b/c"):
            self.ok("relpath", v)
        for v in ("/abs", "C:/x", "c:x", "a/../b", "..", "x/..", "a\\b", "a:b", "", 5):
            self.bad("relpath", v)

    def test_kbid_rowuid_i18n_num(self):
        for v in ("V001", "X022", "COCHRANE-10-4-2", "RULE_A-B1"):
            self.ok("kbid", v)
        for v in ("V01", "v001", "V0012", "-X", "A-", "COCHRANE-10.4"):
            self.bad("kbid", v)
        for v in ("r0000", "rabc123def456"):
            self.ok("row_uid", v)
        for v in ("r000", "R0000", "r0000000000000", "rABCD", "x0000"):
            self.bad("row_uid", v)
        self.ok("i18n", {"hu": "a", "en": "b", "de": "c"})
        self.bad("i18n", {"hu": "a"})
        self.bad("i18n", {"hu": "a", "en": None})
        for v in (None, 0, -1.5):
            self.ok("num", v)
        for v in ("1", True, float("nan")):
            self.bad("num", v)


class CapabilitiesContractTests(unittest.TestCase):
    """4.1 kézfogás: ≥ 3 pozitív és ≥ 3 negatív példa (8.3)."""

    def base(self):
        return {
            "schema": "szk.capabilities/v1", "plugin": "validator", "version": "1.1.0", "python": "3.11.4",
            "ok": True,
            "contracts": {"szk.appraisal-result/v1": {"dir": ["out"], "sha256": SHA}},
            "commands": [{"name": "assess", "argv": ["python", "x.py", "--json"], "needs": [], "available": True}],
            "known_issues": [{"id": "H1", "summary": "üres TRIPOD", "fixed_in": "1.1.0"}],
        }

    def test_positive(self):
        r = reg()
        docs = [self.base()]
        d = self.base()
        d.update(contracts={}, commands=[], plugin="metaelemzes", version="0.2.0-dev")
        del d["known_issues"], d["python"]
        docs.append(d)
        d = self.base()
        d["contracts"]["szk.ma.plot/v2"] = {"dir": ["in", "out"], "sha256": "f" * 64, "extra": 1}
        d["requires"] = {"modules": ["matplotlib"], "missing": ["matplotlib"]}
        d["ok"] = False
        d["unknown_future_field"] = {"x": 1}          # bővítés: ismeretlen mező megengedett
        docs.append(d)
        for doc in docs:
            self.assertEqual(validate(doc, CAPABILITIES, r), [], doc)
            self.assertEqual(validate(doc, {"$ref": "urn:szk:contract:capabilities:1"}, r), [])

    def test_negative(self):
        r = reg()
        cases = []
        d = self.base(); d["schema"] = "szk.capabilities/v2"; cases.append((d, ["/schema"]))
        d = self.base(); del d["commands"]; cases.append((d, [""]))
        d = self.base(); d["version"] = "v1"; cases.append((d, ["/version"]))
        d = self.base(); d["ok"] = 1; cases.append((d, ["/ok"]))
        d = self.base(); d["contracts"]["szk.x/v1"] = {"dir": ["both"], "sha256": "ABC"}
        cases.append((d, ["/contracts/szk.x~1v1/dir/0", "/contracts/szk.x~1v1/sha256"]))
        d = self.base(); d["commands"][0]["argv"] = ["python", 3]; del d["commands"][0]["name"]
        cases.append((d, ["/commands/0", "/commands/0/argv/1"]))
        d = self.base(); d["known_issues"] = [{"id": "H1"}]; cases.append((d, ["/known_issues/0"]))
        for doc, paths in cases:
            errs = sl.validate_detailed(doc, CAPABILITIES, r)
            self.assertEqual(sorted(e.path for e in errs), sorted(paths), errs)


class LimitsAndPrivacyTests(unittest.TestCase):
    def test_error_limit(self):
        s = {"type": "array", "items": {"type": "string"}}
        errs = sl.validate_detailed(list(range(50)), s, limit=10)
        self.assertEqual(len(errs), 11)
        self.assertEqual(errs[-1].keyword, "limit")
        self.assertEqual(len(validate(list(range(1000)), s)), sl.MAX_ERRORS + 1)

    def test_is_valid(self):
        self.assertTrue(sl.is_valid(1, {"type": "integer"}))
        self.assertFalse(sl.is_valid(True, {"type": "integer"}))
        self.assertTrue(sl.is_valid(SHA, ref("sha256"), reg()))

    def test_deep_instance_does_not_crash(self):
        inst = []
        node = inst
        for _ in range(500):
            child = []
            node.append(child)
            node = child
        s = {"$defs": {"t": {"type": "array", "items": {"$ref": "#/$defs/t"}}}, "$ref": "#/$defs/t"}
        errs = sl.validate_detailed(inst, s)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].keyword, "depth")

    def test_messages_never_contain_instance_values(self):
        secret = "TITKOS-BETEG-Kovács"
        schemas = [
            {"type": "integer"}, {"enum": ["a", "b"]}, {"const": "x"}, {"pattern": "^[0-9]+$"}, {"maxLength": 3},
            {"oneOf": [{"type": "integer"}, {"type": "null"}]}, {"items": {"type": "integer"}},
            {"properties": {"study": {"type": "integer"}}}, False,
        ]
        for s in schemas:
            for inst in (secret, [secret], {"study": secret}):
                for e in sl.validate_detailed(inst, s):
                    self.assertNotIn("TITKOS", e.message, (s, inst))

    def test_large_table_performance(self):
        import time
        s = {"type": "object", "required": ["header", "rows"], "properties": {
            "header": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
            "rows": {"type": "array", "maxItems": 5000,
                     "items": {"type": "array", "maxItems": 200, "items": {"type": ["string", "null"]}}}}}
        inst = {"header": ["c%d" % i for i in range(20)], "rows": [["1.5"] * 20 for _ in range(5000)]}
        t0 = time.perf_counter()
        self.assertEqual(validate(inst, s), [])
        self.assertLess(time.perf_counter() - t0, 3.0)


if __name__ == "__main__":
    unittest.main()
