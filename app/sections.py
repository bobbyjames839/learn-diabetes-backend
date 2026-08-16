"""Splitting a lesson body into the chunks a reader works through.

A checkpoint question sits between sections, so the reader and the question
generator have to agree on exactly where the boundaries are. That agreement is
this module's job: the backend splits once and ships the sections to the
frontend, so there is no second implementation to drift out of sync.

Pure — no database, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Only a level-2 heading starts a new section. `###` is a subheading inside one,
# and `#` isn't used in lesson bodies (the title lives in its own column).
_H2 = re.compile(r"^##\s+(?!#)(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Section:
    """One readable chunk of a lesson."""

    index: int
    heading: str
    body: str

    @property
    def markdown(self) -> str:
        """The section as it should render, heading included."""
        if not self.heading:
            return self.body
        return f"## {self.heading}\n\n{self.body}" if self.body else f"## {self.heading}"


def split_sections(body: str) -> list[Section]:
    """Split markdown at its level-2 headings.

    Any prose before the first heading becomes an untitled leading section, so
    nothing is silently dropped. Headings inside fenced code blocks are content,
    not boundaries.
    """
    heading = ""
    buffer: list[str] = []
    sections: list[Section] = []
    in_fence = False
    fence_marker = ""

    def flush() -> None:
        text = "\n".join(buffer).strip()
        # An empty lead-in (the usual case — bodies start with a heading) is not
        # a section, but an empty *titled* section still is.
        if not text and not heading:
            return
        sections.append(Section(index=len(sections), heading=heading, body=text))

    for line in body.splitlines():
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False

        match = None if in_fence else _H2.match(line)
        if match:
            flush()
            heading = match.group(1)
            buffer = []
        else:
            buffer.append(line)

    flush()
    return sections
