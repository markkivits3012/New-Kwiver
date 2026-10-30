# Plan: connecting the course map to Mark's self-driving pushcart

_Draft 2026-10-28, for Tim and Mark to review._

**Status 2026-10-28:** Phase 4 step 1 is done. A mock trolley runs in the PC sim with
the full command set, keep-out checks, route planning, heartbeat stop and audit log
(see README, "Self-driving trolley"). Phases 1–3 against the real trolley wait on
the questions below.

## What we know

- The "cart" is a **self-driving golf pushcart (trolley)**, homemade on **ROS**.
- It **plans its own path to any goal point** (Nav2-style), so "go to X" is
  a single goal. It doesn't need a hand-built route.
- It takes routes as **GPX files**. `waypoints (2).gpx` was made in
  geoplaner.com (a route planner), so it's most likely a route drawn for the
  trolley, not a recording. It hugs the mapped cart paths to within 1–3 m. The
  first 51 points are a separate test route ~15 km away.
- Goal: the 5" Pi screen shows **where the trolley is** and can **send it
  live commands**, on the course **during play**.

## Design rules

1. **The trolley stays in charge of moving.** The app only sends goals
   ("go to this point") and "stop". Path planning, obstacle avoidance, speed
   limits and the physical stop stay on the trolley.
2. **The app refuses bad goals.** Every goal is checked against a keep-out
   map built from the course data before it's sent: greens (+3 m), tee boxes,
   bunkers, water (+5 m), and anything outside the course boundary.
3. **Stop is always one tap and always on screen.** Every "go" command needs
   a second tap to confirm, so a bump on the screen can't send it off.
4. **The link is private and checked.** The trolley's ROS bridge is only
   reachable on its own network (its own WPA2 hotspot or a cable), only the Pi
   app may command it, and the app sends a heartbeat. If the heartbeat stops,
   the trolley holds position (this needs a small watchdog node on the trolley).
5. **Every command is logged** on the Pi (time, command, goal, trolley
   position) so any odd behavior can be traced afterwards.

## Phases

**Phase 0: learn Mark's setup.** See the questions below. It blocks phases 1 and 3.

**Phase 1: live trolley position (read-only, no risk).**
Connect the app to the trolley through **rosbridge** (the standard ROS ↔ web
bridge) instead of, or alongside, gpsd. Show the trolley's position, heading,
battery and state (idle / driving / following) on the map. The yardages can
come from the trolley's GPS, which is likely better than the USB puck if it's RTK.

**Phase 2: course map for the trolley.**
Export the keep-out zones and preferred paths from `course.js` in the form the
trolley can use: GeoJSON, a Nav2 keep-out mask in its map frame, or GPX. Also
check that its own planner won't route across a green or tee.

**Phase 3: commands.** Proposed short list:

| Button | What it sends |
|---|---|
| **STOP** | Cancel the current goal and hold. Always visible, one tap |
| **Next tee** | Goal = a parking spot beside the next hole's tee (off the tee box) |
| **Green side** | Goal = a parking spot beside this green, on the next-tee side |
| **Come to me** | Goal = the golfer's position (needs a phone or tag; see questions) |
| **Tap map → Go here** | Any point that passes the keep-out check, with a confirm step |

The app shows the planned path and ETA while it drives.

**Phase 4: testing, in this order.**
1. **PC:** add a mock trolley to `gps_server.py --sim` that accepts goals and
   "drives" to them, so the whole UI can be tested without the hardware.
2. **Mark's yard or test area**, with the real trolley.
3. **Course with no players nearby** (early morning), someone walking alongside.
4. **During play**, only after the course has said yes.

## Questions for Mark

**ROS setup**
1. ROS 1 or ROS 2, and which version (Noetic, Humble, Jazzy…)?
2. What computer runs ROS? Is it the same Pi 4 as the touchscreen, or a separate board?
3. Is **rosbridge** installed, or OK to install? Or is there another way in (custom topic, REST)?

**Position and navigation**
4. Which topic has the position (e.g. `/fix` NavSatFix, `/odometry/gps`)? Plain GPS or **RTK**?
5. How are goals sent: a Nav2 `NavigateToPose` action, the GPS waypoint follower, or something custom?
6. Is there a map/costmap where a keep-out mask can go?

**Safety**
7. Is there a physical stop on the trolley? What happens if it loses GPS or a sensor?
8. Obstacle sensing: what sensors, and does it stop for people, bags, carts and ball-washers?
9. Top speed? **What's the steepest grade it can climb and descend loaded** (in %, or
   degrees)? The map blocks anything over 20% for now and makes 8–20% costlier. Does it
   handle drop-offs near water? Can it drive under trees (low branches, roots, GPS loss)?
10. What does it do if its controller crashes or the link drops while it's moving?

**Behavior on the course**
11. How does "follow me" or "come to me" find the golfer today: phone, Bluetooth tag, camera?
12. Was the GPX drawn in Geoplaner for the trolley to follow? What was the route ~15 km away?
13. Has the course (pro shop / town) been asked about a self-driving trolley during play?

## Things only the course or Mark can decide
- Permission to run it during play, and any course rules (no greens or tees,
  must stay within X m of the golfer, etc.).
- Whether the "go ahead to the next tee" feature is allowed at all. It means
  the trolley moves with no one beside it.
