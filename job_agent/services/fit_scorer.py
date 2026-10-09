from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from job_agent.config import UserProfile


class JobFitResult(BaseModel):
	"""Detailed match scoring breakdown between candidate profile and a job posting."""

	score: float = Field(ge=0.0, le=100.0, description='Overall fit score percentage (0-100)')
	matched_skills: list[str] = Field(default_factory=list, description='Skills required that the candidate possesses')
	missing_skills: list[str] = Field(default_factory=list, description='Skills required that the candidate lacks')
	title_score: float = Field(ge=0.0, le=100.0, description='Job title relevance score (0-100)')
	experience_score: float = Field(ge=0.0, le=100.0, description='Experience level alignment score (0-100)')
	reasoning: str = Field(description='Human-readable explanation of why this job is or is not a match')


class JobFitScorer:
	"""Evaluates job postings against candidate profile to calculate best-fit priority."""

	SYNONYMS: dict[str, set[str]] = {
		'react': {'react', 'react.js', 'reactjs'},
		'next': {'next', 'next.js', 'nextjs'},
		'node': {'node', 'node.js', 'nodejs'},
		'postgres': {'postgres', 'postgresql', 'psql', 'pgvector'},
		'python': {'python', 'python3', 'py'},
		'fastapi': {'fastapi', 'fast-api'},
		'express': {'express', 'express.js', 'expressjs'},
		'ai': {'ai', 'artificial intelligence', 'genai', 'generative ai', 'llm', 'llms', 'agents'},
		'rag': {'rag', 'retrieval-augmented generation', 'vector database', 'embeddings'},
		'docker': {'docker', 'containerization', 'containers'},
		'k8s': {'k8s', 'kubernetes'},
		'mongo': {'mongo', 'mongodb'},
		'ts': {'ts', 'typescript'},
		'js': {'js', 'javascript'},
	}

	@classmethod
	def normalize_skill(cls, skill: str) -> str:
		s = skill.strip().lower()
		for canonical, synonyms in cls.SYNONYMS.items():
			if s in synonyms:
				return canonical
		return s

	@classmethod
	def score_fit(
		cls,
		job: dict[str, Any],
		user: UserProfile,
	) -> JobFitResult:
		"""Compute a multi-dimensional best-fit score between a job posting and candidate."""
		# Extract job fields
		title = str(job.get('job_title', '')).lower()
		description = str(job.get('job_description_summary', '')).lower()
		raw_skills = job.get('required_skills', [])
		if isinstance(raw_skills, str):
			skills_list = [s.strip() for s in raw_skills.split(',') if s.strip()]
		else:
			skills_list = list(raw_skills)

		# 1. Skill Matching
		user_norm_skills = {cls.normalize_skill(s) for s in user.skills}
		# Also extract skills mentioned in user summary
		user_text_lower = f'{user.current_role} {user.summary} {" ".join(user.skills)}'.lower()

		matched: list[str] = []
		missing: list[str] = []

		if skills_list:
			for sk in skills_list:
				norm = cls.normalize_skill(sk)
				if norm in user_norm_skills or sk.lower() in user_text_lower:
					matched.append(sk)
				else:
					missing.append(sk)
			skill_ratio = len(matched) / max(len(skills_list), 1)
			skill_score = skill_ratio * 100.0
		else:
			# Infer skills from description if required_skills was empty
			found_in_desc = [s for s in user.skills if s.lower() in description]
			if found_in_desc:
				matched = found_in_desc
				skill_score = min(len(found_in_desc) * 15.0, 90.0)
			else:
				skill_score = 50.0  # Neutral baseline

		# 2. Title & Role Matching
		title_score = 30.0  # baseline
		candidate_roles = [user.current_role.lower()]
		for role in candidate_roles:
			role_words = [w for w in re.split(r'[\s/,-]+', role) if len(w) > 2]
			matches = sum(1 for w in role_words if w in title)
			if matches:
				title_score = min(40.0 + (matches / len(role_words)) * 60.0, 100.0)

		# Bonus for key terms
		for keyword in ('ai', 'engineer', 'developer', 'software', 'full stack', 'backend'):
			if keyword in title and keyword in user_text_lower:
				title_score = min(title_score + 10.0, 100.0)

		# 3. Experience Alignment
		exp_score = 80.0
		user_exp = user.years_of_experience
		# Check if description mentions years requirement e.g. "5+ years", "3-5 years"
		exp_match = re.search(r'(\d+)\+?\s*(?:to\s*(\d+))?\s*(?:years|yrs)', description)
		if exp_match:
			min_req = int(exp_match.group(1))
			if user_exp >= min_req:
				exp_score = 100.0
			elif user_exp == min_req - 1:
				exp_score = 75.0
			else:
				exp_score = max(30.0, 100.0 - (min_req - user_exp) * 25.0)

		# Weighted Overall Score: 50% Skills, 35% Title, 15% Experience
		final_score = round(
			(skill_score * 0.50) + (title_score * 0.35) + (exp_score * 0.15),
			1,
		)
		final_score = max(0.0, min(100.0, final_score))

		# Generate human-readable reasoning
		parts: list[str] = [f'{final_score:.0f}% Fit']
		if matched:
			parts.append(f"Matched {len(matched)} skills ({', '.join(matched[:4])})")
		if title_score >= 80:
			parts.append('High title relevance')
		elif title_score >= 50:
			parts.append('Moderate title relevance')
		if missing:
			parts.append(f"Missing: {', '.join(missing[:3])}")

		reasoning = '; '.join(parts)

		return JobFitResult(
			score=final_score,
			matched_skills=matched,
			missing_skills=missing,
			title_score=round(title_score, 1),
			experience_score=round(exp_score, 1),
			reasoning=reasoning,
		)
