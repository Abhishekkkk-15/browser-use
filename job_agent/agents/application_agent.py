from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import UserMessage
from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.platforms import get_platform_adapter
from job_agent.prompts.pitch_prompt import build_pitch_prompt
from job_agent.tools.job_tools import create_job_tools

logger = logging.getLogger(__name__)


class ApplicationAgent:
	"""Autonomous browser agent that fills out application forms, uploads resumes, and applies."""

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

	async def generate_pitch(self, job: dict[str, Any]) -> str:
		"""Generate a tailored pitch/cover note for this specific role and company."""
		try:
			prompt = build_pitch_prompt(job, self.user_profile)
			response = await self.llm.ainvoke([UserMessage(content=prompt)])
			output = response.completion if hasattr(response, 'completion') else getattr(response, 'output', str(response))
			return str(output).strip()
		except Exception as e:
			logger.warning(f'Pitch generation failed via LLM, falling back to default template: {e}')
			return (
				f'I am a {self.user_profile.current_role} with {self.user_profile.years_of_experience}+ years of experience '
				f'specializing in {", ".join(self.user_profile.skills[:4])}. I have built scalable backend architectures and AI agent systems. '
				f"I am very excited about {job.get('company_name', 'your company')}'s mission and would love to contribute."
			)

	async def apply_to_job(
		self,
		job: dict[str, Any],
		pitch: str,
		session: BrowserSession,
	) -> bool:
		"""Apply to a single job posting using the provided browser session."""
		job_url = job.get('job_url', '')
		platform = job.get('platform', 'linkedin')

		if self.tracker.is_already_applied(job_url):
			logger.info(f'Skipping {job_url}: already applied.')
			return False

		adapter = get_platform_adapter(platform)
		task_prompt = adapter.get_apply_task(
			job=job,
			user=self.user_profile,
			pitch=pitch,
			dry_run=self.preferences.dry_run,
			mode=self.preferences.mode,
		)

		available_files: list[str] = []
		if self.user_profile.resume_path.exists():
			available_files.append(str(self.user_profile.resume_path.resolve()))

		logger.info(
			f"📝 Applying to '{job.get('job_title')}' at '{job.get('company_name')}' (Mode: {self.preferences.mode}, Dry Run: {self.preferences.dry_run})...",
		)

		agent = Agent(
			task=task_prompt,
			llm=self.llm,
			browser=session,
			tools=self.tools,
			demo_mode=self.agent_config.demo_mode,
			available_file_paths=available_files,
			use_vision=True,
			max_actions_per_step=4,
			max_failures=4,
		)

		history = await agent.run(max_steps=35)
		success = history.is_successful()

		if success:
			logger.info(f'✅ Application process completed for {job.get("company_name")}')
		else:
			logger.warning(f'⚠️ Application completed with issues or incomplete steps for {job_url}')

		return bool(success)

	async def run_batch(self, max_applications: int | None = None) -> int:
		"""Apply to pending jobs sequentially with human-like delays to avoid bot flags."""
		limit = max_applications or self.preferences.max_applications_per_run
		pending_jobs = self.tracker.get_pending_jobs(limit=limit * 2)

		if not pending_jobs:
			logger.info('No pending jobs found in tracker database to apply for.')
			return 0

		# Sort by Best Fit score descending
		pending_jobs.sort(key=lambda j: j.get('match_score', 0.0), reverse=True)
		qualified_jobs = [j for j in pending_jobs if j.get('match_score', 0.0) >= self.preferences.min_fit_score]
		jobs_to_process = qualified_jobs if qualified_jobs else pending_jobs

		logger.info(
			f'🚀 Found {len(jobs_to_process)} best-fit jobs to process (Min Fit: {self.preferences.min_fit_score}%, Limit: {limit})'
		)

		# Create browser session for batch processing
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

		applied_count = 0
		try:
			for i, job in enumerate(jobs_to_process, 1):
				if applied_count >= limit:
					break

				logger.info(f'[{i}/{len(pending_jobs)}] Processing: {job.get("job_title")} at {job.get("company_name")}')

				try:
					pitch = await self.generate_pitch(job)
					success = await self.apply_to_job(job, pitch, session)
					if success:
						applied_count += 1
				except Exception as job_err:
					logger.error(f'Error applying to {job.get("job_url")}: {job_err}', exc_info=True)
					self.tracker.update_status(
						job_url=job.get('job_url', ''),
						status='failed',
						notes=f'Application error: {str(job_err)[:200]}',
					)

				# Randomized sleep to simulate natural human activity and prevent anti-bot throttling
				delay = random.uniform(
					self.agent_config.action_delay_seconds_min,
					self.agent_config.action_delay_seconds_max,
				)
				logger.info(f'Cooling down for {delay:.1f}s before next application...')
				await asyncio.sleep(delay)

		finally:
			if should_close_session and session:
				await session.stop()

		logger.info(f'Finished application run. Applied to {applied_count} jobs.')
		return applied_count
