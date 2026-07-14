class MeliError(Exception):
    """Base error for the Mercado Libre gateway."""


class MeliReauthRequired(MeliError):
    """Refresh failed with invalid_grant — the seller must reconnect the account."""
