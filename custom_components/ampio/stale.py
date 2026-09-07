"""Registry records a setup left unclaimed, and the repair that removes them."""

from dataclasses import dataclass

from ampio_mqtt import AccessTier

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
    issue_registry as ir,
)

from .const import DOMAIN, STALE_RECORDS_ISSUE
from .data import AmpioConfigEntry


@dataclass(frozen=True)
class StaleRecords:
    """What the registries hold for the entry beyond what the setup built."""

    # Children before full devices: removing a module takes its children
    # with it, and a child removed twice raises.
    devices: list[dr.AnyDeviceEntry]
    # Entities on no stale device, such as a scene that left the catalogue.
    entities: list[er.RegistryEntry]

    def __bool__(self) -> bool:
        """True while anything is left to delete."""
        return bool(self.devices or self.entities)

    @property
    def names(self) -> list[str]:
        """What the user sees for each record, sorted for the issue text."""
        names = [
            device.name_by_user or device.name or next(iter(device.identifiers))[1]
            for device in self.devices
        ]
        names.extend(
            entity.name or entity.original_name or entity.entity_id
            for entity in self.entities
        )
        return sorted(names, key=str.casefold)


@callback
def find_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> StaleRecords:
    """Collect the entry's records that no platform claimed on this setup.

    An entity record is stale when it is enabled and no loaded platform
    holds an entity under its id. A child device is stale when every
    entity on it is stale, a device without entities included. A module
    device is stale when no eligible object resolves to it. A disabled
    entity is skipped by its platform on purpose, so it and its device are
    never stale.
    """
    claimed = {
        entity_id
        for platform in entity_platform.async_get_platforms(hass, DOMAIN)
        for entity_id in platform.entities
    }
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    stale_entities = {
        entity.entity_id: entity
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if entity.disabled_by is None and entity.entity_id not in claimed
    }

    devices: list[dr.AnyDeviceEntry] = []
    covered: set[str] = set()
    for child in dr.async_child_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        entities = er.async_entries_for_device(
            entity_registry, child.id, include_disabled_entities=True
        )
        if all(entity.entity_id in stale_entities for entity in entities):
            devices.append(child)
            covered.update(entity.entity_id for entity in entities)
    live, _ = entry.runtime_data.live_identifiers()
    devices.extend(
        device
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if not device.identifiers & live
    )
    entities = [
        entity
        for entity_id, entity in stale_entities.items()
        if entity_id not in covered
    ]
    return StaleRecords(devices, entities)


@callback
def async_report_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """Raise the one repair issue for the stale records, or clear it.

    The wording follows the account tier. An administrator sees every row,
    so an unclaimed record means a delete or a move in Designer. A
    restricted account is served a subset, so the same record may mean a
    lost app permission instead, and the text says so.
    """
    stale = find_stale_records(hass, entry)
    if not stale:
        ir.async_delete_issue(hass, DOMAIN, STALE_RECORDS_ISSUE)
        return
    names = stale.names
    if entry.runtime_data.client.access_tier is AccessTier.ADMIN:
        translation_key = "stale_records_deleted"
    else:
        translation_key = "stale_records_not_served"
    ir.async_create_issue(
        hass,
        DOMAIN,
        STALE_RECORDS_ISSUE,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders={
            "count": str(len(names)),
            "names": "\n".join(f"- {name}" for name in names),
        },
    )


@callback
def async_remove_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """Delete the stale records as they stand now, not as the issue listed them."""
    stale = find_stale_records(hass, entry)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for device in stale.devices:
        # A module's removal already took its children.
        if device_registry.async_get(device.id) is not None:
            device_registry.async_remove_device(device.id)
    for entity in stale.entities:
        entity_registry.async_remove(entity.entity_id)
