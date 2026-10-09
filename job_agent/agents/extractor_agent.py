from __future__ import annotations

import asyncio
import logging
from typing import Any

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm.base import BaseChatModel
from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.prompts.extractor_prompt import build_extractor_prompt
from job_agent.tools.job_tools import create_job_tools

logger = logging.getLogger(__name__)


class ExtractorAgent:
	"""Autonomous intelligence agent that hunts for HR contacts, emails, and recruiter LinkedIn profiles."""

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

	async def extract_contacts_for_job(self, job: dict[str, Any], session: BrowserSession) -> None:
		"""Run browser extraction agent for a specific job posting."""
		company_name = job.get('company_name', 'Target Company')
		job_url = job.get('job_url', '')

		logger.info(f'🕵️ Hunting recruiter contacts for {company_name} ({job.get("job_title")})...')
		prompt = build_extractor_prompt(job)

		agent = Agent(
			task=prompt,
			llm=self.llm,
			browser=session,
			tools=self.tools,
			use_vision=True,
			max_actions_per_step=3,
			max_failures=3,
		)

		await agent.run(max_steps=25)

	async def run_batch(self, limit: int = 15) -> int:
		"""Extract HR contacts for discovered jobs lacking contact info."""
		jobs = self.tracker.get_pending_jobs(limit=limit)
		jobs_needing_contacts = [j for j in jobs if not j.get('hr_email') and not j.get('hr_linkedin')]

		if not jobs_needing_contacts:
			logger.info('No jobs currently needing contact extraction.')
			return 0

		logger.info(f'🔍 Starting contact extraction for {len(jobs_needing_contacts)} companies...')

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
			)
			session = BrowserSession(browser_profile=profile)
			should_close_session = True

		processed = 0
		try:
			for job in jobs_needing_contacts:
				await self.extract_contacts_for_job(job, session)
				processed += 1
				await asyncio.sleep(4)
		finally:
			if should_close_session and session:
				await session.stop()

		return processed
