"""Scripted external API shared by the HTTP client tests."""

import httpx

SERVICE = "test_api"
BASE_URL = "https://api.test/v2.1"


class Upstream:
    """Scripted API: plays `steps` in order, repeats the last, records requests."""

    def __init__(self, *steps: httpx.Response | Exception) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return httpx.Response(
            step.status_code, headers=step.headers, content=step.content
        )
