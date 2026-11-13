from __future__ import annotations

import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from codegraph.analyzer import build_index
from codegraph.database import CodeGraphDB
from codegraph.semantic import search as semantic_search_index

mcp = FastMCP("codebase-impact-graph")
DATABASE_NAME = ".codegraph/index.sqlite3"


def _root(project_path: str | None = None) -> Path:
    configured = project_path or os.environ.get("CODEGRAPH_PROJECT_ROOT")
    if not configured:
        raise ValueError(
            "Set CODEGRAPH_PROJECT_ROOT in the MCP server environment or pass project_path."
        )
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project path is not a directory: {root}")
    return root


def _db(project_path: str | None = None) -> CodeGraphDB:
    root = _root(project_path)
    path = root / DATABASE_NAME
    if not path.is_file():
        raise RuntimeError(f"No code graph found for {root}. Run index_project first.")
    db = CodeGraphDB(path)
    stored_root = db.metadata().get("project_root")
    if stored_root != str(root):
        db.close()
        raise RuntimeError(f"The code graph at {path} belongs to a different project.")
    return db


def _resolve_symbol(db: CodeGraphDB, query: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    symbols = db.symbols()
    exact = [symbol for symbol in symbols if symbol["id"] == query or symbol["qualified_name"] == query]
    if not exact:
        exact = [symbol for symbol in symbols if symbol["name"] == query]
    if len(exact) == 1:
        return exact[0], []
    if exact:
        return None, exact[:30]
    partial = [
        symbol for symbol in symbols if symbol["kind"] != "module"
        and (query.lower() in symbol["qualified_name"].lower()
             or query.lower() in symbol["name"].lower())
    ]
    return None, partial[:30]


def _source_reference(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "relationship": edge["kind"],
        "symbol": edge["source_qualified_name"],
        "symbol_id": edge["source_id"],
        "symbol_kind": edge["source_kind"],
        "language": edge["source_language"],
        "file": edge["source_file_path"],
        "definition_line": edge["source_definition_line"],
        "line": edge["line"],
        "expression": edge["expression"],
        "signature": edge["source_signature"],
        "docstring": edge["source_docstring"],
    }


def _format_impact_report(
    target: dict[str, Any],
    direct_dependents: list[dict[str, Any]],
    transitive_callers: list[dict[str, Any]],
    risk: str,
    limitations: list[str],
) -> str:
    language = target["language"]
    start = target["line"]
    end = target["end_line"]
    location = f"{target['file_path']}:{start}"
    if end > start:
        location += f"-{end}"
    lines = [
        f"## `{target['qualified_name']}` ({target['kind']}, {language})",
        f"**Definition:** `{location}`",
    ]
    if target["signature"]:
        lines.append(f"**Signature:** `{target['signature']}`")
    if target["source"]:
        lines.extend(["", f"```{language}", target["source"], "```"])

    what_it_does = target["docstring"].strip() or (
        "No docstring is available; see the implementation above."
    )
    lines.extend(["", "**What it does:**", what_it_does])
    lines.extend(["", "**What would be affected if you change it:**"])
    if not direct_dependents and not transitive_callers:
        lines.append("- No statically resolved dependents were found.")
    for dependent in direct_dependents:
        site = f"{dependent['file']}:{dependent['line']}"
        lines.append(
            f"- Direct {dependent['relationship']}: `{dependent['symbol']}` "
            f"({dependent['language']}) at `{site}`."
        )
    for caller in transitive_callers:
        site = f"{caller['file']}:{caller['definition_line']}"
        call_lines = ", ".join(str(line) for line in caller["call_site_lines"])
        detail = f"; calls at line(s) {call_lines}" if call_lines else ""
        lines.append(
            f"- Indirect caller: `{caller['symbol']}` ({caller['language']}) "
            f"defined at `{site}`{detail} "
            f"(call distance {caller['call_distance']})."
        )

    lines.extend(["", "**Important nuance:**"])
    lines.extend(f"- {item}" for item in limitations)
    lines.extend([
        "",
        f"**If you change the symbol itself, the main risk is:** {risk}",
    ])
    return "\n".join(lines)


@mcp.tool()
def index_project(project_path: str | None = None) -> dict[str, Any]:
    """Scan Python, C#, TypeScript, JavaScript, and Java source files in a project folder."""
    root = _root(project_path)
    index = build_index(root)
    db = CodeGraphDB(root / DATABASE_NAME)
    try:
        db.replace_index(
            str(root), index.symbols, index.edges, index.files, index.diagnostics,
            index.languages,
        )
        result = db.stats()
        result["database"] = str(root / DATABASE_NAME)
        result["relationship_types"] = {
            "call": sum(edge.kind == "call" for edge in index.edges),
            "reference": sum(edge.kind == "reference" for edge in index.edges),
            "import": sum(edge.kind == "import" for edge in index.edges),
        }
        return result
    finally:
        db.close()


@mcp.tool()
def get_index_status(project_path: str | None = None) -> dict[str, Any]:
    """Show index counts, timestamp, and parse diagnostics for the selected project."""
    db = _db(project_path)
    try:
        return db.stats()
    finally:
        db.close()


@mcp.tool()
def search_symbols(query: str, limit: int = 10, project_path: str | None = None) -> dict[str, Any]:
    """Find functions, classes, and indexed variables by name, signature, or docstring."""
    db = _db(project_path)
    try:
        results = db.search_symbols(query, max(1, min(int(limit), 50)))
        return {"query": query, "count": len(results), "results": results}
    finally:
        db.close()


@mcp.tool()
def find_references(symbol: str, project_path: str | None = None) -> dict[str, Any]:
    """Find direct callers, imports, and references to an indexed function, class, or variable."""
    db = _db(project_path)
    try:
        target, matches = _resolve_symbol(db, symbol)
        if not target:
            return {
                "error": "Symbol was not uniquely identified. Use a qualified name or symbol ID.",
                "matches": [item["id"] for item in matches],
            }
        references = [_source_reference(edge) for edge in db.incoming_edges(target["id"])]
        return {"symbol": target, "count": len(references), "references": references}
    finally:
        db.close()


@mcp.tool()
def analyze_impact(
    symbol: str,
    change_type: str = "edit",
    project_path: str | None = None,
) -> dict[str, Any]:
    """Explain direct and transitive impact if an indexed symbol is edited or removed."""
    if change_type not in {"edit", "remove"}:
        raise ValueError("change_type must be 'edit' or 'remove'.")
    db = _db(project_path)
    try:
        target, matches = _resolve_symbol(db, symbol)
        if not target:
            return {
                "error": "Symbol was not uniquely identified. Use a qualified name or symbol ID.",
                "matches": [item["id"] for item in matches],
            }
        incoming = db.incoming_edges(target["id"])
        direct = [_source_reference(edge) for edge in incoming]
        reverse_callers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in db.edges():
            if edge["kind"] == "call":
                reverse_callers[edge["target_id"]].append(edge)
        distances: dict[str, int] = {}
        queue: deque[str] = deque()
        for edge in incoming:
            if edge["kind"] == "call" and edge["source_id"] != target["id"]:
                if edge["source_id"] not in distances:
                    distances[edge["source_id"]] = 1
                    queue.append(edge["source_id"])
        call_site_lines: dict[str, set[int]] = defaultdict(set)
        for edge in incoming:
            if edge["kind"] == "call":
                call_site_lines[edge["source_id"]].add(edge["line"])
        while queue:
            callee = queue.popleft()
            for edge in reverse_callers.get(callee, []):
                caller = edge["source_id"]
                call_site_lines[caller].add(edge["line"])
                if caller != target["id"] and caller not in distances:
                    distances[caller] = distances[callee] + 1
                    queue.append(caller)
        symbols = {item["id"]: item for item in db.symbols()}
        transitive = [
            {
                "symbol": symbols[symbol_id]["qualified_name"],
                "symbol_id": symbol_id,
                "kind": symbols[symbol_id]["kind"],
                "language": symbols[symbol_id]["language"],
                "file": symbols[symbol_id]["file_path"],
                "definition_line": symbols[symbol_id]["line"],
                "call_distance": distance,
                "call_site_lines": sorted(call_site_lines[symbol_id]),
            }
            for symbol_id, distance in sorted(distances.items(), key=lambda item: (item[1], item[0]))
            if symbol_id in symbols and distance > 1
        ]
        affected_files = sorted({
            edge["source_file_path"] for edge in incoming
        } | {
            symbols[item["symbol_id"]]["file_path"] for item in transitive
        })
        calls = sum(edge["kind"] == "call" for edge in incoming)
        imports = sum(edge["kind"] == "import" for edge in incoming)
        references = sum(edge["kind"] == "reference" for edge in incoming)
        if change_type == "remove":
            risk = (
                "High: removing this symbol breaks resolved callers, imports, or references."
                if incoming else "No statically resolved dependents were found; dynamic uses may still exist."
            )
        else:
            risk = (
                "Review all listed call sites and references. Signature or behavior changes can "
                "propagate through the transitive caller chain."
                if incoming else "No statically resolved dependents were found; dynamic uses may still exist."
            )
        limitations = [
            "Static analysis cannot guarantee resolution of dynamic imports, reflection, "
            "dependency injection, generated code, or runtime monkey-patching.",
            "Unresolved or ambiguous call targets are omitted; re-index after project changes.",
        ]
        formatted_response = _format_impact_report(
            target, direct, transitive, risk, limitations
        )
        return {
            "target": target,
            "change_type": change_type,
            "formatted_response": formatted_response,
            "summary": {
                "direct_call_sites": calls,
                "direct_imports": imports,
                "direct_references": references,
                "transitive_callers": len(transitive),
                "affected_file_count": len(affected_files),
                "affected_files": affected_files,
                "risk": risk,
            },
            "direct_dependents": direct,
            "transitive_callers": transitive,
            "limitations": limitations,
        }
    finally:
        db.close()


@mcp.tool()
def semantic_search(
    query: str,
    limit: int = 10,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Search indexed code by meaning using a local embedding model; code is not sent to an API."""
    db = _db(project_path)
    try:
        results = semantic_search_index(db, query, max(1, min(int(limit), 50)))
        return {"query": query, "model": os.environ.get(
            "CODEGRAPH_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"
        ), "count": len(results), "results": results}
    finally:
        db.close()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
