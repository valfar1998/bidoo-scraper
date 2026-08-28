"""Eccezioni specifiche del client Bidoo."""


class CloudflareBlockedError(RuntimeError):
    """Bidoo/Cloudflare ha mostrato una pagina di verifica (es. 'Ci siamo quasi…')."""
