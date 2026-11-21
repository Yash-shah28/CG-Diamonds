"""Read-only lookups through the labs' documented report services."""
import re
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import httpx


LABS = ('GIA', 'IGI', 'HRD')
HRD_ENDPOINT = 'https://my.hrdantwerp.com/api/public/GetReport'
_GIA_GRADES = """
    shape_and_cutting_style carat_weight color_grade clarity_grade cut_grade
    polish symmetry fluorescence measurements inscriptions report_comments
    proportions { depth_pct table_pct crown_angle crown_height pavilion_angle pavilion_depth }
"""
GIA_QUERY = """
query InventoryReport($number: String!) {
  getReport(report_number: $number) {
    report_number
    results {
      __typename
      ... on DiamondGradingReportResults { """ + _GIA_GRADES + """ }
      ... on LabGrownDiamondGradingReportResults { """ + _GIA_GRADES + """ }
    }
  }
}"""
_GIA_FIELDS = {
    'Shape': 'shape_and_cutting_style', 'Weight': 'carat_weight', 'Color': 'color_grade',
    'Clarity': 'clarity_grade', 'Cut': 'cut_grade', 'Polish': 'polish', 'Symmetry': 'symmetry',
    'Fluorescence': 'fluorescence', 'Measurements': 'measurements',
    'Laser Inscription': 'inscriptions', 'Certificate Comment': 'report_comments',
}
_GIA_PROPORTIONS = {
    'Depth %': 'depth_pct', 'Table': 'table_pct', 'Crown Angle': 'crown_angle',
    'Crown Height': 'crown_height', 'Pavilion Angle': 'pavilion_angle', 'Pavilion Depth': 'pavilion_depth',
}
_HRD_FIELDS = {
    'Shape': 'Results/Shape/ShapeCode', 'Weight': 'Results/Carat',
    'Color': 'Results/ColourGrade/ColourCode', 'Clarity': 'Results/ClarityGrade/ClarityDescription',
    'Cut': 'Results/Cut/Proportions/ProportionsDescription',
    'Polish': 'Results/Cut/Polish/PolishDescription', 'Symmetry': 'Results/Cut/Symmetry/SymmetryDescription',
    'Fluorescence': 'AdditionalInformation/Fluorescence/FluorescenceDescription',
    'Measurements': 'AdditionalInformation/Measurements',
    'Depth %': 'AdditionalInformation/TotalDepth/TotalDepthPercent',
    'Table': 'AdditionalInformation/TableWidth/TableWidthPercent',
    'Crown Angle': 'AdditionalInformation/CrownHeight/CrownAngle',
    'Crown Height': 'AdditionalInformation/CrownHeight/CrownHeightPercent',
    'Pavilion Angle': 'AdditionalInformation/PavilionDepth/PavilionAngle',
    'Pavilion Depth': 'AdditionalInformation/PavilionDepth/PavilionDepthPercent',
    'Certificate Comment': 'Remarks', 'Laser Inscription': 'Inscriptions',
}
_NUMERIC = frozenset(('Weight', *_GIA_PROPORTIONS))


class ReportLookupError(ValueError):
    pass


def report_number(text):
    text = text.strip().upper()
    if not re.fullmatch(r'[A-Z0-9-]{3,40}', text) or not any(character.isdigit() for character in text):
        raise ReportLookupError('Enter the report number exactly as printed on the certificate.')
    return text


def report_check_url(lab, number):
    base, parameter = {
        'GIA': ('https://www.gia.edu/report-check', 'reportno'),
        'IGI': ('https://www.igi.org/Verify-Your-Report/', 'r'),
        'HRD': ('https://my.hrdantwerp.com/', 'record_number'),
    }[lab]
    return f'{base}?{urlencode({parameter: report_number(number)})}'


def gia_endpoint(value):
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme == 'https' and parsed.hostname and not (
            parsed.username or parsed.password or parsed.fragment or parsed.query
        ):
            return parsed.geturl()
    except ValueError:
        pass
    raise ReportLookupError('Enter the HTTPS Report Results API endpoint supplied by GIA.')


def _clean(fields):
    cleaned = {}
    for field, source in fields.items():
        if not isinstance(source, (str, int, float)) or isinstance(source, bool):
            continue
        value = str(source).strip()
        if field in _NUMERIC:
            match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(?:ct|carats?|%|°|deg)?', value, re.I)
            value = match.group(1) if match else ''
        elif field == 'Shape' and value.casefold() in ('brilliant', 'round brilliant'):
            value = 'Round'
        if value and value not in ('-', '—', 'N/A'):
            cleaned[field] = value
    return cleaned


def _gia_result(payload, number):
    if not isinstance(payload, dict) or payload.get('errors'):
        raise ReportLookupError('GIA could not return the report. Check the number and your API plan.')
    report = (payload.get('data') or {}).get('getReport') or {}
    grades = report.get('results') or {}
    kind = {
        'DiamondGradingReportResults': 'Natural',
        'LabGrownDiamondGradingReportResults': 'Lab Grown',
    }.get(grades.get('__typename'))
    if str(report.get('report_number', '')).upper() != number or not kind:
        raise ReportLookupError('GIA did not return a matching diamond grading report.')
    proportions = grades.get('proportions') or {}
    return kind, _clean({
        **{field: grades.get(key) for field, key in _GIA_FIELDS.items()},
        **{field: proportions.get(key) for field, key in _GIA_PROPORTIONS.items()},
    })


def _hrd_result(payload, number):
    if b'<!DOCTYPE' in payload.upper():
        raise ReportLookupError('HRD returned an unexpected report format.')
    report = ElementTree.fromstring(payload)
    if report.findtext('ReportNumber', '').strip().upper() != number or report.find('Results/Shape') is None:
        raise ReportLookupError('HRD did not return a matching diamond grading report.')
    origin = report.findtext('AdditionalInformation/Origin', '').strip().lower()
    types = ' '.join(node.text or '' for node in report.findall('AvailableReports/*/ReportType')).lower()
    if any(label in f'{origin} {types}' for label in ('lab grown', 'laboratory grown', 'laboratory-grown', 'synthetic')):
        kind = 'Lab Grown'
    else:
        kind = 'Natural' if 'natural' in f'{origin} {types}' else None
    return kind, _clean({field: report.findtext(path) for field, path in _HRD_FIELDS.items()})


def lookup_report(lab, number, credentials, *, client=None):
    """Return the reported inventory type and mapped values for a single stone."""
    number = report_number(number)
    if lab == 'IGI':
        raise ReportLookupError('Open the IGI verification report and enter its grades in the form.')
    if lab not in ('GIA', 'HRD'):
        raise ReportLookupError('Select a supported lab.')
    key = credentials.get('key', '').strip()
    if not key:
        raise ReportLookupError(f'Enter your {lab} API credentials in Lookup settings.')
    requests = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        if lab == 'GIA':
            response = requests.post(
                gia_endpoint(credentials.get('url', '')), headers={'Authorization': key},
                json={'query': GIA_QUERY, 'variables': {'number': number}},
            )
        else:
            response = requests.get(f'{HRD_ENDPOINT}/{number}', headers={'API-Token': key})
        if response.status_code in (401, 403):
            raise ReportLookupError(f'{lab} rejected this lookup. Check your API credentials and report access.')
        if response.status_code == 429:
            raise ReportLookupError(f'{lab} is limiting requests. Wait a moment before trying again.')
        if response.status_code in (400, 404):
            raise ReportLookupError(f'{lab} could not find this report. Check the report number.')
        if not response.is_success:
            raise ReportLookupError(f'{lab} lookup failed (HTTP {response.status_code}).')
        if lab == 'GIA':
            return _gia_result(response.json(), number)
        return _hrd_result(response.content, number)
    except httpx.RequestError:
        raise ReportLookupError(f'Could not connect to {lab}. Check your connection and try again.') from None
    except (ElementTree.ParseError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, ReportLookupError):
            raise
        raise ReportLookupError(f'{lab} returned an unreadable report.') from None
    finally:
        if client is None:
            requests.close()

