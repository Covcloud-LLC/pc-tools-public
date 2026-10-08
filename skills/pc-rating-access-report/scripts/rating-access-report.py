#!/usr/bin/env python3
"""Write one line's rating access report from the extraction outputs in its product root.

Reads only `<product-root>/extracted/`: `rating-workspace.json`, every
`rating-process-<Engine>.json`, and the ratebook folders' routine and table JSON. Writes
`extracted/rating-access.json` and `RATING-ACCESS-REPORT-<Line>.md` at the product root, both
deterministic (sorted where order carries no meaning, no timestamp). Python 3.8+, standard
library only, no network.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

FORMAT_VERSION = "pc-tools.rating-access/1.0.0"
WORKSPACE_FILE = "rating-workspace.json"
PROCESS_PATTERN = re.compile(r"^rating-process-(.+)\.json$")
JSON_NAME = "rating-access.json"
ENTITY_BACKED = ("entity", "wrapper")
NOT_ESTABLISHED = "not established"
UNBOUND_ROUTINE = "routine not in an exported book: store targets not established"

MANUAL_SECTIONS = [
    OrderedDict([
        ("heading", "Manual: wrapper setters and reference or copy"),
        ("question", "For each wrapper or plain-class object in the object backing table (kind `wrapper`, "
                     "`gosuClass` or `dto`): does the object hold a reference to the entity it wraps or a copy of "
                     "its values, and which of its setters write through to an entity field?"),
        ("columns", ["object key", "class", "holds reference or copies values", "setter", "writes to "
                     "(entity field or local)", "path:line", "inference?"]),
    ]),
    OrderedDict([
        ("heading", "Manual: tree builder"),
        ("question", "Which code builds the wrapper tree the engine passes to the calc routines (the factory, "
                     "constructor or initializer that walks the entity tree), is it built once per rating run or "
                     "once per slice, and is the tree shared across rating threads?"),
        ("columns", ["engine", "builder (class.method)", "path:line", "built per run or per slice", "shared across "
                     "threads", "inference?"]),
    ]),
    OrderedDict([
        ("heading", "Manual: base-class method bodies"),
        ("question", "For each base-class member the engine's units call (listed below by the class that declares "
                     "it): what does its body read and write, and does it reach an entity?"),
        ("columns", ["declaring class", "member", "declared at", "called from (unit, path:line)", "body reads",
                     "body writes", "reaches an entity?", "inference?"]),
    ]),
    OrderedDict([
        ("heading", "Manual: rate-routine plugin wrappers"),
        ("question", "Which class implements the rate-routine plugin in this checkout, which cost-data wrapper "
                     "classes does it hand to the routines, and which in-scope parameter type codes map to which "
                     "wrapper classes?"),
        ("columns", ["plugin class", "path:line", "cost-data wrapper class", "parameter type code", "wrapper class "
                     "for the type code", "inference?"]),
    ]),
    OrderedDict([
        ("heading", "Manual: configuration values"),
        ("question", "Beyond the assumed gates above, which configuration values govern parallel rating in this "
                     "environment (the parallel-rating switch, the thread pool size, the per-coverable timeout, "
                     "any line-specific gate) and what are their values?"),
        ("columns", ["parameter", "value", "where set (path:line or file#key)", "overridden in code? (path:line)",
                     "inference?"]),
    ]),
    OrderedDict([
        ("heading", "Manual: runtime evidence"),
        ("question", "Are rating worksheets retained in this environment, and for one rated job of this line, "
                     "which object properties did one cost's worksheet read, with their values?"),
        ("columns", ["job number", "worksheets retained?", "cost", "object.property read", "value", "source "
                     "(worksheet file, path or query)", "inference?"]),
    ]),
]


class Refusal(Exception):
    pass


def refuse(message):
    raise Refusal(message)


# ----------------------------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------------------------


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        refuse("cannot read %s: %s" % (path, error))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(product_root, path):
    return Path(path).relative_to(product_root).as_posix()


class Inputs:
    """Everything the report reads, loaded once from the product root."""

    def __init__(self, product_root):
        self.root = product_root
        extracted = product_root / "extracted"
        workspace_path = extracted / WORKSPACE_FILE
        if not workspace_path.is_file():
            refuse("%s has no extracted/%s; run the rating workspace extraction first" % (product_root, WORKSPACE_FILE))
        self.workspace_path = workspace_path
        self.workspace = read_json(workspace_path)
        self.process_paths = sorted(p for p in extracted.iterdir() if p.is_file() and PROCESS_PATTERN.match(p.name))
        if not self.process_paths:
            refuse("%s has no extracted/rating-process-<Engine>.json; run the rating process extraction first"
                   % product_root)
        self.processes = [read_json(p) for p in self.process_paths]
        source = self.workspace.get("source") or {}
        expected = (("product", self.workspace.get("product")), ("line", self.workspace.get("line")),
                    ("revision", source.get("revision")), ("dirty", source.get("dirty")))
        for path, doc in zip(self.process_paths, self.processes):
            for field, value in expected:
                if doc.get(field) != value:
                    refuse("%s has %s %r but %s has %r; every rating-process document must come from the same "
                           "product, line and checkout state as the workspace"
                           % (path, field, doc.get(field), workspace_path, value))
        self.line = self.workspace.get("line") or ""
        if not self.line:
            refuse("%s names no line" % workspace_path)
        self.objects = OrderedDict((o["key"], o) for o in self.workspace.get("objects", []))
        self.scalars = list(self.workspace.get("scalars", []))
        self.scalar_names = set(s.get("parameter") for s in self.scalars)
        self.books = []  # [(folder name, folder path)]
        ratebooks = extracted / "ratebooks"
        if ratebooks.is_dir():
            self.books = sorted((p.name, p) for p in ratebooks.iterdir() if p.is_dir())
        self.routines = []  # [(folder name, routine path, routine doc)]
        for folder, folder_path in self.books:
            routines = folder_path / "routines"
            if routines.is_dir():
                for routine_path in sorted(routines.glob("*.json")):
                    self.routines.append((folder, routine_path, read_json(routine_path)))
        self._tables = {}

    def table(self, folder, code):
        """The table JSON `code` in book folder `folder`, or in any other folder, or None."""
        key = (folder, code)
        if key in self._tables:
            return self._tables[key]
        found = None
        candidates = [f for f in self.books if f[0] == folder] + [f for f in self.books if f[0] != folder]
        for name, folder_path in candidates:
            tables = (folder_path / "tables").resolve()
            path = (folder_path / "tables" / (code + ".json")).resolve()
            if tables not in path.parents:
                refuse("table code %r in %s leaves %s; a routine may only consult a table of an extracted book"
                       % (code, name, folder_path / "tables"))
            if path.is_file():
                found = (name, read_json(path))
                break
        self._tables[key] = found
        return found

    def object_kind(self, key):
        obj = self.objects.get(key)
        return obj["kind"] if obj else None

    def is_unknown(self, param):
        """True for a named parameter that is neither a workspace object nor a scalar."""
        return bool(param) and param not in self.objects and param not in self.scalar_names

    def backing(self, entity):
        """(backing, through) for an entity name: the workspace object whose `entity` is it, then whose
        `subtype.entity` is it, else unknown."""
        if entity:
            for key, obj in self.objects.items():
                if obj.get("entity") == entity:
                    return obj["kind"], key
            for key, obj in self.objects.items():
                if (obj.get("subtype") or {}).get("entity") == entity:
                    return obj["kind"], key
        return "unknown", None


# ----------------------------------------------------------------------------------------------
# Facts
# ----------------------------------------------------------------------------------------------


def engine_inventory(inputs):
    rows = []
    for path, doc in zip(inputs.process_paths, inputs.processes):
        engine = doc.get("engine", {})
        pins = doc.get("pins", {})
        units = doc.get("units", [])
        rows.append(OrderedDict([
            ("document", relative(inputs.root, path)),
            ("sha256", sha256_file(path)),
            ("engine", engine.get("selected")),
            ("base", engine.get("base")),
            ("plugin", engine.get("plugin")),
            ("at", engine.get("at")),
            ("overrides", [o.get("name") for o in engine.get("overrides", [])]),
            ("dispatch", doc.get("dispatch", [])),
            ("assumedGates", doc.get("assumedGates", [])),
            ("sourceRoot", pins.get("sourceRoot")),
            ("configurationRoot", pins.get("configurationRoot")),
            ("revision", doc.get("revision")),
            ("dirty", doc.get("dirty")),
            ("units", OrderedDict([
                ("total", len(units)),
                ("gosuMethod", sum(1 for u in units if u.get("kind") == "gosuMethod")),
                ("calcRoutine", sum(1 for u in units if u.get("kind") == "calcRoutine")),
            ])),
        ]))
    return rows


def object_backing(inputs):
    rows = []
    for key, obj in inputs.objects.items():
        properties = obj.get("properties", [])
        sources = OrderedDict()
        for prop in properties:
            kind = (prop.get("source") or {}).get("kind") or "unresolved"
            sources[kind] = sources.get(kind, 0) + 1
        rows.append(OrderedDict([
            ("key", key),
            ("kind", obj.get("kind")),
            ("className", obj.get("className")),
            ("entity", obj.get("entity")),
            ("subtypeEntity", (obj.get("subtype") or {}).get("entity")),
            ("parent", obj.get("parent")),
            ("properties", len(properties)),
            ("sourceKinds", OrderedDict(sorted(sources.items()))),
            ("parameters", [OrderedDict([("book", p.get("book")), ("parameterSet", p.get("parameterSet")),
                                         ("parameter", p.get("parameter")), ("paramType", p.get("paramType"))])
                            for p in obj.get("parameters", [])]),
        ]))
    scalars = [OrderedDict([("parameter", s.get("parameter")), ("type", s.get("type")), ("book", s.get("book")),
                            ("parameterSet", s.get("parameterSet")), ("routines", s.get("routines", []))])
               for s in inputs.scalars]
    return rows, scalars


def store_targets(inputs):
    rows = []
    by_object = OrderedDict()
    locals_count = 0
    no_target = 0
    flagged = []
    for folder, _path, routine in inputs.routines:
        code = routine.get("routine", {}).get("code")
        for step in routine.get("steps", []):
            if step.get("stepType") != "assignment":
                continue
            param = step.get("inScopeParam") or ""
            value = step.get("inScopeValue") or ""
            local = step.get("storeLocation") or ""
            row = OrderedDict([("book", folder), ("routine", code), ("step", step.get("order"))])
            if param:
                kind = inputs.object_kind(param)
                row["target"] = param + ("." + value if value else "")
                row["object"] = param
                row["property"] = value
                row["kind"] = kind if kind else "not a workspace object"
                row["flagged"] = kind in ENTITY_BACKED
                by_object[param] = by_object.get(param, 0) + 1
                if row["flagged"]:
                    flagged.append(row)
            elif local:
                row["target"] = local
                row["object"] = None
                row["property"] = None
                row["kind"] = "local"
                row["flagged"] = False
                locals_count += 1
            else:
                row["target"] = None
                row["object"] = None
                row["property"] = None
                row["kind"] = "no target"
                row["flagged"] = False
                no_target += 1
            row["storeType"] = step.get("storeType") or ""
            rows.append(row)
    counts = OrderedDict([("rows", len(rows)), ("byObject", OrderedDict(sorted(by_object.items()))),
                          ("locals", locals_count), ("noTarget", no_target), ("flagged", len(flagged))])
    return rows, counts


def engine_writes(inputs):
    engines = []
    for doc in inputs.processes:
        engine = doc.get("engine", {}).get("selected")
        units = []
        totals = OrderedDict([("costData", 0), ("entityWrites", 0), ("beanInserts", 0), ("memberAccess", 0)])
        for unit in doc.get("units", []):
            if unit.get("kind") != "gosuMethod":
                continue
            entity_writes = []
            for write in unit.get("entityWrites", []):
                target = write.get("target")
                backing, through = inputs.backing(target)
                row = OrderedDict([("target", target)])
                if "property" in write:
                    row["shape"] = "property"
                    row["property"] = write.get("property")
                else:
                    row["shape"] = "method"
                    row["method"] = write.get("method")
                    row["declaredIn"] = write.get("declaredIn")
                row["at"] = write.get("at")
                row["backing"] = backing
                row["through"] = through
                entity_writes.append(row)
            bean_inserts = []
            for insert in unit.get("beanInserts", []):
                entity = insert.get("entity")
                backing, through = inputs.backing(entity)
                bean_inserts.append(OrderedDict([("entity", entity), ("at", insert.get("at")),
                                                 ("backing", backing), ("through", through)]))
            members = []
            for member in unit.get("engineMembers", []):
                if member.get("access") == "read":
                    continue
                declared = member.get("declaredIn") or {}
                members.append(OrderedDict([("name", member.get("name")), ("access", member.get("access")),
                                            ("declaredIn", declared.get("class") if isinstance(declared, dict) else declared),
                                            ("declaredAt", declared.get("at") if isinstance(declared, dict) else None),
                                            ("at", member.get("at"))]))
            cost = list(unit.get("writes", []))
            totals["costData"] += len(cost)
            totals["entityWrites"] += len(entity_writes)
            totals["beanInserts"] += len(bean_inserts)
            totals["memberAccess"] += len(members)
            units.append(OrderedDict([("unit", unit.get("key")), ("at", unit.get("at")), ("costData", cost),
                                      ("entityWrites", entity_writes), ("beanInserts", bean_inserts),
                                      ("memberAccess", members)]))
        engines.append(OrderedDict([("engine", engine), ("units", units), ("counts", totals)]))
    return engines


def entity_reads(inputs):
    # (i) unit entityReads, per engine
    unit_rows = []
    per_engine = OrderedDict()
    for doc in inputs.processes:
        engine = doc.get("engine", {}).get("selected")
        n = 0
        for unit in doc.get("units", []):
            for read in unit.get("entityReads", []) or []:
                entity = read.get("entity")
                backing, through = inputs.backing(entity)
                row = OrderedDict([("engine", engine), ("unit", unit.get("key")), ("entity", entity)])
                if "property" in read:
                    row["shape"] = "property"
                    row["property"] = read.get("property")
                else:
                    row["shape"] = "method"
                    row["method"] = read.get("method")
                    row["declaredIn"] = read.get("declaredIn")
                row["at"] = read.get("at")
                row["backing"] = backing
                row["through"] = through
                unit_rows.append(row)
                n += 1
        per_engine[engine] = n

    # (ii) in-scope operands whose parameter is an entity or wrapper object: top level and nested arguments
    operand_rows = []
    # (iii) lookup arguments through the argument source set each table-consulting operand names
    lookup_rows = []
    gaps = []

    def unknown_param(folder, code, order, where, param):
        gaps.append("%s %s step %s: %s reads in-scope parameter %s, which is neither a workspace object nor a "
                    "scalar, so whether the read is entity-backed is not established" % (folder, code, order, where, param))
    for folder, _path, routine in inputs.routines:
        code = routine.get("routine", {}).get("code")
        parameter_set = (routine.get("parameterSet") or {}).get("publicId") or ""
        for step in routine.get("steps", []):
            order = step.get("order")
            step_type = step.get("stepType")
            for operand in step.get("operands", []):
                base = OrderedDict([("book", folder), ("routine", code), ("step", order), ("stepType", step_type)])
                if operand.get("operandType") == "inscope":
                    param = operand.get("inScopeParam") or ""
                    kind = inputs.object_kind(param)
                    if inputs.is_unknown(param):
                        unknown_param(folder, code, order, "operand", param)
                    if kind in ENTITY_BACKED:
                        row = OrderedDict(base)
                        row["where"] = "operand"
                        row["object"] = param
                        row["property"] = operand.get("inScopeValue") or ""
                        row["wholeObject"] = row["property"] == ""
                        row["kind"] = kind
                        operand_rows.append(row)
                arguments = operand.get("arguments", []) or []
                for argument in arguments:
                    if argument.get("operandType") == "inscope":
                        param = argument.get("inScopeParam") or ""
                        kind = inputs.object_kind(param)
                        if inputs.is_unknown(param):
                            unknown_param(folder, code, order, "argument " + (argument.get("parameter") or ""), param)
                        if kind in ENTITY_BACKED:
                            row = OrderedDict(base)
                            row["where"] = "argument " + (argument.get("parameter") or "")
                            row["object"] = param
                            row["property"] = argument.get("inScopeValue") or ""
                            row["wholeObject"] = row["property"] == ""
                            row["kind"] = kind
                            row["overridesSource"] = bool(argument.get("overridesSource"))
                            operand_rows.append(row)
                table_code = operand.get("tableCode") or ""
                if not table_code:
                    continue
                set_code = operand.get("argumentSourceSetCode") or ""
                found = inputs.table(folder, table_code)
                if found is None:
                    gaps.append("%s %s step %s: table %s has no JSON under extracted/ratebooks/*/tables/"
                                % (folder, code, order, table_code))
                    continue
                table_folder, table = found
                overrides = OrderedDict((a.get("parameter"), a) for a in arguments if a.get("overridesSource"))
                # The set the lookup reads: the one with the operand's code bound to the routine's
                # parameter set. Without it only the step's overriding arguments are known.
                sets = [s for s in table.get("argumentSourceSets", []) if s.get("code") == set_code]
                matched = [s for s in sets if s.get("parameterSet") == parameter_set]
                if matched:
                    sources = [s for s in matched[0].get("sources", []) if s.get("key") in overrides or s.get("isBound")]
                    listed = set(s.get("key") for s in matched[0].get("sources", []))
                    sources += [{"key": k} for k in overrides if k not in listed]
                else:
                    sources = [{"key": k} for k in overrides]
                    open_keys = [k.get("name") for k in table.get("keys", []) if k.get("name") not in overrides]
                    if open_keys:
                        bound = sorted(set(s.get("parameterSet") or "" for s in sets))
                        gaps.append("%s %s step %s: table %s has no argument source set %s bound to parameter set %s (%s), "
                                    "so what keys %s read is not established"
                                    % (folder, code, order, table_code, set_code or '""', parameter_set,
                                       "sets with that code are bound to " + ", ".join(bound) if bound else "no set has that code",
                                       ", ".join(open_keys)))
                for source in sources:
                    key = source.get("key")
                    override = overrides.get(key)
                    if override is not None:
                        if override.get("operandType") == "inscope":
                            root = override.get("inScopeParam") or ""
                            value = override.get("inScopeValue") or ""
                            path = root + ("." + value if value else "")
                        else:
                            root = ""
                            path = ""
                        via = "override (%s)" % (override.get("operandType") or "")
                    else:
                        root = source.get("root") or ""
                        path = source.get("path") or ""
                        via = "argument source set " + set_code
                    kind = inputs.object_kind(root)
                    if inputs.is_unknown(root):
                        unknown_param(folder, code, order, "lookup %s key %s" % (table_code, key), root)
                    if kind not in ENTITY_BACKED:
                        continue
                    row = OrderedDict(base)
                    row["table"] = table_code
                    row["tableBook"] = table_folder
                    row["key"] = key
                    row["object"] = root
                    row["path"] = path
                    row["kind"] = kind
                    row["via"] = via
                    lookup_rows.append(row)

    distinct_operands = sorted(set((r["object"], r["property"]) for r in operand_rows))
    distinct_lookups = sorted(set((r["object"], r["path"]) for r in lookup_rows))
    counts = OrderedDict([
        ("units", per_engine),
        ("inScopeOperands", OrderedDict([("rows", len(operand_rows)), ("distinct", len(distinct_operands)),
                                         ("wholeObject", sum(1 for r in operand_rows if r["wholeObject"]))])),
        ("lookupArguments", OrderedDict([("rows", len(lookup_rows)), ("distinct", len(distinct_lookups))])),
    ])
    return OrderedDict([("units", unit_rows), ("inScopeOperands", operand_rows), ("lookupArguments", lookup_rows),
                        ("gaps", gaps), ("counts", counts)])


def bindings(inputs):
    engines = []
    for doc in inputs.processes:
        engine = doc.get("engine", {}).get("selected")
        rows = []
        unbound_routines = []
        by_kind = OrderedDict()
        for unit in doc.get("units", []):
            if unit.get("kind") != "calcRoutine":
                continue
            unit_key = unit.get("key")
            binds = {b.get("parameter"): b for b in unit.get("binds", []) or []}
            ratebook = unit.get("ratebook")
            parameters = OrderedDict()
            if ratebook:
                routine_rel = ratebook.get("routine") or ""
                routine_path = (inputs.root / routine_rel).resolve()
                extracted = (inputs.root / "extracted").resolve()
                if routine_rel and extracted not in routine_path.parents:
                    refuse("unit %s of %s names routine %r outside %s"
                           % (unit_key, engine, routine_rel, inputs.root / "extracted"))
                routine_doc = read_json(routine_path) if routine_rel and routine_path.is_file() else None
                where = "%s edition %s" % (ratebook.get("book"), ratebook.get("edition"))
                if routine_doc is not None:
                    for parameter in routine_doc.get("parameterSet", {}).get("parameters", []):
                        parameters[parameter.get("code")] = parameter
                else:
                    where += " (routine file %s not readable)" % routine_rel
                for parameter in ratebook.get("parameters", []):
                    parameters.setdefault(parameter.get("code"), OrderedDict([("code", parameter.get("code")),
                                                                               ("paramType", parameter.get("paramType"))]))
            else:
                where = UNBOUND_ROUTINE
                unbound_routines.append(unit_key)
            for name in binds:
                parameters.setdefault(name, None)
            for name, parameter in parameters.items():
                bind = binds.get(name)
                kind = bind.get("kind") if bind else None
                if bind and kind is None:
                    kind = "scalar" if "type" not in bind else None
                row = OrderedDict([
                    ("unit", unit_key), ("routine", where), ("parameter", name),
                    ("expression", bind.get("expression") if bind else None),
                    ("type", bind.get("type") if bind else None),
                    ("kind", kind if bind else "no bind"),
                    ("from", bind.get("from") if bind else None),
                    ("verify", bind.get("verify") if bind else None),
                    ("paramType", parameter.get("paramType", NOT_ESTABLISHED) if parameter else NOT_ESTABLISHED),
                    ("useWrapper", parameter.get("useWrapper", NOT_ESTABLISHED) if parameter else NOT_ESTABLISHED),
                    ("wrapperClass", parameter.get("wrapperClass", NOT_ESTABLISHED) if parameter else NOT_ESTABLISHED),
                    ("writable", parameter.get("writable", NOT_ESTABLISHED) if parameter else NOT_ESTABLISHED),
                    ("at", bind.get("at") if bind else None),
                ])
                rows.append(row)
                label = row["kind"] if row["kind"] is not None else "untyped"
                by_kind[label] = by_kind.get(label, 0) + 1
        engines.append(OrderedDict([("engine", engine), ("rows", rows), ("unboundRoutines", unbound_routines),
                                    ("counts", OrderedDict([("rows", len(rows)), ("byKind", OrderedDict(sorted(by_kind.items()))),
                                                            ("unboundRoutines", len(unbound_routines))]))]))
    return engines


def unresolved(inputs):
    engines = []
    for doc in inputs.processes:
        engine = doc.get("engine", {}).get("selected")
        units = []
        facts = 0
        for unit in doc.get("units", []):
            rows = unit.get("unresolved") or []
            if not rows:
                continue
            units.append(OrderedDict([("unit", unit.get("key")), ("at", unit.get("at")),
                                      ("facts", [OrderedDict([("fact", f.get("fact")), ("expression", f.get("expression")),
                                                              ("reason", f.get("reason")), ("at", f.get("at"))]) for f in rows])]))
            facts += len(rows)
        engines.append(OrderedDict([("engine", engine), ("units", units),
                                    ("counts", OrderedDict([("units", len(units)), ("facts", facts)]))]))
    return engines


def member_calls_by_class(writes):
    """The engine member calls of every engine grouped by declaring class, for the base-class section."""
    groups = OrderedDict()
    for engine in writes:
        for unit in engine["units"]:
            for member in unit["memberAccess"]:
                if member["access"] != "call":
                    continue
                cls = member["declaredIn"] or "(unknown class)"
                groups.setdefault(cls, OrderedDict())
                entry = groups[cls].setdefault(member["name"], OrderedDict([("declaredAt", member["declaredAt"]),
                                                                            ("access", member["access"]), ("calledFrom", [])]))
                entry["calledFrom"].append("%s %s (%s)" % (engine["engine"], unit["unit"], member["at"]))
    out = []
    for cls in sorted(groups):
        members = [OrderedDict([("member", name), ("declaredAt", v["declaredAt"]), ("access", v["access"]),
                                ("calledFrom", v["calledFrom"])]) for name, v in sorted(groups[cls].items())]
        out.append(OrderedDict([("class", cls), ("members", members)]))
    return out


def build(inputs):
    objects, scalars = object_backing(inputs)
    targets, target_counts = store_targets(inputs)
    writes = engine_writes(inputs)
    reads = entity_reads(inputs)
    binds = bindings(inputs)
    facts = unresolved(inputs)
    engines = engine_inventory(inputs)
    doc = OrderedDict([
        ("formatVersion", FORMAT_VERSION),
        ("product", inputs.workspace.get("product")),
        ("line", inputs.line),
        ("revision", (inputs.workspace.get("source") or {}).get("revision")),
        ("configurationRoot", (inputs.workspace.get("source") or {}).get("configurationRoot")),
        ("inputs", OrderedDict([
            ("workspace", OrderedDict([("path", relative(inputs.root, inputs.workspace_path)),
                                       ("sha256", sha256_file(inputs.workspace_path))])),
            ("ratingProcesses", [OrderedDict([("path", e["document"]), ("sha256", e["sha256"]), ("engine", e["engine"])])
                                 for e in engines]),
            ("ratebooks", [folder for folder, _ in inputs.books]),
            ("routines", [relative(inputs.root, path) for _, path, _ in inputs.routines]),
        ])),
        ("engines", [OrderedDict((k, v) for k, v in e.items() if k not in ("document", "sha256")) for e in engines]),
        ("objects", objects),
        ("scalars", scalars),
        ("storeTargets", OrderedDict([("rows", targets), ("counts", target_counts)])),
        ("engineWrites", writes),
        ("entityReads", reads),
        ("bindings", binds),
        ("unresolved", facts),
        ("manualSections", [OrderedDict([("heading", s["heading"]), ("question", s["question"]), ("columns", s["columns"])])
                            for s in MANUAL_SECTIONS]),
        ("baseClassMembers", member_calls_by_class(writes)),
    ])
    doc["counts"] = OrderedDict([
        ("engines", len(engines)),
        ("objects", len(objects)),
        ("scalars", len(scalars)),
        ("storeTargets", target_counts),
        ("engineWrites", OrderedDict((e["engine"], e["counts"]) for e in writes)),
        ("entityReads", reads["counts"]),
        ("bindings", OrderedDict((e["engine"], e["counts"]) for e in binds)),
        ("unresolved", OrderedDict((e["engine"], e["counts"]) for e in facts)),
    ])
    return doc


# ----------------------------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------------------------


def cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(cell(v) for v in value)
    text = str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def code(value):
    text = cell(value)
    return "`%s`" % text if text else ""


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(doc):
    out = []

    def w(*lines):
        out.extend(lines)

    line = doc["line"]
    w("# Rating access report: %s" % line)
    w("")
    w("Product `%s`, line `%s`, checkout revision `%s`, configuration root `%s`. Every row below comes "
      "from the extraction outputs under `extracted/` (the rating workspace, one rating-process document per "
      "engine, and the ratebook folders' routine and table JSON); `rating-access.json` beside them holds the "
      "same rows. Locators are `path:line` in the checkout or `file#step` in a ratebook folder. The manual "
      "sections at the end are for a reader of the checkout to fill."
      % (doc["product"], line, doc["revision"], doc["configurationRoot"]))
    w("")
    w("Inputs: `%s`; %s." % (doc["inputs"]["workspace"]["path"],
                             "; ".join("`%s`" % p["path"] for p in doc["inputs"]["ratingProcesses"])))
    w("")

    # Environment and engines
    w("## Environment and engines")
    w("")
    w("One row per rating-process document. `source root` is the checkout path as the extraction recorded it.")
    w("")
    w(*table(["engine", "base", "plugin", "declared at", "source root", "revision", "units (method / routine)"],
             [[code(e["engine"]), code(e["base"]), code(e["plugin"]), code(e["at"]), code(e["sourceRoot"]),
               code(e["revision"]), "%d (%d / %d)" % (e["units"]["total"], e["units"]["gosuMethod"], e["units"]["calcRoutine"])]
              for e in doc["engines"]]))
    w("")
    for e in doc["engines"]:
        w("### %s dispatch and assumed gates" % e["engine"])
        w("")
        w(*table(["branch engine", "when", "selected", "at"],
                 [[code(d.get("engine")), code(d.get("when") or d.get("rateMethod")), cell(bool(d.get("selected"))), code(d.get("at"))]
                  for d in e["dispatch"]]))
        w("")
        if e["assumedGates"]:
            w(*table(["gate", "assumed value", "at"],
                     [[code(g.get("name")), code(g.get("value")), ", ".join(code(a) for a in (g.get("at") or []))]
                      for g in e["assumedGates"]]))
        else:
            w("No assumed gates.")
        w("")
        if e["overrides"]:
            w("Engine overrides: %s." % ", ".join(code(o) for o in e["overrides"]))
            w("")

    # Object backing
    w("## Object backing")
    w("")
    w("The workspace objects the calc routines read and write, with what backs each one. `source kinds` counts "
      "each property's recorded source.")
    w("")
    w(*table(["object", "kind", "class", "entity", "subtype entity", "parent", "properties", "source kinds"],
             [[code(o["key"]), code(o["kind"]), code(o["className"]), code(o["entity"]), code(o["subtypeEntity"]),
               code(o["parent"]), str(o["properties"]),
               ", ".join("%s %d" % (k, n) for k, n in o["sourceKinds"].items())] for o in doc["objects"]]))
    w("")
    if doc["scalars"]:
        w("Scalar parameters (not objects): %s." % ", ".join("%s (`%s`)" % (code(s["parameter"]), cell(s["type"]))
                                                            for s in doc["scalars"]))
        w("")

    # Store targets
    w("## Routine store targets")
    w("")
    counts = doc["storeTargets"]["counts"]
    w("Every assignment step of every routine in the exported books: %d steps; by object %s; %d to locals; %d with "
      "no target; **%d flagged** (target object of kind `entity` or `wrapper`)."
      % (counts["rows"], ", ".join("`%s` %d" % (k, n) for k, n in counts["byObject"].items()) or "none",
         counts["locals"], counts["noTarget"], counts["flagged"]))
    w("")
    w(*table(["book", "routine", "step", "target", "kind", "store type", "flagged"],
             [[code(r["book"]), code(r["routine"]), str(r["step"]), code(r["target"]) if r["target"] else "no target",
               code(r["kind"]), code(r["storeType"]), "**yes**" if r["flagged"] else ""] for r in doc["storeTargets"]["rows"]]))
    w("")

    # Engine writes
    w("## Engine writes")
    w("")
    w("Per engine method unit: cost data fields the unit writes (`cost data (engine)`), entity writes with the "
      "backing of each target (matched by the target's entity to a workspace object's entity, then a wrapper's "
      "subtype entity, else `unknown`), bean inserts, and engine member accesses other than reads (calls and "
      "member writes into the engine or a base class).")
    w("")
    for engine in doc["engineWrites"]:
        c = engine["counts"]
        w("### %s" % engine["engine"])
        w("")
        w("%d cost data write rows, %d entity writes, %d bean inserts, %d member accesses."
          % (c["costData"], c["entityWrites"], c["beanInserts"], c["memberAccess"]))
        w("")
        rows = []
        for unit in engine["units"]:
            for field in unit["costData"]:
                rows.append([code(unit["unit"]), "cost data (engine)", code(field), "", code("costData"), "", code(unit["at"])])
            for write in unit["entityWrites"]:
                what = code(write["property"]) if write["shape"] == "property" else code(write["method"] + "()")
                detail = "" if write["shape"] == "property" else "declared " + code(write["declaredIn"])
                rows.append([code(unit["unit"]), "entity write (%s)" % write["shape"], code(write["target"]), what + (" " + detail if detail else ""),
                             code(write["backing"]), code(write["through"]), code(write["at"])])
            for insert in unit["beanInserts"]:
                rows.append([code(unit["unit"]), "bean insert", code(insert["entity"]), "", code(insert["backing"]),
                             code(insert["through"]), code(insert["at"])])
            for member in unit["memberAccess"]:
                rows.append([code(unit["unit"]), "member " + cell(member["access"]), code(member["declaredIn"]),
                             code(member["name"]) + (" declared " + code(member["declaredAt"]) if member["declaredAt"] else ""),
                             "engine", "", code(member["at"])])
        if rows:
            w(*table(["unit", "write", "target", "field or member", "backing", "through", "at"], rows))
        else:
            w("No write facts.")
        w("")

    # Entity-backed reads
    reads = doc["entityReads"]
    rc = reads["counts"]
    w("## Entity-backed reads")
    w("")
    w("Three lists under one rule: a read counts when its object is backed by an entity or a wrapper over one. "
      "Together they are the candidate field set a copy would have to carry.")
    w("")
    w("### Unit entity reads")
    w("")
    w("Rows per engine: %s." % ", ".join("%s %d" % (code(k), n) for k, n in rc["units"].items()))
    w("")
    w(*table(["engine", "unit", "entity", "read", "backing", "through", "at"],
             [[code(r["engine"]), code(r["unit"]), code(r["entity"]),
               code(r["property"]) if r["shape"] == "property" else code(r["method"] + "()") + " declared " + code(r["declaredIn"]),
               code(r["backing"]), code(r["through"]), code(r["at"])] for r in reads["units"]]))
    w("")
    w("### Routine in-scope operands")
    w("")
    w("Top-level operands and nested arguments whose in-scope parameter is an `entity` or `wrapper` object: %d "
      "rows, %d distinct object.property, %d whole-object passes (an empty property hands the object itself)."
      % (rc["inScopeOperands"]["rows"], rc["inScopeOperands"]["distinct"], rc["inScopeOperands"]["wholeObject"]))
    w("")
    w(*table(["book", "routine", "step", "where", "object", "property", "kind", "overrides source"],
             [[code(r["book"]), code(r["routine"]), "%s (%s)" % (r["step"], r["stepType"]), cell(r["where"]), code(r["object"]),
               code(r["property"]) if r["property"] else "(whole object)", code(r["kind"]),
               cell(r["overridesSource"]) if "overridesSource" in r else ""] for r in reads["inScopeOperands"]]))
    w("")
    w("### Lookup arguments")
    w("")
    w("For each step operand that consults a table: the sources of the argument source set it names, each "
      "replaced by a step argument with `overridesSource` on the same key, kept when the resulting root is an "
      "`entity` or `wrapper` object: %d rows, %d distinct object.path."
      % (rc["lookupArguments"]["rows"], rc["lookupArguments"]["distinct"]))
    w("")
    w(*table(["book", "routine", "step", "table", "key", "object", "path", "kind", "via"],
             [[code(r["book"]), code(r["routine"]), "%s (%s)" % (r["step"], r["stepType"]), code(r["table"]), code(r["key"]),
               code(r["object"]), code(r["path"]), code(r["kind"]), cell(r["via"])] for r in reads["lookupArguments"]]))
    w("")
    if reads["gaps"]:
        w("Not established:")
        w("")
        for gap in reads["gaps"]:
            w("- %s" % cell(gap))
        w("")

    # Bindings
    w("## Routine parameter bindings per engine")
    w("")
    w("One row per routine parameter of each calc-routine unit: what the engine passes (`binds[]` of the "
      "rating-process document) beside the parameter set's declaration from the routine file the unit names.")
    w("")
    for engine in doc["bindings"]:
        c = engine["counts"]
        w("### %s" % engine["engine"])
        w("")
        w("%d rows; bind kinds %s; %d routine(s) in no exported book%s."
          % (c["rows"], ", ".join("`%s` %d" % (k, n) for k, n in c["byKind"].items()) or "none", c["unboundRoutines"],
             (": " + ", ".join(code(u) for u in engine["unboundRoutines"])) if engine["unboundRoutines"] else ""))
        w("")
        w(*table(["unit", "routine", "parameter", "bound expression", "bound type", "bind kind", "from", "paramType",
                  "useWrapper", "wrapperClass", "writable", "at"],
                 [[code(r["unit"]), cell(r["routine"]), code(r["parameter"]), code(r["expression"]), code(r["type"]),
                   code(r["kind"]), cell(r["from"]) + (" (verify: %s)" % cell(r["verify"]) if r["verify"] else ""),
                   code(r["paramType"]) if r["paramType"] != NOT_ESTABLISHED else NOT_ESTABLISHED,
                   cell(r["useWrapper"]), code(r["wrapperClass"]) if r["wrapperClass"] not in (NOT_ESTABLISHED, "") else cell(r["wrapperClass"]),
                   cell(r["writable"]), code(r["at"])] for r in engine["rows"]]))
        w("")

    # Unresolved
    w("## Unresolved facts")
    w("")
    w("Every expression an extraction could not establish, verbatim, per unit. A write hidden in one of these "
      "would not appear above.")
    w("")
    for engine in doc["unresolved"]:
        c = engine["counts"]
        w("### %s" % engine["engine"])
        w("")
        w("%d unit(s) with unresolved facts, %d facts." % (c["units"], c["facts"]))
        w("")
        rows = []
        for unit in engine["units"]:
            for fact in unit["facts"]:
                rows.append([code(unit["unit"]), code(fact["fact"]), code(fact["expression"]), cell(fact["reason"]), code(fact["at"])])
        if rows:
            w(*table(["unit", "fact", "expression", "reason", "at"], rows))
        else:
            w("None.")
        w("")

    # Manual sections
    for section in doc["manualSections"]:
        w("## %s" % section["heading"])
        w("")
        w("Question: %s" % section["question"])
        w("")
        w("Answer as a table with these columns, one row per fact, each with its `path:line` in the checkout; "
          "mark an inferred cell `(inference)`; write `not established` where the checkout does not say.")
        w("")
        w(*table(section["columns"], [["" for _ in section["columns"]]]))
        w("")
        if section["heading"] == "Manual: base-class method bodies":
            w("Member calls to answer for, by declaring class (from the engine writes above):")
            w("")
            if doc["baseClassMembers"]:
                w(*table(["declaring class", "member", "access", "declared at", "called from"],
                         [[code(g["class"]), code(m["member"]), cell(m["access"]), code(m["declaredAt"]),
                           "; ".join(cell(c) for c in m["calledFrom"])] for g in doc["baseClassMembers"] for m in g["members"]]))
            else:
                w("None recorded.")
            w("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------------------------
# Command
# ----------------------------------------------------------------------------------------------


def summary(doc, json_path, report_path):
    c = doc["counts"]
    lines = ["Saved %s" % report_path, "Saved %s" % json_path,
             "  %d engine(s), %d object(s), %d scalar(s)" % (c["engines"], c["objects"], c["scalars"])]
    st = c["storeTargets"]
    lines.append("  store targets: %d step(s); %s; %d local(s); %d with no target; %d flagged"
                 % (st["rows"], ", ".join("%s %d" % (k, n) for k, n in st["byObject"].items()) or "no object targets",
                    st["locals"], st["noTarget"], st["flagged"]))
    for engine, ec in c["engineWrites"].items():
        lines.append("  %s: %d cost data write(s), %d entity write(s), %d bean insert(s), %d member access(es)"
                     % (engine, ec["costData"], ec["entityWrites"], ec["beanInserts"], ec["memberAccess"]))
    er = c["entityReads"]
    lines.append("  entity-backed reads: units %s; in-scope operands %d (%d distinct, %d whole-object); lookup arguments %d (%d distinct)"
                 % (", ".join("%s %d" % (k, n) for k, n in er["units"].items()), er["inScopeOperands"]["rows"],
                    er["inScopeOperands"]["distinct"], er["inScopeOperands"]["wholeObject"],
                    er["lookupArguments"]["rows"], er["lookupArguments"]["distinct"]))
    for engine, bc in c["bindings"].items():
        lines.append("  %s bindings: %d row(s), %s; %d routine(s) in no exported book"
                     % (engine, bc["rows"], ", ".join("%s %d" % (k, n) for k, n in bc["byKind"].items()) or "no binds", bc["unboundRoutines"]))
    for engine, uc in c["unresolved"].items():
        lines.append("  %s unresolved: %d unit(s), %d fact(s)" % (engine, uc["units"], uc["facts"]))
    for gap in doc["entityReads"]["gaps"]:
        lines.append("  not established: %s" % gap)
    lines.append("Manual sections to fill: %d" % len(doc["manualSections"]))
    return "\n".join(lines) + "\n"


def write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Write one line's rating access report and its JSON from the extraction outputs in a product root.")
    parser.add_argument("--product-root", required=True,
                        help="product root holding extracted/rating-workspace.json, extracted/rating-process-<Engine>.json "
                             "and extracted/ratebooks/")
    parser.add_argument("--out", default=None,
                        help="directory for both outputs (default: the report at the product root, the JSON under extracted/)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    product_root = Path(args.product_root)
    try:
        if not product_root.is_dir():
            refuse("%s is not a directory" % product_root)
        inputs = Inputs(product_root)
        if args.out:
            out = Path(args.out)
            json_path = out / JSON_NAME
            report_path = out / ("RATING-ACCESS-REPORT-%s.md" % inputs.line)
        else:
            json_path = product_root / "extracted" / JSON_NAME
            report_path = product_root / ("RATING-ACCESS-REPORT-%s.md" % inputs.line)
        for target in (json_path, report_path):
            if target.exists() and not args.overwrite:
                refuse("%s exists; pass --overwrite to replace it" % target)
        doc = build(inputs)
    except Refusal as error:
        sys.stderr.write(json.dumps({"status": "refused", "error": str(error)}) + "\n")
        return 2
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(json_path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    write_atomic(report_path, render(doc))
    sys.stdout.write(summary(doc, json_path, report_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
