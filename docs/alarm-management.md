# Alarm management and detection

The detection engine (`plant_ai/detect/engine.py`) runs once per minute on the latest sample. The evaluation and
the live collector call the same code.

## Layers

| Layer | What it checks | Examples |
|---|---|---|
| Limit | HI, HIHI, LO, LOLO limits with a deadband and a 2-minute on-delay | DO low below 1.0 mg/L, clears above 1.2 |
| Rate of change | Change over a window | PAC day tank falling more than 14 % in 60 min; DO falling 1.0 mg/L in 5 min |
| Discrepancy | Command against feedback | Blower commanded on but not running; PAC flow below 70 % of command for 10 min; valve position more than 8 % from command |
| Sensor health | Is the measurement believable? | Flatline (no change of half a register count for 30 min); the two pH probes disagreeing by more than 0.3 pH; valve feedback not moving while the command moves; analyser at range limit |
| Anomaly | PCA on 21 process tags, fitted on fault-free runs | Hotelling T-squared (extreme but correlated) and SPE (correlation broken), alarm above 1.3 x the 99.9th percentile for 5 min |

The threshold-only baseline in the evaluation is the limit layer alone.

## ISA-18.2 practices built in

- **Priorities**: every alarm has Low, Medium, High or Critical priority. Discharge consent breaches are Critical.
- **Deadbands and delays** stop alarms chattering around a limit.
- **Alarm states**: active unacknowledged, active acknowledged, returned to normal unacknowledged, cleared.
  Operators acknowledge from the dashboard; a later clear never overwrites the acknowledgement.
- **Designed suppression**: a consequence is logged but not annunciated while its cause is active (DO low while
  the blower discrepancy is active; turbidity HI while turbidity HIHI is active).
- **Flood suppression**: when 10 or more alarms have been annunciated in 10 minutes, new Low and Medium alarms are
  logged but not annunciated. High and Critical alarms always show.
- **Shelving**: the engine supports shelving an alarm until a time (logged as suppressed).
- **Incidents**: alarms raised within 45 minutes of each other form one incident, titled by its first-out alarm.
  An incident closes 30 minutes after its last annunciated alarm clears. Severity is the highest annunciated
  priority.
- **KPIs**: the shift report shows alarms per hour (target 6 or fewer), the peak in any 10 minutes, the most
  frequent alarms (bad actors) and the standing alarms at handover.

## PCA model

`plant_ai/detect/anomaly.py` standardises 21 tags (after 5-minute exponential smoothing), keeps the principal
components that explain 90 % of the variance, and sets T-squared and SPE limits at the 99.9th percentile of six
fault-free training runs (seeds 900-905, three days each). Those seeds are never scored. The fitted model is
cached in `.cache/` under a key that changes whenever the simulator code changes.

The alarm message lists the three tags that contribute most to the statistic. The assistant uses that list in its
search query, so a PCA alarm led by AIT-201 and AIT-201B finds the probe-drift procedure.
