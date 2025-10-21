import logging
#  (C) Copyright
#  Logivations GmbH, Munich 2025
import logging
from typing import Any, Dict, Optional

import zulip

from tools.utils import get_expanded_appconfig
from watcher.schemas import (
    ZulipStatus,
)
logger = logging.getLogger(__name__)


class ZulipClient:
    def __init__(self, server_url: str, user_email: str, api_token: str):
        self.client = zulip.Client(site=server_url, email=user_email, api_key=api_token)
        self.user = self.get_user_by_email(user_email)
        if self.user is None:
            raise ValueError("Failed to fetch user details.")
        logger.info(f"Zulip client initialized. User: {self.user}")

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch user details by email."""
        result = self.client.call_endpoint(
            url=f"/users/{email}",
            method="GET",
        )
        if result["result"] == "success":
            return result.get("user", None)

    def get_user_status(self) -> Optional[ZulipStatus]:
        """Fetch the current user's status."""
        try:
            result = self.client.call_endpoint(
                url=f"/users/{self.user['user_id']}/status",
                method="GET",
            )
            logger.debug(f"Get status response: {result}")
            if result["result"] == "success":
                return ZulipStatus(**result.get("status", {}))
            else:
                logger.warning(f"Failed to fetch user status: {result}")
                return None
        except Exception as e:
            logger.error(f"Error fetching user status: {e}")
            return None

    def update_user_status(self, zulip_status: ZulipStatus) -> bool:
        """Update the current user's status."""
        try:
            request = {
                "status_text": zulip_status.status_text,
                "away": False,
                "emoji_name": zulip_status.emoji_name,
                "reaction_type": zulip_status.reaction_type,
            }

            result = self.client.call_endpoint(
                url="/users/me/status", method="POST", request=request
            )
            logger.debug(f"Update status response: {result}")
            return result["result"] == "success"
        except Exception as e:
            logger.error(f"Error updating user status: {e}")
            return False
