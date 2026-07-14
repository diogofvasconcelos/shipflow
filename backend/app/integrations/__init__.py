"""External service gateways. Each provider gets one subpackage owning ALL
I/O with that provider — no other module may import httpx or know its URLs.
Currently: meli/ (Mercado Libre API).
"""
