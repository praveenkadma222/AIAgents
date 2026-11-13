import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codegraph.database import CodeGraphDB
from codegraph.models import Symbol, Edge, IndexedFile
from codegraph.impact import analyze_impact, format_report


class ImpactTests(unittest.TestCase):
    def test_direct_and_transitive_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'service.cs').write_text('void Target() {}\n', encoding='utf-8')
            (root / 'controller.cs').write_text('void Controller() { Target(); }\n', encoding='utf-8')
            (root / 'app.cs').write_text('void Start() { Controller(); }\n', encoding='utf-8')
            symbols = [
                Symbol('service::Target', 'Target', 'Target', 'function', 'service.cs', 1, 1, 'Target()', '', 'void Target() {}', None, None, 'csharp'),
                Symbol('controller::Controller', 'Controller', 'Controller', 'function', 'controller.cs', 1, 1, 'Controller()', '', '', None, None, 'csharp'),
                Symbol('app::Start', 'Start', 'Start', 'function', 'app.cs', 1, 1, 'Start()', '', '', None, None, 'csharp'),
            ]
            edges = [Edge('controller::Controller', 'service::Target', 'call', 1, 'Target'),
                     Edge('app::Start', 'controller::Controller', 'call', 1, 'Controller')]
            db = CodeGraphDB(root / 'graph.sqlite')
            try:
                db.replace_index(str(root), symbols, edges,
                                 [IndexedFile(p, 0, 0, 'indexed', '', 'csharp') for p in ('service.cs','controller.cs','app.cs')], [])
                report = analyze_impact(db, 'Target')
                self.assertEqual(report['status'], 'ok')
                self.assertEqual(report['affected_file_count'], 2)
                self.assertEqual(report['direct_reference_count'], 1)
                self.assertEqual(report['indirect_reference_count'], 1)
                self.assertIn('## Important nuance', format_report(report))
                self.assertEqual(analyze_impact(db, 'Missing')['status'], 'not_found')
            finally:
                db.close()

    def test_ambiguous_symbol_requires_disambiguation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CodeGraphDB(Path(tmp) / 'graph.sqlite')
            try:
                syms = [Symbol(f'{p}::Do', 'Do', 'Do', 'function', p, 1, 2, 'Do()', '', '', None, None, 'csharp')
                        for p in ('a.cs', 'b.cs')]
                db.replace_index(tmp, syms, [], [], [])
                self.assertEqual(analyze_impact(db, 'Do')['status'], 'ambiguous')
                self.assertEqual(analyze_impact(db, 'Do', file_path='a.cs')['status'], 'ok')
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
