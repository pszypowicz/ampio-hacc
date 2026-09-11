"""Registry records a setup left unclaimed, and the repair that removes them."""

from collections.abc import Iterable
from dataclasses import dataclass

from ampio_mqtt import AccessTier

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
    issue_registry as ir,
)

from .const import ADMIN_ONLY_RECORDS_ISSUE, DOMAIN, STALE_RECORDS_ISSUE
from .data import AmpioConfigEntry


@dataclass(frozen=True)
class StaleRecords:
    """What the registries hold for the entry beyond what the setup built."""

    # Children before full devices: removing a module takes its children
    # with it, and a child removed twice raises.
    devices: list[dr.AnyDeviceEntry]
    # Entities on no stale device, such as a scene that left the catalogue.
    entities: list[er.RegistryEntry]
    # Entity records the administrator rule withholds from this account.
    # Separate, because the integration knows exactly why these went and
    # can only guess about the rest.
    withheld: list[er.RegistryEntry]


def _record_names(
    devices: Iterable[dr.AnyDeviceEntry], entities: Iterable[er.RegistryEntry]
) -> list[str]:
    """What the issue text lists for each record, sorted.

    A device carries a name. An entity carries its pinned entity id
    instead: that is the string an automation names, it never collides
    with another entity of the same kind, and it cannot go stale inside a
    stored issue the way a renamed device can.
    """
    names = [
        device.name_by_user or device.name or next(iter(device.identifiers))[1]
        for device in devices
    ]
    names.extend(entity.entity_id for entity in entities)
    return sorted(names, key=str.casefold)


@callback
def find_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> StaleRecords:
    """Collect the entry's records that no platform claimed on this setup.

    An entity record is stale when it is enabled and no loaded platform
    holds an entity under its id. A child device is stale when every
    entity on it is stale, a device without entities included. A module
    device is stale when no eligible object resolves to it, and its
    entities are covered by the device rather than listed on their own. A
    disabled entity is skipped by its platform on purpose, so it and its
    device are never stale.
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
        child_entities = er.async_entries_for_device(
            entity_registry, child.id, include_disabled_entities=True
        )
        if all(entity.entity_id in stale_entities for entity in child_entities):
            devices.append(child)
            covered.update(entity.entity_id for entity in child_entities)
    live, _ = entry.runtime_data.live_identifiers()
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.identifiers & live:
            continue
        devices.append(device)
        covered.update(
            entity.entity_id
            for entity in er.async_entries_for_device(
                entity_registry, device.id, include_disabled_entities=True
            )
        )
    withheld_ids = entry.runtime_data.withheld_unique_ids()
    entities: list[er.RegistryEntry] = []
    withheld: list[er.RegistryEntry] = []
    for entity_id, entity in stale_entities.items():
        if entity_id in covered:
            continue
        target = withheld if entity.unique_id in withheld_ids else entities
        target.append(entity)
    return StaleRecords(devices, entities, withheld)


@callback
def async_report_stale_records(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """Raise the repair issues for what the setup left unclaimed, or clear them.

    Two issues, because the two causes are not equally well understood.
    The administrator rule withholds a known, closed set, and that text
    names the account tier. What is left may be a Designer delete or a lost
    app permission, and its wording follows the tier as before.
    """
    stale = find_stale_records(hass, entry)
    _async_report(
        hass,
        ADMIN_ONLY_RECORDS_ISSUE,
        "admin_only_records",
        _record_names([], stale.withheld),
    )
    if entry.runtime_data.client.access_tier is AccessTier.ADMIN:
        translation_key = "stale_records_deleted"
    else:
        translation_key = "stale_records_not_served"
    _async_report(
        hass,
        STALE_RECORDS_ISSUE,
        translation_key,
        _record_names(stale.devices, stale.entities),
    )


@callback
def _async_report(
    hass: HomeAssistant, issue_id: str, translation_key: str, names: list[str]
) -> None:
    """Raise one repair issue for a list of records, or clear it when empty."""
    if not names:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders={
            "count": str(len(names)),
            "names": "\n".join(f"- {name}" for name in names),
        },
    )


@callback
def async_remove_stale_records(
    hass: HomeAssistant, entry: AmpioConfigEntry, issue_id: str
) -> None:
    """Delete the records the issue covers, as they stand now.

    Read again at submit time, because the list in the issue is as old as
    the last setup.
    """
    stale = find_stale_records(hass, entry)
    entity_registry = er.async_get(hass)
    if issue_id == ADMIN_ONLY_RECORDS_ISSUE:
        for entity in stale.withheld:
            entity_registry.async_remove(entity.entity_id)
        return
    device_registry = dr.async_get(hass)
    for device in stale.devices:
        # A module's removal already took its children.
        if device_registry.async_get(device.id) is not None:
            device_registry.async_remove_device(device.id)
    for entity in stale.entities:
        entity_registry.async_remove(entity.entity_id)
