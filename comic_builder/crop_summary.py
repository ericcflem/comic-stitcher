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

    lines = []
    smaller = []  # Screenshots of a page size other than their screenshot size's main one.
    for (canvas, width), indexes in sorted(groups.items(), key=lambda item: (item[0][0], -len(item[1]))):
        main = max(len(members) for (c, _), members in groups.items() if c == canvas)
        singles = sum(proposals[i].kind == "single" for i in indexes)
        spreads = len(indexes) - singles
        spread_size = f"{width * 2} × {canvas[1]}"
        if not spreads:
            kinds = "all single pages" if singles > 1 else "a single page"
        elif not singles:
            kinds = f"all two-page spreads at {spread_size}" if spreads > 1 else f"a two-page spread at {spread_size}"
        else:
            kinds = f"{_plural(singles, 'single page')} and {_plural(spreads, 'two-page spread')} at {spread_size}"
        heading = f"{width} × {canvas[1]} pages: {_plural(len(indexes), 'screenshot')}, {kinds}"
        if len(indexes) < main:
            smaller.extend(indexes)
            heading += f": screenshots {screenshot_numbers(indexes)}"
        measured = sum(proposals[i].source == "measured" for i in indexes)
        hidden = len(indexes) - measured
        how = []
        if measured:
            how.append(f"{measured} measured from clear page edges")
        if hidden:
            how.append(f"{hidden} with black borders hiding the edges, fitted to this size")
        placed = [i for i in indexes if getattr(proposals[i], "by_position", False) or
                  getattr(proposals[i], "by_margins", False)]
        detail = "• " + "\n• ".join(how)
        if placed and len(indexes) < main:
            detail += (f" ({screenshot_numbers(placed)} going by the pages around "
                       f"{'it' if len(placed) == 1 else 'them'} and their black borders)")
        lines.append(f"{heading}\n{detail}")

    if own:
        parts = [f"{'screenshot' if len(indexes) == 1 else 'screenshots'} {screenshot_numbers(indexes)} at {w} × {h}"
                 for (w, h), indexes in sorted(own.items(), key=lambda item: item[1][0])]
        lines.append("Covers and other pages measured at their own size: " + "; ".join(parts) + ".")
    for index in wider:
        proposal = proposals[index]
        lines.append(f"Screenshot {index + 1} is wider ({_per_page(proposal)} × {proposal.canvas[1]}) than the "
                     f"book's {proposal.wider_than} × {proposal.canvas[1]} pages, so it may have been scaled down "
                     "to fit the screen. It keeps its own size; retaking it at its full height would give a "
                     "sharper page.")
    if smaller:
        lines.append(f"Two page sizes often means some pages were scaled down to fit the screen. If screenshots "
                     f"{screenshot_numbers(smaller)} were, retaking them at their full height would give sharper "
                     "pages. Otherwise each size is cropped as shown; in Review crop previews you can choose the "
                     "page size for any screenshot, or one size for all.")
    if attention:
        lines.append(f"{_plural(len(attention), 'screenshot')} need{'s' if len(attention) == 1 else ''} your "
                     f"choice in the next step: {screenshot_numbers(attention)}.")
    total = len(proposals)
    heading = (f"{_plural(total, 'screenshot')} measured" if attention
               else f"Ready to crop {_plural(total, 'screenshot')}")
    return heading, lines
