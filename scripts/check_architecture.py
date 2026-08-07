#!/usr/bin/env python3
"""Check deterministic dependency rules for the R2 ``src/snotes`` namespace."""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = PROJECT_ROOT / "src" / "snotes"

LAYERS = frozenset(
    {"domain", "application", "storage", "ui", "transport", "background", "exporting"}
)
ADAPTER_LAYERS = frozenset({"ui", "transport", "background", "exporting"})
SQL_PATTERN = re.compile(
    r"\b(?:"
    r"SELECT\s+.+\s+FROM|"
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO|"
    r"UPDATE\s+[A-Za-z_][\w.]*\s+SET|"
    r"DELETE\s+FROM|"
    r"CREATE\s+(?:VIRTUAL\s+)?(?:TABLE|INDEX|TRIGGER)|"
    r"ALTER\s+TABLE|"
    r"DROP\s+(?:TABLE|INDEX|TRIGGER)|"
    r"PRAGMA(?:\s|\()"
    r")",
    re.IGNORECASE | re.DOTALL,
)
OWNER_MARKERS = {
    "authorize": ("authorize", "authorization"),
    "redaction": ("redact", "redaction"),
    "owner": ("owner",),
}


@dataclass(frozen=True)
class Finding:
    code: str
    path: Path
    line: int
    message: str

    def render(self, source_root: Path) -> str:
        try:
            display_path = self.path.relative_to(source_root.parent.parent)
        except ValueError:
            display_path = self.path
        return f"{display_path}:{self.line}: {self.code}: {self.message}"


def inspect_tree(source_root: Path = DEFAULT_SOURCE_ROOT) -> tuple[list[Finding], list[Finding]]:
    """Return hard failures and human-review warnings for an R2 source tree."""
    source_root = Path(source_root).resolve()
    errors: list[Finding] = []
    warnings: list[Finding] = []
    marker_files: dict[str, set[Path]] = {marker: set() for marker in OWNER_MARKERS}

    if not source_root.is_dir():
        return (
            [Finding("missing-source-root", source_root, 1, "R2 source root does not exist")],
            [],
        )

    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        source_layer = relative.parts[0] if len(relative.parts) > 1 else None
        is_bootstrap = relative == Path("bootstrap.py")
        text = path.read_text(encoding="utf-8")

        if len(text.splitlines()) > 500:
            warnings.append(
                Finding("large-file", path, 1, "R2 production file exceeds 500 lines")
            )

        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:
            errors.append(
                Finding(
                    "syntax-error",
                    path,
                    error.lineno or 1,
                    error.msg,
                )
            )
            continue

        current_module, current_package = _module_context(path, source_root)
        docstrings = _docstring_constant_ids(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3" or alias.name.startswith("sqlite3."):
                        _require_storage_sql(errors, path, node.lineno, source_layer, "sqlite3 import")
                    _check_dependency(
                        errors,
                        path,
                        node.lineno,
                        source_layer,
                        alias.name,
                        is_bootstrap,
                    )
            elif isinstance(node, ast.ImportFrom):
                imported_module = _resolve_from_module(node, current_package)
                for alias in node.names:
                    dependency_module = _dependency_module(imported_module, alias.name)
                    _check_dependency(
                        errors,
                        path,
                        node.lineno,
                        source_layer,
                        dependency_module,
                        is_bootstrap,
                    )
                    if imported_module == "sqlite3" or imported_module.startswith("sqlite3."):
                        _require_storage_sql(
                            errors, path, node.lineno, source_layer, "sqlite3 import"
                        )
                    if alias.name.startswith("_") and imported_module != current_module:
                        errors.append(
                            Finding(
                                "private-import",
                                path,
                                node.lineno,
                                f"cross-module import of private member {alias.name!r}",
                            )
                        )
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and SQL_PATTERN.search(node.value)
            ):
                _require_storage_sql(
                    errors, path, node.lineno, source_layer, "SQL definition or statement"
                )
            elif isinstance(node, ast.Call) and _is_dynamic_import(node):
                warnings.append(
                    Finding(
                        "dynamic-import",
                        path,
                        node.lineno,
                        "dynamic import requires manual dependency review",
                    )
                )

            symbol = _symbol_name(node)
            if symbol:
                lowered = symbol.casefold()
                for marker, needles in OWNER_MARKERS.items():
                    if any(needle in lowered for needle in needles):
                        marker_files[marker].add(path)

    for marker, paths in marker_files.items():
        if len(paths) > 1:
            warnings.append(
                Finding(
                    f"possible-duplicate-{marker}-owner",
                    sorted(paths)[0],
                    1,
                    f"symbols related to {marker!r} appear in {len(paths)} files; review ownership",
                )
            )

    return errors, warnings


def _module_context(path: Path, source_root: Path) -> tuple[str, tuple[str, ...]]:
    relative = path.relative_to(source_root)
    directories = list(relative.parts[:-1])
    package = ("src", "snotes", *directories)
    if path.name == "__init__.py":
        return ".".join(package), package
    return ".".join((*package, path.stem)), package


def _resolve_from_module(node: ast.ImportFrom, current_package: tuple[str, ...]) -> str:
    if node.level == 0:
        return node.module or ""
    trim = node.level - 1
    if trim > len(current_package):
        return node.module or ""
    base = current_package[: len(current_package) - trim]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*base, *suffix))


def _dependency_module(imported_module: str, imported_name: str) -> str:
    normalized = _normalized_snotes_parts(imported_module)
    if imported_module in {"src.snotes", "snotes"} or normalized == ("application",):
        return f"{imported_module}.{imported_name}"
    return imported_module


def _normalized_snotes_parts(module: str) -> tuple[str, ...]:
    parts = tuple(part for part in module.split(".") if part)
    if parts[:2] == ("src", "snotes"):
        return parts[2:]
    if parts[:1] == ("snotes",):
        return parts[1:]
    return ()


def _target_layer(module: str) -> str | None:
    parts = _normalized_snotes_parts(module)
    if parts and parts[0] in LAYERS:
        return parts[0]
    return None


def _is_application_ports(module: str) -> bool:
    parts = _normalized_snotes_parts(module)
    return parts[:2] == ("application", "ports")


def _check_dependency(
    errors: list[Finding],
    path: Path,
    line: int,
    source_layer: str | None,
    imported_module: str,
    is_bootstrap: bool,
) -> None:
    if is_bootstrap or source_layer not in LAYERS:
        return
    target_layer = _target_layer(imported_module)
    if target_layer is None or target_layer == source_layer:
        return

    violation = False
    if source_layer == "domain":
        violation = True
    elif source_layer == "application":
        violation = target_layer != "domain"
    elif source_layer == "storage":
        violation = target_layer in ADAPTER_LAYERS or (
            target_layer == "application" and not _is_application_ports(imported_module)
        )
    elif source_layer in ADAPTER_LAYERS:
        violation = target_layer == "storage" or (
            target_layer in ADAPTER_LAYERS and target_layer != source_layer
        )

    if violation:
        errors.append(
            Finding(
                "dependency-direction",
                path,
                line,
                f"{source_layer} must not import {imported_module}",
            )
        )


def _require_storage_sql(
    errors: list[Finding],
    path: Path,
    line: int,
    source_layer: str | None,
    description: str,
) -> None:
    if source_layer != "storage":
        errors.append(
            Finding(
                "sql-outside-storage",
                path,
                line,
                f"{description} is allowed only under src/snotes/storage",
            )
        )


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return docstrings


def _is_dynamic_import(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id == "__import__"
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
    )


def _symbol_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def main() -> int:
    errors, warnings = inspect_tree()
    for warning in warnings:
        print(f"WARNING: {warning.render(DEFAULT_SOURCE_ROOT)}")
    for error in errors:
        print(f"ERROR: {error.render(DEFAULT_SOURCE_ROOT)}", file=sys.stderr)
    if errors:
        print(f"ARCHITECTURE_CHECK=FAIL errors={len(errors)} warnings={len(warnings)}")
        return 1
    file_count = sum(1 for _ in DEFAULT_SOURCE_ROOT.rglob("*.py"))
    print(f"ARCHITECTURE_CHECK=PASS files={file_count} warnings={len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
