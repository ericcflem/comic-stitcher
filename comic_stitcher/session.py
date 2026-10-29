"""Atomic, source-checked review checkpoints separate from analysis reports."""
from __future__ import annotations

import hashlib
import json
import copy
import os
from pathlib import Path

from .backend import BookPlan, Candidate, Page, Scan
from .detector import collect_images


def session_path(source: Path, app_folder: Path) -> Path:
    key = hashlib.sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    return app_folder / "sessions" / f"{key}.json"


def clear_session(source: Path, app_folder: Path) -> None:
    target = session_path(source, app_folder)
    target.unlink(missing_ok=True)
    target.with_suffix(".scan.json").unlink(missing_ok=True)


def _write_json(target: Path, data: dict) -> None:
    temporary = target.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _source_identity(source: Path, scan: Scan) -> dict:
    if source.is_file():
        stat = source.stat()
        return {"path": str(source.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return {"path": str(source.resolve()), "pages": [
        {"name": p.name, "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
        for p in collect_images(source)]}


def _valid_source(source: Path, identity: dict) -> bool:
    if identity.get("path") != str(source.resolve()) or not source.exists():
        return False
    if source.is_file():
        stat = source.stat()
        return (stat.st_size, stat.st_mtime_ns) == (identity.get("size"), identity.get("mtime_ns"))
    pages = collect_images(source)
    expected = identity.get("pages")
    return (isinstance(expected, list) and len(expected) == len(pages) and
            all(p.name == old["name"] and p.stat().st_size == old["size"] and
                p.stat().st_mtime_ns == old["mtime_ns"] for p, old in zip(pages, expected, strict=True)))


def save_session(source: Path, app_folder: Path, plan: BookPlan, *,
                 possible_started: bool, stage: str, preview_index: int,
                 pdf_options: dict | None = None) -> None:
    """Replace the last complete checkpoint, never append partial decisions."""
    target = session_path(source, app_folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "identity": _source_identity(source, plan.scan),
            "decisions": {str(i): yes for i, yes in plan.decisions.items()},
            "history": [h if isinstance(h, int) else {str(i): yes for i, yes in h.items()}
                        for h in plan.history],
            "manual_mode": plan.scan.manual_mode,
            "right_to_left": plan.scan.right_to_left,
            "source_order_reversed": plan.scan.right_to_left,
            "manual_pairs": sorted(plan.manual_pairs),
            "preview_history": plan.preview_history,
            "page_order": plan.page_order,
            "manual_rights": {str(a): b for a, b in plan.manual_rights.items()},
            "joined_rotations": {str(a): list(turns) for a, turns in plan.joined_rotations.items()},
            "join_overlaps": {str(a): [pixels, top] for a, (pixels, top) in plan.join_overlaps.items()
                              if a in plan.approved()},
            "removed_pages": sorted(plan.removed_pages),
            "rotations": {str(left): degrees for left, degrees in plan.rotations.items()},
            "deferred_possible": plan.deferred_possible,
            "centering": {str(i): list(value) for i, value in plan.centering.items()},
            "centering_measured": plan.centering_measured,
            "center_art": plan.center_art,
            "center_skipped": sorted(plan.center_skipped),
            "centering_offered": plan.centering_offered,
            "comicinfo_xml": plan.comicinfo_xml,
            "include_comicinfo": plan.include_comicinfo,
            "possible_started": possible_started,
            "stage": stage, "preview_index": preview_index,
            "pdf_options": pdf_options or {},
            "base_candidate_count": plan.base_candidate_count,
            "candidates": [[c.left, c.right, c.likely, c.continuity]
                           for c in plan.scan.candidates]}
    if source.is_dir() and not target.with_suffix(".scan.json").exists():
        scan = plan.scan
        scan_data = {
            "pages": [{"name": p.path.name, "index": p.index, "group": p.group,
                       "width": p.width, "height": p.height, "size": p.size,
                       "mtime_ns": p.mtime_ns} for p in scan.pages],
            "candidates": [{"left": c.left, "right": c.right,
                            "continuity": c.continuity, "likely": c.likely,
                            "metrics": c.metrics} for c in scan.candidates],
            "number_gaps_detected": scan.number_gaps_detected,
            "numbering_assumed": scan.numbering_assumed,
            "manual_mode": scan.manual_mode,
            "right_to_left": scan.right_to_left,
            "screenshot_stage": str(scan.folder) if scan.screenshot_source else None,
            "flagged_pages": sorted(scan.flagged_pages),
            "page_notes": [[list(pages), text] for pages, text in scan.page_notes]}
        _write_json(target.with_suffix(".scan.json"), scan_data)
    _write_json(target, data)


def read_session(source: Path, app_folder: Path) -> dict | None:
    target = session_path(source, app_folder)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if data.get("right_to_left", False) and not data.get("source_order_reversed", False):
            return None  # Earlier RTL checkpoints used a different source sequence.
        if data.get("version") == 1 and _valid_source(source, data["identity"]):
            if source.is_dir():
                data["folder_scan"] = json.loads(target.with_suffix(".scan.json").read_text(encoding="utf-8"))
            return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def cached_folder_scan(source: Path, data: dict) -> Scan:
    """Only call with a checkpoint already checked by read_session."""
    cache = data["folder_scan"]
    if bool(cache.get("manual_mode", False)) != bool(data.get("manual_mode", False)):
        raise ValueError("Saved scan and review mode differ.")
    stage = Path(cache["screenshot_stage"]) if cache.get("screenshot_stage") else source
    pages = [Page(stage / item["name"], item["index"], item["group"],
                  item["width"], item["height"], item["size"], item["mtime_ns"])
             for item in cache["pages"]]
    if any(not p.path.is_file() or
           (p.path.stat().st_size, p.path.stat().st_mtime_ns) != (p.size, p.mtime_ns)
           for p in pages):
        raise ValueError("Staged screenshots are missing or changed; start a new import.")
    candidates = [Candidate(**item) for item in cache["candidates"]]
    return Scan(stage, pages, candidates, cache["number_gaps_detected"],
                cache["numbering_assumed"], output_folder=source if cache.get("screenshot_stage") else None,
                manual_mode=bool(cache.get("manual_mode", False)),
                screenshot_source=source if cache.get("screenshot_stage") else None,
                flagged_pages=set(cache.get("flagged_pages", [])),
                right_to_left=bool(data.get("right_to_left", False)),
                page_notes=[(tuple(int(i) for i in noted), str(text))
                            for noted, text in cache.get("page_notes", [])
                            if all(0 <= int(i) < len(pages) for i in noted)])


def _restore_centering(plan: BookPlan, data: dict) -> None:
    """The art-centering measurements and choices, which older saved reviews lack."""
    pages = len(plan.scan.pages)
    centering = {}
    for key, value in data.get("centering", {}).items():
        index = int(key)
        if (not 0 <= index < pages or not isinstance(value, list) or len(value) != 3
                or any(type(v) is not int for v in value)):
            raise ValueError("Saved page centering is invalid.")
        shift, top, bottom = value
        page = plan.scan.pages[index]
        if not (shift and abs(shift) < page.width and 0 <= top < bottom <= page.height):
            raise ValueError("Saved page centering is invalid.")
        centering[index] = (shift, top, bottom)
    skipped = data.get("center_skipped", [])
    if not isinstance(skipped, list) or any(type(i) is not int or not 0 <= i < pages for i in skipped):
        raise ValueError("Saved page centering is invalid.")
    plan.centering = centering
    plan.centering_measured = bool(data.get("centering_measured", False))
    plan.center_art = bool(data.get("center_art", False))
    plan.center_skipped = set(skipped)
    plan.centering_offered = bool(data.get("centering_offered", False))


def _valid_removal(item: dict, page_count: int) -> bool:
    """A saved page removal: one page, or two at once (a spread, with how it was joined)."""
    def page(value):
        return type(value) is int and 0 <= value < page_count

    def turn(value, allowed=(90, 180, 270)):
        return type(value) is int and value in allowed

    keys = {"removed", "also", "rotation", "also_rotation", "overlap", "turns"}
    return ({"removed"} <= set(item) <= keys and page(item["removed"]) and
            ("also" not in item or (page(item["also"]) and item["also"] != item["removed"])) and
            ("rotation" not in item or turn(item["rotation"])) and
            ("also_rotation" not in item or ("also" in item and turn(item["also_rotation"]))) and
            ("overlap" not in item or ("also" in item and isinstance(item["overlap"], list) and
                                       len(item["overlap"]) == 2 and type(item["overlap"][0]) is int and
                                       item["overlap"][0] > 0 and item["overlap"][1] in ("left", "right"))) and
            ("turns" not in item or ("also" in item and isinstance(item["turns"], list) and
                                     len(item["turns"]) == 2 and
                                     all(turn(t, (0, 90, 180, 270)) for t in item["turns"]))))


def restore_plan(scan: Scan, data: dict, *, restore_undo: bool = True) -> BookPlan:
    if scan.right_to_left != bool(data.get("right_to_left", False)):
        raise ValueError("The saved reading direction differs from this import.")
    if scan.manual_mode != bool(data.get("manual_mode", False)):
        raise ValueError("The saved review mode differs from this import.")
    signature = [[c.left, c.right, c.likely, c.continuity] for c in scan.candidates]
    if signature != data["candidates"][:len(signature)]:
        raise ValueError("Suggestions have changed; scan and review this comic again.")
    scan = copy.copy(scan)
    scan.candidates = list(scan.candidates)
    for item in data["candidates"][len(signature):]:
        if (len(item) != 4 or any(type(i) is not int or not 0 <= i < len(scan.pages) for i in item[:2])
                or item[0] == item[1] or type(item[2]) is not bool
                or not isinstance(item[3], (int, float)) or not 0 <= item[3] <= 1
                or any((c.left, c.right) == tuple(item[:2]) for c in scan.candidates)):
            raise ValueError("Saved reordered suggestions are invalid.")
        scan.candidates.append(Candidate(item[0], item[1], item[3], item[2]))
    plan = BookPlan(scan)
    base_count = data.get("base_candidate_count", len(signature))
    if type(base_count) is not int or not 0 <= base_count <= len(scan.candidates):
        raise ValueError("Saved original suggestions are invalid.")
    plan.base_candidate_count = base_count
    if "comicinfo_xml" in data:
        from .comicinfo import parse_xml
        if data["comicinfo_xml"] is not None:
            parse_xml(data["comicinfo_xml"])
        plan.comicinfo_xml = data["comicinfo_xml"]
        plan.include_comicinfo = bool(data.get("include_comicinfo", False))
    decisions = {int(i): yes for i, yes in data["decisions"].items()}
    history = []
    for item in data["history"]:
        if isinstance(item, dict) and "removed" in item:
            history.append(item)
        elif scan.manual_mode:
            history.append(int(item))
        else:
            history.append({int(i): yes for i, yes in item.items()})
    manual = set(data["manual_pairs"])
    order = data.get("page_order", list(range(len(scan.pages))))
    rights = {int(a): b for a, b in data.get("manual_rights", {}).items()}
    parts = {int(a): tuple(turns) for a, turns in data.get("joined_rotations", {}).items()}
    if (any(type(i) is not int for i in order) or sorted(order) != list(range(len(scan.pages))) or
        any(a not in manual or type(b) is not int or b < 0 or b >= len(scan.pages) or a == b
            for a, b in rights.items()) or
        any(a not in manual or len(turns) != 2 or any(type(t) is not int or t not in (0, 90, 180, 270) for t in turns)
            for a, turns in parts.items())):
        raise ValueError("Saved page order or component rotations are invalid.")
    plan.page_order, plan.manual_rights, plan.joined_rotations = order, rights, parts
    removed = set(data.get("removed_pages", []))
    rotations = {int(left): degrees for left, degrees in data.get("rotations", {}).items()}
    if (any(i < 0 or i >= len(scan.candidates) or type(yes) is not bool
            for i, yes in decisions.items()) or
        any(i < 0 or i >= len(scan.pages) or rights.get(i, i + 1) >= len(scan.pages) for i in manual) or
        any(type(i) is not int or i < 0 or i >= len(scan.pages) for i in removed) or
        len(removed) >= len(scan.pages) or
        any(i in removed or rights.get(i, i + 1) in removed for i in manual) or
        any(not _valid_removal(item, len(scan.pages))
            if isinstance(item, dict) and "removed" in item else
            (type(item) is not int or item < 0 or item >= len(scan.candidates))
            if scan.manual_mode else
            any(i < 0 or i >= len(scan.candidates) or type(yes) is not bool
                for i, yes in item.items())
            for item in history)):
        raise ValueError("Saved review decisions are invalid.")
    plan.decisions, plan.history, plan.manual_pairs, plan.removed_pages = decisions, history, manual, removed
    used = set()
    for left in plan.approved():
        right = plan.partner(left)
        if left in used or right in used or order.index(right) != order.index(left) + 1:
            raise ValueError("Saved joined pages overlap or are out of order.")
        used.update((left, right))
    rotation_targets = {left for left, _ in plan.entries()} | {candidate.left for candidate in scan.candidates}
    if any(left not in rotation_targets or type(degrees) is not int or degrees not in (90, 180, 270)
           for left, degrees in rotations.items()):
        raise ValueError("Saved page rotations are invalid.")
    plan.rotations = rotations
    overlaps = {int(left): tuple(value) for left, value in data.get("join_overlaps", {}).items()}
    approved = plan.approved()
    if any(left not in approved or len(value) != 2 or type(value[0]) is not int or value[1] not in ("left", "right")
           or not 0 < value[0] < min(scan.pages[left].width, scan.pages[plan.partner(left)].width)
           for left, value in overlaps.items()):
        raise ValueError("Saved spread overlaps are invalid.")
    plan.join_overlaps = overlaps
    plan.deferred_possible = bool(data["deferred_possible"])
    _restore_centering(plan, data)
    if restore_undo:
        snapshots = data.get("preview_history", [])
        if not isinstance(snapshots, list) or len(snapshots) > 100:
            raise ValueError("Saved final-review undo history is invalid.")
        expected = {"decisions", "manual_pairs", "removed_pages", "rotations", "page_order",
                    "manual_rights", "joined_rotations", "_action", "_focus", "_history_length"}
        optional = {"join_overlaps", "center_art", "center_skipped"}  # Not in older saved reviews.
        for saved in snapshots:
            if (not isinstance(saved, dict) or not expected <= set(saved) <= expected | optional or
                    saved["_action"] not in ("page move", "page swap", "rotation", "join", "split", "page removal",
                                             "spread overlap", "centering") or
                    type(saved["_focus"]) is not int or not 0 <= saved["_focus"] < len(scan.pages) or
                    type(saved["_history_length"]) is not int or not 0 <= saved["_history_length"] <= len(history)):
                raise ValueError("Saved final-review undo history is invalid.")
            # Snapshots saved before overlaps existed had none.
            previous = restore_plan(scan, {**data, "join_overlaps": {}, "center_art": False, "center_skipped": [],
                                           **saved,
                                           "history": data["history"][:saved["_history_length"]]},
                                    restore_undo=False)
            plan.preview_history.append(previous.preview_snapshot(saved["_action"], saved["_focus"]))
    return plan
