"""Unit tests for MaritimeTools class."""

import base64
import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from agno.tools.maritime import MaritimeTools

TEST_API_KEY = "mk_test_key"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"
SCREENSHOT = {
    "action": "screenshot",
    "ok": True,
    "frameId": 7,
    "width": 1280,
    "height": 800,
    "mime": "image/png",
    "imageB64": base64.b64encode(PNG_BYTES).decode(),
}


def _mock_response(payload, status_code=200):
    response = Mock(spec=httpx.Response)
    response.status_code = status_code
    response.content = json.dumps(payload).encode() if payload is not None else b""
    response.json.return_value = payload
    if status_code >= 400:
        request = httpx.Request("POST", "https://api.maritime.sh")
        real = httpx.Response(status_code, request=request, text=json.dumps(payload))
        response.text = real.text
        response.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=request, response=real)
    else:
        response.raise_for_status.return_value = None
    return response


class FakeApi:
    """Records every request; answers computer creation with an id and actions with a screenshot."""

    def __init__(self, action_result=None):
        self.calls = []
        self.action_result = action_result or SCREENSHOT

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        if url.endswith("/api/v1/computers"):
            return _mock_response({"id": "cmp_new", "os": (json or {}).get("os", "linux")}, 201)
        if url.endswith("/actions"):
            return _mock_response(self.action_result)
        if url.endswith("/exec"):
            return _mock_response({"exitCode": 0, "stdout": "hello\n", "stderr": ""})
        if url.endswith("/viewer"):
            return _mock_response({"url": "https://maritime.sh/v/abc", "mode": "control"})
        if url.endswith("/sessions/close"):
            return _mock_response(None)
        raise AssertionError(f"unexpected request {method} {url}")


@pytest.fixture
def api():
    fake = FakeApi()
    with patch("agno.tools.maritime.httpx.request", side_effect=fake):
        yield fake


@pytest.fixture
def tools():
    return MaritimeTools(api_key=TEST_API_KEY, computer_id="cmp_1", all=True)


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


def test_init_with_env_var():
    with patch.dict("os.environ", {"MARITIME_API_KEY": TEST_API_KEY}, clear=True):
        t = MaritimeTools()
        assert t.api_key == TEST_API_KEY
        assert t.os == "linux"
        assert t.api_base_url == "https://api.maritime.sh"


def test_default_tools_registered():
    t = MaritimeTools(api_key=TEST_API_KEY)
    names = set(t.functions.keys())
    assert {"take_screenshot", "click", "move_mouse", "drag", "scroll", "type_text", "press_key"} <= names
    assert "run_shell_command" not in names
    assert "get_takeover_link" not in names


def test_all_flag_registers_every_tool(tools):
    assert {"run_shell_command", "get_takeover_link"} <= set(tools.functions.keys())


def test_missing_api_key_returns_error_without_calling_api():
    with patch.dict("os.environ", {}, clear=True), patch("agno.tools.maritime.httpx.request") as request:
        t = MaritimeTools(all=True)
        assert t.take_screenshot().content == "Error: MARITIME_API_KEY not set"
        assert t.run_shell_command("ls") == "Error: MARITIME_API_KEY not set"
        request.assert_not_called()


# ============================================================================
# COMPUTER CREATION
# ============================================================================


def test_creates_computer_once_on_first_use(api):
    t = MaritimeTools(api_key=TEST_API_KEY, external_user_id="user_42", os="windows")
    t.take_screenshot()
    t.click(10, 20)
    creates = [c for c in api.calls if c["url"].endswith("/api/v1/computers")]
    assert len(creates) == 1
    assert creates[0]["json"] == {"externalUserId": "user_42", "os": "windows"}
    assert t.computer_id == "cmp_new"
    assert api.calls[-1]["url"].endswith("/api/v1/computers/cmp_new/actions")


def test_existing_computer_is_not_created(api, tools):
    tools.take_screenshot()
    assert not any(c["url"].endswith("/api/v1/computers") for c in api.calls)
    assert api.calls[0]["headers"]["Authorization"] == f"Bearer {TEST_API_KEY}"


# ============================================================================
# ACTIONS
# ============================================================================


def test_screenshot_returns_image_and_frame_size(api, tools):
    result = tools.take_screenshot()
    assert json.loads(result.content) == {"action": "screenshot", "width": 1280, "height": 800, "frameId": 7}
    assert len(result.images) == 1
    assert result.images[0].content == PNG_BYTES
    assert result.images[0].mime_type == "image/png"
    assert api.calls[0]["json"] == {"action": "screenshot"}


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, {"action": "left_click", "coordinate": [5, 6]}),
        ({"clicks": 2}, {"action": "double_click", "coordinate": [5, 6]}),
        ({"clicks": 3}, {"action": "triple_click", "coordinate": [5, 6]}),
        ({"button": "right"}, {"action": "right_click", "coordinate": [5, 6]}),
        ({"button": "middle"}, {"action": "middle_click", "coordinate": [5, 6]}),
        ({"modifier": "ctrl"}, {"action": "left_click", "coordinate": [5, 6], "modifier": "ctrl"}),
    ],
)
def test_click_maps_to_canonical_action(api, tools, kwargs, expected):
    tools.click(5, 6, **kwargs)
    assert api.calls[-1]["json"] == expected


def test_drag_scroll_type_key_payloads(api, tools):
    tools.drag(1, 2, 3, 4)
    tools.scroll(100, 200, direction="up", amount=5)
    tools.type_text("hello world")
    tools.press_key("ctrl+s", repeat=2)
    tools.move_mouse(9, 9)
    assert [c["json"] for c in api.calls] == [
        {"action": "left_click_drag", "startCoordinate": [1, 2], "coordinate": [3, 4]},
        {"action": "scroll", "coordinate": [100, 200], "scrollDirection": "up", "scrollAmount": 5},
        {"action": "type", "text": "hello world"},
        {"action": "key", "key": "ctrl+s", "repeat": 2},
        {"action": "mouse_move", "coordinate": [9, 9]},
    ]


def test_action_error_result_is_reported():
    fake = FakeApi(action_result={"ok": False, "isError": True, "error": "coordinate outside the screen"})
    with patch("agno.tools.maritime.httpx.request", side_effect=fake):
        t = MaritimeTools(api_key=TEST_API_KEY, computer_id="cmp_1")
        result = t.click(99999, 1)
    assert result.content == "Error: coordinate outside the screen"
    assert not result.images


def test_http_error_is_returned_not_raised(tools):
    with patch(
        "agno.tools.maritime.httpx.request",
        return_value=_mock_response({"detail": "a person holds the desktop"}, status_code=409),
    ):
        result = tools.click(1, 1)
    assert result.content.startswith("Error running click: 409")
    assert "a person holds the desktop" in result.content


def test_result_without_image(tools):
    fake = FakeApi(action_result={"action": "key", "ok": True})
    with patch("agno.tools.maritime.httpx.request", side_effect=fake):
        result = tools.press_key("Return")
    assert json.loads(result.content) == {"action": "key"}
    assert not result.images


# ============================================================================
# SHELL, TAKEOVER, SESSION
# ============================================================================


def test_run_shell_command_caps_timeout(api, tools):
    out = json.loads(tools.run_shell_command("echo hello", timeout_seconds=300))
    assert out["stdout"] == "hello\n"
    assert api.calls[-1]["json"] == {"command": "echo hello", "timeoutS": 30}


def test_takeover_link(api, tools):
    out = json.loads(tools.get_takeover_link(reason="Log in to the bank"))
    assert out["url"] == "https://maritime.sh/v/abc"
    assert api.calls[-1]["json"] == {"mode": "control", "reason": "Log in to the bank"}


def test_close_session(api, tools):
    tools.close_session()
    assert api.calls[-1]["url"].endswith("/api/v1/computers/cmp_1/sessions/close")


def test_close_session_without_computer_does_nothing(api):
    MaritimeTools(api_key=TEST_API_KEY).close_session()
    assert api.calls == []


# ============================================================================
# ASYNC
# ============================================================================


@pytest.fixture
def async_api():
    fake = FakeApi()

    async def request(method, url, json=None, headers=None):
        return fake(method, url, json=json, headers=headers)

    client = AsyncMock()
    client.request.side_effect = request
    client.__aenter__.return_value = client
    with patch("agno.tools.maritime.httpx.AsyncClient", return_value=client):
        yield fake


@pytest.mark.asyncio
async def test_async_creates_computer_and_clicks(async_api):
    t = MaritimeTools(api_key=TEST_API_KEY, os="android")
    result = await t.aclick(3, 4, clicks=2)
    assert async_api.calls[0]["json"] == {"os": "android"}
    assert async_api.calls[1]["url"].endswith("/api/v1/computers/cmp_new/actions")
    assert async_api.calls[1]["json"] == {"action": "double_click", "coordinate": [3, 4]}
    assert result.images[0].content == PNG_BYTES


@pytest.mark.asyncio
async def test_async_shell_and_takeover(async_api, tools):
    assert json.loads(await tools.arun_shell_command("echo hello"))["exitCode"] == 0
    assert json.loads(await tools.aget_takeover_link())["mode"] == "control"
    await tools.aclose_session()
    assert async_api.calls[-1]["url"].endswith("/sessions/close")


def test_async_variants_registered(tools):
    assert set(tools.async_functions.keys()) == set(tools.functions.keys())
