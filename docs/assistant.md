# Maintenance assistant

The assistant explains one alarm: what is happening, the likely causes, the checks to make and a draft for the
shift-handover log, with the SOP sections it used.

## Evidence

`plant_ai/assistant/context.py` builds the evidence from the historian:

- the selected alarm (rule, priority, value, state and any evidence such as the command value or the PCA
  contributors);
- the other alarms in the same incident or active at the same time;
- the last 60 minutes of up to 10 related tags: the value now, the range, and the change over 30 minutes.

An active alarm is explained with the latest data; a past alarm with the hour around it.

## Retrieval

16 SOPs in `plant_ai/assistant/kb/` are split into numbered sections (96 in all). BM25 indexes each section
together with its SOP title and the SOP's tag list, so an instrument tag such as `AIT-301` finds every section of
the procedures that use it. An SOP's score is its best section plus a quarter of its next two sections. The query
is the alarm message, its tag and description, the PCA contributors and the related alarms.

Optional hybrid retrieval adds embeddings (`gemini-embedding-001`) fused with BM25 by reciprocal rank. It is off by
default and is reported separately in the evaluation.

## Two answer modes

| | Retrieval + template | LLM + guards |
|---|---|---|
| Needs a key | No | `AI_API_KEY` |
| Summary | Built from the alarm and the tag that moved most | Written by the model from the facts |
| Causes and checks | The top SOP's "Likely causes" and "Immediate checks" | Chosen by the model from the top three SOPs |
| Citations | The two sections used | Any retrieved sections, root-cause SOP first |

The model sees the facts block and sections 2 to 5 of the top three SOPs, and must answer in JSON.

## Guards

Every model answer passes through `plant_ai/assistant/guards.py`. If any guard fails, the template answer is shown
and the reason is recorded.

1. **Schema**: valid JSON with summary, likely causes, checks, handover and citations.
2. **Citations**: at least one, and only sections that were retrieved for this alarm.
3. **No invented readings**: every number in the answer must appear in the facts or the retrieved SOP text
   (within rounding). Instrument tags, SOP references and dates are ignored when numbers are extracted.
4. **Never command equipment**: no claims of having operated plant ("I have restarted ..."), no control syntax
   (`write_register`, `:=`), and no check that starts with a control verb (start, stop, open, close, set ...).
   Control actions stay with the operator and are phrased as "Ask the control room to ...", as in the SOPs.

The assistant also has no write path: it reads the historian and the SOP files only, and the dashboard API has no
endpoint that writes to the PLC.

## Model client

`plant_ai/assistant/llm.py` talks to any OpenAI-compatible endpoint (Gemini's by default,
`gemini-flash-lite-latest`). Responses are cached on disk with their token counts and latency, live calls are
spaced at least 2.5 s apart, and rate limits are retried with back-off. The evaluation cache is committed in
`eval/llm-cache/`, so `plant-ai eval assistant --llm --offline` reproduces the reported LLM numbers without a key.
