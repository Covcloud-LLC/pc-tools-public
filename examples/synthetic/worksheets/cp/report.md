# Worksheet comparison report

**Complete comparison: different.**

| Count | Value |
|---|---:|
| Established worksheet pairs | 3 |
| Pairs with changes | 3 |
| Changed identifiers / compared | 1 / 3 |
| Changed context fields | 3 |
| Unresolved worksheets, baseline / candidate | 0 / 0 |

Identifier categories (one identifier can count in several): added 0, removed 0, value 1, type 0, receiver 0.

## Changed worksheets

### Pair 0: baseline 0 -> candidate 2

reference `CPBuildingCovGrp1Cost:a`, Tag absent, interval `2026-01-01` to `2026-07-01`, routine `cp_cov_premium_rr`.

| Worksheet | Identifier | Before | After | Category |
|---|---|---|---|---|
| `Building Coverage Basic Group I` | `routine.RateBookEdition` | `"1"` | `"2"` | context |
| `Building Coverage Basic Group I` | `finalAmount` | `20` | `21` | value |

### Pair 1: baseline 1 -> candidate 0

reference `CPBuildingCovGrp1Cost:b`, Tag absent, interval `2026-01-01` to `2026-07-01`, routine `cp_cov_premium_rr`.

| Worksheet | Identifier | Before | After | Category |
|---|---|---|---|---|
| `Building Coverage Basic Group I` | `routine.RateBookEdition` | `"1"` | `"2"` | context |

### Pair 2: baseline 2 -> candidate 1

reference `CPBuildingCovGrp1Cost:a`, Tag absent, interval `2026-07-01` to `2027-01-01`, routine `cp_cov_premium_rr`.

| Worksheet | Identifier | Before | After | Category |
|---|---|---|---|---|
| `Building Coverage Basic Group I` | `routine.RateBookEdition` | `"1"` | `"2"` | context |

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
      "identifier_count": 3,
      "sha256": "edd0484966dd52bfc1ac6beb15c4f09aeb1984453641b6ce9709414d0e5783d3",
      "version": 2,
      "worksheet_count": 3
    },
    "candidate": {
      "format": "pc-worksheet-final-values",
      "identifier_count": 3,
      "sha256": "cb810df3ca3b6c09e4a401b16700287dcc0b75217292cabdda1680e9c5233fe5",
      "version": 2,
      "worksheet_count": 3
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
          "Description": "Building Coverage Basic Group I",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2026-07-01",
          "FixedId": "CPBuildingCovGrp1Cost:a"
        },
        "routine": {
          "RateBookCode": "cp_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "cp_cov_premium_rr",
          "RoutineVersion": "1"
        }
      },
      "candidate": {
        "index": 2,
        "metadata": {
          "Description": "Building Coverage Basic Group I",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2026-07-01",
          "FixedId": "CPBuildingCovGrp1Cost:a"
        },
        "routine": {
          "RateBookCode": "cp_synthetic",
          "RateBookEdition": "2",
          "RoutineCode": "cp_cov_premium_rr",
          "RoutineVersion": "1"
        }
      },
      "context_changes": [
        {
          "baseline": {
            "present": true,
            "value": "1"
          },
          "candidate": {
            "present": true,
            "value": "2"
          },
          "field": "RateBookEdition",
          "group": "routine"
        }
      ],
      "identifier_changes": [
        {
          "baseline": {
            "value": {
              "kind": "number",
              "type": "java.math.BigDecimal",
              "value": "20"
            }
          },
          "candidate": {
            "value": {
              "kind": "number",
              "type": "java.math.BigDecimal",
              "value": "21"
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
          "Description": "Building Coverage Basic Group I",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2026-07-01",
          "FixedId": "CPBuildingCovGrp1Cost:b"
        },
        "routine": {
          "RateBookCode": "cp_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "cp_cov_premium_rr",
          "RoutineVersion": "1"
        }
      },
      "candidate": {
        "index": 0,
        "metadata": {
          "Description": "Building Coverage Basic Group I",
          "EffectiveDate": "2026-01-01",
          "ExpirationDate": "2026-07-01",
          "FixedId": "CPBuildingCovGrp1Cost:b"
        },
        "routine": {
          "RateBookCode": "cp_synthetic",
          "RateBookEdition": "2",
          "RoutineCode": "cp_cov_premium_rr",
          "RoutineVersion": "1"
        }
      },
      "context_changes": [
        {
          "baseline": {
            "present": true,
            "value": "1"
          },
          "candidate": {
            "present": true,
            "value": "2"
          },
          "field": "RateBookEdition",
          "group": "routine"
        }
      ],
      "identifier_changes": [],
      "outcome": "different"
    },
    {
      "baseline": {
        "index": 2,
        "metadata": {
          "Description": "Building Coverage Basic Group I",
          "EffectiveDate": "2026-07-01",
          "ExpirationDate": "2027-01-01",
          "FixedId": "CPBuildingCovGrp1Cost:a"
        },
        "routine": {
          "RateBookCode": "cp_synthetic",
          "RateBookEdition": "1",
          "RoutineCode": "cp_cov_premium_rr",
          "RoutineVersion": "1"
        }
      },
      "candidate": {
        "index": 1,
        "metadata": {
          "Description": "Building Coverage Basic Group I",
          "EffectiveDate": "2026-07-01",
          "ExpirationDate": "2027-01-01",
          "FixedId": "CPBuildingCovGrp1Cost:a"
        },
        "routine": {
          "RateBookCode": "cp_synthetic",
          "RateBookEdition": "2",
          "RoutineCode": "cp_cov_premium_rr",
          "RoutineVersion": "1"
        }
      },
      "context_changes": [
        {
          "baseline": {
            "present": true,
            "value": "1"
          },
          "candidate": {
            "present": true,
            "value": "2"
          },
          "field": "RateBookEdition",
          "group": "routine"
        }
      ],
      "identifier_changes": [],
      "outcome": "different"
    }
  ],
  "policy": "exact-reference-tag-interval-routine-v1",
  "summary": {
    "changed_context_fields": 3,
    "changed_identifiers": 1,
    "changed_pairs": 3,
    "compared_identifiers": 3,
    "established_pairs": 3,
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
