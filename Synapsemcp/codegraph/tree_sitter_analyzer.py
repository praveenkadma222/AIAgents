from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from .models import Edge, IndexedFile, Symbol

EXTENSION_LANGUAGES = {
    ".cs": "csharp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "tsx",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
}

CLASS_NODES = {
    "class_declaration", "class", "interface_declaration", "enum_declaration",
    "struct_declaration", "record_declaration",
}
FUNCTION_NODES = {
    "function_declaration", "generator_function_declaration", "method_definition",
    "method_declaration", "constructor_declaration", "local_function_statement",
}
CALL_NODES = {"call_expression", "method_invocation", "invocation_expression"}
IMPORT_NODES = {
    "import_statement", "import_declaration", "using_directive",
    "namespace_import_declaration",
}
MAX_SYMBOL_SOURCE = 12000


def _text(source: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _children(node: Node):
    return node.named_children


def _name_node(node: Node, source: bytes) -> str:
    for field in ("name", "declarator", "property"):
        child = node.child_by_field_name(field)
        if child is not None:
            if child.type in {"variable_declarator", "identifier"}:
                nested = child.child_by_field_name("name")
                return _text(source, nested or child)
            return _text(source, child)
    preferred = {
        "function_declaration": {"identifier"},
        "generator_function_declaration": {"identifier"},
        "class_declaration": {"identifier", "type_identifier"},
        "class": {"identifier"},
        "method_definition": {"property_identifier", "identifier"},
        "method_declaration": {"identifier"},
        "constructor_declaration": {"identifier"},
        "local_function_statement": {"identifier"},
        "interface_declaration": {"identifier", "type_identifier"},
        "enum_declaration": {"identifier", "type_identifier"},
        "struct_declaration": {"identifier", "type_identifier"},
        "record_declaration": {"identifier", "type_identifier"},
    }.get(node.type, set())
    for child in _children(node):
        if child.type in preferred:
            return _text(source, child)
    return ""


def _source_segment(source: bytes, node: Node) -> str:
    return _text(source, node)[:MAX_SYMBOL_SOURCE]


def _signature(node: Node, source: bytes, name: str) -> str:
    parameters = (
        node.child_by_field_name("parameters")
        or node.child_by_field_name("parameter_list")
    )
    if parameters:
        return f"{name}{_text(source, parameters)}"
    if node.type == "variable_declarator":
        value = node.child_by_field_name("value")
        if value and value.type in {"arrow_function", "function_expression"}:
            params = value.child_by_field_name("parameters")
            return f"{name}{_text(source, params)}" if params else name
    return name


def _module_name(relative_path: str) -> str:
    path = Path(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "index":
        parts.pop()
    return ".".join(parts)


def _namespace(source: bytes, language: str) -> str:
    text = source.decode("utf-8", errors="replace")
    pattern = r"^\s*package\s+([\w.]+)\s*;" if language == "java" else (
        r"^\s*namespace\s+([\w.]+)\s*[;{]" if language == "csharp" else ""
    )
    match = re.search(pattern, text, re.MULTILINE) if pattern else None
    return match.group(1) if match else ""


def _walk(node: Node):
    yield node
    for child in _children(node):
        yield from _walk(child)


def _declaration_name(node: Node, source: bytes) -> str:
    name = _name_node(node, source)
    if name:
        return name.split(".")[-1]
    return ""


class _SymbolCollector:
    def __init__(
        self,
        path: str,
        language: str,
        source: bytes,
        tree: Node,
        namespace: str = "",
    ) -> None:
        self.path = path
        self.language = language
        self.source = source
        self.namespace = namespace
        self.module_id = f"{path}::<module>"
        self.symbols = [
            Symbol(self.module_id, Path(path).stem, _module_name(path), "module", path,
                   1, max(1, source.count(b"\n") + 1), "", "", "", None, None,
                   language)
        ]
        self.scopes: list[Symbol] = [self.symbols[0]]
        self.seen: set[str] = {self.module_id}
        self._visit(tree)

    def _add(
        self,
        node: Node,
        kind: str,
        name: str,
        *,
        signature: str = "",
        owner_class: str | None = None,
    ) -> Symbol | None:
        if not name:
            return None
        parent = self.scopes[-1]
        qualified = (
            f"{parent.qualified_name}.{name}" if parent.kind != "module" else
            f"{self.namespace}.{name}" if self.namespace else name
        )
        symbol_id = f"{self.path}::{qualified}"
        if kind == "variable":
            symbol_id += f"@{node.start_point.row + 1}"
        if symbol_id in self.seen:
            return None
        self.seen.add(symbol_id)
        symbol = Symbol(
            symbol_id, name, qualified, kind, self.path,
            node.start_point.row + 1, max(node.start_point.row + 1, node.end_point.row + 1),
            signature, "", _source_segment(self.source, node), parent.id,
            owner_class, self.language,
        )
        self.symbols.append(symbol)
        return symbol

    def _visit(self, node: Node) -> None:
        kind = "class" if node.type in CLASS_NODES else None
        if node.type in FUNCTION_NODES:
            kind = "function"
        function_value: Node | None = None
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value and value.type in {"arrow_function", "function_expression"}:
                kind = "function"
                function_value = value
            elif self.scopes[-1].kind in {"module", "class"}:
                kind = "variable"
        if kind:
            declaration = function_value or node
            name = _declaration_name(node, self.source) if function_value else _declaration_name(node, self.source)
            if node.type in CLASS_NODES or node.type in FUNCTION_NODES:
                name = _declaration_name(node, self.source)
            signature = _signature(declaration, self.source, name) if kind == "function" else ""
            parent_class = (
                self.scopes[-1].qualified_name
                if self.scopes[-1].kind == "class" else None
            )
            symbol = self._add(
                node, kind, name, signature=signature, owner_class=parent_class
            )
            if symbol and kind in {"class", "function"}:
                self.scopes.append(symbol)
                for child in _children(node):
                    self._visit(child)
                self.scopes.pop()
                return
        for child in _children(node):
            self._visit(child)


def _import_bindings(source: bytes, language: str) -> list[tuple[str, str, str | None]]:
    text = source.decode("utf-8", errors="replace")
    bindings: list[tuple[str, str, str | None]] = []
    if language in {"javascript", "typescript", "tsx"}:
        for match in re.finditer(
            r"""import\s+(?:type\s+)?(?P<clause>[\s\S]*?)\s+from\s+["'](?P<module>[^"']+)["']""",
            text,
        ):
            clause, module = match.group("clause").strip(), match.group("module")
            clause = clause.removeprefix("type ").strip()
            default_match = re.match(r"([A-Za-z_$][\w$]*)", clause)
            if default_match:
                bindings.append((default_match.group(1), module, "default"))
            namespace_match = re.search(r"\*\s+as\s+([\w$]+)", clause)
            if namespace_match:
                bindings.append((namespace_match.group(1), module, None))
            named = re.search(r"\{([^}]*)\}", clause)
            if named:
                for item in named.group(1).split(","):
                    parts = re.split(r"\s+as\s+", item.strip())
                    if parts and parts[0]:
                        bindings.append((parts[-1], module, parts[0]))
        for match in re.finditer(r"""import\s+["'](?P<module>[^"']+)["']""", text):
            bindings.append(("", match.group("module"), None))
    elif language == "java":
        for match in re.finditer(r"import\s+(?:static\s+)?([\w.$*]+)\s*;", text):
            target = match.group(1)
            bindings.append((target.rsplit(".", 1)[-1], target, None))
    elif language == "csharp":
        for match in re.finditer(r"using\s+(?:(\w+)\s*=\s*)?([\w.]+)\s*;", text):
            alias, namespace = match.groups()
            bindings.append((alias or namespace.rsplit(".", 1)[-1], namespace, None))
    return bindings


def _resolve_import_path(current_path: str, module: str, candidates: dict[str, str]) -> str | None:
    normalized = module.replace("\\", "/")
    if normalized.startswith("."):
        normalized = str(Path(current_path).parent.joinpath(normalized)).replace("\\", "/")
        normalized = str(Path(normalized)).replace("\\", "/")
    normalized = normalized.lstrip("./").removesuffix(".js").removesuffix(".jsx")
    normalized = normalized.removesuffix(".ts").removesuffix(".tsx")
    direct = candidates.get(normalized)
    if direct:
        return direct
    suffix_matches = [path for key, path in candidates.items() if key.endswith("/" + normalized) or key == normalized]
    return suffix_matches[0] if len(suffix_matches) == 1 else None


def _is_call(node: Node) -> bool:
    return node.type in CALL_NODES


def _call_expression(node: Node, source: bytes) -> str:
    if node.type in {"call_expression", "invocation_expression"}:
        return _text(source, node.child_by_field_name("function"))
    if node.type == "method_invocation":
        obj = node.child_by_field_name("object")
        name = node.child_by_field_name("name")
        return f"{_text(source, obj)}.{_text(source, name)}" if obj else _text(source, name)
    return ""


def _call_leaf(node: Node) -> Node | None:
    if node.type == "method_invocation":
        return node.child_by_field_name("name")
    target = node.child_by_field_name("function")
    if target is None:
        return node.child_by_field_name("name")
    while target.named_children:
        target = target.named_children[-1]
    return target


def _module_symbol(
    symbols_by_file: dict[str, list[Symbol]], path: str, name: str
) -> Symbol | None:
    matches = [
        symbol for symbol in symbols_by_file.get(path, [])
        if symbol.kind != "module"
        if symbol.name == name or symbol.qualified_name == name
    ]
    return matches[0] if len(matches) == 1 else None


def _owner_for_node(
    node: Node,
    declarations: dict[tuple[int, int], Symbol],
) -> Symbol | None:
    current: Node | None = node
    while current is not None:
        symbol = declarations.get((current.start_byte, current.end_byte))
        if symbol and symbol.kind in {"function", "class"}:
            return symbol
        current = current.parent
    return None


def _analyze_references(
    path: str,
    language: str,
    source: bytes,
    tree: Node,
    symbols: list[Symbol],
    symbols_by_file: dict[str, list[Symbol]],
    module_paths: dict[str, str],
    all_symbols: list[Symbol],
    namespace: str,
) -> list[Edge]:
    module = next(symbol for symbol in symbols if symbol.kind == "module")
    nodes = list(_walk(tree))
    symbols_by_location: dict[tuple[int, str, str], list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        symbols_by_location[(symbol.line, symbol.name, symbol.kind)].append(symbol)
    declarations: dict[tuple[int, int], Symbol] = {}
    bindings: dict[str, tuple[str | None, str | None]] = {}
    edges: list[Edge] = []
    for alias, import_name, imported_name in _import_bindings(source, language):
        target_path = _resolve_import_path(path, import_name, module_paths)
        bindings[alias] = (target_path, imported_name)
        if target_path:
            target = (
                _module_symbol(symbols_by_file, target_path, imported_name)
                if imported_name and imported_name != "default" else None
            )
            if target is None and language == "java":
                target = _module_symbol(symbols_by_file, target_path, alias)
            target = target or next(
                (symbol for symbol in symbols_by_file[target_path] if symbol.kind == "module"),
                None,
            )
            if target:
                lines = source.decode("utf-8", errors="replace").splitlines()
                import_line = next(
                    (number for number, line in enumerate(lines, start=1)
                     if import_name in line and re.search(r"\b(import|using)\b", line)),
                    1,
                )
                edges.append(Edge(module.id, target.id, "import", import_line, import_name))

    declaration_spans: set[tuple[int, int]] = set()
    for node in nodes:
        if node.type not in CLASS_NODES | FUNCTION_NODES | {"variable_declarator"}:
            continue
        name = _declaration_name(node, source)
        if not name:
            continue
        kind = "class" if node.type in CLASS_NODES else "function" if node.type in FUNCTION_NODES else "variable"
        candidates = symbols_by_location.get((node.start_point.row + 1, name, kind), [])
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value and value.type in {"arrow_function", "function_expression"}:
                candidates = symbols_by_location.get((node.start_point.row + 1, name, "function"), [])
        symbol = candidates[0] if candidates else None
        if symbol:
            declarations[(node.start_byte, node.end_byte)] = symbol
        name_node = node.child_by_field_name("name")
        if name_node:
            declaration_spans.add((name_node.start_byte, name_node.end_byte))
    import_spans = [
        (node.start_byte, node.end_byte)
        for node in nodes if node.type in IMPORT_NODES
    ]
    call_name_spans = [
        (child.start_byte, child.end_byte)
        for node in nodes if _is_call(node)
        for child in [_call_leaf(node)]
        if child is not None
    ]
    identifier_types = {
        "identifier", "type_identifier", "property_identifier", "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
    }
    for node in nodes:
        if node.type not in identifier_types:
            continue
        span = (node.start_byte, node.end_byte)
        if span in declaration_spans or any(start <= node.start_byte < end for start, end in import_spans):
            continue
        if any(start <= node.start_byte and node.end_byte <= end for start, end in call_name_spans):
            continue
        name = _text(source, node)
        owner = _owner_for_node(node, declarations) or module
        target: Symbol | None = None
        matching_binding = bindings.get(name)
        if matching_binding:
            target_path, imported_name = matching_binding
            if target_path and imported_name and imported_name != "default":
                target = _module_symbol(symbols_by_file, target_path, imported_name)
        if target is None:
            local = [
                symbol for symbol in symbols_by_file.get(path, [])
                if symbol.name == name and symbol.id != owner.id
                and symbol.kind in {"function", "class", "variable"}
            ]
            class_matches = [
                symbol for symbol in local
                if symbol.parent_id == owner.id
                or owner.class_name and symbol.class_name == owner.class_name
            ]
            candidates = class_matches or local
            if len(candidates) == 1:
                target = candidates[0]
        if target is None:
            global_matches = [
                symbol for symbol in all_symbols
                if symbol.language == language
                and symbol.name == name and symbol.kind in {"function", "class", "variable"}
            ]
            local_namespace = [
                symbol for symbol in global_matches
                if namespace and symbol.qualified_name.startswith(namespace + ".")
            ]
            candidates = local_namespace or global_matches
            if len(candidates) == 1:
                target = candidates[0]
        if target:
            edges.append(Edge(
                owner.id, target.id, "reference", node.start_point.row + 1, name
            ))

    for node in nodes:
        if not _is_call(node):
            continue
        expression = _call_expression(node, source)
        if not expression:
            continue
        parts = re.findall(r"[A-Za-z_$][\w$]*", expression)
        if not parts:
            continue
        target: Symbol | None = None
        binding = bindings.get(parts[0])
        if binding:
            target_path, imported_name = binding
            if target_path:
                if imported_name and imported_name not in {"default"}:
                    target = _module_symbol(symbols_by_file, target_path, imported_name)
                elif len(parts) > 1:
                    target = _module_symbol(symbols_by_file, target_path, parts[-1])
        if target is None:
            target = _module_symbol(symbols_by_file, path, parts[-1])
        if target is None:
            candidates = [
                symbol for symbol in all_symbols
                if symbol.language == language
                and symbol.kind in {"function", "class", "variable"}
                and symbol.name == parts[-1]
            ]
            in_namespace = [
                symbol for symbol in candidates
                if namespace and symbol.qualified_name.startswith(namespace + ".")
            ]
            unique = in_namespace or candidates
            if len(unique) == 1:
                target = unique[0]
        if target:
            owner = _owner_for_node(node, declarations) or module
            edges.append(Edge(
                owner.id, target.id, "call", node.start_point.row + 1, expression
            ))

    for node in _walk(tree):
        if node.type not in IMPORT_NODES:
            continue
        if node.start_point.row == 0:
            continue
        # Import sites are represented by module-level edges above.

    return edges


def build_tree_sitter_index(
    project_root: Path,
    paths: list[Path],
) -> tuple[list[Symbol], list[Edge], list[IndexedFile], list[dict[str, object]]]:
    parsed: list[tuple[str, str, bytes, Node]] = []
    files: list[IndexedFile] = []
    diagnostics: list[dict[str, object]] = []
    for path in paths:
        relative = path.relative_to(project_root).as_posix()
        language = EXTENSION_LANGUAGES[path.suffix.lower()]
        try:
            stat = path.stat()
            if stat.st_size > 2 * 1024 * 1024:
                files.append(IndexedFile(
                    relative, stat.st_mtime_ns, stat.st_size, "skipped",
                    "File exceeds 2 MiB limit", language,
                ))
                diagnostics.append({"file": relative, "error": "File exceeds 2 MiB limit"})
                continue
            source = path.read_bytes()
            parser = get_parser(language)
            tree = parser.parse(source)
        except (OSError, UnicodeError, LookupError, ValueError) as exc:
            try:
                stat = path.stat()
                files.append(IndexedFile(relative, stat.st_mtime_ns, stat.st_size, "error", str(exc), language))
            except OSError:
                files.append(IndexedFile(relative, 0, 0, "error", str(exc), language))
            diagnostics.append({"file": relative, "language": language, "error": str(exc)})
            continue
        stat = path.stat()
        if tree.root_node.has_error:
            diagnostics.append({
                "file": relative,
                "language": language,
                "error": "Parser reported syntax errors; partial symbols may still be indexed.",
            })
        files.append(IndexedFile(relative, stat.st_mtime_ns, stat.st_size, "indexed", "", language))
        parsed.append((relative, language, source, tree.root_node))

    symbols: list[Symbol] = []
    file_symbols: dict[str, list[Symbol]] = {}
    module_paths: dict[str, str] = {}
    namespaces: dict[str, str] = {}
    for relative, language, source, tree in parsed:
        namespace = _namespace(source, language)
        namespaces[relative] = namespace
        collected = _SymbolCollector(relative, language, source, tree, namespace).symbols
        symbols.extend(collected)
        file_symbols[relative] = collected
        module_paths[_module_name(relative)] = relative
        module_paths[str(Path(relative).with_suffix("")).replace("\\", "/")] = relative
        if namespace:
            module_paths.setdefault(namespace, relative)
            for symbol in collected:
                if symbol.kind == "class":
                    module_paths[f"{namespace}.{symbol.name}"] = relative
    for relative, _, _, _ in parsed:
        parent = Path(relative).parent.as_posix()
        module_paths.setdefault(parent, relative)
    edges: list[Edge] = []
    for relative, language, source, tree in parsed:
        edges.extend(_analyze_references(
            relative, language, source, tree, file_symbols[relative],
            file_symbols, module_paths, symbols, namespaces[relative],
        ))
    return symbols, list(dict.fromkeys(edges)), files, diagnostics
