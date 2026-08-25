#!/usr/bin/env python3
"""Fail CI when runtime DDL or legacy ensure debt changes outside inventory."""

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path


DDL_PATTERN = re.compile(
    r"\b(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)|"
    r"ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|TRIGGER)|"
    r"RENAME\s+TABLE|PRAGMA\s+user_version)\b",
    re.IGNORECASE,
)
ENSURE_PATTERN = re.compile(r"^_?ensure_")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_segment(source, node):
    if hasattr(node, "end_lineno") and node.end_lineno is not None:
        lines = source.splitlines(True)
        start = node.lineno - 1
        end = node.end_lineno - 1
        if start == end:
            return lines[start][node.col_offset:node.end_col_offset]
        return "".join(
            [lines[start][node.col_offset:]]
            + lines[start + 1:end]
            + [lines[end][:node.end_col_offset]]
        )
    lines = source.splitlines(True)
    start = node.lineno - 1
    indentation = node.col_offset
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].lstrip()
        if not stripped.strip():
            continue
        current = len(lines[index]) - len(stripped)
        if current <= indentation:
            end = index
            break
    return "".join(lines[start:end]).rstrip("\r\n")


def functions(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    result = []

    def visit(nodes, prefix=()):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                visit(node.body, prefix + (node.name,))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = ".".join(prefix + (node.name,))
                result.append((symbol, node, source_segment(source, node)))
                visit(node.body, prefix + (node.name,))

    visit(tree.body)
    return source, tree, result


def assignment(path, name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return source_segment(source, node)
    raise LookupError("{}:{} not found".format(path, name))


def ddl_containers(path):
    source, tree, discovered_functions = functions(path)
    result = set()
    for symbol, unused_node, segment in discovered_functions:
        if DDL_PATTERN.search(segment) or ".executescript(" in segment:
            result.add(symbol)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            segment = source_segment(source, node)
            if DDL_PATTERN.search(segment):
                names = [
                    target.id for target in node.targets
                    if isinstance(target, ast.Name)
                ]
                result.update(names)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inventory", default="docs/runtime-ddl-inventory.json"
    )
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads(
        (root / arguments.inventory).read_text(encoding="utf-8")
    )
    errors = []

    expected_ensures = {
        (item["file"], item["symbol"]): item
        for item in inventory["ensure_functions"]
    }
    current_ensures = {}
    function_index = {}
    for path in sorted((root / "app").rglob("*.py")):
        relative = str(path.relative_to(root))
        unused_source, unused_tree, definitions = functions(path)
        function_index[relative] = {
            symbol: (node, segment) for symbol, node, segment in definitions
        }
        for symbol, unused_node, segment in definitions:
            if ENSURE_PATTERN.match(symbol.rsplit(".", 1)[-1]):
                current_ensures[(relative, symbol)] = digest(segment)

    if set(current_ensures) != set(expected_ensures):
        errors.append(
            "ensure function inventory changed: added={} removed={}".format(
                sorted(set(current_ensures) - set(expected_ensures)),
                sorted(set(expected_ensures) - set(current_ensures)),
            )
        )
    for key in sorted(set(current_ensures) & set(expected_ensures)):
        if current_ensures[key] != expected_ensures[key]["sha256"]:
            errors.append(
                "ensure checksum changed: {}:{}".format(key[0], key[1])
            )

    expected_containers = {
        (item["file"], item["symbol"]): item
        for item in inventory["runtime_containers"]
    }
    controlled_migration_files = {
        item["file"]: item for item in inventory.get("migration_modules", [])
    }
    for relative, item in sorted(controlled_migration_files.items()):
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != item["sha256"]:
            errors.append("migration module checksum changed: {}".format(relative))
    for key, item in sorted(expected_containers.items()):
        path = root / item["file"]
        try:
            if item["node_type"] == "assignment":
                segment = assignment(path, item["symbol"])
            else:
                segment = function_index[item["file"]][item["symbol"]][1]
        except (KeyError, LookupError) as error:
            errors.append("missing tracked runtime container {}: {}".format(key, error))
            continue
        if digest(segment) != item["sha256"]:
            errors.append(
                "runtime container checksum changed: {}:{}".format(*key)
            )

    allowed_runtime = set(expected_containers) | set(expected_ensures)
    for path in sorted((root / "app").rglob("*.py")):
        relative = str(path.relative_to(root))
        if relative in controlled_migration_files:
            continue
        for symbol in sorted(ddl_containers(path)):
            if (relative, symbol) not in allowed_runtime:
                errors.append(
                    "new runtime DDL container: {}:{}".format(relative, symbol)
                )

    for relative, definitions in sorted(function_index.items()):
        if relative in controlled_migration_files:
            continue
        for symbol, (node, unused_segment) in definitions.items():
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                called = child.func
                name = (
                    called.id if isinstance(called, ast.Name)
                    else called.attr if isinstance(called, ast.Attribute)
                    else ""
                )
                if name == "apply_migrations":
                    errors.append(
                        "migration runner called from runtime: {}:{}".format(
                            relative, symbol
                        )
                    )

    for item in inventory["legacy_scripts"]:
        path = root / item["file"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            errors.append("legacy migration checksum changed: {}".format(item["file"]))

    report = {
        "ok": not errors,
        "errors": errors,
        "tracked_runtime_containers": len(expected_containers),
        "tracked_ensure_functions": len(expected_ensures),
        "tracked_legacy_scripts": len(inventory["legacy_scripts"]),
        "tracked_migration_modules": len(controlled_migration_files),
    }
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif errors:
        for error in errors:
            print("RUNTIME_DDL_GATE_ERROR: {}".format(error), file=sys.stderr)
    else:
        print(
            "Runtime DDL gate passed: {} containers, {} ensure functions, "
            "{} legacy scripts".format(
                report["tracked_runtime_containers"],
                report["tracked_ensure_functions"],
                report["tracked_legacy_scripts"],
            )
        )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
