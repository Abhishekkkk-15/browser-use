from __future__ import annotations

from job_agent.config import UserProfile
from job_agent.services.fit_scorer import JobFitResult, JobFitScorer


def test_fit_scorer_perfect_match():
	user = UserProfile(
		name='Alice Smith',
		current_role='Applied AI Engineer',
		years_of_experience=3,
		skills=['Python', 'FastAPI', 'RAG', 'AI Agents', 'Docker', 'PostgreSQL'],
		summary='Experienced AI Engineer building LLM applications and agent pipelines.',
	)

	job = {
		'job_title': 'Senior Applied AI Engineer',
		'job_description_summary': 'Looking for an AI Engineer with 3+ years experience to build autonomous agents and RAG pipelines using Python and FastAPI.',
		'required_skills': 'Python, FastAPI, RAG, AI Agents',
	}

	result: JobFitResult = JobFitScorer.score_fit(job, user)

	assert result.score >= 80.0
	assert len(result.matched_skills) >= 4
	assert 'Python' in result.matched_skills
	assert 'AI Agents' in result.matched_skills
	assert 'High title relevance' in result.reasoning


def test_fit_scorer_low_match():
	user = UserProfile(
		name='Bob Developer',
		current_role='iOS Developer',
		years_of_experience=1,
		skills=['Swift', 'Objective-C', 'iOS', 'Xcode'],
		summary='Mobile developer focused on native iOS applications.',
	)

	job = {
		'job_title': 'Lead DevOps / Cloud Infrastructure Engineer',
		'job_description_summary': 'Require 7+ years of Kubernetes, Terraform, AWS, and distributed cloud systems.',
		'required_skills': 'Kubernetes, Terraform, AWS, Helm',
	}

	result: JobFitResult = JobFitScorer.score_fit(job, user)

	assert result.score < 50.0
	assert len(result.matched_skills) == 0
	assert len(result.missing_skills) == 4


def test_fit_scorer_synonym_matching():
	user = UserProfile(
		name='Charlie',
		current_role='Full Stack Developer',
		skills=['React.js', 'Node.js', 'PostgreSQL', 'TypeScript'],
	)

	job = {
		'job_title': 'Full Stack Engineer',
		'job_description_summary': 'Build apps using React, Node, and Postgres.',
		'required_skills': 'React, Node, Postgres, TypeScript',
	}

	result = JobFitScorer.score_fit(job, user)

	assert result.score >= 70.0
	assert len(result.matched_skills) == 4


def test_fit_scorer_location_penalty():
	user = UserProfile(
		name='Alice Smith',
		current_role='Software Engineer',
		years_of_experience=3,
		skills=['Python', 'FastAPI'],
	)

	# Candidate wants remote or Bangalore
	target_locations = ['Remote', 'Bangalore']

	# Job is on-site in Berlin with no remote option
	job = {
		'job_title': 'Software Engineer',
		'job_description_summary': 'On-site in Berlin, Germany. Relocation required.',
		'location': 'Berlin, Germany',
		'required_skills': 'Python, FastAPI',
	}

	result = JobFitScorer.score_fit(job, user, target_locations=target_locations)
	assert 'On-site in non-target location' in result.reasoning
	# Should have a significant penalty compared to remote
	job_remote = dict(job)
	job_remote['location'] = 'Remote'
	result_remote = JobFitScorer.score_fit(job_remote, user, target_locations=target_locations)
	assert result.score < result_remote.score


def test_fit_scorer_zero_match_irrelevant_job():
	user = UserProfile(
		name='Alice Smith',
		current_role='Python Backend Engineer',
		years_of_experience=2,
		skills=['Python', 'Django', 'PostgreSQL'],
	)

	# Completely irrelevant job: Head of AI / Quantum Physics
	job = {
		'job_title': 'Chief Quantum Physics Scientist',
		'job_description_summary': 'Direct laser physics laboratory in Geneva.',
		'location': 'Geneva, Switzerland',
		'required_skills': 'Quantum Electrodynamics, Optics, Lasers',
	}

	result = JobFitScorer.score_fit(
		job,
		user,
		target_roles=['Backend Engineer', 'Python Engineer'],
		target_locations=['Remote'],
	)

	assert result.score == 0.0
	assert len(result.matched_skills) == 0
	assert 'Low title relevance' in result.reasoning
