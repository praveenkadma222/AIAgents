# Codebase Impact Graph MCP

This MCP server indexes a project folder locally and builds a navigable graph of
modules, classes, functions, module/class/instance attributes, calls, imports,
and statically resolved references across Python, C#, TypeScript/TSX,
JavaScript/JSX, and Java.
Ask which functions call a symbol, search code by meaning, or inspect the direct
and transitive impact of editing or removing it. Source files and search queries
remain on the machine; semantic search uses a local embedding model, not a
hosted embedding API.

## Requirements and setup

- Python 3.11 or newer
- Windows (the included `mcp.json` configuration is Windows-oriented)

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

In VS Code, use the workspace MCP configuration in `.vscode/mcp.json`. It
starts the server over stdio and selects this workspace as the default project.
Call `index_project` once before searching; call it again after source changes
to refresh the graph. To analyze a different project, pass its absolute path
to any MCP tool's optional `project_path` argument.

The first semantic search downloads the small `BAAI/bge-small-en-v1.5` model
into FastEmbed's local model cache. Embedding and similarity ranking then run
locally. Set `CODEGRAPH_EMBEDDING_MODEL` to another FastEmbed-supported model
when needed.

## MCP tools

- `index_project(project_path?)` scans supported, non-ignored source files in
  any selected folder and replaces the graph in
  `<project>/.codegraph/index.sqlite3`. Its response lists detected languages
  and parse diagnostics.
- `get_index_status(project_path?)` reports index counts and diagnostics.
- `search_symbols(query, limit?, project_path?)` searches identifiers,
  qualified names, signatures, and docstrings.
- `find_references(symbol, project_path?)` lists statically resolved direct
  callers, imports, and references with language, file paths, and line numbers.
- `analyze_impact(symbol, change_type?, project_path?)` details direct
  dependents, transitive callers, affected files, call distances, and risks for
  an `edit` or `remove`, across all indexed languages. Its `formatted_response`
  field includes the definition location and source followed by **What it does**,
  **What would be affected if you change it**, **Important nuance**, and the
  main change risk.
- `semantic_search(query, limit?, project_path?)` finds functions and classes
  related by meaning, using cached local embeddings.

Use the symbol ID (`path/to/file.py::Class.function`) or qualified name when a
name is ambiguous. The index is explicit rather than an automatic file watcher,
so re-index after changing project files for reports to reflect the current code.

## Index behavior and limitations

Supported source extensions are `.py`, `.cs`, `.java`, `.js`, `.jsx`, `.mjs`,
`.cjs`, `.ts`, `.tsx`, `.mts`, and `.cts`. The scanner follows the selected
folder's `.gitignore` and skips common generated, virtual-environment,
dependency, and cache directories. Files larger than 2 MiB and files that do
not parse are listed in diagnostics. SQLite is the local graph/index cache; it
does not limit the languages or project folder scanned.

Relationships are inferred statically from each language's syntax tree.
Dynamic imports, reflection, dependency injection, generated code,
monkey-patching, and ambiguous/unresolved calls cannot be guaranteed to appear.
The report includes this limitation so static analysis is not mistaken for
runtime proof. Unsupported file types are ignored rather than parsed as source.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
