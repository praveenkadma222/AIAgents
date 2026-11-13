# MCP client instruction: evidence-grounded impact analysis

When a user asks what happens if a method, variable, or condition is removed, modified, or renamed:

1. Call `find_symbol` to identify exact symbol IDs. If ambiguous, ask the user to select the exact file or qualified ID. Never guess between overloads or similarly named methods.
2. Call `analyze_change_impact` using the chosen ID. If the index is missing or stale, ask the user to run `index_project` first.
3. Use `affected_locations` and code excerpts as the evidence for concrete file and line references. Distinguish direct from indirect dependencies and report the number of distinct affected files. Do not describe the file count as an exhaustive guarantee.
4. Read and interpret the returned source excerpt to explain the function's actual behavior. If the excerpt is insufficient, explicitly say that the behavior is not established. Do not invent domain behavior or call paths.
5. Respond with the following sections, in order:

**Definition and affected locations**: symbol, file, declaration line range, total distinct affected files, direct/indirect counts, and a table of file, exact usage line, caller, dependency kind.

**What it does**: explain the actual implementation from available source evidence, distinguishing observed behavior from inference.

**What would be affected if you change it**: list proven incoming references, probable indirect consequences, and which files/tests need review. A reference alone is not proof of runtime failure.

**Important nuance**: note dynamic dispatch, dependency injection, reflection, Angular HTML templates, runtime configuration, external API clients, C# overload resolution, unindexed files and skipped diagnostics where relevant.

**If you change the symbol itself, the main risk is**: explain compile-time risks vs silent behavioral changes. Recommend a build and relevant tests.

For condition changes, identify the containing method and branch, but do not assert branch reachability or data flow without dedicated analysis. Never modify repository files unless the user separately requests a code edit.
