from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from pydantic import BaseModel

from job_agent.config import UserProfile
from job_agent.database import JobTracker

logger = logging.getLogger(__name__)


class EmailDraft(BaseModel):
	"""Structured cold email draft."""

	to_email: str
	to_name: str | None = None
	subject: str
	body_text: str
	body_html: str | None = None


class EmailSender:
	"""Handles drafting and sending personalized cold emails to hiring managers/recruiters."""

	def __init__(self, tracker: JobTracker, user_profile: UserProfile, dry_run: bool = True):
		self.tracker = tracker
		self.user_profile = user_profile
		self.dry_run = dry_run
		self.smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
		self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
		self.smtp_user = os.getenv('SMTP_USER', '')
		self.smtp_password = os.getenv('SMTP_PASSWORD', '')
		self.sender_email = os.getenv('SMTP_FROM_EMAIL', self.user_profile.email)

	def draft_cold_email(
		self,
		job_title: str,
		company_name: str,
		hr_name: str | None = None,
		job_description_summary: str = '',
	) -> EmailDraft:
		"""Generate a professional, high-converting cold email tailored to the role."""
		greeting = f'Hi {hr_name},' if hr_name else 'Hi Hiring Team,'
		subject = f'Application: {job_title} - {self.user_profile.name} ({self.user_profile.years_of_experience}+ yrs exp)'

		body_text = f"""{greeting}

I noticed your open role for {job_title} at {company_name} and wanted to reach out directly.

With {self.user_profile.years_of_experience}+ years of experience as a {self.user_profile.current_role}, I specialize in {', '.join(self.user_profile.skills[:4])}. In my recent work, I've focused on building high-performance systems and production architectures that drive tangible business value.

Given {company_name}'s exciting mission, I believe my background aligns closely with what your engineering team is building.

I have attached my resume and would welcome 10 minutes to discuss how I can contribute to {company_name}'s goals.

You can also view my portfolio and profiles here:
- LinkedIn: {self.user_profile.linkedin_url}
{f'- GitHub: {self.user_profile.github_url}' if self.user_profile.github_url else ''}

Best regards,
{self.user_profile.name}
{self.user_profile.phone}
{self.user_profile.email}
"""

		body_html = f"""<p>{greeting}</p>
<p>I noticed your open role for <strong>{job_title}</strong> at <strong>{company_name}</strong> and wanted to reach out directly.</p>
<p>With {self.user_profile.years_of_experience}+ years of experience as a {self.user_profile.current_role}, I specialize in <em>{', '.join(self.user_profile.skills[:4])}</em>. In my recent work, I've focused on building high-performance systems and production architectures that drive tangible business value.</p>
<p>Given {company_name}'s exciting mission, I believe my background aligns closely with what your engineering team is building.</p>
<p>I have attached my resume and would welcome 10 minutes to discuss how I can contribute to {company_name}'s goals.</p>
<p>You can also view my profiles here:<br/>
&bull; <a href="{self.user_profile.linkedin_url}">LinkedIn Profile</a><br/>
{f'&bull; <a href="{self.user_profile.github_url}">GitHub Profile</a><br/>' if self.user_profile.github_url else ''}
</p>
<p>Best regards,<br/>
<strong>{self.user_profile.name}</strong><br/>
{self.user_profile.phone} | <a href="mailto:{self.user_profile.email}">{self.user_profile.email}</a>
</p>
"""
		return EmailDraft(
			to_email='',
			to_name=hr_name,
			subject=subject,
			body_text=body_text,
			body_html=body_html,
		)

	def send_email(self, job_id: int, draft: EmailDraft) -> bool:
		"""Send the email or log simulation if in dry_run mode."""
		if not draft.to_email:
			logger.warning('Cannot send email: recipient email is missing.')
			return False

		if self.dry_run or not (self.smtp_user and self.smtp_password):
			logger.info(
				f'[DRY RUN / SIMULATION] Cold email prepared for {draft.to_email} (Job #{job_id}):\n'
				f'Subject: {draft.subject}\n'
				f'Body:\n{draft.body_text[:200]}...\n',
			)
			self.tracker.log_cold_email(
				job_id=job_id,
				to_email=draft.to_email,
				to_name=draft.to_name,
				subject=draft.subject,
				body=draft.body_text,
				status='simulated',
			)
			return True

		try:
			msg = MIMEMultipart('alternative')
			msg['Subject'] = draft.subject
			msg['From'] = self.sender_email
			msg['To'] = draft.to_email

			msg.attach(MIMEText(draft.body_text, 'plain'))
			if draft.body_html:
				msg.attach(MIMEText(draft.body_html, 'html'))

			with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
				server.starttls()
				server.login(self.smtp_user, self.smtp_password)
				server.send_message(msg)

			self.tracker.log_cold_email(
				job_id=job_id,
				to_email=draft.to_email,
				to_name=draft.to_name,
				subject=draft.subject,
				body=draft.body_text,
				status='sent',
			)
			logger.info(f'Successfully sent cold email to {draft.to_email} for job #{job_id}')
			return True
		except Exception as e:
			logger.error(f'Failed to send email to {draft.to_email}: {e}')
			self.tracker.log_cold_email(
				job_id=job_id,
				to_email=draft.to_email,
				to_name=draft.to_name,
				subject=draft.subject,
				body=draft.body_text,
				status=f'error: {str(e)[:100]}',
			)
			return False
