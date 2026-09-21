"""Queries use saved graph facts; incoming lists are derived, never re-inferred."""
from __future__ import annotations


def file_context(graph: dict, path: str) -> dict:
    file = next((f for f in graph["files"] if f["path"] == path), None)
    if file is None:
        raise KeyError(path)
    imports = [e for e in graph["edges"] if e["kind"] == "import"]
    usages = []
    for source in graph["files"]:
        for binding in source.get("bindings", []):
            for use in binding["uses"]:
                if path in {use.get("target_file"), use.get("binding_target_file")}:
                    usages.append({"file": source["path"], "local_name": binding["local"], **use})
    return {
        "path": path,
        "snapshot_id": graph.get("snapshot", {}).get("id"),
        "bindings_available": "bindings" in file and file["language"] in {"python", "rust"},
        "imports": file.get("bindings", []) if file["language"] in {"python", "rust"} and "bindings" in file else file.get("imports", []),
        "imported_by": sorted({e["source"] for e in imports if e["target"] == path}),
        "used_by": usages,
        "symbols": file["symbols"],
        "diagnostics": file.get("diagnostics", []),
        "note": "No recorded uses means no static references were identified; it does not prove an import is unused.",
    }
