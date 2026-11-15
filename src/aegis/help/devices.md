---
title: Phones agents can use
kind: howto
when: read a phone's sensors or compass, use the person's Android phone from an agent, pair the aegis Android app, device_call
hints:
  - Install the aegis Android app and let your agents read the phone's sensors. #pair-a-phone
  - Ask an agent which way your phone points: it reads the compass. #operations
---

The aegis app for Android runs the same web client as the browser, and can
also let the agents of its owner use the phone: today its sensors and compass,
later its files and the apps an agent builds for it. The phone decides what
each call may do; the server only passes calls along. Nothing here needs a USB
cable or ADB.

## Pair a phone

Install the app from Settings → Phone app, on the phone's browser. Open it,
type the server's address as you open it in a browser, and sign in as usual.
Then Settings → Phone app → Let agents use this phone. Android asks to allow
notifications; the app keeps a quiet notification while agents can reach the
phone, and its Disconnect button stops that until you pair again or open the
app. Settings lists your paired phones, with Unpair for each.

Inside the app the bell's push is not used: the app shows the sessions that
need you, errors and toasts as its own notifications.

## The tools

`device_list` returns the phones paired to your session's owner: id, name,
whether it is online, its model, Android API level, app version and the
operations it runs.

`device_call` runs one operation on a phone and returns its result. Give `op`
and `args`, and `device` (a name or id) when the owner has more than one
phone. `timeout` is in seconds, 120 by default and at most 600.

## Operations

- device.info: manufacturer, model, Android version and API level, battery
  percent and whether it charges, the network (wifi, cellular, ethernet,
  none), and the operations it runs.
- sensor.list: every sensor with its type, name, vendor, range, resolution
  and minimum delay. A type such as accelerometer, gyroscope,
  magnetic_field, light, pressure or proximity names it in sensor.read.
- sensor.read: `args` {"type": "magnetic_field", "seconds": 2, "rate": 20}.
  Returns `samples`, each one the milliseconds since the start and the
  sensor's values in Android's order, at most 2000 of them.
- compass.read: `args` {"seconds": 2}. Returns the mean `azimuth` in degrees
  from magnetic north, and `samples` of milliseconds, azimuth, pitch and roll.

Hold the phone still and flat for a compass reading you will trust: the
samples show how much it moved.

## Errors

- no_device: no phone is paired. Ask the person to pair one.
- which_device: several are paired; name one with `device`.
- unknown_device: no paired phone has that name or id.
- device_offline: the phone is not connected. Ask the person to open the app.
- device_timeout: the phone did not answer within `timeout`.
- needs_newer_app: the phone's app does not run that operation yet.
- denied: the phone does not allow the operation.
- no_sensor: the phone has no such sensor; sensor.list says which it has.

## What leaves the phone

A result goes into your transcript, and the transcript goes to your model's
provider. Ask for no more than the task needs.
