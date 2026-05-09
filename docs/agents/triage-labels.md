# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's GitHub issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## Label provisioning

As of setup, only `wontfix` exists in `ChoKhoOu/TinoHelm`. The other four will not exist in the repo until the first time a triage skill tries to apply them. When that happens, create the missing label before applying it, e.g.:

```bash
gh label create "ready-for-agent" --description "Fully specified, ready for an AFK agent to pick up" --color "0E8A16"
```

Suggested colors (keep distinct from the default GitHub palette already in use):

- `needs-triage` → yellow (`FBCA04`)
- `needs-info` → orange (`D93F0B`)
- `ready-for-agent` → green (`0E8A16`)
- `ready-for-human` → blue (`1D76DB`)

Edit the right-hand column of the table above if you later adopt different vocabulary.
