"""Guard service boundaries and module cycles, including deferred imports."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project_imports():
    paths = [ROOT / "main.py"]
    paths += [p for package in ("handlers", "utils", "webapp") for p in (ROOT / package).rglob("*.py")]
    modules = {}
    for path in paths:
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        modules[".".join(parts)] = (path, is_package)
    graph, imports = {}, {}
    for name, (path, is_package) in modules.items():
        dependencies = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    package = name.split(".") if is_package else name.split(".")[:-1]
                    package = package[: len(package) - node.level + 1]
                    base = ".".join(package + ([base] if base else []))
                dependencies.add(base)
                dependencies.update(base + "." + alias.name for alias in node.names)
        imports[name] = dependencies
        graph[name] = (dependencies & modules.keys()) - {name}
    return graph, imports


class ArchitectureTests(unittest.TestCase):
    def test_runtime_modules_have_no_import_cycles(self):
        graph, _ = project_imports()
        visited, active = set(), []

        def visit(name):
            self.assertNotIn(name, active, "Import cycle: " + " -> ".join([*active, name]))
            if name in visited:
                return
            active.append(name)
            for dependency in graph[name]:
                visit(dependency)
            active.pop()
            visited.add(name)

        for name in graph:
            visit(name)

    def test_document_and_qa_services_do_not_depend_on_transports(self):
        graph, imports = project_imports()
        pending, visited = ["utils.ocr", "utils.question_answering"], set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            forbidden = {
                i for i in imports[name] if i.split(".")[0] in {"telebot", "flask", "webapp", "handlers"}
            }
            self.assertFalse(forbidden, f"{name} imports a transport: {forbidden}")
            pending.extend(graph[name] - visited)
