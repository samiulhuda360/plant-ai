# SOP-07 Control valve not following command (stuck valve)

Area: 100 Equalisation. Tags: FV-101, ZT-101, LIT-101, FIT-102.

## 7.1 Purpose and scope
Valve FV-101 sets the forward flow from the equalisation tank to treatment. Its position feedback ZT-101 should
follow the command FV-101 within a few seconds. A stuck valve stops the level controller from working: the
equalisation level then drifts up towards overflow, or down until the treatment plant is surged.

## 7.2 Symptoms and alarms
- Discrepancy alarm: valve FV-101 position does not follow command.
- Health alarm: valve FV-101 feedback not moving while the command changes.
- Equalisation level LIT-101 drifting away from its setpoint, then level high or low.
- Forward flow FIT-102 stays constant while the command changes.

## 7.3 Likely causes
- Loss of instrument air to the actuator, or a failed positioner.
- Fibre and lint jamming the valve trim, or a seized stem.
- Position transmitter fault (the valve moves but ZT-101 does not).

## 7.4 Immediate checks
- Check the instrument air pressure at the actuator and the positioner output.
- Look at the valve stem and position indicator locally and compare them with ZT-101 on the HMI.
- Check whether the forward flow FIT-102 changes when the command changes.

## 7.5 Corrective actions
- If the valve is stuck, ask the control room to put the level loop in manual; the supervisor may then authorise
  control of the forward flow by hand on the bypass valve.
- Free the stem or restore the air supply. Stroke-test the valve at 0, 50 and 100 % before it is returned to
  automatic.
- If only the transmitter is faulty, raise a work order and control on the forward flow reading.

## 7.6 Escalation and records
- If the equalisation level reaches the high-high alarm, follow SOP-11.
- Record the cause and the stroke-test results.
