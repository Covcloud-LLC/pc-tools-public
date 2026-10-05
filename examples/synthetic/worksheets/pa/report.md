# Worksheet comparison report

**Complete comparison: different.**

| Count | Value |
|---|---:|
| Established worksheet pairs | 2 |
| Pairs with changes | 1 |
| Changed identifiers / compared | 1 / 2 |
| Changed context fields | 0 |
| Unresolved worksheets, baseline / candidate | 0 / 0 |

Identifier categories (one identifier can count in several): added 0, removed 0, value 1, type 0, receiver 0.

## Changed worksheets

### Pair 0: baseline 0 -> candidate 1

reference `PersonalVehicle:a`, Tag `driver:a`, interval `2026-01-01` to `2027-01-01`, routine `pa_assign_driver_style2_rr`.

| Worksheet | Identifier | Before | After | Category |
|---|---|---|---|---|
| `Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver` | `finalAmount` | `10` | `11` | value |

## Equal worksheets

- Pair 1: baseline 1 -> candidate 0, `Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver`.

## Unresolved worksheets

None.

Indexes are zero-based positions in each input. Pair numbers are positions in `pairs`. Unresolved worksheets are not additions or removals. No business impact is inferred.

## Appendix: comparison JSON

The exact comparison result, including structured identifiers and input hashes.

```json
{
  "complete": true,
  "format": "pc-worksheet-comparison",
  "inputs": {
    "baseline": {
      "format": "pc-worksheet-final-values",
      "identifier_count": 2,
      "sha256": "3962798ac2143cefbd455bd43a132a65f62c340639e7776757e6fbd1d3f2acff",
      "version": 2,
      "worksheet_count": 2
    },
    "candidate": {
      "format": "pc-worksheet-final-values",
      "identifier_count": 2,
      "sha256": "600380ccee24ccf26b8005d14ecbf41c3ceeb6d54f47846ee4f66273b9e6d55e",
      "version": 2,
      "worksheet_count": 2
    }
  },
  "limits": [
    "Caller assumes different retained runs of the same job and quote branch; inputs do not attest this.",
    "Only exact qualified reference, Tag presence/value, raw interval and nonempty RoutineCode establish correspondence.",
    "Unresolved worksheets are not inferred additions/removals, replacements, splits or merges.",
    "Opaque values and receivers describe recorded text, not underlying object equality.",
    "Category counts overlap; changed_identifiers counts each changed identity once within established pairs."
  ],
  "outcome": "different",
  "pairs": [
    {
      "baseline": {
        "index": 0,
        "metadata": {
          "Description": "Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2027-01-01",
          "FixedId": "PersonalVehicle:a",
          "Tag": "driver:a"
        },
        "routine": {
          "RateBookCode": "pa_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "pa_assign_driver_style2_rr",
          "RoutineVersion": "1"
        }
      },
      "candidate": {
        "index": 1,
        "metadata": {
          "Description": "Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2027-01-01",
          "FixedId": "PersonalVehicle:a",
          "Tag": "driver:a"
        },
        "routine": {
          "RateBookCode": "pa_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "pa_assign_driver_style2_rr",
          "RoutineVersion": "1"
        }
      },
      "context_changes": [],
      "identifier_changes": [
        {
          "baseline": {
            "value": {
              "kind": "number",
              "type": "java.math.BigDecimal",
              "value": "10"
            }
          },
          "candidate": {
            "value": {
              "kind": "number",
              "type": "java.math.BigDecimal",
              "value": "11"
            }
          },
          "categories": [
            "value"
          ],
          "identifier": {
            "kind": "variable",
            "name": "finalAmount"
          }
        }
      ],
      "outcome": "different"
    },
    {
      "baseline": {
        "index": 1,
        "metadata": {
          "Description": "Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2027-01-01",
          "FixedId": "PersonalVehicle:a",
          "Tag": "driver:b"
        },
        "routine": {
          "RateBookCode": "pa_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "pa_assign_driver_style2_rr",
          "RoutineVersion": "1"
        }
      },
      "candidate": {
        "index": 0,
        "metadata": {
          "Description": "Driver assignment for vehicle Synthetic Vehicle, driver Synthetic Driver",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2027-01-01",
          "FixedId": "PersonalVehicle:a",
          "Tag": "driver:b"
        },
        "routine": {
          "RateBookCode": "pa_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "pa_assign_driver_style2_rr",
          "RoutineVersion": "1"
        }
      },
      "outcome": "equal"
    }
  ],
  "policy": "exact-reference-tag-interval-routine-v1",
  "summary": {
    "changed_context_fields": 0,
    "changed_identifiers": 1,
    "changed_pairs": 1,
    "compared_identifiers": 2,
    "established_pairs": 2,
    "identifier_categories": {
      "added": 0,
      "receiver": 0,
      "removed": 0,
      "type": 0,
      "value": 1
    },
    "unresolved_baseline": 0,
    "unresolved_candidate": 0
  },
  "unresolved": [],
  "version": 2
}
```
