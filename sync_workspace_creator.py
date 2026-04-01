"""
Sync Workspace Creator from Manifest
=====================================

Reads workspace_manifest.json and regenerates the SHEET_DEFINITIONS and
FOLDER_STRUCTURE in create_workspace.py so they match the live workspace.

Usage:
    python sync_workspace_creator.py          # Preview changes (dry run)
    python sync_workspace_creator.py --apply  # Write changes to create_workspace.py

This replaces the manual process of keeping create_workspace.py in sync
with the manifest every time a sheet/column is added or changed.
"""

import json
import sys
import re
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent / "functions" / "workspace_manifest.json"
CREATOR_PATH = Path(__file__).parent / "create_workspace.py"

# Picklist options that are data-dependent (populated at runtime, not structural).
# These will be emitted as PICKLIST type but without hardcoded options.
DATA_DEPENDENT_OPTIONS_PREFIX = ("TAG-", "SCHED-", "ALLOC-", "NEST-")


def load_manifest():
    """Load and return the workspace manifest."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_data_dependent(options):
    """Check if picklist options are data-dependent (not structural)."""
    if not options:
        return False
    return any(
        opt.startswith(DATA_DEPENDENT_OPTIONS_PREFIX)
        for opt in options
    )


def build_folder_structure(manifest):
    """Extract physical folder names from manifest, sorted by name."""
    folders = []
    for _logical, info in manifest.get("folders", {}).items():
        folders.append(info["name"])
    folders.sort()
    return folders


def build_sheet_definitions(manifest):
    """Build SHEET_DEFINITIONS dict from manifest data."""
    # Build reverse folder map: logical name -> physical name
    folder_map = {}
    for logical, info in manifest.get("folders", {}).items():
        folder_map[logical] = info["name"]

    sheets = {}
    for _logical_name, sheet_info in manifest.get("sheets", {}).items():
        sheet_name = sheet_info["name"]
        folder_logical = sheet_info.get("folder")
        folder_physical = folder_map.get(folder_logical) if folder_logical else None

        # Build columns sorted by index
        columns_raw = sheet_info.get("columns", {})
        sorted_columns = sorted(
            columns_raw.values(),
            key=lambda c: c.get("index", 0)
        )

        columns = []
        for col in sorted_columns:
            col_def = {
                "title": col["name"],
                "type": col["type"]
            }
            if col.get("primary"):
                col_def["primary"] = True
            if col["type"] == "PICKLIST" and col.get("options"):
                if not is_data_dependent(col["options"]):
                    col_def["options"] = col["options"]
            columns.append(col_def)

        sheets[sheet_name] = {
            "folder": folder_physical,
            "columns": columns
        }

    return sheets


def format_column(col, indent=12):
    """Format a single column definition as Python dict literal."""
    pad = " " * indent
    parts = [f'{pad}{{"title": {json.dumps(col["title"])}']
    parts.append(f'"type": {json.dumps(col["type"])}')
    if col.get("primary"):
        parts.append('"primary": True')
    if col.get("options"):
        opts = json.dumps(col["options"])
        parts.append(f'"options": {opts}')
    return ", ".join(parts) + "}"


def format_sheet_definitions(sheets, folder_structure):
    """Format the full SHEET_DEFINITIONS as a Python source string."""
    # Group sheets by folder for section comments
    folder_order = [None] + folder_structure
    folder_sheets = {f: [] for f in folder_order}

    for sheet_name, defn in sheets.items():
        folder = defn["folder"]
        if folder not in folder_sheets:
            folder_sheets[folder] = []
        folder_sheets[folder].append((sheet_name, defn))

    # Sort sheets within each folder by name
    for folder in folder_sheets:
        folder_sheets[folder].sort(key=lambda x: x[0])

    lines = ["# Sheet definitions matching workspace_manifest.json"]
    lines.append("SHEET_DEFINITIONS = {")

    for folder in folder_order:
        if folder not in folder_sheets or not folder_sheets[folder]:
            continue

        # Section comment
        if folder is None:
            lines.append("    # ==================== Root level sheets ====================")
        else:
            lines.append(f"")
            lines.append(f"    # ==================== {folder} ====================")

        for sheet_name, defn in folder_sheets[folder]:
            folder_val = f'"{defn["folder"]}"' if defn["folder"] else "None"
            lines.append(f"    {json.dumps(sheet_name)}: {{")
            lines.append(f'        "folder": {folder_val},')
            lines.append(f'        "columns": [')

            for col in defn["columns"]:
                lines.append(format_column(col) + ",")

            lines.append(f"        ]")
            lines.append(f"    }},")

    lines.append("}")
    return "\n".join(lines)


def format_folder_structure(folders):
    """Format the FOLDER_STRUCTURE as a Python source string."""
    lines = ["# Folder structure to create"]
    lines.append("FOLDER_STRUCTURE = [")
    for f in folders:
        lines.append(f"    {json.dumps(f)},")
    lines.append("]")
    return "\n".join(lines)


def update_creator_source(source, new_folders, new_sheets):
    """Replace FOLDER_STRUCTURE and SHEET_DEFINITIONS in the source file."""
    # Replace FOLDER_STRUCTURE
    folder_pattern = re.compile(
        r"^# Folder structure to create\nFOLDER_STRUCTURE = \[.*?\]",
        re.MULTILINE | re.DOTALL
    )
    source = folder_pattern.sub(new_folders, source)

    # Replace SHEET_DEFINITIONS
    sheet_pattern = re.compile(
        r"^# Sheet definitions.*?\nSHEET_DEFINITIONS = \{.*?^\}",
        re.MULTILINE | re.DOTALL
    )
    source = sheet_pattern.sub(new_sheets, source)

    return source


def diff_summary(old_sheets, new_sheets):
    """Print a human-readable summary of changes."""
    old_names = set(old_sheets.keys())
    new_names = set(new_sheets.keys())

    added = new_names - old_names
    removed = old_names - new_names
    common = old_names & new_names

    changes = []

    if added:
        changes.append(f"\n  Added sheets ({len(added)}):")
        for name in sorted(added):
            n_cols = len(new_sheets[name]["columns"])
            changes.append(f"    + {name} ({n_cols} columns)")

    if removed:
        changes.append(f"\n  Removed sheets ({len(removed)}):")
        for name in sorted(removed):
            changes.append(f"    - {name}")

    modified = []
    for name in sorted(common):
        old_cols = {c["title"]: c for c in old_sheets[name]["columns"]}
        new_cols = {c["title"]: c for c in new_sheets[name]["columns"]}

        old_col_names = set(old_cols.keys())
        new_col_names = set(new_cols.keys())

        col_added = new_col_names - old_col_names
        col_removed = old_col_names - new_col_names

        # Check for type/option changes in common columns
        col_changed = []
        for col_name in old_col_names & new_col_names:
            old_c = old_cols[col_name]
            new_c = new_cols[col_name]
            diffs = []
            if old_c.get("type") != new_c.get("type"):
                diffs.append(f"type: {old_c.get('type')} -> {new_c.get('type')}")
            if old_c.get("primary") != new_c.get("primary"):
                diffs.append(f"primary: {old_c.get('primary')} -> {new_c.get('primary')}")
            if old_c.get("options") != new_c.get("options"):
                diffs.append("options changed")
            if diffs:
                col_changed.append((col_name, diffs))

        if col_added or col_removed or col_changed:
            detail = []
            for c in sorted(col_added):
                detail.append(f"      + column: {c}")
            for c in sorted(col_removed):
                detail.append(f"      - column: {c}")
            for c, diffs in col_changed:
                detail.append(f"      ~ column: {c} ({', '.join(diffs)})")
            modified.append((name, detail))

    if modified:
        changes.append(f"\n  Modified sheets ({len(modified)}):")
        for name, details in modified:
            changes.append(f"    ~ {name}")
            for d in details:
                changes.append(d)

    if not changes:
        return "  No changes detected. create_workspace.py is up to date."

    return "\n".join(changes)


def parse_existing_sheets(source):
    """Parse existing SHEET_DEFINITIONS from create_workspace.py source for diffing."""
    # Use exec to extract the dict - safe since we control the file
    match = re.search(
        r"^SHEET_DEFINITIONS = \{.*?^\}",
        source,
        re.MULTILINE | re.DOTALL
    )
    if not match:
        return {}

    local_ns = {}
    try:
        exec(match.group(0), {}, local_ns)
    except Exception:
        return {}
    return local_ns.get("SHEET_DEFINITIONS", {})


def main():
    apply_mode = "--apply" in sys.argv

    print("=" * 60)
    print("Sync Workspace Creator from Manifest")
    print("=" * 60)

    # Load manifest
    if not MANIFEST_PATH.exists():
        print(f"Error: Manifest not found at {MANIFEST_PATH}")
        print("Run fetch_manifest.py first.")
        sys.exit(1)

    manifest = load_manifest()
    meta = manifest.get("_meta", {})
    print(f"Manifest: {MANIFEST_PATH.name}")
    print(f"Generated: {meta.get('generated_at', 'unknown')}")
    print(f"Sheets: {len(manifest.get('sheets', {}))}")
    print(f"Folders: {len(manifest.get('folders', {}))}")

    # Build new definitions from manifest
    folder_structure = build_folder_structure(manifest)
    sheet_definitions = build_sheet_definitions(manifest)

    # Read current create_workspace.py
    if not CREATOR_PATH.exists():
        print(f"Error: {CREATOR_PATH} not found")
        sys.exit(1)

    source = CREATOR_PATH.read_text(encoding="utf-8")

    # Parse existing definitions for diff
    existing_sheets = parse_existing_sheets(source)

    # Show diff
    print("\nChanges detected:")
    print(diff_summary(existing_sheets, sheet_definitions))

    # Generate new source sections
    new_folders_src = format_folder_structure(folder_structure)
    new_sheets_src = format_sheet_definitions(sheet_definitions, folder_structure)

    if not apply_mode:
        print(f"\nDry run complete. To apply changes, run:")
        print(f"  python sync_workspace_creator.py --apply")
        return

    # Apply changes
    updated_source = update_creator_source(source, new_folders_src, new_sheets_src)

    CREATOR_PATH.write_text(updated_source, encoding="utf-8")
    print(f"\n✅ Updated {CREATOR_PATH}")
    print(f"   Folders: {len(folder_structure)}")
    print(f"   Sheets: {len(sheet_definitions)}")

    # Count total columns
    total_cols = sum(len(s["columns"]) for s in sheet_definitions.values())
    print(f"   Total columns: {total_cols}")


if __name__ == "__main__":
    main()
