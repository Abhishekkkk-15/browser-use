from __future__ import annotations

import csv
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from job_agent.config import JobRecord

logger = logging.getLogger(__name__)


class JobTracker:
	"""SQLite persistent storage and analytics tracker for job applications, HR contacts, and interviews."""

	def __init__(self, db_path: Path = Path('job_agent/data/jobs_tracker.db')):
		self.db_path = Path(db_path)
		self.db_path.parent.mkdir(parents=True, exist_ok=True)
		self._init_db()

	@contextmanager
	def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
		"""Context manager providing auto-committing, auto-closing SQLite connections."""
		conn = sqlite3.connect(str(self.db_path))
		conn.row_factory = sqlite3.Row
		try:
			yield conn
			conn.commit()
		except Exception:
			conn.rollback()
			raise
		finally:
			conn.close()

	def _init_db(self) -> None:
		"""Initialize tables and indexes."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute("""
				CREATE TABLE IF NOT EXISTS jobs (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					job_title TEXT NOT NULL,
					company_name TEXT NOT NULL,
					job_url TEXT UNIQUE NOT NULL,
					platform TEXT NOT NULL,
					location TEXT,
					salary_range TEXT,
					job_description_summary TEXT,
					required_skills TEXT,
					hr_name TEXT,
					hr_email TEXT,
					hr_linkedin TEXT,
					recruiter_name TEXT,
					application_type TEXT DEFAULT 'easy_apply',
					status TEXT DEFAULT 'found',
					match_score REAL DEFAULT 0.0,
					pitch_used TEXT,
					notes TEXT,
					discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
					applied_at TIMESTAMP,
					last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
				)
			""")

			cursor.execute("""
				CREATE TABLE IF NOT EXISTS cold_emails (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
					to_email TEXT NOT NULL,
					to_name TEXT,
					subject TEXT NOT NULL,
					body TEXT NOT NULL,
					sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
					status TEXT DEFAULT 'sent',
					reply_received INTEGER DEFAULT 0
				)
			""")

			cursor.execute("""
				CREATE TABLE IF NOT EXISTS interviews (
					id INTEGER PRIMARY KEY AUTOINCREMENT,
					job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
					company_name TEXT NOT NULL,
					role TEXT NOT NULL,
					scheduled_at TIMESTAMP,
					interview_type TEXT DEFAULT 'video',
					meeting_link TEXT,
					interviewer_notes TEXT,
					status TEXT DEFAULT 'scheduled',
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
				)
			""")

			# Helpful indexes for performance
			cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)')
			cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_platform ON jobs(platform)')
			cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(job_url)')

	def add_job(self, job_data: dict[str, Any] | JobRecord) -> int:
		"""Add or update a job record. Returns the job ID."""
		if isinstance(job_data, JobRecord):
			data = job_data.model_dump()
		else:
			data = dict(job_data)

		skills_str = json.dumps(data.get('required_skills', []))

		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				"""
				INSERT INTO jobs (
					job_title, company_name, job_url, platform, location,
					salary_range, job_description_summary, required_skills,
					hr_name, hr_email, hr_linkedin, recruiter_name,
					application_type, status, match_score, notes
				) VALUES (
					:job_title, :company_name, :job_url, :platform, :location,
					:salary_range, :job_description_summary, :required_skills,
					:hr_name, :hr_email, :hr_linkedin, :recruiter_name,
					:application_type, :status, :match_score, :notes
				)
				ON CONFLICT(job_url) DO UPDATE SET
					job_title = excluded.job_title,
					company_name = excluded.company_name,
					location = COALESCE(excluded.location, jobs.location),
					salary_range = COALESCE(excluded.salary_range, jobs.salary_range),
					job_description_summary = COALESCE(excluded.job_description_summary, jobs.job_description_summary),
					required_skills = excluded.required_skills,
					hr_name = COALESCE(excluded.hr_name, jobs.hr_name),
					hr_email = COALESCE(excluded.hr_email, jobs.hr_email),
					hr_linkedin = COALESCE(excluded.hr_linkedin, jobs.hr_linkedin),
					last_updated = CURRENT_TIMESTAMP
			""",
				{
					'job_title': data.get('job_title', 'Unknown Role'),
					'company_name': data.get('company_name', 'Unknown Company'),
					'job_url': data.get('job_url'),
					'platform': data.get('platform', 'other'),
					'location': data.get('location', 'Remote'),
					'salary_range': data.get('salary_range'),
					'job_description_summary': data.get('job_description_summary', ''),
					'required_skills': skills_str,
					'hr_name': data.get('hr_name'),
					'hr_email': data.get('hr_email'),
					'hr_linkedin': data.get('hr_linkedin'),
					'recruiter_name': data.get('recruiter_name'),
					'application_type': data.get('application_type', 'easy_apply'),
					'status': data.get('status', 'found'),
					'match_score': data.get('match_score', 0.0),
					'notes': data.get('notes'),
				},
			)
			return cursor.lastrowid or 0

	def is_already_applied(self, job_url: str) -> bool:
		"""Check if the user has already applied or is in progress for this URL."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('SELECT status FROM jobs WHERE job_url = ?', (job_url,))
			row = cursor.fetchone()
			if not row:
				return False
			return row['status'] in ('applied', 'interview', 'offer', 'rejected')

	def is_job_saved(self, job_url: str) -> bool:
		"""Check if a job is already in the database."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('SELECT id FROM jobs WHERE job_url = ?', (job_url,))
			return cursor.fetchone() is not None

	def update_status(
		self,
		job_url: str,
		status: str,
		notes: str | None = None,
		pitch_used: str | None = None,
	) -> None:
		"""Update job status and set applied_at timestamp when status becomes 'applied'."""
		applied_clause = ', applied_at = CURRENT_TIMESTAMP' if status == 'applied' else ''
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				f"""
				UPDATE jobs SET
					status = ?,
					notes = COALESCE(?, notes),
					pitch_used = COALESCE(?, pitch_used),
					last_updated = CURRENT_TIMESTAMP
					{applied_clause}
				WHERE job_url = ?
			""",
				(status, notes, pitch_used, job_url),
			)

	def update_hr_contact(
		self,
		job_url: str,
		hr_name: str | None = None,
		hr_email: str | None = None,
		hr_linkedin: str | None = None,
	) -> None:
		"""Update HR contact fields for a specific job."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				"""
				UPDATE jobs SET
					hr_name = COALESCE(?, hr_name),
					hr_email = COALESCE(?, hr_email),
					hr_linkedin = COALESCE(?, hr_linkedin),
					last_updated = CURRENT_TIMESTAMP
				WHERE job_url = ?
			""",
				(hr_name, hr_email, hr_linkedin, job_url),
			)

	def get_pending_jobs(self, platform: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
		"""Retrieve jobs that have been discovered but not yet applied to."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			query = "SELECT * FROM jobs WHERE status = 'found'"
			params: list[Any] = []
			if platform:
				query += ' AND platform = ?'
				params.append(platform)
			query += ' ORDER BY match_score DESC, discovered_at DESC LIMIT ?'
			params.append(limit)

			cursor.execute(query, params)
			rows = cursor.fetchall()
			return [dict(row) for row in rows]

	def get_all_jobs(self, platform: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
		"""Retrieve all cataloged jobs sorted by discovery date."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			query = 'SELECT * FROM jobs'
			params: list[Any] = []
			if platform:
				query += ' WHERE platform = ?'
				params.append(platform)
			query += ' ORDER BY discovered_at DESC LIMIT ?'
			params.append(limit)

			cursor.execute(query, params)
			rows = cursor.fetchall()
			return [dict(row) for row in rows]

	def get_jobs_with_hr_email(self, limit: int = 50) -> list[dict[str, Any]]:
		"""Retrieve jobs where an HR/recruiter email has been extracted and no email sent yet."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				"""
				SELECT j.* FROM jobs j
				LEFT JOIN cold_emails ce ON j.id = ce.job_id
				WHERE j.hr_email IS NOT NULL AND j.hr_email != '' AND ce.id IS NULL
				ORDER BY j.discovered_at DESC LIMIT ?
			""",
				(limit,),
			)
			return [dict(row) for row in cursor.fetchall()]

	def log_cold_email(
		self,
		job_id: int,
		to_email: str,
		subject: str,
		body: str,
		to_name: str | None = None,
		status: str = 'sent',
	) -> int:
		"""Log a sent cold email."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				"""
				INSERT INTO cold_emails (job_id, to_email, to_name, subject, body, status)
				VALUES (?, ?, ?, ?, ?, ?)
			""",
				(job_id, to_email, to_name, subject, body, status),
			)
			return cursor.lastrowid or 0

	def record_interview(
		self,
		job_id: int,
		company_name: str,
		role: str,
		scheduled_at: str | None = None,
		interview_type: str = 'video',
		meeting_link: str | None = None,
		notes: str | None = None,
	) -> int:
		"""Record an interview invitation."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				"""
				INSERT INTO interviews (job_id, company_name, role, scheduled_at, interview_type, meeting_link, interviewer_notes)
				VALUES (?, ?, ?, ?, ?, ?, ?)
			""",
				(job_id, company_name, role, scheduled_at, interview_type, meeting_link, notes),
			)
			cursor.execute("UPDATE jobs SET status = 'interview' WHERE id = ?", (job_id,))
			return cursor.lastrowid or 0

	def get_job_by_id(self, job_id: int) -> dict[str, Any] | None:
		"""Retrieve a single job record by ID."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('SELECT * FROM jobs WHERE id = ?', (job_id,))
			row = cursor.fetchone()
			return dict(row) if row else None

	def delete_job(self, job_id: int) -> bool:
		"""Delete a job record by ID."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
			return cursor.rowcount > 0

	def delete_jobs_below_fit_score(self, min_fit_score: float) -> int:
		"""Delete unapplied jobs with a match score strictly below min_fit_score."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute(
				"DELETE FROM jobs WHERE match_score < ? AND status NOT IN ('applied', 'interview', 'offer')",
				(min_fit_score,),
			)
			return cursor.rowcount

	def reset_database(self) -> None:
		"""Drop all tables and re-initialize a completely clean, fresh schema."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('DROP TABLE IF EXISTS cold_emails')
			cursor.execute('DROP TABLE IF EXISTS interviews')
			cursor.execute('DROP TABLE IF EXISTS jobs')
		self._init_db()

	def get_interviews(self, limit: int = 50) -> list[dict[str, Any]]:
		"""Retrieve recorded interviews."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('SELECT * FROM interviews ORDER BY created_at DESC LIMIT ?', (limit,))
			return [dict(row) for row in cursor.fetchall()]

	def get_cold_emails(self, limit: int = 50) -> list[dict[str, Any]]:
		"""Retrieve logged cold emails."""
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('SELECT * FROM cold_emails ORDER BY sent_at DESC LIMIT ?', (limit,))
			return [dict(row) for row in cursor.fetchall()]

	def get_stats(self) -> dict[str, Any]:
		"""Get high-level analytics on job hunt progress."""
		with self._get_connection() as conn:
			cursor = conn.cursor()

			cursor.execute('SELECT COUNT(*) FROM jobs')
			total_found = cursor.fetchone()[0]

			cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'applied'")
			total_applied = cursor.fetchone()[0]

			cursor.execute("SELECT COUNT(*) FROM jobs WHERE status = 'interview'")
			total_interviews = cursor.fetchone()[0]

			cursor.execute("SELECT COUNT(*) FROM jobs WHERE hr_email IS NOT NULL AND hr_email != ''")
			total_hr_emails = cursor.fetchone()[0]

			cursor.execute('SELECT COUNT(*) FROM cold_emails')
			total_emails_sent = cursor.fetchone()[0]

			cursor.execute("""
				SELECT platform, COUNT(*) as count,
					SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) as applied
				FROM jobs GROUP BY platform
			""")
			platform_stats = [dict(row) for row in cursor.fetchall()]

			return {
				'total_found': total_found,
				'total_applied': total_applied,
				'total_interviews': total_interviews,
				'total_hr_emails': total_hr_emails,
				'total_emails_sent': total_emails_sent,
				'platform_breakdown': platform_stats,
			}

	def export_to_csv(self, output_path: Path) -> Path:
		"""Export all jobs to a CSV file."""
		output_path = Path(output_path)
		output_path.parent.mkdir(parents=True, exist_ok=True)
		with self._get_connection() as conn:
			cursor = conn.cursor()
			cursor.execute('SELECT * FROM jobs ORDER BY discovered_at DESC')
			rows = cursor.fetchall()

			if not rows:
				output_path.write_text('No jobs found', encoding='utf-8')
				return output_path

			columns = [desc[0] for desc in cursor.description]
			with open(output_path, 'w', newline='', encoding='utf-8') as f:
				writer = csv.writer(f)
				writer.writerow(columns)
				for row in rows:
					writer.writerow(list(row))
		return output_path
