# Frequently asked questions

If something looks wrong after an update or after a change in Ampio Designer, find the symptom below. Each answer tells you how to check whether it affects you, and how to fix it.

## Back up first

Do not start a procedure on this page without a backup. Some of them delete records that Home Assistant cannot rebuild from the Ampio server.

1. Open Settings, then System, then Backups.
2. Select "Create backup".
3. Wait for the backup to finish.

## Entities are missing or stay unavailable after an update

**Check:** Open Settings, then Devices and services, then Ampio. Compare the entity count with what you had. An entity with the state `unavailable` or the label "restored" is one the integration no longer builds.

**Fix:** Remove the Ampio integration entry, then add it again. Home Assistant remembers a removed entity for 30 days, so the re-add restores your entity ids, your renames, and your areas. That is the supported first step, not a last resort. If the entity count is still wrong afterwards, download the diagnostics as described in [debugging.md](debugging.md) and report it in the issues.

## Setup does not finish, and stays on "Retrying setup"

**Check:** Open Settings, then Devices and services. The Ampio entry shows "Retrying setup" instead of its usual state, and the reason underneath reads "Connected to the Ampio server, but it did not answer the module description request." This affects the administrator login only, because a standard account never sends that request.

**Fix:** None is required. The integration retries the request on its own, waiting longer between each attempt, and finishes loading as soon as the M-SERV answers. If it stays stuck for several minutes, check whether the M-SERV itself is slow to respond, mid-restart, or overloaded, and give it time to recover. If the entry still will not finish after that, download the diagnostics as described in [debugging.md](debugging.md) and report it in the issues.

## An entity is listed that Ampio Designer no longer has

**Check:** Such an entity shows the state `unavailable` or the label "restored". After each start or reload the integration raises up to two repairs on the Settings page, under Repairs. One lists the devices and entities it did not build and cannot explain. The other lists the entities it withheld because your Ampio account is not the administrator one.

**Fix:** Select Submit on the repair to delete them all at once. The integration then reloads. An object that you moved to another module in Ampio Designer comes back under its new module.

The repair never deletes on its own. On an account that is not the administrator one, an object that lost its app permission looks the same as a deleted object. Read the list before you submit.

To delete a single record by hand instead:

1. Open Settings, then Devices and services, then Entities.
2. Search for the entity.
3. Select it, then select the cog icon.
4. Select "Delete".

If the "Delete" button is not offered, the integration still creates that entity. Check Ampio Designer before you go further.

## I changed my Ampio password

Home Assistant notices on its own. The entry stops, and a notification asks you to sign in again. Open it, enter the new password, and submit.

To change the password before it breaks, open the entry, choose Reconfigure from its menu, and enter the new one.

## I want to use a different Ampio account, or I replaced my M-SERV

Open the entry, choose Reconfigure from its menu, and enter the address and the credentials to use. Your devices and your entities keep their ids, their areas, and any name you gave them yourself.

Do not delete the integration for this. A delete removes every device record and every entity record. You lose every rename and every area with them.

A different account changes what the server serves you. An app-created user receives the objects granted to it in the Ampio app, so an object outside that grant loses its entities. The repair on the Settings page lists them. Read the list before you submit it. If you move to a standard account, a second repair lists the administrator-only entities it withheld, such as the Identify buttons, the module sensors, and the buzzer, and that one is explained under "The administrator-only entities are gone".

If the address you enter answers with different M-SERV hardware, the flow asks you to confirm first. It names the CAN address it found. Continue only if you replaced the M-SERV, or if you meant to point Home Assistant at another one.

## My entity ids look different from the ones in the docs

**Check:** Open Settings, then Devices and services, then Entities, and search for `ampio`. The current form is `<domain>.ampio_obj_<object id>` for an object's entity, for example `light.ampio_obj_7`, or `<domain>.ampio_module_<row>_<name>` for a module's, for example `button.ampio_module_12_identify`. An install that predates a change of the form keeps its older ids.

**Fix:** None is required. Both forms work, and nothing forces you to change. A release can change the form for a fresh install, and every existing install keeps the ids it has. If you want the current form on an existing install, use the reset procedure below. It is a support procedure, and no update requires it.

## Why does an entity id never change on its own?

Home Assistant builds an entity id once, when the entity registers for the first time. After that the id is stored, and no later change moves it. A device rename does not move it. An area change does not move it. An integration update does not move it.

Removing the integration does not move it either. Home Assistant remembers a removed entity for 30 days. If you add the integration again inside that window, the old entity id comes back. The name you gave the entity and the area you put it in come back with it.

This is good behavior. Your automations keep working across an update, a rename, and a reinstall.

## A relay I tagged as a light shows as a switch

See [designer-quirks.md](designer-quirks.md). That page tells you how to check the tag in the diagnostics, and how to re-save the output in Designer.

## An object sits under the wrong module

See [designer-quirks.md](designer-quirks.md). That page explains why Home Assistant cannot move a child device, and how the repair puts the object under its new module.

## A module shows no last-seen time in the diagnostics

**Check:** Find the module's row under `snapshot.modules` in the diagnostics download, as described in [debugging.md](debugging.md). Look at `supply_voltage` in the same row. If that value is empty too, the module sends no health broadcast, and its last-seen time moves only when one of its objects changes. On the reference install the roller modules, the M-SERV row, and an M-CON-s on old firmware send none. The library lists every type and firmware in [raw-channel-bridge.md](https://github.com/pszypowicz/ampio-mqtt/blob/main/docs/raw-channel-bridge.md). A module that shows a voltage does send the broadcast. Its last-seen time can still lag by a few minutes after a restart, because the broadcast comes on a change of the reading only.

**Fix:** None is required. An empty value says only that nothing arrived from that module since the restart. Move one of its covers, or wait for one of its objects to change, and the row fills. If a light or a cover on that module still responds, the module is alive.

## The administrator-only entities are gone

Several module entities are provided with the administrator login alone: the Identify button, the supply voltage and temperature sensors, and the buzzer on a module with a touch panel. The Ampio server carries the frames and the readings behind them to that login and no other, so a standard account is given none of them.

The Identify button once existed on both accounts, where a press on a standard account only ever returned an error. If you did not change anything, an update is what removed it. If you changed the account this integration uses, that removed it too. The sensors and the buzzer are administrator-only from their first release, so a standard account never had them to lose.

A repair on the Settings page lists whichever of these entities your account withholds. Submit it to delete the records, or leave it alone. If you point the integration back at the administrator account, the entities come back on their own with the same entity ids.

## My module devices are named "Ampio module 12" now

Only on a standard Ampio account. The names you gave your modules in Ampio Designer are served to the administrator login alone, so an update replaced a name built from the module's address with its row number in Designer. That row is where the module was named in the first place, and it is still the same device you have always had.

A name you typed yourself in Home Assistant is untouched, and no entity id moved. On an administrator account nothing changed.

## How do I reset every Ampio entity id?

Use this to move an existing install onto the current id form. The procedure deletes every Ampio entity record, so Home Assistant builds the ids again from scratch on the next start.

**Every automation, script, scene, and dashboard card that names an Ampio entity id stops working.** Write those ids down first, and plan to repoint them.

You need shell access to the Home Assistant host, through the SSH add-on or the Terminal add-on.

1. Take a backup, as described above.
2. Write down the Ampio entity ids your automations use.
3. Stop Home Assistant:

   ```sh
   ha core stop
   ```

4. Copy the entity registry, then remove every Ampio record from it:

   ```sh
   sudo cp /config/.storage/core.entity_registry /config/.storage/core.entity_registry.bak
   sudo jq '(.data.entities, .data.deleted_entities) |= map(select(.platform != "ampio"))' \
     /config/.storage/core.entity_registry > /tmp/registry.json
   sudo cp /tmp/registry.json /config/.storage/core.entity_registry
   ```

5. Start Home Assistant:

   ```sh
   ha core start
   ```

6. Open Settings, then Devices and services, then Ampio. Confirm that the entity count matches what you had.
7. Repoint your automations at the new ids.

The `deleted_entities` list matters as much as the `entities` list. Leave the deleted records in place, and Home Assistant restores every old id on the next start.

### If something goes wrong

Stop Home Assistant, copy the backup file back, then start Home Assistant:

```sh
ha core stop
sudo cp /config/.storage/core.entity_registry.bak /config/.storage/core.entity_registry
ha core start
```

If the instance does not start at all, restore the full backup from Settings, System, Backups.

## What this does not touch

- Your Ampio configuration. The M-SERV holds it, and this integration only reads it.
- Your devices. Device names and areas live in a separate registry.
- Any other integration. The filter selects the `ampio` platform alone.
