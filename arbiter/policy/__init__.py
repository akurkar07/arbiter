"""Deterministic policy engine — Arbiter's governance core."""

from .rules import evaluate, register_rule, RuleFn  # noqa: F401

__all__ = ["evaluate", "register_rule", "RuleFn"]
