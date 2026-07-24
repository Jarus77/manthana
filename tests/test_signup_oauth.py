"""Self-serve onboarding through Google sign-in.

The contract under test is the one that lets the operator stop hand-onboarding
every startup, without giving anything away in the process:

  * a stranger can create their own org and immediately get an engineer install line
  * the second person from a work domain JOINS as an engineer, never as a founder
  * a personal-email address is never treated as a company
  * an invite link is how anyone (personal address included) joins a team in a browser
  * two self-serve orgs get two distinct team rows (TeamRow.id is a global PK)
  * a forged or replayed sign-in cannot mint a session

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from manthana.server import ServerConfig, ServerStore, create_app
from manthana.server.auth import (
    AuthError,
    issue_oauth_state,
    issue_pending_signup,
    verify_founder_token,
    verify_pending_signup,
)
from manthana.server.llm import ScriptedProvider
from manthana.server.signup import PENDING_COOKIE, STATE_COOKIE
from manthana.server.storage import InMemoryObjectStore
from manthana.server.tables import OrgQuotaRow
from manthana.server.ui import COOKIE

_SECRET = "x" * 40


def _make(*, signup: bool = True):
    config = ServerConfig(
        jwt_secret=_SECRET,
        admin_token="adm",
        enable_self_serve_signup=signup,
        google_client_id="cid" if signup else "",
        google_client_secret="sec" if signup else "",
        public_url="https://api.example.com",
    )
    store = ServerStore.open("sqlite://")
    client = TestClient(
        create_app(config, store, InMemoryObjectStore(), ScriptedProvider([])),
        follow_redirects=False,
    )
    return client, config, store


def _role(store: ServerStore, identity_id: str) -> str:
    """The stored role, asserting the identity exists — a missing row is a failure
    of the test's premise, not a role to compare against."""
    row = store.get_identity(identity_id)
    assert row is not None, f"no identity {identity_id}"
    return row.role


def _google(monkeypatch, sub: str, email: str, name: str = "", verified: bool = True) -> None:
    """Stand in for the Google token endpoint. Everything downstream of the
    exchange is the code under test; the exchange itself is Google's."""
    from manthana.server import signup

    monkeypatch.setattr(
        signup,
        "exchange_code_for_profile",
        lambda config, code, redirect_uri: {
            "sub": sub,
            "email": email,
            "email_verified": verified,
            "name": name,
        },
    )


def _sign_in(client, monkeypatch, sub: str, email: str, name: str = "", invite: str = ""):
    """Drive the full round trip: start → Google → callback. Returns the callback
    response with the client's cookie jar updated exactly as a browser's would be."""
    _google(monkeypatch, sub, email, name)
    start = client.get("/ui/auth/google", params={"invite": invite} if invite else None)
    assert start.status_code == 303
    state = client.cookies[STATE_COOKIE]
    return client.get("/ui/auth/google/callback", params={"code": "authcode", "state": state})


# ── creating an org ────────────────────────────────────────────────────────
def test_new_founder_creates_org_and_gets_engineer_install_line(monkeypatch) -> None:
    client, config, store = _make()

    resp = _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com", "Priya")
    assert resp.status_code == 200
    assert "Create a new organization" in resp.text
    assert PENDING_COOKIE in client.cookies

    created = client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    assert created.status_code == 303
    assert created.headers["location"] == "/ui/welcome"

    # The org exists, the founder holds a founder session, and it is THEIR org.
    org_id = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    assert store.get_org(org_id) is not None
    assert store.get_org_by_domain("acmeco.com") == org_id

    welcome = client.get("/ui/welcome")
    assert welcome.status_code == 200
    # The whole point: the two lines an engineer runs, without an operator.
    assert "manthana setup mia_" in welcome.text
    assert "install.sh" in welcome.text
    assert "/ui/join?code=" in welcome.text


def test_self_serve_org_has_no_quota_row_so_it_is_uncapped(monkeypatch) -> None:
    """Deliberate current policy: grow first, meter later. An absent OrgQuotaRow
    means the org falls back to the server default (0 = unlimited)."""
    client, config, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})

    from sqlmodel import Session, select

    org_id = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    assert store.get_org_quota(org_id) is None
    with Session(store._engine) as db:  # noqa: SLF001 - asserting a row's absence
        assert db.exec(select(OrgQuotaRow)).all() == []


def test_two_orgs_get_distinct_team_rows(monkeypatch) -> None:
    """Regression: TeamRow.id is a GLOBAL primary key and create_team upserts on
    it, so naming every self-serve org's team "core" would make each new signup
    silently overwrite the previous org's team row."""
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "a@alpha.com")
    client.post("/ui/signup/create", data={"org_name": "Alpha"})
    client.cookies.clear()
    _sign_in(client, monkeypatch, "sub-2", "b@beta.com")
    client.post("/ui/signup/create", data={"org_name": "Beta"})

    alpha = store.list_teams("alpha")
    beta = store.list_teams("beta")
    assert len(alpha) == 1 and len(beta) == 1
    assert alpha[0].id != beta[0].id
    assert alpha[0].org_id == "alpha" and beta[0].org_id == "beta"


def test_same_org_name_does_not_land_in_the_first_tenant(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "a@alpha.com")
    client.post("/ui/signup/create", data={"org_name": "Acme"})
    first = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    client.cookies.clear()
    _sign_in(client, monkeypatch, "sub-2", "b@other.com")
    client.post("/ui/signup/create", data={"org_name": "Acme"})
    second = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    assert first != second


# ── joining an existing org ────────────────────────────────────────────────
def test_second_person_from_work_domain_joins_as_engineer(monkeypatch) -> None:
    client, config, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    org_id = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    client.cookies.clear()

    resp = _sign_in(client, monkeypatch, "sub-2", "dev@acmeco.com", "Dev")
    assert "Join Acme Co" in resp.text

    joined = client.post("/ui/signup/join", data={"org_id": org_id})
    assert joined.status_code == 303
    # Engineer, not founder: they land on the wiki and the console is not theirs.
    assert joined.headers["location"] == "/ui/home"
    with pytest.raises(AuthError):
        verify_founder_token(_SECRET, client.cookies[COOKIE])
    assert client.get("/ui").headers["location"] == "/ui/home"

    roles = {i.email: i.role for i in store.list_identities(org_id)}
    assert roles == {"priya@acmeco.com": "founder", "dev@acmeco.com": "engineer"}


def test_join_form_cannot_reach_an_org_the_domain_does_not_own(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    victim = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    client.cookies.clear()

    # An unrelated domain posts the victim's org id straight at the join route.
    _sign_in(client, monkeypatch, "sub-9", "mallory@evil.com")
    resp = client.post("/ui/signup/join", data={"org_id": victim})
    assert resp.status_code == 400
    assert store.list_identities(victim) and len(store.list_identities(victim)) == 1


def test_personal_email_is_never_offered_an_existing_org(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "one@gmail.com")
    client.post("/ui/signup/create", data={"org_name": "Solo"})
    assert store.get_org_by_domain("gmail.com") is None  # never claimed
    client.cookies.clear()

    resp = _sign_in(client, monkeypatch, "sub-2", "two@gmail.com")
    assert "Create a new organization" in resp.text
    assert "Join" not in resp.text


def test_invite_link_joins_any_address_as_engineer(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    org_id = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    client.get("/ui/welcome")  # mints the org's shared invite
    code = next(i.code for i in store.list_invites(org_id) if i.actor is None)
    client.cookies.clear()

    page = client.get("/ui/join", params={"code": code})
    assert page.status_code == 200 and "Acme Co" in page.text

    resp = _sign_in(client, monkeypatch, "sub-3", "contractor@gmail.com", invite=code)
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/home"
    assert [i.role for i in store.list_identities(org_id) if i.email.endswith("gmail.com")] == [
        "engineer"
    ]


def test_unknown_invite_link_is_rejected() -> None:
    client, _, _ = _make()
    assert client.get("/ui/join", params={"code": "nope"}).status_code == 404


def test_returning_user_goes_straight_back_in(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    org_id = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    client.cookies.clear()

    resp = _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    assert resp.status_code == 303 and resp.headers["location"] == "/ui/welcome"
    assert verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id == org_id
    assert len(store.list_identities(org_id)) == 1  # not duplicated


# ── promotion ──────────────────────────────────────────────────────────────
def test_founder_promotes_an_engineer_and_cannot_reach_another_org(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    founder_cookie = client.cookies[COOKIE]
    org_id = verify_founder_token(_SECRET, founder_cookie).org_id
    client.cookies.clear()
    _sign_in(client, monkeypatch, "sub-2", "dev@acmeco.com")
    client.post("/ui/signup/join", data={"org_id": org_id})
    client.cookies.clear()

    # An outsider's org, to prove promotion is tenant-scoped.
    _sign_in(client, monkeypatch, "sub-9", "solo@other.com")
    client.post("/ui/signup/create", data={"org_name": "Other"})
    outsider = client.cookies[COOKIE]
    client.cookies.clear()

    client.cookies.set(COOKIE, outsider)
    promote = {"identity_id": "google:sub-2"}
    assert client.post("/ui/members/promote", data=promote).status_code == 404
    assert _role(store, "google:sub-2") == "engineer"

    client.cookies.clear()
    client.cookies.set(COOKIE, founder_cookie)
    assert client.post("/ui/members/promote", data=promote).status_code == 303
    assert _role(store, "google:sub-2") == "founder"


def test_promotion_survives_the_promoted_person_signing_in_again(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    founder_cookie = client.cookies[COOKIE]
    org_id = verify_founder_token(_SECRET, founder_cookie).org_id
    client.cookies.clear()
    _sign_in(client, monkeypatch, "sub-2", "dev@acmeco.com")
    client.post("/ui/signup/join", data={"org_id": org_id})
    client.cookies.clear()
    client.cookies.set(COOKIE, founder_cookie)
    client.post("/ui/members/promote", data={"identity_id": "google:sub-2"})
    client.cookies.clear()

    resp = _sign_in(client, monkeypatch, "sub-2", "dev@acmeco.com")
    assert resp.headers["location"] == "/ui/welcome"
    assert verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id == org_id


# ── the sign-in itself ─────────────────────────────────────────────────────
def test_callback_requires_the_state_it_set(monkeypatch) -> None:
    """Signature alone is not enough: an attacker who starts their own sign-in
    holds a validly signed state, and could otherwise replay it into someone
    else's browser."""
    client, _, _ = _make()
    _google(monkeypatch, "sub-1", "priya@acmeco.com")
    foreign = issue_oauth_state(_SECRET, nonce="attacker")
    resp = client.get("/ui/auth/google/callback", params={"code": "c", "state": foreign})
    assert resp.status_code == 400
    assert COOKIE not in client.cookies


def test_callback_rejects_a_garbage_state(monkeypatch) -> None:
    client, _, _ = _make()
    _google(monkeypatch, "sub-1", "priya@acmeco.com")
    client.cookies.set(STATE_COOKIE, "not-a-jwt")
    resp = client.get("/ui/auth/google/callback", params={"code": "c", "state": "not-a-jwt"})
    assert resp.status_code == 400
    assert COOKIE not in client.cookies


def test_unverified_google_email_is_refused(monkeypatch) -> None:
    client, _, store = _make()
    _google(monkeypatch, "sub-1", "priya@acmeco.com", verified=False)
    client.get("/ui/auth/google")
    resp = client.get(
        "/ui/auth/google/callback",
        params={"code": "c", "state": client.cookies[STATE_COOKIE]},
    )
    assert resp.status_code == 400
    assert store.list_orgs() == []


def test_an_oauth_state_cannot_be_used_as_a_verified_profile() -> None:
    """The two are signed with the same secret, so they are separated by SCOPE.
    Without that, a stranger could take the state from their own sign-in and post
    it as a pending profile carrying any email they like — and with it claim
    another company's domain."""
    state = issue_oauth_state(_SECRET, nonce="google:sub-x", invite="ceo@victim.com")
    with pytest.raises(AuthError):
        verify_pending_signup(_SECRET, state)


def test_forged_pending_cookie_cannot_create_an_org() -> None:
    client, _, store = _make()
    client.cookies.set(PENDING_COOKIE, "not-a-jwt")
    assert client.post("/ui/signup/create", data={"org_name": "Acme"}).status_code == 400
    assert store.list_orgs() == []


def test_a_correctly_signed_pending_cookie_is_honoured() -> None:
    """The other half of the check above: the scope test rejects foreign tokens,
    not everything."""
    client, _, store = _make()
    client.cookies.set(
        PENDING_COOKIE,
        issue_pending_signup(_SECRET, identity_id="google:sub-1", email="a@acmeco.com"),
    )
    assert client.post("/ui/signup/create", data={"org_name": "Acme"}).status_code == 303
    assert store.get_identity("google:sub-1") is not None


# ── the feature flag ───────────────────────────────────────────────────────
def test_signup_surface_absent_when_disabled() -> None:
    client, _, _ = _make(signup=False)
    for path in ("/ui/signup", "/ui/auth/google", "/ui/welcome"):
        assert client.get(path).status_code == 404
    assert "Sign in with Google" not in client.get("/ui/login").text


def test_login_page_offers_google_when_enabled() -> None:
    client, _, _ = _make()
    body = client.get("/ui/login").text
    assert "Sign in with Google" in body
    assert "Your Manthana token" in body  # the operator's path still works


def test_enabling_signup_without_an_oauth_client_fails_fast() -> None:
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        ServerConfig(jwt_secret=_SECRET, admin_token="adm", enable_self_serve_signup=True)


# ── the credentials handed out ─────────────────────────────────────────────
def test_browser_session_is_short_and_api_token_is_long(monkeypatch) -> None:
    """A stolen cookie should expire in weeks; the year-long credential is created
    deliberately, from the console, and is revocable."""
    import jwt

    client, config, _ = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    session_exp = jwt.decode(client.cookies[COOKIE], _SECRET, algorithms=["HS256"])["exp"]

    resp = client.post("/ui/api-token")
    assert resp.status_code == 200
    token = resp.text.split("<pre>")[1].split("</pre>")[0]
    assert jwt.decode(token, _SECRET, algorithms=["HS256"])["exp"] > session_exp
    assert verify_founder_token(_SECRET, token).org_id is not None


def test_engineer_cannot_mint_an_api_token(monkeypatch) -> None:
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    org_id = verify_founder_token(_SECRET, client.cookies[COOKIE]).org_id
    client.cookies.clear()
    _sign_in(client, monkeypatch, "sub-2", "dev@acmeco.com")
    client.post("/ui/signup/join", data={"org_id": org_id})

    assert client.post("/ui/api-token").headers["location"] == "/ui/login"


def test_revoked_session_stops_working(monkeypatch) -> None:
    """Self-serve sessions are ordinary scoped JWTs, so the existing blocklist
    kills them like any other token."""
    client, _, store = _make()
    _sign_in(client, monkeypatch, "sub-1", "priya@acmeco.com")
    client.post("/ui/signup/create", data={"org_name": "Acme Co"})
    assert client.get("/ui/welcome").status_code == 200

    store.revoke_token(client.cookies[COOKIE], reason="test", revoked_by="admin")
    assert client.get("/ui/welcome").headers["location"] == "/ui/login"
