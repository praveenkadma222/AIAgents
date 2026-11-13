"""Read-only MCP tools. Start: python -m server (from project directory)."""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from codegraph.analyzer import build_index
from codegraph.database import CodeGraphDB
from codegraph.impact import analyze_impact, find_symbols, format_report

mcp = FastMCP('Code Impact Analyzer')


def _root() -> Path:
    value = os.environ.get('CODEGRAPH_PROJECT_ROOT')
    if not value:
        raise ValueError('Set CODEGRAPH_PROJECT_ROOT to the absolute folder of the project to analyze')
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('CODEGRAPH_PROJECT_ROOT must be an existing directory')
    return root


def _db_path() -> Path:
    return _root() / '.codegraph' / 'impact.sqlite'


@mcp.tool()
def index_project() -> dict:
    """Recursively scan the configured project root and rebuild the code graph."""
    root = _root()
    result = build_index(root)
    db = CodeGraphDB(_db_path())
    try:
        db.replace_index(str(root), result.symbols, result.edges, result.files,
                         result.diagnostics, result.languages)
        return db.stats()
    finally:
        db.close()


@mcp.tool()
def find_symbol(query: str, limit: int = 30) -> list[dict]:
    """Find method, class, variable or condition declarations by name or ID."""
    db = CodeGraphDB(_db_path())
    try:
        return [{k: s[k] for k in ('id','name','qualified_name','kind','file_path','line','end_line','signature','language')}
                for s in find_symbols(db, query, min(max(1, limit), 100))]
    finally:
        db.close()


@mcp.tool()
def analyze_change_impact(symbol: str, change_type: str = 'remove',
                          file_path: str = '', max_depth: int = 5) -> dict:
    """Analyze possible effects of removing, modifying or renaming an indexed symbol."""
    db = CodeGraphDB(_db_path())
    try:
        return analyze_impact(db, symbol, change_type, file_path or None, max_depth)
    finally:
        db.close()


@mcp.tool()
def explain_code_impact(symbol: str, change_type: str = 'remove',
                        file_path: str = '', max_depth: int = 5) -> str:
    """Return the four-section impact report with file names and line numbers."""
    db = CodeGraphDB(_db_path())
    try:
        return format_report(analyze_impact(db, symbol, change_type, file_path or None, max_depth))
    finally:
        db.close()


@mcp.tool()
def get_affected_files(symbol: str, file_path: str = '') -> dict:
    """Return distinct affected files and exact indexed usage line numbers."""
    db = CodeGraphDB(_db_path())
    try:
        result = analyze_impact(db, symbol, file_path=file_path or None)
        return {'status': result['status'], 'affected_files': result.get('affected_files', []),
                'candidates': result.get('candidates', [])}
    finally:
        db.close()


if __name__ == '__main__':
    mcp.run(transport='stdio')
