"""
This is an example of how to use the MaritimeTools to let an agent use a real computer.

Maritime (maritime.sh) runs cloud computers for agents: Linux and Windows desktops and Android phones.
The agent sees the screen through screenshots and acts with clicks, typing and key presses.

Prerequisites:
- Create a Maritime account and an API key
- Set the API key as an environment variable:
    export MARITIME_API_KEY=<your-api-key>
- Use a model that can read images
"""

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools.maritime import MaritimeTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

# One persistent computer per end user: the same external_user_id always returns the same computer.
# Use os="windows" for a Windows desktop or os="android" for an Android phone.
maritime = MaritimeTools(
    external_user_id="cookbook-user", os="linux", enable_shell=True
)

agent = Agent(
    model=OpenAIResponses(id="gpt-5.6-luna"),
    tools=[maritime],
    instructions=[
        "You control a computer through screenshots.",
        "Take a screenshot first, then act, and check the screenshot each action returns.",
        "Coordinates are pixels of the last screenshot.",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        agent.print_response(
            "Open a terminal, check which operating system this computer runs, and describe what is on the screen."
        )
    finally:
        # End the session so the computer goes to sleep.
        maritime.close_session()
