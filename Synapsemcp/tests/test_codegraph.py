from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codegraph.analyzer import build_index
from codegraph.database import CodeGraphDB
from server import analyze_impact, index_project


class CodeGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "pkg").mkdir()
        (self.root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "helpers.py").write_text(
            "memberCache = {}\n\n"
            "def normalize(value: str) -> str:\n"
            '    """Normalize a user value."""\n'
            "    return value.strip().lower()\n"
            "\n"
            "def get_member_cache():\n"
            "    return memberCache\n",
            encoding="utf-8",
        )
        (self.root / "pkg" / "service.py").write_text(
            "from .helpers import normalize\n\n"
            "class MemberService:\n"
            "    def save(self, value):\n"
            "        return normalize(value)\n\n"
            "def endpoint(value):\n"
            "    return MemberService().save(value)\n",
            encoding="utf-8",
        )
        (self.root / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")
        (self.root / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_index_resolves_calls_imports_and_skips_gitignored(self) -> None:
        result = build_index(self.root)
        ids = {symbol.id for symbol in result.symbols}
        normalize = "pkg/helpers.py::normalize"
        save = "pkg/service.py::MemberService.save"
        endpoint = "pkg/service.py::endpoint"
        self.assertIn(normalize, ids)
        self.assertIn(save, ids)
        self.assertIn(endpoint, ids)
        self.assertNotIn("ignored.py::ignored", ids)
        self.assertTrue(any(item["file"] == "broken.py" for item in result.diagnostics))
        self.assertTrue(any(
            edge.source_id == save and edge.target_id == normalize and edge.kind == "call"
            for edge in result.edges
        ))
        self.assertTrue(any(
            edge.source_id == "pkg/service.py::<module>"
            and edge.target_id == normalize and edge.kind == "import"
            for edge in result.edges
        ))
        self.assertTrue(any(
            edge.source_id == endpoint and edge.target_id == save and edge.kind == "call"
            for edge in result.edges
        ))

    def test_graph_is_persisted_and_incoming_references_are_queryable(self) -> None:
        result = build_index(self.root)
        db = CodeGraphDB(self.root / ".codegraph" / "index.sqlite3")
        try:
            db.replace_index(
                str(self.root), result.symbols, result.edges, result.files, result.diagnostics
            )
            refs = db.incoming_edges("pkg/helpers.py::normalize")
            self.assertTrue(any(edge["source_id"] == "pkg/service.py::MemberService.save" for edge in refs))
            self.assertEqual(db.stats()["symbols"], 6)
            self.assertTrue(any(
                edge["source_id"] == "pkg/helpers.py::get_member_cache"
                for edge in db.incoming_edges("pkg/helpers.py::memberCache")
            ))
        finally:
            db.close()

    def test_impact_report_includes_direct_and_transitive_callers(self) -> None:
        index_project(str(self.root))
        report = analyze_impact("normalize", "remove", str(self.root))
        self.assertEqual(report["summary"]["direct_call_sites"], 1)
        self.assertEqual(report["summary"]["transitive_callers"], 1)
        self.assertEqual(report["summary"]["affected_files"], ["pkg/service.py"])
        formatted = report["formatted_response"]
        self.assertIn("**Definition:** `pkg/helpers.py:", formatted)
        self.assertIn("```python", formatted)
        self.assertIn("**What it does:**", formatted)
        self.assertIn("**What would be affected if you change it:**", formatted)
        self.assertIn("**Important nuance:**", formatted)
        self.assertIn("**If you change the symbol itself, the main risk is:**", formatted)
        self.assertIn(
            "pkg/service.py::MemberService.save",
            [reference["symbol_id"] for reference in report["direct_dependents"]],
        )
        self.assertEqual(
            [caller["symbol"] for caller in report["transitive_callers"]],
            ["endpoint"],
        )
        self.assertEqual(report["transitive_callers"][0]["call_site_lines"], [8])

    def test_build_index_supports_typescript_javascript_java_and_csharp(self) -> None:
        (self.root / "helpers.ts").write_text(
            "export function normalize(value: string) { return value.trim(); }\n",
            encoding="utf-8",
        )
        (self.root / "service.ts").write_text(
            'import { normalize } from "./helpers";\n'
            "export class User { save(name: string) { return normalize(name); } }\n",
            encoding="utf-8",
        )
        (self.root / "app.js").write_text(
            "const memberCache = new Map();\n"
            "function clearMembers() { memberCache.clear(); }\n",
            encoding="utf-8",
        )
        (self.root / "User.java").write_text(
            "class User { String format(String value) { return value.trim(); } "
            "String run(String value) { return format(value); } }\n",
            encoding="utf-8",
        )
        (self.root / "User.cs").write_text(
            "class User { string Format(string value) { return value.Trim(); } "
            "string Run(string value) { return Format(value); } }\n",
            encoding="utf-8",
        )

        result = build_index(self.root)
        symbols = {symbol.id: symbol for symbol in result.symbols}
        self.assertEqual(
            set(result.languages),
            {"python", "typescript", "javascript", "java", "csharp"},
        )
        self.assertIn("service.ts::User.save", symbols)
        self.assertEqual(symbols["service.ts::User.save"].language, "typescript")
        self.assertTrue(any(
            edge.source_id == "service.ts::User.save"
            and edge.target_id == "helpers.ts::normalize"
            and edge.kind == "call"
            for edge in result.edges
        ))
        self.assertTrue(any(
            edge.source_id == "app.js::clearMembers"
            and edge.target_id == "app.js::memberCache@1"
            and edge.kind == "reference"
            for edge in result.edges
        ))
        self.assertTrue(any(
            edge.source_id == "User.java::User.run"
            and edge.target_id == "User.java::User.format"
            and edge.kind == "call"
            for edge in result.edges
        ))
        self.assertTrue(any(
            edge.source_id == "User.cs::User.Run"
            and edge.target_id == "User.cs::User.Format"
            and edge.kind == "call"
            for edge in result.edges
        ))
        indexed = index_project(str(self.root))
        self.assertEqual(
            set(indexed["languages"]),
            {"python", "typescript", "javascript", "java", "csharp"},
        )
        report = analyze_impact("helpers.ts::normalize", "remove", str(self.root))
        self.assertEqual(report["summary"]["direct_call_sites"], 1)
        self.assertEqual(
            {item["language"] for item in report["direct_dependents"]},
            {"typescript"},
        )
        self.assertEqual(
            report["summary"]["affected_files"],
            ["service.ts"],
        )
        self.assertIn("**Definition:** `helpers.ts:1`", report["formatted_response"])
        self.assertIn("```typescript", report["formatted_response"])
        self.assertIn("service.ts:2", report["formatted_response"])

    def test_java_package_imports_resolve_across_files(self) -> None:
        (self.root / "Util.java").write_text(
            "package com.example;\n"
            "class Util { static String normalize(String value) { return value.trim(); } }\n",
            encoding="utf-8",
        )
        (self.root / "Service.java").write_text(
            "package com.example;\n"
            "import com.example.Util;\n"
            "class Service { String run(String value) { return Util.normalize(value); } }\n",
            encoding="utf-8",
        )

        result = build_index(self.root)
        self.assertTrue(any(
            edge.source_id == "Service.java::com.example.Service.run"
            and edge.target_id == "Util.java::com.example.Util.normalize"
            and edge.kind == "call"
            for edge in result.edges
        ))
        self.assertTrue(any(
            edge.source_id == "Service.java::<module>"
            and edge.target_id == "Util.java::com.example.Util"
            and edge.kind == "import"
            for edge in result.edges
        ))


if __name__ == "__main__":
    unittest.main()
