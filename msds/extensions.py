"""Shared extension singletons.

The rate limiter lives here so it can be swapped for a shared backend
(e.g. Redis) in production without touching the routes.
"""

from .rate_limit import RateLimiter

rate_limiter = RateLimiter()
