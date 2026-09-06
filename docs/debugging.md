# Debugging

Two tools answer most questions: the diagnostics download shows what the integration sees, and debug logging shows what the connection does. Start with diagnostics - the log is thin by design.

## The diagnostics download

Settings -> Devices & Services -> Ampio -> the three-dot menu on the entry -> Download diagnostics. The file is JSON with two blocks:

- `entry_data`: the config entry with host, username, and password redacted.
- `snapshot`: the library's health report.
  - `access_tier`: `admin` or `restricted` - the account class the server assigned to your login. Several features differ by tier (module metadata, Designer locations), so check this first.
  - `available`, `auth_failure`: whether the broker connection is up, and the rejection reason when the connection loop stopped for auth.
  - `server_info`: the M-SERV self-report (the LAN address is redacted).
  - `connection`: liveness counters - `started_at`, `reconnect_count`, `last_message_at`, `last_error`, and `subscribe_failures` (topics the broker rejected, usually a grant problem).
  - `mac_collisions`: override MACs shared by more than one module row.
  - `last_payloads`: each server endpoint's verbatim last reply. This is the raw material everything else derives from.

### Reading the raw catalogue

`last_payloads.details` holds the object catalogue as the server sent it, one row per object under `List`. Search for your object by its name (`opis_menu`). The fields the integration classifies from:

- `typ_komponentu`: the object type (`przekaznik`, `flaga`, `roleta`, ...) - decides the base kind.
- `type`: the Matter device-type tag mirror - `"256"` (0x0100) marks a relay as a light. An empty value on a relay you tagged in Designer is the half-existing-tag case: see [designer-quirks.md](designer-quirks.md).
- `params`: the Designer flag bitfield - bit 4 hides an object, bit 6 marks it read-only.
- `leafId`: the module output the object drives, and the mac its device parents to - empty once Designer's Matter box is unchecked, which costs the object its module and not its entity.

Privacy note before sharing a download publicly: credentials, host, and LAN address are redacted, but object, room, and module names are present verbatim.

## Debug logging

Settings -> Devices & Services -> Ampio -> Enable debug logging. The switch raises both `custom_components.ampio` and the `ampio_mqtt` library to debug; when you disable it again, Home Assistant offers the captured log as a download. The YAML equivalent:

```yaml
logger:
  default: warning
  logs:
    custom_components.ampio: debug
    ampio_mqtt: debug
```

What to expect in the log:

- Connection lifecycle: one warning when the broker connection is lost, one info line when it is restored, and an error with an automatic reload when the connection ends for good (for example after a credential change).
- Setup degradation warnings: a failed room-map fetch or a failed Designer-description sweep, each with its consequence.
- From the library at debug level: connection errors and messages it dropped as unparseable.

State questions ("why is this entity missing", "why is this a switch") are catalogue questions - answer them from the diagnostics download, not the log.
