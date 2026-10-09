from __future__ import annotations

from typing import Any

from job_agent.config import JobPreferences, UserProfile
from job_agent.platforms.base import PlatformAdapter
from job_agent.prompts.application_prompt import build_application_prompt
from job_agent.prompts.search_prompt import build_naukri_search_prompt


class NaukriAdapter(PlatformAdapter):
	platform_name = 'naukri'
	base_url = 'https://www.naukri.com'

	def get_search_task(self, user: UserProfile, prefs: JobPreferences) -> str:
		return build_naukri_search_prompt(user, prefs)

	def get_apply_task(
		self,
		job: dict[str, Any],
		user: UserProfile,
		pitch: str,
		dry_run: bool = True,
		mode: str = 'ask',
	) -> str:
		return build_application_prompt(job, user, pitch, dry_run=dry_run, mode=mode)
