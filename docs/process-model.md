# Process model

`plant_ai/sim/process.py` models the effluent treatment plant of a fictional textile mill, Acme Textiles, and the
dye-house chemical dispensing station that feeds it. It runs in one-minute steps and is seeded, so a seed always
gives the same influent, the same dye batches and the same sensor noise.

The model is simple on purpose. It is not a design tool. It keeps the cause-and-effect chains that matter for
alarms, dosing and maintenance, with plausible magnitudes.

## Units and equations

| Stage | Model | Main parameters |
|---|---|---|
| Dye-house dispensing | Batches arrive about every 75 min (exponential). Each batch draws soda ash, salt brine and acetic acid from day tanks, which refill automatically below 25 %. The spent dye bath drains 3 to 5 h later as a 12 to 20 min "dye dump". | Dump: 40-70 m3/h, alkalinity 4-7 meq/L, turbidity 500-900 NTU, 50-60 degC |
| Influent | Base flow 55 m3/h with a daily cycle and AR(1) noise, alkaline (about pH 9.8), plus any dumps. Streams mix by flow weighting. | Base turbidity 150 NTU, COD 800 mg/L, conductivity about 4000 uS/cm |
| Equalisation | Level mass balance (80 m2 footprint) and completely mixed contents. Outlet valve FV-101 moves at most 5 %/min; flow = valve x 140 m3/h x sqrt(level / 2.5 m). | Level setpoint 2.5 m |
| pH correction | Alkalinity balance in a 30 m3 tank; 30 % sulphuric acid adds 7.5 eq/L. pH from a buffered titration curve, pH = 7 + 1.7 asinh(alkalinity / 0.4). Two probes, AIT-201 (control) and AIT-201B (check). | Setpoint pH 7.3 |
| Coagulation / flocculation | Jar-test dose curve: optimal PAC dose = 0.25 x turbidity + 25 mg/L. Removal efficiency = 0.985 (1 - e^(-3r)) for dose ratio r, with a small overdose penalty above r = 2 and a penalty for pH away from 7. | PAC product 1.2 kg/L |
| Aeration | 900 m3 basin. COD removal by Monod kinetics on COD and dissolved oxygen. Oxygen transfer kLa = 0.36 x blower speed; saturation from temperature. Blower power = 6 + 30 x speed kW. | DO setpoint 2.0 mg/L |
| Clarifier | First-order settling (45 min) of the unremoved turbidity, worse above 80 m3/h surface loading. | |
| Discharge | pH (lagged 120 min, plus 0.2 from CO2 stripping), COD = basin COD + 1.2 x turbidity, temperature lagged and cooled. | Consent below |

Every run starts with a 240-minute warm-up so it begins from a settled plant.

## Discharge consent (fictional)

| Parameter | Tag | Limit |
|---|---|---|
| pH | AIT-501 | 6.5 to 8.5 |
| Clarified turbidity | AIT-401 | 20 NTU or less |
| COD | AIT-502 | 250 mg/L or less |
| Temperature | TT-501 | 38 degC or less |

## Control strategies

| | Fixed-rate baseline | Adaptive |
|---|---|---|
| Acid | On/off: pump at 22 L/h above pH 7.6, off below 7.0 | Feed-forward from a soft sensor of the equalisation tank (built from the influent pH and flow), plus a PI trim on AIT-201 |
| Coagulant | Constant 8 L/h, sized for dye dumps | Flow- and turbidity-paced dose (1.15 x jar-test dose) with a slow trim from the clarified turbidity |
| Aeration | Blower at 100 % | PI loop on dissolved oxygen at 2.0 mg/L |
| Equalisation | PI level control | Same |

Both strategies read only measured tags, as a PLC would, so a drifting probe misleads them exactly as it would
mislead a real loop.

## Planted faults

| Fault | What the simulator does | Randomised |
|---|---|---|
| pH probe drifting | Adds a ramp to AIT-201 or AIT-201B, capped at 2 pH | 0.5 to 0.9 pH/h, either sign, 150-210 min |
| Coagulant pump clogging | Delivered flow falls to (1 - blockage) of the command over 60 min | Blockage 60-90 %, 150-210 min |
| Chemical day tank running low | Make-up blocked and a passing drain valve on LIT-202 (PAC) or LIT-901 (soda ash) | 15-30 %/h, 240-300 min |
| Influent shock load | A concentrated dump: about 150 m3/h, alkalinity 20 meq/L, turbidity 2000 NTU, 72 degC | Severity 0.8-1.2, 30-45 min |
| Blower failure | Blower stops while still commanded on | 45-120 min |
| Stuck valve | FV-101 freezes at its position plus an offset | Offset 12-25 % either way, 120-240 min |
| Sensor flatline | AIT-301, AIT-401 or FIT-101 freezes at its last value | 120-240 min |

Each fault has ground truth: start, end, target and the set of related tags whose alarms count as detecting it.
