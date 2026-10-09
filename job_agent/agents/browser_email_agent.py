from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm.base import BaseChatModel
from browser_use.llm.messages import UserMessage
from job_agent.config import AgentConfig, JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.tools.job_tools import create_job_tools

logger = logging.getLogger(__name__)
console = Console(legacy_windows=False)


class BrowserEmailAgent:
	"""Autonomous browser agent that sends cold outreach directly via Gmail Web Compose

	or LinkedIn messaging, requiring zero SMTP credentials or external mail APIs.
	"""

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
- Core Skills: {', '.join(self.user_profile.skills[:6])}
- Portfolio: {self.user_profile.portfolio_url or ''}
- GitHub: {self.user_profile.github_url or ''}
- LinkedIn: {self.user_profile.linkedin_url}

FORMAT:
First line must be: Subject: <subject line>
Followed by the body text (140-180 words).
Keep it concise, respectful, highlighting what value the candidate brings to {job.get('company_name')}.
"""
		try:
			response = await self.llm.ainvoke([UserMessage(content=prompt)])
			raw_text = response.completion if hasattr(response, 'completion') else getattr(response, 'output', str(response))
			lines = str(raw_text).strip().splitlines()
			subject_line = f'Application: {job.get("job_title")} - {self.user_profile.name}'
			body_lines: list[str] = []

			for line in lines:
				if line.lower().startswith('subject:'):
					subject_line = line.split(':', 1)[1].strip()
				else:
					body_lines.append(line)

			body_text = '\n'.join(body_lines).strip()
			return subject_line, body_text
		except Exception as e:
			logger.warning(f'LLM cold email drafting failed, using default template: {e}')
			subject_line = f'{job.get("job_title")} Application - {self.user_profile.name}'
			body_text = (
				f'Hi {job.get("hr_name") or "Hiring Team"},\n\n'
				f'I noticed your opening for {job.get("job_title")} at {job.get("company_name")} and wanted to reach out directly.\n\n'
				f'As a {self.user_profile.current_role} with {self.user_profile.years_of_experience}+ years of experience, '
				f'I have extensive hands-on expertise with {", ".join(self.user_profile.skills[:4])}. '
				f'I would love the opportunity to contribute to {job.get("company_name")}.\n\n'
				f'My portfolio: {self.user_profile.portfolio_url or self.user_profile.github_url}\n'
				f'LinkedIn: {self.user_profile.linkedin_url}\n\n'
				f'Best regards,\n{self.user_profile.name}'
			)
			return subject_line, body_text

	async def send_via_gmail_browser(
		self,
		job: dict[str, Any],
		subject: str,
		body: str,
		session: BrowserSession,
	) -> bool:
		"""Navigate to Gmail Web Compose, review/confirm with user if in ask mode, and send."""
		hr_email = str(job.get('hr_email', '')).strip()
		if not hr_email:
			return False

		# Ask mode confirmation before browser action
		if self.preferences.mode == 'ask':
			preview_card = (
				f'[bold white]Recipient:[/bold white] [bold cyan]{job.get("hr_name") or "Hiring Manager"}[/bold cyan] <{hr_email}>\n'
				f'[bold white]Company:[/bold white] {job.get("company_name")} | [bold white]Role:[/bold white] {job.get("job_title")}\n'
				f'[bold white]Subject:[/bold white] [yellow]{subject}[/yellow]\n\n'
				f'[bold green]Email Body:[/bold green]\n{body}'
			)
			console.print()
			console.print(
				Panel(
					preview_card,
					title='[bold yellow]✉️ Confirm Cold Outreach (Ask Mode)[/bold yellow]',
					border_style='yellow',
				)
			)

			loop = asyncio.get_running_loop()
			try:
				choice = await loop.run_in_executor(
					None,
					lambda: Prompt.ask('Send this email via Gmail?', choices=['y', 'n', 's', 'edit'], default='y'),
				)
			except Exception:
				choice = 'y'

			choice_clean = str(choice).strip().lower()
			if choice_clean in ('n', 'no', 's', 'skip'):
				logger.info(f'Skipped sending email to {hr_email} by user request.')
				return False
			elif choice_clean == 'edit':
				new_body = await loop.run_in_executor(
					None,
					lambda: Prompt.ask('Enter edited email body text'),
				)
				if new_body.strip():
					body = new_body.strip()

		# Encode for Google Web Compose URL
		encoded_subject = urllib.parse.quote(subject)
		encoded_body = urllib.parse.quote(body)
		compose_url = f'https://mail.google.com/mail/?view=cm&fs=1&to={hr_email}&su={encoded_subject}&body={encoded_body}'

		task_prompt = f"""
1. Navigate directly to: {compose_url}
2. Wait 3 seconds for the Gmail compose window to populate the recipient, subject, and body.
3. Verify that the 'To' field contains '{hr_email}'.
4. Click the 'Send' button (or press Ctrl+Enter) to dispatch the email.
5. Wait 2 seconds to ensure the 'Message sent' confirmation appears.
6. Call the 'done' action.
"""

		logger.info(f'📧 Opening Gmail in browser to send outreach to {hr_email} at {job.get("company_name")}...')

		agent = Agent(
			task=task_prompt,
			llm=self.llm,
			browser=session,
			tools=self.tools,
			demo_mode=self.agent_config.demo_mode,
			use_vision=True,
			max_actions_per_step=3,
			max_failures=3,
		)

		history = await agent.run(max_steps=12)
		success = bool(history.is_successful())

		if success:
			logger.info(f'✅ Successfully sent browser email to {hr_email} for {job.get("company_name")}')
			# Record in database
			self.tracker.record_cold_email(
				job_id=job['id'],
				recipient_email=hr_email,
				recipient_name=job.get('hr_name'),
				subject=subject,
				body_text=body,
				status='sent',
			)
		else:
			logger.warning(f'⚠️ Browser email flow completed with potential issues for {hr_email}')
			self.tracker.record_cold_email(
				job_id=job['id'],
				recipient_email=hr_email,
				recipient_name=job.get('hr_name'),
				subject=subject,
				body_text=body,
				status='draft',
				error_message='Draft created in browser but send confirmation unverified',
			)

		return success

	async def run_campaign(self, limit: int = 5) -> int:
		"""Find candidates with verified HR emails and dispatch browser-native outreach."""
		jobs_with_emails = self.tracker.get_jobs_with_hr_email(limit=limit)

		if not jobs_with_emails:
			logger.info('No jobs found with HR emails pending outreach.')
			return 0

		logger.info(f'📧 Starting browser-native outreach campaign for {len(jobs_with_emails)} contacts...')

		# Set up browser session
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

		sent_count = 0
		try:
			for i, job in enumerate(jobs_with_emails, 1):
				hr_email = job.get('hr_email')
				if not hr_email:
					continue

				logger.info(f'[{i}/{len(jobs_with_emails)}] Outreach for {job.get("company_name")} ({hr_email})')
				subject, body = await self.generate_personalized_body(job)
				success = await self.send_via_gmail_browser(job, subject, body, session)
				if success:
					sent_count += 1

				await asyncio.sleep(4)
		finally:
			if should_close_session and session:
				await session.stop()

		logger.info(f'Browser outreach campaign finished. Sent {sent_count} emails.')
		return sent_count
