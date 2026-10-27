"""Persist reviewer labels and detector measurements without touching the comic."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .backend import BookPlan, POSSIBLE_MIN_SCORE, join_layout, offered


def report_data(plan: BookPlan) -> dict:
    pages = plan.scan.pages
    approved = plan.approved()
    used = {page for left in approved for page in (left, plan.partner(left))}
    candidates = []
    for index, candidate in enumerate(plan.scan.candidates):
        decision = plan.decisions.get(index)
        if candidate.left in plan.removed_pages or candidate.right in plan.removed_pages:
            status = "removed_page"
        elif decision is not None:
            status = "approved" if decision else "rejected"
        elif candidate.left in used or candidate.right in used:
            status = "overlapped"
        elif not offered(candidate):
            status = "below_cutoff"
        elif plan.deferred_possible and not candidate.likely:
            status = "deferred"
        else:
            status = "pending"
        candidates.append({
            "left_page": candidate.left + 1,
            "right_page": candidate.right + 1,
            "left_file": pages[candidate.left].path.name,
            "right_file": pages[candidate.right].path.name,
            "status": status,
            "joined_in_final_preview": candidate.left in approved and plan.partner(candidate.left) == candidate.right,
            "rotation_clockwise": plan.rotations.get(candidate.left, 0) if candidate.left in approved else 0,
            "likely": candidate.likely if not plan.scan.manual_mode else None,
            "continuity": candidate.continuity if not plan.scan.manual_mode else None,
            "metrics": candidate.metrics,
        })
    counts = {status: sum(c["status"] == status for c in candidates)
              for status in ("approved", "rejected", "overlapped", "below_cutoff", "deferred", "pending")}
    if plan.removed_pages:
        counts["removed_page"] = sum(c["status"] == "removed_page" for c in candidates)
    return {
        "version": 1,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "book_folder": plan.scan.folder.name,
        "page_count": len(pages),
        "output_page_count": len(plan.entries()),
        "removed_files": [pages[i].path.name for i in sorted(plan.removed_pages)],
        "review_mode": "manual" if plan.scan.manual_mode else "suggested",
        "reading_direction": "right-to-left" if plan.scan.right_to_left else "left-to-right",
        "possible_min_score": None if plan.scan.manual_mode else POSSIBLE_MIN_SCORE,
        "review_complete": counts["pending"] == 0 and counts["deferred"] == 0,
        "continued_with_possible_unreviewed": plan.deferred_possible,
        "counts": counts,
        # Spreads sliced with an overlap: what the scan found, what was decided, and
        # the overlap each joined spread ended up with (Seam can change it).
        "overlapping_halves": [
            {"left_page": c["left_page"], "right_page": c["right_page"],
             "repeated_px": c["metrics"]["overlap"], "status": c["status"],
             "final_overlap_px": plan.join_overlaps.get(c["left_page"] - 1, (0, ""))[0]
             if c["joined_in_final_preview"] else None}
            for c in candidates if c["metrics"].get("overlap")],
        # Pairs joined covering a thin border on one inner edge (no repeated strip).
        "covered_borders": [
            {"left_page": c["left_page"], "right_page": c["right_page"], "border_px": layout[0],
             "covered_page": "left" if layout[1] == "right" else "right", "status": c["status"],
             "final_overlap_px": plan.join_overlaps.get(c["left_page"] - 1, (0, ""))[0]
             if c["joined_in_final_preview"] else None}
            for c, candidate in zip(candidates, plan.scan.candidates, strict=True)
            if not c["metrics"].get("overlap") and (layout := join_layout(candidate))],
        "rotated_single_pages": [
            {"file": pages[left].path.name, "rotation_clockwise": degrees}
            for left, degrees in sorted(plan.rotations.items()) if left not in approved
        ],
        "page_order": plan.page_order,
        "manual_joins": [{"left_file": pages[left].path.name,
                          "right_file": pages[plan.partner(left)].path.name,
                          "rotation_clockwise": plan.rotations.get(left, 0)}
                         for left in sorted(plan.manual_pairs)],
        "candidates": candidates,
    }


def save_report(plan: BookPlan, destination: Path) -> None:
    """An interrupted write leaves the last complete report available."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(report_data(plan), file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
