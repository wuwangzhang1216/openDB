"""Small deterministic code intelligence helpers.

This is intentionally narrower than a full code graph. It extracts enough
structure to make local agent lookups cheaper: Python symbols and import links,
plus a few regex-based symbols for JS/TS-style files.
"""

from __future__ import annotations

import ast
import posixpath
import re
from pathlib import Path


_PY_EXTENSIONS = {".py"}
_JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_CODE_EXTENSIONS = _PY_EXTENSIONS | _JS_EXTENSIONS

_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)
_JS_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
    re.M,
)
_JS_CONST_FN_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
    re.M,
)
_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:.+?\s+from\s+)?|export\s+.+?\s+from\s+)['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\)""",
    re.S,
)


def is_code_path(filename: str, source_path: str | None = None) -> bool:
    suffix = Path(source_path or filename).suffix.lower()
    return suffix in _CODE_EXTENSIONS


def extract_code_intel(
    content: str,
    *,
    filename: str,
    source_path: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return ``(symbols, links)`` extracted from source text.

    Symbols are dictionaries ready for storage. Links use the same shape as
    ``extract_file_links`` so they can be stored in the existing file_links
    table.
    """
    path = (source_path or filename).replace("\\", "/")
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return _extract_python(content, path)
    if suffix in _JS_EXTENSIONS:
        return _extract_js_like(content, path)
    return [], []


def extract_code_intel_from_pages(
    pages,
    page_line_ranges: list[tuple[int, int]],
    *,
    filename: str,
    source_path: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Extract code intel from parser pages while preserving assembled line numbers."""
    all_symbols: list[dict] = []
    all_links: list[dict] = []
    for index, page in enumerate(pages):
        symbols, links = extract_code_intel(
            page.text,
            filename=filename,
            source_path=source_path,
        )
        offset = 0
        if index < len(page_line_ranges):
            offset = page_line_ranges[index][0] - 1
        for symbol in symbols:
            adjusted = dict(symbol)
            adjusted["start_line"] = int(adjusted["start_line"]) + offset
            adjusted["end_line"] = int(adjusted["end_line"]) + offset
            all_symbols.append(adjusted)
        all_links.extend(links)
    return all_symbols, _dedupe_links(all_links)


def _symbol(
    *,
    name: str,
    kind: str,
    qualified_name: str,
    start_line: int,
    end_line: int,
    signature: str = "",
    docstring: str = "",
) -> dict:
    return {
        "name": name,
        "kind": kind,
        "qualified_name": qualified_name,
        "start_line": start_line,
        "end_line": end_line,
        "signature": signature,
        "docstring": docstring,
    }


def _extract_python(content: str, source_path: str) -> tuple[list[dict], list[dict]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [], []

    symbols: list[dict] = []
    links: list[dict] = []

    def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [arg.arg for arg in node.args.posonlyargs + node.args.args]
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        args.extend(arg.arg for arg in node.args.kwonlyargs)
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        return f"{node.name}({', '.join(args)})"

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            qualified = ".".join([*self.stack, node.name])
            symbols.append(_symbol(
                name=node.name,
                kind="class",
                qualified_name=qualified,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                signature=node.name,
                docstring=ast.get_docstring(node) or "",
            ))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node, "method" if self.stack else "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node, "method" if self.stack else "function")

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
            kind: str,
        ) -> None:
            qualified = ".".join([*self.stack, node.name])
            symbols.append(_symbol(
                name=node.name,
                kind=kind,
                qualified_name=qualified,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                signature=signature(node),
                docstring=ast.get_docstring(node) or "",
            ))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

    Visitor().visit(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                links.extend(_python_import_targets(alias.name, source_path, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            links.extend(_python_import_targets(module, source_path, node.lineno))

    return symbols, _dedupe_links(links)


def _python_import_targets(module: str, source_path: str, line: int) -> list[dict]:
    if not module:
        return []
    if module.startswith("."):
        target_base = _resolve_relative_python_module(module, source_path)
    else:
        target_base = module.replace(".", "/")
    if not target_base:
        return []
    return [
        {
            "target": f"{target_base}.py",
            "link_type": "import",
            "context": f"import {module} (line {line})",
        },
        {
            "target": f"{target_base}/__init__.py",
            "link_type": "import",
            "context": f"import {module} (line {line})",
        },
    ]


def _resolve_relative_python_module(module: str, source_path: str) -> str | None:
    dots = len(module) - len(module.lstrip("."))
    rest = module.lstrip(".")
    directory = posixpath.dirname(source_path)
    parts = directory.split("/") if directory else []
    keep = max(0, len(parts) - max(0, dots - 1))
    base = "/".join(parts[:keep])
    if rest:
        rest_path = rest.replace(".", "/")
        return posixpath.normpath(posixpath.join(base, rest_path))
    return posixpath.normpath(base) if base else None


def _extract_js_like(content: str, source_path: str) -> tuple[list[dict], list[dict]]:
    symbols: list[dict] = []
    for kind, regex in [
        ("class", _JS_CLASS_RE),
        ("function", _JS_FUNCTION_RE),
        ("function", _JS_CONST_FN_RE),
    ]:
        for match in regex.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            name = match.group(1)
            symbols.append(_symbol(
                name=name,
                kind=kind,
                qualified_name=name,
                start_line=line,
                end_line=line,
                signature=name,
            ))

    links: list[dict] = []
    for match in _JS_IMPORT_RE.finditer(content):
        target = match.group(1) or match.group(2)
        if not target or not target.startswith("."):
            continue
        line = content.count("\n", 0, match.start()) + 1
        links.extend(_js_import_targets(target, source_path, line))

    return symbols, _dedupe_links(links)


def _js_import_targets(target: str, source_path: str, line: int) -> list[dict]:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), target))
    suffix = Path(base).suffix
    candidates = [base] if suffix else [
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}.js",
        f"{base}.jsx",
        f"{base}/index.ts",
        f"{base}/index.tsx",
        f"{base}/index.js",
        f"{base}/index.jsx",
    ]
    return [
        {
            "target": candidate,
            "link_type": "import",
            "context": f"import {target} (line {line})",
        }
        for candidate in candidates
    ]


def _dedupe_links(links: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for link in links:
        key = (link["target"], link["link_type"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(link)
    return unique
