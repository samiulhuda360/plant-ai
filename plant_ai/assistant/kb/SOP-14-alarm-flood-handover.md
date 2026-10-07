# SOP-14 Alarm flood handling and shift handover

Area: all. Tags: alarm system, incidents.

## 14.1 Purpose and scope
How the operator manages many alarms at once, and how the shift handover is written. The alarm system follows
ISA-18.2: every alarm has a priority, low-priority consequences are suppressed during a flood, and related
alarms are grouped into one incident with a first-out alarm.

## 14.2 Symptoms and alarms
- More than 10 alarms in 10 minutes (an alarm flood).
- Many alarms in one incident, often led by an influent or equipment alarm.

## 14.3 Likely causes
- One upstream event (shock load, blower trip, power dip) causing consequential alarms downstream.
- Chattering alarms with a deadband too small.

## 14.4 Immediate checks
- Work from the incident list, not the alarm list: deal with the first-out alarm of each open incident.
- Handle Critical and High priority alarms first.
- Use the alarm shelving function only with the supervisor's agreement and always with an expiry time.

## 14.5 Corrective actions
- Acknowledge alarms once they have been assessed, not before.
- Report chattering or nuisance alarms for rationalisation.

## 14.6 Escalation and records
- The shift handover note lists: open incidents and their status, alarms still active, chemicals and tank levels
  that need attention, equipment out of service, and any consent exceedance with its report status.
- Handover notes are drafted from the historian and reviewed and signed by the outgoing operator.
