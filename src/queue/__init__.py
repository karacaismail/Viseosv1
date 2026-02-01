"""
VISE OS Queue Module

Celery-based task queue for asynchronous booking processing.
Manages task scheduling, priority queues, and retry logic.

Components:
- celery_app: Celery application configuration
- tasks: Task definitions (booking, sync, maintenance)
- schedules: Celery Beat scheduled tasks
"""
