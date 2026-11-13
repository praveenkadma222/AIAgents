from __future__ import annotations

import ast
import hashlib
import os
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pathspec

from .models import Edge, IndexedFile, IndexResult, Symbol
from .tree_sitter_analyzer import EXTENSION_LANGUAGES, build_tree_sitter_index

IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", "site-packages", "dist", "build", "bin", "obj",
    "target", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".codegraph", ".idea", ".gradle",
}
SOURCE_EXTENSIONS = {".py", *EXTENSION_LANGUAGES}
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _module_name(relative_path: str) -> str:
    path = Path(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        text = arg.arg
        if arg.annotation:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        parts.append(text)
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        text = arg.arg
        if arg.annotation:
            text += f": {ast.unparse(arg.annotation)}"
        if default is not None:
            text += f" = {ast.unparse(default)}"
        parts.append(text)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({', '.join(parts)}){returns}"


def _iter_source_files(root: Path) -> tuple[list[Path], pathspec.PathSpec | None]:
    gitignore = root / ".gitignore"
    spec = None
    if gitignore.is_file():
        spec = pathspec.PathSpec.from_lines("gitignore", gitignore.read_text(encoding="utf-8").splitlines())
    result: list[Path] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root).as_posix()
        directories[:] = [
            name for name in directories
            if name not in IGNORED_DIRECTORIES
            and not (spec and spec.match_file(f"{relative_dir}/{name}/" if relative_dir != "." else f"{name}/"))
            and not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            if Path(filename).suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if spec and spec.match_file(relative):
                continue
            result.append(path)
    result.sort()
    return result, spec


@dataclass
class _FileInfo:
    path: str
    module: str
    tree: ast.Module
    module_id: str
    imports: dict[str, tuple[str, str | None]]
    is_package: bool


class _ReferenceCollector(ast.NodeVisitor):
    def __init__(
        self,
        info: _FileInfo,
        symbols: list[Symbol],
        symbols_by_file_name: dict[tuple[str, str], list[Symbol]],
        symbols_by_module: dict[str, list[Symbol]],
        module_ids: dict[str, str],
    ) -> None:
        self.info = info
        self.symbols = symbols
        self.symbols_by_file_name = symbols_by_file_name
        self.symbols_by_module = symbols_by_module
        self.module_ids = module_ids
        self.symbols_by_id = {symbol.id: symbol for symbol in symbols}
        self.symbols_by_name: dict[str, list[Symbol]] = {}
        self.symbols_by_location: dict[tuple[int, str, str], list[Symbol]] = {}
        for symbol in symbols:
            self.symbols_by_name.setdefault(symbol.name, []).append(symbol)
            self.symbols_by_location.setdefault(
                (symbol.line, symbol.name, symbol.kind), []
            ).append(symbol)
        self.edges: list[Edge] = []
        self.scope_id = info.module_id
        self.class_name: str | None = None

    def _resolve(self, expression: str) -> str | None:
        if not expression:
            return None
        if expression in self.info.imports:
            module, imported_name = self.info.imports[expression]
            if imported_name:
                match = self._module_symbol(module, imported_name)
                if match:
                    return match.id
            return self.module_ids.get(module)
        if expression.startswith(("self.", "cls.")) and self.class_name:
            method_name = expression.rsplit(".", 1)[-1]
            class_symbol = self.symbols_by_id.get(f"{self.info.path}::{self.class_name}")
            if class_symbol:
                method = next(
                    (s for s in self.symbols_by_name.get(method_name, [])
                     if s.parent_id == class_symbol.id),
                    None,
                )
                if method:
                    return method.id
        root, _, suffix = expression.partition(".")
        imported = self.info.imports.get(root)
        if imported:
            module, imported_name = imported
            if imported_name:
                base_symbol = self._module_symbol(module, imported_name)
                if base_symbol and not suffix:
                    return base_symbol.id
                module = f"{module}.{imported_name}" if imported_name else module
            if suffix:
                full_name = f"{module}.{suffix}"
                for split in range(len(full_name.split(".")) - 1, 0, -1):
                    components = full_name.split(".")
                    imported_module = ".".join(components[:split])
                    imported_symbol = ".".join(components[split:])
                    match = self._module_symbol(imported_module, imported_symbol)
                    if match:
                        return match.id
                    module_id = self.module_ids.get(imported_module)
                    if module_id and not imported_symbol:
                        return module_id
        local = self.symbols_by_file_name.get((self.info.path, expression), [])
        if not local and "." in expression:
            local = self.symbols_by_file_name.get((self.info.path, expression.rsplit(".", 1)[-1]), [])
        if self.class_name and expression.startswith(("self.", "cls.")):
            local = [
                s for s in local
                if s.qualified_name.startswith(self.class_name + ".")
            ]
        if len(local) == 1:
            return local[0].id
        global_matches = [
            symbol for symbol in self.symbols_by_name.get(expression.rsplit(".", 1)[-1], [])
            if symbol.kind != "module"
        ]
        return global_matches[0].id if len(global_matches) == 1 else None

    def _module_symbol(self, module: str, name: str) -> Symbol | None:
        for symbol in self.symbols_by_module.get(module, []):
            if symbol.qualified_name == name or symbol.name == name and "." not in symbol.qualified_name:
                return symbol
        return None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node)

    def _visit_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        candidates = self.symbols_by_location.get((node.lineno, node.name, "function"), [])
        symbol = next((s for s in candidates if s.file_path == self.info.path), None)
        previous_scope, previous_class = self.scope_id, self.class_name
        if symbol:
            self.scope_id = symbol.id
            self.class_name = symbol.class_name
        for statement in node.body:
            self.visit(statement)
        self.scope_id, self.class_name = previous_scope, previous_class

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases, *(k.value for k in node.keywords)]:
            self.visit(expression)
        candidates = self.symbols_by_location.get((node.lineno, node.name, "class"), [])
        symbol = next((s for s in candidates if s.file_path == self.info.path), None)
        previous_scope, previous_class = self.scope_id, self.class_name
        if symbol:
            self.scope_id, self.class_name = symbol.id, symbol.qualified_name
        for statement in node.body:
            self.visit(statement)
        self.scope_id, self.class_name = previous_scope, previous_class

    def visit_Call(self, node: ast.Call) -> None:
        expression = _dotted_name(node.func) or ast.unparse(node.func)
        target = self._resolve(expression)
        if target:
            self.edges.append(Edge(self.scope_id, target, "call", node.lineno, expression))
        self._visit_nested_calls(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def _visit_nested_calls(self, node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            self.visit(node)
            return
        for child in ast.iter_child_nodes(node):
            self._visit_nested_calls(child)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            target = self._resolve(node.id)
            if target:
                self.edges.append(Edge(self.scope_id, target, "reference", node.lineno, node.id))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        expression = _dotted_name(node)
        if expression:
            target = self._resolve(expression)
            if target:
                self.edges.append(Edge(self.scope_id, target, "reference", node.lineno, expression))
                return
        self.visit(node.value)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            imported_module = alias.name
            target = self.module_ids.get(imported_module)
            if target:
                self.edges.append(Edge(self.scope_id, target, "import", node.lineno, alias.name))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolve_import_module(
            self.info.module, node.module or "", node.level, self.info.is_package
        )
        for alias in node.names:
            symbol = self._module_symbol(module, alias.name)
            target = (
                symbol.id if symbol else
                self.module_ids.get(f"{module}.{alias.name}") or self.module_ids.get(module)
            )
            if target:
                self.edges.append(Edge(self.scope_id, target, "import", node.lineno, f"{module}.{alias.name}"))


def _resolve_import_module(
    current_module: str,
    imported_module: str,
    level: int,
    is_package: bool = False,
) -> str:
    if level == 0:
        return imported_module
    package_parts = current_module.split(".") if is_package else current_module.split(".")[:-1]
    remove = max(level - 1, 0)
    if remove:
        package_parts = package_parts[:-remove]
    if imported_module:
        package_parts.extend(imported_module.split("."))
    return ".".join(package_parts)


class _SymbolCollector(ast.NodeVisitor):
    def __init__(self, path: str, source: str, module_id: str) -> None:
        self.path = path
        self.source = source
        self.module_id = module_id
        self.symbols = [
            Symbol(module_id, Path(path).stem, _module_name(path), "module", path, 1,
                   max(1, source.count("\n") + 1), "", "", "", None, None)
        ]
        self.scope_names: list[str] = []
        self.parent_ids: list[str] = [module_id]
        self.class_names: list[str | None] = [None]
        self.scope_kinds: list[str] = ["module"]
        self.declared_variables: set[str] = set()

    def _add_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, kind: str) -> None:
        qualified_name = ".".join([*self.scope_names, node.name])
        symbol_id = f"{self.path}::{qualified_name}"
        segment = ast.get_source_segment(self.source, node) or ""
        docstring = ast.get_docstring(node) or ""
        signature = _signature(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ""
        class_name = (
            self.class_names[-1] if kind == "function" else
            qualified_name if kind == "class" else None
        )
        self.symbols.append(Symbol(
            symbol_id, node.name, qualified_name, kind, self.path,
            node.lineno, getattr(node, "end_lineno", node.lineno), signature,
            docstring, segment[:12000], self.parent_ids[-1], class_name,
        ))
        self.scope_names.append(node.name)
        self.parent_ids.append(symbol_id)
        self.class_names.append(qualified_name if kind == "class" else self.class_names[-1])
        self.scope_kinds.append(kind)
        for statement in node.body:
            self.visit(statement)
        self.scope_kinds.pop()
        self.class_names.pop()
        self.parent_ids.pop()
        self.scope_names.pop()

    def _add_variable(
        self,
        target: ast.expr,
        statement: ast.AST,
        annotation: ast.expr | None = None,
    ) -> None:
        owner = ".".join(self.scope_names)
        parent_id = self.parent_ids[-1]
        class_name = self.class_names[-1]
        names: list[tuple[str, str | None]] = []

        def collect_names(expression: ast.expr) -> None:
            if isinstance(expression, ast.Name):
                names.append((expression.id, None))
            elif isinstance(expression, (ast.Tuple, ast.List)):
                for element in expression.elts:
                    collect_names(element)
            elif (
                isinstance(expression, ast.Attribute)
                and isinstance(expression.value, ast.Name)
                and expression.value.id in {"self", "cls"}
                and class_name
            ):
                names.append((expression.attr, class_name))

        collect_names(target)
        if not names:
            return
        if self.scope_kinds[-1] == "function":
            names = [(name, class_name) for name, owner_class in names if owner_class]
        elif self.scope_kinds[-1] not in {"module", "class"}:
            return

        source = ast.get_source_segment(self.source, statement) or ""
        annotation_text = f": {ast.unparse(annotation)}" if annotation else ""
        for name, owner_class in names:
            variable_owner = owner_class if owner_class else owner
            variable_parent = (
                f"{self.path}::{owner_class}" if owner_class else parent_id
            )
            qualified_name = f"{variable_owner}.{name}" if variable_owner else name
            symbol_id = f"{self.path}::{qualified_name}"
            if symbol_id in self.declared_variables:
                continue
            self.declared_variables.add(symbol_id)
            self.symbols.append(Symbol(
                symbol_id, name, qualified_name, "variable", self.path,
                statement.lineno, getattr(statement, "end_lineno", statement.lineno),
                f"{name}{annotation_text}", "", source[:12000],
                variable_parent, owner_class,
            ))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._add_variable(target, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._add_variable(node.target, node, node.annotation)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_definition(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_definition(node, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_definition(node, "class")


def _imports_for(
    tree: ast.Module, module: str, is_package: bool = False
) -> dict[str, tuple[str, str | None]]:
    imports: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0],
                    None,
                )
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import_module(module, node.module or "", node.level, is_package)
            for alias in node.names:
                imports[alias.asname or alias.name] = (base, alias.name)
    return imports


def build_index(project_root: str | Path) -> IndexResult:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    all_files, _ = _iter_source_files(root)
    python_files = [path for path in all_files if path.suffix.lower() == ".py"]
    other_files = [path for path in all_files if path.suffix.lower() != ".py"]
    diagnostics: list[dict[str, object]] = []
    indexed_files: list[IndexedFile] = []
    parsed: list[tuple[str, str, ast.Module, str]] = []
    for path in python_files:
        relative = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
            if stat.st_size > MAX_SOURCE_BYTES:
                indexed_files.append(IndexedFile(relative, stat.st_mtime_ns, stat.st_size, "skipped", "File exceeds 2 MiB limit"))
                diagnostics.append({"file": relative, "error": "File exceeds 2 MiB limit"})
                continue
            with tokenize.open(path) as source_file:
                source = source_file.read()
            tree = ast.parse(source, filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            try:
                stat = path.stat()
                indexed_files.append(IndexedFile(relative, stat.st_mtime_ns, stat.st_size, "error", str(exc)))
            except OSError:
                indexed_files.append(IndexedFile(relative, 0, 0, "error", str(exc)))
            diagnostics.append({"file": relative, "error": str(exc)})
            continue
        stat = path.stat()
        indexed_files.append(IndexedFile(relative, stat.st_mtime_ns, stat.st_size, "indexed"))
        parsed.append((relative, source, tree, _module_name(relative)))

    symbols: list[Symbol] = []
    files_info: list[_FileInfo] = []
    for relative, source, tree, module in parsed:
        module_id = f"{relative}::<module>"
        collector = _SymbolCollector(relative, source, module_id)
        for statement in tree.body:
            collector.visit(statement)
        symbols.extend(collector.symbols)
        is_package = Path(relative).name == "__init__.py"
        files_info.append(_FileInfo(
            relative, module, tree, module_id,
            _imports_for(tree, module, is_package), is_package,
        ))

    module_ids: dict[str, str] = {}
    file_to_module = {info.path: info.module for info in files_info}
    for symbol in symbols:
        if symbol.kind == "module":
            module_ids[symbol.qualified_name] = symbol.id
    by_file_name: dict[tuple[str, str], list[Symbol]] = {}
    by_module: dict[str, list[Symbol]] = {}
    for symbol in symbols:
        by_file_name.setdefault((symbol.file_path, symbol.name), []).append(symbol)
        module = file_to_module.get(symbol.file_path, "")
        by_module.setdefault(module, []).append(symbol)

    edges: list[Edge] = []
    for info in files_info:
        collector = _ReferenceCollector(info, symbols, by_file_name, by_module, module_ids)
        collector.visit(info.tree)
        edges.extend(collector.edges)
        for node in ast.walk(info.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_name = alias.asname or alias.name.split(".")[0]
                    info.imports[imported_name] = (
                        alias.name if alias.asname else alias.name.split(".")[0], None
                    )

    other_symbols, other_edges, other_files_indexed, other_diagnostics = (
        build_tree_sitter_index(root, other_files)
    )
    languages = sorted({
        file.language for file in [*indexed_files, *other_files_indexed]
        if file.status == "indexed"
    })
    return IndexResult(
        [*symbols, *other_symbols],
        list(dict.fromkeys([*edges, *other_edges])),
        [*indexed_files, *other_files_indexed],
        [*diagnostics, *other_diagnostics],
        languages,
    )


def document_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def symbols_to_dict(symbols: Iterable[Symbol]) -> list[dict[str, object]]:
    return [symbol.as_dict() for symbol in symbols]
