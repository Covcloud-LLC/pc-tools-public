#!/usr/bin/env python3
"""Mechanical extractor for a PC Rate Management (RTM) ratebook export.

Reads one `<import xmlns="http://guidewire.com/pc/exim/import">` ratebook XML and writes an
analysis set for humans and LLMs: JSON for the book, its rate tables and its calc routines, one
plain-text rendering of each routine's steps and one CSV of each table's rows. It prints a summary
(counts, open questions, readiness) for the human gate.

Standard library only. Python 3.8+. No network. Self-contained: the skill runs from its own
directory.

Unlike the product-model and rating-process extractors this one reads a single self-contained
export, not a checkout, so there is no --source-root and no git revision: the locator for every
value is the export file plus the entity's `public-id`, and the file's sha256 pins the edition.

What it carries, every value verbatim from the export:
  * book.json           header, membership, per-table storage attributes, parameter sets, counts
  * tables/<code>.json  match-op (key) columns, factor columns, physical-column map, argument
                        source sets with the object-graph path each key binds to, value providers
  * tables/<code>.csv   the rows, physical columns decoded to logical column names, at the
                        volume --rows asked for; a file cut short by --row-limit is named
                        tables/<code>.sample.csv instead, and table.rows records which
  * routines/<code>.json  parameters, the references the routine makes, and its steps at the
                        detail --steps asked for (full operand tree, one summary line per step, or
                        no steps at all); routine.stepDetail records which
  * routines/<code>.txt   the same steps rendered as readable pseudo-code (read-only; the JSON is
                        the machine format)

What it never does: change a value's representation. A decimal exported as `40.0000000` is written
as `40.0000000`. A rate that reads oddly is the client's rate, not a formatting artifact.

What it leaves to the consultant, printed as the summary's open questions:
  * custom match-op definitions, which are a `public-id` here and a class in the checkout
  * tables with zero rows, which may be genuinely empty or may be held in a different edition
  * tables whose rows this run did not carry in full, or did not carry at all
  * argument sources with no binding path
  * any unrecognized step type, operand type, operator, or rounding scale
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

NS = "{http://guidewire.com/pc/exim/import}"


def die(message):
    sys.stderr.write("ratebook-extract: %s\n" % message)
    sys.exit(2)


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------------------------
# Known enumerations. An export value outside these still round-trips verbatim into the JSON and
# the text; it is additionally listed in the summary so a book from another configuration cannot
# introduce a token this extractor renders wrongly without saying so.
# ----------------------------------------------------------------------------------------------

STEP_TYPES = ("comment", "assignment", "continue", "if", "elseif", "else", "endif")
OPERAND_TYPES = ("constant", "ratetable", "inscope", "localvar", "ratefunc", "rounding",
                 "conditional", "Collection")
ROUNDING_OPERATORS = {"halfup": "half-up", "halfdown": "half-down", "halfeven": "half-even",
                      "up": "up", "down": "down", "ceiling": "ceiling", "floor": "floor"}
BINARY_OPERATORS = OrderedDict((
    ("store", ""),
    ("addition", "+"),
    ("subtraction", "-"),
    ("multiplication", "×"),
    ("division", "/"),
    ("lessthan", "<"),
    ("lessthanorequal", "<="),
    ("greaterthan", ">"),
    ("greaterthanorequal", ">="),
    ("equal", "=="),
    ("notequal", "!="),
    ("and", "and"),
    ("or", "or"),
    ("in", "in"),
    ("notin", "not in"),
))
ROUNDING_SCALES = {"minus1": "tens", "minus2": "hundreds", "0": "whole units"}
# A table with no definition in the export is skipped by the writer, so it has no plan of its own.
EMPTY_ROW_PLAN = OrderedDict((("detail", "none"), ("file", ""), ("written", 0),
                              ("truncated", False), ("limit", None)))

MATCH_OPS = {
    "rtm:ExactMatchOpDef": "exact",
    "rtm:RangeMatchOpDefMaxIncl": "range (max inclusive)",
    "rtm:RangeMatchOpDefMaxExcl": "range (max exclusive)",
    "rtm:RangeMatchOpDefMinIncl": "range (min inclusive)",
    "rtm:RangeMatchOpDefMinExcl": "range (min exclusive)",
    "rtm:LongestSubstringMatchOpDef": "longest substring",
    "rtm:ContainsMatchOpDef": "contains",
}

# The generic row slots a rate table backed by the shared DefaultRateFactorRow entity uses. A
# table backed by a dedicated extension entity names its columns instead (`CovCode`, `Factor`), so
# this list is the common case, never a closed set: rows are read by reading every scalar child.
GENERIC_ROW_SLOTS = (["str%d" % n for n in range(1, 9)] + ["int%d" % n for n in range(1, 9)] +
                     ["dec%d" % n for n in range(1, 7)] + ["date1", "date2", "bit1", "bit2"])

# Localization arrays. Empty in every export seen so far; a non-empty one is an open question, because its
# content is a client label this extractor drops.
L10N_ARRAYS = ("BookDesc_L10N_ARRAY", "BookName_L10N_ARRAY", "ColumnLabel_L10N_ARRAY",
               "Description_L10N_ARRAY", "DisplayText_L10N_ARRAY", "Name_L10N_ARRAY",
               "TableDesc_L10N_ARRAY", "TableName_L10N_ARRAY")

# Every element this extractor reads. Anything in the export outside this set is listed in the
# summary as not carried, so a schema change shows up as a line of prose rather than as silence.
CARRIED = {
    "import", "RateBook", "BookCode", "BookDesc", "BookEdition", "BookGroup", "BookJurisdiction",
    "BookName", "BookOffering", "CascadedLookup", "EffectiveDate", "ExpirationDate", "ExportLock",
    "LastStatusChangeDate", "LastTableRowEdit", "PolicyLine", "RenewalEffectiveDate", "Status",
    "UWCompany", "RateBookCalcRoutines", "RateBookCalcRoutine", "RateTables", "RateTable",
    "BasedOnTable", "Checksum", "Definition", "NormalizedRowCount", "QueryStrategy",
    "ReduceMemoryUsage", "RefTable", "RowUniformityStatus",
    "RateTableDefinition", "TableCode", "TableName", "TableDesc", "EntityName", "Factors",
    "MatchOps", "RateTableMatchOp", "MatchOpDefinition", "DisplayText", "Name", "Params",
    "RateTableColumn", "ColumnLabel", "ColumnName", "ColumnScale", "ColumnType",
    "DefinitionForFactor", "DefinitionForParam", "DependsOn", "DisplayType", "MatchOp",
    "MultiSelect", "PhysicalColumnName", "SortOrder", "ValueProvider",
    "ArgumentSourceSets", "RateTableArgumentSourceSet", "Code", "RateTableArgumentSources",
    "RateTableArgumentSource", "ArgumentSource", "ArgumentSourceSet", "IsModifier", "Parameter",
    "Root",
    "CalcRoutineParameterSet", "IncludesCost", "Parameters", "CalcRoutineParameter",
    "CoveragePattern", "ParamType", "UseWrapper", "WrapperClass", "Writable",
    "PolicyLinePatternCode",
    "CalcRoutineDefinition", "Description", "Jurisdiction", "ParameterSet", "Steps", "Version",
    "CalcStepDefinition", "InScopeParam", "InScopeValue", "Notes", "Operands", "SectionComment",
    "StepType", "StoreLocation", "StoreType",
    "CalcStepDefinitionOperand", "ArgumentSourceSetCode", "ArgumentSources", "CalcStep",
    "ConstantValue", "CovTermCode", "FunctionName", "InScopeValueIsModifier", "InScopeValueType",
    "LeftParenthesisGroup", "LogicalNot", "OperandOrder", "OperandType", "OperatorType",
    "ReturnFactorColumns", "RightParenthesisGroup", "RoundingScaleType", "VariableFieldName",
    "VariableName",
    "CalcStepDefinitionArgument", "Operand", "OverrideSource", "ParameterType",
    "CalcStepDefinitionRateFactor", "Subtype",
}
CARRIED |= set(GENERIC_ROW_SLOTS) | set(L10N_ARRAYS)


def local(tag):
    return tag.replace(NS, "")


def text(element, name, default=""):
    """The stripped text of a child element, or default when the child is absent or empty."""
    if element is None:
        return default
    child = element.find(NS + name)
    if child is None or child.text is None:
        return default
    value = child.text.strip()
    return value if value else default


def ref(element, name):
    """The public-id a reference child points at, or ''."""
    if element is None:
        return ""
    child = element.find(NS + name)
    if child is None:
        return ""
    return child.get("public-id") or ""


def flag(element, name, default=None):
    value = text(element, name)
    if value == "true":
        return True
    if value == "false":
        return False
    return default


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Unknowns:
    """Enumeration values the export used that this extractor does not have a rendering for."""

    def __init__(self):
        self.items = OrderedDict()

    def note(self, kind, value, where):
        if not value:
            return value
        key = (kind, value)
        self.items.setdefault(key, []).append(where)
        return value

    def check(self, kind, value, known, where):
        if value and value not in known:
            self.note(kind, value, where)
        return value

    def rows(self):
        return [{"kind": kind, "value": value, "occurrences": len(where), "firstSeenAt": where[0]}
                for (kind, value), where in self.items.items()]


# ----------------------------------------------------------------------------------------------
# Parse
# ----------------------------------------------------------------------------------------------


class Ratebook:
    """The whole export, resolved. `public-id` is unique per entity type only, never globally, so
    every lookup below is scoped by the entity type it resolves against."""

    def __init__(self, path, root, unknowns):
        self.path = path
        self.root = root
        self.unknowns = unknowns
        self.exporter_version = root.get("version", "")
        self.tag_counts = Counter()
        for element in root.iter():
            self.tag_counts[local(element.tag)] += 1

        books = [e for e in root if local(e.tag) == "RateBook"]
        if len(books) != 1:
            die("expected exactly one RateBook element, found %d in %s" % (len(books), path))
        self.book_element = books[0]
        self.book_id = self.book_element.get("public-id", "")

        self.populated_l10n = [(local(e.tag), e.get("public-id", ""))
                               for e in root.iter() if local(e.tag) in L10N_ARRAYS and len(e)]
        self.definitions = self._parse_definitions()
        self.parameter_sets = self._parse_parameter_sets()
        self.tables = self._parse_tables()
        self.rows = self._parse_rows()
        self.routines = self._parse_routines()
        self._attach_rows()
        self._cross_reference()

    # -- header -------------------------------------------------------------------------------

    def header(self):
        book = self.book_element
        return OrderedDict((
            ("publicId", self.book_id),
            ("code", text(book, "BookCode")),
            ("name", text(book, "BookName")),
            ("description", text(book, "BookDesc")),
            ("edition", as_int(text(book, "BookEdition"))),
            ("status", text(book, "Status")),
            ("policyLine", text(book, "PolicyLine")),
            ("jurisdiction", text(book, "BookJurisdiction")),
            ("underwritingCompany", text(book, "UWCompany")),
            ("offering", text(book, "BookOffering")),
            ("group", text(book, "BookGroup")),
            ("effectiveDate", text(book, "EffectiveDate")),
            ("expirationDate", text(book, "ExpirationDate")),
            ("renewalEffectiveDate", text(book, "RenewalEffectiveDate")),
            ("lastStatusChangeDate", text(book, "LastStatusChangeDate")),
            ("lastTableRowEdit", text(book, "LastTableRowEdit")),
            ("cascadedLookup", flag(book, "CascadedLookup")),
            ("exportLock", flag(book, "ExportLock")),
        ))

    # -- rate table definitions ---------------------------------------------------------------

    def _column(self, element, where):
        column_type = text(element, "ColumnType")
        self.unknowns.check("columnType", column_type,
                            ("Decimal", "String", "Integer", "Boolean", "Date"), where)
        physical = text(element, "PhysicalColumnName")
        return OrderedDict((
            ("publicId", element.get("public-id", "")),
            ("columnName", text(element, "ColumnName")),
            ("label", text(element, "ColumnLabel")),
            ("type", column_type),
            ("scale", as_int(text(element, "ColumnScale"))),
            ("physicalColumn", physical),
            ("sortOrder", as_int(text(element, "SortOrder"), 0)),
            ("displayType", text(element, "DisplayType")),
            ("multiSelect", flag(element, "MultiSelect")),
            ("valueProvider", text(element, "ValueProvider")),
            ("dependsOn", ref(element, "DependsOn") or text(element, "DependsOn")),
        ))

    def _parse_definitions(self):
        definitions = OrderedDict()
        for element in self.root:
            if local(element.tag) != "RateTableDefinition":
                continue
            definition_id = element.get("public-id", "")
            code = text(element, "TableCode")
            where = "RateTableDefinition %s (%s)" % (definition_id, code or "no TableCode")

            keys = []
            match_ops_by_id = {}
            match_ops = element.find(NS + "MatchOps")
            for match_op in (match_ops if match_ops is not None else []):
                op_id = match_op.get("public-id", "")
                definition_ref = ref(match_op, "MatchOpDefinition")
                self.unknowns.check("matchOpDefinition", definition_ref, MATCH_OPS, where)
                params = match_op.find(NS + "Params")
                columns = [self._column(c, where) for c in (params if params is not None else [])]
                entry = OrderedDict((
                    ("publicId", op_id),
                    ("name", text(match_op, "Name")),
                    ("displayText", text(match_op, "DisplayText")),
                    ("matchOp", MATCH_OPS.get(definition_ref, "")),
                    ("matchOpDefinition", definition_ref),
                    ("isCustomMatchOp", bool(definition_ref) and definition_ref not in MATCH_OPS),
                    ("columns", columns),
                ))
                keys.append(entry)
                match_ops_by_id[op_id] = entry
            keys.sort(key=lambda k: min([c["sortOrder"] for c in k["columns"]] or [0]))

            factors_element = element.find(NS + "Factors")
            factors = [self._column(c, where) for c in
                       (factors_element if factors_element is not None else [])]
            factors.sort(key=lambda c: c["sortOrder"])

            source_sets = []
            sets_element = element.find(NS + "ArgumentSourceSets")
            for source_set in (sets_element if sets_element is not None else []):
                sources = []
                container = source_set.find(NS + "RateTableArgumentSources")
                for source in (container if container is not None else []):
                    op_id = ref(source, "Parameter")
                    root_name = text(source, "Root")
                    argument = text(source, "ArgumentSource")
                    path = ".".join(part for part in (root_name, argument) if part)
                    sources.append(OrderedDict((
                        ("publicId", source.get("public-id", "")),
                        ("key", match_ops_by_id.get(op_id, {}).get("name", "")),
                        ("keyPublicId", op_id),
                        ("root", root_name),
                        ("argumentSource", argument),
                        ("path", path),
                        ("isModifier", flag(source, "IsModifier")),
                        ("isBound", bool(path)),
                    )))
                source_sets.append(OrderedDict((
                    ("publicId", source_set.get("public-id", "")),
                    ("code", text(source_set, "Code")),
                    ("name", text(source_set, "Name")),
                    ("parameterSet", ref(source_set, "CalcRoutineParameterSet")),
                    ("sources", sources),
                )))

            definitions[definition_id] = OrderedDict((
                ("publicId", definition_id),
                ("code", code),
                ("name", text(element, "TableName")),
                ("description", text(element, "TableDesc")),
                ("policyLine", text(element, "PolicyLine")),
                ("rowEntity", text(element, "EntityName")),
                ("keys", keys),
                ("factors", factors),
                ("argumentSourceSets", source_sets),
            ))
        return definitions

    # -- parameter sets -----------------------------------------------------------------------

    def _parse_parameter_sets(self):
        sets = OrderedDict()
        for element in self.root:
            if local(element.tag) != "CalcRoutineParameterSet":
                continue
            parameters = []
            container = element.find(NS + "Parameters")
            for parameter in (container if container is not None else []):
                parameters.append(OrderedDict((
                    ("publicId", parameter.get("public-id", "")),
                    ("code", text(parameter, "Code")),
                    ("paramType", text(parameter, "ParamType")),
                    ("coveragePattern", text(parameter, "CoveragePattern")),
                    ("writable", flag(parameter, "Writable")),
                    ("useWrapper", flag(parameter, "UseWrapper")),
                    ("wrapperClass", text(parameter, "WrapperClass")),
                )))
            sets[element.get("public-id", "")] = OrderedDict((
                ("publicId", element.get("public-id", "")),
                ("code", text(element, "Code")),
                ("name", text(element, "Name")),
                ("policyLine", text(element, "PolicyLinePatternCode")),
                ("includesCost", flag(element, "IncludesCost")),
                ("parameters", parameters),
            ))
        return sets

    # -- rate tables (the book's instances of the definitions) ---------------------------------

    def _parse_tables(self):
        tables = OrderedDict()
        container = self.book_element.find(NS + "RateTables")
        for element in (container if container is not None else []):
            table_id = element.get("public-id", "")
            definition_id = ref(element, "Definition")
            definition = self.definitions.get(definition_id)
            if definition is None:
                self.unknowns.note("unresolvedTableDefinition", definition_id or "(empty)",
                                   "RateTable %s" % table_id)
            tables[table_id] = OrderedDict((
                ("publicId", table_id),
                ("code", (definition or {}).get("code", "")),
                ("definitionId", definition_id),
                ("queryStrategy", text(element, "QueryStrategy")),
                ("reduceMemoryUsage", flag(element, "ReduceMemoryUsage")),
                ("rowUniformityStatus", text(element, "RowUniformityStatus")),
                ("normalizedRowCount", text(element, "NormalizedRowCount")),
                ("basedOnTable", ref(element, "BasedOnTable")),
                ("refTable", ref(element, "RefTable")),
                ("checksum", text(element, "Checksum")),
                ("lastTableRowEdit", text(element, "LastTableRowEdit")),
                ("rowCount", 0),
                ("rows", []),
            ))
        return tables

    # -- rows ---------------------------------------------------------------------------------

    def _parse_rows(self):
        """A row is any top-level element carrying a RateTable reference. The tag is the row
        subtype, which is `DefaultRateFactorRow` out of the box and a configured entity in a
        client's book, so it is never matched by name."""
        rows = []
        for element in self.root:
            name = local(element.tag)
            if name in ("RateBook", "RateTableDefinition", "CalcRoutineDefinition",
                        "CalcRoutineParameterSet", "CalcRoutineParameter"):
                continue
            table_id = ref(element, "RateTable")
            if not table_id:
                continue
            values = {}
            for child in element:
                name = local(child.tag)
                if name == "Subtype" or child.get("public-id") or len(child):
                    continue
                value = (child.text or "").strip()
                if value:
                    values[name] = value
            rows.append({
                "publicId": element.get("public-id", ""),
                "tableId": table_id,
                "entity": text(element, "Subtype") or name,
                "values": values,
            })
        return rows

    def _attach_rows(self):
        for row in self.rows:
            table = self.tables.get(row["tableId"])
            if table is None:
                self.unknowns.note("orphanRow", row["tableId"],
                                   "row %s references a RateTable not in this book" % row["publicId"])
                continue
            table["rows"].append(row)
        for table in self.tables.values():
            table["rowCount"] = len(table["rows"])

    # -- calc routines -------------------------------------------------------------------------

    def _operand(self, element, where):
        operand_type = text(element, "OperandType")
        self.unknowns.check("operandType", operand_type, OPERAND_TYPES, where)
        operator = text(element, "OperatorType")
        if operator:
            self.unknowns.check(
                "operatorType", operator,
                tuple(BINARY_OPERATORS) + tuple(ROUNDING_OPERATORS), where)
        scale = text(element, "RoundingScaleType")
        if scale and scale not in ROUNDING_SCALES and as_int(scale) is None:
            self.unknowns.note("roundingScaleType", scale, where)

        arguments = []
        container = element.find(NS + "ArgumentSources")
        for argument in (container if container is not None else []):
            arguments.append(OrderedDict((
                ("publicId", argument.get("public-id", "")),
                ("parameter", text(argument, "Parameter")),
                ("parameterType", text(argument, "ParameterType")),
                ("overridesSource", flag(argument, "OverrideSource", False)),
                ("operandType", text(argument, "OperandType")),
                ("constantValue", text(argument, "ConstantValue")),
                ("variableName", text(argument, "VariableName")),
                ("variableFieldName", text(argument, "VariableFieldName")),
                ("inScopeParam", text(argument, "InScopeParam")),
                ("inScopeValue", text(argument, "InScopeValue")),
                ("inScopeValueIsModifier", flag(argument, "InScopeValueIsModifier", False)),
                ("covTermCode", text(argument, "CovTermCode")),
            )))

        factor_columns = []
        returned = element.find(NS + "ReturnFactorColumns")
        for factor in (returned if returned is not None else []):
            factor_columns.append(text(factor, "ColumnName"))

        left = text(element, "LeftParenthesisGroup")
        right = text(element, "RightParenthesisGroup")
        if left or right:
            self.unknowns.note("parenthesisGroup", "%s/%s" % (left or "-", right or "-"), where)

        return OrderedDict((
            ("publicId", element.get("public-id", "")),
            ("order", as_int(text(element, "OperandOrder"), 0)),
            ("operandType", operand_type),
            ("operator", operator),
            ("constantValue", text(element, "ConstantValue")),
            ("tableCode", text(element, "TableCode")),
            ("argumentSourceSetCode", text(element, "ArgumentSourceSetCode")),
            ("returnFactorColumns", factor_columns),
            ("functionName", text(element, "FunctionName")),
            ("variableName", text(element, "VariableName")),
            ("variableFieldName", text(element, "VariableFieldName")),
            ("inScopeParam", text(element, "InScopeParam")),
            ("inScopeValue", text(element, "InScopeValue")),
            ("inScopeValueIsModifier", flag(element, "InScopeValueIsModifier", False)),
            ("inScopeValueType", text(element, "InScopeValueType")),
            ("covTermCode", text(element, "CovTermCode")),
            ("roundingScaleType", scale),
            ("logicalNot", flag(element, "LogicalNot", False)),
            ("leftParenthesisGroup", left),
            ("rightParenthesisGroup", right),
            ("arguments", arguments),
        ))

    def _parse_routines(self):
        routines = OrderedDict()
        attached = set()
        container = self.book_element.find(NS + "RateBookCalcRoutines")
        for element in (container if container is not None else []):
            attached.add(ref(element, "CalcRoutineDefinition"))

        for element in self.root:
            if local(element.tag) != "CalcRoutineDefinition":
                continue
            routine_id = element.get("public-id", "")
            code = text(element, "Code")
            where = "CalcRoutineDefinition %s (%s)" % (routine_id, code or "no Code")
            steps = []
            steps_element = element.find(NS + "Steps")
            for step in (steps_element if steps_element is not None else []):
                step_type = text(step, "StepType")
                self.unknowns.check("stepType", step_type, STEP_TYPES, where)
                operands_element = step.find(NS + "Operands")
                operands = [self._operand(o, where)
                            for o in (operands_element if operands_element is not None else [])]
                operands.sort(key=lambda o: o["order"])
                steps.append(OrderedDict((
                    ("publicId", step.get("public-id", "")),
                    ("order", as_int(text(step, "SortOrder"), 0)),
                    ("stepType", step_type),
                    ("sectionComment", text(step, "SectionComment")),
                    ("notes", text(step, "Notes")),
                    ("inScopeParam", text(step, "InScopeParam")),
                    ("inScopeValue", text(step, "InScopeValue")),
                    ("storeLocation", text(step, "StoreLocation")),
                    ("storeType", text(step, "StoreType")),
                    ("operands", operands),
                )))
            steps.sort(key=lambda s: s["order"])
            routines[routine_id] = OrderedDict((
                ("publicId", routine_id),
                ("code", code),
                ("name", text(element, "Name")),
                ("description", text(element, "Description")),
                ("version", text(element, "Version")),
                ("policyLine", text(element, "PolicyLinePatternCode")),
                ("jurisdiction", text(element, "Jurisdiction")),
                ("parameterSetId", ref(element, "ParameterSet")),
                ("attachedToBook", routine_id in attached),
                ("steps", steps),
            ))
        for routine_id in sorted(attached):
            if routine_id and routine_id not in routines:
                self.unknowns.note("unresolvedRoutine", routine_id,
                                   "RateBookCalcRoutine references a definition not in this export")
        return routines

    # -- joins ---------------------------------------------------------------------------------

    def definition_by_code(self, code):
        for definition in self.definitions.values():
            if definition["code"] == code:
                return definition
        return None

    def table_by_code(self, code):
        for table in self.tables.values():
            if table["code"] == code:
                return table
        return None

    def _cross_reference(self):
        """Everything each routine reaches, and the reverse index from tables back to routines."""
        self.table_users = {}
        for routine in self.routines.values():
            tables, functions, modifiers, cov_terms = OrderedDict(), OrderedDict(), OrderedDict(), OrderedDict()
            in_scope, writes, reads = OrderedDict(), OrderedDict(), OrderedDict()
            for step in routine["steps"]:
                if step["storeLocation"]:
                    writes.setdefault(step["storeLocation"], 0)
                    writes[step["storeLocation"]] += 1
                if step["inScopeParam"] and step["inScopeValue"]:
                    key = "%s.%s" % (step["inScopeParam"], step["inScopeValue"])
                    in_scope.setdefault(key, 0)
                    in_scope[key] += 1
                for operand in step["operands"]:
                    if operand["tableCode"]:
                        entry = tables.setdefault(operand["tableCode"], OrderedDict((
                            ("code", operand["tableCode"]), ("uses", 0), ("columns", []),
                            ("argumentSourceSets", []), ("inBook", False))))
                        entry["uses"] += 1
                        for column in operand["returnFactorColumns"]:
                            if column not in entry["columns"]:
                                entry["columns"].append(column)
                        set_code = operand["argumentSourceSetCode"]
                        if set_code and set_code not in entry["argumentSourceSets"]:
                            entry["argumentSourceSets"].append(set_code)
                    if operand["functionName"]:
                        functions.setdefault(operand["functionName"], 0)
                        functions[operand["functionName"]] += 1
                    if operand["covTermCode"]:
                        cov_terms.setdefault(operand["covTermCode"], 0)
                        cov_terms[operand["covTermCode"]] += 1
                    if operand["variableName"]:
                        reads.setdefault(operand["variableName"], 0)
                        reads[operand["variableName"]] += 1
                    if operand["inScopeParam"] and operand["inScopeValue"]:
                        key = "%s.%s" % (operand["inScopeParam"], operand["inScopeValue"])
                        in_scope.setdefault(key, 0)
                        in_scope[key] += 1
                        if operand["inScopeValueIsModifier"]:
                            modifiers.setdefault(key, 0)
                            modifiers[key] += 1
                    for argument in operand["arguments"]:
                        if argument["variableName"]:
                            reads.setdefault(argument["variableName"], 0)
                            reads[argument["variableName"]] += 1
                        if argument["covTermCode"]:
                            cov_terms.setdefault(argument["covTermCode"], 0)
                            cov_terms[argument["covTermCode"]] += 1
            for code, entry in tables.items():
                entry["inBook"] = self.table_by_code(code) is not None
                self.table_users.setdefault(code, []).append(routine["code"])
            routine["references"] = OrderedDict((
                ("tables", list(tables.values())),
                ("rateFunctions", [{"signature": name, "uses": count}
                                   for name, count in functions.items()]),
                ("modifiers", [{"path": name, "uses": count} for name, count in modifiers.items()]),
                ("covTerms", [{"code": name, "uses": count} for name, count in cov_terms.items()]),
                ("inScopeValues", [{"path": name, "uses": count} for name, count in in_scope.items()]),
                ("localVariablesWritten", [{"name": name, "writes": count}
                                           for name, count in writes.items()]),
                ("localVariablesRead", [{"name": name, "reads": count}
                                        for name, count in reads.items()]),
                ("localVariablesWrittenNeverRead", sorted(set(writes) - set(reads))),
                ("localVariablesReadNeverWritten", sorted(set(reads) - set(writes))),
            ))


# ----------------------------------------------------------------------------------------------
# Text rendering. Read-only: the JSON is the machine format, this is for a human or an LLM reading
# a routine end to end. Evaluation in a calc routine is strictly left to right with no operator
# precedence, so the rendering is a flat operand chain and never inserts parentheses of its own.
# The parentheses an operand carries (LeftParenthesisGroup, RightParenthesisGroup) are the routine's
# own grouping and are written as exported, after the operator and around the operand, the way the
# PC rate routine editor lays them out.
# ----------------------------------------------------------------------------------------------


def quote_constant(value):
    return '"%s"' % value if value else "<empty constant>"


def rounding_scale_label(scale):
    if not scale:
        return ""
    if scale in ROUNDING_SCALES:
        return ROUNDING_SCALES[scale]
    digits = as_int(scale)
    if digits is not None:
        return "%d dp" % digits
    return scale


def render_argument(argument, named=True):
    """`NAME` when the value comes from the table's argument source set; `NAME <- expr` when the
    step overrides it, so an override is never invisible."""
    name = argument["parameter"] or "?"
    if not argument["overridesSource"] and not argument["operandType"]:
        return name
    kind = argument["operandType"]
    if kind == "localvar":
        source = argument["variableName"] or "?"
        if argument["variableFieldName"]:
            source += "." + argument["variableFieldName"]
    elif kind == "constant":
        source = quote_constant(argument["constantValue"])
    elif kind == "inscope":
        source = "%s.%s" % (argument["inScopeParam"] or "?", argument["inScopeValue"] or "?")
        if argument["inScopeValueIsModifier"]:
            source += " [modifier]"
    else:
        source = argument["variableName"] or argument["constantValue"] or kind or "?"
    return source if not named else "%s <- %s" % (name, source)


def render_operand_body(operand):
    kind = operand["operandType"]
    if kind == "ratetable":
        columns = ", ".join(operand["returnFactorColumns"]) or "?"
        arguments = ", ".join(render_argument(a) for a in operand["arguments"])
        body = "table %s[%s](%s)" % (operand["tableCode"] or "?", columns, arguments)
    elif kind == "ratefunc":
        signature = operand["functionName"] or "?"
        name = signature.split("(", 1)[0]
        arguments = ", ".join(render_argument(a, named=False) for a in operand["arguments"])
        body = "%s(%s)" % (name, arguments)
    elif kind == "inscope":
        body = "%s.%s" % (operand["inScopeParam"] or "?", operand["inScopeValue"] or "?")
        if operand["inScopeValueIsModifier"]:
            body += " [modifier]"
    elif kind == "localvar":
        body = operand["variableName"] or "?"
        if operand["variableFieldName"]:
            body += "." + operand["variableFieldName"]
    elif kind == "constant":
        body = quote_constant(operand["constantValue"])
    elif kind == "rounding":
        mode = ROUNDING_OPERATORS.get(operand["operator"], operand["operator"] or "?")
        scale = rounding_scale_label(operand["roundingScaleType"])
        body = "round %s to %s" % (mode, scale) if scale else "round %s" % mode
    elif kind == "conditional":
        return ""
    elif not kind:
        return ""
    else:
        body = "<%s>" % kind
    if operand["logicalNot"]:
        body = "not(%s)" % body
    return body


def render_operand(operand, first):
    """The operand plus the operator that joins it to what came before it."""
    body = "%s%s%s" % (operand["leftParenthesisGroup"], render_operand_body(operand),
                       operand["rightParenthesisGroup"])
    if operand["operandType"] == "rounding":
        return body
    operator = operand["operator"]
    if first or not operator or operator == "store":
        return body
    symbol = BINARY_OPERATORS.get(operator, operator)
    if not body:
        return symbol
    return "%s %s" % (symbol, body)


def step_expression(step):
    """The step as one line of expression text, with no step number and no indent. Returns
    (keyword, expression): `keyword` is the control word a control step renders as (`if`, `else`,
    `endif`) or "" for everything else."""
    kind = step["stepType"]
    if kind == "comment":
        return ("#", step["sectionComment"] or "")
    if kind in ("if", "elseif"):
        body = " ".join(part for part in
                        (render_operand(o, i == 0) for i, o in enumerate(step["operands"])) if part)
        return (kind, body.strip())
    if kind in ("else", "endif"):
        return (kind, "")
    if kind == "continue":
        body = " ".join(part for part in
                        (render_operand(o, False) for o in step["operands"]) if part)
        return ("", body.strip() or "<empty step>")
    target = step_target(step)
    body = " ".join(part for part in
                    (render_operand(o, i == 0) for i, o in enumerate(step["operands"])) if part)
    body = body.strip()
    if target:
        return ("", "%s := %s" % (target, body or "<empty>"))
    if body in ("", "<empty constant>"):
        return ("", "<blank step — the export carries no target and no operand value>")
    return ("", body)


def step_target(step):
    if step["inScopeParam"] and step["inScopeValue"]:
        return "%s.%s" % (step["inScopeParam"], step["inScopeValue"])
    if step["storeLocation"]:
        return step["storeLocation"]
    return ""


def render_routine_text(book, routine):
    parameter_set = book.parameter_sets.get(routine["parameterSetId"], {})
    header = book.header()
    lines = []
    lines.append("%s  (%s)" % (routine["name"] or routine["code"], routine["code"]))
    lines.append("=" * max(8, len(lines[0])))
    lines.append("")
    lines.append("Rate book      %s edition %s (%s)" % (
        header["code"], header["edition"], header["status"] or "no status"))
    lines.append("Policy line    %s" % (routine["policyLine"] or "-"))
    lines.append("Version        %s" % (routine["version"] or "-"))
    if routine["jurisdiction"]:
        lines.append("Jurisdiction   %s" % routine["jurisdiction"])
    if routine["description"]:
        lines.append("Description    %s" % routine["description"])
    lines.append("Attached to the book: %s" % ("yes" if routine["attachedToBook"] else "no"))
    lines.append("")
    lines.append("Parameter set  %s (%s)%s" % (
        parameter_set.get("code", "?"), parameter_set.get("name", "?"),
        ", includes cost data" if parameter_set.get("includesCost") else ""))
    for parameter in parameter_set.get("parameters", []):
        lines.append("    %-24s %s%s" % (
            parameter["code"], parameter["paramType"],
            "  (writable)" if parameter["writable"] else ""))
    lines.append("")
    lines.append("How to read this")
    lines.append("    :=                      assigns the expression to the target on its left")
    lines.append("    table CODE[F](K1, K2)   looks up factor column F in rate table CODE, keyed")
    lines.append("                            on K1 and K2; the value of each key comes from the")
    lines.append("                            table's argument source set unless shown as K <- x")
    lines.append("    [modifier]              the value is a policy modifier, not a plain field")
    lines.append("    continuation lines      extend the assignment above them; PC")
    lines.append("                            evaluates strictly left to right, with no operator")
    lines.append("                            precedence and no implicit grouping")
    lines.append("")
    lines.append("Steps")
    lines.append("-" * 5)

    indent = 0
    for step in routine["steps"]:
        kind = step["stepType"]
        if kind in ("elseif", "else", "endif"):
            indent = max(0, indent - 1)
        pad = "    " * indent
        number = "%4s  " % step["order"]
        note = "        # %s" % step["notes"] if step["notes"] else ""

        keyword, body = step_expression(step)
        if kind == "comment":
            lines.append("")
            lines.append("%s%s# %s" % (number, pad, body))
        elif kind == "continue":
            lines.append("%s%s    %s%s" % (number, pad, body, note))
        elif keyword:
            lines.append("%s%s%s%s" % (number, pad, (keyword + " " + body).strip(), note))
        else:
            if step["storeType"]:
                note = note or "        # %s" % step["storeType"]
            lines.append("%s%s%s%s" % (number, pad, body, note))

        if kind in ("if", "elseif", "else"):
            indent += 1

    lookups = routine["references"]["tables"]
    if lookups:
        lines.append("")
        lines.append("Lookups used")
        lines.append("-" * 12)
        for entry in lookups:
            definition = book.definition_by_code(entry["code"])
            status = "" if entry["inBook"] else "   *** not in this book ***"
            lines.append("")
            lines.append("  %s -> %s%s" % (entry["code"], ", ".join(entry["columns"]) or "?", status))
            if definition is None:
                lines.append("      no RateTableDefinition in this export")
                continue
            table = book.table_by_code(entry["code"])
            if table is not None:
                lines.append("      %d row(s), query strategy %s, %s" % (
                    table["rowCount"], table["queryStrategy"] or "?",
                    table["rowUniformityStatus"] or "?"))
            for key in definition["keys"]:
                columns = ", ".join(c["columnName"] for c in key["columns"])
                lines.append("      key  %-24s %-22s (%s)" % (
                    key["name"], key["matchOp"] or key["matchOpDefinition"] or "?", columns))
            for source_set in definition["argumentSourceSets"]:
                if entry["argumentSourceSets"] and source_set["code"] not in entry["argumentSourceSets"]:
                    continue
                lines.append("      argument source set %s" % source_set["code"])
                for source in source_set["sources"]:
                    lines.append("          %-24s %s" % (
                        source["key"] or "?", source["path"] or "<not bound>"))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------
# CSV: physical columns decoded to logical ones. Values are copied byte for byte from the export.
# ----------------------------------------------------------------------------------------------


def table_csv_columns(definition):
    """Key columns first, in match-op then sort order, then factor columns."""
    columns = []
    for key in definition["keys"]:
        for column in sorted(key["columns"], key=lambda c: c["sortOrder"]):
            columns.append((column, key["name"]))
    for column in definition["factors"]:
        columns.append((column, ""))
    return columns


def sort_value(value, column_type):
    """A blank key column is a catch-all row, so it sorts after every populated one. A numeric
    column sorts numerically, so 4501 does not follow 10001 the way string order would."""
    if value == "":
        return (1, 0.0, "")
    if column_type in ("Decimal", "Integer"):
        try:
            return (0, float(value), "")
        except ValueError:
            pass
    return (0, 0.0, value)


def csv_header(definition):
    """The CSV's column names. Read from the column definitions, never from the rows, so it is the
    same list whether or not this run writes any row."""
    return [column["columnName"] or column["physicalColumn"]
            for column, _ in table_csv_columns(definition)] + ["_publicId"]


def write_table_csv(path, definition, table, limit=None):
    """Write the table's rows, at most `limit` of them (None writes every row). The cut is taken
    after the sort, so a limited file is the deterministic head of the full one and two runs of the
    same export are byte-identical. Returns the header and how many rows were written."""
    columns = table_csv_columns(definition)
    header = csv_header(definition)
    records = []
    for row in table["rows"]:
        records.append([row["values"].get(column["physicalColumn"], "") for column, _ in columns]
                       + [row["publicId"]])
    types = [column["type"] for column, _ in columns] + ["String"]
    records.sort(key=lambda record: [sort_value(value, types[index])
                                     for index, value in enumerate(record)])
    if limit is not None:
        records = records[:limit]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(records)
    return header, len(records)


def plan_rows(table, name, mode, limit):
    """What this run carries of one table's rows: the detail mode, the file it goes to, how many
    rows that file gets, and whether the file is short of the table. Decided before anything is
    written so the JSON and the summary read the same decision.

    `.sample.csv` is used only when the file is actually short of the table — a table under the
    limit is complete, so it keeps the plain `.csv` name. The suffix means exactly one thing: this
    file is not the whole table."""
    count = table["rowCount"]
    if mode == "none":
        return OrderedDict((("detail", "none"), ("file", ""), ("written", 0),
                            ("truncated", False), ("limit", None)))
    cap = limit if mode == "sample" else None
    written = count if cap is None else min(count, cap)
    truncated = written < count
    leaf = "%s.sample.csv" % name if truncated else "%s.csv" % name
    return OrderedDict((("detail", mode), ("file", leaf), ("written", written),
                        ("truncated", truncated), ("limit", cap)))


def unmapped_row_columns(definition, table):
    """Physical columns a row populated that no column definition claims. A non-empty result means
    the CSV would silently drop client data, so it is an open question in the summary."""
    mapped = {column["physicalColumn"] for column, _ in table_csv_columns(definition)}
    used = set()
    for row in table["rows"]:
        used |= set(row["values"])
    return sorted(used - mapped)


# ----------------------------------------------------------------------------------------------
# JSON documents
# ----------------------------------------------------------------------------------------------


def slug(code, fallback):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", code or "").strip("-")
    return cleaned or fallback


def book_document(book, source, row_plans):
    header = book.header()
    routines = []
    for routine in book.routines.values():
        parameter_set = book.parameter_sets.get(routine["parameterSetId"], {})
        routines.append(OrderedDict((
            ("code", routine["code"]),
            ("name", routine["name"]),
            ("publicId", routine["publicId"]),
            ("parameterSet", parameter_set.get("code", "")),
            ("attachedToBook", routine["attachedToBook"]),
            ("stepCount", len(routine["steps"])),
            ("file", "routines/%s.json" % slug(routine["code"], routine["publicId"])),
        )))
    routines.sort(key=lambda r: r["code"])

    tables = []
    for table in book.tables.values():
        definition = book.definitions.get(table["definitionId"], {})
        name = slug(table["code"], table["publicId"])
        plan = row_plans.get(table["publicId"], EMPTY_ROW_PLAN)
        tables.append(OrderedDict((
            ("code", table["code"]),
            ("name", definition.get("name", "")),
            ("publicId", table["publicId"]),
            ("definitionId", table["definitionId"]),
            ("rowCount", table["rowCount"]),
            ("keyColumnCount", sum(len(k["columns"]) for k in definition.get("keys", []))),
            ("factorColumnCount", len(definition.get("factors", []))),
            ("queryStrategy", table["queryStrategy"]),
            ("reduceMemoryUsage", table["reduceMemoryUsage"]),
            ("rowUniformityStatus", table["rowUniformityStatus"]),
            ("basedOnTable", table["basedOnTable"]),
            ("refTable", table["refTable"]),
            ("usedByRoutines", sorted(book.table_users.get(table["code"], []))),
            ("file", "tables/%s.json" % name),
            ("csv", ("tables/%s" % plan["file"]) if plan["file"] else ""),
            ("rowsWritten", plan["written"]),
        )))
    tables.sort(key=lambda t: t["code"])

    parameter_sets = sorted(book.parameter_sets.values(), key=lambda s: s["code"])
    unattached = [d for d in book.definitions.values() if book.table_by_code(d["code"]) is None]

    return OrderedDict((
        ("source", source),
        ("book", header),
        ("counts", OrderedDict((
            ("routines", len(book.routines)),
            ("routinesAttachedToBook", sum(1 for r in book.routines.values() if r["attachedToBook"])),
            ("tables", len(book.tables)),
            ("tableDefinitions", len(book.definitions)),
            ("tablesWithRows", sum(1 for t in book.tables.values() if t["rowCount"])),
            ("rows", sum(t["rowCount"] for t in book.tables.values())),
            ("rowsWritten", sum(p["written"] for p in row_plans.values())),
            ("tablesTruncated", sum(1 for p in row_plans.values() if p["truncated"])),
            ("parameterSets", len(book.parameter_sets)),
            ("calcSteps", sum(len(r["steps"]) for r in book.routines.values())),
        ))),
        ("routines", routines),
        ("tables", tables),
        ("tableDefinitionsNotInBook", sorted(d["code"] for d in unattached)),
        ("parameterSets", parameter_sets),
    ))


def table_document(book, table, definition, header, row_plan):
    keys = []
    for key in definition["keys"]:
        entry = OrderedDict(key)
        entry["columns"] = [OrderedDict(c) for c in sorted(key["columns"],
                                                           key=lambda c: c["sortOrder"])]
        keys.append(entry)
    return OrderedDict((
        ("table", OrderedDict((
            ("code", table["code"]),
            ("name", definition["name"]),
            ("description", definition["description"]),
            ("publicId", table["publicId"]),
            ("definitionId", definition["publicId"]),
            ("policyLine", definition["policyLine"]),
            ("rowEntity", definition["rowEntity"]),
            ("book", book.header()["code"]),
        ))),
        ("storage", OrderedDict((
            ("queryStrategy", table["queryStrategy"]),
            ("reduceMemoryUsage", table["reduceMemoryUsage"]),
            ("rowUniformityStatus", table["rowUniformityStatus"]),
            ("normalizedRowCount", table["normalizedRowCount"]),
            ("basedOnTable", table["basedOnTable"]),
            ("refTable", table["refTable"]),
            ("checksum", table["checksum"]),
            ("lastTableRowEdit", table["lastTableRowEdit"]),
            ("rowCount", table["rowCount"]),
        ))),
        ("keys", keys),
        ("factors", [OrderedDict(c) for c in definition["factors"]]),
        ("argumentSourceSets", definition["argumentSourceSets"]),
        ("rows", OrderedDict((
            ("detail", row_plan["detail"]),
            ("file", row_plan["file"]),
            ("columns", header),
            ("count", table["rowCount"]),
            ("written", row_plan["written"]),
            ("truncated", row_plan["truncated"]),
        ))),
        ("usedByRoutines", sorted(book.table_users.get(table["code"], []))),
    ))


def summarize_step(step):
    """One step as a handful of fields instead of its whole operand tree: what it does, in the same
    expression text the .txt carries, plus the tables it consults. Enough to read the algorithm and
    to grep it; not enough to reconstruct an operand."""
    keyword, body = step_expression(step)
    tables = []
    for operand in step["operands"]:
        if operand["tableCode"] and operand["tableCode"] not in tables:
            tables.append(operand["tableCode"])
    summary = OrderedDict((
        ("order", step["order"]),
        ("stepType", step["stepType"]),
        ("target", step_target(step)),
        ("expression", (keyword + " " + body).strip() if keyword else body),
        ("tables", tables),
    ))
    if step["notes"]:
        summary["notes"] = step["notes"]
    if step["storeType"]:
        summary["storeType"] = step["storeType"]
    return summary


def routine_document(book, routine, step_detail="full"):
    parameter_set = book.parameter_sets.get(routine["parameterSetId"], {})
    document = OrderedDict((
        ("routine", OrderedDict((
            ("code", routine["code"]),
            ("name", routine["name"]),
            ("description", routine["description"]),
            ("publicId", routine["publicId"]),
            ("version", routine["version"]),
            ("policyLine", routine["policyLine"]),
            ("jurisdiction", routine["jurisdiction"]),
            ("attachedToBook", routine["attachedToBook"]),
            ("book", book.header()["code"]),
            ("stepCount", len(routine["steps"])),
            ("stepDetail", step_detail),
            ("text", "%s.txt" % slug(routine["code"], routine["publicId"])),
        ))),
        ("parameterSet", parameter_set or OrderedDict((("publicId", routine["parameterSetId"]),))),
        ("references", routine["references"]),
    ))
    # stepDetail above says which of these three a reader is holding, so a thin document is never
    # mistaken for a short routine.
    if step_detail == "full":
        document["steps"] = routine["steps"]
    elif step_detail == "summary":
        document["steps"] = [summarize_step(step) for step in routine["steps"]]
    return document


# ----------------------------------------------------------------------------------------------
# Terminal summary: the gate reads it. Every fact here is a gate fact; the analysis facts are in
# the JSON.
# ----------------------------------------------------------------------------------------------


def open_questions(book, csv_problems, row_plans, row_detail, row_limit):
    """What the export does not carry, each as (kind, question with its locator)."""
    questions = []
    for definition in sorted(book.definitions.values(), key=lambda d: d["code"]):
        for key in definition["keys"]:
            if key["isCustomMatchOp"]:
                questions.append(("custom match op", "`%s`.`%s` uses match-op definition `%s`, which is"
                                  " not a platform one. Name the class and its semantics from the"
                                  " checkout." % (definition["code"], key["name"], key["matchOpDefinition"])))
    for table in sorted(book.tables.values(), key=lambda t: t["code"]):
        plan = row_plans.get(table["publicId"], {})
        if plan.get("truncated"):
            questions.append(("rows not carried in full", "`%s` has %d row(s); this run wrote %d of"
                              " them to `tables/%s`. Re-run with `--rows all` before using its rates"
                              " for a finding." % (table["code"], table["rowCount"], plan["written"],
                                                  plan["file"])))
    if row_detail == "none" and any(t["rowCount"] for t in book.tables.values()):
        questions.append(("rows not carried", "This run wrote no table rows (`--rows none`); the"
                          " book's %d row(s) are only in the export."
                          % sum(t["rowCount"] for t in book.tables.values())))
    oversize = sorted("`%s` (%d rows)" % (t["code"], t["rowCount"]) for t in book.tables.values()
                      if row_limit and t["rowCount"] > row_limit
                      and not row_plans.get(t["publicId"], {}).get("truncated")
                      and row_plans.get(t["publicId"], {}).get("detail") != "none")
    if oversize:
        questions.append(("large tables", "carried in full and above the `--row-limit` of %d: %s."
                          % (row_limit, ", ".join(oversize))))
    empty = sorted("`%s`" % t["code"] for t in book.tables.values() if not t["rowCount"])
    if empty:
        questions.append(("empty tables", "%d table(s) carry zero rows: %s. Confirm whether they are"
                          " empty, held in another edition, or excluded from the export."
                          % (len(empty), ", ".join(empty))))
    for definition in sorted(book.definitions.values(), key=lambda d: d["code"]):
        for source_set in definition["argumentSourceSets"]:
            for entry in source_set["sources"]:
                if not entry["isBound"]:
                    questions.append(("unbound argument source", "`%s` source set `%s` key `%s` has no"
                                      " Root or ArgumentSource. Confirm what supplies its value."
                                      % (definition["code"], source_set["code"],
                                         entry["key"] or entry["publicId"])))
    for code, columns in csv_problems:
        questions.append(("unmapped row columns", "`%s` rows populate physical column(s) %s that no"
                          " column definition claims, so the CSV omits them."
                          % (code, ", ".join("`%s`" % c for c in columns))))
    dangling = sorted({entry["code"] for routine in book.routines.values()
                       for entry in routine["references"]["tables"] if not entry["inBook"]})
    if dangling:
        questions.append(("table not in book", "routines look up %s, which this book does not carry."
                          " Confirm which book supplies them." % ", ".join("`%s`" % c for c in dangling)))
    unattached = sorted("`%s`" % r["code"] for r in book.routines.values() if not r["attachedToBook"])
    if unattached:
        questions.append(("routine not attached", "%s are in the export but not attached to the book."
                          " Confirm whether they run." % ", ".join(unattached)))
    for row in book.unknowns.rows():
        if row["kind"] == "matchOpDefinition":
            continue  # the custom match op question above names it
        questions.append(("unrecognized %s" % row["kind"], "`%s` appears %d time(s), first at %s."
                          " Confirm how PC evaluates it before trusting the rendered text."
                          % (row["value"], row["occurrences"], row["firstSeenAt"])))
    if book.populated_l10n:
        questions.append(("localized labels", "%d localization array(s) carry content (%s); only the"
                          " base-language label is written."
                          % (len(book.populated_l10n),
                             ", ".join(sorted({name for name, _ in book.populated_l10n})))))
    return questions


def print_summary(book, out, counts, csv_problems, row_plans, args):
    header = book.header()
    not_carried = sorted(set(book.tag_counts) - carried_tags(book))
    questions = open_questions(book, csv_problems, row_plans, args.rows, args.row_limit)
    kinds = {kind for kind, _ in questions}
    lines = ["ratebook-extract: %s edition %s -> %s" % (header["code"], header["edition"], out),
             "  %d routine(s), %d table(s), %d step(s), %s step detail in the JSON"
             % (counts["routines"], counts["tables"], counts["calcSteps"], args.steps),
             "  %d of %d row(s) written, %s row detail" % (counts["rowsWritten"], counts["rows"], args.rows),
             "  counts: " + ", ".join("%s %d" % (label, value) for label, value in counts.items())]
    lines.append("Elements not carried: %s" % (", ".join("%s (%d)" % (name, book.tag_counts[name])
                                                         for name in not_carried) or "none"))
    lines.append("Open questions:")
    lines += ["  - %s: %s" % (kind, text) for kind, text in questions] or ["  none"]
    lines.append("Readiness:")
    for done, text in [
            (not not_carried, "every element in the export is carried"),
            (not book.unknowns.rows(), "no unrecognized enumeration values"),
            ("empty tables" not in kinds, "every rate table carries its rows"),
            (args.rows == "all" and "rows not carried in full" not in kinds,
             "every rate table's rows are in this folder in full"),
            ("table not in book" not in kinds, "every table a routine looks up is in this book"),
            ("unbound argument source" not in kinds, "every argument source binds to an object-graph path"),
            ("custom match op" not in kinds, "every match-op definition is a platform one"),
            (False, "every open question is answered from the configuration checkout"),
            (False, "this edition is the one the client rates on today")]:
        lines.append("  [%s] %s" % ("x" if done else " ", text))
    sys.stdout.write("\n".join(lines) + "\n")


# ----------------------------------------------------------------------------------------------
# Survey and run
# ----------------------------------------------------------------------------------------------


def carried_tags(book):
    """Element names this extractor reads. A row's element name is the configured row entity and a
    named physical column is whatever the row entity calls it, so neither is known until the export
    is parsed; both are read, so both are carried."""
    tags = set(CARRIED)
    tags |= {row["entity"] for row in book.rows}
    tags |= {local(e.tag) for e in book.root if ref(e, "RateTable")}
    for definition in book.definitions.values():
        for key in definition["keys"]:
            tags |= {c["physicalColumn"] for c in key["columns"] if c["physicalColumn"]}
        tags |= {c["physicalColumn"] for c in definition["factors"] if c["physicalColumn"]}
    return tags


def survey(path):
    root = ET.parse(path).getroot()
    if local(root.tag) != "import":
        die("expected an <import> root element, found <%s> in %s" % (local(root.tag), path))
    book = Ratebook(path, root, Unknowns())
    carried = carried_tags(book)
    print("ratebook export: %s" % path)
    print("exporter version: %s" % root.get("version", "-"))
    print("")
    print("%-40s %8s  %s" % ("element", "count", "carried"))
    for name, count in sorted(book.tag_counts.items()):
        print("%-40s %8d  %s" % (name, count, "yes" if name in carried else "NO"))
    missing = sorted(set(book.tag_counts) - carried)
    print("")
    print("elements this extractor would not carry: %s" % (", ".join(missing) if missing else "none"))


def run(args):
    path = args.ratebook
    if not os.path.exists(path):
        die("ratebook not found: %s" % path)
    with open(path, "rb") as handle:
        raw = handle.read()
    source = OrderedDict((
        ("file", os.path.basename(path)),
        ("sha256", hashlib.sha256(raw).hexdigest()),
    ))
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        die("cannot parse %s: %s" % (path, error))
    if local(root.tag) != "import":
        die("expected an <import> root element, found <%s> in %s" % (local(root.tag), path))

    book = Ratebook(path, root, Unknowns())

    out = args.out
    if out is None and args.product_root:
        header = book.header()
        leaf = "%s-%s" % (header["code"], header["edition"])
        out = os.path.join(args.product_root, "extracted", "ratebooks", leaf)
    if os.path.isdir(out) and os.listdir(out) and not args.force:
        die("%s is not empty; pass --force to rewrite it" % out)
    os.makedirs(out, exist_ok=True)

    csv_problems = []
    row_plans = {}

    for table in sorted(book.tables.values(), key=lambda t: t["code"]):
        definition = book.definitions.get(table["definitionId"])
        if definition is None:
            continue
        name = slug(table["code"], table["publicId"])
        plan = plan_rows(table, name, args.rows, args.row_limit)
        row_plans[table["publicId"]] = plan
        if plan["file"]:
            csv_path = os.path.join(out, "tables", plan["file"])
            header, _ = write_table_csv(csv_path, definition, table, plan["limit"])
        else:
            # --rows none. The header still comes from the column definitions, so the table JSON
            # names its columns whether or not a row was written.
            header = csv_header(definition)
        # Run in every mode: this is the check that catches client data the column map would drop,
        # and it must not weaken because a run chose to carry fewer rows.
        unmapped = unmapped_row_columns(definition, table)
        if unmapped:
            csv_problems.append([table["code"], unmapped])
        write_json(os.path.join(out, "tables", "%s.json" % name),
                   table_document(book, table, definition, header, plan))

    for routine in sorted(book.routines.values(), key=lambda r: r["code"]):
        name = slug(routine["code"], routine["publicId"])
        write_json(os.path.join(out, "routines", "%s.json" % name), routine_document(book, routine, args.steps))
        text_path = os.path.join(out, "routines", "%s.txt" % name)
        os.makedirs(os.path.dirname(text_path), exist_ok=True)
        with open(text_path, "w", encoding="utf-8") as handle:
            handle.write(render_routine_text(book, routine))

    document = book_document(book, source, row_plans)
    write_json(os.path.join(out, "book.json"), document)
    print_summary(book, out, document["counts"], csv_problems, row_plans, args)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ratebook", required=True, help="path to the ratebook export XML")
    parser.add_argument("--product-root", default=None,
                        help="product root; supplies the default for --out"
                             " (<root>/extracted/ratebooks/<book code>-<edition>, derived once the export is"
                             " parsed); an explicitly passed --out always wins and is written to"
                             " directly, with no extracted/ratebooks/ nesting")
    parser.add_argument("--out", default=None,
                        help="directory to write the extraction into; required unless --survey or --product-root")
    parser.add_argument("--steps", choices=("full", "summary", "none"), default="full",
                        help="how much of each calc routine the routine JSON carries. full"
                             " (default): every step with its whole operand tree. summary: one line"
                             " per step — order, type, target, the same expression text the .txt"
                             " renders, and the tables it consults. none: no steps at all, leaving"
                             " the parameters and the references. The .txt rendering is written in"
                             " full whichever is chosen, and routine.stepDetail in the JSON records"
                             " which one produced it")
    parser.add_argument("--rows", choices=("all", "sample", "none"), default="all",
                        help="how much of each rate table's rows the CSVs carry. all (default):"
                             " every row. sample: at most --row-limit rows per table, taken as the"
                             " head of the sorted table so re-runs are byte-stable; a table cut"
                             " short is written to tables/<code>.sample.csv, never"
                             " tables/<code>.csv. none: no row CSV is written at all. Row counts,"
                             " keys, factors, the physical-column map and the argument sources are"
                             " complete whichever is chosen, and table.rows in the JSON records"
                             " which one produced the folder")
    parser.add_argument("--row-limit", type=int, default=500, metavar="N",
                        help="rows per table under --rows sample (default: 500). Under --rows all"
                             " it cuts nothing; it only decides which tables the summary names as"
                             " large, so a later sample run tells you what it would cut")
    parser.add_argument("--force", action="store_true", help="rewrite a non-empty --out")
    parser.add_argument("--survey", action="store_true",
                        help="list every element in the export and whether this extractor carries"
                             " it; needs no --out, prints to stdout and writes nothing")
    args = parser.parse_args(argv)
    if not args.survey and not args.out and not args.product_root:
        parser.error("--out is required unless --survey or --product-root")
    if args.row_limit < 1:
        parser.error("--row-limit must be at least 1; pass --rows none to write no rows at all")
    return args


if __name__ == "__main__":
    _args = parse_args(sys.argv[1:])
    if _args.survey:
        survey(_args.ratebook)
    else:
        run(_args)
