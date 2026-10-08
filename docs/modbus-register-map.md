# Modbus TCP register map

The PLC simulator (`plant-ai modbus-server`, or the `demo` command) is a Modbus TCP server, device id 1, default
port 5020. All values are unsigned 16-bit integers; divide by the scale to get engineering units. The collector
reads the whole input-register block (37 registers) in one function-code 4 request per poll.

## Input registers (function code 4, read only)

| Register | Tag | Description | Unit | Scale |
|---|---|---|---|---|
| IR 0-1 | (plant time) | Minutes since the Unix epoch, high word first | min | 1 |
| IR 2 | FIT-101 | Influent flow | m3/h | x10 |
| IR 3 | AIT-101 | Influent pH | pH | x100 |
| IR 4 | TT-101 | Influent temperature | degC | x10 |
| IR 5 | AIT-102 | Influent conductivity | uS/cm | x1 |
| IR 6 | AIT-103 | Influent turbidity | NTU | x10 |
| IR 7 | LIT-101 | Equalisation tank level | m | x100 |
| IR 8 | FV-101 | Equalisation outlet valve command | % | x10 |
| IR 9 | ZT-101 | Equalisation outlet valve position | % | x10 |
| IR 10 | FIT-102 | Forward flow to treatment | m3/h | x10 |
| IR 11 | AIT-201 | pH correction tank pH (control probe) | pH | x100 |
| IR 12 | AIT-201B | pH correction tank pH (check probe) | pH | x100 |
| IR 13 | P-201_SP | Acid dosing pump rate command | L/h | x10 |
| IR 14 | P-201_RUN | Acid dosing pump running | - | x1 |
| IR 15 | LIT-201 | Acid day tank level | % | x10 |
| IR 16 | P-202_SP | Coagulant dosing pump rate command | L/h | x10 |
| IR 17 | FIT-202 | Coagulant dosing flow | L/h | x10 |
| IR 18 | P-202_RUN | Coagulant dosing pump running | - | x1 |
| IR 19 | LIT-202 | Coagulant day tank level | % | x10 |
| IR 20 | AIT-301 | Aeration basin dissolved oxygen | mg/L | x100 |
| IR 21 | B-301_CMD | Aeration blower run command | - | x1 |
| IR 22 | B-301_RUN | Aeration blower running | - | x1 |
| IR 23 | SC-301 | Aeration blower speed | % | x10 |
| IR 24 | JT-301 | Aeration blower power | kW | x10 |
| IR 25 | AIT-401 | Clarified water turbidity | NTU | x10 |
| IR 26 | AIT-501 | Discharge pH | pH | x100 |
| IR 27 | AIT-502 | Discharge COD | mg/L | x1 |
| IR 28 | TT-501 | Discharge temperature | degC | x10 |
| IR 29 | FIT-501 | Discharge flow | m3/h | x10 |
| IR 30 | JT-001 | Plant total power | kW | x10 |
| IR 31 | LIT-901 | Soda ash solution tank level | % | x10 |
| IR 32 | LIT-902 | Salt brine tank level | % | x10 |
| IR 33 | LIT-903 | Acetic acid tank level | % | x10 |
| IR 34 | FIT-901 | Dispensing flow | L/min | x10 |
| IR 35 | DS-900 | Dispensing station state (0 idle, 1 dispensing, 2 hold) | - | x1 |
| IR 36 | FQ-901 | Batches dispensed today | - | x1 |

## Holding registers (function codes 3, 6 and 16)

Writes outside the range are refused with the Modbus exception ILLEGAL DATA VALUE, so a bad write cannot push a
setpoint outside its engineering limits.

| Register | Setpoint | Description | Range | Scale |
|---|---|---|---|---|
| HR 0 | AIC-201_SP | pH correction setpoint | 6 to 8 pH | x100 |
| HR 1 | AIC-301_SP | Dissolved oxygen setpoint | 0.5 to 4 mg/L | x100 |
| HR 2 | LIC-101_SP | Equalisation level setpoint | 1 to 4 m | x100 |
| HR 3 | CTRL_MODE | Dosing strategy (0 fixed-rate, 1 adaptive) | 0 to 1 | x1 |

## Example

```bash
plant-ai read-tags --port 5020                 # one FC4 block read, decoded with the scales above
plant-ai write-setpoint AIC-301_SP 2.5         # FC6 write to holding register 1
plant-ai write-setpoint AIC-301_SP 9           # refused: outside 0.5 to 4 mg/L
```

The dashboard and the maintenance assistant have no write path to these registers.
