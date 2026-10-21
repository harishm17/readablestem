"""Open sign-up (`SIGNUP_MODE=open`): who may sign in, the access tiers, the limits of the
open tier, the daily cap on new accounts, and deleting your own account.

The spend pool and its concurrency are in tests/test_web_open_spend.py; the words of the
pages in both modes are in tests/test_web_signup_copy.py; Google and Microsoft sign-in with
open sign-up are in tests/test_web_open_signup_providers.py. Everything is offline: SQLite,
dev login, the fake converter and the offline model client.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import (
    ADMIN,
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    job_id_from,
    login,
    make_pdf,
    make_settings,
    token,
    upload,
)
from readablestem.config import Settings
from readablestem.models.fake import FakeVisionClient
from readablestem.web import access
from readablestem.web.auth import set_allowed, set_blocked, sign_in_by_email
from readablestem.web.database import utc_today
from readablestem.web.db import (
    AuthIdentity,
    Blob,
    Course,
    Event,
    Job,
    LinkedEmail,
    QuotaDay,
    User,
    session_scope,
)
from readablestem.web.quota import (
    rereads_used,
    signups_today,
    try_consume_reread,
    try_take_signup,
)

CAROL = "carol@example.org"
DAVE = "dave@example.org"


def make_env(tmp_path, **overrides) -> WebEnv:
    overrides.setdefault("signup_mode", "open")
    return WebEnv(tmp_path, **overrides)


@pytest.fixture
def open_web(tmp_path):
    env = make_env(tmp_path, open_tier_daily_page_quota=3)
    with env.running() as client:
        env.browser = client
        yield env


@pytest.fixture
def closed_web(tmp_path):
    env = make_env(tmp_path, signup_mode="allowlist")
    with env.running() as client:
        env.browser = client
        yield env


def user_row(env, email: str) -> User | None:
    with session_scope(env.ctx.factory) as session:
        return session.scalar(select(User).where(User.email == email))


def user_count(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.count()).select_from(User)) or 0)


def signups_now(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return signups_today(session, utc_today())


def client_for(env, email: str):
    """A fresh browser, signed in as `email` (dev login)."""
    client = env.client()
    login(client, email)
    return client


# --------------------------------------------------------------------------- settings


def test_the_default_is_the_allowlist_and_the_open_tier_has_its_numbers():
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.signup_mode == "allowlist" and s.signup_open is False
    assert s.open_tier_daily_page_quota == 20
    assert s.open_tier_daily_rereads == 30
    assert s.max_new_accounts_per_day == 100
    assert s.open_tier_daily_spend_cap_usd == 4.0


def test_signup_mode_is_validated_and_forgiving_about_case():
    assert Settings(_env_file=None, signup_mode=" Open ").signup_open is True  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, signup_mode="everyone")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "name",
    ["open_tier_daily_page_quota", "open_tier_daily_spend_cap_usd", "max_new_accounts_per_day"],
)
def test_the_open_tier_numbers_refuse_negative_values(name):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: -1})  # type: ignore[call-arg]


def test_signup_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "open")
    monkeypatch.setenv("OPEN_TIER_DAILY_PAGE_QUOTA", "12")
    monkeypatch.setenv("OPEN_TIER_DAILY_REREADS", "7")
    monkeypatch.setenv("MAX_NEW_ACCOUNTS_PER_DAY", "40")
    monkeypatch.setenv("OPEN_TIER_DAILY_SPEND_CAP_USD", "2.5")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.signup_mode == "open"
    assert (s.open_tier_daily_page_quota, s.open_tier_daily_rereads) == (12, 7)
    assert (s.max_new_accounts_per_day, s.open_tier_daily_spend_cap_usd) == (40, 2.5)


def test_the_limits_check_names_open_signup_settings_that_do_not_fit(tmp_path):
    from readablestem.ops.limits import spend_findings

    texts = [
        str(f)
        for f in spend_findings(
            make_settings(
                tmp_path,
                signup_mode="open",
                daily_spend_cap_usd=2.0,
                per_user_daily_page_quota=10,
                open_tier_daily_page_quota=20,
                open_tier_daily_spend_cap_usd=3.0,
            )
        )
    ]
    assert any(
        "OPEN_TIER_DAILY_SPEND_CAP_USD $3.00 is above DAILY_SPEND_CAP_USD" in t for t in texts
    )
    assert any(
        "OPEN_TIER_DAILY_PAGE_QUOTA 20 is above PER_USER_DAILY_PAGE_QUOTA 10" in t for t in texts
    )
    tiny = make_settings(tmp_path, signup_mode="open", open_tier_daily_spend_cap_usd=0.001)
    assert any("below one call's reservation" in str(f) for f in spend_findings(tiny))
    closed = make_settings(tmp_path, signup_mode="open", open_tier_daily_spend_cap_usd=0)
    assert not any("OPEN_TIER" in str(f) for f in spend_findings(closed))
    quiet = make_settings(tmp_path, signup_mode="allowlist", daily_spend_cap_usd=2.0)
    assert not any("OPEN_TIER" in str(f) for f in spend_findings(quiet))


# --------------------------------------------------------------------------- who gets in


def test_in_allowlist_mode_a_stranger_is_refused_and_nothing_is_stored(closed_web):
    response = login(closed_web.browser, CAROL)
    assert response.status_code == 403
    assert "You are not on the beta list yet" in response.text
    assert user_row(closed_web, CAROL) is None
    assert signups_now(closed_web) == 0


def test_in_open_mode_the_first_sign_in_makes_an_open_tier_account(open_web):
    response = login(open_web.browser, CAROL)
    assert response.status_code == 303 and response.headers["location"] == "/app"
    row = user_row(open_web, CAROL)
    assert row is not None
    assert row.is_allowed and not row.is_admin
    assert row.access_tier == access.OPEN and row.last_login_at is not None
    assert open_web.browser.get("/app").status_code == 200
    assert signups_now(open_web) == 1


def test_signing_in_again_uses_the_same_account_and_counts_no_new_one(open_web):
    login(open_web.browser, CAROL)
    first = user_row(open_web, CAROL)
    users = user_count(open_web)
    assert login(open_web.client(), CAROL).status_code == 303
    second = user_row(open_web, CAROL)
    assert first is not None and second is not None and first.id == second.id
    assert user_count(open_web) == users
    assert signups_now(open_web) == 1


def test_the_dev_login_address_is_normalised_before_an_account_is_made(open_web):
    login(open_web.browser, "  Carol@Example.ORG ")
    assert user_row(open_web, CAROL) is not None


def test_a_person_an_admin_blocked_stays_refused_in_open_mode_and_gets_no_new_row(open_web):
    login(open_web.browser, CAROL)
    with session_scope(open_web.ctx.factory) as session:
        assert set_blocked(session, CAROL, blocked=True) is not None
    before = user_count(open_web)
    response = login(open_web.client(), CAROL)
    assert response.status_code == 403
    assert "This account cannot be used" in response.text
    assert "You are not on the beta list" not in response.text
    assert user_count(open_web) == before
    # The session they already had is over too.
    assert open_web.browser.get("/app").status_code == 303


def test_blocked_is_not_the_same_as_never_invited(tmp_path):
    """Open mode: no row means a new account, a row that is not allowed means a refusal.
    Allowlist mode: both are refused (as before), and neither gets the blocked wording."""
    for mode, expect_account in (("open", True), ("allowlist", False)):
        env = make_env(tmp_path / mode, signup_mode=mode)
        with env.running():
            stranger = login(env.client(), DAVE)
            assert (stranger.status_code == 303) is expect_account
            assert (user_row(env, DAVE) is not None) is expect_account
            env.allow(BOB)
            with session_scope(env.ctx.factory) as session:
                set_blocked(session, BOB, blocked=True)
            refused = login(env.client(), BOB)
            assert refused.status_code == 403
            assert ("This account cannot be used" in refused.text) is (mode == "open")
            row = user_row(env, BOB)
            assert row is not None and not row.is_allowed


def test_an_admin_removed_person_is_refused_in_allowlist_mode_as_before(closed_web):
    closed_web.allow(BOB)
    with session_scope(closed_web.ctx.factory) as session:
        set_allowed(session, BOB, allowed=False)
    response = login(closed_web.client(), BOB)
    assert response.status_code == 403 and "not on the list for this beta" in response.text


def test_a_linked_google_address_signs_the_owner_in_and_makes_no_account(open_web):
    from readablestem.web import linked_emails

    open_web.allow(BOB)
    with session_scope(open_web.ctx.factory) as session:
        linked_emails.link(session, BOB, CAROL)
    before = user_count(open_web)
    assert login(open_web.client(), CAROL).status_code == 303
    assert user_count(open_web) == before
    assert signups_now(open_web) == 0


def test_two_first_sign_ins_of_one_address_at_once_make_one_account(open_web):
    results = []

    def go() -> None:
        results.append(
            sign_in_by_email(open_web.ctx.factory, open_web.settings, "race@example.org", None)
        )

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8 and all(r.user_id for r in results)
    assert len({r.user_id for r in results}) == 1
    assert sum(r.created for r in results) == 1
    assert signups_now(open_web) == 1


# --------------------------------------------------------------------------- tiers


def test_allowlisting_makes_someone_invited_and_so_are_the_seeded_admins(open_web):
    open_web.allow(BOB)
    row = user_row(open_web, BOB)
    assert row is not None and row.access_tier == access.INVITED
    admin = user_row(open_web, ADMIN)  # seeded at start-up
    assert admin is not None and admin.access_tier == access.INVITED


def test_allowlisting_an_open_tier_person_promotes_them_and_unblocking_does_not(open_web):
    login(open_web.browser, CAROL)
    open_web.allow(CAROL)
    promoted = user_row(open_web, CAROL)
    assert promoted is not None and promoted.access_tier == access.INVITED

    login(open_web.client(), DAVE)
    with session_scope(open_web.ctx.factory) as session:
        set_blocked(session, DAVE, blocked=True)
        set_blocked(session, DAVE, blocked=False)
    back = user_row(open_web, DAVE)
    assert back is not None and back.is_allowed and back.access_tier == access.OPEN
    assert login(open_web.client(), DAVE).status_code == 303


def test_blocking_an_admin_takes_the_role_and_unblocking_does_not_give_it_back(open_web):
    with session_scope(open_web.ctx.factory) as session:
        set_blocked(session, ADMIN, blocked=True)
        set_blocked(session, ADMIN, blocked=False)
    row = user_row(open_web, ADMIN)
    assert row is not None and row.is_allowed and not row.is_admin


def test_blocking_an_unknown_address_changes_nothing(open_web):
    with session_scope(open_web.ctx.factory) as session:
        assert set_blocked(session, "nobody@example.org", blocked=True) is None
        assert set_blocked(session, "nobody@example.org", blocked=False) is None
    assert user_row(open_web, "nobody@example.org") is None


def test_the_tier_decides_the_limits():
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        per_user_daily_page_quota=90,
        open_tier_daily_page_quota=20,
        open_tier_daily_rereads=30,
    )
    assert access.page_quota(s, access.INVITED) == 90
    assert access.page_quota(s, access.OPEN) == 20
    assert access.reread_allowance(s, access.INVITED) is None
    assert access.reread_allowance(s, access.OPEN) == 30
    assert access.spend_pool(access.INVITED) is None
    assert access.spend_pool(access.OPEN) == access.POOL_OPEN


# ----------------------------------------------------------------- the daily cap on accounts


def test_when_the_days_accounts_are_taken_new_sign_ups_get_a_calm_page(tmp_path):
    env = make_env(tmp_path, max_new_accounts_per_day=2)
    with env.running():
        assert login(env.client(), CAROL).status_code == 303
        assert login(env.client(), DAVE).status_code == 303
        before = user_count(env)
        full = login(env.client(), "late@example.org")
        assert full.status_code == 503 and full.headers["retry-after"]
        assert "Sign-ups are full for today" in full.text
        assert "Please try again tomorrow" in full.text
        assert 'href="/demo"' in full.text
        assert user_row(env, "late@example.org") is None and user_count(env) == before
        # People who already have an account are not affected.
        assert login(env.client(), CAROL).status_code == 303
        assert signups_now(env) == 2


def test_the_count_starts_again_on_the_next_utc_day(tmp_path, monkeypatch):
    env = make_env(tmp_path, max_new_accounts_per_day=1)
    with env.running():
        assert login(env.client(), CAROL).status_code == 303
        assert login(env.client(), DAVE).status_code == 503
        tomorrow = utc_today() + timedelta(days=1)
        monkeypatch.setattr("readablestem.web.identities.utc_today", lambda: tomorrow)
        assert login(env.client(), DAVE).status_code == 303
        with session_scope(env.ctx.factory) as session:
            assert signups_today(session, tomorrow) == 1
            assert signups_today(session, utc_today()) == 1


def test_zero_accounts_a_day_pauses_sign_up_for_everyone_new(tmp_path):
    env = make_env(tmp_path, max_new_accounts_per_day=0)
    with env.running():
        assert login(env.client(), CAROL).status_code == 503
        env.allow(BOB)
        assert login(env.client(), BOB).status_code == 303


def test_the_account_count_is_one_conditional_update(open_web):
    with session_scope(open_web.ctx.factory) as session:
        assert try_take_signup(session, limit=2, day=utc_today())
        assert try_take_signup(session, limit=2, day=utc_today())
        assert not try_take_signup(session, limit=2, day=utc_today())
        assert signups_today(session, utc_today()) == 2
        assert try_take_signup(session, limit=2, day=utc_today() + timedelta(days=1))


def test_a_count_whose_account_was_not_saved_is_not_spent(open_web):
    before = signups_now(open_web)
    with pytest.raises(RuntimeError), session_scope(open_web.ctx.factory) as session:
        try_take_signup(session, limit=5, day=utc_today())
        raise RuntimeError("the account could not be saved")
    assert signups_now(open_web) == before


def test_many_sign_ups_at_once_never_pass_the_daily_cap(tmp_path):
    env = make_env(tmp_path, max_new_accounts_per_day=5)
    with env.running():
        results = []

        def go(n: int) -> None:
            results.append(
                sign_in_by_email(env.ctx.factory, env.settings, f"user{n}@example.org", None)
            )

        threads = [threading.Thread(target=go, args=(n,)) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(1 for r in results if r.created) == 5
        assert sum(1 for r in results if r.refused == "signups_full") == 15
        assert signups_now(env) == 5


def test_a_per_address_rule_limits_new_accounts_made_from_one_address(tmp_path):
    env = make_env(tmp_path, rate_limits_enabled=True)
    with env.running():
        made = [login(env.client(), f"person{n}@example.org").status_code for n in range(7)]
        assert made == [303] * 5 + [429, 429]
        assert user_row(env, "person5@example.org") is None
        # The rule counts accounts about to be made, not sign-ins: an existing account is
        # not held up by it, and the refused attempts took no place from the day's count.
        assert login(env.client(), "person0@example.org").status_code == 303
        assert signups_now(env) == 5


def test_the_per_address_rule_is_listed_with_the_other_rules():
    from readablestem.web.abuse import RULES

    assert RULES["signup"].limit == 5 and RULES["signup"].window_s == 3600


# --------------------------------------------------------------------------- page quota by tier


def test_an_open_tier_account_has_the_open_quota_and_is_told_the_number(open_web):
    login(open_web.browser, CAROL)
    assert upload(open_web.browser, make_pdf(2)).status_code == 303
    response = upload(open_web.browser, make_pdf(2), filename="week2.pdf")
    assert response.status_code == 429
    text = " ".join(response.text.split())
    assert (
        "week2.pdf has 2 pages, but you have 1 of the 3 pages a new account can convert "
        "today left" in text
    )
    assert upload(open_web.browser, make_pdf(1)).status_code == 303
    response = upload(open_web.browser, make_pdf(1), filename="week3.pdf")
    assert response.status_code == 429
    text = " ".join(response.text.split())
    assert "you have used all 3 pages a new account can convert today" in text
    page = " ".join(open_web.browser.get("/app").text.split())
    assert "You have converted all 3 pages a new account can convert today" in page
    with session_scope(open_web.ctx.factory) as session:
        assert session.scalar(select(QuotaDay.pages)) == 3


def test_the_upload_form_tells_a_new_account_its_limit(open_web):
    login(open_web.browser, CAROL)
    page = " ".join(open_web.browser.get("/app").text.split())
    assert "You can convert 3 more pages today (a new account can convert 3 pages a day)" in page


def test_an_invited_person_keeps_the_normal_quota_in_open_mode(open_web):
    open_web.allow(BOB)
    login(open_web.browser, BOB)
    for _ in range(4):  # the invited quota in these tests is 20 pages
        assert upload(open_web.browser, make_pdf(5)).status_code == 303
    response = upload(open_web.browser, make_pdf(1), filename="more.pdf")
    assert response.status_code == 429
    text = " ".join(response.text.split())
    assert "you have used all 20 pages you can convert today" in text
    assert "new account" not in text


def test_the_quota_message_for_an_invited_person_is_what_it_always_was(open_web):
    open_web.allow(BOB)
    login(open_web.browser, BOB)
    for _ in range(3):
        upload(open_web.browser, make_pdf(5))
    upload(open_web.browser, make_pdf(2))
    response = upload(open_web.browser, make_pdf(5), filename="chapter2.pdf")
    assert "chapter2.pdf has 5 pages, but you have 3 of today&#39;s 20 pages left" in response.text


# --------------------------------------------------------------------------- re-reads


def test_the_reread_allowance_is_one_conditional_update_per_person_and_day(open_web):
    login(open_web.browser, CAROL)
    login(open_web.client(), DAVE)
    carol, dave = user_row(open_web, CAROL), user_row(open_web, DAVE)
    assert carol is not None and dave is not None
    day = utc_today()
    with session_scope(open_web.ctx.factory) as session:
        assert try_consume_reread(session, carol.id, allowance=2, day=day)
        assert try_consume_reread(session, carol.id, allowance=2, day=day)
        assert not try_consume_reread(session, carol.id, allowance=2, day=day)
        assert rereads_used(session, carol.id, day) == 2
        assert try_consume_reread(session, dave.id, allowance=2, day=day)  # per person
        assert try_consume_reread(session, carol.id, allowance=2, day=day + timedelta(days=1))
        assert not try_consume_reread(session, dave.id, allowance=0, day=day)


@pytest.fixture
def reviewing(tmp_path):
    """An open-mode app where ALICE (an open-tier account) has a converted document."""
    env = make_env(tmp_path, open_tier_daily_rereads=2, open_tier_daily_page_quota=20)
    env.converter.output = review_output()
    with env.running() as client:
        env.browser = client
        login(client, ALICE)
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


BOX = {"left": "10", "top": "55", "right": "50", "bottom": "70"}
ANSWER = {"is_equation": True, "latex": "x^2 + 1", "confidence": 0.99}


def with_model(env, sent: list | None = None) -> None:
    def factory(settings, budget):
        client = FakeVisionClient(budget=budget, defaults={"equation": ANSWER})
        if sent is not None:
            sent.append(client)
        return client

    env.ctx.client_factory = factory


def mark_region(env, kind: str = "equation"):
    path = f"/jobs/{env.job_id}/review"
    version = str(env.job(env.job_id).result_version or 0)
    data = {"csrf_token": token(env.browser, path), "version": version, "kind": kind} | BOX
    if kind == "picture":
        data["alt"] = "A cart on a ramp."
    return env.browser.post(f"/jobs/{env.job_id}/pages/1/add", data=data)


def rereads_counted(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.coalesce(func.sum(QuotaDay.rereads), 0))) or 0)


def test_an_open_tier_account_can_start_only_so_many_model_reads_a_day(reviewing):
    with_model(reviewing)
    assert mark_region(reviewing).status_code == 303
    assert mark_region(reviewing).status_code == 303
    third = mark_region(reviewing)
    assert third.status_code == 422
    text = " ".join(third.text.split())
    assert "You have used all 2 reads with the model that a new account has today" in text
    assert "Type it yourself" in text
    assert rereads_counted(reviewing) == 2


def test_adding_a_picture_costs_no_read_and_is_not_counted(reviewing):
    with_model(reviewing)
    for _ in range(3):
        assert mark_region(reviewing, "picture").status_code == 303
    assert rereads_counted(reviewing) == 0
    assert mark_region(reviewing).status_code == 303  # the allowance is still whole


def test_an_invited_person_has_no_reread_allowance(tmp_path):
    env = make_env(tmp_path, open_tier_daily_rereads=1)
    env.converter.output = review_output()
    with env.running() as client:
        env.browser = client
        env.allow(ALICE)
        login(client, ALICE)
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        with_model(env)
        for _ in range(3):
            assert mark_region(env).status_code == 303
        assert rereads_counted(env) == 0


def test_a_read_refused_by_the_allowance_sends_nothing_to_the_model(reviewing):
    sent: list = []
    with_model(reviewing, sent)
    mark_region(reviewing)
    mark_region(reviewing)
    assert len(sent) == 2
    assert mark_region(reviewing).status_code == 422
    assert len(sent) == 2


# --------------------------------------------------------------------------- admin


@pytest.fixture
def admin_web(tmp_path):
    env = make_env(tmp_path, max_new_accounts_per_day=50)
    with env.running() as client:
        env.browser = client
        login(client, ADMIN)
        yield env


def post_admin(env, path: str, **fields):
    data = {"csrf_token": token(env.browser, "/admin")} | fields
    return env.browser.post(path, data=data)


def test_the_admin_page_shows_the_tier_the_sign_up_numbers_and_the_open_pool(admin_web):
    admin_web.allow(BOB)
    login(admin_web.client(), CAROL)
    page = " ".join(admin_web.browser.get("/admin").text.split())
    assert '<th scope="col">Access</th>' in page
    assert '<th scope="row">carol@example.org</th> <td>Instructor</td> <td>Open</td>' in page
    assert '<th scope="row">bob@example.org</th> <td>Instructor</td> <td>Invited</td>' in page
    assert "New accounts today (UTC)</dt><dd>1 of 50" in page
    assert "Pool for new accounts (open tier)" in page and "$4.00" in page
    assert "Open-tier accounts</dt><dd>1" in page


def test_an_admin_can_block_and_unblock_an_open_tier_account(admin_web):
    carol = client_for(admin_web, CAROL)
    response = post_admin(admin_web, "/admin/users/block", email=CAROL)
    assert response.status_code == 303
    assert carol.get("/app").status_code == 303  # signed out at once
    assert login(admin_web.client(), CAROL).status_code == 403
    page = admin_web.browser.get("/admin").text
    assert 'action="/admin/users/unblock"' in page and CAROL in page
    assert "The account was blocked." in page

    post_admin(admin_web, "/admin/users/unblock", email=CAROL)
    assert login(admin_web.client(), CAROL).status_code == 303
    row = user_row(admin_web, CAROL)
    assert row is not None and row.access_tier == access.OPEN


def test_the_admin_page_offers_block_for_open_tier_and_remove_for_invited(admin_web):
    admin_web.allow(BOB)
    login(admin_web.client(), CAROL)
    page = admin_web.browser.get("/admin").text
    assert page.count('action="/admin/users/block"') == 1
    assert page.count('action="/admin/allowlist/remove"') == 1  # bob; not the admin's own row


def test_an_admin_cannot_block_themselves_or_a_stranger_and_others_cannot_block(admin_web):
    post_admin(admin_web, "/admin/users/block", email=ADMIN)
    row = user_row(admin_web, ADMIN)
    assert row is not None and row.is_allowed
    assert "You cannot block yourself" in admin_web.browser.get("/admin").text
    post_admin(admin_web, "/admin/users/block", email="ghost@example.org")
    assert "Nobody has an account under that address" in admin_web.browser.get("/admin").text
    post_admin(admin_web, "/admin/users/block", email="not an address")

    carol = client_for(admin_web, CAROL)
    forbidden = carol.post("/admin/users/block", data={"csrf_token": token(carol), "email": BOB})
    assert forbidden.status_code == 403
    assert carol.post("/admin/users/block", data={"email": BOB}).status_code == 403


def test_blocking_stops_the_accounts_queued_work(open_web):
    client = client_for(open_web, CAROL)
    job_id = job_id_from(upload(client, make_pdf(1)))
    with session_scope(open_web.ctx.factory) as session:
        set_blocked(session, CAROL, blocked=True)
    job = open_web.job(job_id)
    assert job.status == "failed" and job.error_code == "access_removed"
    assert open_web.run_worker() is None  # nothing of theirs is claimed


def test_the_admin_list_says_how_many_it_shows(tmp_path):
    import readablestem.web.routes.admin as admin_routes

    env = make_env(tmp_path)
    with env.running() as client:
        login(client, ADMIN)
        assert (
            f"The first {admin_routes.PEOPLE_ROWS} accounts are listed" in client.get("/admin").text
        )


# --------------------------------------------------------------------------- deleting an account


def populate(env, email: str) -> str:
    """An account with a document, a course, an identity, a linked address and an event."""
    client = client_for(env, email)
    job_id = job_id_from(upload(client, make_pdf(2)))
    env.run_worker()
    me = user_row(env, email)
    assert me is not None
    with session_scope(env.ctx.factory) as session:
        session.add(Course(user_id=me.id, name="Mechanics"))
        session.add(
            AuthIdentity(user_id=me.id, provider="google", issuer="i", subject=f"s-{me.id}")
        )
        session.add(LinkedEmail(user_id=me.id, email=f"alt-{me.id}@example.org"))
        session.add(Event(user_id=me.id, name="download", job_ref=job_id))
    return me.id


def rows_of(env, user_id: str) -> dict[str, int]:
    with session_scope(env.ctx.factory) as session:

        def n(model, column) -> int:
            query = select(func.count()).select_from(model).where(column == user_id)
            return int(session.scalar(query) or 0)

        return {
            "users": n(User, User.id),
            "jobs": n(Job, Job.user_id),
            "courses": n(Course, Course.user_id),
            "identities": n(AuthIdentity, AuthIdentity.user_id),
            "linked": n(LinkedEmail, LinkedEmail.user_id),
            "events": n(Event, Event.user_id),
            "quota": n(QuotaDay, QuotaDay.user_id),
        }


def job_ids(env, user_id: str) -> list[str]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(Job.id).where(Job.user_id == user_id)))


def delete_account(client):
    csrf = csrf_from(client.get("/account/delete").text)
    return client.post("/account/delete", data={"csrf_token": csrf, "confirm": "yes"})


def test_the_account_page_shows_what_is_held_the_limits_and_the_delete_link(open_web):
    login(open_web.browser, CAROL)
    upload(open_web.browser, make_pdf(1))
    page = " ".join(open_web.browser.get("/account").text.split())
    assert "carol@example.org" in page and "Documents kept now</dt><dd>1" in page
    assert "You signed yourself up, so the limits for new accounts apply" in page
    assert "Pages you can convert today: 2 of 3" in page
    assert "of 30 left today" in page  # the model reads from review
    assert 'href="/account/delete"' in page and "Delete my account" in page
    assert 'href="/account"' in open_web.browser.get("/app").text  # in the main navigation


def test_an_invited_person_sees_their_own_limits_and_no_reread_line(open_web):
    open_web.allow(BOB)
    login(open_web.browser, BOB)
    page = " ".join(open_web.browser.get("/account").text.split())
    assert "You were invited, so the limits for invited people apply" in page
    assert "Pages you can convert today: 20 of 20" in page
    assert "Reads with the model" not in page


def test_the_account_pages_need_a_signed_in_person(open_web):
    for path in ("/account", "/account/delete"):
        assert open_web.client().get(path).status_code == 303
    assert open_web.client().post("/account/delete", data={"confirm": "yes"}).status_code == 403


def test_deleting_an_account_asks_first_and_changes_nothing_until_confirmed(open_web):
    uid = populate(open_web, CAROL)
    client = client_for(open_web, CAROL)
    confirm = client.get("/account/delete")
    assert confirm.status_code == 200 and "Delete your account?" in confirm.text
    assert "If you sign in again later, you get a new, empty account" in confirm.text
    assert "invitation list" not in confirm.text
    csrf = csrf_from(confirm.text)
    # A POST without the confirmation is only the confirmation page again.
    again = client.post("/account/delete", data={"csrf_token": csrf})
    assert again.status_code == 200 and "Yes, delete my account" in again.text
    assert rows_of(open_web, uid)["users"] == 1 and rows_of(open_web, uid)["jobs"] == 1
    # The form needs the CSRF token.
    assert client.post("/account/delete", data={"confirm": "yes"}).status_code == 403
    assert rows_of(open_web, uid)["users"] == 1


def test_deleting_an_account_removes_the_person_and_everything_of_theirs(open_web):
    mine = populate(open_web, CAROL)
    theirs = populate(open_web, DAVE)
    before = rows_of(open_web, mine)
    assert before["jobs"] == 1 and before["courses"] == 1 and before["identities"] == 1
    assert before["linked"] == 1 and before["events"] >= 1 and before["quota"] == 1
    my_job = job_ids(open_web, mine)[0]

    me = client_for(open_web, CAROL)
    response = delete_account(me)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert rows_of(open_web, mine) == dict.fromkeys(before, 0)
    with session_scope(open_web.ctx.factory) as session:
        assert session.get(Job, my_job) is None
        blobs = session.scalar(select(func.count()).select_from(Blob).where(Blob.job_id == my_job))
        assert blobs == 0
    # Signed out, with a word about what happened; the other person is untouched.
    assert me.get("/app").status_code == 303
    assert "Your account and everything in it have been deleted." in me.get("/").text
    kept = rows_of(open_web, theirs)
    assert kept["users"] == 1 and kept["jobs"] == 1 and kept["courses"] == 1
    assert kept["identities"] == 1 and kept["linked"] == 1


def test_a_deleted_account_signing_in_again_is_a_new_empty_account_and_counts_as_new(open_web):
    old = populate(open_web, CAROL)
    delete_account(client_for(open_web, CAROL))
    assert signups_now(open_web) == 1  # not given back
    assert login(open_web.client(), CAROL).status_code == 303
    row = user_row(open_web, CAROL)
    assert row is not None and row.id != old and row.access_tier == access.OPEN
    assert rows_of(open_web, row.id)["jobs"] == 0
    assert signups_now(open_web) == 2


def test_in_invitation_mode_the_confirmation_says_deleting_leaves_the_list(closed_web):
    closed_web.allow(BOB)
    client = client_for(closed_web, BOB)
    page = " ".join(client.get("/account/delete").text.split())
    assert "Deleting your account takes you off it, so you would need to be invited again" in page
    delete_account(client)
    assert user_row(closed_web, BOB) is None
    assert login(closed_web.client(), BOB).status_code == 403


def test_deleting_an_account_while_a_job_is_queued_leaves_nothing_to_claim(open_web):
    client = client_for(open_web, CAROL)
    job_id = job_id_from(upload(client, make_pdf(1)))
    delete_account(client)
    assert open_web.run_worker() is None
    with session_scope(open_web.ctx.factory) as session:
        assert session.get(Job, job_id) is None


# --------------------------------------------------------------------------- the command line


@pytest.fixture
def cli_settings(tmp_path, monkeypatch):
    s = make_settings(tmp_path, env="dev", signup_mode="open")
    monkeypatch.setattr("readablestem.config.get_settings", lambda: s)
    return s


def test_users_list_shows_the_tier_and_unblock_brings_a_person_back(cli_settings, capsys):
    from readablestem.cli.main import main
    from readablestem.web import db

    assert main(["users", "allow", "prof@example.org"]) == 0
    engine = db.make_engine(cli_settings.database_url)
    factory = db.make_sessionmaker(engine)
    with db.session_scope(factory) as session:
        session.add(db.User(email="open@example.org", is_allowed=True, access_tier=access.OPEN))
    assert main(["users", "list"]) == 0
    out = capsys.readouterr().out
    assert "tier" in out and "invited" in out and "open" in out
    assert main(["users", "deny", "open@example.org"]) == 0
    assert main(["users", "unblock", "open@example.org"]) == 0
    with db.session_scope(factory) as session:
        row = session.scalar(select(db.User).where(db.User.email == "open@example.org"))
        assert row is not None and row.is_allowed and row.access_tier == access.OPEN
    assert main(["users", "unblock", "nobody@example.org"]) == 2
    assert main(["users", "allow", "open@example.org"]) == 0
    with db.session_scope(factory) as session:
        row = session.scalar(select(db.User).where(db.User.email == "open@example.org"))
        assert row is not None and row.access_tier == access.INVITED
    engine.dispose()
