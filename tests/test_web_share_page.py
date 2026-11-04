"""The sharing page's words and parts (templates/share.html, routes/sharing.py): sign-in
wording from the providers this server has, the address rule, the deletion time beside the
address, the role rows and the Copy link button."""

from __future__ import annotations

import re

import pytest

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.web.routes.sharing import sign_in_words


@pytest.mark.parametrize(
    ("google", "microsoft", "email_link", "words"),
    [
        (True, False, False, "Google"),
        (True, True, False, "Google or Microsoft"),
        (True, True, True, "Google, Microsoft or an emailed sign-in link"),
        (False, False, True, "an emailed sign-in link"),
        (False, False, False, ""),
    ],
)
def test_sign_in_words_name_only_what_is_on(google, microsoft, email_link, words):
    assert sign_in_words(google=google, microsoft=microsoft, email_link=email_link) == words


def share_page(tmp_path, **overrides) -> tuple[str, str]:
    env = WebEnv(tmp_path, **overrides)
    with env.running() as owner:
        env.allow(ALICE)
        login(owner, ALICE)
        job_id = job_id_from(upload(owner, make_pdf(2)))
        assert env.run_worker() == job_id
        return owner.get(f"/jobs/{job_id}/share").text, job_id


def hint_of(page: str) -> str:
    match = re.search(r'id="share-email-hint">(.*?)</p>', page, re.S)
    assert match
    return " ".join(match.group(1).split())


def test_the_address_hint_names_no_provider_that_is_not_set_up(tmp_path):
    page, _ = share_page(tmp_path, google_client_id="id", google_client_secret="secret")
    assert hint_of(page) == "The address they sign in with (Google). Nothing is sent to it."
    assert "Microsoft" not in page


def test_the_address_hint_names_microsoft_when_it_is_set_up(tmp_path):
    page, _ = share_page(
        tmp_path,
        google_client_id="id",
        google_client_secret="secret",
        ms_client_id="m",
        ms_client_secret="s",
        ms_allowed_tenants=["11111111-2222-3333-4444-555555555555"],
    )
    assert "(Google or Microsoft)" in hint_of(page)


def test_the_rule_says_the_same_address_and_that_case_does_not_matter(tmp_path):
    page, _ = share_page(tmp_path)
    text = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert "exactly as it is written" not in text
    assert "Letter case does not matter, but it has to be the same address" in text
    assert "an alias" in text


def test_the_deletion_time_and_a_way_to_keep_it_longer_sit_by_the_address(tmp_path):
    page, job_id = share_page(tmp_path)
    block = page[page.index('id="address-heading"') :]
    assert 'id="share-expiry"' in block
    assert "deleted automatically on" in block and '<time class="local-time"' in block
    assert f'href="/jobs/{job_id}#delete-heading">Keep it longer</a>' in block


def test_the_roles_are_native_radios_inside_whole_row_labels(tmp_path):
    page, _ = share_page(tmp_path)
    rows = re.findall(r'<label class="choice-row" for="(share-role-\w+)">(.*?)</label>', page, re.S)
    assert [row_id for row_id, _ in rows] == ["share-role-view", "share-role-review"]
    for row_id, inner in rows:
        assert f'<input type="radio" id="{row_id}" name="role"' in inner
        assert "choice-name" in inner and "choice-help" in inner  # name and what it allows
    assert "checked" in rows[0][1] and "checked" not in rows[1][1]  # View by default


def test_remove_is_a_danger_button_and_copy_link_is_hidden_until_script(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as owner:
        env.allow(ALICE)
        login(owner, ALICE)
        job_id = job_id_from(upload(owner, make_pdf(2)))
        assert env.run_worker() == job_id
        from test_job_shares import page_token  # the CSRF token of a page

        owner.post(
            f"/jobs/{job_id}/share",
            data={
                "csrf_token": page_token(owner, f"/jobs/{job_id}/share"),
                "email": "carol@example.org",
                "role": "view",
            },
        )
        page = owner.get(f"/jobs/{job_id}/share").text
    assert re.search(r'class="button-danger">Remove carol@example\.org<', page)
    assert (
        'data-copy-target="share-address" data-copy-state="share-copy-state" hidden>Copy link'
        in page
    )
    assert 'id="share-address" readonly value=' in page  # still selectable without script
    assert 'role="status"' in page and "/static/copy-link." in page
