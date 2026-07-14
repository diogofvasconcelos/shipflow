"""Mercado Libre gateway — the ONLY module that talks to the ML API.

client.py owns auth injection, token refresh (single-flight), and the
401/429/5xx retry policy (docs/ARCHITECTURE.md §6). errors.py defines the
exceptions services are allowed to catch.
"""
