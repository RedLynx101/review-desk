# Run summary: dev split (synthetic documents)

Model: `granite-4.1-8b`, quantization Q4_K_M, loaded context 8192. Temperature 0.0, strict JSON schema, images for scans: False.

## Documents

Model seconds include time queued behind other requests on a shared server.

| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |
|---|---|---|---|---|---|---|---|
| clean | 15 | 0 | 6 (40.0%) | 2 | 0 | 0 / 101 | 37.5067 |
| scanned | 15 | 0 | 2 (13.3%) | 0 | 0 | 0 / 101 | 39.2133 |
| degraded | 15 | 0 | 0 (0.0%) | 0 | 0 | 43 / 101 | 28.7467 |
| all | 45 | 0 | 8 (17.8%) | 2 | 0 | 43 / 303 | 35.1556 |

## By scan quality

Values that are actually printed, so an easy null can't flatter a bad scan.

| Quality | Documents | Printed values right | Printed values OCR could read |
|---|---|---|---|
| clean | 15 | 96.6% (517/535) | 100.0% |
| light | 15 | 90.6% (485/535) | 97.4% |
| moderate | 9 | 51.0% (185/363) | 74.7% |
| heavy | 6 | 3.5% (6/172) | 12.2% |

## Accuracy by field

| Field | Clean | Scanned | Degraded | All |
|---|---|---|---|---|
| document_type | 100.0% (15/15) | 100.0% (15/15) | 93.3% (14/15) | 97.8% (44/45) |
| vendor_name | 100.0% (15/15) | 93.3% (14/15) | 33.3% (5/15) | 75.6% (34/45) |
| document_number | 93.3% (14/15) | 100.0% (15/15) | 20.0% (3/15) | 71.1% (32/45) |
| issue_date | 86.7% (13/15) | 86.7% (13/15) | 33.3% (5/15) | 68.9% (31/45) |
| due_date | 93.3% (14/15) | 80.0% (12/15) | 73.3% (11/15) | 82.2% (37/45) |
| purchase_order | 86.7% (13/15) | 80.0% (12/15) | 73.3% (11/15) | 80.0% (36/45) |
| bill_to_name | 100.0% (15/15) | 100.0% (15/15) | 60.0% (9/15) | 86.7% (39/45) |
| currency | 100.0% (15/15) | 100.0% (15/15) | 46.7% (7/15) | 82.2% (37/45) |
| subtotal | 100.0% (15/15) | 93.3% (14/15) | 26.7% (4/15) | 73.3% (33/45) |
| discount | 100.0% (15/15) | 93.3% (14/15) | 80.0% (12/15) | 91.1% (41/45) |
| shipping | 100.0% (15/15) | 86.7% (13/15) | 100.0% (15/15) | 95.6% (43/45) |
| tax | 100.0% (15/15) | 80.0% (12/15) | 33.3% (5/15) | 71.1% (32/45) |
| total | 100.0% (15/15) | 93.3% (14/15) | 33.3% (5/15) | 75.6% (34/45) |
| item.description | 100.0% (103/103) | 96.1% (99/103) | 31.1% (32/103) | 75.7% (234/309) |
| item.quantity | 84.5% (87/103) | 74.8% (77/103) | 40.8% (42/103) | 66.7% (206/309) |
| item.unit_price | 95.2% (98/103) | 88.3% (91/103) | 37.9% (39/103) | 73.8% (228/309) |
| item.amount | 100.0% (103/103) | 98.1% (101/103) | 34.9% (36/103) | 77.7% (240/309) |

## Coverage against error, all values

Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.

| Threshold | Skip review | Coverage | Wrong among skipped | Error |
|---|---|---|---|---|
| 0.0 | 1821 / 1821 | 100.0% | 440 | 24.2% |
| 0.5 | 1271 / 1821 | 69.8% | 81 | 6.4% |
| 0.6 | 1209 / 1821 | 66.4% | 43 | 3.6% |
| 0.7 | 1193 / 1821 | 65.5% | 40 | 3.4% |
| 0.75 | 1186 / 1821 | 65.1% | 40 | 3.4% |
| 0.8 | 1144 / 1821 | 62.8% | 24 | 2.1% |
| 0.85 | 1129 / 1821 | 62.0% | 20 | 1.8% |
| 0.9 | 1068 / 1821 | 58.7% | 5 | 0.5% |
| 0.92 | 1068 / 1821 | 58.7% | 5 | 0.5% |
| 0.95 | 1009 / 1821 | 55.4% | 4 | 0.4% |
| 0.97 | 771 / 1821 | 42.3% | 2 | 0.3% |
| 0.98 | 723 / 1821 | 39.7% | 1 | 0.1% |
| 0.99 | 264 / 1821 | 14.5% | 1 | 0.4% |

## OCR losses against model errors

Printed values whose exact characters survive in the extracted words, and how often the model got those right.

| Condition | Readable | Right when readable |
|---|---|---|
| clean | 100.0% (505/505) | 96.4% (487/505) |
| scanned | 97.6% (493/505) | 92.1% (454/493) |
| degraded | 55.0% (278/505) | 61.2% (170/278) |
| all | 84.2% (1276/1515) | 87.1% (1111/1276) |

## Source highlight

Of right values that are printed, how often the highlight landed on the ink.

| Condition | Hit rate |
|---|---|
| clean | 99.4% (484/487) |
| scanned | 99.1% (451/455) |
| degraded | 96.5% (164/170) |
| all | 98.8% (1099/1112) |

## By layout

| Layout | Accuracy | Values |
|---|---|---|
| banner | 73.8% | 210 |
| boxed | 61.1% | 198 |
| card_slip | 82.7% | 75 |
| classic | 67.7% | 198 |
| ledger | 67.1% | 310 |
| pos_wide | 72.3% | 130 |
| sidebar | 90.7% | 486 |
| thermal | 77.6% | 214 |
