# Code Impact Analysis MCP

Preserves the five uploaded CodeGraph modules under `codegraph/`. Adds `impact.py` and `server.py`.

## Setup

Python 3.10+ recommended (3.11+ preferred).

```powershell
cd code_impact_mcp
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:CODEGRAPH_PROJECT_ROOT = 'D:\Projects\InsuranceApp'
python server.py
```

The MCP server communicates using stdio. Register `server.py` in your MCP client, with `CODEGRAPH_PROJECT_ROOT` set to your solution's root. Example generic MCP client configuration (replace paths with absolute paths):

```json
{
  "mcpServers": {
    "code-impact": {
      "command": "C:\\Python311\\python.exe",
      "args": ["D:\\tools\\code_impact_mcp\\server.py"],
      "env": {"CODEGRAPH_PROJECT_ROOT": "D:\\Projects\\InsuranceApp"}
    }
  }
}
```

Call `index_project` first, then `find_symbol`, `analyze_change_impact` or `explain_code_impact`.

Example: `explain_code_impact(symbol="GetPolicyDetails", change_type="remove")`.

For an IF condition, use `find_symbol("if_statement")`, then pass its full `id` into `explain_code_impact`.

## Design / accuracy notes

- Scans recursively, respecting `.gitignore` and excluding build/dependency directories.
- Supports C#, JavaScript, TypeScript, TSX and Python through the original indexer. Java support is retained.
- Definitions and call/reference edges have file and line coordinates. Condition declarations are additionally indexed for Tree-sitter languages.
- Counts represent **indexed sites**, not guaranteed exhaustive usage. Some call/reference nodes may produce multiple edge kinds on one line.
- Direct vs indirect dependency means graph depth, not confirmed runtime behavior.
- TypeScript/Angular HTML templates and external project wiring are **not yet fully indexed**. C# overload resolution, virtual/interface dispatch, extension methods, reflection, DI registration and dynamic invocation are **not compiler-accurate**. For high-confidence C# results, integrate Roslyn; for Angular, add Angular compiler template analysis.
- A condition is attached to its enclosing method. Changing the condition can affect the method's callers, but the analyzer does not yet compute branch-specific data flow.
- No source files are modified. Indexing writes `.codegraph/impact.sqlite` under the configured root.
- The four report sections are **deterministic evidence summaries**, not LLM-generated business explanations. See `IMPACT_ANALYSIS_PROMPT.md` for client-side LLM guidance. An MCP client LLM may elaborate based on code excerpts, but should label inferences and not invent references.
- File size limit inherited from the original indexer is 2 MiB per source file. Skipped files appear in diagnostics.
- Set `CODEGRAPH_PROJECT_ROOT` only to a trusted repository; the MCP process can read the repository source.

## Tests

`python -m unittest discover -s tests -v`
