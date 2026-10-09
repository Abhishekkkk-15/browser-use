from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm.base import BaseChatModel
from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.platforms import get_platform_adapter
from job_agent.tools.job_tools import create_job_tools

logger = logging.getLogger(__name__)


class SearchAgent:
	"""Autonomous browser agent that searches job boards and catalogs matching vacancies."""

	def __init__(
		self,
		tracker: JobTracker,
		user_profile: UserProfile,
		preferences: JobPreferences,
		agent_config: AgentConfig,
		llm: BaseChatModel,
		browser_session: BrowserSession | None = None,
	):
		self.tracker = tracker
		self.user_profile = user_profile
		self.preferences = preferences
		self.agent_config = agent_config
		self.llm = llm
		self.browser_session = browser_session
		self.tools = create_job_tools(self.tracker, self.user_profile, self.preferences)

	def _build_sensitive_data(self) -> dict[str, dict[str, str]]:
		"""Build domain-specific credentials map for stealth authentication without LLM exposure."""
		creds: dict[str, dict[str, str]] = {}
		li_user = os.getenv('LINKEDIN_EMAIL')
		li_pass = os.getenv('LINKEDIN_PASSWORD')
		if li_user and li_pass:
			creds['linkedin.com'] = {'email': li_user, 'password': li_pass}

		nk_user = os.getenv('NAUKRI_EMAIL')
		nk_pass = os.getenv('NAUKRI_PASSWORD')
		if nk_user and nk_pass:
			creds['naukri.com'] = {'email': nk_user, 'password': nk_pass}

		wf_user = os.getenv('WELLFOUND_EMAIL')
		wf_pass = os.getenv('WELLFOUND_PASSWORD')
		if wf_user and wf_pass:
			creds['wellfound.com'] = {'email': wf_user, 'password': wf_pass}

		return creds

	async def search_platform(self, platform: str) -> list[dict[str, Any]]:
		"""Execute autonomous search on a designated platform."""
		adapter = get_platform_adapter(platform)
		task_prompt = adapter.get_search_task(self.user_profile, self.preferences)

		logger.info(f'🔎 Starting job search on {platform.upper()}...')

		sensitive_data = self._build_sensitive_data()

		# Initialize session if not provided externally
		should_close_session = False
		session = self.browser_session
		if session is None:
			storage_arg = str(self.agent_config.storage_state_path) if self.agent_config.storage_state_path.exists() else None
			profile = BrowserProfile(
				user_data_dir=self.agent_config.chrome_user_data_dir,
				storage_state=storage_arg,
				cdp_url=self.agent_config.cdp_url,
				headless=self.agent_config.headless,
				demo_mode=self.agent_config.demo_mode,
				keep_alive=True,
				ignore_default_args=[
					'--disable-window-activation',
					'--disable-focus-on-load',
				],
			)
			session = BrowserSession(browser_profile=profile)
			should_close_session = True

		try:
			agent = Agent(
				task=task_prompt,
				llm=self.llm,
				browser=session,
				tools=self.tools,
				demo_mode=self.agent_config.demo_mode,
				use_vision=True,
				max_actions_per_step=4,
				max_failures=5,
				extend_system_message="""
CRITICAL SEARCH & COMPLETION RULES:
1. NEVER call the 'done' action prematurely after only 3-5 steps or when a search filter yields 0 results.
2. If a search filter yields 0 results, you MUST recover by:
   - Removing conflicting filter pills (like narrow experience or location pills).
   - Clearing filters or refreshing/navigating back to restore the full listings.
   - Scrolling through the active visible feed to find and inspect jobs.
3. STRICT RELEVANCE: Only call 'save_job' for postings that genuinely match candidate's target roles, stack, and location. Skip irrelevant postings without calling save_job.
4. If save_job returns 'REJECTED AS NOT RELEVANT', respect the rejection and continue searching for better-fitting roles.
5. If hiring team / recruiter / founder info is visible on the posting, call 'save_hr_contact'.
""",
				sensitive_data=sensitive_data if sensitive_data else None,
			)

			history = await agent.run(max_steps=40)
			logger.info(f'Completed search run on {platform}. Success: {history.is_successful()}')
		finally:
			if should_close_session and session:
				await session.stop()

		return self.tracker.get_pending_jobs(platform=platform, limit=self.preferences.max_searches_per_platform)

	async def run_all(self) -> dict[str, int]:
		"""Run search sequentially across all configured platforms."""
		results: dict[str, int] = {}
		for platform in self.preferences.platforms:
			try:
				jobs = await self.search_platform(platform)
				results[platform] = len(jobs)
				await asyncio.sleep(5)  # Human-like cooldown between platforms
			except Exception as e:
				logger.error(f'Error during {platform} search: {e}', exc_info=True)
				results[platform] = 0
		return results
