"""Build/check the SF LectureText 2.0.0 handoff; never edit or assess meaning.

Python standard library only. No SF Publisher imports or controller operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


SOURCE_DEFAULTS = {"substantive": True, "source_uri": None, "start_ms": None,
                   "end_ms": None, "page": None, "slide": None, "locator": None}
PLACEMENT_DEFAULTS = {"role": "paragraph", "timestamp_range": None,
                      "source_page": None, "source_slide": None,
                      "visual_path": None, "visual_sha256": None, "caption": None,
                      "visual_kind": None, "keep_together": False}
ASSERTIONS = {"complete_source_accounting": True, "direct_perspective_tags": True,
              "semantic_review_required": True}
DRAFT_FIELDS = {"folder_id", "title", "blocks", "transformation_ledger", "structure"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def unique(rows, key):
    require(isinstance(rows, list) and bool(rows), f"{key}: expected nonempty array")
    ids = [row.get(key) for row in rows]
    require(all(nonempty(item) for item in ids), f"{key}: empty identifier")
    require(len(set(ids)) == len(ids), f"{key}: duplicate identifier")
    return ids


def normalize_sources(rows):
    require(isinstance(rows, list) and bool(rows), "No source blocks")
    result = []
    for raw in rows:
        require(isinstance(raw, dict), "Source block must be an object")
        require({"source_block_id", "text"} <= raw.keys(), "Incomplete source block")
        require(not raw.keys() - (SOURCE_DEFAULTS.keys() | {"source_block_id", "text"}),
                "Unknown source fields")
        item = {**SOURCE_DEFAULTS, **raw}
        require(nonempty(item["text"]), "Empty source text")
        require(type(item["substantive"]) is bool, "substantive must be Boolean")
        for name in ("start_ms", "end_ms", "page", "slide"):
            value = item[name]
            require(value is None or (type(value) is int and value >= (1 if name in {"page", "slide"} else 0)),
                    f"Invalid source {name}")
        if item["end_ms"] is not None:
            require(item["start_ms"] is not None and item["end_ms"] >= item["start_ms"],
                    "Invalid time range")
        require(any(nonempty(item[key]) for key in ("source_uri", "locator")),
                "Source needs source_uri or a verifiable locator")
        result.append(item)
    unique(result, "source_block_id")
    return result


def normalize_structure(raw, block_ids):
    require(isinstance(raw, dict) and set(raw) == {"sections", "topics", "placements"},
            "Invalid lecture structure")
    structure = json.loads(json.dumps(raw))
    sections = structure["sections"]
    topics = structure["topics"]
    section_ids = unique(sections, "section_id")
    topic_ids = unique(topics, "topic_id")
    for row in sections:
        require(set(row) == {"section_id", "number", "title"} and
                nonempty(row["number"]) and nonempty(row["title"]), "Invalid section")
    for row in topics:
        require(set(row) == {"topic_id", "section_id", "title"} and
                row["section_id"] in section_ids and nonempty(row["title"]), "Invalid topic")
    required = {"text_block_id", "section_id", "topic_id"}
    placements = structure["placements"]
    require(unique(placements, "text_block_id") == block_ids,
            "Placements must reference every text block exactly once in order")
    by_topic = {row["topic_id"]: row for row in topics}
    for row in placements:
        require(required <= row.keys() and not row.keys() - (required | PLACEMENT_DEFAULTS.keys()),
                "Unknown or incomplete placement")
        for key, value in PLACEMENT_DEFAULTS.items():
            row.setdefault(key, value)
        require(row["topic_id"] in by_topic and row["section_id"] == by_topic[row["topic_id"]]["section_id"],
                "Placement refers to an unknown or mismatched topic/section")
        require(row["role"] in {"paragraph", "callout", "summary"}, "Text stage cannot introduce visuals")
        require(all(row[key] is None for key in ("visual_path", "visual_sha256", "caption", "visual_kind")),
                "Visual selection belongs to the PDF stage")
        require(type(row["keep_together"]) is bool, "keep_together must be Boolean")
        stamp = row["timestamp_range"]
        require(stamp is None or (isinstance(stamp, str) and re.fullmatch(
            r"\d{2}:\d{2}:\d{2}(?:\.\d{3})?\s*[-–—]\s*\d{2}:\d{2}:\d{2}(?:\.\d{3})?", stamp)),
            "Invalid timestamp_range; leave unknown time null")
    # Topics and sections must form contiguous groups in the declared order.
    for key, expected in (("topic_id", topic_ids), ("section_id", section_ids)):
        groups = []
        for row in placements:
            if not groups or groups[-1] != row[key]:
                groups.append(row[key])
        require(groups == expected, f"Noncontiguous, reordered or empty {key} groups")
    return structure


def build(source_rows, draft):
    sources = normalize_sources(source_rows)
    require(isinstance(draft, dict) and set(draft) == DRAFT_FIELDS, "Invalid draft fields")
    require(nonempty(draft["folder_id"]) and nonempty(draft["title"]), "Missing folder ID or exact title")
    blocks = draft["blocks"]
    block_ids = unique(blocks, "text_block_id")
    anchored = []
    for block in blocks:
        require(set(block) == {"text_block_id", "text", "source_block_ids", "perspective"}, "Invalid text block fields")
        require(nonempty(block["text"]), "Empty edited block")
        require(block["perspective"] == "lecturer_direct", "Perspective must remain lecturer_direct")
        require(isinstance(block["source_block_ids"], list) and bool(block["source_block_ids"]), "Missing source anchors")
        anchored.extend(block["source_block_ids"])
    by_source = {item["source_block_id"]: item for item in sources}
    require(all(nonempty(anchor) and anchor in by_source for anchor in anchored), "Unknown source anchor")
    require(len(set(anchored)) == len(anchored), "Duplicate source anchor")
    ledger = draft["transformation_ledger"]
    require(isinstance(ledger, list), "Invalid transformation ledger")
    removed = []
    for row in ledger:
        require(set(row) == {"source_block_id", "disposition", "reason"}, "Invalid disposition fields")
        anchor = row["source_block_id"]
        require(anchor in by_source and anchor not in removed, "Unknown or repeated disposition")
        require(row["disposition"] == "removed_nonsemantic" and nonempty(row["reason"]), "Unjustified deletion")
        require(by_source[anchor]["substantive"] is False, "Cannot remove substantive content")
        removed.append(anchor)
    require(not set(anchored) & set(removed), "Source both included and removed")
    expected = [item["source_block_id"] for item in sources if item["source_block_id"] not in removed]
    require(anchored == expected, "Missing, repeated, or reordered source blocks")
    structure = normalize_structure(draft["structure"], block_ids)
    locators = {block["text_block_id"]: [
        {key: value for key, value in by_source[anchor].items() if key not in {"text", "substantive"}}
        for anchor in block["source_block_ids"]] for block in blocks}
    document = {"schema_version": "2.0.0", "folder_id": draft["folder_id"],
                "source_blocks_hash": digest(sources), "blocks": blocks,
                "transformation_ledger": ledger, "assertions": dict(ASSERTIONS),
                "title": draft["title"], "structure": structure, "source_locators": locators}
    document["content_hash"] = digest(document)
    return document, sources


def check(sources, document):
    require(isinstance(document, dict), "Lecture text must be an object")
    require(DRAFT_FIELDS <= document.keys(), "Incomplete lecture text")
    rebuilt, _ = build(sources, {key: document[key] for key in DRAFT_FIELDS})
    require(rebuilt == document, "Sealed handoff differs from source, structure, or content hash")
    return {"status": "STRUCTURE_VALIDATED", "source_blocks": len(sources),
            "text_blocks": len(document["blocks"]), "content_hash": document["content_hash"],
            "semantic_review": "NOT_EVALUATED_BY_SCRIPT"}


def time_label(milliseconds):
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" + (f".{ms:03}" if ms else "")


def to_markdown(document):
    sections = {row["section_id"]: row for row in document["structure"]["sections"]}
    topics = {row["topic_id"]: row for row in document["structure"]["topics"]}
    blocks = {row["text_block_id"]: row for row in document["blocks"]}
    lines = [f"# {document['title']}", ""]
    last_section = last_topic = None
    for placement in document["structure"]["placements"]:
        section_id, topic_id = placement["section_id"], placement["topic_id"]
        if section_id != last_section:
            section = sections[section_id]
            lines.extend([f"## {section['number']}. {section['title']}", ""])
            last_section = section_id
        if topic_id != last_topic:
            lines.extend([f"### {topics[topic_id]['title']}", ""])
            last_topic = topic_id
        block = blocks[placement["text_block_id"]]
        anchors = document["source_locators"][block["text_block_id"]]
        metadata = json.dumps({"text_block_id": block["text_block_id"], "sources": anchors}, ensure_ascii=False)
        lines.extend(["<!-- " + metadata.replace("--", "\\u002d\\u002d") + " -->", ""])
        stamp = placement["timestamp_range"]
        if stamp:
            lines.extend([f"**{stamp}**", ""])
        else:
            # A label stays local to its file. Never invent a merged timeline.
            for anchor in anchors:
                if anchor["start_ms"] is not None:
                    label = time_label(anchor["start_ms"])
                    if anchor["end_ms"] is not None:
                        label += "–" + time_label(anchor["end_ms"])
                    lines.extend([f"**{label}**", ""])
        lines.extend([block["text"], ""])
    return "\n".join(lines)


def write_package(target, sources, document):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    for name, value in (("source-blocks.json", sources), ("lecture-text.json", document)):
        (target / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "lecture-text.md").write_text(to_markdown(document), encoding="utf-8")


def checked_file(record):
    require(isinstance(record, dict) and nonempty(record.get("path")), "Missing file reference")
    path = Path(record["path"])
    require(path.is_absolute(), "File references must be absolute")
    expected = record.get("sha256")
    require(isinstance(expected, str) and re.fullmatch(r"[0-9A-Fa-f]{64}", expected), "Invalid file SHA-256")
    require(path.is_file(), f"Missing file: {path}")
    require(hashlib.sha256(path.read_bytes()).hexdigest().upper() == expected.upper(), f"Changed file: {path}")
    return path.resolve()


def decision_rows(rows, document, source_ids):
    require(isinstance(rows, list), "decisions must be an array")
    indexed = {}
    blocks = {b["text_block_id"]: b["text"] for b in document["blocks"]}
    pending = []
    for row in rows:
        require(isinstance(row, dict) and nonempty(row.get("id")), "Invalid decision")
        require(row["id"] not in indexed, "Duplicate decision ID")
        require(row.get("decision") in {"proposed", "correct", "keep"}, "Unknown decision state")
        require(row.get("execution") in {"pending", "applied", "verified"}, "Unknown execution state")
        require(nonempty(row.get("basis")), "Decision requires its recorded basis")
        anchors = row.get("source_block_ids")
        require(isinstance(anchors, list) and all(a in source_ids for a in anchors), "Decision has unknown source IDs")
        targets = row.get("targets")
        require(isinstance(targets, list) and bool(targets), "Decision requires targets")
        if row["decision"] == "proposed":
            require(row["execution"] == "pending", "A proposal cannot be recorded as applied")
        if row["execution"] == "pending":
            pending.append(row["id"])
        elif row["decision"] == "keep":
            require(row["execution"] == "verified", "A keep decision must be verified")
        elif row["decision"] == "correct":
            require(row["execution"] == "applied", "A correction must be applied")
        for target in targets:
            layer = target.get("layer")
            require(layer in {"text", "title"}, "Text package decision layer must be text or title")
            if layer == "text":
                require(target.get("id") in blocks, "Decision target no longer exists")
                value = blocks[target["id"]]
            else:
                value = document["title"]
            require(nonempty(target.get("expected")) and type(target.get("count")) is int and target["count"] >= 1,
                    "Decision requires protected text and positive occurrence count")
            if row["execution"] != "pending":
                require(value.count(target["expected"]) == target["count"], f"Decision not preserved: {row['id']}")
        indexed[row["id"]] = row
    return indexed, pending


def check_package(review_path, previous_review=None):
    """Read-only checks. Recorded human review/authority are not authenticated."""
    review_path = Path(review_path)
    review = read_json(review_path)
    require(review.get("schema_version") == "1.0", "Unsupported text review format")
    artifacts = review.get("artifacts")
    require(isinstance(artifacts, dict) and set(artifacts) == {"sources", "lecture", "markdown", "source_manifest", "review"},
            "Incomplete text package artifacts")
    files = {key: checked_file(value) for key, value in artifacts.items()}
    sources, document = read_json(files["sources"]), read_json(files["lecture"])
    receipt = check(sources, document)
    require(files["markdown"].read_text(encoding="utf-8") == to_markdown(document), "Markdown differs from generated lecture text")
    manifest = read_json(files["source_manifest"])
    source_files = manifest.get("files")
    require(isinstance(source_files, list) and bool(source_files), "Source manifest is empty")
    paths = [checked_file(row) for row in source_files]
    require(len(set(paths)) == len(paths), "Repeated source file")
    require([row.get("order") for row in source_files] == list(range(1, len(paths) + 1)), "Invalid source file order")
    require(all(nonempty(row.get("extraction")) for row in source_files), "Missing extraction method")
    for source in sources:
        uri = source.get("source_uri")
        require(nonempty(uri) and Path(uri).is_absolute() and Path(uri).resolve() in paths,
                "Source block is not bound to a file in the source manifest")
    source_ids = [row["source_block_id"] for row in sources]
    semantic = review.get("semantic_review")
    require(isinstance(semantic, dict) and semantic.get("status") in {"COMPLETED", "INCOMPLETE", "NOT_PERFORMED"},
            "Missing semantic review status")
    covered = semantic.get("covered_source_ids")
    require(isinstance(covered, list) and len(set(covered)) == len(covered) and set(covered) <= set(source_ids),
            "Invalid semantic review coverage")
    require(isinstance(semantic.get("open_issues"), list), "Missing open issue list")
    if semantic["status"] == "COMPLETED":
        require(set(covered) == set(source_ids), "Completed review has incomplete recorded coverage")
    decisions, pending = decision_rows(review.get("decisions"), document, set(source_ids))
    previous_hash = None
    if previous_review is not None:
        previous_review = Path(previous_review)
        previous_hash = hashlib.sha256(previous_review.read_bytes()).hexdigest().upper()
        old_rows = read_json(previous_review).get("decisions")
        require(isinstance(old_rows, list), "Previous decision registry is missing")
        seen = set()
        for old in old_rows:
            identifier = old.get("id")
            require(nonempty(identifier) and identifier not in seen, "Invalid previous decision ID")
            seen.add(identifier)
            require(identifier in decisions, f"Previous decision omitted: {identifier}")
            new = decisions[identifier]
            keys = ("decision", "targets", "source_block_ids")
            if any(new.get(key) != old.get(key) for key in keys):
                require(new.get("supersedes") == digest(old) and new["basis"] != old.get("basis"),
                        f"Changed decision requires explicit supersedes hash and new basis: {identifier}")
    removed = {row["source_block_id"] for row in document["transformation_ledger"]}
    count = lambda text: len(re.findall(r"\S+", text))
    return {**receipt, "status": "PACKAGE_VALIDATED", "review_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest().upper(),
            "lecture_sha256": artifacts["lecture"]["sha256"].upper(), "source_files": len(paths),
            "decisions": len(decisions), "pending_decisions": pending,
            "previous_review_sha256": previous_hash, "history_check": "CHECKED" if previous_hash else "NOT_REQUESTED",
            "recorded_semantic_status": semantic["status"], "open_issues": semantic["open_issues"],
            "decision_authority": "RECORDED_NOT_AUTHENTICATED",
            "word_counts": {"method": "nonempty whitespace-separated tokens; text fields only",
                            "source": sum(count(s["text"]) for s in sources),
                            "removed_noise": sum(count(s["text"]) for s in sources if s["source_block_id"] in removed),
                            "lecture": sum(count(b["text"]) for b in document["blocks"])}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--sources", type=Path, required=True)
    build_parser.add_argument("--draft", type=Path, required=True)
    build_parser.add_argument("--out", type=Path, required=True, help="New output directory; must not exist")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--sources", type=Path, required=True)
    check_parser.add_argument("--lecture", type=Path, required=True)
    package_parser = commands.add_parser("check-package")
    package_parser.add_argument("--review", type=Path, required=True)
    package_parser.add_argument("--previous-review", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check-package":
            receipt = check_package(args.review, args.previous_review)
        else:
            sources = read_json(args.sources)
            if args.command == "build":
                document, sources = build(sources, read_json(args.draft))
                receipt = check(sources, document)
                write_package(args.out, sources, document)
            else:
                receipt = check(sources, read_json(args.lecture))
        print(json.dumps(receipt, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
