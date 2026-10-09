from __future__ import annotations

import asyncio
import logging
from typing import Any

from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import UserMessage
from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.tools.email_tools import EmailDraft, EmailSender

logger = logging.getLogger(__name__)


class EmailAgent:
	"""Coordinates personalized cold email campaigns to discovered hiring managers and recruiters."""

	def __init__(
		self,
		tracker: JobTracker,
		user_profile: UserProfile,
		preferences: JobPreferences,
		agent_config: AgentConfig,
		llm: BaseChatModel,
	):
		self.tracker = tracker
		self.user_profile = user_profile
		self.preferences = preferences
		self.agent_config = agent_config
		self.llm = llm
		self.sender = EmailSender(
			tracker=self.tracker,
			user_profile=self.user_profile,
			dry_run=self.preferences.dry_run,
		)

	async def generate_personalized_body(self, job: dict[str, Any]) -> tuple[str, str]:
		"""Use LLM to generate a customized cold email subject line and body."""
		prompt = f"""
Write an outstanding cold outreach email from a software engineer candidate to a recruiter or hiring manager.

RECIPIENT & ROLE:
- Recruiter Name: {job.get('hr_name') or 'Hiring Team'}
- Company: {job.get('company_name')}
- Role: {job.get('job_title')}
- Job requirements summary: {job.get('job_description_summary', '')[:400]}

CANDIDATE:
- Name: {self.user_profile.name}
- Current Role: {self.user_profile.current_role} ({self.user_profile.years_of_experience}+ years experience)
- Core Skills: {', '.join(self.user_profile.skills[:5])}
- LinkedIn: {self.user_profile.linkedin_url}
- GitHub: {self.user_profile.github_url or ''}

FORMAT:
First line must be: Subject: <subject line>
Followed by the body text (180-220 words).
Keep it respectful, focused on what the candidate can build for {job.get('company_name')}, with a direct call to action.
"""
		try:
			response = await self.llm.ainvoke([UserMessage(content=prompt)])
			raw_text = response.completion if hasattr(response, 'completion') else getattr(response, 'output', str(response))
			text = str(raw_text).strip()
			lines = text.splitlines()
			subject_line = f'Application: {job.get("job_title")} - {self.user_profile.name}'
			body_lines: list[str] = []

			for i, line in enumerate(lines):
				if line.lower().startswith('subject:'):
					subject_line = line.split(':', 1)[1].strip()
				else:
					body_lines.append(line)

			body_text = '\n'.join(body_lines).strip()
			return subject_line, body_text
		except Exception as e:
			logger.warning(f'LLM cold email drafting failed, using template: {e}')
			draft = self.sender.draft_cold_email(
				job_title=job.get('job_title', 'Software Engineer'),
				company_name=job.get('company_name', 'Company'),
				hr_name=job.get('hr_name'),
				job_description_summary=job.get('job_description_summary', ''),
			)
			return draft.subject, draft.body_text

	async def run_campaign(self, limit: int = 10) -> int:
		"""Find jobs with HR emails and dispatch tailored cold outreach."""
		jobs_with_emails = self.tracker.get_jobs_with_hr_email(limit=limit)

		if not jobs_with_emails:
			logger.info('No candidates found with verified HR emails pending outreach.')
			return 0

		logger.info(f'📧 Starting outreach campaign for {len(jobs_with_emails)} contacts...')
		sent_count = 0

		for job in jobs_with_emails:
			hr_email = job.get('hr_email')
			if not hr_email:
				continue

			subject, body_text = await self.generate_personalized_body(job)
			draft = EmailDraft(
				to_email=hr_email,
				to_name=job.get('hr_name'),
				subject=subject,
				body_text=body_text,
			)

			success = self.sender.send_email(job_id=job['id'], draft=draft)
			if success:
				sent_count += 1

			await asyncio.sleep(2)  # Cooldown between emails

		logger.info(f'Outreach campaign complete. Sent/simulated {sent_count} emails.')
		return sent_count
