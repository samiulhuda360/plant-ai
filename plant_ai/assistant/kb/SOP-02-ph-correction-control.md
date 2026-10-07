# SOP-02 pH correction loop and acid dosing

Area: 200 pH correction. Tags: AIT-201, AIT-201B, P-201_SP, P-201_RUN, LIT-201, AIC-201_SP.

## 2.1 Purpose and scope
Textile effluent is alkaline (typically pH 9 to 11 from the soda ash and caustic used in reactive dyeing). The pH
correction tank doses 30 % sulphuric acid with pump P-201 to bring the pH to the setpoint before coagulation,
because the coagulant works best near neutral pH and the discharge consent requires pH 6.5 to 8.5.

## 2.2 Symptoms and alarms
- pH correction tank pH high or low (AIT-201 HI or LO).
- Acid pump command at maximum for a long period, or at zero while the pH is high.
- Discharge pH trending towards its limits (AIT-501).

## 2.3 Likely causes
- An influent alkalinity rise larger than the acid pump capacity (see SOP-01).
- Acid day tank empty or the suction line air-locked (see SOP-05).
- A drifting or fouled pH probe misleading the loop (see SOP-03).
- Controller left in manual, or the setpoint changed.

## 2.4 Immediate checks
- Compare the control probe AIT-201 with the check probe AIT-201B. If they disagree, go to SOP-03.
- Check that P-201 is running and the acid day tank level LIT-201 is above the low alarm.
- Check the controller mode and the setpoint AIC-201_SP on the HMI.
- Look for white vapour or leaks at the acid dosing point.

## 2.5 Corrective actions
- If the load exceeds the pump capacity, ask the dye-house to hold drains (SOP-01).
- If the acid supply has failed, restore it following SOP-05 and SOP-15 before the pump is restarted.
- Ask the control room to return the loop to automatic once the probe and the pump are confirmed healthy.

## 2.6 Escalation and records
- Escalate to the process engineer if the loop cannot hold the setpoint for more than one hour.
- Log the cause, the duration and the minimum and maximum pH seen.
