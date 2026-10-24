# DRC import mapping

The endpoint is `https://connect.drc.diamonds/v1/inventory/push`, using POST, `x-api-key`, and `application/json` with a `diamonds` array.

The six exact JSON keys supplied in the curl example are `StockId`, `Shape`, `Weight`, `Color`, `Clarity`, and `PricePerCarat`. For all other properties the app emits an accepted source header from the supplied DRC header list, rather than guessing a PascalCase API name from the database schema. For example, it sends `Certificate #`, `Diamond Type`, `Image 1` and `Image 2`. Each mapped value is serialized as a string, matching the sample and header-based import format. Blank optional values are omitted. Additional explicitly configured keys remain supported.

The user-confirmed field catalogue includes basics, lot number, fancy colour, pricing and cost, lab and certificates, video and six separate image links, dimensions and proportions, inclusions, location, status, comments, growth type, star length, treatment, origin/polishing country and category. Its exact outgoing keys and accepted Excel header aliases are defined in `drc_sync/contract.py`. The database schema is retained separately as `docs/stone-schema-reference.prisma`; database IDs, supplier relations, audit timestamps and unrelated internal fields are not automatically transmitted.

## Configure

Choose Add Excel file, pick its worksheet and Natural or Lab Grown profile. The dropdown for every column offers all import fields. Header matching ignores case and common punctuation while distinguishing percentages from plain names. If two columns suggest the same outgoing field, only the first is selected automatically; choose the intended source manually. Unrecognized columns are skipped until mapped explicitly. Nothing is inferred from cell positions.

For existing profiles choose Auto-map saved files to add recognized columns without overwriting custom mappings. Then use Preview API payload to validate the entire workbook and inspect the first ten outgoing records locally. Save profiles when ready. New profiles require StockId, Shape, Weight and PricePerCarat as a local completeness policy. Color and Clarity may be blank, matching their optional status in the supplied schema. Existing profiles retain their existing required-field list. Adjust required/numeric rules in the advanced editor if DRC documents other requirements.

Both profiles include `Diamond Type`: `NATURAL` or `LAB_GROWN`, based on the selected profile and values in the supplied schema. If an Excel Diamond Type column is mapped, it must agree with that profile; conflicting or unknown values block the workbook. Growth Type is read independently from its own column. Images remain six individual import fields; they are not combined into the database's internal JSON array.

`Weight` and `PricePerCarat` must be positive finite numbers and are emitted as strings. Other decimal fields must be finite; discounts may be negative or zero. Stock and certificate identifiers stored as Excel text preserve leading zeroes. Numeric Excel cells cannot preserve formatting-only zeroes; use Text cells for identifiers.

## Existing installation migration

The original repeated `/v1/v1` endpoint is corrected on startup. Original generic mappings (`stock_id`, `carat`, etc.) change to the curl field names. File paths, sheets, inventory kinds and credentials are preserved. Pending/retry/review jobs in the old format are superseded, with their old payloads removed; fresh payloads are regenerated from the workbook. Confirmation and retry-safety flags are reset for migrated profiles so a changed request does not immediately upload without contract review. Completed history is preserved.

## Still to verify before live use

The accepted field list does not provide the response structure, partial-failure fields, full replacement/deletion semantics, retry safety or server size limits. Configure `success_field`, `success_value` and `confirmed` only after verifying those details. The app never treats HTTP 200 alone as a complete import. Tests are mocked; development has not sent any inventory to DRC.

