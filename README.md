# Ampio for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/pszypowicz/ampio-homeassistant)](https://github.com/pszypowicz/ampio-homeassistant/releases)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz/)
[![License](https://img.shields.io/github/license/pszypowicz/ampio-homeassistant)](LICENSE)

A Home Assistant integration for the [Ampio Smart Home](https://ampio.com/) system. It talks to the local M-SERV controller over MQTT through the [`ampio-mqtt`](https://pypi.org/project/ampio-mqtt/) library. Local push, no cloud.

## Platforms

| Platform        | What you get                                                                 |
| --------------- | ---------------------------------------------------------------------------- |
| `sensor`        | Temperature, humidity, pressure, CO2, air quality, illuminance, loudness     |
| `binary_sensor` | Wired button inputs                                                          |
| `light`         | Dimmers, RGBW outputs, and relays tagged as lights in Ampio Designer         |
| `cover`         | Shutters and blinds, with position and slat tilt where the hardware has them |
| `switch`        | Remaining relays and Ampio flags, with the outlet class for plug-tagged ones |
| `button`        | Relays and flags marked as bell objects in Ampio Designer (a single press)   |
| `climate`       | Heating regulators with temperature readback and operating-mode presets      |
| `scene`         | The Ampio app's scene catalog                                                |

## Installation

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pszypowicz&repository=ampio-homeassistant&category=integration)

Click the badge, confirm the repository in HACS, install "Ampio", and restart Home Assistant.

Manual steps:

1. Open HACS in Home Assistant.
2. Add `https://github.com/pszypowicz/ampio-homeassistant` as a custom repository (type: Integration).
3. Install "Ampio" and restart Home Assistant.

Requires Home Assistant 2026.8.0 or newer. `ampio-mqtt` is installed automatically.

### Updating

Every `0.0.x` release is beta. None of them carries a migration, so an update can change device names or the entity set with no upgrade path. Take a backup before you update.

Your entity ids survive an update, and your automations keep working. Home Assistant builds an entity id once and keeps it after that.

If an update leaves you with missing entities or entities that stay unavailable, remove the integration and add it again. That is the supported first step, not a last resort. Home Assistant remembers a removed entity for 30 days, so a re-add restores your entity ids, your renames, and your areas.

See [docs/stale-entities.md](docs/stale-entities.md) to clean up an entity Ampio Designer no longer has, or to learn what happens to your ids when a release changes their form.

## Configuration

1. In the Ampio app, create a dedicated Home Assistant user and grant it the devices you want in Home Assistant.
2. In Home Assistant, go to Settings -> Devices & Services -> Add Integration -> Ampio.
3. Enter the M-SERV host and that user's MQTT credentials.

Devices appear as a hub for the M-SERV and one device per Ampio module. A module takes the name you gave it in Ampio Designer, or `Ampio module 0x<MAC>` when your account is not an administrator one. Every entity attaches to the module that carries its object, or to the hub for the M-SERV's own objects. Entities are named as in the Ampio app.

Rename the devices and assign the areas to suit yourself. Nothing you do there moves an entity id. Home Assistant normally builds an id from the area and the device name, but an Ampio entity carries its own: `<domain>.ampio_<server mac>_obj_<object id>`, the same string as its unique id. The ids are not pretty, and they never change. Your automations keep working through a rename, an area move, and an Ampio account tier change alike.

One physical output can carry several objects in Ampio Designer. Each object gets its own entity.

If a relay tagged as a light in Designer surfaces as a switch, see [docs/designer-quirks.md](docs/designer-quirks.md).

Do not toggle an object's Matter checkbox in Designer once the object has an entity here. Unchecking it clears the object's leaf id, and the entity then moves to the M-SERV hub until you check the box again. To stop the M-SERV's Matter bridge, use "Clear configuration" in Designer's Matter panel instead. See [docs/designer-quirks.md](docs/designer-quirks.md).

## Relationship to home-assistant/core

An earlier form of this integration is submitted to `home-assistant/core` as [PR #179548](https://github.com/home-assistant/core/pull/179548). That submission is still open, and getting Ampio into Home Assistant itself remains the goal.

This repository is where the integration is developed, and it now leads that pull request rather than mirroring it. Install it through HACS to run Ampio today. Field reports from real installs are what will eventually make the upstream version worth merging.

## Disclaimer

This integration is an independent, best-effort project and has no affiliation with Ampio. Use it at your own risk. It commands real hardware, and a wrong command moves real devices.

The M-SERV itself guarantees the safety of a standard account. The broker limits such an account to the objects granted in the Ampio app, and it denies the raw CAN surfaces on the wire. A defect in this integration or the underlying [`ampio-mqtt`](https://github.com/pszypowicz/ampio-mqtt) library cannot widen that boundary.

Ampio does not guarantee the stability of the wire surfaces this integration depends on. A server update or a module firmware update can change or remove behavior without notice, and breaking changes by Ampio are a known pattern. The author of an earlier Ampio integration [stopped maintenance for exactly this reason](https://github.com/kstaniek/ampio-hacc/issues/2). If your install works and you are happy with it, stay on your current versions and do not chase the latest ones. If you decide to update anyway, make a full backup first - ideally a full image of the M-SERV's microSD card.

Report bugs and ideas in the [issues](https://github.com/pszypowicz/ampio-homeassistant/issues). The [debugging guide](docs/debugging.md) shows how to capture diagnostics and debug logs for a report.
