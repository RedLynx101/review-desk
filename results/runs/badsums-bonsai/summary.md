# Run summary: badsums split (synthetic documents)

Model: `prism-ml/bonsai-27b`, quantization Q1_0, loaded context 8192. Temperature 0.0, strict JSON schema, images for scans: False.

## Documents

Model seconds include time queued behind other requests on a shared server.

| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |
|---|---|---|---|---|---|---|---|
| clean | 12 | 1 | 9 (75.0%) | 0 | 0 | 36 / 75 | 31.3909 |
| scanned | 12 | 0 | 2 (16.7%) | 0 | 0 | 0 / 75 | 42.9833 |
| degraded | 12 | 0 | 0 (0.0%) | 0 | 0 | 55 / 75 | 24.05 |
| all | 36 | 1 | 11 (30.6%) | 0 | 0 | 91 / 225 | 32.8486 |

Model failures during this run, counting every attempt, including ones a retry got past: truncated 1.

## By scan quality

Values that are actually printed, so an easy null can't flatter a bad scan.

| Quality | Documents | Printed values right | Printed values OCR could read |
|---|---|---|---|
| clean | 12 | 61.0% (245/402) | 100.0% |
| light | 12 | 93.3% (375/402) | 98.8% |
| moderate | 8 | 41.0% (78/190) | 69.0% |
| heavy | 4 | 0.5% (1/212) | 14.1% |

## Accuracy by field

| Field | Clean | Scanned | Degraded | All |
|---|---|---|---|---|
| document_type | 91.7% (11/12) | 100.0% (12/12) | 66.7% (8/12) | 86.1% (31/36) |
| vendor_name | 91.7% (11/12) | 75.0% (9/12) | 58.3% (7/12) | 75.0% (27/36) |
| document_number | 91.7% (11/12) | 100.0% (12/12) | 25.0% (3/12) | 72.2% (26/36) |
| issue_date | 91.7% (11/12) | 100.0% (12/12) | 41.7% (5/12) | 77.8% (28/36) |
| due_date | 91.7% (11/12) | 100.0% (12/12) | 58.3% (7/12) | 83.3% (30/36) |
| purchase_order | 100.0% (12/12) | 100.0% (12/12) | 91.7% (11/12) | 97.2% (35/36) |
| bill_to_name | 83.3% (10/12) | 91.7% (11/12) | 50.0% (6/12) | 75.0% (27/36) |
| currency | 91.7% (11/12) | 100.0% (12/12) | 41.7% (5/12) | 77.8% (28/36) |
| subtotal | 91.7% (11/12) | 100.0% (12/12) | 41.7% (5/12) | 77.8% (28/36) |
| discount | 100.0% (12/12) | 100.0% (12/12) | 91.7% (11/12) | 97.2% (35/36) |
| shipping | 100.0% (12/12) | 100.0% (12/12) | 100.0% (12/12) | 100.0% (36/36) |
| tax | 91.7% (11/12) | 100.0% (12/12) | 33.3% (4/12) | 75.0% (27/36) |
| total | 91.7% (11/12) | 91.7% (11/12) | 41.7% (5/12) | 75.0% (27/36) |
| item.description | 52.0% (39/75) | 92.5% (74/80) | 13.0% (10/77) | 53.0% (123/232) |
| item.quantity | 50.7% (38/75) | 86.2% (69/80) | 16.9% (13/77) | 51.7% (120/232) |
| item.unit_price | 50.7% (38/75) | 88.8% (71/80) | 15.6% (12/77) | 52.2% (121/232) |
| item.amount | 52.0% (39/75) | 86.2% (69/80) | 10.4% (8/77) | 50.0% (116/232) |

## Coverage against error, all values

Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.

| Threshold | Skip review | Coverage | Wrong among skipped | Error |
|---|---|---|---|---|
| 0.0 | 1396 / 1396 | 100.0% | 531 | 38.0% |
| 0.5 | 743 / 1396 | 53.2% | 58 | 7.8% |
| 0.6 | 695 / 1396 | 49.8% | 39 | 5.6% |
| 0.7 | 684 / 1396 | 49.0% | 36 | 5.3% |
| 0.75 | 669 / 1396 | 47.9% | 36 | 5.4% |
| 0.8 | 643 / 1396 | 46.1% | 25 | 3.9% |
| 0.85 | 632 / 1396 | 45.3% | 21 | 3.3% |
| 0.9 | 602 / 1396 | 43.1% | 12 | 2.0% |
| 0.92 | 602 / 1396 | 43.1% | 12 | 2.0% |
| 0.95 | 553 / 1396 | 39.6% | 10 | 1.8% |
| 0.97 | 405 / 1396 | 29.0% | 5 | 1.2% |
| 0.98 | 375 / 1396 | 26.9% | 4 | 1.1% |
| 0.99 | 112 / 1396 | 8.0% | 0 | 0.0% |

## OCR losses against model errors

Printed values whose exact characters survive in the extracted words, and how often the model got those right.

| Condition | Readable | Right when readable |
|---|---|---|
| clean | 100.0% (378/378) | 59.0% (223/378) |
| scanned | 98.9% (374/378) | 93.8% (351/374) |
| degraded | 39.4% (149/378) | 44.3% (66/149) |
| all | 79.5% (901/1134) | 71.0% (640/901) |

## Source highlight

Of right values that are printed, how often the highlight landed on the ink.

| Condition | Hit rate |
|---|---|
| clean | 99.6% (222/223) |
| scanned | 98.9% (347/351) |
| degraded | 98.5% (65/66) |
| all | 99.1% (634/640) |

## By layout

| Layout | Accuracy | Values |
|---|---|---|
| banner | 66.7% | 126 |
| boxed | 69.3% | 75 |
| card_slip | 82.7% | 75 |
| classic | 84.1% | 226 |
| ledger | 80.0% | 75 |
| pos_wide | 80.0% | 55 |
| sidebar | 40.5% | 558 |
| thermal | 71.4% | 206 |
