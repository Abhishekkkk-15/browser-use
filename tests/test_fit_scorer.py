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
