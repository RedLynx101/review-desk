# Run summary: heldout split (synthetic documents)

Model: `prism-ml/bonsai-27b`, quantization Q1_0, loaded context 8192. Temperature 0.0, strict JSON schema, images for scans: False.

## Documents

Model seconds include time queued behind other requests on a shared server.

| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |
|---|---|---|---|---|---|---|---|
| clean | 30 | 0 | 22 (73.3%) | 15 | 1 | 0 / 218 | 64.0633 |
| scanned | 30 | 0 | 12 (40.0%) | 2 | 1 | 0 / 218 | 64.4367 |
| degraded | 30 | 1 | 0 (0.0%) | 0 | 0 | 152 / 218 | 37.5069 |
| all | 90 | 1 | 34 (37.8%) | 17 | 2 | 152 / 654 | 55.536 |

Model failures during this run, counting every attempt, including ones a retry got past: truncated 1.

## By scan quality

Values that are actually printed, so an easy null can't flatter a bad scan.

| Quality | Documents | Printed values right | Printed values OCR could read |
|---|---|---|---|
| clean | 30 | 98.8% (1126/1140) | 100.0% |
| light | 30 | 96.0% (1094/1140) | 99.0% |
| moderate | 12 | 51.5% (203/394) | 80.2% |
| heavy | 18 | 1.9% (14/746) | 15.7% |

## Accuracy by field

| Field | Clean | Scanned | Degraded | All |
|---|---|---|---|---|
| document_type | 100.0% (30/30) | 100.0% (30/30) | 66.7% (20/30) | 88.9% (80/90) |
| vendor_name | 96.7% (29/30) | 86.7% (26/30) | 40.0% (12/30) | 74.4% (67/90) |
| document_number | 100.0% (30/30) | 86.7% (26/30) | 30.0% (9/30) | 72.2% (65/90) |
| issue_date | 100.0% (30/30) | 93.3% (28/30) | 23.3% (7/30) | 72.2% (65/90) |
| due_date | 96.7% (29/30) | 96.7% (29/30) | 56.7% (17/30) | 83.3% (75/90) |
| purchase_order | 96.7% (29/30) | 93.3% (28/30) | 73.3% (22/30) | 87.8% (79/90) |
| bill_to_name | 96.7% (29/30) | 96.7% (29/30) | 53.3% (16/30) | 82.2% (74/90) |
| currency | 100.0% (30/30) | 100.0% (30/30) | 46.7% (14/30) | 82.2% (74/90) |
| subtotal | 100.0% (30/30) | 100.0% (30/30) | 23.3% (7/30) | 74.4% (67/90) |
| discount | 100.0% (30/30) | 100.0% (30/30) | 93.3% (28/30) | 97.8% (88/90) |
| shipping | 100.0% (30/30) | 100.0% (30/30) | 73.3% (22/30) | 91.1% (82/90) |
| tax | 96.7% (29/30) | 96.7% (29/30) | 20.0% (6/30) | 71.1% (64/90) |
| total | 100.0% (30/30) | 96.7% (29/30) | 20.0% (6/30) | 72.2% (65/90) |
| item.description | 99.5% (217/218) | 99.5% (217/218) | 18.1% (41/226) | 71.8% (475/662) |
| item.quantity | 98.2% (214/218) | 93.6% (204/218) | 18.6% (42/226) | 69.5% (460/662) |
| item.unit_price | 98.2% (214/218) | 94.0% (205/218) | 16.4% (37/226) | 68.9% (456/662) |
| item.amount | 99.5% (217/218) | 98.2% (214/218) | 19.5% (44/226) | 71.8% (475/662) |

## Coverage against error, all values

Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.

| Threshold | Skip review | Coverage | Wrong among skipped | Error |
|---|---|---|---|---|
| 0.0 | 3818 / 3818 | 100.0% | 1007 | 26.4% |
| 0.5 | 2752 / 3818 | 72.1% | 131 | 4.8% |
| 0.6 | 2648 / 3818 | 69.4% | 83 | 3.1% |
| 0.7 | 2622 / 3818 | 68.7% | 82 | 3.1% |
| 0.75 | 2596 / 3818 | 68.0% | 82 | 3.2% |
| 0.8 | 2522 / 3818 | 66.1% | 48 | 1.9% |
| 0.85 | 2496 / 3818 | 65.4% | 38 | 1.5% |
| 0.9 | 2432 / 3818 | 63.7% | 18 | 0.7% |
| 0.92 | 2432 / 3818 | 63.7% | 18 | 0.7% |
| 0.95 | 2318 / 3818 | 60.7% | 12 | 0.5% |
| 0.97 | 1777 / 3818 | 46.5% | 3 | 0.2% |
| 0.98 | 1691 / 3818 | 44.3% | 2 | 0.1% |
| 0.99 | 689 / 3818 | 18.1% | 1 | 0.1% |

## OCR losses against model errors

Printed values whose exact characters survive in the extracted words, and how often the model got those right.

| Condition | Readable | Right when readable |
|---|---|---|
| clean | 100.0% (1080/1080) | 98.7% (1066/1080) |
| scanned | 99.0% (1069/1080) | 96.6% (1033/1069) |
| degraded | 38.4% (415/1080) | 43.6% (181/415) |
| all | 79.1% (2564/3240) | 88.9% (2280/2564) |

## Source highlight

Of right values that are printed, how often the highlight landed on the ink.

| Condition | Hit rate |
|---|---|
| clean | 99.6% (1062/1066) |
| scanned | 99.0% (1024/1034) |
| degraded | 95.6% (175/183) |
| all | 99.0% (2261/2283) |

## By layout

| Layout | Accuracy | Values |
|---|---|---|
| banner | 67.4% | 301 |
| boxed | 66.4% | 217 |
| card_slip | 79.1% | 182 |
| classic | 75.4% | 721 |
| kiosk | 78.7% | 174 |
| ledger | 71.2% | 625 |
| letterhead | 76.0% | 613 |
| pos_wide | 75.8% | 277 |
| sidebar | 71.4% | 357 |
| summary_first | 67.3% | 162 |
| thermal | 81.5% | 189 |
