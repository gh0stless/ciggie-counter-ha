# Ciggie Counter for Home Assistant

Count your cigarettes in Home Assistant, pace yourself with breaks and get reminders – standalone, no other app needed. Optionally it syncs with the [Ciggie Counter Android app and the Nextcloud app](https://github.com/gh0stless/ciggie-counter).

*Deutsch: Zigaretten in Home Assistant zählen, Pausen einhalten, Erinnerungen bekommen – eigenständig, oder im Abgleich mit der Android-App und der Nextcloud-App. Die Oberfläche gibt es auf Deutsch und Englisch.*

## Features

- **Count** with a button entity, the action `ciggie_counter.count`, by voice (Assist) – or with **any entity you already have**: a Zigbee button, a door contact on the ashtray, a switch, an event entity. Each press counts one cigarette.
- **Sensors:** today, yesterday, this month, this year, cost today, current break, next allowed, last cigarette, Butt Bin battery
- **Breaks:** fixed, or calculated from a daily target over your waking hours (smoke faster and the breaks get longer); binary sensor *Allowed*
- **Reminders** as events – "You may smoke again" and "Did you count them all?" – so you decide in an automation how they reach you (TTS, Telegram, a light …)
- **Mute switch** for everything, e.g. at night
- **Your day, your rules:** choose the hour at which a new day starts
- **Sync API** for the Android app, same protocol as the Nextcloud app (sync v2)

## Installation

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/gh0stless/ciggie-counter-ha` as *Integration*.
2. Install **Ciggie Counter**, restart Home Assistant.
3. Settings → Devices & services → *Add integration* → **Ciggie Counter**.
4. *Configure* for cost, breaks, reminders and the entities that should count.

Manual: copy `custom_components/ciggie_counter` to `config/custom_components/`.

## Entities

| Entity | |
|---|---|
| `button.ciggie_counter_count_cigarette` | count one |
| `sensor.ciggie_counter_today` / `_yesterday` / `_this_month` / `_this_year` | counts |
| `sensor.ciggie_counter_cost_today` | in your currency |
| `sensor.ciggie_counter_break` | minutes |
| `sensor.ciggie_counter_next_allowed`, `sensor.ciggie_counter_last_cigarette` | timestamps |
| `binary_sensor.ciggie_counter_allowed` | on = break is over |
| `switch.ciggie_counter_mute_reminders` | no reminder events while on |
| `sensor.ciggie_counter_butt_bin_battery` | reported by the Android app |

Entity IDs follow your language; the table shows the English ones.

## Actions

- `ciggie_counter.count` – optional `timestamp` (for a cigarette you forgot) and `source`
- `ciggie_counter.delete_last` – e.g. after a double press

## Events

| Event | Data |
|---|---|
| `ciggie_counter_reminder` | `type`: `next_allowed` or `count_check` (not fired while muted) |
| `ciggie_counter_counted` | `number` (today), `source`, `timestamp`, `muted` – only for fresh entries, not for backfills |

Example: speak the reminders.

```yaml
automation:
  - alias: "Cigarette reminders"
    mode: queued
    triggers:
      - trigger: event
        event_type: ciggie_counter_reminder
    actions:
      - action: tts.speak
        target:
          entity_id: tts.piper
        data:
          media_player_entity_id: media_player.living_room
          message: >
            {{ 'You may smoke again.' if trigger.event.data.type == 'next_allowed'
               else 'Did you count all your cigarettes?' }}
```

Example: mute at night.

```yaml
automation:
  - alias: "Cigarette reminders quiet at night"
    triggers:
      - trigger: time
        at: "23:00:00"
        id: "on"
      - trigger: time
        at: "07:00:00"
        id: "off"
    actions:
      - action: "switch.turn_{{ trigger.id }}"
        target:
          entity_id: switch.ciggie_counter_mute_reminders
```

## Sync with the Android app

The Android app can use Home Assistant instead of (or besides) Nextcloud: enter your Home Assistant URL (also from outside, e.g. Nabu Casa or your own domain) and a **long-lived access token** (your profile → Security). *Supported from Android app version 1.0.7.*

API, all under `/api/ciggie_counter`, Home Assistant authentication (Bearer token):

| | |
|---|---|
| `GET /today` | summary |
| `GET /v2/entries?since=N` | records changed after revision N: `{"rev", "entries": [{"id", "ts", "source", "deleted", "rev"}]}` |
| `POST /v2/entries` | JSON `{"entries": [{"id", "ts", "source", "deleted"}]}` – unknown IDs are added, a tombstone always wins |
| `GET`/`POST /mute` | `{"muted": bool}`, POST `muted=1`/`0` |
| `GET`/`POST /ashtray` | Butt Bin state (battery, firmware; placeholders for air quality, spray, display) |

Every cigarette has its own ID, so several entries in the same second stay separate, and deletions reach every device.

## Not a medical product

Ciggie Counter helps you see how much you smoke. It does not give medical advice.

## License

GPL-3.0 – © gh0stless ([crazy-midi.de](https://crazy-midi.de))
