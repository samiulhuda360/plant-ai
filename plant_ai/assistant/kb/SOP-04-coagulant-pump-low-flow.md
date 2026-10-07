# SOP-04 Coagulant dosing pump low flow or clogging

Area: 200 Coagulation. Tags: P-202_SP, FIT-202, P-202_RUN, LIT-202, AIT-401.

## 4.1 Purpose and scope
Pump P-202 doses polyaluminium chloride (PAC) coagulant into the flash mixer. The dosing line has a flow meter
FIT-202 so the delivered flow can be compared with the commanded rate. PAC crystallises and gels at fittings, in
the injection quill and in the pump valves, which partly or fully blocks the line.

## 4.2 Symptoms and alarms
- Discrepancy alarm: coagulant pump P-202 delivering less than commanded (FIT-202 well below P-202_SP).
- The command rises as the controller tries to compensate while the measured flow keeps falling.
- Clarified turbidity rising (AIT-401 rising, then AIT-401 HI), and the floc looks small and pin-pointed.

## 4.3 Likely causes
- Crystallised PAC in the injection quill or the non-return valve.
- Worn or stuck pump check valves, or a ruptured diaphragm.
- Suction strainer blocked, or air in the pump head.
- Coagulant tank nearly empty (see SOP-05).

## 4.4 Immediate checks
- Check the coagulant day tank level LIT-202 to rule out an empty tank.
- Check the pump stroke and the discharge pressure gauge: high pressure points to a blocked quill, low pressure
  to a suction or valve problem.
- Do a drawdown test with the calibration column to measure the real delivered flow.
- Look at the injection quill for white crystalline deposits.

## 4.5 Corrective actions
- Change over to the standby coagulant pump if one is installed.
- Isolate, depressurise and flush the line and the quill with warm water following SOP-15. Clean or replace the
  check valves.
- After the restart, confirm FIT-202 follows P-202_SP within 10 % and watch AIT-401 recover.

## 4.6 Escalation and records
- If the clarified turbidity exceeds the consent limit, follow SOP-09 and SOP-10.
- Log the cause and the parts replaced. Repeated clogging should be raised as a dilution-water or flushing
  improvement.
