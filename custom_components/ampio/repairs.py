"""Repair flows for the Ampio integration."""

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .data import AmpioConfigEntry
from .stale import async_remove_stale_records


class StaleRecordsRepairFlow(RepairsFlow):
    """Confirm, then delete the records the last setup left unclaimed."""

    def __init__(self, entry: AmpioConfigEntry) -> None:
        """Bind the flow to the one Ampio entry."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Go straight to the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show what goes, and delete it on submit.

        The records are read again at submit time, because the list in the
        issue is as old as the last setup. The reload that follows rebuilds
        a moved object's child under its new module.
        """
        if self._entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            async_remove_stale_records(self.hass, self._entry)
            self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=issue.translation_placeholders if issue else None,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for the stale-records issue.

    One config entry is allowed, so the issue names none. The flow aborts
    on its own when that entry is not loaded.
    """
    entry: AmpioConfigEntry = hass.config_entries.async_entries(DOMAIN)[0]
    return StaleRecordsRepairFlow(entry)
