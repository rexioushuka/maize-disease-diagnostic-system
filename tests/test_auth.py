"""Authentication, CSRF, lockout and password-policy tests."""

from conftest import csrf


def test_login_success_and_redirect(client, login):
    resp = login("farmer")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user_id")


def test_login_wrong_password_generic_message(client):
    token = csrf(client, "/login")
    resp = client.post(
        "/login",
        data={"login_input": "farmer", "password": "wrong", "_csrf_token": token},
    )
    body = resp.get_data(as_text=True)
    assert "Invalid credentials" in body


def test_unknown_user_gives_same_message(client):
    token = csrf(client, "/login")
    resp = client.post(
        "/login",
        data={"login_input": "nobody", "password": "whatever", "_csrf_token": token},
    )
    assert "Invalid credentials" in resp.get_data(as_text=True)


def test_csrf_required_for_post(client):
    resp = client.post("/login", data={"login_input": "farmer", "password": "x"})
    assert resp.status_code == 400


def test_csrf_token_rotates_on_login(client, login):
    token = csrf(client, "/login")
    login("farmer")
    with client.session_transaction() as sess:
        assert sess.get("_csrf_token") != token


def test_logout_requires_post(client, login):
    login("farmer")
    resp = client.get("/logout")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user_id")  # GET must NOT log out


def test_logout_post_clears_session(client, login):
    login("farmer")
    token = csrf(client, "/farmer/dashboard")
    resp = client.post("/logout", data={"_csrf_token": token})
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert not sess.get("user_id")


def test_role_matrix_denies_farmer_admin(client, login):
    login("farmer")
    assert client.get("/admin/dashboard").status_code == 403


def test_role_matrix_allows_admin(client, login):
    login("admin")
    assert client.get("/admin/dashboard").status_code == 200


def test_lockout_after_five_attempts(app, client):
    for _ in range(5):
        token = csrf(client, "/login")
        client.post("/login", data={"login_input": "farmer", "password": "bad",
                                    "_csrf_token": token})
    with app.app_context():
        from msds.db import get_db
        row = get_db().execute(
            "SELECT account_status FROM app_user WHERE username='farmer';"
        ).fetchone()
        assert row["account_status"] == "LOCKED"


def test_forgot_password_uniform_response(client):
    token = csrf(client, "/forgot-password")
    known = client.post("/forgot-password",
                        data={"identifier": "farmer", "_csrf_token": token},
                        follow_redirects=True).get_data(as_text=True)
    token = csrf(client, "/forgot-password")
    unknown = client.post("/forgot-password",
                          data={"identifier": "ghost", "_csrf_token": token},
                          follow_redirects=True).get_data(as_text=True)
    assert "not found" not in unknown.lower()
    assert ("request" in known.lower()) == ("request" in unknown.lower())


def test_password_policy_helper():
    from msds.services.validation import password_errors
    assert password_errors("short")               # too short
    assert password_errors("alllowercase123!")    # no upper
    assert password_errors("ValidPassword123!") == []
