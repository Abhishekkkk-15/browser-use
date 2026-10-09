"""AI Job Application Agent package powered by browser-use."""

from job_agent.config import AgentConfig, JobPreferences, JobRecord, UserProfile
from job_agent.database import JobTracker
from job_agent.orchestrator import JobAgentOrchestrator

__version__ = '1.0.0'

__all__ = [
	'JobAgentOrchestrator',
	'JobTracker',
	'UserProfile',
	'JobPreferences',
	'JobRecord',
	'AgentConfig',
]
