- **A quota gauge projects at the current rate.** The arrow was the window's
  average since it opened, so an early burst kept it over 100%, and a `pace`
  line shut, for days after the agents had stopped. It is now what is spent plus
  the recent rate times the hours left, with a half-life per window length (6 h
  for a week) set in `quota.projection.half_life`, and the rate shows on hover
  and in `quota_read`.
help: usage#where-a-quota-gauge-says-it-will-land
