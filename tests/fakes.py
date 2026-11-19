"""In-memory stand-in for `reviewkit.github.client.GitHubPort`."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Optional

from reviewkit.github.client import GitHubError


class FakeGitHub:
    """Route table keyed by `(METHOD, path-regex)`; records every call.

    A route value is returned as-is, or called with `(match, params, body)`
    when it is callable. Unrouted calls raise a 404 so tests fail loudly.
    """

    def __init__(self) -> None:
        self.routes: list = []
        self.calls: list = []
        self.graphql_handler: Optional[Callable[[str, Mapping[str, Any]], dict]] = None

    def on(self, method: str, pattern: str, response: Any) -> FakeGitHub:
        self.routes.append((method.upper(), re.compile(pattern + r"\Z"), response))
        return self

    def _dispatch(self, method: str, path: str, params: Any = None, body: Any = None) -> Any:
        self.calls.append({"method": method, "path": path, "params": dict(params or {}), "body": body})
        for route_method, pattern, response in reversed(self.routes):
            match = pattern.match(path)
            if route_method == method and match:
                return response(match, dict(params or {}), body) if callable(response) else response
        raise GitHubError(404, method, path, "no fake route")

    def get(self, path, params=None, accept=None):
        return self._dispatch("GET", path, params)

    def get_all(self, path, params=None, items_key=None, limit=3000):
        data = self._dispatch("GET", path, params)
        items = data.get(items_key, []) if items_key and isinstance(data, dict) else data
        return list(items)[:limit]

    def post(self, path, body):
        return self._dispatch("POST", path, None, body)

    def patch(self, path, body):
        return self._dispatch("PATCH", path, None, body)

    def put(self, path, body=None):
        return self._dispatch("PUT", path, None, body)

    def delete(self, path):
        return self._dispatch("DELETE", path)

    def graphql(self, query, variables):
        self.calls.append({"method": "GRAPHQL", "path": "/graphql", "params": dict(variables), "body": query})
        if self.graphql_handler is None:
            raise GitHubError(404, "POST", "/graphql", "no fake graphql handler")
        return self.graphql_handler(query, variables)

    # -- assertions helpers ------------------------------------------------

    def calls_to(self, method: str, pattern: str) -> list:
        regex = re.compile(pattern + r"\Z")
        return [c for c in self.calls if c["method"] == method.upper() and regex.match(c["path"])]
