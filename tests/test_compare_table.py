"""The comparison guide's table (D4): five tools, and every cell that states a fact names its
source and the day it was read. A cell with no source says so in words (we have not run it, not
compared here, or a date line)."""

from __future__ import annotations

import re

import pytest

from fixtures.web.support import WebEnv


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> str:
    env = WebEnv(tmp_path_factory.mktemp("compare"))
    with env.running() as anon:
        response = anon.get("/guides/readablestem-mathpix-latexml")
        assert response.status_code == 200
        return response.text


def cells(page: str) -> list[list[str]]:
    table = page.split('<table class="compare-table compare-wide">', 1)[1].split("</table>", 1)[0]
    body = table.split("<tbody>", 1)[1]
    return [re.findall(r"<td>(.*?)</td>", row, re.S) for row in body.split("<tr>")[1:]]


def test_five_tools_in_the_header(page):
    head = page.split("<thead>", 1)[1].split("</thead>", 1)[0]
    names = re.findall(r'<th scope="col">([^<]+)</th>', head)
    assert names == ["Topic", "ReadableSTEM", "Mathpix", "Aelira", "Chatbots", "LaTeXML"]


def test_every_cell_has_a_source_and_a_date_or_says_why_not(page):
    rows = cells(page)
    assert len(rows) >= 8 and all(len(row) == 5 for row in rows)
    allowed = (
        "We have not run it",
        "Not compared here",
        "Beta; the live service",
        "Prices and documentation as read on",
        "Beta software, as its site says on",
        "Run recorded on",
    )
    for row in rows:
        for cell in row:
            if 'class="source-note"' in cell:
                assert re.search(
                    r'<a href="[^"]+">[^<]+</a>, read <time datetime="20\d\d-\d\d-\d\d">', cell
                )
            else:
                assert any(phrase in cell for phrase in allowed), cell


def test_no_cell_claims_conformance_and_accuracy_appears_only_where_measured(page):
    text = " ".join(" ".join(row) for row in cells(page)).lower()
    for word in ("conforms", "compliant", "certified", "guarantee"):
        assert word not in text, word
    # The only accuracy numbers are ReadableSTEM's and the chatbot recipe's, from PROOF.md.
    rows = cells(page)
    accuracy = next(row for row in rows if "50% exactly right" in row[0])
    assert "PROOF.md" in accuracy[0] and "62% exactly right" in accuracy[3]
    for index in (1, 2, 4):
        assert "%" not in re.sub(r"<[^>]+>", "", accuracy[index]).replace("not run", "")
