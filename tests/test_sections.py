from app.sections import split_sections


def test_splits_at_level_two_headings():
    sections = split_sections("## One\n\nalpha\n\n## Two\n\nbeta")

    assert [(s.index, s.heading, s.body) for s in sections] == [
        (0, "One", "alpha"),
        (1, "Two", "beta"),
    ]


def test_prose_before_the_first_heading_becomes_a_leading_section():
    sections = split_sections("intro paragraph\n\n## One\n\nalpha")

    assert sections[0].heading == ""
    assert sections[0].body == "intro paragraph"
    assert sections[1].heading == "One"


def test_a_body_that_opens_with_a_heading_has_no_empty_lead_in():
    assert len(split_sections("## One\n\nalpha")) == 1


def test_subheadings_stay_inside_their_section():
    sections = split_sections("## One\n\nalpha\n\n### Deeper\n\nbeta")

    assert len(sections) == 1
    assert "### Deeper" in sections[0].body


def test_headings_inside_code_fences_are_content():
    sections = split_sections("## One\n\n```\n## not a heading\n```\n\nalpha")

    assert len(sections) == 1
    assert "## not a heading" in sections[0].body


def test_markdown_round_trips_the_heading():
    section = split_sections("## One\n\nalpha")[0]

    assert section.markdown == "## One\n\nalpha"


def test_empty_body_yields_no_sections():
    assert split_sections("") == []
    assert split_sections("\n\n  \n") == []
