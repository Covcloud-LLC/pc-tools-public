#!/usr/bin/env python3
"""Extract one PC line's rating workspace: the objects its calc routines read and write.

Inputs, all read-only:
  * <product-root>/extracted/ratebooks/*/   pc-ratebook-extraction output (book.json, routines/, tables/)
  * <product-root>/extracted/product-capture.json   (state: ready)
  * the PC checkout at the capture's source.revision, and its admin/lib/pc-<version>.jar
    when present

Output: <product-root>/extracted/rating-workspace.json, plus a summary on stdout (counts and
every unresolved subject) for the human gate.

One mechanical pass. Objects come from the routines' parameter sets, nesting from the captured data
model (arrays) and DTO collection fields, property types from the capture, the checkout's entity
metadata, enhancements and PC classes, then the export. Whatever the inputs do not establish is
listed under `unresolved`.

Exit codes: 0 written, 2 refused (bad flags, missing or mismatched input, existing output).
Python 3.8+ standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.parsers.expat
import zipfile
from pathlib import Path

FORMAT = "pc-tools.rating-workspace/1.0.0"
CONFIGURATION_ROOT = "modules/configuration"
CAPTURE_PATH = "extracted/product-capture.json"
RATEBOOKS_PATH = "extracted/ratebooks"
CAPTURE_FORMAT = "pc-tools.product-capture/1.0.0"
COST_DATA_CLASS = "gw.rating.flow.domain.CalcRoutineCostData"
COST_PARAMETER = "costdata"
DTO_PACKAGE = "gw.api.rating.dtobased."
POLICY_LINE_DTO = "gw.api.rating.dtobased.data.PolicyLineDTO"
PLATFORM_JAR = re.compile(r"^pc-\d+(\.\d+)*\.jar$")
PRIMITIVES = {"int", "long", "short", "byte", "boolean", "double", "float", "char", "String", "Date",
              "BigDecimal", "BigInteger", "Integer", "Long", "Boolean", "Double", "Object", "Key"}
MEMBER_KINDS = {"column", "typekey", "foreignkey", "array", "monetaryamount", "onetoone", "edgeforeignkey"}
MAX_DEPTH = 8


class Refusal(Exception):
    pass


def refuse(message):
    raise Refusal(message)


# ----------------------------------------------------------------------------------------------
# Small helpers
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


def git(path, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(path)] + list(args),
                                   stderr=subprocess.DEVNULL).decode().strip()


def source_identity(root, read_paths):
    """Commit SHA and whether `read_paths` have uncommitted or untracked changes, for a source root
    that is the top level of its own Git checkout; (None, None) otherwise."""
    try:
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
            return None, None
        return git(root, "rev-parse", "HEAD"), bool(git(root, "status", "--porcelain", "--", *read_paths))
    except (OSError, subprocess.CalledProcessError):
        return None, None


def simple_name(name):
    return name.rsplit(".", 1)[-1]


def strip_comments(text):
    """Blank out PC code // and /* */ comments, keeping every line break so line numbers hold."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", text[i:end]))
            i = end
        elif text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif text[i] == '"':
            end = i + 1
            while end < n and text[end] not in '"\n':
                end += 2 if text[end] == "\\" else 1
            out.append(text[i:end + 1])
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def parse_xml_with_lines(path):
    """Parse an entity metadata file into nested dicts {tag, attrs, line, children}."""
    root, stack = {}, []
    parser = xml.parsers.expat.ParserCreate()

    def start(name, attrs):
        node = {"tag": name, "attrs": dict(attrs), "line": parser.CurrentLineNumber, "children": []}
        if stack:
            stack[-1]["children"].append(node)
        else:
            root.update(node)
        stack.append(node)

    def end(_name):
        stack.pop()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    with open(str(path), "rb") as handle:
        parser.ParseFile(handle)
    return root


# ----------------------------------------------------------------------------------------------
# The checkout: entity metadata, enhancements and PC classes, plus the platform jar.
# ----------------------------------------------------------------------------------------------


class GosuClass:
    """The members one PC class, interface or enhancement declares in its own source: each
    `property get Name() : Type` and `var _x : Type as Name`, with line numbers."""

    def __init__(self, fqn, text, location, kind):
        self.fqn = fqn
        self.location = location
        self.kind = kind
        code = strip_comments(text)
        self.code = code
        package = re.search(r"^\s*package\s+([\w.]+)", code, re.M)
        self.package = package.group(1) if package else ""
        self.uses = re.findall(r"^\s*uses\s+([\w.]+)\s*$", code, re.M)
        header = re.search(r"\b(?:class|interface|enhancement)\s+(\w+)(\s*<[^>]*>)?([^{]*)\{", code)
        self.extends = None
        if header:
            extends = re.search(r"\bextends\s+([\w.]+)", header.group(3))
            if extends:
                self.extends = extends.group(1)
        self.fields = {}
        for name, field_type in re.findall(r"^\s*(?:(?:private|protected|public|internal|static)\s+)*var\s+(\w+)\s*:\s*([\w.]+)",
                                           code, re.M):
            self.fields.setdefault(name, field_type)
        self.members = {}
        for number, line in enumerate(code.split("\n"), 1):
            prop = re.search(r"\bproperty\s+get\s+(\w+)\s*\(\s*\)\s*:\s*([^{]+?)\s*(?:\{.*)?$", line)
            if prop:
                self.members.setdefault(prop.group(1), (prop.group(2).strip(), number))
                continue
            var = re.search(r"\bvar\s+\w+\s*:\s*(.+?)\s+as\s+(?:readonly\s+)?(\w+)\b", line)
            if var:
                self.members.setdefault(var.group(2), (var.group(1).strip(), number))

    def at(self, line):
        return "%s:%d" % (self.location, line)

    def getter_body(self, name):
        """(first line, body text) of `property get Name()`, brace-matched; None for a property a
        `var ... as Name` declares or a getter with no body."""
        match = re.search(r"\bproperty\s+get\s+%s\s*\(\s*\)" % re.escape(name), self.code)
        if not match:
            return None
        start = self.code.find("{", match.end())
        if start < 0:
            return None
        depth, end = 0, start
        while end < len(self.code):
            if self.code[end] == "{":
                depth += 1
            elif self.code[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        return self.code.count("\n", 0, start) + 1, self.code[start:end + 1]


class Checkout:
    def __init__(self, root, configuration_root, jar_path):
        self.root = root
        self.config = root / configuration_root / "config"
        self.gsrc = root / configuration_root / "gsrc"
        self.jar_path = jar_path
        self._jar = None
        self._jar_entries = None
        self.jar_used = False
        self.classes = {}
        self._entities = None
        self._enhancements = None

    def rel(self, path):
        return Path(os.path.relpath(str(path), str(self.root))).as_posix()

    @property
    def jar_entries(self):
        """The platform jar's entry names, opening the jar on first use."""
        if self._jar_entries is None:
            self._jar = zipfile.ZipFile(str(self.jar_path)) if self.jar_path else None
            self._jar_entries = set(self._jar.namelist()) if self._jar else set()
        return self._jar_entries

    def entities(self):
        """Entity name -> [(file, root node)] across entity, subtype, delegate and extension files."""
        if self._entities is None:
            self._entities = {}
            for folder in [self.config / "metadata/entity", self.config / "extensions/entity"]:
                if not folder.is_dir():
                    continue
                for path in sorted(folder.iterdir()):
                    if path.suffix not in (".eti", ".etx", ".eix"):
                        continue
                    try:
                        node = parse_xml_with_lines(path)
                    except (xml.parsers.expat.ExpatError, OSError):
                        continue
                    attrs = node.get("attrs", {})
                    name = attrs.get("entity") or attrs.get("entityName") or attrs.get("name")
                    if name:
                        self._entities.setdefault(name, []).append((path, node))
        return self._entities

    def enhancements(self, entity):
        """The enhancements in gsrc whose target is the entity."""
        if self._enhancements is None:
            self._enhancements = {}
            if self.gsrc.is_dir():
                for path in sorted(self.gsrc.rglob("*.gsx")):
                    try:
                        code = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
                    match = re.search(r"^\s*enhancement\s+\w+\s*:\s*([\w.]+)", code, re.M)
                    if match:
                        target = match.group(1)
                        if target.startswith("entity."):
                            target = target[7:]
                        self._enhancements.setdefault(target, []).append(path)
        result = []
        for path in self._enhancements.get(entity, []):
            key = ("file", str(path))
            if key not in self.classes:
                fqn = ".".join(path.relative_to(self.gsrc).with_suffix("").parts)
                text = path.read_text(encoding="utf-8", errors="replace")
                self.classes[key] = GosuClass(fqn, text, self.rel(path), "checkout")
            result.append(self.classes[key])
        return result

    def gosu_class(self, fqn):
        """The class source for a fully qualified name: the checkout's gsrc first, then the jar."""
        if fqn in self.classes:
            return self.classes[fqn]
        found = None
        relative = fqn.replace(".", "/") + ".gs"
        path = self.gsrc / relative
        if path.is_file() and path.stat().st_size > 0:
            found = GosuClass(fqn, path.read_text(encoding="utf-8", errors="replace"), self.rel(path), "checkout")
        elif relative in self.jar_entries:
            text = self._jar.read(relative).decode("utf-8", errors="replace")
            found = GosuClass(fqn, text, "%s!/%s" % (self.rel(self.jar_path), relative), "platformJar")
        self.classes[fqn] = found
        return found

    def class_exists(self, fqn):
        return self.gosu_class(fqn) is not None

    def package_classes(self, package):
        names = set()
        folder = self.gsrc / package.replace(".", "/")
        if folder.is_dir():
            for path in folder.iterdir():
                if path.suffix == ".gs" and path.stat().st_size > 0:
                    names.add(package + "." + path.stem)
        prefix = package.replace(".", "/") + "/"
        for entry in self.jar_entries:
            if entry.startswith(prefix) and entry.endswith(".gs") and "/" not in entry[len(prefix):]:
                names.add(package + "." + entry[len(prefix):-3])
        return sorted(names)


# ----------------------------------------------------------------------------------------------
# The product capture
# ----------------------------------------------------------------------------------------------


def node_at(node):
    refs = node.get("sourceRefs") or []
    if not refs:
        return ""
    return "%s#%s" % (refs[0].get("path", ""), refs[0].get("symbol", ""))


class Capture:
    def __init__(self, data):
        self.data = data
        self.entities = {e["name"]: e["declarations"] for e in data.get("entities", [])}
        line = data["policyLinePattern"]["declaration"]
        self.line_entity = line["attributes"].get("policyLineSubtype", "")
        self.supertypes = {}
        for name, declarations in self.entities.items():
            for declaration in declarations:
                if declaration.get("kind") == "subtype" and declaration["attributes"].get("supertype"):
                    self.supertypes[name] = declaration["attributes"]["supertype"]
        self.arrays = {}
        for name in sorted(self.entities):
            for declaration in self.entities[name]:
                for child in declaration.get("children", []):
                    if child.get("kind") == "array" and child.get("referenceStatus", "captured") == "captured":
                        target = child["attributes"].get("arrayentity", "")
                        self.arrays.setdefault(target, []).append(
                            (name, child["attributes"].get("name", ""), node_at(child)))
        self.patterns = {}
        self.terms = {}
        self.term_clauses = {}
        for clause in data["policyLinePattern"].get("clauses", []):
            if clause.get("kind") != "CoveragePattern":
                continue
            clause_code = clause["attributes"].get("codeIdentifier", "")
            self.patterns[clause_code] = clause
            for group in clause.get("children", []):
                for term in group.get("children", []):
                    code = term.get("attributes", {}).get("codeIdentifier")
                    if code and term.get("kind", "").endswith("CovTermPattern"):
                        self.terms.setdefault(code, []).append(term)
                        if clause_code not in self.term_clauses.setdefault(code, []):
                            self.term_clauses[code].append(clause_code)
        self.modifiers = {}
        for group in line.get("children", []):
            if group.get("kind") == "ModifierPatterns":
                for modifier in group.get("children", []):
                    self.modifiers[modifier["attributes"].get("codeIdentifier", "")] = modifier

    def supertype_chain(self, entity):
        chain, seen = [], set()
        while entity in self.supertypes and entity not in seen:
            seen.add(entity)
            entity = self.supertypes[entity]
            chain.append(entity)
        return chain


# ----------------------------------------------------------------------------------------------
# Type resolution
# ----------------------------------------------------------------------------------------------


class Resolver:
    def __init__(self, capture, checkout):
        self.capture = capture
        self.checkout = checkout

    def entity_known(self, name):
        return name in self.capture.entities or name in self.checkout.entities()

    def entity_declarations(self, entity):
        if entity in self.capture.entities:
            return "capture", self.capture.entities[entity]
        return "checkout", self.checkout.entities().get(entity, [])

    @staticmethod
    def entity_parents(source, declarations):
        """Supertype and delegate names one entity's declarations name."""
        names = []
        if source == "capture":
            for declaration in declarations:
                attrs = declaration.get("attributes", {})
                if declaration.get("kind") == "subtype" and attrs.get("supertype"):
                    names.append(attrs["supertype"])
                for child in declaration.get("children", []):
                    if child.get("kind") == "implementsEntity" and child["attributes"].get("name"):
                        names.append(child["attributes"]["name"])
        else:
            for _path, node in declarations:
                if node.get("tag") == "subtype" and node["attrs"].get("supertype"):
                    names.append(node["attrs"]["supertype"])
                for child in node.get("children", []):
                    if child["tag"] == "implementsEntity" and child["attrs"].get("name"):
                        names.append(child["attrs"]["name"])
        return names

    @staticmethod
    def member_type(kind, attrs):
        if kind == "column":
            return attrs.get("type", ""), None
        if kind == "typekey":
            return "typekey." + attrs.get("typelist", ""), None
        if kind in ("foreignkey", "onetoone", "edgeforeignkey"):
            return "entity." + attrs.get("fkentity", ""), ("entity", attrs.get("fkentity", ""))
        if kind == "array":
            return "entity." + attrs.get("arrayentity", "") + "[]", None
        return kind, None

    def entity_member(self, entity, name):
        """(type, typeFrom, next, source) for a property of an entity, its supertypes and delegates:
        the capture or the checkout's entity metadata first, then enhancements. `source` names the
        declaring entity and column, with the member kind, or kind enhancement. None when undeclared."""
        order, queue, seen = [], [entity], set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            source, declarations = self.entity_declarations(current)
            if source == "capture":
                for declaration in declarations:
                    for child in declaration.get("children", []):
                        if child.get("kind") in MEMBER_KINDS and child["attributes"].get("name") == name:
                            found_type, nxt = self.member_type(child["kind"], child["attributes"])
                            return (found_type, {"kind": "capture", "at": node_at(child)}, nxt,
                                    self.field_source(current, name, child["kind"]))
            else:
                for path, node in declarations:
                    for child in node.get("children", []):
                        tag = child["tag"].lower()
                        if tag in MEMBER_KINDS and child["attrs"].get("name") == name:
                            found_type, nxt = self.member_type(tag, child["attrs"])
                            at = "%s:%d" % (self.checkout.rel(path), child["line"])
                            return found_type, {"kind": "checkout", "at": at}, nxt, self.field_source(current, name, tag)
            queue.extend(self.entity_parents(source, declarations))
        for current in order:
            for enhancement in self.checkout.enhancements(current):
                if name in enhancement.members:
                    found_type, line = enhancement.members[name]
                    return (found_type, {"kind": "checkout", "at": enhancement.at(line)},
                            self.type_target(found_type, enhancement), {"kind": "enhancement"})
        return None

    @staticmethod
    def field_source(entity, name, member_kind):
        """An entity member in the PAS Binding's words; the member kind stays private to the walk."""
        return {"kind": "entityField", "entityType": "entity." + entity, "field": name, "_member": member_kind}

    def type_target(self, type_name, context):
        """What a declared type names, for walking further: ('entity', name), ('class', fqn) or None."""
        name = type_name.strip()
        if name.startswith("entity."):
            return ("entity", name[7:])
        if "<" in name or name.endswith("]") or name.startswith(("typekey.", "java.")) or name in PRIMITIVES:
            return None
        if "." in name:
            return ("class", name) if self.checkout.class_exists(name) else None
        for use in context.uses:
            if use.endswith("." + name):
                if use.startswith("entity."):
                    return ("entity", name)
                if use.startswith(("typekey.", "java.")):
                    return None
                return ("class", use)
        if context.package and self.checkout.class_exists(context.package + "." + name):
            return ("class", context.package + "." + name)
        if self.entity_known(name):
            return ("entity", name)
        return None

    def class_chain(self, fqn):
        """The class and the classes it extends that the inputs hold, and the first name in the chain
        they do not hold (None when the chain ends)."""
        chain, seen, missing, current = [], set(), None, fqn
        while current and current not in seen:
            seen.add(current)
            klass = self.checkout.gosu_class(current)
            if klass is None:
                missing = current
                break
            chain.append(klass)
            if not klass.extends:
                break
            target = self.type_target(klass.extends, klass)
            if target and target[0] == "class":
                current = target[1]
            else:
                missing = klass.extends
                break
        return chain, missing

    def class_member(self, fqn, name):
        """(type, typeFrom, next, missing, declaring class) for a property of a PC class or the
        classes it extends."""
        chain, missing = self.class_chain(fqn)
        for klass in chain:
            if name in klass.members:
                found_type, line = klass.members[name]
                if klass.kind == "platformJar":
                    self.checkout.jar_used = True
                return found_type, {"kind": klass.kind, "at": klass.at(line)}, self.type_target(found_type, klass), None, klass
        return None, None, None, missing, None

    def collection_element(self, type_name, context):
        match = (re.match(r"^(?:java\.util\.)?(?:List|Set|Collection|ArrayList)\s*<\s*([\w.]+)\s*>$", type_name)
                 or re.match(r"^([\w.]+)\s*\[\]$", type_name))
        if not match:
            return None
        target = self.type_target(match.group(1), context)
        return target[1] if target and target[0] == "class" else None


# ----------------------------------------------------------------------------------------------
# The extraction
# ----------------------------------------------------------------------------------------------


def public_source(source):
    """A source without the walk's private keys, its own and its reads'."""
    clean = {k: v for k, v in source.items() if not k.startswith("_")}
    if "reads" in clean:
        clean["reads"] = [public_source(read) for read in clean["reads"]]
    return clean


def classify(parameter):
    """(kind, className, entity) for a parameter: entity, wrapper, dto, gosuClass or scalar."""
    param_type = parameter.get("paramType", "")
    if param_type.startswith("entity."):
        if parameter.get("useWrapper") and parameter.get("wrapperClass"):
            return "wrapper", parameter["wrapperClass"], param_type[7:]
        return "entity", param_type, param_type[7:]
    if param_type.startswith(DTO_PACKAGE):
        return "dto", param_type, None
    if param_type.startswith(("typekey.", "java.")) or param_type in PRIMITIVES or "." not in param_type:
        return "scalar", param_type, None
    return "gosuClass", param_type, None


class Extraction:
    def __init__(self, args):
        self.args = args
        self.product_root = Path(args.product_root)
        self.source_root = Path(args.source_root)
        self.unresolved = {}

    def note(self, subject, reason, at):
        entry = self.unresolved.setdefault(subject, {"subject": subject, "reason": reason, "at": []})
        for locator in at:
            if locator and locator not in entry["at"]:
                entry["at"].append(locator)

    # -- inputs ------------------------------------------------------------------------------

    def load_inputs(self):
        if not self.product_root.is_dir():
            refuse("product root %s is not a directory" % self.product_root)
        if not self.source_root.is_dir():
            refuse("source root %s is not a directory" % self.source_root)
        capture_path = self.product_root / CAPTURE_PATH
        if not capture_path.is_file():
            refuse("missing product capture %s" % capture_path)
        try:
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            refuse("cannot read %s: %s" % (capture_path, error))
        if capture.get("formatVersion") != CAPTURE_FORMAT:
            refuse("%s is not a %s capture" % (capture_path, CAPTURE_FORMAT))
        if capture.get("state") != "ready":
            refuse("%s has state %r; only a ready capture is accepted" % (capture_path, capture.get("state")))
        self.capture = Capture(capture)
        self.product = capture.get("scope", {}).get("product", "")
        self.line = capture.get("scope", {}).get("line", "")

        configuration = self.source_root / CONFIGURATION_ROOT
        if not (configuration / "gsrc").is_dir() and not (configuration / "config").is_dir():
            refuse("%s holds neither config/ nor gsrc/" % configuration)
        read_paths = [Path(CONFIGURATION_ROOT, part).as_posix() for part in ("config", "gsrc")]
        self.revision, self.dirty = source_identity(self.source_root, read_paths)
        captured = capture.get("source", {}).get("revision")
        if self.revision is None:
            refuse("source root %s is not the top level of a Git checkout, so it has no revision" % self.source_root)
        if self.revision != captured:
            refuse("checkout is at %s but the capture was taken at %s" % (self.revision, captured))

        jar = None
        if not self.args.no_platform_jar:
            lib = self.source_root / "admin" / "lib"
            jars = sorted(p for p in lib.iterdir() if PLATFORM_JAR.match(p.name)) if lib.is_dir() else []
            if len(jars) > 1:
                refuse("more than one platform jar in %s: %s" % (lib, ", ".join(p.name for p in jars)))
            jar = jars[0] if jars else None
        self.checkout = Checkout(self.source_root, CONFIGURATION_ROOT, jar)
        self.resolver = Resolver(self.capture, self.checkout)

        folder = self.product_root / RATEBOOKS_PATH
        books = sorted(p.parent for p in folder.glob("*/book.json")) if folder.is_dir() else []
        if not books:
            refuse("no ratebook folder under %s" % folder)
        self.books = []
        for book_dir in books:
            book = read_json(book_dir / "book.json")
            line = book.get("book", {}).get("policyLine", "")
            if line not in ("", self.line):
                refuse("ratebook folder %s is for line %s, not %s" % (book_dir.name, line, self.line))
            routines = []
            for entry in book.get("routines", []):
                path = book_dir / entry["file"]
                if not path.is_file():
                    refuse("ratebook folder %s lists missing routine file %s" % (book_dir.name, entry["file"]))
                routine = read_json(path)
                detail = routine.get("routine", {}).get("stepDetail")
                if detail != "full":
                    refuse("ratebook folder %s routine %s has stepDetail %r; re-extract the book with --steps full"
                           % (book_dir.name, entry["file"], detail))
                routines.append(routine)
            tables = {}
            for entry in book.get("tables", []):
                path = book_dir / entry["file"]
                if not path.is_file():
                    refuse("ratebook folder %s lists missing table file %s" % (book_dir.name, entry["file"]))
                tables[entry["code"]] = (read_json(path), entry["file"])
            self.books.append({"folder": RATEBOOKS_PATH + "/" + book_dir.name, "book": book,
                               "code": book["book"]["code"], "routines": routines, "tables": tables})
        self.book_folders = {b["code"]: b["folder"] for b in self.books}

    # -- objects -----------------------------------------------------------------------------

    def is_line_parameter(self, kind, class_name, entity):
        if kind == "entity":
            return entity == self.capture.line_entity or entity in self.capture.supertype_chain(self.capture.line_entity)
        if kind == "dto":
            chain, _missing = self.resolver.class_chain(class_name)
            names = [klass.fqn for klass in chain]
            if chain and chain[-1].extends:
                target = self.resolver.type_target(chain[-1].extends, chain[-1])
                if target:
                    names.append(target[1])
            return POLICY_LINE_DTO in names
        return False

    def wrapper_patterns(self, class_name):
        """The capture's coverage pattern codes named by `case X:` in a wrapper's constructor."""
        klass = self.checkout.gosu_class(class_name)
        found = []
        if klass is None:
            return found
        body = re.search(r"\bconstruct\s*\(", klass.code)
        if not body:
            return found
        start = klass.code.find("{", body.end())
        depth, end = 0, start
        while 0 <= end < len(klass.code):
            if klass.code[end] == "{":
                depth += 1
            elif klass.code[end] == "}":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        for match in re.finditer(r"\bcase\s+(\w+)\s*:", klass.code[start:end]):
            if match.group(1) in self.capture.patterns:
                found.append((match.group(1), klass.at(klass.code.count("\n", 0, start + match.start()) + 1)))
        return found

    def narrowing(self, parameter, kind, class_name, entity):
        """The coverage subtype a coverage parameter stands for: from its coveragePattern, or from
        the coverage patterns its wrapper's constructor accepts, when they agree on one subtype."""
        if kind not in ("entity", "wrapper") or not entity:
            return None
        if parameter.get("coveragePattern"):
            found = [(parameter["coveragePattern"], "")]
        elif kind == "wrapper":
            found = self.wrapper_patterns(class_name)
        else:
            return None
        subtypes, at = set(), []
        for code, locator in found:
            clause = self.capture.patterns.get(code)
            if clause is None:
                return None
            subtypes.add(clause["attributes"].get("coverageSubtype", ""))
            at.extend([locator, node_at(clause)])
        if len(subtypes) != 1 or "" in subtypes:
            return None
        return {"entity": subtypes.pop(), "coveragePatterns": sorted({c for c, _ in found}),
                "at": [a for i, a in enumerate(at) if a and a not in at[:i]]}

    def build_objects(self):
        groups = {}
        self.scalars = []
        self.parameter_sets = {}
        for book in self.books:
            for routine in book["routines"]:
                pset = routine["parameterSet"]
                entry = self.parameter_sets.setdefault((book["code"], pset["code"]), {"set": pset, "routines": set()})
                entry["routines"].add(routine["routine"]["code"])
        for (book_code, set_code), info in sorted(self.parameter_sets.items()):
            pset = info["set"]
            routines = sorted(info["routines"])
            for parameter in pset.get("parameters", []):
                kind, class_name, entity = classify(parameter)
                ref = {"book": book_code, "parameterSet": set_code, "parameter": parameter["code"],
                       "paramType": parameter.get("paramType", "")}
                if kind == "scalar":
                    self.scalars.append({"book": book_code, "parameterSet": set_code, "parameter": parameter["code"],
                                         "type": parameter.get("paramType", ""), "routines": routines})
                    continue
                if parameter.get("useWrapper") and not parameter.get("wrapperClass"):
                    self.note("parameter:%s:%s:%s" % (book_code, set_code, parameter["code"]),
                              "useWrapper is set without a wrapperClass; read as the entity",
                              [self.book_folders[book_code] + "/book.json"])
                line_object = self.is_line_parameter(kind, class_name, entity)
                narrowed = None if line_object else self.narrowing(parameter, kind, class_name, entity)
                if line_object:
                    identity = ("@root",) if kind == "entity" else ("@line", class_name)
                else:
                    identity = (parameter["code"], kind, class_name, entity or "", parameter.get("coveragePattern", ""))
                group = groups.setdefault(identity, {"codes": [], "kind": kind, "className": class_name,
                                                     "entity": entity, "subtype": narrowed, "parameters": [],
                                                     "line": line_object})
                group["codes"].append(parameter["code"])
                group["parameters"].append(ref)
            if pset.get("includesCost"):
                group = groups.setdefault(("@cost",), {"codes": [], "kind": "costData", "className": COST_DATA_CLASS,
                                                       "entity": None, "subtype": None, "parameters": [], "line": False})
                group["codes"].append(COST_PARAMETER)
                group["parameters"].append({"book": book_code, "parameterSet": set_code,
                                            "parameter": COST_PARAMETER, "paramType": COST_DATA_CLASS})

        dto_lines = sorted((i for i, g in groups.items() if g["line"]), key=str)
        if ("@root",) not in groups and len(dto_lines) == 1:
            root_identity = dto_lines[0]
        else:
            root_identity = ("@root",)
            root = groups.setdefault(root_identity, {"codes": [], "kind": "entity", "subtype": None,
                                                     "parameters": [], "line": True})
            root["className"], root["entity"] = "entity." + self.capture.line_entity, self.capture.line_entity

        by_code = {}
        for identity, group in groups.items():
            codes = sorted(set(group["codes"]), key=lambda c: (-group["codes"].count(c), c))
            group["code"] = codes[0] if codes else (group["entity"] or simple_name(group["className"]))
            by_code.setdefault(group["code"], []).append(identity)
        self.objects = {}
        for code, identities in sorted(by_code.items()):
            for identity in sorted(identities, key=str):
                group = groups[identity]
                key = code
                if len(identities) > 1:
                    key = "%s@%s" % (code, (group["subtype"] or {}).get("entity") or group["entity"]
                                     or simple_name(group["className"]))
                group["parameters"].sort(key=lambda r: (r["book"], r["parameterSet"], r["parameter"]))
                self.objects[key] = {
                    "key": key, "kind": group["kind"], "className": group["className"], "entity": group["entity"],
                    "subtype": group["subtype"], "parent": None, "parentVia": None,
                    "parameters": group["parameters"], "uses": {}}
                if identity == root_identity:
                    self.root = key
        self.scalars.sort(key=lambda s: (s["book"], s["parameterSet"], s["parameter"]))
        self.parameter_objects = {}
        for key, obj in self.objects.items():
            for ref in obj["parameters"]:
                self.parameter_objects[(ref["book"], ref["parameterSet"], ref["parameter"])] = key
        self.scalar_parameters = {(s["book"], s["parameterSet"], s["parameter"]) for s in self.scalars}

    # -- nesting -----------------------------------------------------------------------------

    @staticmethod
    def backing_entity(obj):
        if obj["kind"] not in ("entity", "wrapper"):
            return None
        return (obj["subtype"] or {}).get("entity") or obj["entity"]

    def objects_backing(self, owner, dto):
        keys = []
        for key in sorted(self.objects):
            obj = self.objects[key]
            if dto:
                if obj["kind"] == "dto" and obj["className"] == owner:
                    keys.append(key)
            else:
                entity = self.backing_entity(obj)
                if entity and (entity == owner or owner in self.capture.supertype_chain(entity)):
                    keys.append(key)
        return keys

    def dto_owners(self, fqn):
        edges = []
        packages = sorted({obj["className"].rsplit(".", 1)[0] for obj in self.objects.values() if obj["kind"] == "dto"})
        for package in packages:
            for candidate in self.checkout.package_classes(package):
                klass = self.checkout.gosu_class(candidate)
                if klass is None:
                    continue
                for name in sorted(klass.members):
                    member_type, line = klass.members[name]
                    if self.resolver.collection_element(member_type, klass) == fqn:
                        if klass.kind == "platformJar":
                            self.checkout.jar_used = True
                        edges.append((candidate, "%s.%s" % (simple_name(candidate), name), klass.at(line)))
        return edges

    def entity_owners(self, entity):
        return [(owner, "%s.%s" % (owner, name), at) for owner, name, at in self.capture.arrays.get(entity, [])]

    def entity_foreign_keys(self, entity):
        """(target, field, at) for each captured foreign key the entity declares."""
        edges = []
        for declaration in self.capture.entities.get(entity, []):
            for child in declaration.get("children", []):
                target = child.get("attributes", {}).get("fkentity", "")
                if child.get("kind") == "foreignkey" and target and child.get("referenceStatus", "captured") == "captured":
                    edges.append((target, "%s.%s" % (entity, child["attributes"].get("name", "")), node_at(child)))
        return edges

    def find_parent(self, key):
        """Walk up the data model from an object to the nearest level that reaches another object:
        DTO collection fields for a DTO, else captured arrays, then captured foreign keys when no
        array connects it. Returns (edge kind, target key, path, ambiguity)."""
        obj = self.objects[key]
        if obj["kind"] == "dto":
            return ("dtoField",) + self.walk_up(key, obj["className"], True, self.dto_owners)
        start = self.backing_entity(obj)
        found = self.walk_up(key, start, False, self.entity_owners)
        if found == (None, None, None):
            by_key = self.walk_up(key, start, False, self.entity_foreign_keys)
            if by_key != (None, None, None):
                return ("foreignKey",) + by_key
        return ("array",) + found

    def walk_up(self, key, start, dto, owners):
        """Level by level from `start` along `owners` edges. Returns (target key, path, ambiguity)."""
        level, seen = {start: [[]]}, {start}
        for _depth in range(MAX_DEPTH):
            hits, nxt = [], {}
            for node in sorted(level):
                for path in level[node]:
                    for owner, field, at in owners(node):
                        step = path + [(owner, field, at)]
                        targets = [k for k in self.objects_backing(owner, dto) if k != key]
                        if targets:
                            hits.append((targets, step))
                        elif owner not in seen:
                            nxt.setdefault(owner, []).append(step)
            if hits:
                targets = {t for target_keys, _ in hits for t in target_keys}
                if len(hits) == 1 and len(targets) == 1:
                    return targets.pop(), hits[0][1], None
                routes = sorted("%s through %s" % ("/".join(t), " then ".join(s[1] for s in step)) for t, step in hits)
                return None, None, "the data model connects it to more than one place: " + "; ".join(routes)
            if not nxt:
                break
            seen.update(nxt)
            level = nxt
        return None, None, None

    def object_locators(self, obj):
        return sorted({self.book_folders[p["book"]] + "/book.json" for p in obj["parameters"]})

    def nest(self):
        for key in sorted(self.objects):
            obj = self.objects[key]
            if key == self.root or obj["parent"] is not None:
                continue
            if obj["kind"] in ("gosuClass", "costData"):
                reason = ("the routines' cost output object; the data model gives it no parent"
                          if obj["kind"] == "costData" else "%s is a PC class outside the data model" % obj["className"])
                self.note("object:%s" % key, reason, self.object_locators(obj))
                continue
            edge_kind, target, path, ambiguity = self.find_parent(key)
            if target is None:
                start = obj["className"] if edge_kind == "dtoField" else self.backing_entity(obj)
                if ambiguity:
                    reason = ambiguity
                else:
                    reason = "no %s in the inputs connects %s to the line object" % (
                        "DTO collection field" if edge_kind == "dtoField" else "captured array or foreign key", start)
                    if edge_kind == "array":
                        held = []
                        for subtype in sorted(n for n, s in self.capture.supertypes.items() if s == start):
                            held.extend(field for _o, field, _a in self.entity_owners(subtype))
                        if held:
                            reason += "; arrays holding its subtypes: %s" % ", ".join(sorted(held))
                self.note("object:%s" % key, reason, self.object_locators(obj))
                continue
            child = key
            for index, (owner, field, at) in enumerate(path):
                last = index == len(path) - 1
                parent_key = target if last else self.intermediate(owner, edge_kind)
                self.objects[child]["parent"] = parent_key
                self.objects[child]["parentVia"] = {"kind": edge_kind, "field": field, "at": at}
                if not last and self.objects[parent_key]["parent"] is not None:
                    break
                child = parent_key

    def intermediate(self, owner, edge_kind):
        """The object for a data-model class between a parameter object and its nearest object."""
        dto = edge_kind == "dtoField"
        existing = self.objects_backing(owner, dto)
        if existing:
            return existing[0]
        key = simple_name(owner)
        while key in self.objects:
            key += "_"
        self.objects[key] = {
            "key": key, "kind": "dto" if dto else "entity", "className": owner if dto else "entity." + owner,
            "entity": None if dto else owner, "subtype": None, "parent": None, "parentVia": None,
            "parameters": [], "uses": {}}
        return key

    # -- uses --------------------------------------------------------------------------------

    def record(self, routine, book, pset, param, path, mode, at, export_type="", modifier=False, cov_term=""):
        """One read or write of `param.path` by a routine."""
        identity = (book["code"], pset["code"], param)
        obj_key = self.parameter_objects.get(identity)
        if obj_key is None:
            if identity in self.scalar_parameters:
                if path:
                    self.note("scalarPath:%s:%s.%s" % (routine, param, path),
                              "a property path on the scalar parameter %s" % param, [at])
                return
            self.note("parameter:%s:%s" % (routine, param),
                      "names a parameter its parameter set %s does not have" % pset["code"], [at])
            return
        if not path:
            return
        use = self.objects[obj_key]["uses"].setdefault(
            path, {"readBy": set(), "writtenBy": set(), "exportTypes": {}, "modifier": False, "covTerm": "", "at": []})
        (use["writtenBy"] if mode == "write" else use["readBy"]).add(routine)
        if export_type:
            use["exportTypes"].setdefault(export_type, at)
        use["modifier"] = use["modifier"] or bool(modifier)
        if cov_term and not use["covTerm"]:
            use["covTerm"] = cov_term
        if at not in use["at"]:
            use["at"].append(at)

    def collect_uses(self):
        for book in self.books:
            for routine in book["routines"]:
                code = routine["routine"]["code"]
                pset = routine["parameterSet"]
                locator = "%s/routines/%s.json" % (book["folder"], code)
                for step in routine.get("steps", []):
                    step_at = "%s#step %s" % (locator, step.get("order"))
                    if step.get("inScopeParam"):
                        self.record(code, book, pset, step["inScopeParam"], step.get("inScopeValue", ""), "write",
                                    step_at, step.get("storeType", ""))
                    for operand in step.get("operands", []):
                        if operand.get("inScopeParam"):
                            self.record(code, book, pset, operand["inScopeParam"], operand.get("inScopeValue", ""),
                                        "read", step_at, operand.get("inScopeValueType", ""),
                                        operand.get("inScopeValueIsModifier", False), operand.get("covTermCode", ""))
                        for argument in operand.get("arguments", []):
                            if argument.get("inScopeParam"):
                                self.record(code, book, pset, argument["inScopeParam"], argument.get("inScopeValue", ""),
                                            "read", step_at, "", argument.get("inScopeValueIsModifier", False),
                                            argument.get("covTermCode", ""))
                        if operand.get("tableCode"):
                            self.table_sources(code, book, pset, operand, step_at)

    def table_sources(self, code, book, pset, operand, step_at):
        """The argument-source paths a table lookup reads: the set with the operand's code that is
        bound to the routine's parameter set, less the keys the step overrides."""
        table_code = operand["tableCode"]
        set_code = operand.get("argumentSourceSetCode", "")
        entry = book["tables"].get(table_code)
        if entry is None:
            self.note("table:%s:%s" % (code, table_code), "the table is not in the book's export", [step_at])
            return
        table, table_file = entry
        table_at = "%s/%s" % (book["folder"], table_file)
        overridden = {a.get("parameter") for a in operand.get("arguments", []) if a.get("overridesSource")}
        open_keys = [k.get("name", "") for k in table.get("keys", []) if k.get("name", "") not in overridden]
        sets = [s for s in table.get("argumentSourceSets", []) if s.get("code") == set_code]
        matched = [s for s in sets if s.get("parameterSet") == pset.get("publicId")]
        if not matched:
            if open_keys:
                bound = sorted({s.get("parameterSet", "") for s in sets})
                self.note("argumentSources:%s:%s:%s" % (code, table_code, set_code),
                          "table %s has no argument source set %s bound to parameter set %s (%s), and the step does not "
                          "override keys %s" % (table_code, set_code or '""', pset["code"],
                                                "sets with that code are bound to " + ", ".join(bound) if bound
                                                else "no set has that code", ", ".join(open_keys)),
                          [step_at, table_at])
            return
        for source in matched[0].get("sources", []):
            if not source.get("isBound") or not source.get("root") or source.get("key") in overridden:
                continue
            self.record(code, book, pset, source["root"], source.get("argumentSource", ""), "read",
                        "%s#argumentSourceSets/%s/%s" % (table_at, set_code, source.get("key", "")),
                        "", source.get("isModifier", False))

    # -- properties --------------------------------------------------------------------------

    def resolve(self, obj, path, use):
        """(type, typeFrom, source) for a path on an object, or (None, reason, source). `source` is
        the PC field the path stands for; it never changes the type or reason."""
        start = ("entity", self.backing_entity(obj)) if obj["kind"] == "entity" else ("class", obj["className"])
        narrowed = (obj["subtype"] or {}).get("coveragePatterns") or []
        type_name, type_from, source = self.walk(start, path.split("."), use["modifier"], narrowed, path)
        if use["covTerm"] and source.get("kind") != "covTerm":
            source = self.term_source(use["covTerm"], narrowed)
        if obj["kind"] == "costData":
            source = {"kind": "costData"}
        return type_name, type_from, source

    def walk(self, current, segments, modifier, narrowed, path, getters=True):
        """(type, typeFrom, source) for property segments walked from `current`, an ('entity', name)
        or ('class', fqn); (None, reason, source) when the inputs do not declare a segment. The
        source is the declaration of the last segment."""
        result, source = None, None
        for index, segment in enumerate(segments):
            last = index == len(segments) - 1
            if current is None:
                return None, "%s does not resolve past %s" % (path, ".".join(segments[:index])), {"kind": "unresolved"}
            if index == 0 and modifier and segment in self.capture.modifiers:
                modifier_node = self.capture.modifiers[segment]
                result = (modifier_node["attributes"].get("modifierDataType", ""),
                          {"kind": "capture", "at": node_at(modifier_node)}, None)
                source = {"kind": "modifierPattern", "codeIdentifier": segment}
            elif segment.endswith("Term") and segment[:-4] in self.capture.terms:
                return (None, "cov term %s: its property type is generated from the product model (%s)" % (
                    segment[:-4], node_at(self.capture.terms[segment[:-4]][0])), self.term_source(segment[:-4], narrowed))
            elif current[0] == "entity":
                found = self.resolver.entity_member(current[1], segment)
                if found is None:
                    source = {"kind": "unresolved"}
                    if last and segment.endswith("_amt"):
                        # PC's amount column of a MonetaryAmount field <field> is <field>_amt.
                        amount = self.resolver.entity_member(current[1], segment[:-4])
                        if amount and amount[3].get("_member") == "monetaryamount":
                            source = amount[3]
                    return None, "entity %s declares no %s in the capture, entity metadata or enhancements" % (
                        current[1], segment), source
                result, source = found[:3], found[3]
            else:
                found_type, type_from, nxt, missing, klass = self.resolver.class_member(current[1], segment)
                if found_type is None:
                    reason = "class %s declares no %s" % (current[1], segment)
                    if missing:
                        reason += "; %s is not in the inputs" % missing
                    return None, reason, {"kind": "unresolved"}
                result = (found_type, type_from, nxt)
                source = {"kind": "gosuProperty", "at": type_from["at"]}
                if getters and last:
                    source["reads"] = self.getter_reads(klass, segment)
            current = result[2]
        return result[0], result[1], source

    def term_source(self, code, narrowed):
        """A cov term by its code: the one captured clause pattern that declares it, among the
        patterns the object or the getter's case narrows to when any of them declares it."""
        clauses = self.capture.term_clauses.get(code, [])
        within = [c for c in clauses if c in narrowed]
        clauses = within or clauses
        if len(clauses) != 1:
            return {"kind": "unresolved"}
        return {"kind": "covTerm", "clausePattern": clauses[0], "codeIdentifier": code}

    def getter_reads(self, klass, name):
        """One read per `case`/return of a PC code property getter: what the returned expression reads
        from one of the class's own fields, in the same source vocabulary, with the case it is under.
        A literal return reads nothing; any other expression is a read of kind unresolved. A read of
        another PC code property is kind gosuProperty, not followed further."""
        found = klass.getter_body(name)
        if found is None:
            return []
        first, body = found
        reads, when = [], None
        for offset, line in enumerate(body.split("\n")):
            case = re.match(r"\s*case\s+(\w+)\s*:", line)
            if case:
                when = case.group(1)
            elif re.match(r"\s*default\s*:", line):
                when = None
            for match in re.finditer(r"\breturn\s+([^;\n]+)", line):
                expression = match.group(1).strip()
                if re.match(r"^(true|false|null|-?\d[\d.]*|\"[^\"]*\")$", expression):
                    continue
                entry = {"when": when} if when else {}
                entry.update(self.expression_source(klass, expression, when))
                entry["at"] = klass.at(first + offset)
                reads.append(entry)
        return reads

    def expression_source(self, klass, expression, when):
        """The source a getter's `<field>.<path>` expression reads, walked from the field's type."""
        match = re.match(r"^(\w+)((?:\.\w+)+)$", expression)
        if not match or match.group(1) not in klass.fields:
            return {"kind": "unresolved"}
        target = self.resolver.type_target(klass.fields[match.group(1)], klass)
        if target is None:
            return {"kind": "unresolved"}
        narrowed = [when] if when in self.capture.patterns else []
        source = self.walk(target, match.group(2)[1:].split("."), False, narrowed, expression, getters=False)[2]
        return {"kind": "gosuProperty"} if source["kind"] == "gosuProperty" else source

    def build_properties(self):
        for key, obj in self.objects.items():
            properties = []
            for path in sorted(obj["uses"]):
                use = obj["uses"][path]
                type_name, type_from, source = self.resolve(obj, path, use)
                export_types = sorted(use["exportTypes"])
                if type_name is None:
                    if len(export_types) == 1:
                        type_name = export_types[0]
                        type_from = {"kind": "export", "at": use["exportTypes"][type_name]}
                    else:
                        self.note("property:%s.%s" % (key, path), type_from, use["at"])
                        type_from = None
                properties.append({
                    "path": path, "type": type_name, "typeFrom": type_from, "exportTypes": export_types,
                    "modifier": use["modifier"], "source": public_source(source),
                    "readBy": sorted(use["readBy"]), "writtenBy": sorted(use["writtenBy"])})
            obj["properties"] = properties


    # -- document ----------------------------------------------------------------------------

    def ordered_objects(self):
        children = {}
        for key in sorted(self.objects):
            children.setdefault(self.objects[key]["parent"], []).append(key)
        order = []
        for start in [self.root] + [k for k in sorted(self.objects) if self.objects[k]["parent"] is None and k != self.root]:
            stack = [start]
            while stack:
                current = stack.pop()
                order.append(current)
                stack.extend(sorted(children.get(current, []), reverse=True))
        return order

    def document(self):
        objects = []
        for key in self.ordered_objects():
            obj = self.objects[key]
            objects.append({
                "key": key, "kind": obj["kind"], "className": obj["className"], "entity": obj["entity"],
                "subtype": obj["subtype"], "parent": obj["parent"], "parentVia": obj["parentVia"],
                "parameters": obj["parameters"], "properties": obj["properties"]})
        jar = None
        if self.checkout.jar_path and self.checkout.jar_used:
            jar = {"path": self.checkout.rel(self.checkout.jar_path), "sha256": sha256_file(self.checkout.jar_path)}
        ratebooks = []
        for book in self.books:
            source = book["book"].get("source", {})
            ratebooks.append({"folder": book["folder"], "book": book["code"],
                              "edition": book["book"]["book"].get("edition"),
                              "export": source.get("file", ""), "sha256": source.get("sha256", "")})
        unresolved = [self.unresolved[s] for s in sorted(self.unresolved)]
        return {
            "formatVersion": FORMAT,
            "product": self.product,
            "line": self.line,
            "source": {"revision": self.revision, "dirty": self.dirty, "configurationRoot": CONFIGURATION_ROOT},
            "inputs": {"productCapture": {"path": CAPTURE_PATH,
                                          "revision": self.capture.data.get("source", {}).get("revision")},
                       "ratebooks": ratebooks, "platformJar": jar},
            "root": self.root,
            "counts": {"objects": len(objects), "properties": sum(len(o["properties"]) for o in objects),
                       "scalars": len(self.scalars), "unresolved": len(unresolved)},
            "objects": objects,
            "scalars": [dict(scalar, source={"kind": "scalar"}) for scalar in self.scalars],
            "unresolved": unresolved,
        }

    def run(self):
        self.load_inputs()
        self.build_objects()
        self.nest()
        self.collect_uses()
        self.build_properties()
        return self.document()


# ----------------------------------------------------------------------------------------------
# Summary: what the human gate reads
# ----------------------------------------------------------------------------------------------


def summary(doc, target):
    counts = doc["counts"]
    lines = ["rating-workspace-extract: %s %s -> %s" % (doc["product"], doc["line"], target),
             "  %d object(s), %d propert(ies), %d scalar(s), %d unresolved"
             % (counts["objects"], counts["properties"], counts["scalars"], counts["unresolved"]),
             "Unresolved:"]
    lines += ["  - %s: %s (%s)" % (u["subject"], u["reason"], u["at"][0] if u["at"] else "no locator")
              for u in doc["unresolved"]] or ["  none"]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------


def write_atomic(path, text):
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp, str(path))
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Extract one PC line's rating workspace.")
    parser.add_argument("--product-root", required=True,
                        help="product root holding extracted/product-capture.json and extracted/ratebooks/")
    parser.add_argument("--source-root", required=True, help="PC checkout at the capture's revision")
    parser.add_argument("--out", default=None, help="output directory (default <product-root>/extracted)")
    parser.add_argument("--no-platform-jar", action="store_true",
                        help="do not read admin/lib/pc-<version>.jar even when it is present")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing rating-workspace.json")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out = Path(args.out) if args.out else Path(args.product_root) / "extracted"
    target = out / "rating-workspace.json"
    try:
        inside = out.resolve()
        for name, tree in (("the checkout", Path(args.source_root)),
                           ("the ratebook folders", Path(args.product_root) / RATEBOOKS_PATH)):
            tree = tree.resolve()
            if inside == tree or tree in inside.parents:
                refuse("output directory %s is inside %s %s, which is read-only" % (out, name, tree))
        if target.exists() and not args.overwrite:
            refuse("%s exists; pass --overwrite to replace it" % target)
        doc = Extraction(args).run()
    except Refusal as error:
        sys.stderr.write(json.dumps({"status": "refused", "error": str(error)}) + "\n")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(target, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.write(summary(doc, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
