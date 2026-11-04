"""The public status page says when it was checked, what the AI service line is based on,
and offers a way to check again (templates/status.html)."""

from __future__ import annotations

import re

from fixtures.web.support import WebEnv


def test_the_status_page_says_when_it_was_checked_and_links_to_check_again(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        response = client.get("/status")
    page = response.text
    assert response.status_code == 200
    checked = re.search(
        r'id="status-checked">Checked at <time class="local-time" datetime="(.*?)"', page
    )
    assert checked and checked.group(1).endswith("Z")
    assert '<a href="/status">Check again</a>' in page
    window = " ".join(re.sub(r"<[^>]+>", " ", page[page.index('id="status-window"') :]).split())
    assert "requests to the model in the last 10 minutes" in window
    assert "does not test that the service is up" in window
    assert "This page does not update by itself" in window
    assert "state right now" not in page
