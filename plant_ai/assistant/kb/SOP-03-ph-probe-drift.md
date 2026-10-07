# SOP-03 pH probe calibration, drift and probe disagreement

Area: 200 pH correction. Tags: AIT-201, AIT-201B, AIT-501.

## 3.1 Purpose and scope
The pH correction tank has two probes: AIT-201 controls the acid dose and AIT-201B is a check probe. A probe
that drifts (fouled junction, coated glass, ageing electrode, damaged cable) makes the controller hold the wrong
pH while the screen looks normal. This SOP covers detecting, confirming and correcting probe drift.

## 3.2 Symptoms and alarms
- Health alarm: pH probes AIT-201 and AIT-201B disagree, with one probe named as suspected of drifting.
- The control probe reads on setpoint but the discharge pH (AIT-501) moves away from its usual value.
- The acid dose changes without any change in influent pH or flow.
- The anomaly monitor names AIT-201 or AIT-201B as the top contributors.

## 3.3 Likely causes
- Fouling of the probe by dye, fibre lint or calcium deposits.
- Reference junction poisoning, or an ageing electrode past its service life.
- Water in the cable gland or a damaged probe cable.

## 3.4 Immediate checks
- Take a grab sample from the pH correction tank and measure it with the calibrated hand-held meter.
- Compare the hand-held reading with AIT-201, AIT-201B and the discharge pH AIT-501.
- Check when each probe was last calibrated (calibration log) and whether it is due.
- Inspect the probe for coating and the cable for damage.

## 3.5 Corrective actions
- If the control probe is wrong, ask the control room to place the pH loop in manual at the current acid rate,
  or to select the healthy probe for control, before the faulty probe is removed.
- Clean the probe in dilute hydrochloric acid, rinse, and carry out a two-point calibration with pH 7 and pH 10
  buffers. Replace the electrode if the slope is below 90 % or the offset is above 30 mV.
- Return the loop to automatic and confirm both probes agree within 0.1 pH.

## 3.6 Escalation and records
- Record the as-found and as-left readings in the calibration log.
- If the discharge pH left the consent band, follow SOP-10.
