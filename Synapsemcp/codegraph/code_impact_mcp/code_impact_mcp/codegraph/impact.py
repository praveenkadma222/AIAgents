"""Read-only, evidence-first code impact reporting on a CodeGraph index."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .database import CodeGraphDB


def find_symbols(db: CodeGraphDB, query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Find a symbol by exact/partial name, qualified name or ID."""
    if not query.strip():
        raise ValueError('A symbol name or ID is required')
    matches = [s for s in db.symbols() if s['id'] == query or s['name'] == query or s['qualified_name'] == query]
    if matches:
        return matches[:limit]
    return db.search_symbols(query, limit)


def _line_excerpt(root: Path, file_path: str, line: int, radius: int = 2) -> str:
    path = (root / file_path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return ''
    try:
        lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    except OSError:
        return ''
    return '\n'.join(f'{i+1}: {lines[i]}' for i in range(max(0, line-radius-1), min(len(lines), line+radius)))


def analyze_impact(db: CodeGraphDB, symbol: str, change_type: str = 'remove',
                   file_path: str | None = None, max_depth: int = 5) -> dict[str, Any]:
    """Trace incoming references. No code is changed; all effects are potential."""
    if change_type not in {'remove', 'modify', 'rename'}:
        raise ValueError('change_type must be remove, modify or rename')
    if not 1 <= max_depth <= 20:
        raise ValueError('max_depth must be between 1 and 20')
    candidates = find_symbols(db, symbol, 100)
    if file_path:
        candidates = [s for s in candidates if s['file_path'] == file_path.replace('\\', '/')]
    if not candidates:
        return {'status': 'not_found', 'query': symbol, 'message': 'No matching indexed symbol. Check index diagnostics and the source path.'}
    if len(candidates) != 1:
        return {'status': 'ambiguous', 'query': symbol,
                'candidates': [{k: s[k] for k in ('id','kind','file_path','line','signature')} for s in candidates],
                'message': 'Specify the fully qualified symbol ID or file_path.'}
    target = candidates[0]
    all_symbols = {s['id']: s for s in db.symbols()}
    all_edges = db.edges()
    incoming = defaultdict(list)
    for edge in all_edges:
        incoming[edge['target_id']].append(edge)
    queue = deque([(target['id'], 0)])
    visited = {target['id']}
    usages = []
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in incoming[current]:
            owner = all_symbols.get(edge['source_id'])
            if not owner:
                continue
            usages.append({'file': owner['file_path'], 'line': edge['line'],
                           'kind': edge['kind'], 'expression': edge['expression'],
                           'caller': owner['qualified_name'], 'caller_id': owner['id'],
                           'depth': depth + 1,
                           'classification': 'direct' if depth == 0 else 'indirect'})
            if owner['id'] not in visited:
                visited.add(owner['id'])
                queue.append((owner['id'], depth + 1))
    # A condition is embedded in a parent method: deleting it can affect that parent
    # even when it has no incoming reference edge of its own.
    if target['kind'] == 'condition' and target['parent_id'] in all_symbols:
        owner = all_symbols[target['parent_id']]
        usages.append({'file': owner['file_path'], 'line': target['line'], 'kind': 'contains',
                       'expression': target['signature'], 'caller': owner['qualified_name'],
                       'caller_id': owner['id'], 'depth': 1, 'classification': 'enclosing_scope'})
        queue = deque([(owner['id'], 1)])
        seen = {target['id'], owner['id']}
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in incoming[current]:
                src = all_symbols.get(edge['source_id'])
                if not src:
                    continue
                usages.append({'file': src['file_path'], 'line': edge['line'], 'kind': edge['kind'],
                               'expression': edge['expression'], 'caller': src['qualified_name'],
                               'caller_id': src['id'], 'depth': depth + 1,
                               'classification': 'indirect'})
                if src['id'] not in seen:
                    seen.add(src['id'])
                    queue.append((src['id'], depth + 1))
    usages = list({(u['file'], u['line'], u['kind'], u['caller_id'], u['depth']): u for u in usages}.values())
    usages.sort(key=lambda u: (u['depth'], u['file'], u['line'], u['kind']))
    files = defaultdict(list)
    for u in usages:
        files[u['file']].append(u)
    root_str = db.metadata().get('project_root')
    root = Path(root_str).resolve() if root_str else Path.cwd()
    for u in usages:
        u['code_excerpt'] = _line_excerpt(root, u['file'], u['line'], 1)
    diagnostics = db.stats().get('diagnostics', [])
    direct = sum(u['classification'] == 'direct' for u in usages)
    indirect = sum(u['classification'] == 'indirect' for u in usages)
    kind = target['kind']
    action = {'remove': 'removing', 'modify': 'modifying', 'rename': 'renaming'}[change_type]
    what = (f"{kind.title()} '{target['qualified_name']}' is declared in {target['file_path']} "
            f"at lines {target['line']}-{target['end_line']}. "
            f"Signature/condition: {target['signature'] or '(not recorded)'}. "
            f"Source excerpt: {target['source'][:1200] or '(not available)'}. "
            'The business purpose requires inspection of the surrounding application code.')
    affected = (f"Found {direct} direct and {indirect} indirect indexed dependency sites "
                f"across {len(files)} distinct referencing files. "
                f"{action.capitalize()} this {kind} may affect these callers or enclosing scopes; "
                'see affected_locations for file, line, call expression and evidence.')
    nuance = ('Static analysis cannot guarantee complete usage detection: dependency injection, reflection, '
              'dynamic dispatch, Angular templates, route configuration, external consumers, and runtime wiring '
              'may create additional dependencies. The analyzer is best-effort; verify with builds and tests.')
    if diagnostics:
        nuance += f' Index reported {len(diagnostics)} diagnostic(s); inspect diagnostics before treating counts as exhaustive.'
    risk = ('Removing or renaming a referenced declaration can break builds or runtime execution; '
            'modifying its logic can change behavior without a compile-time error. '
            'For conditions, changing predicates can change which branches execute. '
            'Review each listed usage and run affected tests.')
    return {
        'status': 'ok', 'symbol': {k: target[k] for k in ('id','name','qualified_name','kind','file_path','line','end_line','signature','language')},
        'change_type': change_type, 'affected_file_count': len(files),
        'reference_count': len(usages), 'direct_reference_count': direct,
        'indirect_reference_count': indirect,
        'affected_locations': usages,
        'affected_files': [{'file': name, 'lines': sorted({u['line'] for u in locs}), 'count': len(locs)}
                           for name, locs in sorted(files.items())],
        'what_it_does': what,
        'what_would_be_affected_if_you_change_it': affected,
        'important_nuance': nuance,
        'if_you_change_the_symbol_itself_the_main_risk_is': risk,
        'diagnostics': diagnostics,
        'analysis_limitations': {'max_depth': max_depth, 'coverage': 'best-effort static graph, not verified runtime behavior'}
    }


def format_report(report: dict[str, Any]) -> str:
    if report['status'] != 'ok':
        return str(report)
    s = report['symbol']
    locations = '\n'.join(f"| `{u['file']}` | {u['line']} | {u['classification']} | `{u['caller']}` |"
                          for u in report['affected_locations']) or '| (none found) | — | — | — |'
    return (f"# Code impact: `{s['qualified_name']}`\n\n"
            f"**Definition:** `{s['file_path']}` lines {s['line']}-{s['end_line']}\n\n"
            f"**Affected files:** {report['affected_file_count']} | **Reference sites:** {report['reference_count']} "
            f"| **Direct:** {report['direct_reference_count']} | **Indirect:** {report['indirect_reference_count']}\n\n"
            f"| File | Line | Relationship | Caller |\n|---|---:|---|---|\n{locations}\n\n"
            f"## What it does\n{report['what_it_does']}\n\n"
            f"## What would be affected if you change it\n{report['what_would_be_affected_if_you_change_it']}\n\n"
            f"## Important nuance\n{report['important_nuance']}\n\n"
            f"## If you change the symbol itself, the main risk is\n{report['if_you_change_the_symbol_itself_the_main_risk_is']}\n")
