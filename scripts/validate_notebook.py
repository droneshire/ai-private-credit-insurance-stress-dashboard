from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

import nbformat


DEFAULT_NOTEBOOK = Path("ai_private_credit_insurance_stress_dashboard.ipynb")


def validate_notebook(path: Path) -> tuple[int, int]:
    """Validate the notebook schema and compile each Python code cell."""
    with path.open(encoding="utf-8") as notebook_file:
        raw_notebook: dict[str, Any] = json.load(notebook_file)

    nbformat.validate(raw_notebook)
    cells = raw_notebook.get("cells", [])
    code_cells = 0

    for index, cell in enumerate(cells, start=1):
        if cell.get("cell_type") != "code":
            continue
        code_cells += 1
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        ast.parse(source, filename=f"{path}:cell-{index}")

    return len(cells), code_cells


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebook", nargs="?", type=Path, default=DEFAULT_NOTEBOOK)
    args = parser.parse_args()

    total_cells, code_cells = validate_notebook(args.notebook)
    print(f"Validated {args.notebook}: {total_cells} cells, {code_cells} code cells")


if __name__ == "__main__":
    main()
