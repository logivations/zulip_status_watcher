#  (C) Copyright
#  Logivations GmbH, Munich 2025
import logging
import os
from typing import List

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


class GroupsClient:
    def __init__(self, credentials_file: str, admin_email: str):
        """
        Initialize the Groups client.

        Args:
            credentials_file: Path to service account credentials JSON
            admin_email: Admin email to impersonate for directory API access
        """
        self.credentials_file = credentials_file
        self.admin_email = admin_email
        self.scopes = [
            "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
            "https://www.googleapis.com/auth/admin.directory.user.readonly",
        ]
        self.service = self._authenticate()

    def _authenticate(self):
        """Authenticate and return the Admin Directory service."""
        if not os.path.exists(self.credentials_file):
            logger.error(f"Credential file not found: {self.credentials_file}")
            raise FileNotFoundError(
                f"Credential file not found: {self.credentials_file}"
            )

        creds = ServiceAccountCredentials.from_service_account_file(
            self.credentials_file, scopes=self.scopes
        )
        creds = creds.with_subject(self.admin_email)

        return build("admin", "directory_v1", credentials=creds)

    def get_group_members(self, group_email: str) -> List[str]:
        """
        Get all member emails from a Google Group.

        Args:
            group_email: The email address of the Google Group

        Returns:
            List of member email addresses
        """
        try:
            members = []
            request = self.service.members().list(groupKey=group_email)

            while request is not None:
                response = request.execute()
                for member in response.get("members", []):
                    if member.get("type") == "USER":
                        members.append(member.get("email"))
                request = self.service.members().list_next(request, response)

            logger.info(f"Found {len(members)} members in group {group_email}")
            return members
        except Exception as e:
            logger.error(f"Error fetching group members: {e}")
            return []

    def get_all_domain_users(self, domain: str = None, customer: str = "my_customer") -> List[str]:
        """
        Get all active user emails in the Google Workspace domain.

        Args:
            domain: Optional specific domain to query. If None, uses `customer`.
            customer: Customer ID for multi-domain workspaces. Defaults to "my_customer".

        Returns:
            List of user email addresses (active, non-suspended only).
        """
        try:
            users = []
            list_kwargs = {"maxResults": 500, "orderBy": "email"}
            if domain:
                list_kwargs["domain"] = domain
            else:
                list_kwargs["customer"] = customer

            request = self.service.users().list(**list_kwargs)
            while request is not None:
                response = request.execute()
                for user in response.get("users", []):
                    if user.get("suspended") or user.get("archived"):
                        continue
                    email = user.get("primaryEmail")
                    if email:
                        users.append(email)
                request = self.service.users().list_next(request, response)

            logger.info(f"Found {len(users)} active users in workspace")
            return users
        except Exception as e:
            logger.error(f"Error fetching domain users: {e}")
            return []
