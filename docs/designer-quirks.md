# Ampio Designer quirks

## Matter device-type tags that only half exist

The device-type tag on an output ("Description in device" -> for example "Lighting - On-off light") is stored twice: in the output's description record inside the module's CAN memory, and mirrored into the `type` column of the M-SERV's object catalogue. The integration classifies relays from the catalogue column, because the column is served to every account tier. The CAN records answer the admin login only, and an entity's platform must build identically on both tiers (see the stability contract below).

Tags saved with older Ampio tooling exist only in the CAN record, and the catalogue column stayed empty. A relay in that state shows its Lighting tag in Designer, yet Home Assistant surfaces it as a switch. To check what the integration sees for an output, download the diagnostics and look up the object's `type` field in the catalogue payload - [debugging.md](debugging.md) shows how.

The fix, in the current web Designer: touch every affected output individually - select a different device type and switch it back to Lighting, so Designer registers an edit - then save once. One save covers all the outputs you touched. Verified behavior on a real install, and confirmed in the Designer web bundle:

- Designer tracks changes per module and per category (a `descriptions` dirty flag on the device), and the save re-sends a dirty module's whole description table over the CAN bus.
- The catalogue column, however, updates only for the outputs you actually edited in the UI. An untouched neighbor keeps its stale column even though its record just went over the wire again - which is why every output needs its own flip, however correct it looks in Designer.
- Designer registers an edit only on a real change, so flip the value away and back. A Lokalizacja change counts too, and the tag rides along with it.

## The Matter checkbox clears the leaf id

Every object row carries a `leafId`, the pointer to the module output that drives it. Designer's per-object "Matter" checkbox rewrites that field on every save. A check writes it back from the linked output record and re-syncs the `type` column from the module record. An uncheck saves the row without it, and the M-SERV stores an empty value.

The object survives the uncheck. It keeps its type, its rooms, and its state. The integration keeps its entity, its id, its name, its area, and its module, because the device tree reads `id_urzadzenia`, the Designer module row that every object carries on both account tiers, and not the leaf.

What the uncheck costs is the diagnostics. The leaf id is how the diagnostics download joins an object to its Designer record, so a leafless object carries no record there until you check the box again.

So leave the Matter box alone on every object that has an entity here, in either state. To stop the M-SERV's Matter bridge, use "Clear configuration" in Designer's Matter panel. That wipes the bridge's pairing and restarts it unpaired, and it touches no object. A check on a relay also re-syncs the type column from the module record, so a relay whose record lost its Lighting tag comes back as a switch (see the section above).

Verified on server 1865 with a virtual test relay, and pinned by the integration's tests: a leafless object keeps its module, and a hidden row yields nothing.

## Moving an object to another module

Home Assistant cannot move a child device to another parent. When you move an object to another module in Designer, or a replacement gives a module a new row, the object's device keeps its old parent. The integration removes the object's entities within seconds, logs one warning that names the object, and lists the device in the repair on the Settings page.

Submit the repair, or delete the object's device under Settings, then Devices and services. The object comes back under the new module, with its id, its area, and its name restored. If that object was the last one on its old module, the old module device stays behind empty, and the repair lists it too.

## Integer sensor slots and the Modbus divider

An M-CON-485 stores each Modbus reading in an integer sensor slot, and Designer turns the slot into a `bit 8`, `bit 16`, `sbit 16`, or `bit 32` object. The slot holds whole numbers only. To keep decimals, do this in Designer:

1. In the Modbus query, multiply the register by a power of ten, for
   example 100 for two decimals.
2. Open the object, click Dictionary, enable Divide by, and enter the same
   factor.
3. Set the Unit field, or a string format that ends with the unit, such as
   `%.2f A`. Click outside the Unit field before you save, because Designer
   commits the field when it loses focus.

The M-SERV applies the divider before it publishes, so the integration receives the real value and adds no scale of its own. The entity reads the unit from the string format tail first, then from the Unit field. Home Assistant's own unit table then gives the device class: `A` reads current, `V` voltage, `W` and `kW` power, `Hz` frequency, and `lx` illuminance. Some units belong to two classes in that table, and the integration settles these:

| Unit                    | Device class                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------- |
| kWh, Wh, MWh            | energy, as a running total                                                                        |
| °C, °F                  | temperature                                                                                       |
| hPa, Pa, kPa, bar, mbar | pressure (the M-SENS barometer reads atmospheric pressure, and a generic slot is not a barometer) |

Any other unit that belongs to two classes, such as `%` or `m³`, keeps the unit and gets no device class. A unit Home Assistant does not know does the same. A slot with no unit at all surfaces as a plain number with no state class, so it keeps no long-term statistics until you give it a unit.

Set the Unit field before the slot gets its entity when you can. For a slot that exists before you install the integration, that means before the install. For a new slot, it means the same Designer save that creates it. Home Assistant takes the unit of a new statistic from the first state in the five-minute window it compiles. A unit set after that state leaves the statistic with none. Such a statistic compiles no further, and the Energy dashboard picker never lists the slot, because the statistic carries no unit class. If the picker does not list a slot, open Developer tools, Statistics, and fix the unit issue on the entity. Both choices there, delete the old statistics or update the unit, put the slot in the picker.

A unit change in Designer reaches the entity at once. Home Assistant writes the precision hint and the original device class into its registry at registration, so those two follow on the next reload. If you change a unit to another unit of the same quantity, such as `A` to `mA`, Home Assistant keeps showing the unit it registered first and converts the values, because it stores that first unit as the display unit at registration. Home Assistant then raises its statistics repair for that entity, because the recorded history carries the old unit. A change of the divider rescales the value, and the history keeps the old scale.

The Divide by checkbox is the same mechanism that gives linear inputs their decimals, because Designer creates those with Divide by 10.

## The device list numbers rows by position, not by device id

The first column of Designer's device list is the row's place in the list. It is not the device id that the integration keys a module device on.

The two match on an installation where no device was ever deleted, so the difference never shows. Delete a device and the server keeps the id it gave every other device, leaving a gap. Designer's list has no gap, so every device below the deleted one is drawn with a number one lower than its real id. Create a device and fill the gap, and the numbers line up again.

So a number you read off that screen is not the id an object carries. It is also why a delete looks like it renumbered your devices. Nothing was renumbered. Device ids are only ever appended, and a freed id comes back only to a device with the same MAC address as the one that owned it.

## Deleting an object takes two passes

Removing an object from a place in Designer does not delete it. It unassigns it, and the object moves to the collapsed **UNGROUPED** section at the bottom. It still exists, it still belongs to its device, and Home Assistant still has its entity. Open UNGROUPED and delete it there to remove it.

Deleting a device behaves differently again: it applies at once, with no save step, and it leaves its objects behind.

Between those two, Designer can leave an object that is still shown to Home Assistant while the device it belongs to is gone. The integration handles it: that module device keeps its entities and takes the name `Ampio module <row>`, because no catalogue row is left to name it. Finish the delete in UNGROUPED and the repair on the Settings page lists what is left over.

## Designer's messages do not tell you what the server did

All three cases below were seen on **virtual devices**, and each one misleads in a different direction:

- **A virtual device created without an object offers no save button, and reaches the server anyway.** The device is there even though Designer never let you save it.
- **Renaming that device fails, and says so confusingly.** The toast reads **"Device does not exist"** with **"Name updated"** underneath. The title is right and the subtitle is wrong: the name is not saved.
- **Editing its MAC address in place can be dropped with no message at all.** On a real module the MAC edit goes through.

So do not trust the toast in either direction. Refresh the Designer page instead. It asks for confirmation and then asks for the password again, even when the browser has it stored, and what it shows afterwards is what the server holds.

## The stability contract

Ampio accounts upgrade and downgrade between the admin login and app-created users. The integration therefore derives everything that defines an entity's platform or the device topology from data the restricted tier receives.

Entity ids are exempt from that rule, because the integration writes them itself. Home Assistant normally builds an entity id from the area name, the device name, and the entity name. An Ampio entity carries its own id instead, which is the same string as its unique id. An object's entity reads `<domain>.ampio_obj_<object id>`, and a module's reads `<domain>.ampio_module_<row>_<name>`. No name reaches it. Rename a device, move it to another area, or switch the account tier, and every id holds still.

That frees the device name. An object device takes the name you gave the object in the Ampio app. A module takes the name you gave it in Ampio Designer where the admin-only module catalogue answers, and falls back to `Ampio module <row>` on a restricted account, where the row is its number in Designer. The hub is always `M-SERV`. The catalogue also decorates the module's model, the firmware and hardware versions, and the serial number. All of those follow the account tier, so a tier change renames a module in the interface and moves nothing else. The parent of an object device derives from the Designer module row id, which both tiers receive, so no tier change moves a device either. Home Assistant cannot move a child device to another parent, so the one thing that does move an object between modules is a Designer edit followed by the delete described above.
