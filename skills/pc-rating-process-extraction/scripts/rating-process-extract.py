#!/usr/bin/env python3
"""Mechanical extractor for the PC Rating Process document
(`pas-adapters.policycenter-rating-process/4.0.0`).

Reads one PC configuration checkout, the line's product capture
(<product-root>/extracted/product-capture.json, a ready pc-tools.product-capture/1.0.0
taken at the checkout's commit) and its ratebook folders, and writes one JSON document for one line
and one engine, `<product-root>/extracted/rating-process-<EngineClass>.json`. It prints a summary
for the human gate: counts, open questions and the readiness check.
A witness, not an engine: it records symbols, call edges, the method bodies, the tables consulted and
what each unit reads and writes, never a derived formula, rate, factor value, or rounding rule.
A capture that is missing, not ready, of another format, product or line, or taken at another
commit than the checkout's stops the run (exit 2).

Standard library only. Python 3.8+. No network. Imports nothing outside this directory: the reader
for the YAML decisions file and the locator helper are defined below.

Every fact carries one locator `at: "<path>[:<line>][#<symbol>]"` (a list of locators when a fact
has several). Checkout paths are relative to --source-root; ratebook paths to the product root.

The document (top-level keys, in order):
  * identity: product, line, revision (the checkout commit), dirty,
    and the productModel pin (the capture's path, source.revision, dirty, product, line pattern)
  * engine: the selected engine class, its base class, the registered IRatingPlugin, and every
    overridden member with its baseline status (omitted when vanilla)
  * dispatch: every createRatingEngine branch in source order, with its verbatim gate and
    RateMethod, `selected: true` on the branch the assumed gates reach
  * assumedGates: from the decisions file, never inferred
  * flow: the rating roots rateSlice / rateWindow, each with its call edges. An edge is
    {call | routine, args, for, when, emits?, at}: the loops (each naming the captured entity or
    the gsrc class, such as a rating DTO, its collection holds) and guards around the site, and,
    when the callee selects a CostData class by a `switch` on a parameter this site passes as a
    string literal, the cost entity the site emits. An edge whose target is a unit stops there; a
    non-unit helper's own sites nest under the edge's `calls`
  * units: one entry per unit of work, each once. A gosuMethod unit is a method reachable from the
    roots that constructs a CostData, calls a calc routine or is scoped to a captured clause
    pattern, or, when no unit sits between the roots and it, one that consults a lookup, reads a
    term or has dataflow facts of its own (a root included). It carries its own call edges, the
    CostData members it writes, its term reads, the cost entities it emits, its dataflow facts and
    its body verbatim in `source`. The dataflow facts are seven lists, each present and possibly
    empty, over its body and the helpers it folds: entityReads (entity properties typed through
    the capture), engineMembers (reads, writes and calls of the engine's and its base classes'
    members, with where each is declared), entityWrites, beanInserts, crossInstance (reads of an
    engine member's CostData list, or of the collection a loop the unit runs under iterates),
    cacheAccess (the FX rate cache, FX-converted CostData amounts, rate book selection) and
    unresolved (every expression whose fact the run cannot establish, with the reason). A
    calcRoutine unit carries the ratebook routine it binds to (book, edition, routine JSON,
    readable text, sha256, parameter set), the tables the routine consults, the reads and writes
    its steps spell, and its parameter `binds` (the Map<CalcRoutineParamName, Object> entries
    with their static type and where each value comes from)
  * emissions: one per (constructing unit, CostData class, selecting switch case): the cost
    entity, rateMode, the case as a PC code expression, the key dimensions the source resolves and,
    in unresolvedKey, the ones it does not
  * lookups: one per RateAdjFactorSearchCriteria factor name, dimensions from the row shape of
    config/resources/systables/rating_adj_factors.xml (never its rows)
  * pins: source root, configuration root, generation mode, and the sha256 of every ratebook file
    the run read

What it leaves to the consultant, through the --decisions file (YAML):
  * selectedEngine (required for a valid document) and assumedGates
  * unit reads and produces that neither the code nor a bound ratebook routine spells
  * emission key values and status
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, OrderedDict

SCHEMA_URI = "urn:pas-adapters:schema:policycenter-rating-process:4.0.0"
SCHEMA_VERSION = "pas-adapters.policycenter-rating-process/4.0.0"
DECISION_KEYS = ("selectedEngine", "assumedGates", "units", "emissions")
CONFIGURATION_ROOT = "modules/configuration"
UNIT_KEY_ORDER = ("key", "kind", "at", "status", "appliesTo", "calls", "writes", "scratch", "reads", "emits",
                  "entityReads", "engineMembers", "entityWrites", "beanInserts", "crossInstance", "cacheAccess",
                  "unresolved", "source", "ratebook", "tables", "binds")
LOOP_METHODS = ("rateSlice", "rateWindow")
ROOT_METHODS = LOOP_METHODS + ("rateOnly",)
PLATFORM_KEY_DIMENSIONS = ("Currency", "ChargePattern", "ChargeGroup", "RateAmountType", "BillGroup", "CostCode")
COSTDATA_MEMBERS = ("Basis", "StandardBaseRate", "StandardAdjRate", "StandardTermAmount", "StandardAmount",
                    "ActualBaseRate", "ActualAdjRate", "ActualTermAmount", "ActualAmount", "NumDaysInRatedTerm",
                    "RateAmountType", "ChargePattern", "ChargeGroup", "BillGroup", "CostCode", "RateBook",
                    "Overridable", "SubjectToReporting", "RatedOrder", "OverrideTermAmount",
                    "OverrideReason", "OverrideSource")
OVERRIDE_WATCH = ("rateOnly", "rateSlice", "rateWindow", "shouldRateThisSliceForward", "existingSliceModeCosts",
                  "createCostDataForCost", "NumDaysInCoverageRatedTerm", "preLoadCostArrays", "mergeCosts")
# Java/PC code scalar types a CalcRoutineParamName binding's expression may resolve to, when
# the parser cannot resolve it to a captured entity, a CostData subclass, or another PC class.
JAVA_SCALAR_TYPES = {"String", "boolean", "Boolean", "int", "Integer", "long", "Long", "double", "Double",
                     "float", "Float", "BigDecimal", "BigInteger", "Date", "DateTime", "short", "Short",
                     "byte", "Byte", "char", "Character", "Object", "Currency"}


# --- shared helpers: decisions reader, JSON writer, locators ----------------------------------------

# The locator grammar every `at` obeys: `<path>[:<line>][#<symbol>]`, the same pattern the
# rating-process schema states. Checked when a consultant-written locator is read from the
# decisions file.
LOCATOR_RE = re.compile(r"^[^:#\s]+(:[1-9]\d*)?(#.+)?$")
CAPTURE_FORMAT = "pc-tools.product-capture/1.0.0"
# Capture entity children that declare a PC code-visible member (a property on the entity).
ENTITY_MEMBER_KINDS = ("column", "typekey", "foreignkey", "array", "onetoone", "edgeforeignkey", "monetaryamount")

def die(message):
    sys.stderr.write("error: %s\n" % message)
    sys.exit(2)

def check_at(value, where):
    """A consultant-written `at`: one locator string, or a non-empty list of them, each matching
    the locator grammar. Without this the extractor merges whatever the decisions file says
    straight into the document, exits 0, and leaves a document that only fails later, in a
    validator the extractor never runs."""
    items = value if isinstance(value, list) else [value]
    if isinstance(value, list) and not value:
        die("decisions file: %s must be a locator string or a non-empty array of them" % where)
    for i, item in enumerate(items):
        at = "%s[%d]" % (where, i) if isinstance(value, list) else where
        if not isinstance(item, str) or not LOCATOR_RE.match(item):
            die("decisions file: %s must be a locator `<path>[:<line>][#<symbol>]` with a 1-based line"
                " and no whitespace in the path (got %r)" % (at, item))

def check_decision_locators(node, where):
    """Walk the decisions document and check every `at` it carries, wherever it sits. A
    `sourceRefs` key anywhere is refused: the locator form is `at`."""
    if isinstance(node, dict):
        for key, value in node.items():
            at = "%s.%s" % (where, key) if where else key
            if key == "sourceRefs":
                die("decisions file: %s uses sourceRefs; write the locator as `at` (\"<path>[:<line>][#<symbol>]\", or a list of them)" % at)
            if key == "at":
                check_at(value, at)
            else:
                check_decision_locators(value, at)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            check_decision_locators(value, "%s[%d]" % (where, i))

def split_at(at):
    """(path, line or None, symbol or None) of one locator string."""
    path, symbol = (at.split("#", 1) + [None])[:2]
    line = None
    if ":" in path:
        path, number = path.rsplit(":", 1)
        line = int(number)
    return path, line, symbol

def local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag

def parse_xml(path):
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as error:
        die("cannot parse %s: %s" % (path, error))

def rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")

# A mapping key in the decisions subset: a bare name, or a double-quoted string for a key such as
# `CPRatingEngine.rateBuilding`. A quoted key holds no backslash, so no escape can change it.
KEY_PATTERN = r'[A-Za-z_$][A-Za-z0-9_$-]*|"[^"\\]+"'

class YamlSubsetError(Exception):
    pass

def read_yaml_subset(path):
    """Read the consultant's decisions file, YAML in this subset: comments, `key: value` (a key bare or
    double-quoted, such as a unit key with dots), `key:` + nested block, `- ` items, inline `[]` and
    `{}`, double-quoted strings, bare ints and booleans,
    and `key: |` literal block scalars (lines indented deeper than the key; the first non-blank
    line's indent is stripped, blank lines inside stay, the value ends with exactly one line feed).
    Anything else raises YamlSubsetError so a hand edit outside the conventions is never
    silently dropped."""
    with open(path, encoding="utf-8") as handle:
        raw = handle.read().split("\n")

    def indent_of(text):
        return len(text) - len(text.lstrip(" "))

    def is_blank(text):
        return text.strip(" ") == ""

    index = [0]

    def read_block(key_indent, number):
        collected = []
        while index[0] < len(raw):
            text = raw[index[0]]
            if not is_blank(text) and indent_of(text) <= key_indent:
                break
            collected.append((text, index[0] + 1))
            index[0] += 1
        first = next((text for text, _ in collected if not is_blank(text)), None)
        if first is None:
            raise YamlSubsetError("%s:%d block scalar has no content" % (path, number))
        content_indent = indent_of(first)
        out = []
        for text, line_number in collected:
            if not is_blank(text) and indent_of(text) < content_indent:
                raise YamlSubsetError("%s:%d line is indented less than the block scalar's first line (%d spaces)" % (path, line_number, content_indent))
            out.append(text[content_indent:] if len(text) > content_indent else "")
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out) + "\n"

    lines = []
    while index[0] < len(raw):
        number = index[0] + 1
        text = raw[index[0]]
        index[0] += 1
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = indent_of(text)
        block = None
        m = re.match(r"(- )?(?:(%s): )?([|>].*)$" % KEY_PATTERN, stripped)
        if m and (m.group(1) or m.group(2)):
            if m.group(2) is None:
                raise YamlSubsetError("%s:%d a block scalar as a sequence item is outside the YAML subset" % (path, number))
            if m.group(3) != "|":
                raise YamlSubsetError("%s:%d block scalar indicator %r is outside the YAML subset (only `|`)" % (path, number, m.group(3)))
            block = read_block(indent + (2 if m.group(1) else 0), number)
        lines.append((indent, stripped, number, block))

    def scalar(text, number):
        if text == "null":
            return None
        if text == "true":
            return True
        if text == "false":
            return False
        if text == "[]":
            return []
        if text == "{}":
            return {}
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            body = text[1:-1]
            out, i = [], 0
            while i < len(body):
                ch = body[i]
                if ch == "\\":
                    i += 1
                    if i >= len(body):
                        raise YamlSubsetError("%s:%d dangling escape" % (path, number))
                    if body[i] not in '\\"':
                        raise YamlSubsetError("%s:%d escape \\%s is outside the YAML subset (only \\\\ and \\\")" % (path, number, body[i]))
                    out.append(body[i])
                else:
                    out.append(ch)
                i += 1
            return "".join(out)
        raise YamlSubsetError("%s:%d cannot read scalar %r (only double-quoted strings, integers, booleans, null, [], {})" % (path, number, text))

    pos = [0]

    def parse_block(indent):
        if pos[0] >= len(lines):
            return OrderedDict()
        if lines[pos[0]][1].startswith("- "):
            return parse_list(indent)
        return parse_map(indent)

    def parse_map(indent):
        result = OrderedDict()
        while pos[0] < len(lines):
            ind, text, number, block = lines[pos[0]]
            if ind < indent:
                break
            if ind > indent or text.startswith("- "):
                raise YamlSubsetError("%s:%d unexpected indentation" % (path, number))
            m = re.match(r"(%s):(?: (.*))?$" % KEY_PATTERN, text)
            if not m:
                raise YamlSubsetError("%s:%d cannot read mapping line %r" % (path, number, text))
            key, value = scalar(m.group(1), number) if m.group(1).startswith('"') else m.group(1), m.group(2)
            if key in result:
                raise YamlSubsetError("%s:%d duplicate key %s" % (path, number, key))
            pos[0] += 1
            if value is None:
                if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                    result[key] = parse_block(lines[pos[0]][0])
                elif pos[0] < len(lines) and lines[pos[0]][0] == indent and lines[pos[0]][1].startswith("- "):
                    result[key] = parse_list(indent)
                else:
                    raise YamlSubsetError("%s:%d key %s has no value" % (path, number, key))
            elif block is not None:
                result[key] = block
            else:
                result[key] = scalar(value, number)
        return result

    def parse_list(indent):
        result = []
        while pos[0] < len(lines):
            ind, text, number, block = lines[pos[0]]
            if ind < indent or not text.startswith("- "):
                break
            if ind > indent:
                raise YamlSubsetError("%s:%d unexpected indentation" % (path, number))
            item = text[2:]
            m = re.match(r"(%s):(?: (.*))?$" % KEY_PATTERN, item)
            if m:
                # a mapping item: rewrite the first line as a map line at indent+2 and parse
                lines[pos[0]] = (indent + 2, item, number, block)
                result.append(parse_map(indent + 2))
            else:
                pos[0] += 1
                result.append(scalar(item, number))
        return result

    result = parse_block(lines[0][0] if lines else 0)
    return result

def write_json(path, value):
    """Write the document beside its target, then rename it into place."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staged = "%s.staging-%d" % (path, os.getpid())
    try:
        with open(staged, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.remove(staged)

# --- PC code reading ------------------------------------------------------------------------------

FUNCTION_RE = re.compile(r"^\s*((?:(?:public|private|protected|internal|static|override|final|abstract)\s+)*)(function|property\s+get)\s+(\w+)\s*\(([^)]*)\)", re.M)
CLASS_RE = re.compile(r"^\s*(?:@\w+\s*)*(?:(?:public|abstract|final|internal)\s+)*class\s+(\w+)(?:<[^>]*>)?\s+extends\s+([\w.]+)", re.M)
CLASS_DECL_RE = re.compile(r"^\s*(?:@\w+\s*)*((?:(?:public|private|protected|internal|abstract|final|static)\s+)*)(class|enhancement)\s+(\w+)(?:<[^>\n]*>)?(?:\s+extends\s+([\w.]+))?", re.M)
CALL_RE = re.compile(r"(?:\b(\w+)\s*\.\s*)?\b(\w+)\s*\(")
PROPERTY_RE = re.compile(r"\b(\w+)\s*\.\s*(\w+)\b(?![ \t]*[(\w])")
STRING_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
# A bare or `Typelist.`-qualified typecode literal, e.g. `TC_TAXSURCHARGE` or
# `Typelist.TC_TAXSURCHARGE`; group("code") is always the bare TC_X spelling.
TC_LITERAL_RE = re.compile(r"^(?:[\w.]*\.)?(?P<code>TC_\w+)$")
# PC code constructors are declared `construct(...)`, not `function construct(...)`, so
# method_blocks/FUNCTION_RE never see them. This rewrites a constructor declaration line into a
# synthetic `function __construct__(...)` line (same line count, so line numbers still line up)
# so the existing function-body brace-matcher can be reused for constructors too.
CONSTRUCT_HEADER_RE = re.compile(r"(?m)^(\s*)construct(\s*\()")


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def structural_text(text):
    """Blank comments and strings without moving any source offset or line."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*',
                  lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def method_blocks(text):
    """[(name, params, modifiers, start_line, end_line, body)] for every function / property getter,
    bodies delimited by brace matching from the declaration line."""
    lines = text.split("\n")
    structural = structural_text(text)
    structure_lines = structural.split("\n")
    out = []
    for m in FUNCTION_RE.finditer(structural):
        start = text.count("\n", 0, m.start(2)) + 1  # the line of the `function` keyword, not of a preceding blank line
        depth, opened = 0, False
        end = start
        paren, paren_seen, signature_done = 0, False, False
        for j in range(start - 1, len(lines)):
            t = structure_lines[j]
            if not opened and not signature_done:
                # a signature may span several lines; it is bodiless (abstract / interface) only
                # when its closing parenthesis is followed by no `{` on that line or the next
                paren += t.count("(") - t.count(")")
                paren_seen = paren_seen or "(" in t
                if paren_seen and paren <= 0:
                    signature_done = True
                    rest = t.rsplit(")", 1)[-1] if ")" in t else t
                    if "{" not in rest:
                        k = j + 1
                        while k < len(lines) and not structure_lines[k].strip():
                            k += 1
                        if k >= len(lines) or not structure_lines[k].lstrip().startswith("{"):
                            end = start
                            break
            depth += t.count("{") - t.count("}")
            opened = opened or "{" in t
            end = j + 1
            if opened and depth <= 0:
                break
        body = "\n".join(lines[start - 1:end])
        out.append(OrderedDict([("name", m.group(3)), ("params", text[m.start(4):m.end(4)]), ("modifiers", m.group(1)),
                                ("kind", "property" if m.group(2).startswith("property") else "function"),
                                ("start", start), ("end", end), ("body", body)]))
    return out


def class_spans(text):
    """[(name, extends_simple_name, abstract, start_line, end_line)] for every class or enhancement
    declaration in a PC code file, brace-matched from the declaration line; inner classes included."""
    structural = structural_text(text)
    out = []

    def closing_brace(opening):
        depth = 1
        for j in range(opening + 1, len(structural)):
            depth += (structural[j] == "{") - (structural[j] == "}")
            if depth == 0:
                return j
        return len(structural)

    for m in CLASS_DECL_RE.finditer(structural):
        start = text.count("\n", 0, m.start(2)) + 1
        opening = structural.find("{", m.end())
        end = text.count("\n", 0, closing_brace(opening)) + 1 if opening >= 0 else start
        ext = m.group(4).split("<")[0].split(".")[-1] if m.group(4) else None
        out.append((m.group(3), ext, "abstract" in (m.group(1) or ""), start, end))
    # Anonymous implementations have no source class name. Use an explicitly synthetic lexical
    # identity, located at the constructor expression, and retain its declared base type.
    for m in re.finditer(r"\bnew\s+([\w.]+)(?:<[^>\n]*>)?\s*\(", structural):
        depth, j = 1, m.end()
        while j < len(structural) and depth:
            depth += (structural[j] == "(") - (structural[j] == ")")
            j += 1
        while j < len(structural) and structural[j].isspace():
            j += 1
        if j >= len(structural) or structural[j] != "{":
            continue
        end_offset = closing_brace(j)
        if not FUNCTION_RE.search(structural[j + 1:end_offset]):
            continue
        start = text.count("\n", 0, m.start()) + 1
        end = text.count("\n", 0, end_offset) + 1
        outer = max((s for s in out if s[3] <= start and end <= s[4]),
                    key=lambda s: s[3], default=None)
        base = m.group(1).split(".")[-1]
        column = m.start() - text.rfind("\n", 0, m.start())
        name = "%s@anonymous-L%d-C%d" % (outer[0] if outer else base, start, column)
        out.append((name, base, False, start, end))
    return out


def method_key(block, siblings):
    """Keep ordinary keys stable; distinguish overloads and remaining source-site collisions."""
    peers = [b for b in siblings if b["cls"] == block["cls"] and b["name"] == block["name"]]
    key = "%s.%s" % (block["cls"], block["name"])
    signature = ",".join(t for _, t in param_types(block["params"]))
    if len(peers) > 1:
        key += "(%s)" % signature
        if sum(",".join(t for _, t in param_types(b["params"])) == signature for b in peers) > 1:
            key += "@L%d" % block["start"]
    return key


def tagged_blocks(path, text):
    """method_blocks(text) with each block tagged by its declaring class (the innermost class span
    containing it) and file, plus the class spans themselves."""
    spans = class_spans(text)
    blocks = method_blocks(text)
    for b in blocks:
        inner = None
        for span in spans:
            if span[3] <= b["start"] <= span[4] and (inner is None or span[3] > inner[3]):
                inner = span
        b["cls"] = inner[0] if inner else os.path.basename(path).rsplit(".", 1)[0]
        b["path"] = path
    return blocks, spans


def param_types(params):
    """'cov : BAOwnedLiabilityCov, veh : BusinessVehicle' -> [('cov', 'BAOwnedLiabilityCov'), ...]"""
    out = []
    for piece in re.split(r",(?![^<(]*[>)])", params):
        piece = piece.strip()
        if not piece or ":" not in piece:
            continue
        name, typ = piece.split(":", 1)
        typ = typ.strip().split("<")[0].split(".")[-1].strip()
        out.append((name.strip(), typ))
    return out


def top_level_pieces(text, angles=True):
    """[(start offset, piece verbatim)] for each top-level comma-separated piece of text. With
    `angles`, `<`/`>` nest like brackets (PC code generic arguments); a Map literal's entries need it
    off, since a `TC_X->expr` entry's own `->` would read as a generic-argument close."""
    opening, closing = ("([{<", ")]}>") if angles else ("([{", ")]}")
    out, depth, start = [], 0, 0
    for j, ch in enumerate(text):
        if ch in opening:
            depth += 1
        elif ch in closing:
            depth -= 1
        elif ch == "," and depth == 0:
            out.append((start, text[start:j]))
            start = j + 1
    out.append((start, text[start:]))
    return out


def split_args(arg_text):
    """A call's top-level arguments, stripped; a blank last piece is no argument."""
    pieces = [piece for _, piece in top_level_pieces(arg_text)]
    return [piece.strip() for piece in pieces[:-1]] + ([pieces[-1].strip()] if pieces[-1].strip() else [])


def split_map_entries(text):
    """Top-level comma splits of a Map literal's inner text (no surrounding braces), verbatim."""
    pieces = [piece for _, piece in top_level_pieces(text, angles=False)]
    return pieces[:-1] + ([pieces[-1]] if pieces[-1].strip() else [])


def call_arg_offsets(text, open_idx, angles):
    """text[open_idx] is a call's '(': the offset of each top-level argument's first non-blank
    character, split as split_args (angles) or split_map_entries (not) splits them."""
    inner = text[open_idx + 1:paren_end(text, open_idx) - 1]
    return [open_idx + 1 + start + len(piece) - len(piece.lstrip()) for start, piece in top_level_pieces(inner, angles)]


def brace_span(text, open_idx):
    """text[open_idx] is '{'; the balanced (inner_text, index_just_past_the_matching_'}')."""
    j = paren_end(text, open_idx)
    return text[open_idx + 1:j - 1], j


def parse_map_literal_entries(inner, base_offset):
    """[(TC_X, valueExpr, offset)] for a Map<CalcRoutineParamName, Object> literal's inner text
    (already stripped of the surrounding braces). offset is base_offset + the value expression's
    position within the original text base_offset was measured from, or None when base_offset is
    None (the literal sits in another block's text, where no lambda-scope position is needed)."""
    out, pos = [], 0
    for item in split_map_entries(inner):
        idx = item.find("->")
        if idx == -1:
            pos += len(item) + 1
            continue
        key = item[:idx].strip().split(".")[-1]
        val_raw = item[idx + 2:]
        val = val_raw.strip()
        if val:
            entry_pos = None if base_offset is None else base_offset + pos + idx + 2 + (len(val_raw) - len(val_raw.lstrip()))
            out.append((key, val, entry_pos))
        pos += len(item) + 1
    return out


def call_args(body, name):
    """Argument lists of every `name(...)` call in body, with the 1-based line offset of each."""
    out = []
    for m in re.finditer(r"\b%s\s*\(" % re.escape(name), body):
        i = paren_end(body, m.end() - 1)
        out.append((split_args(body[m.end():i - 1]), body.count("\n", 0, m.start())))
    return out


def normalize(text):
    return re.sub(r"\s+", " ", blank_comments(text)).strip()


def method_source(body):
    """A method body for `source: |`: the declaration line through the closing brace, verbatim
    except that the common leading indentation is removed (and, when the declaration line is
    still indented, its own leading spaces), so a reader gets the same text whatever the method's
    indentation in its file. Space-only lines become empty; the text ends with exactly one line feed."""
    lines = [line.expandtabs(2) if "\t" in line else line for line in body.split("\n")]
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    common = min(indents) if indents else 0
    out = [line[common:] if line.strip() else "" for line in lines]
    if out and out[0].startswith(" "):
        out[0] = out[0].lstrip(" ")
    return "\n".join(out).rstrip("\n") + "\n"


def ordered_unit(unit):
    """The unit mapping in the document's key order, without the keys a unit of its kind does
    not carry."""
    out = OrderedDict()
    for key in UNIT_KEY_ORDER:
        if key in unit:
            out[key] = unit[key]
    return out


def last_segment(name):
    """The bare class name of a possibly fully-qualified engineType, e.g.
    `com.covcloud.rating.gl.GLExternalLineRatingEngine` -> `GLExternalLineRatingEngine`. A bare
    name is returned unchanged. Used only for class/file lookups; the document keeps the verbatim
    string."""
    return name.rsplit(".", 1)[-1] if name else name


def engine_selection_matches(selected, engine_type):
    """True when a --engine/decisions selection names a drafted engineSelections row, allowing for
    one side (or both) being fully qualified: exact match, the row's last dot-segment equal to the
    selected value, or the selected value's last dot-segment equal to the row's last dot-segment."""
    if selected == engine_type:
        return True
    if last_segment(engine_type) == selected:
        return True
    if last_segment(selected) == last_segment(engine_type):
        return True
    return False


# --- The statement walker: guards and loops per statement, for the call edges ----------

IF_RE = re.compile(r"^(?:\}\s*)?(else\s+)?if\s*\((.*)\)\s*\{?\s*$", re.S)
ELSE_RE = re.compile(r"^\}?\s*else\s*\{?\s*$")
FOR_RE = re.compile(r"^for\s*\(\s*(\w+)\s+in\s+(.*?)\s*\)\s*\{?\s*$", re.S)
FOR_INDEX_RE = re.compile(r"^for\s*\(\s*(\w+)\s+in\s+(.*?)\s+index\s+\w+\s*\)\s*\{?\s*$", re.S)
EACH_RE = re.compile(r"^(.*?)\.(each|eachWithIndex|map|where|firstWhere|hasMatch|allMatch|sum|forEach)\s*\(\s*\\\s*(\w+)\s*(?:,\s*\w+\s*)?->\s*\{?\s*$", re.S)
PARALLEL_RE = re.compile(r"^rateInParallel\s*\(\s*(.*?)\s*,\s*\\\s*(\w+)\s*->\s*\{?\s*$", re.S)
LAMBDA_TAIL_RE = re.compile(r"\\\s*(\w+)?\s*(?::\s*[\w.<>]+)?\s*->\s*\{\s*$")
INLINE_EACH_RE = re.compile(r"^(.*?)\.(each|eachWithIndex|forEach)\s*\(\s*\\\s*(\w+)\s*(?:,\s*\w+\s*)?->\s*(.+?)\s*\)?\s*$", re.S)
INLINE_PARALLEL_RE = re.compile(r"^rateInParallel\s*\(\s*(.*?)\s*,\s*\\\s*(\w+)\s*->\s*(.+?)\s*\)?\s*$", re.S)
SWITCH_RE = re.compile(r"^switch\s*\((.*)\)\s*\{?\s*$", re.S)
CASE_RE = re.compile(r"^case\s+(.*?)\s*:\s*(.*)$", re.S)
DEFAULT_RE = re.compile(r"^default\s*:\s*(.*)$", re.S)
WHILE_RE = re.compile(r"^(?:do\s*\{|while\s*\((.*)\)\s*\{?)\s*$", re.S)
USING_RE = re.compile(r"^using\s*\((.*)\)\s*\{?\s*$", re.S)
TRY_RE = re.compile(r"^try\s*\{?\s*$")
CATCH_RE = re.compile(r"^\}?\s*catch\s*\((.*)\)\s*\{?\s*$", re.S)
FINALLY_RE = re.compile(r"^\}?\s*finally\s*\{?\s*$")
RETURN_RE = re.compile(r"^(return|throw|break|continue)\b\s*(.*)$", re.S)
VAR_RE = re.compile(r"^\s*(?:final\s+)?var\s+(\w+)(?:\s*:\s*([\w.<>\[\]]+))?\s*(?:=\s*(.*))?$")
ASSIGN_RE = re.compile(r"^(?!.*\b(?:if|while|for|switch|return)\b)([\w.\[\]]+(?:\s*\.\s*\w+)*)\s*([-+*/]?=)(?!=)(.*)$")
LOG_RE = re.compile(r"\b(_?logger|_?log|PCFinancialsLogger|JobProcessLogger|PCLoggerCategory|RatingLogger)\b\s*\.\s*\w+\s*\(|\.log(Info|Debug|Warn|Error|Trace)\s*\(|\blog(Info|Debug|Warn|Error)\s*\(", re.I)
ASSERT_RE = re.compile(r"\b(assertSliceMode|assertWindowMode|assert)\s*\(")
GUARD_KINDS = ("if", "elif", "else", "case", "default", "catch")
LOOP_KINDS = ("for", "each", "parallel")
# `continue`, `return` and `throw` end the enclosing block for the statements after the guard
# they sit under; a loop or lambda body is where that search stops.
EXIT_KINDS = ("return", "throw", "continue")
EXIT_STOP_KINDS = LOOP_KINDS + ("while", "lambda")


def blank_comments(text):
    """Comments blanked to spaces, string literals kept, every offset and line unchanged."""
    return re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*',
                  lambda m: m.group(0) if m.group(0)[0] in "\"'" else re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def blank_strings(text):
    """String literal contents blanked to spaces, quotes kept, every offset unchanged."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', text)


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


def is_statement_open(joined):
    """True when a logical statement still continues on the next physical line."""
    bare = blank_strings(joined)
    if re.match(r"^\s*(?:\}\s*)?(?:case\b.*|default\s*):\s*$", bare):
        return False
    depth = bare.count("(") - bare.count(")") + bare.count("[") - bare.count("]")
    if depth > 0:
        return True
    tail = bare.rstrip()
    if not tail:
        return False
    if re.search(r"(?:[,+\-*/=<>?:.]|->|\b(?:and|or|not|as|typeis|in|new)|&&|\|\|)\s*$", tail) and not tail.endswith("{"):
        return True
    return False


def next_starts_continuation(line):
    bare = line.strip()
    if re.match(r"^(?:case\b|default\s*:)", bare):
        return False
    return bool(re.match(r"^(?:\.\w|\?|:\s|and\b|or\b|&&|\|\||\+|-\s|\*|/\s)", bare))


class Frame:
    def __init__(self, kind, text, line, depth, extra=None):
        self.kind, self.text, self.line, self.depth = kind, text, line, depth
        self.extra = extra or {}
        self.negations = []  # `not (g)` for each exit under guard g that closed inside this block

    def own_guard(self):
        """The condition this frame adds on its own, the one an exit under it negates."""
        if self.kind in ("if", "elif"):
            return self.text
        if self.kind == "else":
            return "not (%s)" % self.text
        if self.kind == "case":
            return "%s is %s" % (self.extra.get("subject", "?"), self.text)
        if self.kind == "default":
            return "%s is none of the cases" % self.extra.get("subject", "?")
        if self.kind == "catch":
            return "on exception %s" % self.text
        return None

    def guards(self):
        """`if (x)` gives x; an `else if (y)` gives `not (x)` then y; `else` gives `not (<last>)`
        after the negations of the branches before it."""
        own = self.own_guard()
        if own is None:
            return []
        return list(self.extra.get("prior", [])) + [own]


class MethodWalk:
    """One method's body as a list of statements, each with its enclosing guards and loops.
    `loop_entity(expr, bindings, line)` types the collection a loop iterates: ("entity", name),
    ("class", name) or None."""

    def __init__(self, block, loop_entity=None):
        self.block = block
        self.loop_entity = loop_entity or (lambda expr, bindings, line: None)
        self.statements = []
        self.method_negations = []
        self._last_if = None
        self._last_case_rec = None
        self._last_opened = None
        self.walk()

    def logical_lines(self):
        body = blank_comments(self.block["body"])
        lines = body.split("\n")
        # skip the signature up to and including the line holding the opening brace
        start = 0
        while start < len(lines) and "{" not in blank_strings(lines[start]):
            start += 1
        if start < len(lines):
            first = lines[start]
            cut = blank_strings(first).find("{")
            lines[start] = first[cut + 1:]
        else:
            return []
        out, i = [], start
        while i < len(lines):
            raw = lines[i]
            if not raw.strip():
                i += 1
                continue
            joined, first_line = raw.strip(), self.block["start"] + i
            j = i + 1
            while j < len(lines) and (is_statement_open(joined) or next_starts_continuation(lines[j])) and not joined.endswith("{"):
                if lines[j].strip():
                    joined += " " + lines[j].strip()
                j += 1
            out.append((first_line, joined))
            i = j
        return out

    def pop(self, stack):
        f = stack.pop()
        if f.extra.get("exits"):
            negation = "not (%s)" % f.own_guard()
            target = stack[-1].negations if stack else self.method_negations
            if negation not in target:
                target.append(negation)
        return f

    def walk(self):
        stack = []
        brace = 0
        subjects = []  # switch subjects, innermost last
        for line, text in self.logical_lines():
            bare = blank_strings(text)
            opens, closes = bare.count("{"), bare.count("}")
            if bare.lstrip().startswith("}"):
                brace -= 1
                closed_if = None
                while stack and stack[-1].depth > brace:
                    f = self.pop(stack)
                    if f.kind in ("if", "elif", "else"):
                        closed_if = f
                    if f.kind == "switch" and subjects:
                        subjects.pop()
                self._last_if = closed_if
                bare_rest = bare.lstrip()[1:]
                text_rest = text.strip()[1:].strip()
                opens = bare_rest.count("{")
                closes = bare_rest.count("}")
                if not text_rest or re.fullmatch(r"[)\s;]*", text_rest):
                    continue
                text = text_rest
                bare = bare_rest
            if re.fullmatch(r"[)\s;]*", text.strip()):
                brace += opens - closes
                continue
            if text.strip() == "{" and stack and stack[-1].depth == brace and self._last_opened is stack[-1]:
                brace += 1
                stack[-1].depth = brace
                continue
            stmt = self.classify(text, line, stack, subjects)
            if stmt is not None:
                self.statements.append(stmt)
            brace += opens - closes
            if stmt is not None and stmt.get("opens"):
                stmt["frame"].depth = brace
                self._last_opened = stmt["frame"]
            elif stmt is not None:
                self._last_opened = None
        # unclosed frames at the end are simply dropped

    def context(self, stack):
        guards = list(self.method_negations)
        loops = []
        for f in stack:
            guards += f.guards() + f.negations
            if f.kind in LOOP_KINDS:
                loops.append(f.extra["loop"])
        return list(OrderedDict.fromkeys(guards)), loops

    def loop(self, var, expr, parallel, stack, line=None):
        bindings = {f.extra["loop"]["var"]: f.extra["loop"]["entity"]
                    for f in stack if f.kind in LOOP_KINDS and f.extra["loop"].get("entity")}
        typ = self.loop_entity(expr, bindings, line)
        loop = OrderedDict([("var", var), ("expr", expr), ("entity", typ[1] if typ and typ[0] == "entity" else None)])
        if typ and typ[0] == "class":
            loop["class"] = typ[1]
        loop["parallel"] = parallel
        return loop

    def classify(self, text, line, stack, subjects):
        t0 = text.strip()
        if stack and stack[-1].kind in ("case", "default") and not re.match(r"^(?:case\b|default\s*:)", t0):
            stack[-1].extra["fresh"] = False
        guards, loops = self.context(stack)
        rec = OrderedDict([("line", line), ("kind", None), ("text", squash(text)), ("guards", guards), ("loops", loops)])
        t = text.strip()
        m = IF_RE.match(t)
        if m:
            kind = "elif" if m.group(1) else "if"
            f = Frame(kind, squash(m.group(2)), line, None)
            if kind == "elif" and self._last_if is not None:
                prev = self._last_if
                f.extra["prior"] = list(prev.extra.get("prior", [])) + ["not (%s)" % prev.text]
            rec.update(kind=kind, text=squash(m.group(2)), opens=True, frame=f)
            stack.append(f)
            return rec
        if ELSE_RE.match(t):
            prev = self._last_if
            f = Frame("else", prev.text if prev else "?", line, None, {"prior": list(prev.extra.get("prior", [])) if prev else []})
            rec.update(kind="else", text="", opens=True, frame=f)
            stack.append(f)
            return rec
        m = FOR_INDEX_RE.match(t) or FOR_RE.match(t)
        if m:
            f = Frame("for", squash(m.group(2)), line, None, {"loop": self.loop(m.group(1), squash(m.group(2)), False, stack, line)})
            rec.update(kind="for", text=squash(m.group(2)), opens=True, frame=f)
            stack.append(f)
            return rec
        m = PARALLEL_RE.match(t)
        if m:
            f = Frame("parallel", squash(m.group(1)), line, None, {"loop": self.loop(m.group(2), squash(m.group(1)), True, stack, line)})
            rec.update(kind="parallel", text=squash(m.group(1)), opens=True, frame=f)
            stack.append(f)
            return rec
        m = EACH_RE.match(t)
        if m:
            f = Frame("each", squash(m.group(1)), line, None, {"loop": self.loop(m.group(3), squash(m.group(1)), False, stack, line)})
            rec.update(kind="each", text=squash(m.group(1)), opens=True, frame=f)
            stack.append(f)
            return rec
        m = SWITCH_RE.match(t)
        if m:
            f = Frame("switch", squash(m.group(1)), line, None)
            subjects.append(squash(m.group(1)))
            rec.update(kind="switch", text=squash(m.group(1)), opens=True, frame=f)
            stack.append(f)
            return rec
        m = CASE_RE.match(t) or DEFAULT_RE.match(t)
        if m and subjects:
            is_default = t.startswith("default")
            label = "" if is_default else squash(m.group(1))
            tail0 = (m.group(2) if not is_default else m.group(1)) or ""
            # `case A:` directly under `case B:` with nothing between: one guard, `S is A or B`
            if stack and stack[-1].kind == "case" and stack[-1].extra.get("fresh") and not is_default:
                stack[-1].text += " or " + label
                if self._last_case_rec is not None:
                    self._last_case_rec["text"] += ", " + label
                if tail0.strip():
                    stack[-1].extra["fresh"] = False
                    return self.classify(tail0, line, stack, subjects)
                return None
            # a case replaces the previous case frame under the same switch
            while stack and stack[-1].kind in ("case", "default"):
                self.pop(stack)
            f = Frame("default" if is_default else "case", label, line, (stack[-1].depth if stack else 0), {"subject": subjects[-1], "fresh": True})
            rec.update(kind="default" if is_default else "case", text=label)
            stack.append(f)
            self._last_case_rec = rec
            if tail0.strip():
                f.extra["fresh"] = False
                self.statements.append(rec)
                return self.classify(tail0, line, stack, subjects)
            return rec
        m = WHILE_RE.match(t)
        if m:
            f = Frame("while", squash(m.group(1) or "do"), line, None)
            rec.update(kind="while", text=squash(m.group(1) or ""), opens=True, frame=f)
            stack.append(f)
            return rec
        m = USING_RE.match(t)
        if m:
            f = Frame("using", squash(m.group(1)), line, None)
            rec.update(kind="using", text=squash(m.group(1)), opens=True, frame=f)
            stack.append(f)
            return rec
        if TRY_RE.match(t):
            f = Frame("try", "", line, None)
            rec.update(kind="try", text="", opens=True, frame=f)
            stack.append(f)
            return rec
        m = CATCH_RE.match(t)
        if m:
            f = Frame("catch", squash(m.group(1)), line, None)
            rec.update(kind="catch", text="", opens=True, frame=f)
            stack.append(f)
            return rec
        if FINALLY_RE.match(t):
            f = Frame("finally", "", line, None)
            rec.update(kind="finally", text="", opens=True, frame=f)
            stack.append(f)
            return rec
        # a call whose last argument is a block lambda: `foo(x, \ y -> {`
        if LAMBDA_TAIL_RE.search(blank_strings(t)) and t.rstrip().endswith("{"):
            lm = LAMBDA_TAIL_RE.search(t)
            prefix = t[:lm.start()].rstrip().rstrip(",") if lm else t
            var = lm.group(1) if lm else None
            mp = re.search(r"\brateInParallel\s*\(\s*(.*)$", prefix, re.S)
            me = re.match(r"^(.*?)\.(each|eachWithIndex|forEach)\s*\($", prefix.strip(), re.S)
            if mp and var:
                expr = squash(mp.group(1))
                f = Frame("parallel", expr, line, None, {"loop": self.loop(var, expr, True, stack, line)})
                rec.update(kind="parallel", text=expr, opens=True, frame=f)
                stack.append(f)
                return rec
            if me and var:
                expr = squash(me.group(1))
                f = Frame("each", expr, line, None, {"loop": self.loop(var, expr, False, stack, line)})
                rec.update(kind="each", text=expr, opens=True, frame=f)
                stack.append(f)
                return rec
            f = Frame("lambda", squash(prefix), line, None)
            rec.update(kind="lambda", text=squash(prefix), opens=True, frame=f)
            stack.append(f)
            return rec
        # an inline lambda loop on one line: `vehicles.each(\ veh -> rateLineCoverage(cov, veh))`
        mi = INLINE_EACH_RE.match(t) or INLINE_PARALLEL_RE.match(t)
        if mi and not t.rstrip().endswith("{"):
            parallel = mi.re is INLINE_PARALLEL_RE
            expr, var, body = (mi.group(1), mi.group(2), mi.group(3)) if parallel else (mi.group(1), mi.group(3), mi.group(4))
            rec.update(kind="each-inline", text=squash(expr), body=squash(body),
                       bodyLoops=loops + [self.loop(var, squash(expr), parallel, stack, line)])
            return rec
        if t == "{":
            f = Frame("block", "", line, None)
            rec.update(kind="block", text="", opens=True, frame=f)
            stack.append(f)
            return rec
        m = VAR_RE.match(t)
        if m:
            rec.update(kind="var", text=squash(m.group(3) or ""))
            return rec
        m = RETURN_RE.match(t)
        if m:
            rec.update(kind=m.group(1), text=squash(m.group(2)))
            if m.group(1) in EXIT_KINDS:
                for f in reversed(stack):
                    if f.kind in EXIT_STOP_KINDS:
                        break
                    if f.kind in GUARD_KINDS:
                        f.extra["exits"] = True
                        break
            if m.group(1) == "break" and stack and stack[-1].kind in ("case", "default"):
                self.pop(stack)
            return rec
        m = ASSIGN_RE.match(t)
        if m and "==" not in m.group(2) and not re.match(r"^\w+\s*\(", t):
            rec.update(kind="assign")
            return rec
        if LOG_RE.search(t):
            rec.update(kind="log", text="")
            return rec
        if ASSERT_RE.search(t):
            rec.update(kind="assert", text="")
            return rec
        rec.update(kind="call" if "(" in t else "expr")
        return rec


# --- Dataflow facts: what each method reads and writes outside the ratebook routine ------------

FACT_LISTS = ("entityReads", "engineMembers", "entityWrites", "beanInserts", "crossInstance", "cacheAccess", "unresolved")
GOSU_KEYWORDS = {"and", "or", "not", "new", "var", "return", "if", "else", "for", "in", "index", "while", "do",
                 "switch", "case", "default", "break", "continue", "throw", "try", "catch", "finally", "using",
                 "typeis", "typeof", "statictypeof", "as", "null", "true", "false", "this", "super", "final",
                 "readonly", "function", "exists", "where", "outer", "static", "private", "public", "protected",
                 "internal", "override", "abstract", "property", "get", "set", "construct", "uses", "package",
                 "class", "enhancement", "interface", "enum", "delegate", "represents", "eval", "assert"}
# A method called on an entity receiver is classified by the enhancement that declares it: a body
# that assigns through `this` or calls a method whose name starts with one of these is a write.
MUTATOR_PREFIXES = ("add", "remove", "set", "create", "update", "delete", "clear", "insert", "put", "attach",
                    "detach", "merge", "copy", "init", "apply", "link", "unlink", "reset", "replace", "assign")
# Collection operations on an entity array, a PC class list or the CostDatas list: the element
# type passes to the lambda parameter, to the result (KEEP), or to the single element (ELEMENT).
COLLECTION_KEEP = {"where", "whereTypeIs", "filter", "toList", "toArray", "toSet", "orderBy", "orderByDescending",
                   "thenBy", "thenByDescending", "sortBy", "sortByDescending", "reverse", "distinct", "copy",
                   "subList", "union", "intersect", "disjunction", "concat", "freeze", "take", "drop"}
COLLECTION_ELEMENT = {"first", "last", "single", "firstWhere", "lastWhere", "singleWhere", "atMostOne",
                      "atMostOneWhere", "maxBy", "minBy", "get", "elementAt"}
COLLECTION_PROPERTIES = {"HasElements", "Count", "Empty", "IsEmpty", "length", "size", "Length", "Size"}
COLLECTION_AGGREGATES = {"sum", "partition", "countWhere", "count", "Count", "average", "max", "min", "reduce",
                         "fold", "groupBy", "maxBy", "minBy", "HasElements", "allMatch", "hasMatch", "sumBy"}
JAVA_VALUE_TYPES = JAVA_SCALAR_TYPES | {"Date", "BigDecimal", "MonetaryAmount", "Number", "Key", "Class",
                                        "Iterable", "Collection", "Future", "Runnable", "Thread"}
COLLECTION_TYPES = {"List", "Set", "Collection", "Iterable", "LinkedList", "ArrayList", "HashSet", "TreeSet",
                    "LinkedHashSet", "ImmutableList", "ImmutableSet"}
MAP_TYPES = {"Map", "HashMap", "LinkedHashMap", "TreeMap", "ImmutableMap"}
IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")
# A class-level field: `var _x : T as readonly Name = init`, `var _x = new T()`, `static var x : T`.
FIELD_RE = re.compile(r"\bvar\s+(\w+)\s*(?::\s*([\w.]+(?:\s*<[^=\n]*?>)?(?:\[\])?))?\s*(?:\bas\s+(?:readonly\s+)?(\w+))?\s*(?:=|$)")


def chain_text(root, segments):
    """`root.a.b(...)`: a chain as an expression, call arguments elided."""
    return root + "".join("." + n + ("(...)" if a is not None else "") for n, a, _ in segments)


def generic_args(text):
    """['A', 'B<C>'] of a `<A, B<C>>` text."""
    text = text.strip()
    if not text.startswith("<") or not text.endswith(">"):
        return []
    return split_args(text[1:-1])


def split_type(text):
    """('List', ['CPBuildingCovCostData']) for `java.util.List<CPBuildingCovCostData>`; the base
    is the last dot segment, `X[]` reads as List<X>."""
    text = (text or "").strip()
    if text.endswith("[]"):
        return "List", [text[:-2]]
    m = re.match(r"([\w.]+)\s*(<.*>)?\s*$", text, re.S)
    if not m:
        return None, []
    return m.group(1).split(".")[-1], generic_args(m.group(2) or "")


def angle_end(text, j):
    """text[j] is '<': the index just past the balanced '>' (or j when it does not balance on
    identifier, dot, comma, space and nested angle characters only - a comparison, not a type)."""
    depth, k = 0, j
    while k < len(text):
        ch = text[k]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                return k + 1
        elif not (ch.isalnum() or ch in "_.,? []\n"):
            return j
        k += 1
    return j


def paren_end(text, j):
    """text[j] is '(' (or '[' / '{'): the index just past its balanced close."""
    opening = text[j]
    closing = {"(": ")", "[": "]", "{": "}"}[opening]
    depth, k = 0, j
    while k < len(text):
        if text[k] == opening:
            depth += 1
        elif text[k] == closing:
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return len(text)


def scan_text(body):
    """The body with comments and string contents blanked, offsets unchanged, except that a
    `${...}` template inside a string keeps its expression (it is PC code that runs)."""
    out = list(structural_text(body))
    for m in re.finditer(r'"(?:\\.|[^"\\])*"', re.sub(r"/\*.*?\*/|//[^\n]*", lambda c: " " * len(c.group(0)), body, flags=re.S)):
        for t in re.finditer(r"\$\{([^{}]*)\}", m.group(0)):
            start = m.start() + t.start(1)
            out[start:start + len(t.group(1))] = list(t.group(1))
    return "".join(out)


class Dataflow:
    """Per-method dataflow facts, read mechanically from one PC code method body: entity property
    reads typed through the product capture, reads, writes and calls of the members of the
    method's class and its base classes (the engine members, for an engine method), entity
    writes, bean inserts, collection reads (classified as cross-instance once the loops each unit
    runs under are known) and FX-rate and rate-book access. Whatever it cannot type or classify is
    an `unresolved` row naming the expression and the reason; nothing is dropped silently."""

    def __init__(self, ex):
        self.ex = ex
        self.pm = ex.pm
        self.line_entity = ex.pm["line"]["policyLineSubtype"]
        self.class_cache = {}
        self.member_cache = {}
        self.enhancements = None
        self.fx_properties = None
        self.block_facts = {}
        self.header_cache = {}
        self.cost_data_cache = {}

    # -- types ------------------------------------------------------------------------------------

    def entity_of_name(self, name):
        """The captured entity a PC code type name denotes: an entity, the line pattern's productmodel
        type (its policyLineSubtype) or a clause pattern's productmodel type (its *Subtype)."""
        if name in self.pm["entities"]:
            return name
        if name == self.pm["line"]["codeIdentifier"]:
            return self.line_entity
        clause = self.pm["clauses"].get(name)
        if clause and clause.get("entity"):
            return clause["entity"]
        return None

    def class_source(self, name):
        """(path, text) of a PC class under gsrc, or None."""
        if name not in self.class_cache:
            path = self.ex.class_index.get(name)
            self.class_cache[name] = (path, read_text(path)) if path else None
        return self.class_cache[name]

    def class_header(self, name):
        """(type parameter names, extends simple name, extends generic args) of a gsrc class."""
        if name not in self.header_cache:
            self.header_cache[name] = self.read_class_header(name)
        return self.header_cache[name]

    def read_class_header(self, name):
        src = self.class_source(name)
        if not src:
            return [], None, []
        text = structural_text(src[1])
        m = re.search(r"\b(?:class|interface)\s+%s\b" % re.escape(name), text)
        if not m:
            return [], None, []
        j = m.end()
        while j < len(text) and text[j] in " \t\n":
            j += 1
        params = []
        if j < len(text) and text[j] == "<":
            end = angle_end(text, j)
            params = [p.split()[0] for p in generic_args(text[j:end]) if p.split()]
            j = end
        em = re.match(r"\s*extends\s+([\w.]+)", text[j:])
        if not em:
            return params, None, []
        k = j + em.end()
        while k < len(text) and text[k] in " \t\n":
            k += 1
        args = generic_args(text[k:angle_end(text, k)]) if k < len(text) and text[k] == "<" else []
        return params, em.group(1).split(".")[-1], args

    def is_cost_data(self, name):
        if name not in self.cost_data_cache:
            cur, seen, found = name, set(), False
            while cur and cur not in seen:
                seen.add(cur)
                if cur == "CostData":
                    found = True
                    break
                cur = self.class_header(cur)[1]
            self.cost_data_cache[name] = found
        return self.cost_data_cache[name]

    def type_of_decl(self, text, subst=None):
        """A type descriptor for a declared PC code type, generic parameters substituted."""
        subst = subst or {}
        base, args = split_type(text)
        if base is None:
            return None
        if base in subst:
            return self.type_of_decl(subst[base])
        if base in COLLECTION_TYPES:
            elem = self.type_of_decl(args[0], subst) if args else None
            if elem and elem[0] == "entity":
                return ("entities", elem[1])
            if elem and elem[0] == "class":
                return ("classes", elem[1])
            if elem and elem[0] == "costData":
                return ("costDatas", None)
            return ("value", base)
        if base in MAP_TYPES:
            return ("map", self.type_of_decl(args[1], subst) if len(args) > 1 else None)
        if "FXRate" in base:
            return ("fx", base)
        if base == "RateBook":
            return ("rateBook", base)
        entity = self.entity_of_name(base)
        if entity:
            return ("entity", entity)
        if base == "CostData" or (base in self.ex.class_index and self.is_cost_data(base)):
            return ("costData", base)
        if base in self.ex.class_index:
            return ("class", base)
        if base in JAVA_VALUE_TYPES or base in self.pm["typelists"]:
            return ("value", base)
        return ("unknownType", base)

    def entity_member(self, entity, name):
        """(kind, target entity or None, declaring entity) of a member of a captured entity, its
        supertypes and the entities it implements; ('branch', ...) for the effdated `Branch`;
        ('term', ...) for a generated `<CovTermCode>Term` property of a coverage entity."""
        for cur in self.entity_lineage(entity):
            rec = self.pm["entities"].get(cur)
            if rec is None:
                continue
            for f in rec["fields"]:
                if f["name"] == name:
                    return f["kind"], f.get("targetEntity"), cur
            if name == "Branch" and rec.get("branchType"):
                return "branch", rec["branchType"], cur
        if name.endswith("Term"):
            for clause in self.pm["clauses"].values():
                if clause.get("entity") and (clause["entity"] == entity or clause["entity"] in self.entity_chain(entity)) \
                        and any(t["codeIdentifier"] + "Term" == name for t in clause["terms"]):
                    return "term", None, entity
        return None

    def entity_lineage(self, entity):
        """The entity, then its supertypes and the entities it implements, breadth first, each once.
        A name the capture does not hold is yielded but not expanded."""
        seen, queue = set(), [entity]
        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            yield cur
            rec = self.pm["entities"].get(cur)
            if rec:
                queue += ([rec["supertype"]] if rec.get("supertype") else []) + list(rec.get("delegates", []))

    def entity_chain(self, entity):
        out, cur = [], self.pm["entities"].get(entity, {}).get("supertype")
        while cur and cur not in out:
            out.append(cur)
            cur = self.pm["entities"].get(cur, {}).get("supertype")
        return out

    def related(self, a, b):
        return a == b or a in self.entity_chain(b) or b in self.entity_chain(a)

    def enhancement_method(self, entity, method):
        """(path, line, body) of `function <method>` in a gsrc enhancement of the entity, its
        supertypes or the entities it implements, or None."""
        if self.enhancements is None:
            self.enhancements = {}
            skip = {"gclasses", "idea-gclasses", "build", ".gradle"}
            for dirpath, dirnames, names in os.walk(self.ex.gsrc):
                dirnames[:] = sorted(d for d in dirnames if d not in skip)
                for n in sorted(names):
                    if not n.endswith(".gsx"):
                        continue
                    p = os.path.join(dirpath, n)
                    with open(p, encoding="utf-8", errors="replace") as handle:
                        head = handle.read()
                    m = re.search(r"^\s*enhancement\s+\w+\s*:\s*([\w.]+)", structural_text(head), re.M)
                    if m:
                        self.enhancements.setdefault(m.group(1).split(".")[-1], []).append(p)
        for target in self.entity_lineage(entity):
            for p in self.enhancements.get(target, []):
                text = read_text(p)
                for b in method_blocks(text):
                    if b["name"] == method:
                        return p, b["start"], b["body"]
        return None

    def fx_members(self):
        """CostData properties backed by a LazyFXConversion (gw/rating/CostData.gs): reading one
        converts through the FX rate cache."""
        if self.fx_properties is None:
            self.fx_properties = {}
            src = self.class_source("CostData")
            if src:
                code = structural_text(src[1])
                lazy = set(re.findall(r"\bvar\s+(\w+)\s*=\s*new\s+LazyFXConversion\b", code))
                for m in re.finditer(r"property\s+get\s+(\w+)\s*\(\s*\)[^{]*\{\s*return\s+(\w+)\.get\(\)", code):
                    if m.group(2) in lazy:
                        self.fx_properties[m.group(1)] = (src[0], code.count("\n", 0, m.start()) + 1)
        return self.fx_properties

    # -- engine (class) members -------------------------------------------------------------------

    def own_members(self, cls):
        """{name: [(kind, declared type, generic substitution, path, line, arity)]} for the members
        of a class and its gsrc base classes, most derived first: fields (and their `as` aliases),
        property getters and functions. Generic parameters map to the arguments the subclass
        passes in its `extends` clause."""
        key = ("own", cls)
        if key in self.member_cache:
            return self.member_cache[key]
        out = OrderedDict()
        self.member_cache[key] = out
        cur, subst, seen = cls, {}, set()
        while cur and cur not in seen:
            seen.add(cur)
            src = self.class_source(cur)
            if not src:
                break
            path, text = src
            code = structural_text(text)
            blocks = method_blocks(text)
            spans = class_spans(text)
            mine = next((sp for sp in spans if sp[0] == cur), None)
            inner = [sp for sp in spans if mine and sp[0] != cur and mine[3] < sp[3] <= mine[4]]
            for n, line in enumerate(code.split("\n"), 1):
                if mine and not (mine[3] <= n <= mine[4]) or any(sp[3] <= n <= sp[4] for sp in inner):
                    continue
                if any(b["start"] <= n <= b["end"] for b in blocks):
                    continue
                m = FIELD_RE.search(line)
                if m:
                    typ = m.group(2)
                    if not typ:
                        init = re.search(r"=\s*new\s+([\w.]+(?:\s*<[^()]*>)?)\s*\(", line)
                        typ = init.group(1) if init else None
                    out.setdefault(m.group(1), []).append(("field", typ, dict(subst), path, n, None))
                    if m.group(3):
                        out.setdefault(m.group(3), []).append(("property", typ, dict(subst), path, n, None))
            for b in blocks:
                if not (mine is None or mine[3] <= b["start"] <= mine[4]) or any(sp[3] <= b["start"] <= sp[4] for sp in inner):
                    continue
                decl = re.search(r"\)\s*:\s*([\w.<>\[\], ]+?)\s*(?:\{|$)", structural_text(b["body"]).split("{")[0] + "{")
                kind = "property" if b["kind"] == "property" else "function"
                out.setdefault(b["name"], []).append((kind, decl.group(1) if decl else None, dict(subst), path, b["start"],
                                                      len(split_args(b["params"])) if b["params"].strip() else 0))
            _, ext, args = self.class_header(cur)
            if not ext:
                break
            ext_params = self.class_header(ext)[0]
            nxt = {}
            for p_name, a in zip(ext_params, args):
                base, _ = split_type(a)
                nxt[p_name] = subst.get(base, a)
            subst, cur = nxt, ext
        return out

    def member(self, cls, name, arity=None):
        """The most-derived declaration of `name` on the class: (kind, type descriptor, path, line)
        or None. A function is matched by arity when one declaration has it."""
        decls = self.own_members(cls).get(name)
        if not decls:
            return None
        if arity is not None:
            funcs = [d for d in decls if d[0] == "function" and d[5] == arity]
            if funcs:
                decls = funcs
        kind, typ, subst, path, line, _ = decls[0]
        return kind, (self.type_of_decl(typ, subst) if typ else None), path, line

    def bind_class(self, typ):
        """(type name, bind kind, verify or None) for a bind value's type descriptor: an entity, a
        CostData subclass, a class under gsrc or a Java scalar; `unclassified`, with what would
        settle it, for a type that is none of these; None when the descriptor names no type."""
        if not typ or len(typ) < 2:
            return None
        kind, name = typ
        if kind == "entity":
            return name, "entity", None
        if kind == "costData":
            return name, "costData", None
        if kind in ("class", "fx", "rateBook") and name in self.ex.class_index:
            return name, "gosuClass", None
        if kind == "value" and name in JAVA_SCALAR_TYPES:
            return name, "scalar", None
        if kind == "unknownType":
            return name, "unclassified", "the product capture does not carry entity %s; confirm the entity's role/scope" % name
        if kind in ("entities", "classes", "costDatas"):
            element = name if kind != "costDatas" else "CostData"
            return "List<%s>" % element, "unclassified", (
                "a list of %s, not one value; confirm which element the routine reads" % element)
        if kind == "map":
            return "Map", "unclassified", "a map, not one value; confirm which entry the routine reads"
        if name is None:
            return None
        return name, "unclassified", ("%s is not a captured entity, a CostData subclass, a class under gsrc or a Java "
                                      "scalar; confirm how the routine reads it" % name)

    # -- one method ---------------------------------------------------------------------------------

    def facts_of(self, i):
        if i not in self.block_facts:
            self.block_facts[i] = BlockScan(self, i).run()
        return self.block_facts[i]


class BlockScan:
    """The facts of one method body. Rows are (fact list, row) pairs in source order; a collection
    read is kept as a ('collections', ...) pair for the cross-instance pass."""

    def __init__(self, df, i):
        self.df, self.ex, self.i = df, df.ex, i
        b = self.ex.blocks[i]
        self.b, self.cls = b, b["cls"]
        self.body = b["body"]
        self.text = scan_text(self.body)
        brace = self.text.find("{")
        self.start = brace + 1 if brace >= 0 else len(self.text)
        self.rows = []
        self.params = OrderedDict()
        for piece in split_args(b["params"]) if b["params"].strip() else []:
            if ":" in piece:
                name, typ = piece.split(":", 1)
                self.params[name.strip()] = typ.split("=")[0].strip()
        self.locals = OrderedDict()   # name -> (declared type text or None, initializer text or None, offset)
        self.lambdas = []             # (start, end, [names], receiver type thunk)
        self.loop_vars = OrderedDict()  # name -> (collection text, offset)
        self.typing = set()
        self.local_types = {}

    def at(self, offset):
        return self.ex.ref(self.b["path"], self.b["start"] + self.body.count("\n", 0, offset))

    def add(self, fact, row, offset):
        row["at"] = self.at(offset)
        self.rows.append((fact, row))

    def unresolved(self, fact, expression, reason, offset):
        self.add("unresolved", OrderedDict([("fact", fact), ("expression", squash(expression)), ("reason", reason)]), offset)

    # -- declarations ---------------------------------------------------------------------------

    def collect(self):
        t = self.text
        for m in re.finditer(r"\bvar\s+(\w+)\s*(?::\s*([\w.]+(?:\s*<[^=\n]*>)?(?:\[\])?))?\s*(?:=\s*([^\n]+))?", t[self.start:]):
            off = self.start + m.start()
            init = m.group(3)
            if init is None:
                later = re.search(r"^\s*%s\s*=(?!=)\s*([^\n]+)$" % re.escape(m.group(1)), t[off + m.end() - m.start():], re.M)
                init = later.group(1) if later else None
            self.locals.setdefault(m.group(1), (m.group(2), init.strip() if init else None, off))
        for m in re.finditer(r"\bfor\s*\(\s*(\w+)\s+in\s+(.*?)(?:\s+index\s+\w+)?\s*\)\s*\{?", t[self.start:]):
            self.loop_vars.setdefault(m.group(1), (m.group(2), self.start + m.start()))
        for m in re.finditer(r"\\\s*((?:\w+\s*(?::\s*[\w.<>]+)?\s*,?\s*)*)->", t[self.start:]):
            pos = self.start + m.start()
            names = [p.split(":")[0].strip() for p in m.group(1).split(",") if p.strip()]
            depth, k = 0, pos - 1
            while k >= 0:
                if t[k] in ")]}":
                    depth += 1
                elif t[k] in "([{":
                    if depth == 0:
                        break
                    depth -= 1
                k -= 1
            if k < 0 or t[k] != "(":
                continue
            end = paren_end(t, k)
            head = t[:k].rstrip()
            hm = re.search(r"(?:\.\s*(\w+)|\b(\w+))\s*(?:<[^()]*>)?\s*$", head)
            op = (hm.group(1) or hm.group(2)) if hm else None
            receiver_end = hm.start() if hm and hm.group(1) else None
            self.lambdas.append((pos, end, names, op, receiver_end, k))

    def var_type(self, name, offset):
        """The type descriptor of a local, a parameter, a loop variable or a lambda parameter in
        scope at `offset`; ('none',) when the name is none of these."""
        for start, end, names, op, receiver_end, open_idx in sorted(self.lambdas, key=lambda l: -l[0]):
            if start <= offset < end and name in names:
                return self.lambda_param_type(op, receiver_end, open_idx, names.index(name))
        if name in self.loop_vars and self.loop_vars[name][1] <= offset:
            coll = self.expr_type(self.loop_vars[name][0], self.loop_vars[name][1])
            return self.element(coll)
        if name in self.locals:
            return self.local_type(name)
        if name in self.params:
            return self.df.type_of_decl(self.params[name])
        return ("none",)

    def local_type(self, name):
        if name in self.local_types:
            return self.local_types[name]
        if name in self.typing:
            return None
        self.typing.add(name)
        declared, init, off = self.locals[name]
        typ = self.df.type_of_decl(declared) if declared else (self.expr_type(init, off) if init else None)
        self.typing.discard(name)
        self.local_types[name] = typ
        return typ

    def lambda_param_type(self, op, receiver_end, open_idx, position):
        if op == "rateInParallel":
            first = split_args(self.text[open_idx + 1:paren_end(self.text, open_idx) - 1])
            return self.element(self.expr_type(first[0], open_idx)) if first else None
        if receiver_end is None:
            return None
        receiver = self.chain_before(receiver_end)
        typ = self.expr_type(receiver, receiver_end) if receiver else None
        if typ and typ[0] == "map" and op in ("mapValues", "eachValue"):
            return typ[1]
        if position == 0 and typ and typ[0] in ("entities", "classes", "costDatas"):
            return self.element(typ)
        return None

    def chain_before(self, end):
        """The member chain text ending at `end` (the `.` before the lambda's operation)."""
        t, k = self.text, end
        while k > 0:
            ch = t[k - 1]
            if ch in ")]":
                depth, j = 0, k - 1
                while j >= 0:
                    depth += t[j] in ")]"
                    depth -= t[j] in "(["
                    if depth == 0:
                        break
                    j -= 1
                k = j
                continue
            if ch.isalnum() or ch in "_.?$":
                k -= 1
                continue
            if ch in " \t\n" and t[k:k + 1] == ".":
                k -= 1
                continue
            break
        return t[k:end].strip()

    @staticmethod
    def element(typ):
        if not typ:
            return None
        if typ[0] == "entities":
            return ("entity", typ[1])
        if typ[0] == "classes":
            return ("class", typ[1])
        if typ[0] == "costDatas":
            return ("costData", "CostData")
        return None

    def expr_type(self, expr, offset):
        """The type of an expression, without recording facts: a chain, `new T(...)`, `x as T`."""
        expr = (expr or "").strip()
        if STRING_RE.fullmatch(expr):
            return ("value", "String")
        cast = re.search(r"\bas\s+([\w.]+(?:\s*<.*>)?)\s*$", expr)
        if cast:
            declared = self.df.type_of_decl(cast.group(1))
            inner = self.expr_type(expr[:cast.start()], offset)
            if declared and inner and declared[0] == inner[0] == "costDatas":
                return inner
            return declared
        m = re.match(r"new\s+([\w.]+(?:\s*<[^()]*>)?)\s*[({]", expr)
        if m:
            return self.df.type_of_decl(m.group(1))
        m = IDENT_RE.match(expr)
        if not m:
            return None
        name = m.group(0)
        segments, end = self.segments(expr, m.end())
        q = m.end()
        if not segments and expr[q:q + 1] == "(":
            # a call result: a traversed method's declared return type
            end = paren_end(expr, q)
            segments, end2 = self.segments(expr, end)
            if expr[end2:].strip():
                return None
            rt = self.call_type(name, expr[q + 1:end - 1])
            return self.walk(name, segments, offset, record=False, root_type=rt) if segments else rt
        if expr[end:].strip():
            return None
        rt = self.root_type(name, offset)
        if not segments:
            return rt
        return self.walk(name, segments, offset, record=False, root_type=rt)

    def root_type(self, name, offset):
        """The type of a bare identifier: a variable in scope, else a member of the class."""
        local = self.var_type(name, offset)
        if local != ("none",):
            return local
        if name == "this":
            return ("class", self.cls)
        found = self.df.member(self.cls, name)
        if found:
            return self.tag(name, found[1])
        return None

    @staticmethod
    def tag(name, typ):
        """A member's CostData list keeps the member's name, so a cross-instance read of it can be told
        from a read of a list the method built itself."""
        if typ and typ[0] == "costDatas":
            return ("costDatas", name)
        return typ

    def call_type(self, name, args):
        nargs = len(split_args(args)) if args.strip() else 0
        for c in self.ex._resolve_calls(self.ex._candidates(self.i, None, name), nargs):
            decl = re.search(r"\)\s*:\s*([\w.<>\[\], ]+?)\s*\{", structural_text(self.ex.blocks[c]["body"]))
            if decl:
                return self.df.type_of_decl(decl.group(1))
        return None

    # -- chains --------------------------------------------------------------------------------------

    def segments(self, t, j):
        """[(name, call args text or None, name offset)] of the member chain starting at t[j]
        (just past its root), and the index just past it."""
        out = []
        while True:
            k = j
            while k < len(t) and t[k] in " \t":
                k += 1
            if k < len(t) and t[k] == "\n":
                n = k
                while n < len(t) and t[n] in " \t\n":
                    n += 1
                if t[n:n + 1] == "." or t[n:n + 2] == "?.":
                    k = n
            if t[k:k + 2] == "?.":
                k += 2
            elif t[k:k + 1] == "." and t[k + 1:k + 2] != ".":
                k += 1
            else:
                return out, j
            while k < len(t) and t[k] in " \t\n":
                k += 1
            m = IDENT_RE.match(t, k)
            if not m:
                return out, j
            name_off = k
            j = m.end()
            q = j
            if t[q:q + 1] == "<":
                q2 = angle_end(t, q)
                if q2 > q and t[q2:q2 + 1] == "(":
                    q = q2
            args = None
            if t[q:q + 1] == "(":
                end = paren_end(t, q)
                args = t[q + 1:end - 1]
                j = end
            out.append((m.group(0), args, name_off))

    def walk(self, root, segments, offset, record=True, write=False, root_type=None):
        """Type the chain `root.seg1.seg2...`, recording facts when `record`; the last segment is
        a write when `write`. Returns the final type descriptor or None; ('value', None) is a value
        whose type this run does not read (an entity column, a collection count, a CostData amount)."""
        df, ex = self.df, self.ex
        typ = root_type
        text = root
        expression = chain_text(root, segments)
        for idx, (name, args, off) in enumerate(segments):
            last = idx == len(segments) - 1
            text += "." + name + ("(...)" if args is not None else "")
            kind = typ[0] if typ else None
            if kind == "entity":
                entity = typ[1]
                if args is None:
                    found = df.entity_member(entity, name)
                    if found is None:
                        if record:
                            self.unresolved("entityWrite" if (write and last) else "entityRead", expression,
                                            "%s is not a member of %s, its supertypes or the entities it implements in the product capture" % (name, entity), off)
                        return None
                    mkind, target, _ = found
                    if record:
                        if write and last:
                            self.add("entityWrites", OrderedDict([("target", entity), ("property", name)]), off)
                        else:
                            self.add("entityReads", OrderedDict([("entity", entity), ("property", name)]), off)
                    if mkind in ("foreignkey", "onetoone", "edgeforeignkey"):
                        typ = ("entity", target)
                    elif mkind == "array":
                        typ = ("entities", target)
                    elif mkind == "branch":
                        typ = ("entity", target) if target in self.pm_entities() else ("unknownType", target)
                    else:
                        return ("value", None)
                    continue
                if name == "arrays" and args is not None:
                    field = args.strip().strip('"')
                    found = df.entity_member(entity, field)
                    if found and found[0] == "array":
                        if record:
                            self.add("entityReads", OrderedDict([("entity", entity), ("property", field)]), off)
                        typ = ("entities", found[1])
                        continue
                enhancement = df.enhancement_method(entity, name)
                if enhancement is None:
                    if record:
                        self.unresolved("entityCall", expression, "%s(...) on %s is declared in no gsrc enhancement of %s, its supertypes or the entities it implements; whether it reads or writes is unknown" % (name, entity, entity), off)
                    return None
                ep, eline, ebody = enhancement
                ecode = structural_text(ebody)
                mutates = re.search(r"\bthis\s*\.\s*[\w.]+\s*[-+*/]?=(?!=)", ecode) or any(
                    cm.group(1).startswith(MUTATOR_PREFIXES) for cm in re.finditer(r"\.\s*(\w+)\s*\(", ecode))
                if record:
                    row = OrderedDict([("target" if mutates else "entity", entity), ("method", name),
                                       ("declaredIn", ex.ref(ep, eline))])
                    self.add("entityWrites" if mutates else "entityReads", row, off)
                return None
            if kind in ("entities", "classes", "costDatas"):
                if record:
                    self.rows.append(("collections", OrderedDict([("type", typ), ("op", name), ("expression", expression), ("at", self.at(off))])))
                if args is None and name in COLLECTION_PROPERTIES:
                    return ("value", None)
                if name in COLLECTION_KEEP:
                    continue
                if name in COLLECTION_ELEMENT:
                    typ = self.element(typ)
                    continue
                if name == "partition":
                    typ = ("map", typ)
                    continue
                return ("value", None)
            if kind == "class":
                if args is not None:
                    if record:
                        self.unresolved("externalCall", expression, "a call to %s(...) on %s, a method this run does not traverse" % (name, typ[1]), off)
                    return None
                found = df.member(typ[1], name)
                if found is None or found[0] == "function":
                    if record:
                        self.unresolved("externalCall", expression, "%s is declared neither by %s nor by its base classes under gsrc" % (name, typ[1]), off)
                    return None
                typ = found[1]
                continue
            if kind == "costData":
                fx = df.fx_members()
                if args is None and name in fx and record:
                    self.add("cacheAccess", OrderedDict([("member", "CostData.%s" % name), ("kind", "fxRate"),
                                                         ("declaredIn", ex.ref(*fx[name]))]), off)
                return ("value", None)
            if kind == "map":
                if name in ("get",) and args is not None:
                    typ = typ[1]
                    continue
                if name in ("Values",) and args is None:
                    inner = typ[1]
                    typ = ("value", None) if not inner else inner
                    continue
                return ("value", None)
            if kind in ("value", "fx", "rateBook"):
                return ("value", None)
            if kind == "unknownType":
                if record:
                    self.unresolved("entityWrite" if (write and last) else "entityRead", expression,
                                    "the receiver's type %s is neither a captured entity nor a class under gsrc" % typ[1], off)
                return None
            if record:
                self.unresolved("entityWrite" if (write and last) else "entityRead", expression,
                                "the receiver `%s` has no type this run can establish" % text.rsplit(".", 1)[0], off)
            return None
        return typ

    def pm_entities(self):
        return self.df.pm["entities"]

    # -- the scan --------------------------------------------------------------------------------

    def blank(self, t, start, end):
        return t[:start] + re.sub(r"[^\n]", " ", t[start:end]) + t[end:]

    def run(self):
        self.collect()
        t = self.text
        scan = t
        # bean inserts and constructed types: `new T(` / `new T {` / `new T[`
        for m in re.finditer(r"\bnew\s+([\w.]+)", t[self.start:]):
            off = self.start + m.start()
            name = m.group(1).split(".")[-1]
            qualified = m.group(1)
            end = self.start + m.end()
            if t[end:end + 1] == "<":
                end = angle_end(t, end)
            self.new_site(name, qualified, off, m.start(1) + self.start)
            scan = self.blank(scan, self.start + m.start(1), end)
        # declared types, casts, type tests, lambda and catch declarations, case labels, named args
        for pattern in (r"\bvar\s+\w+\s*(:\s*[\w.]+(?:\s*<[^=\n]*?>)?(?:\[\])?)",
                        r"\b(?:as|typeis)\s+((?:readonly\s+)?[\w.]+(?:\s*<[^()\n]*>)?(?:\[\])?)",
                        r"(\\\s*(?:\w+\s*(?::\s*[\w.<>]+)?\s*,?\s*)*->)",
                        r"\bcatch\s*\(([^)]*)\)",
                        r"\bcase\s+([^:\n]*?)\s*:",
                        r"[{(,]\s*(:\w+)\s*=",
                        r"(@\w+)",
                        r"\bfor\s*\(\s*(\w+)\s+in\b",
                        r"\bvar\s+(\w+)"):
            for m in re.finditer(pattern, scan):
                if m.start(1) >= self.start:
                    scan = self.blank(scan, m.start(1), m.end(1))
        self.scan = scan
        for m in IDENT_RE.finditer(scan, self.start):
            name, off = m.group(0), m.start()
            before = scan[:off].rstrip()
            if before.endswith(".") or name in GOSU_KEYWORDS and name not in ("this", "super"):
                continue
            if name[0].isdigit():
                continue
            segments, end = self.segments(scan, m.end())
            call_args = None
            q = m.end()
            if scan[q:q + 1] == "<":
                q2 = angle_end(scan, q)
                if q2 > q and scan[q2:q2 + 1] == "(":
                    q = q2
            if scan[q:q + 1] == "(" and not segments:
                call_args = scan[q + 1:paren_end(scan, q) - 1]
            write = self.is_write(scan, end if segments or call_args is None else paren_end(scan, q))
            self.root(name, segments, call_args, off, write)
        return self.rows

    @staticmethod
    def is_write(t, end):
        rest = t[end:]
        m = re.match(r"\s*([-+*/]?=)(?!=)|\s*(\+\+|--)", rest)
        return bool(m)

    def new_site(self, name, qualified, off, name_off):
        df = self.df
        entity = df.pm["entities"].get(name) and name
        uses = re.findall(r"^\s*uses\s+([\w.]+)\s*$", structural_text(read_text(self.b["path"])), re.M)
        imported = next((u for u in uses if u.split(".")[-1] == name), None)
        if entity or qualified.startswith("entity.") or (imported or "").startswith("entity."):
            self.add("beanInserts", OrderedDict([("entity", name)]), name_off)
        elif name in self.ex.class_index or imported or name in JAVA_VALUE_TYPES or name in COLLECTION_TYPES \
                or name in MAP_TYPES or name.endswith(("Exception", "Error")) or "." in qualified:
            return
        else:
            self.unresolved("beanInsert", "new %s" % qualified, "%s is neither a captured entity nor an `entity.` import, a class under gsrc or an import of this file; whether this inserts a bean is unknown" % name, name_off)

    def root(self, name, segments, call_args, off, write):
        df, ex = self.df, self.ex
        expression = chain_text(name, segments)
        if name in ("this", "super"):
            if not segments:
                return
            member_name, args, moff = segments[0]
            self.member_access(member_name, segments[1:], args, moff, write, base_only=(name == "super"),
                               expression=expression)
            return
        if re.fullmatch(r"TC_\w+", name):
            return
        local = self.var_type(name, off)
        if local != ("none",):
            if not segments:
                return
            if local is None:
                self.unresolved("entityWrite" if write and len(segments) else "entityRead", expression,
                                "the receiver `%s` has no type this run can establish" % name, off)
                return
            self.walk(name, segments, off, write=write, root_type=local)
            return
        # a traversed method or property: a call edge, its facts roll up through the call graph
        if name in ex.by_name and self.edge_target(name, call_args):
            if call_args is not None:
                q = self.scan.find("(", off + len(name))
                after, _ = self.segments(self.scan, paren_end(self.scan, q))
                if after:
                    rt = self.call_type(name, call_args)
                    self.walk(name + "(...)", after, off, write=write, root_type=rt)
            return
        if self.member_access(name, segments, call_args, off, write, expression=expression):
            return
        if name[0].isupper() or name in df.pm["entities"]:
            self.static_access(name, segments, call_args, off, expression)
            return
        self.unresolved("engineMember", expression if call_args is None else "%s(...)" % name,
                        "`%s` is not a local, a parameter, a loop or lambda variable, a member of %s or its base classes, or a traversed method" % (name, self.cls), off)

    def edge_target(self, name, call_args):
        cands = self.ex._candidates(self.i, None, name)
        if call_args is None:
            cands = [c for c in cands if self.ex.blocks[c]["kind"] == "property"]
        return bool(cands)

    def member_access(self, name, segments, call_args, off, write, base_only=False, expression=None):
        df, ex = self.df, self.ex
        cls = self.cls
        if base_only:
            cls = df.class_header(cls)[1] or cls
        arity = None if call_args is None else (len(split_args(call_args)) if call_args.strip() else 0)
        found = df.member(cls, name, arity)
        if found is None:
            return False
        kind, typ, path, line = found
        if kind in ("function", "property") and any(
                os.path.abspath(b["path"]) == os.path.abspath(path) and b["start"] == line for b in ex.blocks):
            return True  # a traversed method or property: a call edge, its facts come through the call graph
        if self.cls.split("@")[0] != last_segment(ex.selected):
            return True  # a member of another class's own object (a CostData, a wrapper), not engine state
        typ = self.tag(name, typ)
        if call_args is not None:
            access = "call"
        elif write and not segments:
            access = "write"
        else:
            access = "read"
        declared = OrderedDict([("class", os.path.basename(path).rsplit(".", 1)[0]), ("at", ex.ref(path, line))])
        self.add("engineMembers", OrderedDict([("name", name), ("access", access), ("declaredIn", declared)]), off)
        if typ and typ[0] == "fx":
            self.add("cacheAccess", OrderedDict([("member", name), ("kind", "fxRate"), ("declaredIn", ex.ref(path, line))]), off)
        elif typ and typ[0] == "rateBook" and call_args is None:
            self.add("cacheAccess", OrderedDict([("member", name), ("kind", "rateBook"), ("declaredIn", ex.ref(path, line))]), off)
        if segments and call_args is None:
            if typ is None:
                self.unresolved("engineMember", expression or chain_text(name, segments),
                                "`%s` (%s) has no declared type this run can read, so what `.%s` reaches is unknown" % (name, ex.ref(path, line), segments[0][0]), off)
                return True
            if typ and typ[0] == "rateBook" and segments[0][0] == "selectRateBook":
                self.add("cacheAccess", OrderedDict([("member", "RateBook.selectRateBook"), ("kind", "rateBook")]), segments[0][2])
                return True
            if typ and typ[0] == "rateBook" and segments[0][0] == "executeCalcRoutine":
                return True
            self.walk(name, segments, off, write=write, root_type=typ if typ else ("unknownType", "?"))
        return True

    def static_access(self, name, segments, call_args, off, expression):
        if not segments:
            if call_args is not None:
                self.unresolved("externalCall", "%s(...)" % name, "a call to `%s`, which is no member of %s and no traversed method" % (name, self.cls), off)
            return  # a type literal used as a value
        first, args, soff = segments[0]
        if re.fullmatch(r"TC_\w+|[A-Z][A-Z0-9_]+", first) and args is None:
            return  # a typecode or constant
        if name == "RateBook" and first == "selectRateBook":
            self.add("cacheAccess", OrderedDict([("member", "RateBook.selectRateBook"), ("kind", "rateBook")]), soff)
            return
        self.unresolved("externalCall", expression, "a static %s of `%s`, code this run does not read" % ("call" if args is not None else "read", name), off)


# --- The extractor -----------------------------------------------------------------------------

class RatingExtractor:
    def __init__(self, args):
        self.args = args
        self.source_root = os.path.abspath(args.source_root)
        self.config_root = os.path.join(self.source_root, CONFIGURATION_ROOT)
        self.gsrc = os.path.join(self.config_root, "gsrc")
        self.counts = Counter()
        self.unresolved = []
        self.decisions = self.load_decisions(args.decisions) if args.decisions else {}
        self.baseline = self.open_baseline()
        self.class_index = self.index_classes()
        self.calc_routine_param_codes = self.load_calc_routine_param_codes()
        self.routine_gaps = OrderedDict()
        self.ratebook_joins = OrderedDict([("bound", []), ("missing", []), ("ambiguous", []), ("mismatches", []), ("untyped", [])])
        self.ratebook_files = []
        self.binding_scopes = {}
        self.expression_origins = []
        self.draft_notes = []  # extractor drafts the consultant confirms (summary open questions)
        self.unresolved_key_dims = []  # (emission identity, [dimension]) for the summary

    # -- inputs ---------------------------------------------------------------------------------

    def load_decisions(self, path):
        try:
            data = read_yaml_subset(path)
        except YamlSubsetError as error:
            die("decisions file: %s" % error)
        except OSError as error:
            die("decisions file: cannot read %s: %s" % (path, error))
        if not isinstance(data, dict):
            die("decisions file: %s must hold a mapping" % path)
        for key in data:
            if key not in DECISION_KEYS:
                die("decisions file: unknown top-level key %r (allowed: %s)" % (key, ", ".join(DECISION_KEYS)))
        check_decision_locators(data, "")
        gates = data.get("assumedGates", [])
        if not isinstance(gates, list):
            die("decisions file: assumedGates must be an array")
        for gate in gates:
            if not isinstance(gate, dict) or set(gate) != {"name", "value", "at"}:
                die("decisions file: assumedGates entry %r needs exactly name, value and at" % (gate.get("name") if isinstance(gate, dict) else gate))
            if not isinstance(gate["name"], str) or not gate["name"] or not isinstance(gate["value"], str) or not gate["value"]:
                die("decisions file: assumedGates entry %r: name and value must be non-empty strings" % gate.get("name"))
            gate["at"] = gate["at"] if isinstance(gate["at"], list) else [gate["at"]]
        units = data.get("units", {})
        if not isinstance(units, dict):
            die("decisions file: units must be an object keyed by unit key")
        for key, unit in units.items():
            if not isinstance(unit, dict) or set(unit) - {"reads", "produces"}:
                die("decisions file: units.%s allows only reads and produces" % key)
            for p in unit.get("produces", []):
                if not isinstance(p, dict) or set(p) != {"kind", "name"} or p["kind"] not in ("costDataMember", "scratchValue") or not isinstance(p["name"], str) or not p["name"]:
                    die("decisions file: units.%s.produces entries are {kind: costDataMember|scratchValue, name}" % key)
        emissions = data.get("emissions", {})
        if not isinstance(emissions, dict):
            die("decisions file: emissions must be an object keyed by <unit>.<CostDataClass>")
        for key, emission in emissions.items():
            if not isinstance(emission, dict) or set(emission) - {"key", "status"}:
                die("decisions file: emissions.%s allows only key and status" % key)
            if "status" in emission and emission["status"] not in ("liveEngineEmitted", "conditionallyEmitted"):
                die("decisions file: emissions.%s.status must be liveEngineEmitted or conditionallyEmitted" % key)
            values = emission.get("key", {})
            if not isinstance(values, dict) or any(not (v is None or (isinstance(v, str) and v)) for v in values.values()):
                die("decisions file: emissions.%s.key maps each dimension to a non-empty string or null" % key)
        return data

    def ref(self, path, line=None, symbol=None):
        """One locator string `<path>[:<line>][#<symbol>]`, path relative to --source-root."""
        at = rel(path, self.source_root)
        if line:
            at += ":%d" % line
        if symbol:
            at += "#%s" % symbol
        return at

    def open_baseline(self):
        candidates = [os.path.join(self.config_root, "..", "base.zip"), os.path.join(self.source_root, "modules", "base.zip")]
        for c in candidates:
            if os.path.exists(c):
                self.baseline_path = os.path.abspath(c)
                return zipfile.ZipFile(c)
        self.baseline_path = None
        return None

    def baseline_text(self, path):
        """The vanilla text of a configuration-relative path, or None when the baseline lacks it."""
        if not self.baseline:
            return None
        inner = "base/" + rel(path, self.config_root)
        try:
            return self.baseline.read(inner).decode("utf-8", errors="replace")
        except KeyError:
            return None

    def member_status(self, path, block):
        """vanilla / customized / noBaseline for one method body; 'noBaseline' also when base.zip
        is absent, and the summary prints which baseline was read."""
        base = self.baseline_text(path)
        if base is None:
            return "noBaseline"
        for other in method_blocks(base):
            if other["name"] == block["name"] and normalize(other["params"]) == normalize(block["params"]):
                return "vanilla" if normalize(other["body"]) == normalize(block["body"]) else "customized"
        return "noBaseline"

    def load_calc_routine_param_codes(self):
        """Every CalcRoutineParamName typecode `code` attribute, base and client extension, so a
        parameterBinding's `parameter` can be confirmed rather than assumed."""
        codes = set()
        for rel_path in ("config/metadata/typelist/CalcRoutineParamName.tti",
                          "config/extensions/typelist/CalcRoutineParamName.ttx"):
            path = os.path.join(self.config_root, rel_path)
            if os.path.exists(path):
                codes.update(re.findall(r'code="([^"]+)"', read_text(path)))
        return codes

    def index_classes(self):
        """Simple class name -> .gs path, for every PC code file under gsrc (build output excluded)."""
        index = {}
        skip = {"gclasses", "idea-gclasses", "build", ".gradle"}
        for dirpath, dirnames, names in os.walk(self.gsrc):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for n in names:
                if n.endswith(".gs") or n.endswith(".gsx"):
                    index.setdefault(n.rsplit(".", 1)[0], os.path.join(dirpath, n))
        return index

    def revision(self):
        if self.args.revision:
            return self.args.revision, bool(self.args.dirty)
        try:
            # Only a source root that is the top level of its own checkout has its commit; an export
            # copied inside some other checkout must not take that checkout's revision.
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.source_root, capture_output=True, text=True, check=True).stdout.strip()
            if os.path.realpath(top) != os.path.realpath(self.source_root):
                die("--source-root is inside the git checkout %s but is not its top level; pass --revision (and --dirty if the tree differed)" % top)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.source_root, capture_output=True, text=True, check=True).stdout.strip()
            # Only the paths this run reads count: the configuration root and the baseline it opens.
            read_paths = [CONFIGURATION_ROOT] + ([rel(self.baseline_path, self.source_root)] if self.baseline_path else [])
            status = subprocess.run(["git", "status", "--porcelain", "--"] + read_paths, cwd=self.source_root, capture_output=True, text=True, check=True).stdout
            return head, bool(status.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            die("--source-root is not a git checkout; pass --revision (and --dirty if the tree differed)")

    # -- product capture ------------------------------------------------------------------------

    def read_product_capture(self, revision):
        """The lookups this extractor interprets PC code with, from <product-root>/extracted/
        product-capture.json: clause pattern codes with their cov term codes, entities with their
        supertype, delegates and members (each member's target entity for a foreign key, one-to-one
        or array), cost entity types (costModel.costEntities and every captured entity whose
        supertype chain reaches one), the line subtype and the capture's source pin. Refused (exit 2)
        when the capture is missing, not a ready pc-tools.product-capture/1.0.0, for another product
        or line, or taken at another checkout commit than `revision`."""
        path = os.path.join(self.args.product_root, "extracted", "product-capture.json")
        if not os.path.isfile(path):
            die("missing product capture %s" % path)
        try:
            with open(path, encoding="utf-8") as handle:
                capture = json.load(handle, object_pairs_hook=OrderedDict)
        except (OSError, ValueError) as error:
            die("cannot read %s: %s" % (path, error))
        if not isinstance(capture, dict) or capture.get("formatVersion") != CAPTURE_FORMAT:
            die("%s is not a %s capture" % (path, CAPTURE_FORMAT))
        if capture.get("state") != "ready":
            die("%s has state %r; only a ready capture is accepted" % (path, capture.get("state")))
        scope, source = capture.get("scope") or {}, capture.get("source") or {}
        if scope.get("product") != self.args.product or scope.get("line") != self.args.line:
            die("%s holds %s / %s, not %s / %s" % (path, scope.get("product"), scope.get("line"), self.args.product, self.args.line))
        if source.get("revision") != revision:
            die("checkout is at %s but the capture %s was taken at %s" % (revision, path, source.get("revision")))
        line_decl = capture["policyLinePattern"]["declaration"]
        clauses = OrderedDict()
        for clause in capture["policyLinePattern"].get("clauses", []):
            terms = []
            for group in clause.get("children", []):
                for term in group.get("children", []):
                    if term.get("kind", "").endswith("CovTermPattern"):
                        terms.append(OrderedDict([("codeIdentifier", term["attributes"]["codeIdentifier"])]))
            code = clause["attributes"]["codeIdentifier"]
            subtype = next((v for k, v in clause["attributes"].items() if k.endswith("Subtype")), None)
            clauses[code] = OrderedDict([("codeIdentifier", code), ("kind", clause["kind"]), ("entity", subtype), ("terms", terms)])
        entities = OrderedDict()
        for rec in capture.get("entities", []):
            entity = OrderedDict([("name", rec["name"]), ("supertype", None), ("delegates", []), ("fields", []), ("branchType", None)])
            for declaration in rec.get("declarations", []):
                if declaration.get("attributes", {}).get("effDatedBranchType"):
                    entity["branchType"] = declaration["attributes"]["effDatedBranchType"]
                if declaration.get("kind") == "subtype" and declaration["attributes"].get("supertype"):
                    entity["supertype"] = declaration["attributes"]["supertype"]
                for child in declaration.get("children", []):
                    attrs = child.get("attributes", {})
                    if child.get("kind") == "implementsEntity" and attrs.get("name"):
                        entity["delegates"].append(attrs["name"])
                    elif child.get("kind") in ENTITY_MEMBER_KINDS and attrs.get("name"):
                        field = OrderedDict([("name", attrs["name"]), ("kind", child["kind"])])
                        target = attrs.get("fkentity") or attrs.get("arrayentity")
                        if target:
                            field["targetEntity"] = target
                        entity["fields"].append(field)
            entities[rec["name"]] = entity
        cost_types = set(capture.get("costModel", {}).get("costEntities", []))
        for name in entities:
            cur, seen = entities[name]["supertype"], set()
            while cur and cur not in seen:
                seen.add(cur)
                if cur in cost_types:
                    cost_types.add(name)
                    break
                cur = entities.get(cur, {}).get("supertype")
        self.capture_path = rel(path, os.path.abspath(self.args.product_root))
        self.pm = OrderedDict([
            ("configuration", OrderedDict([("revision", source.get("revision")), ("dirty", bool(source.get("dirty"))),
                                           ("generationMode", source.get("generationMode", "classic"))])),
            ("product", OrderedDict([("codeIdentifier", scope["product"])])),
            ("line", OrderedDict([("codeIdentifier", line_decl["attributes"]["codeIdentifier"]),
                                  ("policyLineSubtype", line_decl["attributes"].get("policyLineSubtype"))])),
            ("clauses", clauses), ("entities", entities),
            ("costObjects", [OrderedDict([("entityType", t)]) for t in sorted(cost_types)]),
            ("typelists", [t["name"] for t in capture.get("typeLists", [])])])
        self.counts["capture clause patterns"] = len(clauses)
        self.counts["capture entities"] = len(entities)
        self.counts["capture cost entities"] = len(cost_types)
        self.counts["capture typelists"] = len(self.pm["typelists"])

    # -- dispatch -------------------------------------------------------------------------------

    def read_dispatch(self):
        registry = os.path.join(self.config_root, "config", "plugin", "registry", "IRatingPlugin.gwp")
        plugin = None
        if os.path.exists(registry):
            text = read_text(registry)
            m = re.search(r'gosuclass="([^"]+)"', text)
            if m:
                plugin = (m.group(1), text.count("\n", 0, m.start()) + 1)
        if not plugin:
            self.unresolved.append("config/plugin/registry/IRatingPlugin.gwp has no gosuclass; engine.plugin is a <VERIFY>")
        # createRatingEngine drafts: the process-owned parser
        line_subtype = self.pm["line"]["policyLineSubtype"]
        stand_in = _EngineProbe(self)
        hits = stand_in.engine_evidence(line_subtype)
        drafts, notes = stand_in.draft_engines(hits)
        self.draft_notes += notes
        self.counts["createRatingEngine branches drafted"] = len(drafts)
        selected = self.pick("selectedEngine", self.args.engine)
        matched = None
        if not selected:
            self.unresolved.append("selectedEngine not given: pass --engine or set selectedEngine in the decisions file (candidates: %s)" % ", ".join(s["engine"] for s in drafts) or "none")
        else:
            matched = next((s for s in drafts if engine_selection_matches(selected, s["engine"])), None)
            if matched is None:
                self.unresolved.append("selectedEngine %s is not among the drafted createRatingEngine branches (%s); the consultant must confirm or add the branch through the decisions file" % (selected, ", ".join(s["engine"] for s in drafts)))
        # the row's verbatim engine when a row matched (fully qualified or not, whichever the
        # source spelled), so every downstream identifier built from self.selected agrees with
        # dispatch[]; otherwise the flag/decisions value as given.
        self.selected = matched["engine"] if matched else selected
        if matched is not None:
            matched["selected"] = True
            matched.move_to_end("at")
        self.dispatch = drafts
        self.plugin = plugin[0] if plugin else "<VERIFY: IRatingPlugin.gwp gosuclass>"
        self.plugin_at = self.ref(registry, plugin[1], "gosuclass") if plugin else None
        engine = OrderedDict()
        engine["selected"] = self.selected or "<DECIDE: selected engine>"
        engine_path = self.class_index.get(last_segment(self.selected)) if self.selected else None
        self.engine_path = engine_path
        self.engine_blocks = []
        base_class = "<VERIFY: engine class not found under gsrc>"
        engine_at = None
        overridden = []
        if engine_path:
            text = read_text(engine_path)
            self.engine_blocks = method_blocks(text)
            m = CLASS_RE.search(text)
            if m:
                base_class = m.group(2)
                engine_at = self.ref(engine_path, text.count("\n", 0, m.start()) + 1)
            for b in self.engine_blocks:
                if "override" in b["modifiers"]:
                    row = OrderedDict([("name", b["name"])])
                    status = self.member_status(engine_path, b)
                    if status != "vanilla":
                        row["status"] = status
                    row["at"] = self.ref(engine_path, b["start"])
                    overridden.append(row)
        elif self.selected:
            self.unresolved.append("selected engine %s has no .gs file under gsrc; base class, members, units and flow are <VERIFY>" % self.selected)
        engine["base"] = base_class
        engine["plugin"] = self.plugin
        engine["at"] = engine_at or self.ref(engine_path or self.gsrc, symbol=last_segment(self.selected) if self.selected else "createRatingEngine")
        engine["overrides"] = overridden
        self.counts["overridden members on the selected engine"] = len(overridden)
        return engine

    def pick(self, key, flag_value=None):
        return flag_value or self.decisions.get(key) or None

    # -- units, lookups, emissions, flow --------------------------------------------------------

    def calls_passing(self, t, var, scan, source, start=0):
        """Each call in block t that passes the local `var` as an argument: (call match, stripped
        arguments, var's position, the traversed methods the call resolves to). Calls and their
        parentheses are found in `scan` from `start`; arguments are read from `source`, the same
        text with the same offsets."""
        for m in CALL_RE.finditer(scan, start):
            receiver, name = m.group(1), m.group(2)
            j = paren_end(scan, m.end() - 1)
            args = [a.strip() for a in split_args(source[m.end():j - 1])]
            if var not in args:
                continue
            targets = self._resolve_calls(self._candidates(t, receiver, name), len(args)) if name in self.by_name else []
            yield m, args, args.index(var), targets

    def analyse_engine(self):
        engine, path = self.selected, self.engine_path
        engine_cls = last_segment(engine)
        # -- the traversal set: the engine file plus every PC class under its directory tree ----
        # (never gsrc/gw/rating, never a file outside that tree). Every block is tagged with its
        # declaring class (inner classes included) and file.
        root_dir = os.path.dirname(path)
        files = [path]
        skip = {"gclasses", "idea-gclasses", "build", ".gradle"}
        for dirpath, dirnames, names in os.walk(root_dir):
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            for n in sorted(names):
                p = os.path.join(dirpath, n)
                if (n.endswith(".gs") or n.endswith(".gsx")) and os.path.abspath(p) != os.path.abspath(path):
                    files.append(p)
        blocks, class_info, texts = [], OrderedDict(), OrderedDict()
        for f in files:
            text = read_text(f)
            texts[f] = text
            bl, spans = tagged_blocks(f, text)
            blocks += bl
            for name, ext, abstract, s, e in spans:
                outer = None
                for oname, _, _, os_, oe in spans:
                    if oname != name and os_ <= s and e <= oe and (outer is None or os_ > outer[1]):
                        outer = (oname, os_)
                class_info.setdefault(name, OrderedDict([("path", f), ("extends", ext), ("abstract", abstract),
                                                        ("outer", outer[0] if outer else None), ("fields", {})]))
            # Index only field declarations owned by this lexical class. A declaration inside
            # another implementation or method is not evidence for the caller's receiver type.
            structural = structural_text(text)
            for m in re.finditer(r"\bvar\s+(\w+)\s*(?::\s*([\w.<>]+))?\s*(?:=\s*new\s+([\w.]+)\s*[(<])?", structural):
                field_line = text.count("\n", 0, m.start()) + 1
                if any(b["start"] <= field_line <= b["end"] for b in bl):
                    continue
                owners = [s for s in spans if s[3] <= field_line <= s[4]
                          and not ("@anonymous-" in s[0] and field_line == s[3])]
                owner = max(owners, key=lambda s: s[3], default=None)
                typ = m.group(2) or m.group(3)
                if owner and typ:
                    class_info[owner[0]]["fields"][m.group(1)] = typ
        self.blocks, self.class_info, self.texts = blocks, class_info, texts
        by_name = OrderedDict()
        for i, b in enumerate(blocks):
            by_name.setdefault(b["name"], []).append(i)
        self.by_name = by_name
        self.engine_defines = {b["name"] for b in blocks if b["cls"] == engine_cls}
        self.unit_key_of = {i: method_key(b, blocks) for i, b in enumerate(blocks)}
        params_of = [param_types(b["params"]) for b in blocks]

        def ancestors(cls):
            out, cur = [], class_info.get(cls, {}).get("extends")
            while cur and cur in class_info and cur not in out:
                out.append(cur)
                cur = class_info[cur]["extends"]
            return out

        def descendants(cls):
            return [c for c in class_info if c != cls and cls in ancestors(c)]

        def outers(cls):
            out, cur = [], class_info.get(cls, {}).get("outer")
            while cur and cur not in out:
                out.append(cur)
                cur = class_info.get(cur, {}).get("outer")
            return out

        def simple_type(t):
            return t.strip().split("<")[0].split(".")[-1].strip() if t else None

        def declared_type(caller, name):
            """The declared class of `name` in the caller's scope: a parameter, a local `var x : T`
            or `var x = new T(`, or a field in its lexical class/hierarchy. None when unknown."""
            for pn, pt in params_of[caller]:
                if pn == name:
                    return pt
            for scope in (structural_text(blocks[caller]["body"]),):
                m = re.search(r"\bvar\s+%s\s*:\s*([\w.<>]+)" % re.escape(name), scope)
                if m:
                    return simple_type(m.group(1))
                m = re.search(r"\bvar\s+%s\s*(?::\s*[\w.<>]+\s*)?=\s*new\s+([\w.]+)\s*[(<]" % re.escape(name), scope)
                if m:
                    return simple_type(m.group(1))
            cls = blocks[caller]["cls"]
            scopes = [cls] + ancestors(cls)
            for outer in outers(cls):
                scopes += [outer] + ancestors(outer)
            for owner in scopes:
                typ = class_info.get(owner, {}).get("fields", {}).get(name)
                if typ:
                    return simple_type(typ)
            return None

        def candidates(caller, receiver, name):
            """Block ids a call `receiver.name(` from `caller` may reach, by declared type: the
            caller's own class hierarchy for a bare call, the named class for a static call, the
            declared type and its subclasses for a typed receiver. Nothing outside the set."""
            cls = blocks[caller]["cls"]
            if receiver is None or receiver == "this":
                classes = [cls] + ancestors(cls) + descendants(cls)
                for o in outers(cls):
                    classes += [o] + ancestors(o)
            elif receiver == "super":
                classes = ancestors(cls)
            elif receiver in class_info and declared_type(caller, receiver) is None:
                classes = [receiver] + ancestors(receiver)
            else:
                t = declared_type(caller, receiver)
                classes = [t] + ancestors(t) + descendants(t) if t in class_info else []
            seen, out = set(), []
            for c in classes:
                if c in seen:
                    continue
                seen.add(c)
                out += [i for i in by_name.get(name, []) if blocks[i]["cls"] == c]
            return out

        def resolve_calls(cands, nargs):
            """Every candidate matching the arity (all of them when none does): reachability and
            forwarding follow each candidate, since the static arity match cannot pick by type."""
            exact = [i for i in cands if len(param_types(blocks[i]["params"])) == nargs]
            return exact or cands
        self._candidates, self._resolve_calls = candidates, resolve_calls

        # CostData class -> Cost entity, from the engine's createCostDataForCost switch
        cost_entity_of = OrderedDict()
        for i in by_name.get("createCostDataForCost", []):
            if blocks[i]["cls"] != engine_cls:
                continue
            ccd = blocks[i]
            for m in re.finditer(r"case\s+([\w.]+)\s*:\s*return\s+new\s+(\w+)\(", ccd["body"]):
                cost_entity_of[m.group(2)] = (m.group(1).split(".")[-1], self.ref(ccd["path"], ccd["start"] + ccd["body"].count("\n", 0, m.start())))
        # String parameters a method forwards into a factor lookup or a routine call, directly or
        # through another method's forwarding parameter (fixpoint over the call graph)
        forward = {}
        for i, b in enumerate(blocks):
            for pos, (pname, ptype) in enumerate(params_of[i]):
                if ptype != "String":
                    continue
                if re.search(r"RateAdjFactorSearchCriteria\s*\(\s*%s\b" % re.escape(pname), b["body"]):
                    forward.setdefault(i, set()).add((pos, "factors"))
                if re.search(r"executeCalcRoutine\s*\(\s*%s\b" % re.escape(pname), b["body"]):
                    forward.setdefault(i, set()).add((pos, "routines"))
            # A Map<CalcRoutineParamName, Object>-typed parameter passed straight through to
            # executeCalcRoutine's last argument (PA's rateLineCoverage(cov, vehicle, routineCode)
            # sibling pattern, HOP's callRateRoutine, CP's executeCPRateRoutine) forwards the
            # parameter-map literal one hop further out to the caller's own expression for it.
            for pos, (pname, ptype) in enumerate(params_of[i]):
                if ptype != "Map":
                    continue
                for cargs, _ in call_args(blank_comments(b["body"]), "executeCalcRoutine"):
                    if cargs and cargs[-1].strip() == pname:
                        forward.setdefault(i, set()).add((pos, "paramMap"))
                        break
        # a block's own parameter-map expression, when its own body spells the executeCalcRoutine
        # call directly rather than receiving the map through a Map-typed forwarding parameter
        # (PA's rateLineCoverage(Coverage,PersonalVehicle,String): the routine *code* is a forwarded
        # String parameter, but PolicyLine/vehicle/cov are class-level/own-parameter, so the map
        # literal is entirely local to this block - the paramMap forward never fires for it). A
        # sibling method that only forwards the routine code to this one still needs this block's
        # own map to attach binds to the routine value it resolves. split_map_entries,
        # not split_args, for the same `->` reason as the direct executeCalcRoutine handling below.
        def arg_offset(calls, k, position):
            """The offset of argument `position` of call k, or None when it is not known."""
            return calls[k][position] if k < len(calls) and position < len(calls[k]) else None

        direct_map_expr = {}
        # offsets in a block's body (comments blanked in place) of each executeCalcRoutine call's
        # arguments, in call order: the stripped text above has the same calls in the same order
        routine_arg_offsets = {}
        for i, b in enumerate(blocks):
            blank_i = blank_comments(b["body"])
            routine_arg_offsets[i] = [call_arg_offsets(blank_i, m.end() - 1, False)
                                      for m in re.finditer(r"executeCalcRoutine\s*\(", blank_i)]
            body_i = blank_comments(b["body"])
            for k, m in enumerate(re.finditer(r"executeCalcRoutine\s*\(", body_i)):
                j = paren_end(body_i, m.end() - 1)
                cargs = split_map_entries(body_i[m.end():j - 1])
                if len(cargs) > 1:
                    direct_map_expr.setdefault(i, (cargs[-1], arg_offset(routine_arg_offsets[i], k, len(cargs) - 1)))
        # every call site, resolved by receiver: (caller, candidate ids, args, position in body);
        # raw_arg_offsets[(caller, position)] holds its arguments' offsets in the caller's body
        raw_calls, raw_arg_offsets = [], {}
        for i, b in enumerate(blocks):
            body = blank_comments(b["body"])
            blank_i = blank_comments(b["body"])
            blank_calls = [call_arg_offsets(blank_i, m.end() - 1, True) for m in CALL_RE.finditer(blank_i)]
            for k, m in enumerate(CALL_RE.finditer(body)):
                receiver, name = m.group(1), m.group(2)
                if name not in by_name:
                    continue
                cands = candidates(i, receiver, name)
                if not cands:
                    continue
                j = paren_end(body, m.end() - 1)
                raw_calls.append((i, cands, split_args(body[m.end():j - 1]), m.start(2)))
                raw_arg_offsets[(i, m.start(2))] = blank_calls[k] if k < len(blank_calls) else []
            # property getters referenced without parentheses, e.g. `wrapper.PDIncreasedLimitFactor`
            for m in PROPERTY_RE.finditer(body):
                receiver, name = m.group(1), m.group(2)
                if name not in by_name:
                    continue
                cands = [c for c in candidates(i, receiver, name) if blocks[c]["kind"] == "property"]
                if cands:
                    raw_calls.append((i, cands, [], m.start(2)))
        changed = True
        while changed:
            changed = False
            for i, cands, args, _ in raw_calls:
                for target in resolve_calls(cands, len(args)):
                    for pos, kind in list(forward.get(target, [])):
                        if pos < len(args):
                            arg = args[pos].strip()
                            for ppos, (pname, ptype) in enumerate(params_of[i]):
                                if arg == pname and (ppos, kind) not in forward.get(i, set()):
                                    forward.setdefault(i, set()).add((ppos, kind))
                                    changed = True
        # class-level string literals (routine codes), from every file in the set
        literals = {}
        for f, text in texts.items():
            for m in re.finditer(r"var\s+(\w+)\s*:\s*String[^=\n]*=\s*\"([^\"]+)\"", text):
                literals.setdefault(m.group(1), (m.group(2), (f, text.count("\n", 0, m.start()) + 1)))
            for m in re.finditer(r"\bas\s+(\w+)\s*=\s*\"([^\"]+)\"", text):
                literals.setdefault(m.group(1), (m.group(2), (f, text.count("\n", 0, m.start()) + 1)))
        self.literals = literals

        def literal_of(expr):
            expr = expr.strip()
            lit = STRING_RE.fullmatch(expr)
            if lit:
                return lit.group(1), None
            if expr in literals:
                return literals[expr]
            return None, None

        # class-level `Map<Class<T>, String>` literals (coverage class -> routine code), one entry
        # per line, from every file in the set
        maps = {}
        for f, text in texts.items():
            for m in re.finditer(r"var\s+(\w+)\s*:\s*Map\s*<\s*Class\s*<\s*[\w.]+\s*>\s*,\s*String\s*>\s*=\s*\{", text):
                j = paren_end(text, m.end() - 1)
                entries = []
                for em in re.finditer(r"([\w.]+)\s*->\s*\"([^\"]+)\"", text[m.end():j - 1]):
                    entries.append((em.group(1).split(".")[-1], em.group(2), (f, text.count("\n", 0, m.end() + em.start()) + 1)))
                maps.setdefault(m.group(1), entries)
        self.maps = maps

        def case_labels(lines):
            """Switch-case labels in effect per line; fall-through labels accumulate until break/default."""
            out, pending = [], []
            for t in lines:
                for cm in re.finditer(r"\bcase\s+([\w.]+)\s*:", t):
                    pending.append(cm.group(1).split(".")[-1])
                out.append(list(pending))
                if re.search(r"\bbreak\b|\bdefault\s*:", t):
                    pending = []
            return out

        def switch_cases_at(body):
            """Per line of body: (switch subject, [case labels in effect]) for the innermost
            enclosing switch, or (None, []). A label is the verbatim case token: a string literal
            with its quotes (`case ("cpDeductGrp1") :`) or an identifier's last segment. Labels
            accumulate across fall-through cases until break, return, throw or default."""
            lines = body.split("\n")
            structural = structural_text(body).split("\n")
            out, stack, depth = [], [], 0
            for raw, bare in zip(lines, structural):
                opens, closes = bare.count("{"), bare.count("}")
                sm = re.search(r"\bswitch\s*\((.*)\)\s*\{", raw)
                if sm:
                    stack.append([squash(sm.group(1)), depth + opens - closes, []])
                if stack:
                    for cm in re.finditer(r"\bcase\s*\(?\s*(\"[^\"]*\"|[\w.]+)\s*\)?\s*:", raw):
                        label = cm.group(1)
                        stack[-1][2].append(label if label.startswith('"') else label.split(".")[-1])
                    out.append((stack[-1][0], list(stack[-1][2])))
                    if re.search(r"\b(break|return|throw)\b|\bdefault\s*:", bare):
                        stack[-1][2] = []
                else:
                    out.append((None, []))
                depth += opens - closes
                while stack and stack[-1][1] > depth:
                    stack.pop()
            return out

        def returned_literals(t):
            """[(literal, (file, line), cases)] when every `return` in block t returns a string
            literal; [] otherwise (a computed return is not a routine code the checkout spells)."""
            b = blocks[t]
            body = blank_comments(b["body"])
            lines = body.split("\n")
            cases = case_labels(lines)
            out = []
            for n, t_line in enumerate(lines):
                rm = re.search(r"\breturn\s+(.+?)\s*;?\s*$", t_line)
                if not rm:
                    continue
                lit = STRING_RE.fullmatch(rm.group(1).strip())
                if not lit:
                    return []
                out.append((lit.group(1), (b["path"], b["start"] + n), list(cases[n])))
            return out

        def resolve_routine_expr(i, expr, depth=0):
            """What routine code(s) `expr` in block i spells: [(code, (file, line) or None, extra
            case labels)]. None when expr is one of i's own parameters (an outer call site
            resolves it); [] when nothing in the checkout spells it."""
            expr = expr.strip()
            value, decl = literal_of(expr)
            if value is not None:
                return [(value, decl, [])]
            if any(expr == pn for pn, _ in params_of[i]):
                return None
            if depth > 4:
                return []
            body = blank_comments(blocks[i]["body"])
            m = re.search(r"\bvar\s+%s\s*(?::\s*[\w.<>]+\s*)?=\s*([^\n]+)" % re.escape(expr), body)
            if m and m.group(1).strip() != expr:
                return resolve_routine_expr(i, m.group(1).strip(), depth + 1)
            m = re.fullmatch(r"([\w.]+)\.get\s*\(.*\)", expr, re.S)
            if m and m.group(1).split(".")[-1] in maps:
                return [(code, loc, [key]) for key, code, loc in maps[m.group(1).split(".")[-1]]]
            m = re.fullmatch(r"(?:this\.)?(\w+)\s*\((.*)\)", expr, re.S)
            if m and m.group(1) in by_name:
                out = []
                for t in resolve_calls(candidates(i, None, m.group(1)), len(split_args(m.group(2)))):
                    out += returned_literals(t)
                return out
            return []
        self._resolve_routine_expr = resolve_routine_expr

        # -- binds - the Map<CalcRoutineParamName, Object> literal reaching each
        # executeCalcRoutine call, in three forms: (1) an inline `{TC_X -> expr, ...}` literal,
        # direct at the call or via a local var; (2) a literal returned from a helper method, one
        # binding set per switch case, its own formal parameters substituted by this call's actual
        # arguments; (3) a local var mutated with `.put(TC_X, expr)` before the call, directly or
        # through a one-hop helper function. Never a bare variable name: an unresolved argument
        # leaves that routine's binds empty and the gap is reported (a schema-required,
        # possibly-empty array - never invented). --------------------------------------------------
        self.dataflow = Dataflow(self)
        bind_scans = {}

        def bind_type(t, expr, pos):
            """(type name, kind, verify) of a bind's value expression, typed in block t the way the
            dataflow facts type an expression; pos is where in t's body it is evaluated, or None
            when that is not known (typed at the start of t's body); None when unresolved."""
            if t not in bind_scans:
                bind_scans[t] = BlockScan(self.dataflow, t)
                bind_scans[t].collect()
            scan = bind_scans[t]
            return self.dataflow.bind_class(scan.expr_type(expr, scan.start if pos is None else pos))

        def direct_mutations(body_text, var):
            """[(TC_X, valueExpr, offset)] for every literal `var.put(TC_X, expr)` in body_text."""
            out = []
            for mm in re.finditer(r"\b%s\s*\.\s*put\s*\(\s*(?:CalcRoutineParamName\.)?(TC_\w+)\s*,\s*(.+?)\)\s*;?" % re.escape(var), body_text):
                out.append((mm.group(1), mm.group(2).strip(), mm.start()))
            return out

        def helper_put_entries(t, var, body_text):
            """[(TC_X, valueExpr, offset)] via a one-hop helper call `helper(..., var, ...)` whose
            own body does `<itsMapParam>.put(TC_X, <itsOtherParam>)` (CPDTORatingEngine.gs:318's
            updateDeductFactor), substituting the call's own argument for the helper's formal
            parameter."""
            out = []
            for cm, cargs, map_pos, targets in self.calls_passing(t, var, body_text, body_text):
                for target in targets:
                    tparams = params_of[target]
                    if map_pos >= len(tparams) or simple_type(tparams[map_pos][1]) != "Map":
                        continue
                    tbody = blank_comments(blocks[target]["body"])
                    for tc, val, _ in direct_mutations(tbody, tparams[map_pos][0]):
                        subst = val
                        for p_idx, (p_name, _) in enumerate(tparams):
                            if val == p_name and p_idx < len(cargs):
                                subst = cargs[p_idx]
                                break
                        out.append((tc, subst, cm.start()))
            return out

        def map_entries_for(t, expr, offset=None):
            """[(TC_X, valueExpr, offsetInThisBlockOrNone, typingOffsetOrNone)] for the
            parameter-map literal `expr` (evaluated in block t's own body, at `offset` when the
            caller knows it) spells - forms 1-3 above; None when nothing in this block's own text
            spells it (a bare forwarded parameter resolves at the outer call site, through the
            `forward`/paramMap fixpoint instead). An entry spelled in t's body has its own offset;
            one spelled in a helper's returned literal is typed where t calls the helper."""
            body_text = blank_comments(blocks[t]["body"])
            expr = expr.strip()
            if expr.startswith("{"):
                if not expr.endswith("}"):
                    return None  # an unbalanced fragment - a forwarding call's own arg splitter
                    # (raw_calls's generic split_args, <>-tracking for real generic type arguments)
                    # can misread this literal's own `->` arrows as closing one; never guess at the
                    # missing tail, leave the binding unresolved instead.
                if offset is not None and body_text[offset:offset + 1] == "{":
                    inner, _ = brace_span(body_text, offset)
                    return [(tc, val, pos, pos) for tc, val, pos in parse_map_literal_entries(inner, offset + 1)] or None
                inner, _ = brace_span(expr, 0)
                return [(tc, val, None, None) for tc, val, _ in parse_map_literal_entries(inner, None)] or None
            cm = re.fullmatch(r"(\w+)\s*\((.*)\)", expr, re.S)
            if cm and cm.group(1) in by_name:
                name, arg_text = cm.group(1), cm.group(2)
                cargs = split_args(arg_text)
                out = []
                for target in resolve_calls(candidates(t, None, name), len(cargs)):
                    tparams = [p for p, _ in params_of[target]]
                    tbody = blank_comments(blocks[target]["body"])
                    for lit_m in re.finditer(r"\{[^{}]*\}", tbody):
                        if not re.search(r"return\s*$", tbody[:lit_m.start()].rstrip()):
                            continue
                        inner, _ = brace_span(tbody, lit_m.start())
                        for tc, val, _ in parse_map_literal_entries(inner, None):
                            if val in tparams:
                                p_idx = tparams.index(val)
                                if p_idx < len(cargs):
                                    val = cargs[p_idx].strip()
                            out.append((tc, val, None, offset))
                return out or None
            if re.fullmatch(r"\w+", expr):
                entries = OrderedDict()
                dm = re.search(r"\bvar\s+%s\s*(?::[^=;{}]*)?=\s*\{" % re.escape(expr), body_text)
                if dm:
                    inner, _ = brace_span(body_text, dm.end() - 1)
                    for tc, val, pos in parse_map_literal_entries(inner, dm.end()):
                        entries[tc] = (val, pos, pos)
                else:
                    # a call-based declaration, e.g. `var params = createParamMap(cov, "...")`
                    # (CPDTORatingEngine.gs:216, form 2) - resolve the RHS call the same way, then
                    # keep merging any `.put(...)` mutations below.
                    dm2 = re.search(r"\bvar\s+%s\s*(?::[^=;{}]*)?=\s*(\w+\s*\([^;\n]*\))" % re.escape(expr), body_text)
                    if dm2:
                        for tc, val, pos, tpos in (map_entries_for(t, dm2.group(1), dm2.start(1)) or []):
                            entries[tc] = (val, pos, tpos)
                muts = sorted(direct_mutations(body_text, expr) + helper_put_entries(t, expr, body_text), key=lambda x: x[2])
                for tc, val, pos in muts:
                    entries[tc] = (val, pos, pos)
                return [(tc, val, pos, tpos) for tc, (val, pos, tpos) in entries.items()] or None
            return None

        # binds still to type, by id: typing reads class files, so only the binds of routines the
        # document carries are typed (type_binds), never those of a block no root reaches
        untyped = {}

        def type_binds(bindings):
            for entry in bindings or []:
                job = untyped.pop(id(entry), None)
                if job is None:
                    continue
                t, val_expr, pos = job
                at = entry.pop("at")
                resolved = bind_type(t, val_expr, pos)
                if resolved:
                    entry["type"], entry["kind"], verify = resolved
                    if verify:
                        entry["verify"] = verify
                else:
                    entry["verify"] = ("confirm the static type of `%s`; the parser could not resolve it through a "
                                        "declared parameter, local variable, loop variable, or captured entity field" % val_expr)
                entry["at"] = at

        def resolve_param_map(t, expr, offset=None):
            """Resolved parameterBinding dicts for the Map<CalcRoutineParamName, Object>
            literal `expr` (evaluated in block t's own scope) spells; None when this block's own
            text does not spell it at all (left for the summary to surface as unresolved)."""
            entries = map_entries_for(t, expr, offset)
            if not entries:
                return None
            tb = blocks[t]
            body_text = blank_comments(tb["body"])
            out = OrderedDict()
            for tc, val_expr, pos, tpos in entries:
                code = tc[3:].lower() if tc.upper().startswith("TC_") else tc.lower()
                line = tb["start"] + body_text.count("\n", 0, pos) if pos is not None else tb["start"]
                entry = OrderedDict([("parameter", code), ("expression", val_expr)])
                if code not in self.calc_routine_param_codes:
                    entry["verify"] = ("confirm `%s` exists as a CalcRoutineParamName typecode code; not found in "
                                        "config/metadata/typelist/CalcRoutineParamName.tti or "
                                        "config/extensions/typelist/CalcRoutineParamName.ttx" % code)
                elif val_expr.strip() == "null":
                    entry["type"] = None
                    entry["kind"] = "unbound"
                else:
                    untyped[id(entry)] = (t, val_expr, tpos)
                entry["at"] = self.ref(tb["path"], line)
                # the scope the value expression is evaluated in, for its `from` (value origin)
                self.binding_scopes[id(entry)] = (entry, t, expr, line)
                out[code] = entry
            return list(out.values())
        self._resolve_param_map = resolve_param_map

        # per-block direct facts; every locator is (…, line, path) of the declaring file
        facts = []
        # block id -> [(parameter position, string literal, CostData class)]: the CostData class a
        # block constructs under `switch (<own parameter>) { case ("<literal>"): new X(` - a call
        # site that passes that literal for that parameter emits exactly that class
        switch_emits = {}
        for i, b in enumerate(blocks):
            body = blank_comments(b["body"])
            bpath = b["path"]
            lines = body.split("\n")
            cases_at = case_labels(lines)
            switch_at = switch_cases_at(body)
            f = OrderedDict([("calls", []), ("factors", []), ("routines", []), ("costdata", []), ("assigns", []), ("terms", [])])
            own_params = {pn for pn, _ in params_of[i]}
            own_param_positions = {pn: pos for pos, (pn, _) in enumerate(params_of[i])}
            for caller, cands, args, pos_in_body in raw_calls:
                if caller != i:
                    continue
                offset = body.count("\n", 0, pos_in_body)
                line = b["start"] + offset
                for target in resolve_calls(cands, len(args)):
                    if target == i:
                        continue
                    f["calls"].append((target, line, cases_at[offset]))
                    fwd = forward.get(target, [])
                    # a paramMap forwarding is resolved first (in this caller's own scope, from its
                    # own expression at that position) so it is ready when a routine code forwarded
                    # at the same call is resolved just below, and the two can be attached together.
                    call_bindings = None
                    for pos, kind in fwd:
                        if kind == "paramMap" and pos < len(args):
                            arg_offsets = raw_arg_offsets.get((i, pos_in_body), [])
                            call_bindings = resolve_param_map(i, args[pos], arg_offsets[pos] if pos < len(arg_offsets) else None)
                    if call_bindings is None and target in direct_map_expr:
                        # the map is not itself forwarded; target's own body spells it directly
                        # (resolved in target's own scope, not this caller's)
                        call_bindings = resolve_param_map(target, *direct_map_expr[target])
                    for pos, kind in fwd:
                        if pos >= len(args):
                            continue
                        if kind == "factors":
                            value, decl = literal_of(args[pos])
                            if value is None and args[pos].strip() in own_params:
                                continue  # forwarded again; the literal is found at an outer call site
                            if value is not None and (value, line, cases_at[offset], decl, bpath) not in f[kind]:
                                f[kind].append((value, line, cases_at[offset], decl, bpath))
                            continue
                        if kind == "paramMap":
                            continue  # resolved above
                        resolved = resolve_routine_expr(i, args[pos])
                        if resolved is None:
                            continue  # forwarded again; the outer call site resolves it
                        if not resolved:
                            self.unresolved.append("%s passes `%s` to %s (line %d) but no string literal, class-level map entry, or literal-returning function spells the routine code; no unit was created" % (self.unit_key_of[i], args[pos].strip(), blocks[target]["name"], line))
                            continue
                        for value, decl, extra in resolved:
                            entry = (value, line, cases_at[offset] + extra, decl, bpath, call_bindings)
                            if entry not in f["routines"]:
                                f["routines"].append(entry)
            for m in re.finditer(r"RateAdjFactorSearchCriteria\s*\(\s*\"([^\"]+)\"", body):
                f["factors"].append((m.group(1), b["start"] + body.count("\n", 0, m.start()), [], None, bpath))
            for m in re.finditer(r"new\s+(\w+CostData)\s*\(", body):
                j = paren_end(body, m.end() - 1)
                cd_args = split_args(body[m.end():j - 1])
                offset = body.count("\n", 0, m.start())
                subject, labels = switch_at[offset]
                f["costdata"].append((m.group(1), b["start"] + offset, bpath, cd_args, subject, labels))
                if subject in own_param_positions:
                    for label in labels:
                        if label.startswith('"'):
                            switch_emits.setdefault(i, []).append((own_param_positions[subject], label[1:-1], m.group(1)))
            for k, m in enumerate(re.finditer(r"executeCalcRoutine\s*\(", body)):
                j = paren_end(body, m.end() - 1)
                # split_map_entries, not split_args: split_args's <>-tracking (for PC code generic
                # arguments elsewhere) misreads a `TC_X->expr` map entry's own `->` as closing a
                # generic argument, corrupting a comma inside an inline map literal argument here.
                args = split_map_entries(body[m.end():j - 1])
                offset = body.count("\n", 0, m.start())
                if args:
                    resolved = resolve_routine_expr(i, args[0])
                    if resolved is None:
                        continue  # a parameter; the caller's call site resolves it through `forward`
                    if not resolved:
                        self.unresolved.append("%s calls executeCalcRoutine(%s) (line %d) but no string literal, class-level map entry, or literal-returning function spells the routine code; no unit was created" % (self.unit_key_of[i], args[0].strip(), b["start"] + offset))
                        continue
                    call_bindings = resolve_param_map(i, args[-1], arg_offset(routine_arg_offsets[i], k, len(args) - 1)) if len(args) > 1 else None
                    for value, decl, extra in resolved:
                        entry = (value, b["start"] + offset, cases_at[offset] + extra, decl, bpath, call_bindings)
                        if entry not in f["routines"]:
                            f["routines"].append(entry)
            for m in re.finditer(r"\b\w+\.(%s)\s*=[^=]" % "|".join(COSTDATA_MEMBERS), body):
                f["assigns"].append((m.group(1), b["start"] + body.count("\n", 0, m.start())))
            for pname, ptype in param_types(b["params"]):
                clause = self.pm["clauses"].get(ptype)
                if clause:
                    codes = {t["codeIdentifier"] for t in clause.get("terms", [])}
                    for m in re.finditer(r"\b%s\.(\w+?)Term\b" % re.escape(pname), body):
                        if m.group(1) in codes:
                            f["terms"].append((ptype, m.group(1), b["start"] + body.count("\n", 0, m.start()), bpath))
            facts.append(f)
        self.facts = facts
        self.switch_emits = switch_emits

        # reachability from the engine's rating roots, with the root that reaches each block;
        # rateOnly is a root when the engine defines it (its emissions are slice-mode)
        reached = OrderedDict()
        for root in ROOT_METHODS:
            for root_id in by_name.get(root, []):
                if blocks[root_id]["cls"] != engine_cls:
                    continue
                stack, seen = [root_id], set()
                while stack:
                    cur = stack.pop(0)
                    if cur in seen:
                        continue
                    seen.add(cur)
                    for callee, _, _ in facts[cur]["calls"]:
                        reached.setdefault(callee, root)
                        stack.append(callee)
        self.reached = reached
        # dataflow facts of every method the roots reach, and of the roots themselves
        for i in range(len(blocks)):
            facts[i]["dataflow"] = []
        for root in ROOT_METHODS:
            for r in by_name.get(root, []):
                if blocks[r]["cls"] == engine_cls:
                    facts[r]["dataflow"] = self.dataflow.facts_of(r)
        for i in reached:
            facts[i]["dataflow"] = self.dataflow.facts_of(i)

        def has_dataflow(i):
            return any(kind != "collections" for kind, _ in facts[i]["dataflow"])

        # a root that does rating work itself (constructs a CostData or calls a routine in its own
        # body) or has dataflow facts of its own is also a unit, keyed <Engine>.<root>; it never
        # appears in the flow orders
        working_roots = []
        for root in ROOT_METHODS:
            for r in by_name.get(root, []):
                if blocks[r]["cls"] == engine_cls and (facts[r]["costdata"] or facts[r]["routines"] or has_dataflow(r)):
                    reached[r] = root
                    working_roots.append(r)
        # switch-case joins: the case labels in effect at a call apply to the called block
        applies_from_cases = {}
        for i, f in enumerate(facts):
            for callee, line, cases in f["calls"]:
                for c in cases:
                    applies_from_cases.setdefault(callee, []).append((c, self.unit_key_of[i]))

        # unit grain: a reached method is a unit when its own body constructs a CostData or calls a
        # calc routine, or when it is scoped to a captured clause pattern by parameter type or by the
        # switch label at its call site. A method with none of those is a dispatcher: not a unit;
        # its lookups and term reads roll up into the unit whose closure passes through it, and the
        # flow orders list the units it reaches. The `rate*` name prefix is not the test.
        def clause_scoped(i):
            if any(pt in self.pm["clauses"] for _, pt in params_of[i]):
                return True
            return any(c in self.pm["clauses"] for c, _ in applies_from_cases.get(i, []))

        def is_root(i):
            return blocks[i]["name"] in ROOT_METHODS and blocks[i]["cls"] == engine_cls
        unit_ids = working_roots + [i for i in reached if not is_root(i)
                                    and (facts[i]["costdata"] or facts[i]["routines"] or clause_scoped(i))]

        # the weak half of the rule: a method that only consults a lookup, reads a term or has
        # dataflow facts is a unit when nothing between the roots and it is already a unit
        # (WCCoveredEmployeeRater.rate reads the USLH factor before handing off to rate_impl; CP's
        # rateLocation calls addCosts inside the parallel location loop), so no fact is left without
        # a unit to carry it; a helper sitting below a unit (GL's getIncreaseLimitFactor under the
        # split-type unit) folds into that unit instead
        def above_units(unit_set):
            seen = set()
            for root in ROOT_METHODS:
                for root_id in by_name.get(root, []):
                    if blocks[root_id]["cls"] != engine_cls:
                        continue
                    stack = [root_id]
                    while stack:
                        cur = stack.pop(0)
                        if cur in seen:
                            continue
                        seen.add(cur)
                        if cur in unit_set and not is_root(cur):
                            continue
                        stack += [callee for callee, _, _ in facts[cur]["calls"]]
            return seen

        def weak_grain(i, unit_set, seen=None):
            seen = seen if seen is not None else set()
            if i in seen:
                return False
            seen.add(i)
            if facts[i]["factors"] or facts[i]["terms"] or has_dataflow(i):
                return True
            return any(weak_grain(callee, unit_set, seen) for callee, _, _ in facts[i]["calls"] if callee not in unit_set)

        while True:
            unit_set = set(unit_ids)
            visible = above_units(unit_set)
            weak = [i for i in reached if not is_root(i) and i not in unit_set and i in visible and weak_grain(i, unit_set)]
            dropped = [i for i in unit_ids if i not in visible and i not in working_roots
                       and not (facts[i]["costdata"] or facts[i]["routines"] or clause_scoped(i))]
            if not weak and not dropped:
                break
            unit_ids = [i for i in unit_ids if i not in dropped] + weak
        chosen = set(unit_ids)
        unit_ids = working_roots + [i for i in reached if i in chosen and i not in working_roots]
        self.unit_ids = unit_ids

        def closure(i, key, seen=None):
            seen = seen if seen is not None else set()
            if i in seen:
                return []
            seen.add(i)
            out = list(facts[i][key])
            for callee, _, _ in facts[i]["calls"]:
                if callee not in unit_ids:
                    out += closure(callee, key, seen)
            return out

        units, emissions, lookups_needed = [], [], OrderedDict()
        unit_decisions = self.decisions.get("units", {})
        emission_decisions = self.decisions.get("emissions", {})
        cost_entity_types = {c.get("entityType") for c in self.pm["costObjects"]}
        classic = self.pm["configuration"].get("generationMode") == "classic"
        self.cost_entity_name = {}  # CostData class -> (cost entity name or None, evidence locator)
        self.unit_collections = OrderedDict()  # gosuMethod unit key -> collection reads, for crossInstance
        self.routine_callers = OrderedDict()  # calc routine code -> the gosuMethod units that execute it

        def cost_entity(cd):
            if cd not in self.cost_entity_name:
                entity, eref = cost_entity_of.get(cd, (None, None))
                if entity is None:
                    entity, eref = self.cost_entity_from_class(cd)
                self.cost_entity_name[cd] = (entity, eref)
            return self.cost_entity_name[cd]

        def decided_produces(key):
            writes, scratch = [], []
            for p in unit_decisions.get(key, {}).get("produces", []):
                (scratch if p["kind"] == "scratchValue" else writes).append(p["name"])
            return writes, scratch

        seen_emissions, reported_entity = set(), set()
        for i in unit_ids:
            b = blocks[i]
            key = self.unit_key_of[i]
            unit = OrderedDict([("key", key), ("kind", "gosuMethod"), ("at", self.ref(b["path"], b["start"]))])
            status = self.member_status(b["path"], b)
            if status != "vanilla":
                unit["status"] = status
            clause_codes = []
            for pname, ptype in param_types(b["params"]):
                if ptype in self.pm["clauses"] and ptype not in clause_codes:
                    clause_codes.append(ptype)
            for case_type, caller in applies_from_cases.get(i, []):
                if case_type in self.pm["clauses"]:
                    if case_type not in clause_codes:
                        clause_codes.append(case_type)
                elif case_type in self.pm["entities"]:
                    pass  # an entity-typed switch, not a clause reference: nothing to carry
                else:
                    # Neither a captured clause pattern nor a captured entity in the product
                    # capture. This is still a real switch case guarding a real call
                    # (source-derived, not invented), so it stays in clausePatterns rather than
                    # being silently dropped; resolve_cross_document_references reports the gap,
                    # with a locator, after all units are built - never here, and never by
                    # filtering it out.
                    if case_type not in clause_codes:
                        clause_codes.append(case_type)
            unit["appliesTo"] = clause_codes
            unit["calls"] = []  # this unit's own call edges; filled by build_flow
            writes, seen_members = [], set()
            for member, line in closure(i, "assigns"):
                if member not in seen_members:
                    seen_members.add(member)
                    writes.append(member)
            decided_writes, decided_scratch = decided_produces(key)
            unit["writes"] = writes + [w for w in decided_writes if w not in writes]
            if decided_scratch:
                unit["scratch"] = decided_scratch
            reads, seen_terms = [], set()
            for clause, term, line, tpath in closure(i, "terms"):
                if (clause, term) not in seen_terms:
                    seen_terms.add((clause, term))
                    reads.append(OrderedDict([("clausePattern", clause), ("covTermPattern", term), ("at", self.ref(tpath, line))]))
            reads += list(unit_decisions.get(key, {}).get("reads", []))
            unit["reads"] = reads
            for factor, line, _, _, fpath in closure(i, "factors"):
                lookups_needed.setdefault(factor, []).append((key, line, fpath))
            emits = []
            for cd, line, cpath, cd_args, subject, labels in closure(i, "costdata"):
                ekey = "%s.%s" % (key, cd)
                when = " or ".join("%s == %s" % (subject, label) for label in labels) if labels else None
                if (ekey, when) in seen_emissions:
                    continue
                seen_emissions.add((ekey, when))
                entity, eref = cost_entity(cd)
                emission = OrderedDict([("unit", key), ("costData", cd)])
                emission["cost"] = entity or "<VERIFY: Cost entity of %s>" % cd
                if ekey not in reported_entity:
                    reported_entity.add(ekey)
                    if entity is None:
                        self.unresolved.append("emission %s: no createCostDataForCost case and no `extends ...CostData<Cost>` declaration names the Cost entity for %s" % (ekey, cd))
                    elif entity not in cost_entity_types:
                        self.unresolved.append("emission %s: Cost entity %s is not a cost entity of the product capture" % (ekey, entity))
                emission["rateMode"] = "windowMode" if reached.get(i) == "rateWindow" else "sliceMode"
                if when:
                    emission["when"] = when
                decided = emission_decisions.get(ekey, {})
                mechanical = self.resolve_key_value_sources(cd, cd_args, cpath, line)
                key_values, missing = OrderedDict(), []
                for dim in PLATFORM_KEY_DIMENSIONS:
                    if dim == "CostCode" and classic:
                        key_values[dim] = None  # only the APD engine passes a CostCode; the platform key carries it unset
                    elif dim in mechanical:
                        key_values[dim] = mechanical[dim]
                    else:
                        missing.append(dim)
                missing += self.subclass_key_dimensions(cd)
                for dim, value in (decided.get("key") or {}).items():
                    key_values[dim] = value
                    if dim in missing:
                        missing.remove(dim)
                if key_values:
                    emission["key"] = key_values
                emission["unresolvedKey"] = missing
                if missing:
                    identity = ekey + (" [%s]" % when if when else "")
                    self.unresolved_key_dims.append((identity, missing))
                    self.unresolved.append("emission %s: key dimensions not resolved from %s's constructor chain or %s's body: %s; confirm each value (decisions file `emissions.%s.key`)"
                                           % (identity, cd, b["name"], ", ".join(missing), ekey))
                if decided.get("status"):
                    emission["status"] = decided["status"]
                emission["at"] = self.ref(cpath, line)
                emissions.append(emission)
                if entity and entity not in emits:
                    emits.append(entity)
            unit["emits"] = emits
            self.unit_collections[key] = self.attach_dataflow(unit, closure(i, "dataflow"))
            unit["source"] = method_source(b["body"])
            units.append(unit)
            # calc routines reached from this unit, scoped by the case labels at the call site
            for code, line, cases, decl, rpath, bindings in closure(i, "routines"):
                callers = self.routine_callers.setdefault(code, [])
                if unit not in callers:
                    callers.append(unit)
                if any(u["key"] == code for u in units):
                    continue
                routine = OrderedDict([("key", code), ("kind", "calcRoutine"),
                                       ("at", self.ref(decl[0], decl[1]) if decl else self.ref(rpath, line)),
                                       ("status", "noBaseline")])
                scoped = [c for c in cases if c in self.pm["clauses"]] or list(clause_codes)
                routine["appliesTo"] = scoped
                decided_writes, decided_scratch = decided_produces(code)
                routine["writes"] = decided_writes
                if decided_scratch:
                    routine["scratch"] = decided_scratch
                routine["reads"] = list(unit_decisions.get(code, {}).get("reads", []))
                type_binds(bindings)
                routine["binds"] = bindings or []
                if not bindings:
                    self.unresolved.append(
                        "calc routine %s: the Map<CalcRoutineParamName, Object> literal reaching executeCalcRoutine "
                        "(%s:%d) is not one of the three mechanical forms this parser resolves; binds left empty"
                        % (code, rel(rpath, self.source_root), line))
                units.append(routine)
                gap = "calc routine %s (reached from %s): steps, tables and writes need the ratebook export; status is noBaseline because routines live in the database, not the checkout" % (code, key)
                self.routine_gaps[code] = gap
                self.unresolved.append(gap)
        self.counts["units (gosuMethod)"] = sum(1 for u in units if u["kind"] == "gosuMethod")
        self.counts["units (calcRoutine)"] = sum(1 for u in units if u["kind"] == "calcRoutine")
        self.counts["emissions"] = len(emissions)
        self.counts["emission key dimensions unresolved"] = sum(len(dims) for _, dims in self.unresolved_key_dims)
        self.counts["binds"] = sum(len(u.get("binds", [])) for u in units)
        self.counts["term reads joined to the capture"] = sum(1 for u in units for r in u["reads"] if r.get("covTermPattern"))
        self.units, self.emissions = units, emissions
        self.bind_ratebooks()
        for routine in units:
            if routine["kind"] == "calcRoutine" and "ratebook" not in routine:
                for caller in self.routine_callers.get(routine["key"], []):
                    caller["unresolved"].append(OrderedDict([
                        ("fact", "calcRoutine"), ("expression", routine["key"]),
                        ("reason", "no ratebook folder under the product root binds routines/%s.json, so what the routine reads and writes is unknown" % routine["key"]),
                        ("at", routine["at"])]))
        self.lookups = self.read_lookups(lookups_needed)
        self.call_trees = self.read_call_trees()
        self.read_value_origins()
        self.classify_cross_instance()
        for name in FACT_LISTS:
            self.counts["dataflow %s" % name] = sum(len(u.get(name, [])) for u in units if u["kind"] == "gosuMethod")
        self.flow = self.build_flow()
        self.units = [ordered_unit(u) for u in self.units]
        for method in ("rateOnly", "mergeCosts"):
            for j in by_name.get(method, []):
                if blocks[j]["cls"] == engine_cls and "override" in blocks[j]["modifiers"]:
                    self.unresolved.append("%s overrides %s: the platform loop is specialized; flow needs a consultant reading" % (engine, method))

    # -- dataflow facts ---------------------------------------------------------------------------

    # One row per distinct fact per unit, located at its first occurrence (the unit's own body
    # first, then the helpers it folds, in call order).
    FACT_IDENTITY = {
        "entityReads": lambda r: (r["entity"], r.get("property"), r.get("method")),
        "engineMembers": lambda r: (r["name"], r["access"]),
        "entityWrites": lambda r: (r["target"], r.get("property"), r.get("method")),
        "beanInserts": lambda r: (r["entity"],),
        "crossInstance": lambda r: (r["kind"], r["expression"]),
        "cacheAccess": lambda r: (r["member"], r["kind"]),
        "unresolved": lambda r: (r["fact"], r["expression"]),
    }

    def attach_dataflow(self, unit, rows):
        """Fill a gosuMethod unit's fact lists from its closure's rows; returns its collection reads."""
        lists = OrderedDict((name, []) for name in FACT_LISTS)
        seen = {name: set() for name in FACT_LISTS}
        collections = []
        for kind, row in rows:
            if kind == "collections":
                collections.append(row)
                continue
            identity = self.FACT_IDENTITY[kind](row)
            if identity in seen[kind]:
                continue
            seen[kind].add(identity)
            lists[kind].append(OrderedDict(row))
        unit.update(lists)
        return collections

    def loops_under_units(self):
        """unit key -> every loop (outermost first) any rating root's call path to it runs under."""
        out = OrderedDict()

        def visit(calls, outer):
            for call in calls:
                loops = outer + [l for l in call["loops"]]
                if call["targetKind"] == "unit":
                    bucket = out.setdefault(call["target"], [])
                    for l in loops:
                        if l not in bucket:
                            bucket.append(l)
                visit(call.get("calls", []), loops)
        for tree in self.call_trees:
            visit(tree["calls"], [])
        return out

    def classify_cross_instance(self):
        """Turn each gosuMethod unit's collection reads into crossInstance rows: every operation on
        an engine member's CostData list is a `total` (it reaches every cost rated so far), and an
        operation on a collection whose element type is the element type of a loop the unit runs
        under is a `sibling` read (`total` when it aggregates)."""
        loops_under = self.loops_under_units()
        for unit in self.units:
            if unit["kind"] != "gosuMethod":
                continue
            rows, seen = [], set()
            for c in self.unit_collections.get(unit["key"], []):
                typ, op = c["type"], c["op"]
                row = None
                if typ[0] == "costDatas" and typ[1]:
                    row = OrderedDict([("kind", "total"), ("expression", c["expression"]), ("element", "CostData")])
                elif typ[0] in ("entities", "classes"):
                    for loop in loops_under.get(unit["key"], []):
                        elem = loop.get("entity") if typ[0] == "entities" else loop.get("class")
                        if elem and (elem == typ[1] or typ[0] == "entities" and self.dataflow.related(elem, typ[1])):
                            row = OrderedDict([("kind", "total" if op in COLLECTION_AGGREGATES else "sibling"),
                                               ("expression", c["expression"]), ("element", typ[1]),
                                               ("loop", OrderedDict([("var", loop["var"]), ("in", loop["expr"])]))])
                            break
                if row is None or (row["kind"], row["expression"]) in seen:
                    continue
                seen.add((row["kind"], row["expression"]))
                row["at"] = c["at"]
                rows.append(row)
            unit["crossInstance"] = rows

    # -- value origins (C3) ----------------------------------------------------------------------

    ORIGIN_KINDS = ("callResult", "engineMember", "methodParameter", "loopVariable", "literal", "expression")
    ORIGIN_MAX_HOPS = 4

    def read_value_origins(self):
        """For every bind of a calcRoutine unit, where its value comes from, as one `from` line.
        A local is followed to its assignment and a method parameter to the same argument at
        every call site in the call trees that targets the method, until a call result, an engine
        member, a loop variable or a literal, or 4 hops. Anything else is an `expression` origin
        and an unresolved item. A helper that a local parameter map is passed to between its
        definition and the routine call is named as `mutated by`."""
        blocks = self.blocks
        key_index = {}
        for i, key in self.unit_key_of.items():
            key_index.setdefault(key, i)
        sites = []  # (owner key, call record)

        def collect(calls, owner):
            for call in calls:
                sites.append((owner, call))
                collect(call.get("calls", []), call["target"])
        for tree in self.call_trees:
            collect(tree["calls"], tree["method"])
        # engine members: the fields, `as` aliases and property getters of the engine class and its
        # gsrc base classes
        members = {name for name, decls in self.dataflow.own_members(last_segment(self.selected)).items()
                   if any(d[0] != "function" for d in decls)} if self.engine_path else set()
        walks, bodies = {}, {}

        def body_of(i):
            if i not in bodies:
                bodies[i] = blank_comments(blocks[i]["body"])
            return bodies[i]

        def statement_at(i, line):
            if i not in walks:
                walks[i] = MethodWalk(blocks[i])
            found = None
            for stmt in walks[i].statements:
                if stmt["line"] <= line:
                    found = stmt
            return found

        def loops_at(i, line):
            stmt = statement_at(i, line)
            if stmt is None:
                return []
            return list(stmt.get("bodyLoops") or stmt["loops"])

        def line_of(i, pos):
            return blocks[i]["start"] + body_of(i).count("\n", 0, pos)

        def local_value(i, name, line):
            """(initializer text, line) of the last `var name = ...` (or, for a declaration with no
            initializer, the last `name = ...`) at or before line; None when name is no local."""
            body = body_of(i)
            best = None
            for m in re.finditer(r"\bvar\s+%s\b\s*(?::\s*[^=\n]+?)?\s*(=\s*([^\n]+))?\s*$" % re.escape(name), body, re.M):
                at = line_of(i, m.start())
                if best is None or at <= line:
                    best = (m.group(2).strip() if m.group(2) else None, at, m.end())
            if best is None:
                return None
            if best[0] is not None:
                return best[0], best[1]
            later = None
            for m in re.finditer(r"^\s*%s\s*=(?!=)\s*([^\n]+)$" % re.escape(name), body[best[2]:], re.M):
                at = line_of(i, best[2] + m.start())
                if later is None or at <= line:
                    later = (m.group(1).strip(), at)
            return later

        def split_head(expr):
            """(head, rest) of a single member chain `head`, `head.X.y(z)`, `head(args).X` or
            `this.head ... as T`; None when the text is an operator expression, a construction or
            anything else that is not one chain."""
            e = re.sub(r"^this\s*\.\s*", "", expr.strip())
            e = re.sub(r"\s+as\s+[\w.]+(?:<.*>)?\s*$", "", e)
            m = re.match(r"[A-Za-z_]\w*", e)
            if not m or m.group(0) in ("new", "null", "true", "false", "typeof", "not"):
                return None
            blank = blank_strings(e)
            depth, j = 0, m.end()
            while j < len(blank):
                ch = blank[j]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif depth == 0:
                    if ch == "?" and blank[j + 1:j + 2] == ".":
                        j += 2
                        continue
                    if ch in "+-*/%<>=!&|?:,\\" or ch.isspace() and re.match(r"\s+(and|or|typeis)\b", blank[j:]):
                        return None
                j += 1
            return m.group(0), e[m.end():]

        def call_site_id(owner_key, target_key, stmt_line):
            for owner, call in sites:
                if owner == owner_key and call["target"] == target_key and call["line"] == stmt_line:
                    return call["id"]
            return None

        def origin(kind, i, line, text, chain, **fields):
            o = OrderedDict([("kind", kind)])
            for name in ("target", "callSite"):
                if fields.get(name):
                    o[name] = fields[name]
            if chain:
                o["chain"] = list(chain)
            if kind == "expression":
                o["expression"] = text
                o["verify"] = fields["verify"]
            o["at"] = self.ref(blocks[i]["path"], line)
            return o

        def resolve(i, text, line, loops, chain, visiting):
            text = squash(text.strip())
            if STRING_RE.fullmatch(text) or TC_LITERAL_RE.match(text) or re.fullmatch(r"-?\d[\w.]*|null|true|false", text):
                return [origin("literal", i, line, text, chain, target=text)]
            head = split_head(text)
            if head is None:
                return [origin("expression", i, line, text, chain,
                               verify="`%s` is not a single local, parameter, loop variable, engine member, call or literal; confirm where its value comes from" % text)]
            name, rest = head
            if rest.lstrip().startswith("("):
                if name not in self.by_name:
                    return [origin("expression", i, line, text, chain,
                                   verify="`%s` calls `%s`, which is not a method of the classes this run traversed; confirm what it returns" % (text, name))]
                arg_text = rest.lstrip()
                depth, j = 0, 0
                blank = blank_strings(arg_text)
                while j < len(blank):
                    depth += blank[j] == "("
                    depth -= blank[j] == ")"
                    j += 1
                    if depth == 0:
                        break
                nargs = len(split_args(arg_text[1:j - 1])) if arg_text[1:j - 1].strip() else 0
                stmt = statement_at(i, line)
                stmt_line = stmt["line"] if stmt else line
                out = []
                for target in self._resolve_calls(self._candidates(i, None, name), nargs):
                    target_key = self.unit_key_of[target]
                    out.append(origin("callResult", i, line, text, chain, target=target_key,
                                      callSite=call_site_id(self.unit_key_of[i], target_key, stmt_line)))
                return out or [origin("expression", i, line, text, chain,
                                      verify="`%s` does not resolve to one method of the traversed classes; confirm what it returns" % text)]
            loop = [l for l in loops if l["var"] == name]
            if loop:
                return [origin("loopVariable", i, line, text, chain, target=loop[-1]["expr"])]
            local = local_value(i, name, line)
            params = [pn for pn, _ in param_types(blocks[i]["params"])]
            if local is not None or name in params:
                if len(chain) >= self.ORIGIN_MAX_HOPS or (i, name) in visiting:
                    return [origin("expression", i, line, text, chain,
                                   verify="`%s` is not settled within %d hops; confirm where its value comes from" % (text, self.ORIGIN_MAX_HOPS))]
                next_chain, next_visiting = chain + [name], visiting | {(i, name)}
                if local is not None:
                    init, at = local
                    return resolve(i, init, at, loops_at(i, at), next_chain, next_visiting)
                pos = params.index(name)
                out, seen_sites = [], set()
                for owner, call in sites:
                    if call["target"] != self.unit_key_of[i] or call.get("recursive") or owner not in key_index:
                        continue
                    if pos >= len(call.get("arguments", [])):
                        continue
                    call_line = call["line"]
                    identity = (owner, call_line, call["arguments"][pos])
                    if identity in seen_sites:
                        continue
                    seen_sites.add(identity)
                    out += resolve(key_index[owner], call["arguments"][pos], call_line, call["loops"], next_chain, next_visiting)
                if out:
                    return out
                return [origin("methodParameter", i, line, text, chain, target=name)]
            if name in members:
                return [origin("engineMember", i, line, text, chain, target=name)]
            return [origin("expression", i, line, text, chain,
                           verify="`%s` is not a local, parameter, loop variable or engine member this run can see; confirm where its value comes from" % name)]

        def helper_mutations(t, map_expr, bindings):
            """mutatedBy per binding parameter: every helper call in block t that is passed the local
            map `map_expr` after its definition and before the last call that passes it on, where the
            helper's own body calls `.put(...)` on that parameter."""
            if not re.fullmatch(r"\w+", map_expr or ""):
                return {}
            body = body_of(t)
            dm = re.search(r"\bvar\s+%s\b" % re.escape(map_expr), body)
            if not dm:
                return {}
            passes = []  # (line, helper codes or None when the call is not a mutating helper)
            for m, args, pos, targets in self.calls_passing(t, map_expr, blank_strings(body), body, dm.end()):
                codes = None
                for target in targets:
                    tparams = [pn for pn, _ in param_types(blocks[target]["params"])]
                    if pos >= len(tparams):
                        continue
                    tbody = blank_comments(blocks[target]["body"])
                    puts = re.findall(r"\b%s\s*\.\s*put\s*\(\s*([\w.]+)" % re.escape(tparams[pos]), tbody)
                    if puts:
                        codes = (codes or set()) | {last_segment(p)[3:].lower() if last_segment(p).upper().startswith("TC_") else None for p in puts}
                passes.append((line_of(t, m.start(2)), codes))
            last_forward = max((line for line, codes in passes if codes is None), default=None)
            out = {}
            for line, codes in passes:
                if codes is None or last_forward is None or line > last_forward:
                    continue
                call_id = "%s:%d" % (self.unit_key_of[t], line)
                for pb in bindings:
                    if None in codes or pb["parameter"] in codes:
                        out.setdefault(pb["parameter"], []).append(call_id)
            return out

        def from_text(o):
            """One line: the origin kind and target, the flow call site it was read at, the local
            chain it was followed through, and the helpers that mutated the map on the way."""
            text = "%s %s" % (o["kind"], o.get("target", o.get("expression", "")))
            if o.get("callSite"):
                text += " at %s" % o["callSite"]
            if o.get("chain"):
                text += " through %s" % " → ".join(o["chain"])
            if o.get("mutatedBy"):
                text += ", mutated by %s" % ", ".join(o["mutatedBy"])
            return text

        for unit in self.units:
            if unit["kind"] != "calcRoutine":
                continue
            mutated = {}
            scopes = [self.binding_scopes.get(id(pb)) for pb in unit["binds"]]
            for scope in OrderedDict.fromkeys((s[1], s[2]) for s in scopes if s):
                for parameter, ids in helper_mutations(scope[0], scope[1], unit["binds"]).items():
                    mutated.setdefault(parameter, [])
                    mutated[parameter] += [x for x in ids if x not in mutated[parameter]]
            for pb, scope in zip(unit["binds"], scopes):
                if scope is None:
                    continue
                _, t, _, line = scope
                origins, seen = [], set()
                for o in resolve(t, pb["expression"], line, loops_at(t, line), [], frozenset()):
                    if mutated.get(pb["parameter"]):
                        o["mutatedBy"] = list(mutated[pb["parameter"]])
                    identity = json.dumps([o.get(k) for k in ("kind", "target", "callSite", "chain", "expression")])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    origins.append(o)
                if origins:
                    at = pb.pop("at")
                    pb["from"] = from_text(origins[0])
                    pb["at"] = at
                for o in origins:
                    if o["kind"] == "expression":
                        text = "value origin: %s parameter `%s` comes from the expression `%s` (%s); %s" % (
                            unit["key"], pb["parameter"], o["expression"], o["at"], o["verify"])
                        self.expression_origins.append(text)
                        self.unresolved.append(text)

    # -- ratebook join ---------------------------------------------------------------------------

    def ratebook_product_root(self):
        """Ratebook paths are relative to the product root (--product-root)."""
        return os.path.abspath(self.args.product_root)

    def read_ratebook_folders(self):
        """Every <folder>/book.json under <product-root>/extracted/ratebooks whose book.policyLine is --line or
        "" (a generic book), in folder-name order: [(folder name, book mapping)]."""
        folders = []
        root = self.args.ratebooks
        if not root:
            return folders
        for name in sorted(os.listdir(root)):
            book_path = os.path.join(root, name, "book.json")
            if not os.path.isfile(book_path):
                continue
            self.ratebook_files.append(book_path)
            try:
                with open(book_path, encoding="utf-8") as handle:
                    book = json.load(handle, object_pairs_hook=OrderedDict).get("book") or {}
            except (OSError, ValueError, AttributeError) as error:
                self.unresolved.append("ratebook folder %s: book.json cannot be read (%s); the folder is not searched" % (name, error))
                continue
            line = book.get("policyLine", None)
            if line not in (self.args.line, ""):
                continue
            folders.append((name, book))
        return folders

    def bind_ratebooks(self):
        """C2: bind each calcRoutine unit to the one ratebook folder holding routines/<code>.json,
        and fill its produces and reads from that routine's steps. A routine in no folder or in
        several binds nothing and is reported; a parameter mismatch is reported, never repaired."""
        folders = self.read_ratebook_folders()
        product_root = self.ratebook_product_root()
        routines = [u for u in self.units if u["kind"] == "calcRoutine"] if self.args.ratebooks else []
        for unit in routines:
            code = unit["key"]
            candidates = [(name, book) for name, book in folders
                          if os.path.isfile(os.path.join(self.args.ratebooks, name, "routines", code + ".json"))]
            if not candidates:
                self.ratebook_joins["missing"].append(code)
                continue
            folder_paths = [rel(os.path.join(os.path.abspath(self.args.ratebooks), name), product_root) for name, _ in candidates]
            if len(candidates) > 1:
                self.ratebook_joins["ambiguous"].append((code, folder_paths))
                self.replace_routine_gap(code, "calc routine %s: routines/%s.json is in %d ratebook folders (%s); none is bound until only one remains under <product-root>/extracted/ratebooks"
                                         % (code, code, len(candidates), ", ".join(folder_paths)))
                continue
            name, book = candidates[0]
            folder = folder_paths[0]
            routine_path = os.path.join(self.args.ratebooks, name, "routines", code + ".json")
            self.ratebook_files.append(routine_path)
            try:
                with open(routine_path, encoding="utf-8") as handle:
                    routine = json.load(handle, object_pairs_hook=OrderedDict)
            except (OSError, ValueError) as error:
                self.replace_routine_gap(code, "calc routine %s: %s/routines/%s.json cannot be read (%s); not bound" % (code, folder, code, error))
                self.ratebook_joins["missing"].append(code)
                continue
            with open(routine_path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            parameter_set = routine.get("parameterSet") or {}
            declared = [OrderedDict([("code", p["code"]), ("paramType", p["paramType"])]) for p in parameter_set.get("parameters", [])]
            steps = sorted(routine.get("steps", []), key=lambda step: step["order"])
            tables = []
            for step in steps:
                for operand in sorted(step.get("operands", []), key=lambda o: o["order"]):
                    if operand.get("tableCode") and operand["tableCode"] not in tables:
                        tables.append(operand["tableCode"])
            routine_ref_path = "%s/routines/%s.json" % (folder, code)
            ratebook = OrderedDict([("book", book["code"]), ("edition", book["edition"]), ("routine", routine_ref_path)])
            text_path = os.path.join(self.args.ratebooks, name, "routines", code + ".txt")
            if os.path.isfile(text_path):
                ratebook["text"] = "%s/routines/%s.txt" % (folder, code)
            else:
                self.unresolved.append("calc routine %s: %s/routines/%s.txt (the readable algorithm pc-ratebook-extraction writes beside the routine JSON) is missing; ratebook.text left out" % (code, folder, code))
            ratebook["sha256"] = digest
            ratebook["parameterSet"] = parameter_set.get("code")
            ratebook["parameters"] = declared
            unit["ratebook"] = ratebook
            unit["tables"] = tables
            writes, scratch = self.ratebook_produces(steps, parameter_set)
            unit["writes"] = writes + [w for w in unit["writes"] if w not in writes]
            scratch += [s for s in unit.get("scratch", []) if s not in scratch]
            if scratch:
                unit["scratch"] = scratch
            unit["reads"] = self.ratebook_reads(unit, steps, parameter_set, routine_ref_path) + unit["reads"]
            self.remove_routine_gap(code)
            bound_codes = {pb["parameter"] for pb in unit["binds"]}
            declared_codes = [p["code"] for p in declared]
            for pb in unit["binds"]:
                if pb["parameter"] not in declared_codes:
                    self.ratebook_joins["mismatches"].append((code, "bind `%s` is not declared by parameter set %s" % (pb["parameter"], parameter_set.get("code"))))
            for p in declared_codes:
                if p not in bound_codes:
                    self.ratebook_joins["mismatches"].append((code, "parameter set %s declares `%s`, which no bind binds" % (parameter_set.get("code"), p)))
            self.ratebook_joins["bound"].append(code)
        for code, text in self.ratebook_joins["mismatches"]:
            self.unresolved.append("calc routine %s: %s (%s)" % (code, text, self.unit_ratebook_path(code)))
        for code, text in self.ratebook_joins["untyped"]:
            self.unresolved.append("calc routine %s: %s" % (code, text))
        self.counts["ratebook binds"] = len(self.ratebook_joins["bound"])
        self.counts["ratebook routines missing"] = len(self.ratebook_joins["missing"])
        self.counts["ratebook routines ambiguous"] = len(self.ratebook_joins["ambiguous"])
        self.counts["ratebook parameter mismatches"] = len(self.ratebook_joins["mismatches"])
        self.counts["ratebook reads"] = sum(len(self.ratebook_read_rows(u)) for u in routines)
        self.counts["ratebook reads untyped"] = len(self.ratebook_joins["untyped"])

    def unit_ratebook_path(self, code):
        unit = next(u for u in self.units if u["key"] == code)
        return unit["ratebook"]["routine"]

    @staticmethod
    def ratebook_read_rows(unit):
        """The reads a bound routine's steps spelled: their `at` is the routine JSON plus a step."""
        return [r for r in unit["reads"] if "#step " in r.get("at", "")]

    def replace_routine_gap(self, code, text):
        gap = self.routine_gaps.get(code)
        if gap in self.unresolved:
            self.unresolved[self.unresolved.index(gap)] = text
        else:
            self.unresolved.append(text)
        self.routine_gaps[code] = text

    def remove_routine_gap(self, code):
        gap = self.routine_gaps.pop(code, None)
        if gap in self.unresolved:
            self.unresolved.remove(gap)

    @staticmethod
    def ratebook_produces(steps, parameter_set):
        """(writes, scratch): one CostData member per distinct in-scope value an assignment step
        stores on costdata; one scratch value <param>.<value> per distinct value stored on another
        writable parameter."""
        writable = {p["code"] for p in parameter_set.get("parameters", []) if p.get("writable") is True}
        writes, scratch = [], []
        for step in steps:
            if step.get("stepType") != "assignment" or not step.get("inScopeParam") or not step.get("inScopeValue"):
                continue
            if step["inScopeParam"] == "costdata":
                if step["inScopeValue"] not in writes:
                    writes.append(step["inScopeValue"])
            elif step["inScopeParam"] in writable:
                name = "%s.%s" % (step["inScopeParam"], step["inScopeValue"])
                if name not in scratch:
                    scratch.append(name)
        return writes, scratch

    def ratebook_reads(self, unit, steps, parameter_set, routine_ref_path):
        """One typed read per distinct in-scope value an operand (or an operand's argument) reads
        from a parameter other than costdata, located by the first step that holds it. A value the
        parameter set and bindings cannot type is reported, never guessed. A bare parameter with no
        value is the parameter itself, already recorded as a parameterBinding."""
        code = unit["key"]
        params = {p["code"]: p for p in parameter_set.get("parameters", [])}
        bindings = {pb["parameter"]: pb for pb in unit["binds"]}
        reads, seen, untyped = [], set(), set()
        for step in steps:
            holders = []
            for operand in sorted(step.get("operands", []), key=lambda o: o["order"]):
                holders.append(operand)
                holders.extend(operand.get("arguments", []))
            for holder in holders:
                param, value = holder.get("inScopeParam", ""), holder.get("inScopeValue", "")
                if not param or param == "costdata" or not value:
                    continue
                typed = None
                declared = params.get(param, {})
                if holder.get("inScopeValueIsModifier") is True:
                    typed = [OrderedDict([("modifierPattern", value)])]
                elif holder.get("covTermCode"):
                    clause = self.settle_term_clause(declared, holder["covTermCode"])
                    if clause:
                        typed = [OrderedDict([("clausePattern", clause), ("covTermPattern", holder["covTermCode"])])]
                else:
                    binding = bindings.get(param)
                    if binding and binding.get("kind") == "entity" and "." not in value:
                        if declared.get("useWrapper") is True and declared.get("wrapperClass"):
                            # A wrapper property is what its getter reads, never a property of the wrapped entity.
                            typed = self.wrapper_reads(declared["wrapperClass"], value, binding["type"])
                        else:
                            typed = [OrderedDict([("entity", binding["type"]), ("property", value)])]
                if typed is None:
                    what = "%s.%s" % (param, value)
                    if what not in untyped:
                        untyped.add(what)
                        self.ratebook_joins["untyped"].append((code, "%s step %d reads `%s`, which the parameter set and binds do not type as an entity property, a modifier, a captured coverage term or a wrapper getter's reads" % (routine_ref_path, step["order"], what)))
                    continue
                for row in typed:
                    identity = tuple(row.items())
                    if identity in seen:
                        continue
                    seen.add(identity)
                    row["at"] = "%s#step %d" % (routine_ref_path, step["order"])
                    reads.append(row)
        return reads

    def wrapper_reads(self, wrapper_class, name, entity):
        """What a wrapper class's `property get <name>()` reads, one row per `case`/return: a
        returned `<field>.<Code>Term...` is the coverage term {clausePattern, covTermPattern}, the
        clause being the case's pattern when it declares the term, else the one captured clause
        that does; a returned `<field>.<Property>` is that property of the bound entity. Literal
        returns read nothing, so a getter that returns only literals reads []. None when the getter is
        absent or any return is something else."""
        path = self.class_index.get(last_segment(wrapper_class))
        if not path:
            return None
        text = read_text(path)
        fields = set(re.findall(r"^\s*(?:(?:private|protected|public|internal|static)\s+)*var\s+(\w+)\s*:", structural_text(text), re.M))
        blocks = [b for b in method_blocks(text) if b["kind"] == "property" and b["name"] == name]
        if len(blocks) != 1:
            return None
        rows, when = [], None
        for line in blank_comments(blocks[0]["body"]).split("\n"):
            case = re.match(r"\s*case\s+(\w+)\s*:", line)
            if case:
                when = case.group(1)
            elif re.match(r"\s*default\s*:", line):
                when = None
            for match in re.finditer(r"\breturn\s+([^;\n]+)", line):
                expression = match.group(1).strip()
                if re.match(r"^(true|false|null|-?\d[\d.]*|\"[^\"]*\")$", expression):
                    continue
                term = re.match(r"^(\w+)\.(\w+)Term(?:\.\w+)*$", expression)
                plain = re.match(r"^(\w+)\.(\w+)$", expression)
                if term and term.group(1) in fields:
                    clause = (self.settle_term_clause({"coveragePattern": when}, term.group(2)) if when in self.pm["clauses"] else None) \
                        or self.settle_term_clause({}, term.group(2))
                    if not clause:
                        return None
                    row = OrderedDict([("clausePattern", clause), ("covTermPattern", term.group(2))])
                elif plain and plain.group(1) in fields:
                    row = OrderedDict([("entity", entity), ("property", plain.group(2))])
                else:
                    return None
                if row not in rows:
                    rows.append(row)
        return rows

    def settle_term_clause(self, parameter, term_code):
        """The captured clause pattern that owns a cov term: the parameter's coveragePattern when it
        declares the term, else the one captured clause whose terms include it. None otherwise."""
        def has_term(clause_code):
            clause = self.pm["clauses"].get(clause_code)
            return bool(clause) and any(t.get("codeIdentifier") == term_code for t in clause.get("terms", []) or [])
        pattern = parameter.get("coveragePattern") or ""
        if pattern:
            return pattern if has_term(pattern) else None
        owners = [c for c in self.pm["clauses"] if has_term(c)]
        return owners[0] if len(owners) == 1 else None

    def cost_entity_from_class(self, cd):
        path = self.class_index.get(cd)
        if not path:
            return None, None
        text = read_text(path)
        # `class X extends Y<Cost>` or `class X<R extends Cost> extends Y<R>`: the Cost entity is the
        # type argument, or the bound of the type parameter the argument names.
        m = re.search(r"class\s+%s\s*(?:<\s*(\w+)\s+extends\s+([\w.]+)[^>]*>)?\s+extends\s+[\w.]+\s*<\s*([\w.]+)" % re.escape(cd), text)
        if m:
            entity = m.group(3)
            if m.group(1) and entity == m.group(1):
                entity = m.group(2)
            return entity.split(".")[-1], self.ref(path, text.count("\n", 0, m.start()) + 1)
        return None, None

    def subclass_key_dimensions(self, cd):
        """Expressions listed in the CostData subclass's KeyValues getter, verbatim, as dimension names."""
        path = self.class_index.get(cd)
        if not path:
            return []
        text = read_text(path)
        for b in method_blocks(text):
            if b["name"] == "KeyValues":
                m = re.search(r"return\s*\{([^}]*)\}", blank_comments(b["body"]))
                if m:
                    return [x.strip() for x in split_args(m.group(1)) if x.strip()]
        return []

    # -- mechanical platform key values ----------------------------------------------------------
    #
    # A platform dimension (Currency, ChargePattern, ChargeGroup, RateAmountType, BillGroup) gets a
    # `value` only from a typecode literal that is (a) assigned to that dimension in the CostData
    # subclass's own constructor chain (the constructor and any same-class function it calls, e.g.
    # init()), or (b) forwarded through a constructor parameter that chain assigns to the
    # dimension, with the literal read off the emitting `new X(` call at that parameter's position,
    # or (c) assigned directly in the emitting unit's own body as `costData.Dim = TC_X`. A
    # constructor parameter typed exactly as the dimension name (PC's own convention,
    # e.g. `c : Currency`) still names the call-site expression in the verify note even when no
    # assignment is provable within this traversal's scope (the platform CostData base under
    # gsrc/gw/rating/ is never opened to find it). CostCode is handled separately, upstream, for
    # the classic-generation-mode case; every other dimension this cannot say anything sharper
    # about is simply absent here, and the caller keeps today's generic verify text for it.

    def _kv_class_functions(self, cls_name, path):
        """cls_name's own function blocks (never its constructors), name -> [blocks], preferring
        the blocks already tagged during this engine's traversal; falls back to a fresh parse when
        the class's file sits outside the engine's directory tree (still in scope: the CostData
        class's own file, wherever it is under gsrc)."""
        cache = self.__dict__.setdefault("_kv_functions_cache", {})
        if cls_name in cache:
            return cache[cls_name]
        found = [b for b in self.blocks if b["cls"] == cls_name]
        if not found and path:
            text = self.texts.get(path)
            if text is None:
                text = read_text(path)
            blocks, _ = tagged_blocks(path, text)
            found = [b for b in blocks if b["cls"] == cls_name]
        by_name = OrderedDict()
        for b in found:
            by_name.setdefault(b["name"], []).append(b)
        cache[cls_name] = by_name
        return by_name

    def _kv_constructors(self, cls_name, path):
        """cls_name's own `construct(...)` blocks, in file declaration order."""
        cache = self.__dict__.setdefault("_kv_constructors_cache", {})
        if path not in cache:
            text = self.texts.get(path)
            if text is None:
                text = read_text(path)
            synth = CONSTRUCT_HEADER_RE.sub(r"\1function __construct__\2", text)
            raw = [b for b in method_blocks(synth) if b["name"] == "__construct__"]
            spans = class_spans(text)
            orig_lines = text.split("\n")
            for b in raw:
                inner = None
                for span in spans:
                    if span[3] <= b["start"] <= span[4] and (inner is None or span[3] > inner[3]):
                        inner = span
                b["cls"] = inner[0] if inner else os.path.basename(path).rsplit(".", 1)[0]
                b["path"] = path
                b["body"] = "\n".join(orig_lines[b["start"] - 1:b["end"]])
            cache[path] = raw
        return [b for b in cache[path] if b["cls"] == cls_name]

    @staticmethod
    def _dim_assignment(body, dim):
        """The RHS expression of a bare `Dim = expr` or `this.Dim = expr` statement in body, and
        its 0-based line offset within body, or None. A self-referential RHS (the getter, e.g.
        `ChargePattern = this.ChargePattern`) is never returned: it is not a value, mechanically or
        otherwise."""
        pattern = re.compile(r"(?m)^\s*(?:this\.)?%s\s*=(?!=)\s*([^\n;]+)" % re.escape(dim))
        stripped = blank_comments(body)
        m = pattern.search(stripped)
        if not m:
            return None
        rhs = m.group(1).strip().rstrip(",;").strip()
        if rhs in ("this.%s" % dim, dim):
            return None
        return rhs, stripped.count("\n", 0, m.start())

    @staticmethod
    def _same_class_calls(body, functions):
        """[(name, args)] for every bare/`this.`-receiver call in body to a function declared by
        the same class (by name; `functions` is that class's name -> [blocks])."""
        out = []
        stripped = blank_comments(body)
        for m in CALL_RE.finditer(stripped):
            receiver, name = m.group(1), m.group(2)
            if receiver not in (None, "this") or name not in functions:
                continue
            j = paren_end(stripped, m.end() - 1)
            out.append((name, split_args(stripped[m.end():j - 1])))
        return out

    def _resolve_dimension_chain(self, cd, path, ctor, dim, own_param_names):
        """Search the constructor's own body, then one hop into any same-class function it calls,
        for a bare assignment to `dim`. Returns ('literal', TC_CODE), ('param', ctorParamName),
        ('expr', expr), or None when no assignment to `dim` is found anywhere in that (bounded)
        search."""
        found = self._dim_assignment(ctor["body"], dim)
        if found:
            rhs, _ = found
            lit = TC_LITERAL_RE.match(rhs)
            if lit:
                return "literal", lit.group("code")
            if rhs in own_param_names:
                return "param", rhs
            return "expr", rhs
        functions = self._kv_class_functions(cd, path)
        for name, call_args in self._same_class_calls(ctor["body"], functions):
            candidates = [b for b in functions[name] if len(param_types(b["params"])) == len(call_args)] or functions[name]
            for fn in candidates:
                found = self._dim_assignment(fn["body"], dim)
                if not found:
                    continue
                rhs, _ = found
                lit = TC_LITERAL_RE.match(rhs)
                if lit:
                    return "literal", lit.group("code")
                fn_params = [p for p, _ in param_types(fn["params"])]
                if rhs not in fn_params:
                    return "expr", rhs
                pos = fn_params.index(rhs)
                if pos >= len(call_args):
                    continue
                arg_expr = call_args[pos].strip()
                lit2 = TC_LITERAL_RE.match(arg_expr)
                if lit2:
                    return "literal", lit2.group("code")
                if arg_expr in own_param_names:
                    return "param", arg_expr
                return "expr", arg_expr
        return None

    def _post_construction_assignment(self, cpath, con_line, cd, dim):
        """A `<var>.Dim = TC_X` in the emitting unit's own body, immediately after the `var <var> =
        new cd(`/`<var> = new cd(` at con_line. Returns (TC_CODE, line) or None."""
        owner = None
        for b in self.blocks:
            if b["path"] == cpath and b["start"] <= con_line <= b["end"]:
                if owner is None or (b["end"] - b["start"]) < (owner["end"] - owner["start"]):
                    owner = b
        if owner is None:
            return None
        lines = owner["body"].split("\n")
        idx = con_line - owner["start"]
        if idx < 0 or idx >= len(lines):
            return None
        m = re.search(r"\b(\w+)\s*=\s*new\s+%s\s*\(" % re.escape(cd), lines[idx])
        if not m:
            return None
        var = m.group(1)
        pattern = re.compile(r"^\s*%s\s*\.\s*%s\s*=(?!=)\s*([^\n;]+)" % (re.escape(var), re.escape(dim)))
        for j in range(idx + 1, len(lines)):
            mm = pattern.match(blank_comments(lines[j]))
            if mm:
                rhs = mm.group(1).strip().rstrip(",;").strip()
                lit = TC_LITERAL_RE.match(rhs)
                if lit:
                    return lit.group("code"), owner["start"] + j
                return None
        return None

    def _resolve_one_dimension(self, cd, path, ctor, dim, params, own_param_names, call_args, call_path, call_line):
        """The dimension's value as the source spells it: a typecode literal (TC_X), or the
        verbatim expression the constructor chain or the emitting call assigns it from; None when
        nothing in scope assigns it."""
        nargs = len(call_args)
        resolved = self._resolve_dimension_chain(cd, path, ctor, dim, own_param_names)
        if resolved is not None:
            kind, payload = resolved
            if kind == "literal":
                return payload
            if kind == "param":
                pos = own_param_names.index(payload)
                if pos < nargs:
                    expr = call_args[pos].strip()
                    lit = TC_LITERAL_RE.match(expr)
                    return lit.group("code") if lit else expr
            # kind == "expr" (or a 'param' position outside the emitting call's own arity, which
            # the matching-arity selection below makes unreachable in practice): an assignment
            # exists within scope but resolves to neither a literal nor a traceable ctor parameter.
            return payload
        post = self._post_construction_assignment(call_path, call_line, cd, dim)
        if post is not None:
            return post[0]
        # No provable assignment anywhere in scope. A constructor parameter typed exactly as the
        # dimension is still the mechanical binding PC's own CostData constructors use
        # for it; name that call-site expression rather than leave the dimension unresolved.
        pos = next((idx for idx, (_, ptype) in enumerate(params) if ptype == dim), None)
        if pos is not None and pos < nargs:
            expr = call_args[pos].strip()
            lit = TC_LITERAL_RE.match(expr)
            return lit.group("code") if lit else expr
        return None

    def resolve_key_value_sources(self, cd, call_args, call_path, call_line):
        """dim -> value (a TC_X literal or the verbatim source expression) for the platform key
        dimensions this can resolve mechanically. A dimension this cannot say anything about is
        simply absent; the caller reports it."""
        out = OrderedDict()
        path = self.class_index.get(cd)
        if not path:
            return out
        ctors = self._kv_constructors(cd, path)
        if not ctors:
            return out
        nargs = len(call_args)
        exact = [c for c in ctors if len(param_types(c["params"])) == nargs]
        if not exact:
            return out
        ctor = exact[0]
        params = param_types(ctor["params"])
        own_param_names = [p for p, _ in params]
        for dim in PLATFORM_KEY_DIMENSIONS:
            if dim == "CostCode":
                continue
            entry = self._resolve_one_dimension(cd, path, ctor, dim, params, own_param_names, call_args, call_path, call_line)
            if entry is not None:
                out[dim] = entry
        return out

    def read_lookups(self, needed):
        table = os.path.join(self.config_root, "config", "resources", "systables", "rating_adj_factors.xml")
        shapes = {}
        if os.path.exists(table) and needed:
            root = parse_xml(table)
            for row in root:
                name = None
                cols = []
                for c in row:
                    tag = local(c.tag)
                    if tag == "factorName":
                        name = (c.text or "").strip()
                    elif (c.text or "").strip() and tag not in ("factor", "effDate", "expDate"):
                        cols.append(tag)
                if name:
                    shapes.setdefault(name, OrderedDict())
                    for c in cols:
                        shapes[name][c] = True
        lookups = []
        for factor, uses in needed.items():
            rec = OrderedDict()
            rec["key"] = factor
            rec["kind"] = "rateAdjFactor"
            rec["factorName"] = factor
            dims = [OrderedDict([("name", d)]) for d in shapes.get(factor, {})]
            rec["dimensions"] = dims
            rec["resultColumns"] = ["factor"]
            if factor not in shapes:
                self.unresolved.append("lookup %s: no row in rating_adj_factors.xml carries this factorName; dimensions unknown" % factor)
            rec["at"] = self.ref(uses[0][2], uses[0][1])
            lookups.append(rec)
        self.counts["lookups (rateAdjFactor)"] = len(lookups)
        return lookups

    def read_call_trees(self):
        """The full call trees, one per rating root the engine defines (rateSlice, rateWindow),
        kept in memory for build_flow and read_value_origins. A call site is recorded when its
        target is a unit, or a method whose own call sites reach one; its guards and loops are
        relative to the method body it sits in, and the target method's own call sites nest under
        it (re-expanded at every site, because a forwarded routine code resolves through the
        arguments of the sites above). A calc routine execution is a call site too."""
        blocks = self.blocks
        engine_cls = last_segment(self.selected)
        unit_keys = {u["key"] for u in self.units}
        walks = {}

        def walk_of(i):
            if i not in walks:
                b = blocks[i]
                scan = BlockScan(self.dataflow, i)
                scan.collect()
                line_starts = [0] + [m.end() for m in re.finditer("\n", b["body"])]

                def loop_type(expr, bindings, line):
                    """The captured entity or gsrc class a loop's collection holds, typed like the
                    dataflow facts are (the line, members, locals, loop and lambda variables)."""
                    offset = line_starts[min(line - b["start"], len(line_starts) - 1)] if line else scan.start
                    typ = BlockScan.element(scan.expr_type(expr, offset))
                    return typ if typ and (typ[0] == "class" or typ[1] in self.pm["entities"]) else None
                walks[i] = MethodWalk(b, loop_type)
            return walks[i]

        def split_call_args(text, blank, open_idx, close_idx):
            """Top-level argument texts between the parentheses at open_idx and close_idx."""
            out, depth, start = [], 0, open_idx + 1
            for j in range(open_idx + 1, close_idx):
                ch = blank[j]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "," and depth == 0:
                    out.append(squash(text[start:j]))
                    start = j + 1
            last = squash(text[start:close_idx])
            if last or out:
                out.append(last)
            return out

        def local_init(i, name):
            body = blank_comments(blocks[i]["body"])
            m = re.search(r"\bvar\s+%s\s*(?::\s*[\w.<>]+\s*)?=\s*([^\n]+)" % re.escape(name), body)
            return m.group(1).strip() if m else None

        def routine_codes(i, expr, env, depth=0):
            """Routine codes `expr` spells in block i, following a parameter to the argument its
            caller passed at the call site above (env) and a local to its initializer."""
            expr = expr.strip()
            if depth > 8:
                return []
            if expr in [pn for pn, _ in param_types(blocks[i]["params"])]:
                if expr in env:
                    caller, arg, caller_env = env[expr]
                    return routine_codes(caller, arg, caller_env, depth + 1)
                return []
            resolved = self._resolve_routine_expr(i, expr)
            if resolved is not None:
                return [code for code, _, _ in resolved]
            init = local_init(i, expr)
            if init and init != expr:
                return routine_codes(i, init, env, depth + 1)
            return []

        def reaches_unit(i, seen):
            if self.unit_key_of[i] in unit_keys:
                return True
            if i in seen:
                return False
            seen.add(i)
            if any(code in unit_keys for code, *_ in self.facts[i]["routines"]):
                return True
            return any(reaches_unit(callee, seen) for callee, _, _ in self.facts[i]["calls"])

        def sites_in(i, text, guards, loops, env, path, line):
            """The recorded call sites of one statement's expression text, in evaluation order."""
            blank = blank_strings(text)
            found = []  # (end, start, open, close, record)
            owner_key = self.unit_key_of[i]

            def close_of(open_idx):
                j = paren_end(blank, open_idx)
                return j - 1

            def site(target, kind, arguments, block_id=None):
                return OrderedDict([("id", None), ("target", target), ("targetKind", kind), ("arguments", arguments),
                                    ("guards", list(guards)), ("loops", [OrderedDict(l) for l in loops]), ("calls", []),
                                    ("path", blocks[i]["path"]), ("line", line), ("block", block_id)])

            for m in CALL_RE.finditer(blank):
                receiver, name = m.group(1), m.group(2)
                open_idx = m.end() - 1
                close_idx = close_of(open_idx)
                arguments = split_call_args(text, blank, open_idx, close_idx)
                if name == "executeCalcRoutine":
                    for code in OrderedDict.fromkeys(routine_codes(i, arguments[0], env) if arguments else []):
                        if code in unit_keys:
                            found.append((close_idx, m.start(2), open_idx, close_idx, site(code, "unit", arguments)))
                    continue
                if name not in self.by_name:
                    continue
                cands = self._candidates(i, receiver, name)
                if not cands:
                    continue
                for t in self._resolve_calls(cands, len(arguments)):
                    rec = method_site(i, t, arguments, env, path, site)
                    if rec is not None:
                        found.append((close_idx, m.start(2), open_idx, close_idx, rec))
            for m in PROPERTY_RE.finditer(blank):
                receiver, name = m.group(1), m.group(2)
                if name not in self.by_name:
                    continue
                for t in [c for c in self._candidates(i, receiver, name) if blocks[c]["kind"] == "property"]:
                    rec = method_site(i, t, [], env, path, site)
                    if rec is not None:
                        found.append((m.end(2), m.start(2), None, None, rec))
            found.sort(key=lambda f: (f[0], f[1]))
            for end, start, _, _, rec in found:
                enclosing = [f for f in found if f[2] is not None and f[2] < start and end <= f[3] and f[4] is not rec]
                if enclosing:
                    rec["_argumentOf"] = max(enclosing, key=lambda f: f[2])[4]
            return [f[4] for f in found]

        def method_site(i, t, arguments, env, path, site):
            key = self.unit_key_of[t]
            kind = "unit" if key in unit_keys else "method"
            if t in path:
                if kind == "unit" or reaches_unit(t, set()):
                    rec = site(key, kind, arguments, t)
                    rec["_recursive"] = True
                    return rec
                return None
            params = [pn for pn, _ in param_types(blocks[t]["params"])]
            child_env = {pn: (i, arguments[pos], env) for pos, pn in enumerate(params) if pos < len(arguments)}
            children = calls_of(t, child_env, path + [t])
            if kind == "method" and not children:
                return None
            rec = site(key, kind, arguments, t)
            rec["calls"] = children
            return rec

        def calls_of(i, env, path):
            out = []
            for stmt in walk_of(i).statements:
                if stmt["kind"] in ("log", "assert", "else", "try", "finally", "block", "catch", "case", "default"):
                    continue
                out += sites_in(i, stmt["text"], stmt["guards"], stmt["loops"], env, path, stmt["line"])
                if stmt["kind"] == "each-inline":
                    out += sites_in(i, stmt["body"], stmt["guards"], stmt["bodyLoops"], env, path, stmt["line"])
            return out

        trees = []
        for root, mode in (("rateSlice", "sliceMode"), ("rateWindow", "windowMode")):
            ids = [j for j in self.by_name.get(root, []) if blocks[j]["cls"] == engine_cls]
            if not ids:
                continue
            trees.append(OrderedDict([("root", root), ("method", self.unit_key_of[ids[0]]), ("rateMode", mode),
                                      ("block", ids[0]), ("at", self.ref(blocks[ids[0]]["path"], blocks[ids[0]]["start"])),
                                      ("calls", calls_of(ids[0], {}, [ids[0]]))]))

        # ids: <method key>:<line>, numbered #1..#n in document order when several call sites share
        # one (the `from` text of a bind names the site it was read at)
        ordered = []

        def collect(calls, owner):
            for call in calls:
                ordered.append((owner, call))
                collect(call["calls"], call["target"])
        for tree in trees:
            collect(tree["calls"], tree["method"])
        bases = Counter()
        for owner, call in ordered:
            call["_base"] = "%s:%d" % (owner, call["line"])
            bases[call["_base"]] += 1
        seen = Counter()
        for owner, call in ordered:
            base = call.pop("_base")
            seen[base] += 1
            call["id"] = base if bases[base] == 1 else "%s#%d" % (base, seen[base])
        unresolved_loops = OrderedDict()
        for owner, call in ordered:
            call.pop("_argumentOf", None)
            call["recursive"] = bool(call.pop("_recursive", False))
            for loop in call["loops"]:
                if loop.get("entity") is None and loop.get("class") is None:
                    unresolved_loops.setdefault((owner, loop["var"], loop["expr"]), call["id"])
        for (owner, var, expr), first in unresolved_loops.items():
            self.unresolved.append("flow: loop `%s` over `%s` in %s (first at call site %s) holds no captured entity and no gsrc class this run can establish" % (
                var, expr, owner, first))
        self.counts["unresolved loop entities"] = len(unresolved_loops)
        return trees

    def build_flow(self):
        """The document's call graph from the full call trees: `flow.<root>.calls` holds the
        root's edges, and each gosuMethod unit's `calls` holds its own edges once (the union over
        every site that targets it, since a forwarded routine code can resolve differently per
        site). An edge whose target is a unit stops there; a non-unit helper's own sites nest under
        the edge's `calls`. When the callee selects a CostData class by a `switch` on a parameter
        and the site passes a string literal for it, the edge carries `emits`."""
        unit_by_key = {u["key"]: u for u in self.units}
        edges_total, guards_total, loops_total = [0], [0], [0]

        def loop_of(loop):
            out = OrderedDict([("var", loop["var"]), ("in", loop["expr"])])
            if loop.get("entity"):
                out["entity"] = loop["entity"]
            elif loop.get("class"):
                out["class"] = loop["class"]
            else:
                out["verify"] = "`%s` holds no captured entity and no gsrc class this run can establish; confirm what `%s` iterates" % (
                    loop["expr"], loop["var"])
            if loop.get("parallel"):
                out["parallel"] = True
            return out

        def emits_of(rec):
            block = rec.get("block")
            if block is None:
                return None
            for pos, literal, cd in self.switch_emits.get(block, []):
                if pos < len(rec["arguments"]) and rec["arguments"][pos] == '"%s"' % literal:
                    entity = self.cost_entity_name.get(cd, (None, None))[0]
                    if entity:
                        return entity
            return None

        def edge_of(rec):
            unit = unit_by_key.get(rec["target"])
            edge = OrderedDict()
            edge["routine" if unit and unit["kind"] == "calcRoutine" else "call"] = rec["target"]
            if rec["arguments"]:
                edge["args"] = list(rec["arguments"])
            if rec["loops"]:
                edge["for"] = [loop_of(l) for l in rec["loops"]]
            if rec["guards"]:
                edge["when"] = list(rec["guards"])
            emits = emits_of(rec)
            if emits:
                edge["emits"] = emits
            if rec["targetKind"] != "unit" and rec["calls"]:
                edge["calls"] = [edge_of(c) for c in rec["calls"]]
            edge["at"] = self.ref(rec["path"], rec["line"])
            return edge

        def count(edges):
            for edge in edges:
                edges_total[0] += 1
                guards_total[0] += len(edge.get("when", []))
                loops_total[0] += len(edge.get("for", []))
                count(edge.get("calls", []))

        # every site targeting a unit, in document order, for that unit's own edges
        unit_sites = OrderedDict()

        def collect(calls):
            for call in calls:
                if call["targetKind"] == "unit":
                    unit_sites.setdefault(call["target"], []).append(call)
                collect(call["calls"])

        flow = OrderedDict()
        for tree in self.call_trees:
            flow[tree["root"]] = OrderedDict([("at", tree["at"]), ("calls", [edge_of(c) for c in tree["calls"]])])
            collect(tree["calls"])
            root_unit = unit_by_key.get(tree["method"])
            if root_unit is not None:
                root_unit["calls"] = [edge_of(c) for c in tree["calls"]]
        for key, sites in unit_sites.items():
            unit = unit_by_key[key]
            if unit["kind"] != "gosuMethod" or unit.get("calls"):
                continue
            edges, seen = [], set()
            for site in sites:
                for child in site["calls"]:
                    edge = edge_of(child)
                    identity = json.dumps(edge)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    edges.append(edge)
            unit["calls"] = edges
        for root in flow.values():
            count(root["calls"])
        for unit in self.units:
            if unit["kind"] == "gosuMethod" and unit["key"] not in {t["method"] for t in self.call_trees}:
                count(unit["calls"])
        self.counts["flow edges"] = edges_total[0]
        self.counts["flow edge guards"] = guards_total[0]
        self.counts["flow edge loops"] = loops_total[0]
        return flow

    # -- run --------------------------------------------------------------------------------------

    def run(self):
        if self.args.survey:
            self.survey()
            return
        engine = self.args.engine or self.decisions.get("selectedEngine")
        if not engine:
            die("no engine selected: pass --engine or set selectedEngine in the decisions file (it names the output file extracted/rating-process-<EngineClass>.json)")
        document = os.path.join(self.args.product_root, "extracted", "rating-process-%s.json" % last_segment(engine))
        if os.path.exists(document) and not self.args.force:
            die("%s exists; pass --force to rewrite it" % document)
        revision, dirty = self.revision()
        self.revision_sha = revision
        self.read_product_capture(revision)
        engine = self.read_dispatch()
        self.by_name, self.unit_ids, self.unit_key_of = {}, [], {}
        if self.engine_path:
            self.analyse_engine()
            if not self.units:
                defined = [name for name in ROOT_METHODS if name in self.engine_defines]
                overridden = [name for name in OVERRIDE_WATCH if name in self.engine_defines]
                die("%s yields no units: the traversal from %s found no method that constructs a CostData, "
                    "calls a calc routine, or is scoped to a captured clause pattern, in %s or any class under its "
                    "directory. Override members seen on %s: %s. Nothing was written."
                    % (self.selected, "/".join(defined) if defined else "rateSlice/rateWindow/rateOnly (none defined)",
                       rel(self.engine_path, self.source_root), self.selected, ", ".join(overridden) or "none"))
        else:
            self.units, self.emissions, self.lookups, self.reached, self.blocks, self.call_trees = [], [], [], {}, [], []
            self.flow = OrderedDict([("rateSlice", OrderedDict([("at", self.ref(self.gsrc, symbol="rateSlice")), ("calls", [])]))])
        self.resolve_cross_document_references()

        doc = OrderedDict()
        doc["$schema"] = SCHEMA_URI
        doc["schemaVersion"] = SCHEMA_VERSION
        doc["product"] = self.args.product
        doc["line"] = self.args.line
        doc["revision"] = revision
        doc["dirty"] = dirty
        doc["productModel"] = OrderedDict([
            ("capture", self.capture_path), ("revision", self.pm["configuration"]["revision"]), ("dirty", self.pm["configuration"]["dirty"]),
            ("product", self.pm["product"]["codeIdentifier"]), ("policyLinePattern", self.pm["line"]["codeIdentifier"])])
        doc["engine"] = engine
        doc["dispatch"] = self.dispatch
        gates = self.pick("assumedGates")
        if gates:
            doc["assumedGates"] = gates
        elif any(s.get("when") for s in self.dispatch):
            self.unresolved.append("createRatingEngine has gated branches; the gate values assumed for the selected engine go in the decisions file `assumedGates`")
        doc["flow"] = self.flow
        doc["units"] = self.units
        doc["emissions"] = self.emissions
        doc["lookups"] = self.lookups
        pins = OrderedDict()
        pins["sourceRoot"] = self.args.source_root.replace(os.sep, "/")  # as passed: the anchor every checkout locator is relative to
        pins["configurationRoot"] = CONFIGURATION_ROOT
        pins["generationMode"] = self.pm["configuration"].get("generationMode", "classic")
        ratebook_files = self.ratebook_file_digests()
        if ratebook_files:
            pins["ratebookFiles"] = ratebook_files
        doc["pins"] = pins
        self.doc = doc

        write_json(document, doc)
        self.print_summary(document)

    @staticmethod
    def digest(path):
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def ratebook_file_digests(self):
        """C4: {path, sha256} for every ratebook file this run read, relative to the product root,
        sorted by path."""
        if not self.ratebook_files:
            return []
        product_root = self.ratebook_product_root()
        paths = sorted({rel(os.path.abspath(p), product_root) for p in self.ratebook_files if os.path.isfile(p)})
        self.counts["ratebook files digested"] = len(paths)
        return [OrderedDict([("path", p), ("sha256", self.digest(os.path.join(product_root, p)))]) for p in paths]

    # -- survey -----------------------------------------------------------------------------------

    def survey(self):
        """--survey: read the product capture and run read_dispatch's discovery with no selected engine
        required, print what a consultant needs to pick --engine, and return before anything is
        written (stdout only, no document)."""
        self.read_product_capture(self.revision()[0])
        self.read_dispatch()
        skip = {"gclasses", "idea-gclasses", "build", ".gradle"}
        L = []
        L.append("Rating-process survey — %s / %s" % (self.args.product, self.args.line))
        L.append("")
        L.append("registered IRatingPlugin: %s" % self.plugin)
        L.append("")
        L.append("createRatingEngine branches (%d drafted):" % len(self.dispatch))
        L.append("| engineType | RateMethod | Gate condition (verbatim) | Locator |")
        L.append("| --- | --- | --- | --- |")
        for s in self.dispatch:
            rate_method = s.get("rateMethod", "-")
            condition = s.get("when", "-")
            path, line, _ = split_at(s["at"])
            locator = "%s:%s" % (path, line if line else "-")
            L.append("| `%s` | %s | %s | %s |" % (s["engine"], rate_method, condition, locator))
        L.append("")
        L.append("Candidates resolved under gsrc (base class, .gs/.gsx traversal-scope file count, defined roots):")
        for s in self.dispatch:
            engine_type = s["engine"]
            cls = last_segment(engine_type)
            path = self.class_index.get(cls)
            if not path:
                L.append("- `%s`: class `%s` not found under gsrc" % (engine_type, cls))
                continue
            text = read_text(path)
            m = CLASS_RE.search(text)
            base_class = m.group(2) if m else "<VERIFY: class declaration not matched>"
            root_dir = os.path.dirname(path)
            file_count = 0
            for dirpath, dirnames, names in os.walk(root_dir):
                dirnames[:] = [d for d in dirnames if d not in skip]
                file_count += sum(1 for n in names if n.endswith(".gs") or n.endswith(".gsx"))
            blocks, _ = tagged_blocks(path, text)
            defines = [name for name in ROOT_METHODS if any(b["cls"] == cls and b["name"] == name for b in blocks)]
            L.append("- `%s`: base class `%s`; %d .gs/.gsx files under %s; defines %s" % (
                engine_type, base_class, file_count, rel(root_dir, self.source_root),
                ", ".join(defines) if defines else "none of rateSlice/rateWindow/rateOnly"))
        print("\n".join(L))

    # -- cross-document references and the summary ----------------------------------------------

    def resolve_cross_document_references(self):
        """Once the units, lookups and emissions are built, resolve every clause pattern
        code, entity name and typelist name they reference against the product capture.
        This never changes what the document carries and never changes the
        exit status - it is a finding for the consultant, printed as an open question and
        independently caught by the rating-process join validation."""
        refs = []
        for u in self.units:
            for code in u["appliesTo"]:
                if code not in self.pm["clauses"]:
                    refs.append(OrderedDict([
                        ("kind", "clausePattern"), ("code", code), ("unit", u["key"]), ("at", u["at"])]))
        for e in self.emissions:
            entity = e.get("cost", "")
            if entity and not entity.startswith("<") and entity not in self.pm["entities"]:
                refs.append(OrderedDict([
                    ("kind", "entity"), ("code", entity), ("unit", e["unit"]), ("at", e["at"])]))
        # typelist names: nothing this extractor emits today names a typelist directly (a TC_
        # literal is a typecode *within* a typelist, not the typelist's own name) - so there is
        # currently nothing to check here. The loop stays a no-op rather than being left out, so a
        # future typelist-name reference is covered without another pass being written.
        if refs:
            summary = ", ".join(sorted("%s (%s, unit %s, at %s)" % (r["code"], r["kind"], r["unit"], r["at"]) for r in refs))
            self.unresolved.append(
                "cross-document references: %d reference(s) not found in the product capture - %s"
                % (len(refs), summary))
        self.counts["cross-document references unresolved"] = len(refs)

    def print_summary(self, document):
        """The gate's view of the run: counts, the unresolved key dimensions, every open question
        and the readiness check. Everything here is also in the document or derivable from it."""
        doc = self.doc
        L = ["rating-process-extract: %s / %s, engine %s -> %s" % (
            self.args.product, self.args.line, self.selected or "<DECIDE>", document)]
        L.append("  revision %s%s; baseline %s" % (doc["revision"], " (dirty)" if doc["dirty"] else "",
                                                  rel(self.baseline_path, self.source_root) if self.baseline_path else "none"))
        L.append("  counts: " + ", ".join("%s %d" % (k, v) for k, v in self.counts.items()))
        L.append("Ratebook joins: bound %s; missing %s; ambiguous %s" % (
            ", ".join(self.ratebook_joins["bound"]) or "none",
            ", ".join(self.ratebook_joins["missing"]) or "none",
            "; ".join("%s in %s" % (code, ", ".join(folders)) for code, folders in self.ratebook_joins["ambiguous"]) or "none"))
        L.append("Open questions:")
        L.extend("  - <VERIFY> %s" % u for u in self.unresolved)
        L.extend("  - DRAFT %s" % u for u in self.draft_notes)
        if not self.unresolved and not self.draft_notes:
            L.append("  none")
        L.append("Readiness:")
        checks = [
            ("one configuration revision, one product, one line, one selected engine, one set of gate values", bool(self.selected) and (bool(doc.get("assumedGates")) or not any(s.get("when") for s in doc["dispatch"]))),
            ("every unit has a kind, an `at` and an `appliesTo`, and every gosuMethod unit carries its `source`", bool(self.units) and all(u["kind"] != "gosuMethod" or u.get("source") for u in self.units)),
            ("every unit key is unique", len({u["key"] for u in self.units}) == len(self.units)),
            ("every lookup names its dimensions and result columns", all(l["dimensions"] for l in self.lookups)),
            ("every emission names a captured cost entity and resolves every key dimension", all(not e["cost"].startswith("<") for e in self.emissions) and not self.unresolved_key_dims),
            ("every calc routine unit binds exactly one ratebook routine", all("ratebook" in u for u in self.units if u["kind"] == "calcRoutine")),
            ("every bound routine's parameter set matches the unit's binds", not self.ratebook_joins["mismatches"]),
            ("every in-scope value a bound routine reads is typed", not self.ratebook_joins["untyped"]),
            ("every bind's value origin resolves (no `expression` origin)", not self.expression_origins),
            ("no dataflow fact is unresolved", not any(u.get("unresolved") for u in self.units if u["kind"] == "gosuMethod")),
            ("the selected engine is among the recorded `createRatingEngine` branches", bool(self.selected) and any(s.get("selected") for s in doc["dispatch"])),
            ("no required `<VERIFY>` or `<DECIDE>` remains", not self.unresolved),
        ]
        L.extend("  [%s] %s" % ("x" if done else " ", text) for text, done in checks)
        print("\n".join(L))


class _EngineProbe:
    """Process-owned dispatch evidence parser; preserves recorded branches without selecting one."""

    def __init__(self, owner):
        self.config_root = owner.config_root
        self.config = os.path.join(self.config_root, "config")
        self.source_root = owner.source_root

    def engine_evidence(self, line_subtype=None):
        """createRatingEngine function bodies under every directory in gsrc (APD-generated lines
        put the editable override under gw/apd or ext/lob and the generated base under gw/lob;
        other lines' overrides live elsewhere in gsrc, e.g. gw/apd for APDPolicyLineMethods.gs),
        with line numbers. Files that mention the line entity are listed first and are the only
        ones engines are drafted from."""
        hits = []
        gsrc = os.path.join(self.config_root, "gsrc")
        skip_dirnames = {"gclasses", "idea-gclasses", "build", ".gradle"}
        if not os.path.isdir(gsrc):
            return hits
        for dirpath, dirnames, names in os.walk(gsrc):
            dirnames[:] = sorted(d for d in dirnames if d not in skip_dirnames)
            for n in sorted(names):
                if not (n.endswith("PolicyLineMethods.gs") or n.endswith("PolicyLineMethodsBase.gs")):
                    continue
                p = os.path.join(dirpath, n)
                with open(p, encoding="utf-8", errors="replace") as handle:
                    lines = handle.readlines()
                mentions = bool(line_subtype) and any(re.search(r"\b%s\b" % re.escape(line_subtype), t) for t in lines)
                for i, text in enumerate(lines, 1):
                    if "createRatingEngine" in text and "function" in text:
                        block, depth, opened = [], 0, False
                        for j in range(i, len(lines) + 1):
                            t = lines[j - 1]
                            block.append((j, t.rstrip()))
                            depth += t.count("{") - t.count("}")
                            opened = opened or "{" in t
                            if opened and depth <= 0:
                                break
                        hits.append((rel(p, self.source_root), block, mentions))
                        break
        hits.sort(key=lambda h: (not h[2], h[0]))
        return hits

    def draft_engines(self, hits):
        """Parse each createRatingEngine body that mentions the line: every `return new X(` with the
        if-conditions enclosing it, verbatim. A draft for the consultant to confirm, never a verdict."""
        drafts, notes = [], []
        for path, block, mentions in hits:
            if not mentions:
                continue
            stack = []  # (depth_after_open, condition text)
            depth = 0
            pending_if = None
            returns_null = 0
            for number, text in block:
                code = text.split("//")[0]
                opens = code.count("{")
                closes = code.count("}")
                # `} else {` / `} else if (...) {`: the leading brace closes the sibling branch
                # before this line's own condition opens; remember the sibling's condition so the
                # else branch is recorded as its negation, not as a copy of it.
                closed_cond = None
                if closes and code.lstrip().startswith("}"):
                    depth -= 1
                    closes -= 1
                    while stack and stack[-1][0] > depth:
                        closed_cond = stack.pop()[1]
                m = re.search(r"\b(else\s+if|if)\s*\((.*)\)\s*(\{)?\s*(return\s+new\s+([\w.]+)\s*\()?", code)
                if m:
                    cond = m.group(2).strip()
                    if m.group(1).startswith("else") and closed_cond:
                        cond = "not (%s) and %s" % (closed_cond, cond)
                    if m.group(5):  # single-line: if (...) return new X(
                        drafts.append(self.engine_row(m.group(5), cond, path, number))
                        continue
                    pending_if = cond
                elif re.search(r"\belse\s*\{", code) and closed_cond:
                    pending_if = "not (%s)" % closed_cond
                if opens:
                    depth += opens
                    if pending_if is not None:
                        stack.append((depth, pending_if))
                        pending_if = None
                r = re.search(r"return\s+new\s+([\w.]+)\s*\(", code)
                if r:
                    cond = " and ".join(c for _, c in stack)
                    drafts.append(self.engine_row(r.group(1), cond, path, number))
                elif re.search(r"return\s+null\b", code):
                    returns_null += 1
                if closes:
                    depth -= closes
                    while stack and stack[-1][0] > depth:
                        stack.pop()
            drafted = [d for d in drafts if split_at(d["at"])[0] == path]
            for d in drafted:
                note = "dispatch branch `%s` (%s): parsed by the extractor from `return new %s(`" % (d["engine"], d["at"], d["engine"])
                if d.get("when"):
                    note += "; its gate `%s` is the enclosing if-conditions parsed verbatim - confirm the gate and its assumed value" % d["when"]
                notes.append(note + " before the gate closes")
            notes.append("%s: %d engine-yielding return(s) drafted, %d `return null`" % (path, len(drafted), returns_null))
        self.gate_params = self.config_param_values(drafts)
        return drafts, notes

    def config_param_values(self, drafts):
        """Every `PCConfigParameters.<Name>.Value` named by a drafted gate, with the value the
        checkout's config/config.xml sets for it (line-numbered), so the consultant fixes an assumed
        gate knowing what the checkout says. A gate spelled through a local variable
        (`useExternal and !disabled`) is not followed; the summary says so."""
        found = OrderedDict()
        names = []
        for d in drafts:
            cond = d.get("when", "")
            for m in re.finditer(r"PCConfigParameters\.(\w+)\.Value", cond):
                if m.group(1) not in names:
                    names.append(m.group(1))
        if not names:
            return found
        cfg = os.path.join(self.config, "config.xml")
        lines = []
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        for name in names:
            hit = None
            for number, text in enumerate(lines, 1):
                m = re.search(r"<param\s+name=\"%s\"\s+value=\"([^\"]*)\"" % re.escape(name), text)
                if m:
                    hit = (m.group(1), rel(cfg, self.source_root), number)
                    break
            found[name] = hit
        return found

    def engine_row(self, engine, cond, path, number):
        """One dispatch branch: {engine, rateMethod?, when?, at}. It is a draft the consultant
        confirms; draft_engines hands the note to the summary.s open questions."""
        row = OrderedDict()
        row["engine"] = engine
        method = re.search(r"RateMethod\.(TC_\w+)", re.sub(r"not \([^()]*\)", "", cond or ""))  # a negated branch is not that method's branch
        if method:
            row["rateMethod"] = method.group(1)
        if cond:
            row["when"] = cond
        row["at"] = "%s:%d" % (path, number)
        return row


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-root", default=".", help="checkout root; every checkout locator path is relative to it")
    p.add_argument("--product", required=True, help="Product codeIdentifier (must match the product capture)")
    p.add_argument("--line", required=True, help="PolicyLinePattern codeIdentifier (must match the product capture)")
    p.add_argument("--engine", default=None, help="the selected rating engine class (a createRatingEngine branch); a consultant decision, also settable in the decisions file")
    p.add_argument("--revision", default=None, help="override the git revision (required for a non-git export)")
    p.add_argument("--dirty", action="store_true", help="with --revision: the tree differed from that revision")
    p.add_argument("--decisions", default=None, help="YAML file of consultant decisions: selectedEngine, assumedGates, units (reads / produces per key), emissions (key / status per `<unit>.<CostDataClass>`). Every locator is an `at` string. --engine wins over selectedEngine")
    p.add_argument("--product-root", required=True, help="product root: holds extracted/product-capture.json and extracted/ratebooks/; the document goes to <root>/extracted/rating-process-<EngineClass>.json")
    p.add_argument("--force", action="store_true", help="rewrite an existing <product-root>/extracted/rating-process-<EngineClass>.json")
    p.add_argument("--survey", action="store_true", help="print the registered IRatingPlugin and every createRatingEngine branch (engineType, RateMethod, gate condition, locator), plus each candidate's base class, .gs/.gsx traversal-scope file count, and defined roots (rateSlice/rateWindow/rateOnly) where its class resolves under gsrc; writes nothing and exits. --engine is not required with --survey")
    args = p.parse_args(argv)
    ratebooks = os.path.join(args.product_root, "extracted", "ratebooks")
    args.ratebooks = ratebooks if os.path.isdir(ratebooks) else None
    return args


if __name__ == "__main__":
    RatingExtractor(parse_args(sys.argv[1:])).run()
