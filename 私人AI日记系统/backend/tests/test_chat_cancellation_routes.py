from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.routes import agent as agent_routes
from app.routes import companion as companion_routes


class ChatCancellationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_cancel_targets_only_desktop_source(self) -> None:
        cancel = AsyncMock(return_value=1)
        with (
            patch.object(agent_routes, "_conversation_id", return_value="desktop_test"),
            patch.object(agent_routes.chat_run_coordinator, "cancel", cancel),
        ):
            result = await agent_routes.cancel_agent_chat(
                agent_routes.AgentChatCancelRequest(
                    conversation_id="desktop_test",
                    client_request_id="request-1",
                )
            )

        self.assertEqual(result["cancelled"], 1)
        cancel.assert_awaited_once_with(
            "desktop_test",
            source="desktop",
            reason="user_cancelled:request-1",
        )

    async def test_companion_cancel_does_not_cancel_phone_turns(self) -> None:
        cancel = AsyncMock(return_value=1)
        with patch.object(companion_routes.chat_run_coordinator, "cancel", cancel):
            result = await companion_routes.cancel_companion_chat(
                companion_routes.CompanionChatCancelRequest(client_request_id="pet-1")
            )

        self.assertEqual(result["cancelled"], 1)
        cancel.assert_awaited_once_with(
            companion_routes.DESKTOP_PET_CONVERSATION_ID,
            source="desktop_pet",
            reason="user_cancelled:pet-1",
        )


if __name__ == "__main__":
    unittest.main()
