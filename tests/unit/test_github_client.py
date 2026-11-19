"""HTTP adapter: headers, pagination, error mapping, retry and token discovery."""

from __future__ import annotations

import json

import pytest

from reviewkit.github.client import GitHubError, HttpGitHub, discover_token, split_repo


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        status, payload = self.responses.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, {}, raw


def client(responses):
    transport = Recorder(responses)
    return HttpGitHub("tok", transport=transport, sleep=lambda _s: None), transport


def test_get_sends_auth_and_decodes_json():
    gh, transport = client([(200, {"ok": True})])
    assert gh.get("/repos/o/r") == {"ok": True}
    request = transport.requests[0]
    assert request["url"] == "https://api.github.com/repos/o/r"
    assert request["headers"]["Authorization"] == "Bearer tok"
    assert request["headers"]["Accept"] == "application/vnd.github+json"


def test_post_encodes_body():
    gh, transport = client([(201, {"id": 1})])
    assert gh.post("repos/o/r/labels", {"name": "x"}) == {"id": 1}
    assert json.loads(transport.requests[0]["body"]) == {"name": "x"}
    assert transport.requests[0]["url"].endswith("/repos/o/r/labels")


def test_get_all_follows_pages_until_a_short_one():
    full = [{"n": i} for i in range(100)]
    gh, transport = client([(200, full), (200, [{"n": 100}])])
    items = gh.get_all("/repos/o/r/pulls/1/files")
    assert len(items) == 101
    assert "page=1" in transport.requests[0]["url"] and "page=2" in transport.requests[1]["url"]
    assert "per_page=100" in transport.requests[0]["url"]


def test_get_all_reads_a_named_array_and_honours_limit():
    gh, _ = client([(200, {"total_count": 3, "check_runs": [{"id": 1}, {"id": 2}, {"id": 3}]})])
    assert gh.get_all("/repos/o/r/commits/abc/check-runs", items_key="check_runs", limit=2) == [{"id": 1}, {"id": 2}]


def test_empty_body_is_none():
    gh, _ = client([(204, b"")])
    assert gh.delete("/repos/o/r/issues/1/labels/x") is None


def test_text_accept_returns_text():
    gh, _ = client([(200, b"diff --git a/x b/x\n")])
    assert gh.get("/repos/o/r/pulls/1", accept="application/vnd.github.diff").startswith("diff --git")


def test_http_error_maps_to_github_error_with_message():
    gh, _ = client([(422, {"message": "Validation Failed", "errors": [{"code": "already_exists"}]})])
    with pytest.raises(GitHubError) as err:
        gh.post("/repos/o/r/labels", {"name": "x"})
    assert err.value.status == 422
    assert "Validation Failed" in err.value.message and "already_exists" in err.value.message


def test_transient_server_error_is_retried_once():
    gh, transport = client([(502, b"bad gateway"), (200, {"ok": 1})])
    assert gh.get("/x") == {"ok": 1}
    assert len(transport.requests) == 2


def test_second_server_error_is_raised():
    gh, _ = client([(503, b"nope"), (503, b"nope")])
    with pytest.raises(GitHubError):
        gh.get("/x")


def test_graphql_returns_data_and_raises_on_errors():
    gh, transport = client([(200, {"data": {"viewer": {"login": "x"}}}), (200, {"errors": [{"message": "boom"}]})])
    assert gh.graphql("query { viewer { login } }", {}) == {"viewer": {"login": "x"}}
    assert transport.requests[0]["url"].endswith("/graphql")
    with pytest.raises(GitHubError):
        gh.graphql("query { nope }", {})


def test_refuses_plain_http_api_urls():
    gh = HttpGitHub("tok", api_url="http://example.com", transport=Recorder([]))
    with pytest.raises(GitHubError):
        gh.get("/x")


def test_a_token_is_required():
    with pytest.raises(GitHubError):
        HttpGitHub("")


def test_discover_token_prefers_gh_token_then_github_token():
    assert discover_token({"GH_TOKEN": "a", "GITHUB_TOKEN": "b"}) == "a"
    assert discover_token({"GITHUB_TOKEN": "b"}) == "b"


def test_split_repo():
    assert split_repo("octo/hello") == ("octo", "hello")
    for bad in ("octo", "/hello", "a/b/c", ""):
        with pytest.raises(ValueError):
            split_repo(bad)
