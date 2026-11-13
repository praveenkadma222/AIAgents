from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

import hashlib

def document_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
from .models import Edge, IndexedFile, Symbol

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL, file_path TEXT NOT NULL, line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, signature TEXT NOT NULL, docstring TEXT NOT NULL,
    source TEXT NOT NULL, parent_id TEXT, class_name TEXT,
    language TEXT NOT NULL DEFAULT 'python'
);
CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols(name);
CREATE INDEX IF NOT EXISTS symbols_path_idx ON symbols(file_path);
CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT NOT NULL, target_id TEXT NOT NULL, kind TEXT NOT NULL,
    line INTEGER NOT NULL, expression TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS edges_target_idx ON edges(target_id, kind);
CREATE INDEX IF NOT EXISTS edges_source_idx ON edges(source_id, kind);
CREATE TABLE IF NOT EXISTS indexed_files (
    path TEXT PRIMARY KEY, modified_ns INTEGER NOT NULL, size INTEGER NOT NULL,
    status TEXT NOT NULL, error TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'python'
);
CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id TEXT NOT NULL, model_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    vector BLOB NOT NULL, PRIMARY KEY(symbol_id, model_id)
);
"""


class CodeGraphDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_schema()
        self.connection.commit()

    def _migrate_schema(self) -> None:
        for table, column in (("symbols", "language"), ("indexed_files", "language")):
            columns = {
                row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in columns:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT 'python'"
                )

    def close(self) -> None:
        self.connection.close()

    def replace_index(
        self,
        root: str,
        symbols: list[Symbol],
        edges: list[Edge],
        files: list[IndexedFile],
        diagnostics: list[dict[str, object]],
        languages: list[str] | None = None,
    ) -> None:
        db = self.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM symbols")
            db.execute("DELETE FROM edges")
            db.execute("DELETE FROM indexed_files")
            db.executemany(
                """INSERT INTO symbols
                   (id, name, qualified_name, kind, file_path, line, end_line,
                    signature, docstring, source, parent_id, class_name, language)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (s.id, s.name, s.qualified_name, s.kind, s.file_path, s.line,
                     s.end_line, s.signature, s.docstring, s.source, s.parent_id,
                     s.class_name, s.language)
                    for s in symbols
                ],
            )
            db.executemany(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
                [(e.source_id, e.target_id, e.kind, e.line, e.expression) for e in edges],
            )
            db.executemany(
                """INSERT INTO indexed_files
                   (path, modified_ns, size, status, error, language)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(f.path, f.modified_ns, f.size, f.status, f.error, f.language) for f in files],
            )
            db.execute(
                """INSERT OR REPLACE INTO metadata(key, value) VALUES
                   ('project_root', ?), ('diagnostics', ?), ('indexed_at', datetime('now')),
                   ('languages', ?)""",
                (root, json.dumps(diagnostics), json.dumps(languages or sorted({s.language for s in symbols}))),
            )
            db.execute("DELETE FROM embeddings WHERE symbol_id NOT IN (SELECT id FROM symbols)")
            # Keep cached vectors only when their source text still matches.
            rows = list(db.execute(
                "SELECT id, name, qualified_name, kind, signature, docstring, source FROM symbols"
            ))
            for row in rows:
                text = "\n".join(part for part in (
                    f"{row['kind']} {row['qualified_name']}", row["signature"],
                    row["docstring"], row["source"],
                ) if part)
                db.execute(
                    "DELETE FROM embeddings WHERE symbol_id = ? AND content_hash != ?",
                    (row["id"], document_hash(text)),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise

    def metadata(self) -> dict[str, Any]:
        return {row["key"]: row["value"] for row in self.connection.execute("SELECT key, value FROM metadata")}

    def stats(self) -> dict[str, Any]:
        metadata = self.metadata()
        counts = {
            "files": self.connection.execute("SELECT COUNT(*) FROM indexed_files WHERE status = 'indexed'").fetchone()[0],
            "skipped_files": self.connection.execute("SELECT COUNT(*) FROM indexed_files WHERE status != 'indexed'").fetchone()[0],
            "symbols": self.connection.execute("SELECT COUNT(*) FROM symbols WHERE kind != 'module'").fetchone()[0],
            "modules": self.connection.execute("SELECT COUNT(*) FROM symbols WHERE kind = 'module'").fetchone()[0],
            "relationships": self.connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "languages": sorted(
                row[0] for row in self.connection.execute(
                    "SELECT DISTINCT language FROM indexed_files WHERE status = 'indexed'"
                )
            ),
        }
        return {
            "project_root": metadata.get("project_root"),
            "indexed_at": metadata.get("indexed_at"),
            **counts,
            "diagnostics": json.loads(metadata.get("diagnostics", "[]")),
        }

    def symbols(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM symbols ORDER BY file_path, line")]

    def get_symbol(self, symbol_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,)).fetchone()
        return dict(row) if row else None

    def search_symbols(self, query: str, limit: int) -> list[dict[str, Any]]:
        text = query.strip().lower()
        if not text:
            return []
        rows = self.connection.execute(
            """SELECT * FROM symbols WHERE kind != 'module' AND
               (lower(name) LIKE ? OR lower(qualified_name) LIKE ? OR
                lower(docstring) LIKE ? OR lower(signature) LIKE ?)
               ORDER BY CASE WHEN lower(name) = ? THEN 0
                             WHEN lower(name) LIKE ? THEN 1
                             WHEN lower(qualified_name) LIKE ? THEN 2 ELSE 3 END,
                        file_path, line LIMIT ?""",
            (f"%{text}%", f"%{text}%", f"%{text}%", f"%{text}%",
             text, f"{text}%", f"%{text}%", limit),
        )
        return [dict(row) for row in rows]

    def incoming_edges(self, symbol_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            """SELECT e.*, s.name AS source_name, s.qualified_name AS source_qualified_name,
                      s.kind AS source_kind, s.file_path AS source_file_path,
                      s.line AS source_definition_line, s.signature AS source_signature,
                      s.docstring AS source_docstring, s.language AS source_language
               FROM edges e JOIN symbols s ON s.id = e.source_id
               WHERE e.target_id = ? ORDER BY e.line, s.file_path""",
            (symbol_id,),
        )]

    def edges(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            """SELECT e.*, s.file_path AS source_file_path,
                      t.file_path AS target_file_path
               FROM edges e JOIN symbols s ON s.id = e.source_id
               JOIN symbols t ON t.id = e.target_id"""
        )]

    def indexed_files(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM indexed_files ORDER BY path"
        )]

    def embedding_cache(self, model_id: str) -> dict[str, tuple[str, list[float]]]:
        result: dict[str, tuple[str, list[float]]] = {}
        for row in self.connection.execute(
            "SELECT symbol_id, content_hash, vector FROM embeddings WHERE model_id = ?",
            (model_id,),
        ):
            blob = row["vector"]
            values = list(struct.unpack(f"<{len(blob) // 4}f", blob))
            result[row["symbol_id"]] = (row["content_hash"], values)
        return result

    def save_embeddings(
        self,
        model_id: str,
        embeddings: list[tuple[str, str, list[float]]],
    ) -> None:
        self.connection.executemany(
            """INSERT OR REPLACE INTO embeddings
               (symbol_id, model_id, content_hash, vector) VALUES (?, ?, ?, ?)""",
            [
                (symbol_id, model_id, content_hash, struct.pack(f"<{len(vector)}f", *vector))
                for symbol_id, content_hash, vector in embeddings
            ],
        )
        self.connection.commit()
