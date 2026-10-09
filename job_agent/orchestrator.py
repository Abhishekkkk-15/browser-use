from __future__ import annotations

import logging
import os
from typing import Any

from browser_use.llm.base import BaseChatModel
from job_agent.agents.application_agent import ApplicationAgent
from job_agent.agents.email_agent import EmailAgent
from job_agent.agents.extractor_agent import ExtractorAgent
from job_agent.agents.search_agent import SearchAgent
from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker

logger = logging.getLogger(__name__)


def get_default_llm() -> BaseChatModel:
	"""Instantiate the recommended LLM according to available API keys."""
	openai_key = os.getenv('OPENAI_API_KEY')
	openai_base_url = os.getenv('OPENAI_BASE_URL') or os.getenv('OPENAI_API_BASE') or os.getenv('OPENAI_ENDPOINT')
	openai_model = os.getenv('OPENAI_MODEL', 'gpt-5.6-luna')
	bu_key = os.getenv('BROWSER_USE_API_KEY')
	anthropic_key = os.getenv('ANTHROPIC_API_KEY')
	google_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')

	if openai_key or openai_base_url or os.getenv('OPENAI_MODEL'):
		from browser_use.llm.openai.chat import ChatOpenAI

		logger.info(f'Using ChatOpenAI model: {openai_model}' + (f' (base_url: {openai_base_url})' if openai_base_url else ''))
		return ChatOpenAI(model=openai_model, base_url=openai_base_url)
	elif bu_key:
		from browser_use.llm.browser_use.chat import ChatBrowserUse

		logger.info('Using ChatBrowserUse model (recommended for browser automation)')
		return ChatBrowserUse()
	elif google_key:
		from browser_use.llm.google.chat import ChatGoogle

		logger.info('Using ChatGoogle model (Gemini)')
		return ChatGoogle()
	elif anthropic_key:
		from browser_use.llm.anthropic.chat import ChatAnthropic

		logger.info('Using ChatAnthropic model (Claude)')
		return ChatAnthropic()
	else:
		from browser_use.llm.openai.chat import ChatOpenAI

		return ChatOpenAI(model=openai_model, base_url=openai_base_url)


class JobAgentOrchestrator:
	"""Top-level controller coordinating the entire multi-agent job application ecosystem."""

	def __init__(
		self,
		user_profile: UserProfile | None = None,
		preferences: JobPreferences | None = None,
		agent_config: AgentConfig | None = None,
		llm: BaseChatModel | None = None,
	):
		self.user_profile = user_profile or UserProfile.from_env_or_defaults()
		self.preferences = preferences or JobPreferences.from_env_or_defaults()
		self.agent_config = agent_config or AgentConfig()
		self.tracker = JobTracker(self.agent_config.database_path)
		self.llm = llm or get_default_llm()

		# Sub-agents
		self.search_agent = SearchAgent(
			tracker=self.tracker,
			user_profile=self.user_profile,
			preferences=self.preferences,
			agent_config=self.agent_config,
			llm=self.llm,
		)
		self.application_agent = ApplicationAgent(
			tracker=self.tracker,
			user_profile=self.user_profile,
			preferences=self.preferences,
			agent_config=self.agent_config,
			llm=self.llm,
		)
		self.extractor_agent = ExtractorAgent(
			tracker=self.tracker,
			user_profile=self.user_profile,
			preferences=self.preferences,
			agent_config=self.agent_config,
			llm=self.llm,
		)
		self.email_agent = EmailAgent(
			tracker=self.tracker,
			user_profile=self.user_profile,
			preferences=self.preferences,
			agent_config=self.agent_config,
			llm=self.llm,
		)
		from job_agent.agents.browser_email_agent import BrowserEmailAgent

		self.browser_email_agent = BrowserEmailAgent(
			tracker=self.tracker,
			user_profile=self.user_profile,
			preferences=self.preferences,
			agent_config=self.agent_config,
			llm=self.llm,
		)

	async def run_full_pipeline(self) -> dict[str, Any]:
		"""Execute the end-to-end recruitment lifecycle:

		Phase 1: Search & catalog opportunities
		Phase 2: Extract HR contacts & intelligence
		Phase 3: Autonomous form application (Easy Apply)
		Phase 4: Personalized cold email campaign
		"""
		logger.info('=' * 60)
		logger.info('🚀 STARTING AUTONOMOUS JOB APPLICATION PIPELINE')
		logger.info(f'Candidate: {self.user_profile.name} | Target: {self.preferences.target_roles}')
		logger.info(f'Mode: {self.preferences.mode.upper()} | Dry Run: {self.preferences.dry_run}')
		logger.info('=' * 60)

		from browser_use import BrowserProfile, BrowserSession

		# Clean profile locks before startup to eliminate SingletonLock collisions
		self.agent_config.clean_profile_locks()

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
		shared_session = BrowserSession(browser_profile=profile)
		await shared_session.start()

		# Share single persistent browser session across all agents
		self.search_agent.browser_session = shared_session
		self.extractor_agent.browser_session = shared_session
		self.application_agent.browser_session = shared_session
		self.browser_email_agent.browser_session = shared_session

		try:
			# Phase 1: Search across platforms
			logger.info('\n--- PHASE 1: DISCOVERY & SEARCH ---')
			search_counts = await self.search_agent.run_all()

			# Phase 2: Recruiter intelligence extraction
			logger.info('\n--- PHASE 2: RECRUITER INTELLIGENCE EXTRACTION ---')
			extracted_count = await self.extractor_agent.run_batch(limit=10)

			# Phase 3: Applications
			logger.info('\n--- PHASE 3: SUBMITTING APPLICATIONS (BEST-FIT RANKED) ---')
			applied_count = await self.application_agent.run_batch(max_applications=self.preferences.max_applications_per_run)

			# Phase 4: Cold emails (if enabled)
			email_count = 0
			if self.preferences.auto_cold_email:
				logger.info('\n--- PHASE 4: COLD OUTREACH CAMPAIGN (BROWSER-NATIVE) ---')
				if self.preferences.use_browser_email:
					email_count = await self.browser_email_agent.run_campaign(limit=5)
				else:
					email_count = await self.email_agent.run_campaign(limit=5)

		finally:
			try:
				if self.agent_config.storage_state_path:
					await shared_session.export_storage_state(self.agent_config.storage_state_path)
			except Exception as save_err:
				logger.debug(f'Storage state sync notice: {save_err}')

			await shared_session.stop()
			self.search_agent.browser_session = None
			self.extractor_agent.browser_session = None
			self.application_agent.browser_session = None
			self.browser_email_agent.browser_session = None

		stats = self.tracker.get_stats()
		logger.info('\n' + '=' * 60)
		logger.info('🏁 PIPELINE EXECUTION FINISHED')
		logger.info(f'Total Found: {stats["total_found"]}')
		logger.info(f'Total Applied: {stats["total_applied"]}')
		logger.info(f'HR Emails Discovered: {stats["total_hr_emails"]}')
		logger.info(f'Cold Emails Sent: {stats["total_emails_sent"]}')
		logger.info('=' * 60)

		return {
			'search_counts': search_counts,
			'extracted_count': extracted_count,
			'applied_count': applied_count,
			'email_count': email_count,
			'stats': stats,
		}

	async def run_autonomous_pipeline(
		self,
		mode: str = 'ask',
		platforms: list[str] | None = None,
		max_applications: int = 5,
		min_fit_score: float | None = None,
		dry_run: bool = False,
	) -> dict[str, Any]:
		"""High-level autonomous runner handling Free vs Ask mode end-to-end."""
		self.preferences.mode = 'free' if mode == 'free' else 'ask'
		self.preferences.dry_run = dry_run
		self.preferences.max_applications_per_run = max_applications
		if min_fit_score is not None:
			self.preferences.min_fit_score = min_fit_score
		if platforms:
			self.preferences.platforms = platforms  # type: ignore

		return await self.run_full_pipeline()

	async def run_search_only(self) -> dict[str, int]:
		"""Execute only Phase 1: Search and catalog."""
		return await self.search_agent.run_all()

	async def run_apply_only(self, limit: int | None = None) -> int:
		"""Execute only Phase 3: Apply to already discovered pending jobs."""
		return await self.application_agent.run_batch(max_applications=limit)

	async def run_extract_only(self, limit: int = 15) -> int:
		"""Execute only Phase 2: Extract HR contacts for pending jobs."""
		return await self.extractor_agent.run_batch(limit=limit)

	async def run_email_only(self, limit: int = 10) -> int:
		"""Execute only Phase 4: Send cold emails to contacts with known emails."""
		if self.preferences.use_browser_email:
			return await self.browser_email_agent.run_campaign(limit=limit)
		return await self.email_agent.run_campaign(limit=limit)
