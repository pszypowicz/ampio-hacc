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

What the uncheck costs is the diagnostics. The leaf id is how the diagnostics download joins an object to its Designer record, so a leafless object carries no record there until you check the box again. It is also where a module device gets the mac in its `Ampio module 0x<MAC>` fallback name, so a module whose objects have all lost their leaf reads `Ampio module <row id>` on a restricted account instead.

So leave the Matter box alone on every object that has an entity here, in either state. To stop the M-SERV's Matter bridge, use "Clear configuration" in Designer's Matter panel. That wipes the bridge's pairing and restarts it unpaired, and it touches no object. A check on a relay also re-syncs the type column from the module record, so a relay whose record lost its Lighting tag comes back as a switch (see the section above).

Verified on server 1865 with a virtual test relay, and pinned by the integration's tests: a leafless object keeps its module, and a hidden row yields nothing.

## Moving an object to another module

Home Assistant cannot move a child device to another parent. When you move an object to another module in Designer, or a replacement gives a module a new row, the object's device keeps its old parent. The integration removes the object's entities within seconds, logs one warning that names the object, and lists the device in the repair on the Settings page.

Submit the repair, or delete the object's device under Settings, then Devices and services. The object comes back under the new module, with its id, its area, and its name restored. If that object was the last one on its old module, the old module device stays behind empty, and the repair lists it too.

## The stability contract

Ampio accounts upgrade and downgrade between the admin login and app-created users. The integration therefore derives everything that defines an entity's platform or the device topology from data the restricted tier receives.

Entity ids are exempt from that rule, because the integration writes them itself. Home Assistant normally builds an entity id from the area name, the device name, and the entity name. An Ampio entity carries its own id instead, `<domain>.ampio_obj_<object id>`, which is the same string as its unique id. No name reaches it. Rename a device, move it to another area, or switch the account tier, and every id holds still.

That frees the device name. An object device takes the name you gave the object in the Ampio app. A module takes the name you gave it in Ampio Designer where the admin-only module catalogue answers, and falls back to `Ampio module 0x<MAC>` on a restricted account. The hub is always `M-SERV`. The catalogue also decorates the module's model, the firmware and hardware versions, and the serial number. All of those follow the account tier, so a tier change renames a module in the interface and moves nothing else. The parent of an object device derives from the Designer module row id, which both tiers receive, so no tier change moves a device either. Home Assistant cannot move a child device to another parent, so the one thing that does move an object between modules is a Designer edit followed by the delete described above.
