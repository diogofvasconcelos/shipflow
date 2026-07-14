"""ShipFlow — Order Fulfillment System for Mercado Libre sellers.

Layering (strict, top → bottom; see CLAUDE.md):

    api → services → repositories → models
                   ↘ integrations/meli (external I/O)
    workers → services (thin job wrappers, no business logic)

Start reading at app/main.py (app factory) and docs/ARCHITECTURE.md.
"""
