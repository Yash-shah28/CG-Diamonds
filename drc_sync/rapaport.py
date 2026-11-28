"""Prepare Natural inventory and submit it to Rapaport Trade."""
import csv
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from io import StringIO
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from openpyxl import load_workbook

from .contract import FORMAT, suggested_field
from .core import WorkbookValidationError, parse_excel
from .inventory_excel import workbook_stamp
from .responses import redact_text


TOKEN_URL = 'https://authztoken.api.rapaport.com/api/get'
LOTS_URL = 'https://technet.rapnetapis.com/diamondupdateingest/api/public/lots'
SINGLE_UPLOAD_LIMIT = 5_000_000
FILE_NAME = 'Natural-Inventory.csv'
CSV_FIELDS = {
    'StockId': 'Stock #', 'Status': 'Availability', 'Shape': 'Shape', 'Weight': 'Weight',
    'Color': 'Color', 'Clarity': 'Clarity', 'Cut': 'Cut', 'Polish': 'Polish',
    'Symmetry': 'Symmetry', 'Fluorescence': 'Fluorescence Intensity',
    'Fluorescence Color': 'Fluorescence Color', 'Measurements': 'Measurements',
    'Lab': 'Lab', 'Certificate #': 'Certificate #', 'PricePerCarat': 'PricePerCarat',
    'Discount': 'RapnetDiscountPercent', 'Depth %': 'Depth %', 'Table': 'Table %',
    'Fancy Color': 'Fancy Color', 'Fancy Intensity': 'Fancy Color Intensity',
    'Overtone': 'Fancy Color Overtone', 'Treatment': 'Treatment', 'Girdle Thin': 'Girdle Thin',
    'Girdle Thick': 'Girdle Thick', 'Girdle %': 'Girdle %', 'Girdle': 'Girdle Condition',
    'Culet': 'Culet Size', 'Culet Condition': 'Culet Condition', 'Crown Height': 'Crown Height',
    'Crown Angle': 'Crown Angle', 'Pavilion Depth': 'Pavilion Depth', 'Pavilion Angle': 'Pavilion Angle',
    'Eye Clean': 'Eye Clean', 'Laser Inscription': 'Laser Inscription',
    'Certificate Comment': 'Cert comment', 'Comment': 'Supplier Comment',
    'Star Length': 'Star Length', 'Tinge': 'Shade', 'Black Inclusion': 'Black Inclusion',
    'Milky': 'Milky', 'Key to Symbols': 'Key To Symbols', 'Country': 'Country',
    'State': 'State', 'City': 'City', 'Image': 'Diamond Image', 'Video': 'Video Link',
}


class RapaportError(ValueError):
    """A safe, user-facing Rapaport error."""

    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class NaturalUpload:
    path: Path
    stamp: tuple
    content: bytes
    records: int


def prepare_natural_upload(profile):
    if profile.get('kind') != 'Natural':
        raise WorkbookValidationError('Choose the Natural workbook in Excel Mapping.')
    path = Path(profile['path']).resolve()
    stamp = workbook_stamp(path)
    # Keep the source file, mapping, and original prices used by the inventory pipeline.
    mapping = profile['mapping'].copy()
    if 'Diamond Type' not in mapping.values():
        workbook = load_workbook(path, read_only=True)
        try:
            if profile['sheet'] not in workbook.sheetnames:
                raise WorkbookValidationError('Choose the correct Natural worksheet in Excel Mapping.')
            worksheet = workbook[profile['sheet']]
            headers = next(worksheet.values, ())
            type_column = next((str(header).strip() for header in headers if suggested_field(header) == 'Diamond Type'), None)
            if type_column:
                mapping[type_column] = 'Diamond Type'
        finally:
            workbook.close()
    profile = {**profile, 'path': str(path), 'mapping': mapping, 'request_format': FORMAT, 'identity': 'StockId'}
    stones = parse_excel(profile, pull_feed=True)['diamonds']
    output = StringIO(newline='')
    writer = csv.DictWriter(output, list(CSV_FIELDS.values()))
    writer.writeheader()
    for stone in stones:
        stock = stone['StockId']
        if stone.get('Diamond Type') != 'NATURAL' or str(stone.get('Growth Type', '')).strip().upper() in ('CVD', 'HPHT', 'LGD'):
            raise WorkbookValidationError(f'Stock {stock} is marked lab-grown. Check the Natural workbook.')
        if not (stone.get('Color') or stone.get('Fancy Color')):
            raise WorkbookValidationError(f'Stock {stock} needs a Color or Fancy Color for Rapaport.')
        if not stone.get('Fancy Color') and not stone.get('Clarity'):
            raise WorkbookValidationError(f'Stock {stock} needs a Clarity for Rapaport.')
        if not Decimal('0.01') <= Decimal(stone['Weight']) <= Decimal('99'):
            raise WorkbookValidationError(f'Stock {stock} needs a weight from 0.01 to 99 carats for Rapaport.')

        price = Decimal(stone['PricePerCarat']).to_integral_value(rounding=ROUND_HALF_UP)
        if price <= 0:
            raise WorkbookValidationError(f'Stock {stock} needs a price per carat that rounds to at least one dollar for Rapaport.')
        values = {header: stone[field] for field, header in CSV_FIELDS.items() if stone.get(field) not in (None, '')}
        values['PricePerCarat'] = format(price, 'f')
        if stone.get('Discount'):
            # The feed uses percentage points. Rapaport's CSV uses a fraction.
            values['RapnetDiscountPercent'] = format(Decimal(stone['Discount']).scaleb(-2), 'f')
        for field, precision in (('Depth %', '0.1'), ('Table', '1'), ('Crown Height', '0.1')):
            if stone.get(field):
                values[CSV_FIELDS[field]] = format(Decimal(stone[field]).quantize(Decimal(precision), rounding=ROUND_DOWN), 'f')
        writer.writerow(values)

    if workbook_stamp(path) != stamp:
        raise WorkbookValidationError('The Natural workbook changed while preparing the upload. Start again.')
    return NaturalUpload(path, stamp, output.getvalue().encode('utf-8'), len(stones))


def upload_id(value):
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise RapaportError('Rapaport did not return a valid upload ID.') from None


def _http_error(response, action, secrets=(), *, uncertain=False):
    message = f'Rapaport {action} failed (HTTP {response.status_code}).'
    for header in ('x-request-id', 'x-correlation-id', 'traceparent', 'cf-ray'):
        value = response.headers.get(header, '')
        if re.fullmatch(r'[A-Za-z0-9_.:-]{1,90}', value):
            message += f' Request ID {redact_text(value, secrets)}.'
            break
    if response.headers.get('Content-Type', '').split(';')[0] in ('application/json', 'application/problem+json'):
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = body.get('message') or body.get('detail') or body.get('title')
            if isinstance(detail, str):
                detail = re.sub(r'https?://\S+', '[URL]', detail)
                detail = re.sub(r'eyJ[A-Za-z0-9_.-]+', '[redacted]', detail)
                message += ' ' + redact_text(' '.join(detail.split()), secrets)[:240]
    if uncertain:
        message += ' Check Rapaport Trade Upload History before uploading again.'
    return RapaportError(message, uncertain=uncertain)


def _json_body(response, action):
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise RapaportError(f'Rapaport returned an unreadable {action} response.')
    return body


class RapaportClient:
    def __init__(self):
        self.clear_token()

    def clear_token(self):
        self._credentials = None
        self._token = ''
        self._expires_at = 0

    def login(self, http, credentials):
        if self._credentials == credentials and self._expires_at > monotonic():
            return self._token
        self.clear_token()
        try:
            response = http.post(TOKEN_URL, headers={'Accept': 'application/json'}, json=credentials)
        except httpx.RequestError:
            raise RapaportError('Could not reach Rapaport to request a login token. Check your connection.') from None
        if response.status_code in (400, 401, 403):
            raise RapaportError('Rapaport rejected the credentials. Check the Client ID, Client Secret and account API access.')
        if not response.is_success:
            raise _http_error(response, 'login', tuple(credentials.values()))
        body = _json_body(response, 'login')
        token = body.get('access_token')
        if not isinstance(token, str) or not token or any(ch.isspace() for ch in token):
            raise RapaportError('Rapaport did not return a valid login token.')
        expires_in = body.get('expires_in')
        if type(expires_in) in (int, float) and 60 < expires_in <= 86400:
            self._credentials = credentials.copy()
            self._token = token
            self._expires_at = monotonic() + expires_in - 60
        return token

    def _headers(self, http, credentials):
        return {'Accept': 'application/json', 'Authorization': 'Bearer ' + self.login(http, credentials)}

    def _request(self, http, method, url, action, *, submitted=False, **kwargs):
        try:
            response = http.request(method, url, **kwargs)
        except httpx.RequestError:
            if submitted:
                raise RapaportError('Upload result is unknown. Check Rapaport Trade Upload History before uploading again.',
                                    uncertain=True) from None
            raise RapaportError(f'Could not reach Rapaport for {action}. Check your connection and try again.') from None
        if response.status_code in (401, 403):
            self.clear_token()
            raise RapaportError(f'Rapaport denied {action}. Check that your account has diamond upload access.')
        if not response.is_success:
            authorization = kwargs.get('headers', {}).get('Authorization', '')
            credentials = tuple((self._credentials or {}).values())
            raise _http_error(response, action, (*credentials, authorization.removeprefix('Bearer ')),
                              uncertain=submitted and response.status_code >= 500)
        return response

    def upload(self, http, credentials, prepared):
        headers = self._headers(http, credentials)
        if prepared.stamp != workbook_stamp(prepared.path):
            raise RapaportError('The Natural workbook changed after review. Prepare the upload again.')
        if len(prepared.content) > SINGLE_UPLOAD_LIMIT:
            result = self._multipart(http, headers, prepared.content)
        else:
            result = self._request(
                http, 'POST', LOTS_URL, 'upload', submitted=True, headers=headers,
                data={'DiamondFileFormat': 'Rapnet', 'ReplaceAll': 'false', 'FirstRowHeaders': 'true', 'SendEmail': 'false'},
                files={'File': (FILE_NAME, prepared.content, 'text/csv')},
            )
        try:
            return upload_id(_json_body(result, 'upload').get('uploadId'))
        except RapaportError:
            raise RapaportError('Rapaport did not confirm the upload ID. Check Rapaport Trade Upload History before uploading again.',
                                uncertain=True) from None

    def _multipart(self, http, headers, content):
        url = LOTS_URL + '/multipart'
        init = self._request(http, 'POST', url, 'multipart preparation', headers=headers, json={
            'fileName': FILE_NAME, 'replaceAll': False, 'sendEmail': False,
            'diamondFileFormat': 'Rapnet', 'fileSize': len(content),
        })
        body = _json_body(init, 'multipart preparation')
        upload = upload_id(body.get('uploadId'))
        parts = _multipart_urls(body, len(content))
        etags = {}
        for number, (signed_url, start, end) in enumerate(parts, 1):
            response = self._request(
                http, 'PUT', signed_url, f'upload part {number}',
                content=content[start:end], headers={'Content-Type': 'text/csv'},
            )
            tag = response.headers.get('ETag', '').strip('"')
            if not tag:
                raise RapaportError(f'Rapaport did not acknowledge upload part {number}. The upload was not finalized.')
            etags[str(number)] = tag
        return self._request(http, 'PUT', url, 'multipart completion', submitted=True, headers=headers, json={
            'uploadId': upload, 'multipartUploadId': body['multipartUploadId'], 'eTags': etags,
        })

    def status(self, http, credentials, uid):
        uid = upload_id(uid)
        result = self._request(http, 'GET', LOTS_URL + '/status/' + uid, 'upload status',
                               headers=self._headers(http, credentials))
        body = _json_body(result, 'upload status')
        if not isinstance(body.get('status'), str) or not body['status'].strip():
            raise RapaportError('Rapaport did not return an upload status yet. Check again shortly.')
        return body


def _multipart_urls(body, size):
    urls = body.get('putUrls')
    split = body.get('multipartSplitSize')
    token = body.get('multipartUploadId')
    invalid = 'Rapaport returned incomplete multipart upload instructions.'
    if type(split) is not int or split <= 0 or not isinstance(urls, list) or not isinstance(token, str) or not token:
        raise RapaportError(invalid)
    order = {}
    for url in urls:
        try:
            if not isinstance(url, str):
                raise ValueError
            parsed = urlsplit(url)
            host = parsed.hostname or ''
            part, = parse_qs(parsed.query).get('partNumber', [])
            part = int(part)
            if (parsed.scheme != 'https' or not host.endswith('.amazonaws.com') or parsed.port not in (None, 443)
                    or parsed.username or parsed.password or parsed.fragment or part in order):
                raise ValueError
        except ValueError:
            raise RapaportError(invalid) from None
        order[part] = url
    total = (size + split - 1) // split
    if len(order) != total or any(part not in order for part in range(1, total + 1)):
        raise RapaportError(invalid)
    return [(order[part], (part - 1) * split, min(part * split, size)) for part in range(1, total + 1)]

