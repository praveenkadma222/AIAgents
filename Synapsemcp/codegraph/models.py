from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Symbol:
    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    line: int
    end_line: int
    signature: str
    docstring: str
    source: str
    parent_id: str | None
    class_name: str | None
    language: str = "python"

    def embedding_text(self) -> str:
        return "\n".join(
            part for part in (
                f"{self.kind} {self.qualified_name}",
                self.signature,
                self.docstring,
                self.source,
            ) if part
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line": self.line,
            "end_line": self.end_line,
            "signature": self.signature,
            "docstring": self.docstring,
            "source": self.source,
            "parent_id": self.parent_id,
            "language": self.language,
        }


@dataclass(frozen=True)
class Edge:
    source_id: str
    target_id: str
    kind: str
    line: int
    expression: str


@dataclass(frozen=True)
class IndexedFile:
    path: str
    modified_ns: int
    size: int
    status: str
    error: str = ""
    language: str = "python"


@dataclass(frozen=True)
class IndexResult:
    symbols: list[Symbol]
    edges: list[Edge]
    files: list[IndexedFile]
    diagnostics: list[dict[str, object]]
    languages: list[str] = field(default_factory=list)
