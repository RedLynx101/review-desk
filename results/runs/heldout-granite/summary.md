# Run summary: heldout split (synthetic documents)

Model: `granite-4.1-8b`, quantization Q4_K_M, loaded context 8192. Temperature 0.0, strict JSON schema, images for scans: False.

## Documents

Model seconds include time queued behind other requests on a shared server.

| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |
|---|---|---|---|---|---|---|---|
| clean | 30 | 0 | 8 (26.7%) | 6 | 1 | 0 / 218 | 33.07 |
| scanned | 30 | 0 | 7 (23.3%) | 0 | 0 | 0 / 218 | 33.03 |
| degraded | 30 | 0 | 0 (0.0%) | 0 | 0 | 156 / 218 | 19.0967 |
| all | 90 | 0 | 15 (16.7%) | 6 | 1 | 156 / 654 | 28.3989 |

## By scan quality

Values that are actually printed, so an easy null can't flatter a bad scan.

| Quality | Documents | Printed values right | Printed values OCR could read |
|---|---|---|---|
| clean | 30 | 97.1% (1107/1140) | 100.0% |
| light | 30 | 93.9% (1070/1140) | 99.0% |
| moderate | 12 | 53.5% (211/394) | 80.2% |
| heavy | 18 | 2.5% (19/746) | 15.7% |

## Accuracy by field

| Field | Clean | Scanned | Degraded | All |
|---|---|---|---|---|
| document_type | 100.0% (30/30) | 100.0% (30/30) | 80.0% (24/30) | 93.3% (84/90) |
| vendor_name | 100.0% (30/30) | 90.0% (27/30) | 43.3% (13/30) | 77.8% (70/90) |
| document_number | 73.3% (22/30) | 70.0% (21/30) | 23.3% (7/30) | 55.6% (50/90) |
| issue_date | 90.0% (27/30) | 83.3% (25/30) | 30.0% (9/30) | 67.8% (61/90) |
| due_date | 93.3% (28/30) | 90.0% (27/30) | 53.3% (16/30) | 78.9% (71/90) |
| purchase_order | 83.3% (25/30) | 83.3% (25/30) | 73.3% (22/30) | 80.0% (72/90) |
| bill_to_name | 96.7% (29/30) | 96.7% (29/30) | 53.3% (16/30) | 82.2% (74/90) |
| currency | 100.0% (30/30) | 96.7% (29/30) | 43.3% (13/30) | 80.0% (72/90) |
| subtotal | 100.0% (30/30) | 100.0% (30/30) | 23.3% (7/30) | 74.4% (67/90) |
| discount | 100.0% (30/30) | 100.0% (30/30) | 93.3% (28/30) | 97.8% (88/90) |
| shipping | 100.0% (30/30) | 96.7% (29/30) | 70.0% (21/30) | 88.9% (80/90) |
| tax | 100.0% (30/30) | 100.0% (30/30) | 30.0% (9/30) | 76.7% (69/90) |
| total | 100.0% (30/30) | 96.7% (29/30) | 23.3% (7/30) | 73.3% (66/90) |
| item.description | 98.2% (216/220) | 98.2% (215/219) | 14.5% (33/228) | 69.6% (464/667) |
| item.quantity | 92.7% (204/220) | 84.9% (186/219) | 22.4% (51/228) | 66.1% (441/667) |
| item.unit_price | 96.4% (212/220) | 93.6% (205/219) | 21.5% (49/228) | 69.9% (466/667) |
| item.amount | 98.2% (216/220) | 98.6% (216/219) | 19.3% (44/228) | 71.4% (476/667) |

## Coverage against error, all values

Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.

| Threshold | Skip review | Coverage | Wrong among skipped | Error |
|---|---|---|---|---|
| 0.0 | 3838 / 3838 | 100.0% | 1067 | 27.8% |
| 0.5 | 2695 / 3838 | 70.2% | 138 | 5.1% |
| 0.6 | 2598 / 3838 | 67.7% | 94 | 3.6% |
| 0.7 | 2566 / 3838 | 66.9% | 92 | 3.6% |
| 0.75 | 2533 / 3838 | 66.0% | 87 | 3.4% |
| 0.8 | 2451 / 3838 | 63.9% | 51 | 2.1% |
| 0.85 | 2425 / 3838 | 63.2% | 41 | 1.7% |
| 0.9 | 2317 / 3838 | 60.4% | 24 | 1.0% |
| 0.92 | 2314 / 3838 | 60.3% | 24 | 1.0% |
| 0.95 | 2207 / 3838 | 57.5% | 19 | 0.9% |
| 0.97 | 1685 / 3838 | 43.9% | 11 | 0.7% |
| 0.98 | 1602 / 3838 | 41.7% | 10 | 0.6% |
| 0.99 | 642 / 3838 | 16.7% | 0 | 0.0% |

## OCR losses against model errors

Printed values whose exact characters survive in the extracted words, and how often the model got those right.

| Condition | Readable | Right when readable |
|---|---|---|
| clean | 100.0% (1080/1080) | 96.9% (1047/1080) |
| scanned | 99.0% (1069/1080) | 94.6% (1011/1069) |
| degraded | 38.4% (415/1080) | 46.3% (192/415) |
| all | 79.1% (2564/3240) | 87.8% (2250/2564) |

## Source highlight

Of right values that are printed, how often the highlight landed on the ink.

| Condition | Hit rate |
|---|---|
| clean | 99.5% (1042/1047) |
| scanned | 99.2% (1003/1011) |
| degraded | 92.2% (178/193) |
| all | 98.8% (2223/2251) |

## By layout

| Layout | Accuracy | Values |
|---|---|---|
| banner | 65.7% | 297 |
| boxed | 70.4% | 213 |
| card_slip | 79.0% | 186 |
| classic | 73.4% | 717 |
| kiosk | 75.3% | 178 |
| ledger | 71.0% | 625 |
| letterhead | 71.4% | 609 |
| pos_wide | 74.0% | 277 |
| sidebar | 73.4% | 369 |
| summary_first | 66.0% | 162 |
| thermal | 76.6% | 205 |
