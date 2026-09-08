# Ampio for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/pszypowicz/ampio-homeassistant)](https://github.com/pszypowicz/ampio-homeassistant/releases)
[![CI](https://github.com/pszypowicz/ampio-homeassistant/actions/workflows/ci.yaml/badge.svg)](https://github.com/pszypowicz/ampio-homeassistant/actions/workflows/ci.yaml)
[![License](https://img.shields.io/github/license/pszypowicz/ampio-homeassistant)](LICENSE)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![Maintainer](https://img.shields.io/badge/maintainer-%40pszypowicz-blue.svg)](https://github.com/pszypowicz)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system. It talks to the local M-SERV controller over MQTT through the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Platforms

| Platform        | What you get                                                                                                                                                                         |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sensor`        | Temperature, humidity, pressure, CO2, air quality, illuminance, loudness, and every integer sensor slot, with the Designer unit where one is set (Modbus meters behind an M-CON-485) |
| `binary_sensor` | Wired button inputs                                                                                                                                                                  |
| `light`         | Dimmers, RGBW outputs, and relays tagged as lights in Ampio Designer                                                                                                                 |
| `cover`         | Shutters and blinds, with position and slat tilt where the hardware has them                                                                                                         |
| `switch`        | Remaining relays and Ampio flags, with the outlet class for plug-tagged ones                                                                                                         |
| `button`        | Relays and flags marked as bell objects in Ampio Designer (a single press)                                                                                                           |
| `climate`       | Heating regulators with temperature readback and operating-mode presets                                                                                                              |
| `scene`         | The Ampio app's scene catalog                                                                                                                                                        |

## Installation

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pszypowicz&repository=ampio-homeassistant&category=integration)

Click the badge, confirm the repository in HACS, install "Ampio", and restart Home Assistant.

Manual steps:

1. Open HACS in Home Assistant.
2. Add `https://github.com/pszypowicz/ampio-homeassistant` as a custom repository (type: Integration).
3. Install "Ampio" and restart Home Assistant.

Requires Home Assistant 2026.9.0 or newer. `ampio-mqtt` is installed automatically.

## Configuration

1. In the Ampio app, create a dedicated Home Assistant user and grant it the devices you want in Home Assistant.
2. In Home Assistant, go to Settings -> Devices & Services -> Add Integration -> Ampio.
3. Enter the M-SERV host and that user's MQTT credentials.

Devices appear as a hub for the M-SERV, one device per Ampio module, and one device per Ampio object under its module. An object device takes its name and its area from the Ampio app when Home Assistant creates it, and the integration never moves it afterwards. Every entity carries the id `<domain>.ampio_obj_<object id>`, and that id never changes. See [docs/devices.md](docs/devices.md) for the names, the areas, and what a change in Ampio Designer does.

One M-SERV per Home Assistant. Object ids are unique per server only, so the integration allows one entry.

## Updating

Every `0.0.x` release is beta. None of them carries a migration, so an update can change device names or the entity set with no upgrade path. Take a backup before you update, and read the release note. It leads with the breaking changes and the upgrade steps.

Your entity ids survive an update unless the release note says otherwise. If an update leaves you with missing entities or entities that stay unavailable, remove the integration and add it again. That is the supported first step, not a last resort. Home Assistant remembers a removed entity for 30 days, so a re-add restores your entity ids, your renames, and your areas.

If something else looks wrong, see [docs/faq.md](docs/faq.md). Each answer there tells you how to check whether it affects you, and how to fix it.

## Documentation

- [docs/faq.md](docs/faq.md): what to check and what to do when something looks wrong, from missing entities to entity ids.
- [docs/devices.md](docs/devices.md): the device tree, names, areas, entity ids, and what a change in Ampio Designer does.
- [docs/designer-quirks.md](docs/designer-quirks.md): a relay tagged as a light that shows as a switch, the Matter checkbox, an object moved to another module, and integer sensor slots.
- [docs/debugging.md](docs/debugging.md): the diagnostics download and debug logging, for a bug report.
- [docs/development.md](docs/development.md): the gate venv, the pre-commit hooks, and how to run the CI checks locally.

## Known limitations

- Scenes are read once at setup. A scene added in the app needs a reload.
- The Entity ID format setting under Settings, then System, does not apply. Every Ampio entity carries its own id, `<domain>.ampio_obj_<object id>`, so the setting cannot add the area or the floor to it.

## Relationship to home-assistant/core

An earlier form of this integration is submitted to `home-assistant/core` as [PR #179548](https://github.com/home-assistant/core/pull/179548). This repository leads that submission, and reports from real installs are what will make the upstream version worth merging.

## Disclaimer

This integration is an independent, best-effort project and has no affiliation with Ampio. Use it at your own risk. It commands real hardware, and a wrong command moves real devices.

The M-SERV itself guarantees the safety of a standard account. The broker limits such an account to the objects granted in the Ampio app, and it denies the raw CAN surfaces on the wire. A defect in this integration or the underlying [`ampio-mqtt`](https://github.com/pszypowicz/ampio-mqtt) library cannot widen that boundary.

Ampio does not guarantee the stability of the wire surfaces this integration depends on. A server update or a module firmware update can change or remove behavior without notice, and breaking changes by Ampio are a known pattern. The author of an earlier Ampio integration [stopped maintenance for exactly this reason](https://github.com/kstaniek/ampio-hacc/issues/2). If your install works and you are happy with it, stay on your current versions and do not chase the latest ones. If you decide to update anyway, make a full backup first - ideally a full image of the M-SERV's microSD card.

Report bugs and ideas in the [issues](https://github.com/pszypowicz/ampio-homeassistant/issues). The [debugging guide](docs/debugging.md) shows how to capture diagnostics and debug logs for a report.
