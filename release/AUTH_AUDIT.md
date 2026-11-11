# Company API authentication audit

Every request reaching `/api/v1/inventory` is classified before inventory or
feedback handling. Responses include a server-generated X-Request-ID. Audit
records contain UTC time, request ID, method, actual HTTP status, sanitized IP
and user agent, company ID/name when known, and the last 12 hexadecimal digits
of SHA-256(key). Raw keys and authorization headers are never recorded.

Results are accepted, missing_key, unknown_key, revoked_key and
suspended_company. A valid key may authenticate successfully while a subsequent
inventory/feedback operation fails: accepted is an authentication result, not an
import result. Missing/unrecognized keys always use Unknown company and no ID.
CORS preflight requests are recorded with their actual 204 status.

Rotation and revocation preserve hash-to-company bindings under inventory/keys/.
Old-key attempts can still identify their company but cannot access inventory.
Events are immutable private objects under inventory/auth-audit/, ordered newest
first by reverse time plus a UUID. No shared latest-event file can lose concurrent
requests. The admin-only auth-audit action returns 100 records and next_cursor;
company read keys cannot retrieve it. Both private Blob and Supabase are supported.

Persistence is awaited before ending the response. If audit storage fails, a
sanitized structured fallback is emitted to server logs; inventory availability
is preserved. Such fallback events are not automatically backfilled into History.
Storage lookup outages return 503 rather than falsely classifying a known key.
Existing historical requests cannot be reconstructed before audit deployment.
