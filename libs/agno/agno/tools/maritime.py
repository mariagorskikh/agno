import base64
import json
from os import getenv
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agno.media import Image
from agno.tools import Toolkit
from agno.tools.function import ToolResult
from agno.utils.log import log_error, logger

try:
    import httpx
except ImportError:
    raise ImportError("`httpx` not installed. Please install using `pip install httpx`")


class MaritimeTools(Toolkit):
    """
    Maritime (maritime.sh) runs cloud computers for agents: Linux and Windows desktops and Android phones.
    These tools let an agent see the screen, click, type, press keys, scroll and run shell commands on one
    persistent computer, and hand the screen to a person (for a login or a CAPTCHA) with a takeover link.

    Coordinates are pixels of the last screenshot, whose width and height every screenshot reports.

    Args:
        api_key (Optional[str]): Maritime API key. Retrieved from `MARITIME_API_KEY` env variable if not provided.
        computer_id (Optional[str]): Drive this existing computer. If not set, one is created on first use.
        external_user_id (Optional[str]): Your id for the end user. The same id always returns the same computer.
        os (str): Operating system of a new computer: "linux", "windows" or "android". Default is "linux".
        enable_screenshot (bool): Enable taking screenshots. Default is True.
        enable_mouse (bool): Enable clicking, moving, dragging and scrolling. Default is True.
        enable_keyboard (bool): Enable typing text and pressing keys. Default is True.
        enable_shell (bool): Enable running shell commands on the computer. Default is False.
        enable_takeover (bool): Enable creating a link that hands the screen to a person. Default is False.
        all (bool): Enable all tools. Overrides individual flags when True. Default is False.
        api_base_url (str): Base URL for the Maritime API. Default is "https://api.maritime.sh".
        timeout (int): Timeout in seconds for each API request. Default is 120.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        computer_id: Optional[str] = None,
        external_user_id: Optional[str] = None,
        os: str = "linux",
        enable_screenshot: bool = True,
        enable_mouse: bool = True,
        enable_keyboard: bool = True,
        enable_shell: bool = False,
        enable_takeover: bool = False,
        all: bool = False,
        api_base_url: str = "https://api.maritime.sh",
        timeout: int = 120,
        **kwargs,
    ):
        self.api_key: Optional[str] = api_key or getenv("MARITIME_API_KEY")
        if not self.api_key:
            log_error("MARITIME_API_KEY not set. Please set the MARITIME_API_KEY environment variable.")

        self.computer_id: Optional[str] = computer_id
        self.external_user_id: Optional[str] = external_user_id
        self.os: str = os
        self.api_base_url: str = api_base_url.rstrip("/")
        self.timeout: int = timeout

        tools: List[Any] = []
        async_tools: List[Any] = []
        if all or enable_screenshot:
            tools.append(self.take_screenshot)
            async_tools.append((self.atake_screenshot, "take_screenshot"))
        if all or enable_mouse:
            tools.extend([self.click, self.move_mouse, self.drag, self.scroll])
            async_tools.extend(
                [
                    (self.aclick, "click"),
                    (self.amove_mouse, "move_mouse"),
                    (self.adrag, "drag"),
                    (self.ascroll, "scroll"),
                ]
            )
        if all or enable_keyboard:
            tools.extend([self.type_text, self.press_key])
            async_tools.extend([(self.atype_text, "type_text"), (self.apress_key, "press_key")])
        if all or enable_shell:
            tools.append(self.run_shell_command)
            async_tools.append((self.arun_shell_command, "run_shell_command"))
        if all or enable_takeover:
            tools.append(self.get_takeover_link)
            async_tools.append((self.aget_takeover_link, "get_takeover_link"))

        super().__init__(name="maritime_tools", tools=tools, async_tools=async_tools, **kwargs)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _body(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return {k: v for k, v in payload.items() if v is not None} if payload is not None else None

    @staticmethod
    def _json(response: httpx.Response) -> Dict[str, Any]:
        response.raise_for_status()
        return response.json() if response.content else {}

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = httpx.request(
            method,
            f"{self.api_base_url}{path}",
            json=self._body(payload),
            headers=self._headers(),
            timeout=self.timeout,
        )
        return self._json(response)

    async def _arequest(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method, f"{self.api_base_url}{path}", json=self._body(payload), headers=self._headers()
            )
        return self._json(response)

    def _create_payload(self) -> Dict[str, Any]:
        return {"externalUserId": self.external_user_id, "os": self.os}

    def _computer(self) -> str:
        """Return the computer id, creating the computer on first use."""
        if not self.computer_id:
            self.computer_id = self._request("POST", "/api/v1/computers", self._create_payload())["id"]
        return self.computer_id

    async def _acomputer(self) -> str:
        """Return the computer id, creating the computer on first use."""
        if not self.computer_id:
            self.computer_id = (await self._arequest("POST", "/api/v1/computers", self._create_payload()))["id"]
        return self.computer_id

    @staticmethod
    def _to_tool_result(result: Dict[str, Any]) -> ToolResult:
        """Turn an action result into a ToolResult carrying the screenshot taken after the action."""
        if result.get("isError") or result.get("ok") is False:
            return ToolResult(content=f"Error: {result.get('error') or result.get('reason') or 'action failed'}")

        summary = {k: result.get(k) for k in ("action", "width", "height", "frameId", "note") if result.get(k)}
        image_b64 = result.get("imageB64")
        if not image_b64:
            return ToolResult(content=json.dumps(summary))
        mime_type = result.get("mime") or "image/png"
        image = Image(
            id=str(uuid4()),
            content=base64.b64decode(image_b64),
            mime_type=mime_type,
            format=mime_type.split("/")[-1],
        )
        return ToolResult(content=json.dumps(summary), images=[image])

    @staticmethod
    def _error(name: str, e: Exception) -> str:
        logger.exception(f"Maritime {name} request failed")
        if isinstance(e, httpx.HTTPStatusError):
            return f"Error running {name}: {e.response.status_code} {e.response.text}"
        return f"Error running {name}: {e}"

    def _run(self, name: str, action: Dict[str, Any]) -> ToolResult:
        if not self.api_key:
            return ToolResult(content="Error: MARITIME_API_KEY not set")
        try:
            return self._to_tool_result(self._request("POST", f"/api/v1/computers/{self._computer()}/actions", action))
        except Exception as e:
            return ToolResult(content=self._error(name, e))

    async def _arun(self, name: str, action: Dict[str, Any]) -> ToolResult:
        if not self.api_key:
            return ToolResult(content="Error: MARITIME_API_KEY not set")
        try:
            path = f"/api/v1/computers/{await self._acomputer()}/actions"
            return self._to_tool_result(await self._arequest("POST", path, action))
        except Exception as e:
            return ToolResult(content=self._error(name, e))

    @staticmethod
    def _click_action(x: int, y: int, button: str, clicks: int, modifier: Optional[str]) -> Dict[str, Any]:
        if button == "left":
            action = {1: "left_click", 2: "double_click", 3: "triple_click"}.get(clicks, "left_click")
        else:
            action = f"{button}_click"
        return {"action": action, "coordinate": [x, y], "modifier": modifier}

    def take_screenshot(self) -> ToolResult:
        """Use this function to see the computer's screen.

        Returns:
            ToolResult: The screenshot, with its width and height in pixels.
        """
        return self._run("screenshot", {"action": "screenshot"})

    async def atake_screenshot(self) -> ToolResult:
        """Use this function to see the computer's screen.

        Returns:
            ToolResult: The screenshot, with its width and height in pixels.
        """
        return await self._arun("screenshot", {"action": "screenshot"})

    def click(
        self, x: int, y: int, button: str = "left", clicks: int = 1, modifier: Optional[str] = None
    ) -> ToolResult:
        """Use this function to click on the screen.

        Args:
            x (int): Horizontal pixel position in the last screenshot.
            y (int): Vertical pixel position in the last screenshot.
            button (str): "left", "right" or "middle". Defaults to "left".
            clicks (int): 1, 2 or 3 for a single, double or triple click. Defaults to 1.
            modifier (Optional[str]): Key held during the click, such as "ctrl" or "shift".

        Returns:
            ToolResult: The screenshot after the click.
        """
        return self._run("click", self._click_action(x, y, button, clicks, modifier))

    async def aclick(
        self, x: int, y: int, button: str = "left", clicks: int = 1, modifier: Optional[str] = None
    ) -> ToolResult:
        """Use this function to click on the screen.

        Args:
            x (int): Horizontal pixel position in the last screenshot.
            y (int): Vertical pixel position in the last screenshot.
            button (str): "left", "right" or "middle". Defaults to "left".
            clicks (int): 1, 2 or 3 for a single, double or triple click. Defaults to 1.
            modifier (Optional[str]): Key held during the click, such as "ctrl" or "shift".

        Returns:
            ToolResult: The screenshot after the click.
        """
        return await self._arun("click", self._click_action(x, y, button, clicks, modifier))

    def move_mouse(self, x: int, y: int) -> ToolResult:
        """Use this function to move the mouse without clicking, for example to open a hover menu.

        Args:
            x (int): Horizontal pixel position in the last screenshot.
            y (int): Vertical pixel position in the last screenshot.

        Returns:
            ToolResult: The screenshot after the move.
        """
        return self._run("move_mouse", {"action": "mouse_move", "coordinate": [x, y]})

    async def amove_mouse(self, x: int, y: int) -> ToolResult:
        """Use this function to move the mouse without clicking, for example to open a hover menu.

        Args:
            x (int): Horizontal pixel position in the last screenshot.
            y (int): Vertical pixel position in the last screenshot.

        Returns:
            ToolResult: The screenshot after the move.
        """
        return await self._arun("move_mouse", {"action": "mouse_move", "coordinate": [x, y]})

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> ToolResult:
        """Use this function to drag with the left mouse button held, or to swipe on a phone.

        Args:
            start_x (int): Horizontal start position.
            start_y (int): Vertical start position.
            end_x (int): Horizontal end position.
            end_y (int): Vertical end position.

        Returns:
            ToolResult: The screenshot after the drag.
        """
        return self._run(
            "drag",
            {"action": "left_click_drag", "startCoordinate": [start_x, start_y], "coordinate": [end_x, end_y]},
        )

    async def adrag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> ToolResult:
        """Use this function to drag with the left mouse button held, or to swipe on a phone.

        Args:
            start_x (int): Horizontal start position.
            start_y (int): Vertical start position.
            end_x (int): Horizontal end position.
            end_y (int): Vertical end position.

        Returns:
            ToolResult: The screenshot after the drag.
        """
        return await self._arun(
            "drag",
            {"action": "left_click_drag", "startCoordinate": [start_x, start_y], "coordinate": [end_x, end_y]},
        )

    def scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ToolResult:
        """Use this function to scroll at a point on the screen.

        Args:
            x (int): Horizontal pixel position to scroll at.
            y (int): Vertical pixel position to scroll at.
            direction (str): "up", "down", "left" or "right". Defaults to "down".
            amount (int): Number of wheel clicks, 1 to 100. Defaults to 3.

        Returns:
            ToolResult: The screenshot after scrolling.
        """
        return self._run(
            "scroll",
            {"action": "scroll", "coordinate": [x, y], "scrollDirection": direction, "scrollAmount": amount},
        )

    async def ascroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> ToolResult:
        """Use this function to scroll at a point on the screen.

        Args:
            x (int): Horizontal pixel position to scroll at.
            y (int): Vertical pixel position to scroll at.
            direction (str): "up", "down", "left" or "right". Defaults to "down".
            amount (int): Number of wheel clicks, 1 to 100. Defaults to 3.

        Returns:
            ToolResult: The screenshot after scrolling.
        """
        return await self._arun(
            "scroll",
            {"action": "scroll", "coordinate": [x, y], "scrollDirection": direction, "scrollAmount": amount},
        )

    def type_text(self, text: str) -> ToolResult:
        """Use this function to type text at the current cursor position.

        Args:
            text (str): The text to type.

        Returns:
            ToolResult: The screenshot after typing.
        """
        return self._run("type_text", {"action": "type", "text": text})

    async def atype_text(self, text: str) -> ToolResult:
        """Use this function to type text at the current cursor position.

        Args:
            text (str): The text to type.

        Returns:
            ToolResult: The screenshot after typing.
        """
        return await self._arun("type_text", {"action": "type", "text": text})

    def press_key(self, key: str, repeat: int = 1) -> ToolResult:
        """Use this function to press a key or a key combination.

        Args:
            key (str): An xdotool key name such as "Return", "Tab", "Escape" or "ctrl+s".
            repeat (int): How many times to press it, 1 to 100. Defaults to 1.

        Returns:
            ToolResult: The screenshot after the key press.
        """
        return self._run("press_key", {"action": "key", "key": key, "repeat": repeat})

    async def apress_key(self, key: str, repeat: int = 1) -> ToolResult:
        """Use this function to press a key or a key combination.

        Args:
            key (str): An xdotool key name such as "Return", "Tab", "Escape" or "ctrl+s".
            repeat (int): How many times to press it, 1 to 100. Defaults to 1.

        Returns:
            ToolResult: The screenshot after the key press.
        """
        return await self._arun("press_key", {"action": "key", "key": key, "repeat": repeat})

    def run_shell_command(self, command: str, timeout_seconds: int = 30) -> str:
        """Use this function to run a shell command on the computer.

        Args:
            command (str): The command to run.
            timeout_seconds (int): Timeout in seconds, at most 30. Defaults to 30.

        Returns:
            str: JSON string with exitCode, stdout and stderr.
        """
        if not self.api_key:
            return "Error: MARITIME_API_KEY not set"
        try:
            payload = {"command": command, "timeoutS": min(timeout_seconds, 30)}
            return json.dumps(self._request("POST", f"/api/v1/computers/{self._computer()}/exec", payload))
        except Exception as e:
            return self._error("run_shell_command", e)

    async def arun_shell_command(self, command: str, timeout_seconds: int = 30) -> str:
        """Use this function to run a shell command on the computer.

        Args:
            command (str): The command to run.
            timeout_seconds (int): Timeout in seconds, at most 30. Defaults to 30.

        Returns:
            str: JSON string with exitCode, stdout and stderr.
        """
        if not self.api_key:
            return "Error: MARITIME_API_KEY not set"
        try:
            payload = {"command": command, "timeoutS": min(timeout_seconds, 30)}
            return json.dumps(
                await self._arequest("POST", f"/api/v1/computers/{await self._acomputer()}/exec", payload)
            )
        except Exception as e:
            return self._error("run_shell_command", e)

    def get_takeover_link(self, reason: Optional[str] = None) -> str:
        """Use this function when a person must act on the screen, for example to log in or solve a CAPTCHA.
        Share the link with the user. Actions are refused until they hand the computer back.

        Args:
            reason (Optional[str]): What the person needs to do, shown to them.

        Returns:
            str: JSON string with the takeover link.
        """
        if not self.api_key:
            return "Error: MARITIME_API_KEY not set"
        try:
            payload = {"mode": "control", "reason": reason}
            return json.dumps(self._request("POST", f"/api/v1/computers/{self._computer()}/viewer", payload))
        except Exception as e:
            return self._error("get_takeover_link", e)

    async def aget_takeover_link(self, reason: Optional[str] = None) -> str:
        """Use this function when a person must act on the screen, for example to log in or solve a CAPTCHA.
        Share the link with the user. Actions are refused until they hand the computer back.

        Args:
            reason (Optional[str]): What the person needs to do, shown to them.

        Returns:
            str: JSON string with the takeover link.
        """
        if not self.api_key:
            return "Error: MARITIME_API_KEY not set"
        try:
            payload = {"mode": "control", "reason": reason}
            return json.dumps(
                await self._arequest("POST", f"/api/v1/computers/{await self._acomputer()}/viewer", payload)
            )
        except Exception as e:
            return self._error("get_takeover_link", e)

    def close_session(self) -> None:
        """End the open session so the computer goes to sleep. Call this from your code when the run is done."""
        if self.computer_id and self.api_key:
            self._request("POST", f"/api/v1/computers/{self.computer_id}/sessions/close")

    async def aclose_session(self) -> None:
        """End the open session so the computer goes to sleep. Call this from your code when the run is done."""
        if self.computer_id and self.api_key:
            await self._arequest("POST", f"/api/v1/computers/{self.computer_id}/sessions/close")
