"""GitHub REST and GraphQL client.

`GitHubPort` is what the rest of the kit depends on; `HttpGitHub` implements
it over urllib so nothing but Python is needed on a runner. Tests substitute
an in-memory fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple

DEFAULT_API_URL = "https://api.github.com"
PER_PAGE = 100
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes]], Tuple[int, Mapping[str, str], bytes]]


class GitHubError(Exception):
    def __init__(self, status: int, method: str, path: str, message: str) -> None:
        super().__init__(f"GitHub {method} {path} -> {status}: {message[:300]}")
        self.status = status
        self.method = method
        self.path = path
        self.message = message


class GitHubPort(Protocol):
    def get(self, path: str, params: Optional[Mapping[str, Any]] = None, accept: Optional[str] = None) -> Any: ...

    def get_all(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        items_key: Optional[str] = None,
        limit: int = 3000,
    ) -> list: ...

    def post(self, path: str, body: Any) -> Any: ...

    def patch(self, path: str, body: Any) -> Any: ...

    def put(self, path: str, body: Any = None) -> Any: ...

    def delete(self, path: str) -> Any: ...

    def graphql(self, query: str, variables: Mapping[str, Any]) -> dict: ...


def urllib_transport(method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]):
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - https only, see _url
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read()


class HttpGitHub:
    """`GitHubPort` over HTTPS with one retry on transient server errors."""

    def __init__(
        self,
        token: str,
        api_url: str = DEFAULT_API_URL,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise GitHubError(401, "INIT", "", "no GitHub token available")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep

    # -- public port -----------------------------------------------------

    def get(self, path, params=None, accept=None):
        return self._call("GET", path, params=params, accept=accept)

    def get_all(self, path, params=None, items_key=None, limit=3000):
        items: list = []
        page = 1
        while len(items) < limit:
            query = dict(params or {})
            query.update({"per_page": PER_PAGE, "page": page})
            data = self._call("GET", path, params=query)
            batch = data.get(items_key, []) if items_key else data
            if not isinstance(batch, list):
                raise GitHubError(500, "GET", path, "expected a list page")
            items.extend(batch)
            if len(batch) < PER_PAGE:
                break
            page += 1
        return items[:limit]

    def post(self, path, body):
        return self._call("POST", path, body=body)

    def patch(self, path, body):
        return self._call("PATCH", path, body=body)

    def put(self, path, body=None):
        return self._call("PUT", path, body=body)

    def delete(self, path):
        return self._call("DELETE", path)

    def graphql(self, query, variables):
        data = self._call("POST", "/graphql", body={"query": query, "variables": dict(variables)})
        if not isinstance(data, dict) or data.get("errors"):
            raise GitHubError(200, "POST", "/graphql", json.dumps((data or {}).get("errors"))[:300])
        return data.get("data") or {}

    # -- internals -------------------------------------------------------

    def _url(self, path: str, params: Optional[Mapping[str, Any]]) -> str:
        if not path.startswith("/"):
            path = "/" + path
        url = self._api_url + path
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            raise GitHubError(400, "URL", path, "refusing a non-HTTPS API URL")
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return url

    def _call(self, method, path, params=None, body=None, accept=None):
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept or "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "reviewkit",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = self._url(path, params)
        status, _, raw = self._transport(method, url, headers, payload)
        if status in (502, 503, 504):
            self._sleep(2.0)
            status, _, raw = self._transport(method, url, headers, payload)
        text = raw.decode("utf-8", "replace") if raw else ""
        if status >= 400:
            raise GitHubError(status, method, path, _error_message(text))
        if accept and "json" not in accept:
            return text
        return json.loads(text) if text.strip() else None


def _error_message(text: str) -> str:
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()
    if isinstance(data, dict):
        detail = "; ".join(
            str(e.get("message") or e.get("code") or e) if isinstance(e, dict) else str(e)
            for e in data.get("errors") or []
        )
        return " - ".join(part for part in (str(data.get("message") or ""), detail) if part)
    return text.strip()


def discover_token(env: Optional[Mapping[str, str]] = None) -> str:
    """Token from the environment, else from a logged-in `gh` CLI (local use)."""
    env = os.environ if env is None else env
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if env.get(name):
            return env[name]
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def client_from_env(env: Optional[Mapping[str, str]] = None) -> HttpGitHub:
    env = os.environ if env is None else env
    return HttpGitHub(discover_token(env), env.get("GITHUB_API_URL") or DEFAULT_API_URL)


def split_repo(full_name: str) -> Tuple[str, str]:
    owner, _, name = full_name.partition("/")
    if not owner or not name or "/" in name:
        raise ValueError(f"expected owner/name, got {full_name!r}")
    return owner, name
