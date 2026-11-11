# Version 0.8.2

- Reliable DRC fetch-status recording and automatic status refresh. Failed checks
  display unavailable rather than incorrectly saying inventory was never fetched.
- Change Rates uses the full page and supports scrolling through many companies.
- History includes private API authentication audits: company name/ID when known,
  a 12-character SHA-256 fingerprint, result, HTTP status, time, request ID, IP and
  user agent. Missing/unknown keys show Unknown company. Old key associations
  remain available after rotation/revocation. Raw keys are never logged.
- Authentication filters and Load older authentication provide paged review.
  Authentication accepted is separate from inventory delivery and import results.

Validation: 198 desktop tests and 37 backend tests passed. Tested 60 sample
companies in isolated data at three window sizes. No production company records,
inventory uploads or invitation emails were created during testing. Lost historical
fetch records and requests before audit deployment cannot be reconstructed.

Published on 2026-11-11 to the signed stable update channel and GitHub release v0.8.2.
Live signature, public download size and SHA-256 were verified.
