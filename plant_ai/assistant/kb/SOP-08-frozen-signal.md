# SOP-08 Frozen or flatlined instrument signal

Area: all. Tags: any analyser or transmitter, for example AIT-301, AIT-401, FIT-101.

## 8.1 Purpose and scope
A live process measurement always moves a little. A value that stays exactly the same for 30 minutes or more
is usually frozen: the transmitter has failed to its last value, the analyser is in hold or maintenance mode, or
the communication path from the field to the PLC has stopped updating. Controllers and alarms that use a frozen
value act on stale information without any warning, so a flatline is treated as a fault.

## 8.2 Symptoms and alarms
- Health alarm: signal frozen (flatline), naming the tag.
- The trend shows a perfectly flat line while related measurements keep moving.
- A control loop that uses the tag stops moving its output.

## 8.3 Likely causes
- Analyser left in hold or calibration mode after maintenance.
- Transmitter failure with last-value hold configured.
- Communication fault: a remote I/O module, a gateway, or a fieldbus segment not updating.
- Sample line blocked for a sampling analyser.

## 8.4 Immediate checks
- Check the local display of the instrument and compare it with the HMI value.
- Check the analyser status (hold, calibration, fault) and the maintenance log for recent work.
- Check the communication diagnostics for the I/O module or gateway serving the tag.
- Cross-check the process with a related measurement or a grab sample.

## 8.5 Corrective actions
- Ask the control room to place loops that use the frozen tag in manual until the signal is restored.
- Take the analyser out of hold, or reset the communication module, following the instrument manual.
- If the signal cannot be restored, raise an urgent work order and take manual readings every hour.

## 8.6 Escalation and records
- Inform the instrument technician.
- Record the time the signal froze, the cause, and any manual readings taken.
