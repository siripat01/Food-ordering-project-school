"""Taskiq task handlers.

Handlers in this package are commands ("do this"), never facts ("this
happened"). Facts live in the outbox (:mod:`app.domain.outbox`). Every handler
stays thin: it resolves services from the worker container and delegates.
"""
