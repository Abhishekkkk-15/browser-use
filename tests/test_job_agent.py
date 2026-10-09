from __future__ import annotations

from pathlib import Path

import pytest

from job_agent.config import JobPreferences, UserProfile
from job_agent.database import JobTracker
from job_agent.platforms import get_platform_adapter
from job_agent.prompts import (
	build_application_prompt,
	build_extractor_prompt,
	build_pitch_prompt,
)
from job_agent.tools.email_tools import EmailSender
from job_agent.tools.job_tools import (
	create_job_tools,
)


@pytest.fixture
def temp_db(tmp_path: Path):
	db_file = tmp_path / 'test_jobs.db'
	return JobTracker(db_file)


@pytest.fixture
def sample_user():
	return UserProfile(
		name='Jane Doe',
		email='jane@example.com',
		phone='+1234567890',
		location='Remote',
		linkedin_url='https://linkedin.com/in/janedoe',
		skills=['Python', 'FastAPI', 'React', 'Docker'],
		years_of_experience=4,
		current_role='Senior Software Engineer',
	)


@pytest.fixture
def sample_preferences():
	return JobPreferences(
		target_roles=['Backend Engineer', 'Software Engineer'],
		target_locations=['Remote', 'New York'],
		dry_run=True,
		blacklisted_companies=['Spam Corp'],
	)


def test_database_lifecycle(temp_db: JobTracker, tmp_path: Path):
	# Add a job
	job_data = {
		'job_title': 'Backend Engineer',
		'company_name': 'Acme Inc',
		'job_url': 'https://example.com/jobs/123',
		'platform': 'linkedin',
		'location': 'Remote',
		'salary_range': '$120k - $150k',
		'job_description_summary': 'Looking for FastAPI and PostgreSQL expert.',
		'required_skills': ['Python', 'FastAPI', 'PostgreSQL'],
		'status': 'found',
	}
	job_id = temp_db.add_job(job_data)
	assert job_id > 0
	assert temp_db.is_job_saved('https://example.com/jobs/123')
	assert not temp_db.is_already_applied('https://example.com/jobs/123')

	# Update status to applied
	temp_db.update_status(
		job_url='https://example.com/jobs/123',
		status='applied',
		notes='Application submitted with custom pitch',
		pitch_used='I have 4 years experience with Python.',
	)
	assert temp_db.is_already_applied('https://example.com/jobs/123')

	# Update HR contact
	temp_db.update_hr_contact(
		job_url='https://example.com/jobs/123',
		hr_name='Sarah Recruiter',
		hr_email='sarah@acme.com',
		hr_linkedin='https://linkedin.com/in/sarah-recruiter',
	)

	# Record interview
	int_id = temp_db.record_interview(
		job_id=job_id,
		company_name='Acme Inc',
		role='Backend Engineer',
		scheduled_at='2026-10-15 14:00',
		interview_type='video',
	)
	assert int_id > 0

	# Check stats
	stats = temp_db.get_stats()
	assert stats['total_found'] == 1
	assert stats['total_interviews'] == 1
	assert stats['total_hr_emails'] == 1

	# Export to CSV
	csv_out = tmp_path / 'export.csv'
	temp_db.export_to_csv(csv_out)
	assert csv_out.exists()
	assert 'Acme Inc' in csv_out.read_text(encoding='utf-8')

	# Add a low-fit unapplied job and verify delete_jobs_below_fit_score
	low_job = {
		'job_title': 'Dentist',
		'company_name': 'Dental Clinic',
		'job_url': 'https://example.com/jobs/dental',
		'platform': 'other',
		'match_score': 12.0,
		'status': 'found',
	}
	temp_db.add_job(low_job)
	assert temp_db.is_job_saved('https://example.com/jobs/dental')
	deleted_count = temp_db.delete_jobs_below_fit_score(40.0)
	assert deleted_count == 1
	assert not temp_db.is_job_saved('https://example.com/jobs/dental')
	# Applied job must remain safe
	assert temp_db.is_job_saved('https://example.com/jobs/123')

	# Test reset_database
	temp_db.reset_database()
	stats_after_reset = temp_db.get_stats()
	assert stats_after_reset['total_found'] == 0
	assert stats_after_reset['total_applied'] == 0
	assert not temp_db.is_job_saved('https://example.com/jobs/123')


@pytest.mark.asyncio
async def test_job_tools_actions(
	temp_db: JobTracker,
	sample_user: UserProfile,
	sample_preferences: JobPreferences,
):
	tools = create_job_tools(temp_db, sample_user, sample_preferences)
	save_job_action = tools.registry.registry.actions['save_job']
	mark_applied_action = tools.registry.registry.actions['mark_applied']
	profile_action = tools.registry.registry.actions['get_user_profile']
	check_action = tools.registry.registry.actions['check_already_applied']

	# 1. Test get user profile
	res = await tools.registry.execute_action('get_user_profile', {'field': 'name'})
	assert res.extracted_content == 'Jane Doe'

	res = await tools.registry.execute_action('get_user_profile', {'field': 'skills'})
	assert 'Python' in str(res.extracted_content)

	# 2. Test save job
	res = await tools.registry.execute_action(
		'save_job',
		{
			'job_title': 'Full Stack Developer',
			'company_name': 'Innovate Ltd',
			'job_url': 'https://example.com/jobs/456',
			'platform': 'wellfound',
			'location': 'Remote',
			'required_skills': 'Python, React',
		},
	)
	assert 'Saved job' in str(res.extracted_content)
	assert temp_db.is_job_saved('https://example.com/jobs/456')

	# 3. Test blacklist skip
	res = await tools.registry.execute_action(
		'save_job',
		{
			'job_title': 'Developer',
			'company_name': 'Spam Corp',
			'job_url': 'https://example.com/jobs/999',
			'platform': 'linkedin',
		},
	)
	assert 'Skipped Spam Corp' in str(res.extracted_content)

	# 4. Test check already applied
	check_res = await tools.registry.execute_action(
		'check_already_applied',
		{'job_url': 'https://example.com/jobs/456'},
	)
	assert 'ALREADY_SAVED' in str(check_res.extracted_content)

	# 5. Test mark applied (in dry run mode)
	mark_res = await tools.registry.execute_action(
		'mark_applied',
		{
			'job_url': 'https://example.com/jobs/456',
			'pitch_used': 'Custom test pitch',
			'notes': 'Form preview verified',
		},
	)
	assert '[DRY RUN]' in str(mark_res.extracted_content)

	# 6. Test strict rejection of non-relevant job
	sample_preferences.min_fit_score = 45.0
	reject_res = await tools.registry.execute_action(
		'save_job',
		{
			'job_title': 'Chief Radiologist Physician',
			'company_name': 'Hospital Center',
			'job_url': 'https://example.com/jobs/radiology-777',
			'platform': 'wellfound',
			'location': 'Geneva, Switzerland',
			'required_skills': 'Oncology, MRI, Radiology',
		},
	)
	assert 'REJECTED AS NOT RELEVANT' in str(reject_res.extracted_content)
	assert not temp_db.is_job_saved('https://example.com/jobs/radiology-777')


def test_email_sender_simulation(temp_db: JobTracker, sample_user: UserProfile):
	sender = EmailSender(temp_db, sample_user, dry_run=True)
	draft = sender.draft_cold_email(
		job_title='Lead AI Engineer',
		company_name='FutureTech',
		hr_name='David',
	)
	assert 'David' in draft.body_text
	assert 'Lead AI Engineer' in draft.subject
	assert sample_user.name in draft.body_text

	draft.to_email = 'david@futuretech.com'
	sent = sender.send_email(job_id=1, draft=draft)
	assert sent is True


def test_prompts_and_adapters(sample_user: UserProfile, sample_preferences: JobPreferences):
	li_adapter = get_platform_adapter('linkedin')
	wf_adapter = get_platform_adapter('wellfound')
	nk_adapter = get_platform_adapter('naukri')

	li_task = li_adapter.get_search_task(sample_user, sample_preferences)
	assert 'https://www.linkedin.com/jobs/' in li_task
	assert 'Jane Doe' not in li_task  # Search task shouldn't expose private info unnecessarily

	wf_task = wf_adapter.get_search_task(sample_user, sample_preferences)
	assert 'https://wellfound.com/jobs' in wf_task

	nk_task = nk_adapter.get_search_task(sample_user, sample_preferences)
	assert 'https://www.naukri.com/' in nk_task

	# Application prompt
	job = {
		'job_title': 'Backend Developer',
		'company_name': 'Globex',
		'job_url': 'https://linkedin.com/jobs/view/1001',
		'platform': 'linkedin',
	}
	app_prompt = build_application_prompt(job, sample_user, custom_pitch='Test pitch', dry_run=True)
	assert 'DRY RUN MODE ENABLED' in app_prompt
	assert 'Jane Doe' in app_prompt
	assert 'Test pitch' in app_prompt

	# Pitch prompt
	pitch_prompt = build_pitch_prompt(job, sample_user)
	assert 'Jane Doe' in pitch_prompt
	assert 'Globex' in pitch_prompt

	# Extractor prompt
	ext_prompt = build_extractor_prompt(job)
	assert 'Globex' in ext_prompt
	assert 'save_hr_contact' in ext_prompt
