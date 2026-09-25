"""Per-account dashboard settings (linked Linear/GitHub ticket IDs, duplicate flagging).

Keyed by account id — a Pylon account UUID or an account group id — and stored in
the shared LangGraph Platform Store like account_groups, so a setting turned on for
an account stays on for everyone and survives restarts/replicas.
"""

from __future__ import annotations

import lg_store

_NS = ("psh_account_settings",)
DEFAULTS = {
    "show_linked_ids": False,  # show linked Linear/GitHub IDs on tickets
    "flag_duplicates": True,   # group possible duplicates in the ticket list (runs the AI pass)
}



def get_settings(account_id: str) -> dict:
    stored = lg_store.get(_NS, account_id) or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def update_settings(account_id: str, changes: dict) -> dict:
    with lg_store.lock(_NS, account_id):  # concurrent toggles mustn't undo each other
        settings = {**get_settings(account_id), **{k: v for k, v in changes.items() if k in DEFAULTS}}
        lg_store.put(_NS, account_id, settings)
    return settings
