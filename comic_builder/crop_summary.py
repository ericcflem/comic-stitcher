"""The plain-language summary shown after screenshots are measured."""
from __future__ import annotations

from collections import Counter

from .screenshot_geometry import own_size


def screenshot_numbers(indexes) -> str:
    """1-based screenshot numbers, runs of three or more shortened: 24–35, 40, 41."""
    numbers = sorted(index + 1 for index in indexes)
    parts, start = [], 0
    while start < len(numbers):
        end = start
        while end + 1 < len(numbers) and numbers[end + 1] == numbers[end] + 1:
            end += 1
        if end - start >= 2:
            parts.append(f"{numbers[start]}–{numbers[end]}")
        else:
            parts.extend(str(n) for n in numbers[start:end + 1])
        start = end + 1
    return " and ".join(parts) if len(parts) == 2 else ", ".join(parts)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _per_page(proposal) -> int:
    return round((proposal.box[2] - proposal.box[0]) / (2 if proposal.kind == "double" else 1))


def _page_sizes(proposals) -> dict:
    """Each screenshot size's page sizes: the families found, or the one size
    most clearly measured pages share."""
    sizes = {}
    for proposal in proposals:
        if getattr(proposal, "family_widths", ()):
            sizes.setdefault(proposal.canvas, set()).update(proposal.family_widths)
    for canvas in {p.canvas for p in proposals} - set(sizes):
        usable = [p for p in proposals if p.canvas == canvas and p.box is not None and p.kind in ("single", "double")]
        measured = [p for p in usable if p.source == "measured"] or usable
        if measured:
            sizes[canvas] = {Counter(_per_page(p) for p in measured).most_common(1)[0][0]}
    return {canvas: sorted(widths) for canvas, widths in sizes.items()}


def crop_summary(proposals, attention) -> tuple[str, list[str]]:
    """A heading and paragraphs: each page size with how many screenshots use
    it and how it was found, pages at their own size, and what's left to do."""
    sizes = _page_sizes(proposals)
    groups: dict[tuple, list[int]] = {}
    own: dict[tuple, list[int]] = {}
    wider = []
    for index, proposal in enumerate(proposals):
        if index in attention or proposal.box is None or proposal.kind not in ("single", "double"):
            continue
        families = sizes.get(proposal.canvas, [])
        if getattr(proposal, "wider_than", None):
            wider.append(index)
        elif own_size(proposal, families) or not families:
            own.setdefault((_per_page(proposal), proposal.canvas[1]), []).append(index)
        else:
            width = proposal.family_width or min(families, key=lambda w: abs(w - _per_page(proposal)))
            groups.setdefault((proposal.canvas, width), []).append(index)

    def were(count, plural="were", singular="was"):
        return f"{count} {singular if count == 1 else plural}"

    indent = "\u2003\u2003"  # Em spaces: kept as they are in the window's text.
    lines = []
    several = False
    for (canvas, width), indexes in sorted(groups.items(), key=lambda item: (item[0][0], -len(item[1]))):
        main = max(len(members) for (c, _), members in groups.items() if c == canvas)
        several = several or len(indexes) < main
        size = f"{width} × {canvas[1]}"
        measured = sum(proposals[i].source == "measured" for i in indexes)
        placed = [i for i in indexes if len(indexes) < main and proposals[i].source != "measured" and
                  (getattr(proposals[i], "by_position", False) or getattr(proposals[i], "by_margins", False))]
        fitted = len(indexes) - measured - len(placed)
        rows = [f"{_plural(len(indexes), 'screenshot')} {'has' if len(indexes) == 1 else 'have'} pages measuring {size}"]
        if measured:
            rows.append(f"{indent}{were(measured)} measured from clear page edges")
        if fitted:
            rows.append(f"{indent}{were(fitted, 'have', 'has')} black borders but "
                        f"{'fit' if fitted > 1 else 'fits'} in this page size")
        if placed:
            rows.append(f"{indent}{were(len(placed), 'have', 'has')} black borders but "
                        f"{'were' if len(placed) > 1 else 'was'} placed here because of nearby {size} "
                        f"screenshots and {'their' if len(placed) > 1 else 'its'} border widths")
        lines.append("\n".join(rows))

    for (w, h), indexes in sorted({**own, **{(_per_page(proposals[i]), proposals[i].canvas[1]): []
                                             for i in wider}}.items()):
        members = sorted(indexes + [i for i in wider if (_per_page(proposals[i]), proposals[i].canvas[1]) == (w, h)])
        lines.append(f"{_plural(len(members), 'screenshot')} {'was' if len(members) == 1 else 'were'} "
                     f"individually measured at {w} × {h}")
    if several or wider:
        lines.append("Note: Multiple page sizes may mean some pages were scaled down to fit the screen "
                     "where the screenshots were taken.")
    if attention:
        lines.append(f"{_plural(len(attention), 'screenshot')} need{'s' if len(attention) == 1 else ''} your "
                     f"choice in the next step: {screenshot_numbers(attention)}.")
    total = len(proposals)
    heading = (f"{_plural(total, 'screenshot')} measured" if attention
               else f"Ready to crop {_plural(total, 'screenshot')}")
    return heading, lines
