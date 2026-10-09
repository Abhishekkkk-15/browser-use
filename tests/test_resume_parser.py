from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from job_agent.config import JobPreferences, UserProfile
from job_agent.services.resume_parser import (
	ResumeParser,
	calculate_experience_years,
	clean_resume_text,
)


def test_clean_resume_text():
	raw = 'Software\u00a0Developer\ufb01with\u2013experience'
	cleaned = clean_resume_text(raw)
	assert ' ' in cleaned
	assert 'fi' in cleaned
	assert '-' in cleaned


def test_calculate_experience_junior():
	# Starting in Feb 2026 and evaluating in Oct 2026 should calculate ~0.7 years (<1 year)
	text = """
	## PROFESSIONAL EXPERIENCE
	### Full Stack Developer — Tech Corp (Feb 2026 – Present)
	- Built web applications
	"""
	ref_date = datetime(2026, 10, 1)
	exp = calculate_experience_years(text, reference_date=ref_date)
	assert exp < 1.0
	assert 0.5 <= exp <= 0.8


def test_calculate_experience_multi_interval():
	text = """
	### Software Engineer — Alpha Inc (Jan 2023 - Dec 2024)
	### Backend Developer — Beta LLC (Jan 2025 - Dec 2025)
	"""
	ref_date = datetime(2026, 10, 1)
	exp = calculate_experience_years(text, reference_date=ref_date)
	assert 2.5 <= exp <= 3.5


def test_calculate_experience_explicit_mention():
	text = 'Motivated engineer with 2.5 years of experience in distributed systems.'
	exp = calculate_experience_years(text)
	assert exp == 2.5


def test_parse_rule_based_resume():
	sample_resume = """# Jane Doe
Applied AI Engineer | Python Specialist
jane.doe@example.com | +1 555-019-2834 | San Francisco, CA
LinkedIn: https://www.linkedin.com/in/jane-doe
GitHub: https://github.com/jane-doe
Portfolio: https://janedoe.dev

## PROFESSIONAL SUMMARY
AI engineer specializing in autonomous LLM agents and FastAPI backend development.

## PROFESSIONAL EXPERIENCE
### AI Engineer — OpenAI Partner Labs (Feb 2026 – Present)
- Developed agent workflows and tool calling systems.

## TECHNICAL SKILLS
- Languages: Python, TypeScript
- Frameworks: FastAPI, React, Next.js
- Tools: Docker, PostgreSQL, Redis

## EDUCATION
- Bachelor of Science in Computer Science, 2025
  University of California, Berkeley
"""
	parsed = ResumeParser.parse_rule_based(sample_resume)

	assert parsed['name'] == 'Jane Doe'
	assert parsed['email'] == 'jane.doe@example.com'
	assert parsed['phone'] == '+1 555-019-2834'
	assert 'San Francisco' in parsed['location']
	assert parsed['linkedin_url'] == 'https://www.linkedin.com/in/jane-doe'
	assert parsed['github_url'] == 'https://github.com/jane-doe'
	assert parsed['current_role'] == 'AI Engineer'
	assert parsed['current_company'] == 'OpenAI Partner Labs'
	assert parsed['years_of_experience'] <= 1.0
	assert 'Python' in parsed['skills']
	assert 'FastAPI' in parsed['skills']


def test_user_profile_json_roundtrip():
	with tempfile.TemporaryDirectory() as tmpdir:
		tmp_json = Path(tmpdir) / 'user_profile.json'

		user = UserProfile(
			name='Abhishek Jangid',
			current_role='Full Stack Developer',
			current_company='Adiyogi Technosoft Pvt. Ltd',
			years_of_experience=0.7,
			email='abhishekjangid3489@gmail.com',
			phone='+919799219379',
			location='Jodhpur, Rajasthan, India',
			linkedin_url='https://www.linkedin.com/in/abhishek-jangid-3532b1323',
			github_url='https://github.com/abhishekkkk-15',
			skills=['Python', 'TypeScript', 'React', 'FastAPI'],
			summary='Applied AI Engineer & Full Stack Developer.',
			education='Master of Computer Applications (MCA)',
		)

		saved_path = user.save_to_file(tmp_json)
		assert saved_path.exists()

		loaded = UserProfile.from_file(saved_path)
		assert loaded.name == user.name
		assert loaded.current_role == user.current_role
		assert loaded.years_of_experience == 0.7
		assert loaded.skills == user.skills
		assert loaded.email == user.email


def test_job_preferences_json_roundtrip():
	with tempfile.TemporaryDirectory() as tmpdir:
		tmp_json = Path(tmpdir) / 'job_preferences.json'

		pref = JobPreferences(
			target_roles=['Full Stack Developer', 'AI Engineer'],
			target_locations=['Remote', 'India'],
			platforms=['wellfound', 'linkedin'],
			mode='free',
			max_applications_per_run=12,
			min_fit_score=50.0,
		)

		saved_path = pref.save_to_file(tmp_json)
		assert saved_path.exists()

		loaded = JobPreferences.from_file(saved_path)
		assert loaded.target_roles == ['Full Stack Developer', 'AI Engineer']
		assert loaded.mode == 'free'
		assert loaded.max_applications_per_run == 12
		assert loaded.min_fit_score == 50.0
