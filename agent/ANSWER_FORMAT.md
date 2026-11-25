# Format of answer.json

Write one JSON object with exactly these fields. A program reads it; text outside the fields is ignored.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `"1"` | Fixed. |
| `verdict` | `"intended"`, `"mistake"`, `"cannot_decide"`, `"no_fault"` | See below. |
| `findings` | list | Every place that plays a part in the incident. |
| `constraint` | finding id | The setting, rule or state that the incident runs into. The verdict is about this finding. |
| `decision_used` | `"D-0NN"` or `null` | The entry of DECISIONS.md the verdict rests on. |
| `decision_quote` | text or `null` | That entry's sentence, copied. |
| `if_intended` | object | What has to change if the constraint is something the team wants. |
| `if_mistake` | object | What has to change if the constraint should not be there. |
| `open_question` | text or `null` | Only for `cannot_decide`: the one question you would put to the team. |

## The verdict

- `intended`: the constraint is something the team decided. `if_intended` is your answer.
- `mistake`: the constraint should not be there. `if_mistake` is your answer.
- `cannot_decide`: the files do not say which. Fill `open_question`; `decision_used` is `null`.
- `no_fault`: nothing is wrong with the system. `constraint`, `if_intended` and `if_mistake` are `null`. `findings` may list what you checked.

Fill both `if_intended` and `if_mistake` whatever your verdict is (except `no_fault`). Each has:

- `root_cause`: the id of the finding that has to change in that case.
- `fix_class`: one of four. Choose by what the fix does, not by where it is done.
  - `"revert"`: take away the thing that was added or changed, so the system is as it was before. Dropping an index, removing a setting, undoing a config or code change and undoing an edit made in a data store are all `revert`, also when the thing is inside a data store.
  - `"finish"`: keep the thing and complete what is missing around it, for example change the code so it works with a new limit or index.
  - `"repair_data"`: put back or correct stored data that was lost or damaged, when there is no change to take away (for example restore deleted rows or an emptied cache).
  - `"none"`: no fix.
- `fix_what`: one or two sentences.

A fix with class `revert` on the constraint itself is never right under `if_intended`.

## A finding

| Field | Meaning |
|---|---|
| `id` | `F1`, `F2`, ... |
| `kind` | `"file"`: a line or part of a file under `source/`. `"store"`: the state of a data store. `"runtime"`: something that was done to the running system. |
| `service` | Container or service name as in `docker-compose.yml`. |
| `file`, `line`, `symbol` | For `file`. Line numbers are those of the files you were given. `line` is `null` when the finding is a whole file, a function, or something that is missing; then give `symbol` if there is one. |
| `object_type`, `object_name` | For `store`. Type is one of `index`, `document`, `documents`, `collection`, `key`, `keys`, `whole_store`, `setting`. The name is the database, collection, index or key, as exactly as the files allow. |
| `action` | For `runtime`. One of `restart`, `recreate`, `replace`, `scale`, `deploy_step`, `manual_edit`, `job`, `other`. |
| `what` | One or two sentences. |
| `evidence` | At least one `{source, ref, quote}`. `source` is `ticket`, `source`, `log`, `trace`, `decisions` or `store`. `ref` is a path, with `:line` when there is one; for `store` it is the request you made. `quote` is copied text. |

Fields that do not apply are `null`, not left out.

Order: if your verdict is `intended` or `mistake`, the first finding is the root cause of your answer. After it, list the others from most to least important.

## Example

The example is about a system that is not yours.

```json
{
 "schema_version": "1",
 "verdict": "intended",
 "findings": [
  {
   "id": "F1",
   "kind": "file",
   "service": "report-service",
   "file": "src/ReportService/ExportHandler.h",
   "line": 212,
   "symbol": "ExportAll",
   "object_type": null,
   "object_name": null,
   "action": null,
   "what": "Loads the whole export into memory before writing it.",
   "evidence": [
    {
     "source": "source",
     "ref": "src/ReportService/ExportHandler.h:212",
     "quote": "std::vector<Row> rows = LoadAll(query);"
    }
   ]
  },
  {
   "id": "F2",
   "kind": "file",
   "service": "report-service",
   "file": "docker-compose.yml",
   "line": 88,
   "symbol": "mem_limit",
   "object_type": null,
   "object_name": null,
   "action": null,
   "what": "The container is limited to 256 MB.",
   "evidence": [
    {
     "source": "source",
     "ref": "docker-compose.yml:88",
     "quote": "mem_limit: 256m"
    },
    {
     "source": "log",
     "ref": "evidence/logs/report-service.log:14",
     "quote": "std::bad_alloc"
    }
   ]
  }
 ],
 "constraint": "F2",
 "decision_used": "D-031",
 "decision_quote": "Every container is limited to 256 MB of memory.",
 "if_intended": {
  "root_cause": "F1",
  "fix_class": "finish",
  "fix_what": "Write the export in pages so it fits in 256 MB."
 },
 "if_mistake": {
  "root_cause": "F2",
  "fix_class": "revert",
  "fix_what": "Remove the memory limit."
 },
 "open_question": null
}
```
