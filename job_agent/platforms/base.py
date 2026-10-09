from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from job_agent.config import JobPreferences, UserProfile


class PlatformAdapter(ABC):
	"""Base interface for job platform configurations, task generation, and selectors."""

	platform_name: str
	base_url: str

	@abstractmethod
	def get_search_task(self, user: UserProfile, prefs: JobPreferences) -> str:
		"""Generate search task prompt for this platform."""
		pass

	@abstractmethod
	def get_apply_task(
		self,
		job: dict[str, Any],
		user: UserProfile,
		pitch: str,
		dry_run: bool = True,
		mode: str = 'ask',
	) -> str:
		"""Generate application task prompt for this platform."""
		pass
