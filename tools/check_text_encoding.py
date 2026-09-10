"""Detect and repair double-encoded text in authored declarations.

A string that was written as UTF-8 and then read back as Latin-1 or CP1252 comes
out as mojibake: `·` becomes `Â·`, `—` becomes `â€"`. It survives round-trips,
because the damaged form is itself valid text, and JSON hides it behind `\\u00c2`
escapes where it is easy to miss in review.

This mattered here for a plain reason: those strings are *content*. The source
view labels and the coverage strip are read by a person, so a corrupted middle
dot is a visible defect in the workbench, not a cosmetic detail in a file.

Detection is conservative. A string is only reported when its characters all fit
in Latin-1 *and* those bytes decode as valid UTF-8 that differs from the original
— which is exactly the signature of the double encoding and is vanishingly
unlikely to describe text somebody meant to write.

Repair writes the file back with `ensure_ascii=False`, so text is stored as
literal UTF-8 rather than as escapes. Damage is then visible in a diff instead of
hidden behind `\\u00c2`.

Usage
-----
    python tools/check_text_encoding.py            # report, exit non-zero on damage
    python tools/check_text_encoding.py --repair   # repair in place, then report

Exit codes
----------
    0  no double-encoded text found
   18  double-encoded text found (or repaired, when --repair was not given)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKBENCH = HERE.parent

sys.path.insert(0, str(HERE))
from resolve_ui_dependencies import now_utc, sha256_file  # noqa: E402

# Authored and generated declarations that carry human-readable content.
# Generated trees are included so a repaired basis can be proven to have
# propagated, and build output is excluded because it is rebuilt from these.
SEARCH_ROOTS = ["experiences", "profiles", "contracts", "dependencies",
                "fixtures", "providers", "docs", "runtime", "adapters", "tools",
                "tests"]
SKIP_PARTS = {"build", "node_modules", "__pycache__", "evidence"}


# The single-byte codec the text was misread through. CP1252 must be tried, and
# tried first: it is the Windows default, and its 0x80-0x9F range maps to
# characters Latin-1 has no room for — `—` for 0x97, `’` for 0x92, `€` for 0x80.
# A Latin-1-only check silently passes that whole class of damage, which is how
# `×` corrupted to `Ã—` survived the first repair pass here.
MISREAD_CODECS = ("cp1252", "latin-1")


def repaired(text: str) -> str | None:
    """Return the repaired string, or None when the text is not double-encoded."""
    for codec in MISREAD_CODECS:
        try:
            candidate = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate == text:
            continue
        # Repairing must reduce the string: double encoding always adds bytes.
        if len(candidate) >= len(text):
            continue
        return candidate
    return None


def walk(node, pointer: str, out: list):
    if isinstance(node, dict):
        for key, value in node.items():
            walk(value, pointer + "/" + str(key), out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk(value, pointer + "/" + str(index), out)
    elif isinstance(node, str):
        fixed = repaired(node)
        if fixed is not None:
            out.append({"pointer": pointer, "before": node, "after": fixed})


def repair_tree(node):
    if isinstance(node, dict):
        return {key: repair_tree(value) for key, value in node.items()}
    if isinstance(node, list):
        return [repair_tree(value) for value in node]
    if isinstance(node, str):
        fixed = repaired(node)
        return fixed if fixed is not None else node
    return node


def candidate_files() -> list:
    files = []
    for root in SEARCH_ROOTS:
        base = WORKBENCH / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if SKIP_PARTS & set(path.parts):
                continue
            if path.suffix in (".json", ".md", ".js", ".cjs", ".py", ".html", ".css"):
                files.append(path)
    return sorted(files)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=WORKBENCH / "evidence" / "encoding")
    args = parser.parse_args(argv)

    damaged, repaired_files, scanned = [], [], 0

    for path in candidate_files():
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            damaged.append({"path": path.relative_to(WORKBENCH).as_posix(),
                            "kind": "NOT_UTF8", "strings": []})
            continue
        scanned += 1

        if path.suffix == ".json":
            try:
                document = json.loads(raw)
            except json.JSONDecodeError:
                continue
            findings: list = []
            walk(document, "", findings)
            if not findings:
                continue
            record = {"path": path.relative_to(WORKBENCH).as_posix(),
                      "kind": "DOUBLE_ENCODED_JSON_STRING",
                      "count": len(findings),
                      "strings": [{"pointer": f["pointer"],
                                   "before": f["before"][:120],
                                   "after": f["after"][:120]} for f in findings]}
            damaged.append(record)
            if args.repair:
                path.write_text(
                    json.dumps(repair_tree(document), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
                repaired_files.append(record["path"])
        else:
            # Non-JSON files hold text directly; check line by line so a report
            # names something a person can find.
            findings = []
            for number, line in enumerate(raw.splitlines(), start=1):
                fixed = repaired(line)
                if fixed is not None:
                    findings.append({"line": number, "before": line[:120],
                                     "after": fixed[:120]})
            if not findings:
                continue
            record = {"path": path.relative_to(WORKBENCH).as_posix(),
                      "kind": "DOUBLE_ENCODED_TEXT", "count": len(findings),
                      "strings": findings}
            damaged.append(record)
            if args.repair:
                path.write_text("\n".join(
                    (repaired(line) if repaired(line) is not None else line)
                    for line in raw.splitlines()) + "\n", encoding="utf-8")
                repaired_files.append(record["path"])

    receipt = {
        "receiptType": "text-encoding-receipt.v1",
        "checkedAt": now_utc(),
        "statement": "Authored and generated declarations are checked for text that was "
                     "written as UTF-8 and read back as Latin-1 or CP1252. These strings are "
                     "content a person reads, so the damage is a visible defect rather than a "
                     "file-format detail.",
        "mode": "repair" if args.repair else "check",
        "summary": {"filesScanned": scanned, "filesDamaged": len(damaged),
                    "filesRepaired": len(repaired_files),
                    "clean": not damaged},
        "damaged": damaged,
        "repaired": repaired_files,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = args.out_dir / "text-encoding.receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")

    for record in damaged:
        print("  %-64s %s (%d)" % (record["path"], record["kind"], record.get("count", 0)))
        for entry in record["strings"][:3]:
            where = entry.get("pointer") or ("line %s" % entry.get("line"))
            print("      %s" % where)
    print("encoding       %d scanned, %d damaged%s"
          % (scanned, len(damaged),
             ", %d repaired" % len(repaired_files) if args.repair else ""))
    print("receipt        %s" % receipt_path.as_posix())

    if args.repair:
        return 0 if not damaged or repaired_files else 18
    return 0 if not damaged else 18


if __name__ == "__main__":
    raise SystemExit(main())
