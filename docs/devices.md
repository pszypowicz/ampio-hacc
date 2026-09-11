# Devices, areas, and entity ids

This page explains how the integration builds its devices, and where the names and the areas come from. It also explains why an entity id never changes, and what a change in Ampio Designer does.

## The device tree

The integration builds three levels of devices:

- One hub device for the M-SERV.
- One device per Ampio module, under the hub.
- One device per Ampio object, under its module.

The M-SERV's own objects sit under the hub. The tree follows the module each object belongs to in Ampio Designer.

One physical output can carry several objects in Ampio Designer. Each object gets its own entity.

One M-SERV per Home Assistant. Object ids are unique per server only, so the integration allows one entry.

## Names

An object device takes the name you gave the object in the Ampio app. A module takes the name you gave it in Ampio Designer. When your account is not an administrator one, a module reads `Ampio module <row>` instead, where the row is its number in Ampio Designer. The hub is always `M-SERV`.

A rename in Designer or in the app changes nothing in Home Assistant. Rename the device in Home Assistant instead.

## The Identify button

Each module device has an Identify button. A press lights the module's CAN LED for 30 s, so you can find the module in the cabinet. The button exists with the administrator login alone. A standard account is given none, because the Ampio server would not carry the frame. A DIN-rail module lights its CAN LED steadily. An M-DOT panel lights the LED on its back only, so a wall-mounted panel gives no visible sign.

The module keeps the LED lit until it receives a stop. The integration sends the stop after 30 s, and again at once when you reload or remove the integration. If Home Assistant restarts during those 30 s, the stop is never sent. Then the LED stays lit until Ampio Designer sends a stop or the module restarts.

## The module sensors

Each module device also carries two diagnostic sensors, a supply voltage and a temperature, both readings the module reports about itself. They exist with the administrator login alone, for the same reason as the Identify button. A module that never sends that reading leaves its sensor unknown rather than unavailable, because the reading is not replayed at connect and silence says nothing about the module's health.

## The buzzer

A module with a touch panel also gets a siren entity for its buzzer, on the administrator login alone. Turning it on sounds a single tone for a single length. The `ampio.buzz_pattern` action reaches the two-tone sequences the buzzer also supports, described in the Actions section of the project README.

## Areas

An object device takes the object's app room as its area when Home Assistant creates it. After that the area is yours. The integration never moves a device.

Home Assistant matches rooms to areas by name. If your Ampio rooms and your areas differ in spelling, rename one side before you add the integration, or move the devices afterwards.

## Entity ids

Rename the devices and assign the areas to suit yourself. Nothing you do there moves an entity id. Home Assistant normally builds an id from the area and the device name. An Ampio entity carries its own instead, the same string as its unique id. An entity backed by an Ampio object reads `<domain>.ampio_obj_<object id>`, for example `light.ampio_obj_7`. An entity that belongs to a module device instead reads `<domain>.ampio_module_<row>_<name>`, for example `button.ampio_module_12_identify`. The ids are not pretty, and they never change. Your automations keep working through a rename, an area move, an Ampio account tier change, and an M-SERV replacement alike.

See [faq.md](faq.md) for what an update does to an entity id, and for the reset procedure.

## Changes in Ampio Designer

The integration follows the Ampio catalogue while it runs, on both account tiers. An object you add in Designer gets its entity within seconds, under its module, in its app room. On a standard account the object must also be granted to the Home Assistant user in the app. An object you delete or hide loses its entity at once, and the repair on the Settings page lists it. The delete stays yours, because on a standard account a lost app permission looks the same as a delete. A relay you re-tag as a light, or a pulse time you set, is followed the same way.

The administrator login gets no catalogue push from the M-SERV. It gets a digest of the app tables on every save instead. The integration re-reads the catalogue when that digest changes, so a change in Designer appears a few seconds later there too.

## Moving an object to another module

If you move an object to another module in Designer, or a replacement gives a module a new row, the integration removes the object's entities. The repair on the Settings page then offers the delete of its device. After the delete, the object comes back under the new module with its area and its name. See [designer-quirks.md](designer-quirks.md) for the reason.

## The Matter checkbox

Avoid toggling an object's Matter checkbox in Designer once the object has an entity here. Unchecking it clears the object's leaf id, which the diagnostics use to join the object to its Designer record. The device tree does not depend on it. To stop the M-SERV's Matter bridge, use "Clear configuration" in Designer's Matter panel instead. See [designer-quirks.md](designer-quirks.md).
